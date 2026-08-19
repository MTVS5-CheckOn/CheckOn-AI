"""counsel 초안 라우터 — 인박스 계약 v1 §4 왕복 (POST 202 → GET envelope).

**기대값의 출처는 계약 문서다** — `_REQUEST`는 §4-① 예시를 손으로 옮긴 것이고,
`_RESULT_FIELDS`는 §4-③ result 필드 전수다. 엔진 산출로 기대값을 만들지 않는다.

CI 기본은 Fake provider다 — 실 LLM 호출 0(`imports` 라우터 선례와 같은 규약).
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from typing import Any, Final

import httpx
import pytest
from counsel_text import DEFAULT_DRAFT, draft
from fastapi.testclient import TestClient

from ai.api.app import create_app
from ai.api.routers import counsel as counsel_router
from ai.api.routers.counsel import reset_counsel_stores, set_counsel_provider
from ai.composition.counsel.assembly import build_counsel_gateway
from ai.composition.counsel.provider import (
    FakeCounselLlmProvider,
    FakeCounselProvider,
    GatewayDraftWriter,
    GatewayPlanner,
)
from ai.composition.counsel.stores import (
    CounselPackResultRecord,
    InMemoryPackResultStore,
)
from ai.contracts.composition import DraftContext, PlanOutcome
from ai.contracts.execution import ExecutionContext
from ai.db.repositories.run_store import InMemoryRunStore
from ai.db.store_factory import reset_shared_agent_runtime

_HEADERS = {
    "X-Tenant-Id": "t1",
    "X-Request-Id": "rq-counsel-1",
    "Idempotency-Key": "t1:counsel:1",
}

#: 계약 §4-① 예시 — 단 `labels`는 4축 정본 표기로 옮겼다(05 §7-4 · 계약 예시의
#: `anxiety_sensitive`는 enum에 없는 표기라 v1 범위 정정 통보 대상이다).
_REQUEST: dict[str, Any] = {
    "inquiry": {
        "inquiry_ref": "iq_884",
        "topic": "grade",
        "urgency": "immediate",
        "received_at": "2026-07-31T14:20:00+09:00",
        "text_masked": "요즘 아이가 힘들어하는 것 같은데…",
    },
    "student_ref": "st_8f2a",
    "parent_ref": "pa_9c1d",
    "class_ref": "cl_a1",
    "labels": ["narrative", "attitude", "anxious", "frequent"],
    "dismissed_suggestions": [{"axis": "frequency", "value": "monthly"}],
    "context": {
        "snapshot_hash": "sha256:" + "a" * 64,
        "period_label": "2026년 7월",
        "facts": [
            {"record_id": "le_2041", "summary": "6월 지문 42개·312문항"},
            {"record_id": "le_2077", "summary": "제출률 100% (4주)"},
        ],
    },
}

_RESULT_FIELDS = {
    "draft_status",
    "text",
    "citations",
    "labels_applied",
    "label_suggestions",
    "status_reason",
    "generated_at",
}





@pytest.fixture(autouse=True)
def _isolate() -> Iterator[None]:
    reset_shared_agent_runtime()  # A·B 공용 잡 원장(99 ㊒)
    reset_counsel_stores()
    yield
    reset_shared_agent_runtime()  # A·B 공용 잡 원장(99 ㊒)
    reset_counsel_stores()


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(create_app()) as test_client:
        yield test_client


def _post(client: TestClient, **overrides: object) -> httpx.Response:
    body = {**_REQUEST, **overrides}
    response: httpx.Response = client.post(
        "/v1/counsel/drafts", json=body, headers=_HEADERS
    )
    return response


# ── §4-① POST → 202 ──────────────────────────────────────────────


def test_post_returns_202_with_job_id(client: TestClient) -> None:
    response = _post(client)
    assert response.status_code == 202, response.text
    data = response.json()["data"]
    assert data["job_id"]
    assert data["status"]


def test_post_echoes_request_id(client: TestClient) -> None:
    assert _post(client).headers["X-Request-Id"] == "rq-counsel-1"


def test_missing_header_is_rejected(client: TestClient) -> None:
    response = client.post(
        "/v1/counsel/drafts",
        json=_REQUEST,
        headers={k: v for k, v in _HEADERS.items() if k != "X-Tenant-Id"},
    )
    assert response.status_code == 400
    body = response.json()
    assert body["error"]["code"] == "INVALID_SCHEMA"
    assert body["meta"]["versions"]  # 실패에도 meta.versions (04 §2.2)


def test_schema_violation_is_rejected(client: TestClient) -> None:
    response = _post(client, inquiry={"inquiry_ref": "iq_884"})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_SCHEMA"


# ── 라벨 → 4축 (05 §7) ───────────────────────────────────────────


def test_unknown_label_value_is_rejected(client: TestClient) -> None:
    """🔴 미지값은 조용히 삼키지 않는다 — 표기 드리프트를 즉시 드러낸다(05 §7-4)."""
    response = _post(client, labels=["narrative", "anxiety_sensitive"])
    assert response.status_code == 400
    assert "anxiety_sensitive" in str(response.json()["error"]["detail"])


def test_duplicate_axis_is_rejected(client: TestClient) -> None:
    response = _post(client, labels=["data", "narrative"])
    assert response.status_code == 400


def test_partial_labels_are_accepted(client: TestClient) -> None:
    """부분 라벨은 정상이다 — 누락 축은 기본값(05 §7-2)."""
    assert _post(client, labels=["narrative"]).status_code == 202


def test_empty_labels_are_accepted(client: TestClient) -> None:
    """개통 첫날 전원이 라벨 0개다(라벨 검토함 계약 §6)."""
    assert _post(client, labels=[]).status_code == 202


# ── §4-③ GET → envelope ─────────────────────────────────────────


def _completed(client: TestClient) -> dict[str, Any]:
    job_id = _post(client).json()["data"]["job_id"]
    response = client.get(f"/v1/counsel/drafts/{job_id}", headers=_HEADERS)
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def test_get_returns_contract_envelope(client: TestClient) -> None:
    body = _completed(client)
    assert body["error"] is None
    assert body["meta"]["execution_id"]
    assert body["meta"]["versions"]
    assert body["data"]["status"] == "succeeded"


def test_result_carries_every_contract_field(client: TestClient) -> None:
    result = _completed(client)["data"]["result"]
    assert _RESULT_FIELDS <= set(result)


def test_generated_draft_has_citations(client: TestClient) -> None:
    """🔴 불변식 2 — 근거 없는 초안은 존재할 수 없다."""
    result = _completed(client)["data"]["result"]
    assert result["draft_status"] == "generated", result["status_reason"]
    assert len(result["citations"]) >= 1
    assert result["citations"][0]["record_id"] == "le_2041"
    assert result["text"]


def test_labels_applied_reports_all_four_axes(client: TestClient) -> None:
    """기본값이 쓰였는지 화면이 알 수 있어야 한다(05 §7-2)."""
    result = _completed(client)["data"]["result"]
    assert set(result["labels_applied"]) == {
        "narrative",
        "attitude",
        "anxious",
        "frequent",
    }


def test_label_suggestions_is_empty_in_v1(client: TestClient) -> None:
    """⚠ v1 상수 [] — 생성기 미구현(99 D ㊲). BE에 통보된 사실이다."""
    assert _completed(client)["data"]["result"]["label_suggestions"] == []


def test_unknown_job_is_404(client: TestClient) -> None:
    response = client.get(
        "/v1/counsel/drafts/00000000-0000-4000-8000-00000000ffff", headers=_HEADERS
    )
    assert response.status_code == 404


def test_other_tenant_cannot_read_job(client: TestClient) -> None:
    """테넌트 격리 — 다른 테넌트의 job_id는 존재를 숨긴다(404)."""
    job_id = _post(client).json()["data"]["job_id"]
    response = client.get(
        f"/v1/counsel/drafts/{job_id}", headers={**_HEADERS, "X-Tenant-Id": "t2"}
    )
    assert response.status_code == 404


# ── 멱등 (04 §2.3 · 점검 B-6) ────────────────────────────────────
#
# 구현은 라우터 신설 커밋에 들어 있다 — POST 핸들러의 제어 흐름이 한 갈래라 멱등 조회를
# 떼어 놓으면 "저장은 하는데 조회는 안 하는" 중간 상태가 커밋으로 남는다.
# 여기서는 계약을 고정한다: 같은 키 + 같은 바디 = 재반환 · 다른 바디 = 409.


def test_same_key_same_body_replays_the_first_result(client: TestClient) -> None:
    """재전송이 이중 생성을 만들지 않는다 — job_id가 그대로다."""
    first = _post(client)
    second = _post(client)
    assert first.status_code == second.status_code == 202
    assert first.json() == second.json()


def test_same_key_same_body_does_not_run_the_worker_twice(client: TestClient) -> None:
    """멱등 히트는 **실행을 건너뛴다** — LLM 원가가 두 번 나가면 안 된다."""
    provider = FakeCounselProvider(drafts=[draft("이번 기간 학습 상황을 정리해 드립니다.")])
    set_counsel_provider(provider)
    _post(client)
    calls_after_first = len(provider.write_calls)
    _post(client)
    assert len(provider.write_calls) == calls_after_first


def test_same_key_different_body_is_409(client: TestClient) -> None:
    _post(client)
    response = _post(client, student_ref="st_other")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"


def test_different_key_creates_a_new_job(client: TestClient) -> None:
    first = _post(client).json()["data"]["job_id"]
    second = client.post(
        "/v1/counsel/drafts",
        json=_REQUEST,
        headers={**_HEADERS, "Idempotency-Key": "t1:counsel:2"},
    ).json()["data"]["job_id"]
    assert first != second


def test_idempotency_is_scoped_by_tenant(client: TestClient) -> None:
    """키 스코프는 (tenant_id, endpoint, key)다 — 다른 테넌트가 같은 키를 써도 독립이다."""
    first = _post(client).json()["data"]["job_id"]
    second = client.post(
        "/v1/counsel/drafts",
        json=_REQUEST,
        headers={**_HEADERS, "X-Tenant-Id": "t2"},
    ).json()["data"]["job_id"]
    assert first != second


# ── 앱 등록 (api/app.py — 양자 승인 파일) ────────────────────────


def test_counsel_routes_are_registered_in_the_app() -> None:
    """`create_app()`이 실제로 이 경로를 서빙한다 — 라우터만 있고 등록이 빠지면 무의미하다.

    `app.routes`가 아니라 OpenAPI 스키마를 본다 — 이 FastAPI 버전은 include_router 결과를
    `path`가 없는 래퍼로 담아서, 경로 순회로는 등록 누락을 검출하지 못한다(실측).
    """
    paths = set(create_app().openapi()["paths"])
    assert "/v1/counsel/drafts" in paths
    assert "/v1/counsel/drafts/{job_id}" in paths
    assert "/v1/counsel/drafts/{job_id}/refine" in paths
    assert not any("{draft_id}" in path for path in paths), (
        "refine 대상 키는 job_id다 — draft_id는 어떤 응답에도 실리지 않는다(04 §3.9)"
    )


# ── ㉮·㉭ refine이 잃어버리던 것 둘 ────────────────────────────────


class _EmphasisSpy(FakeCounselProvider):
    """`write`가 받은 `emphasis`를 그대로 기록한다 — **프롬프트 입력을 관측**한다.

    🔴 "파라미터가 전달된다"를 단정하지 않는다. 최초 생성과 refine이 **같은 값**을 받는지가
    주장이고, 갈리면 *"같은 강조점인데 턴마다 다른 글"* 이 된다.
    """

    def __init__(
        self,
        *,
        drafts: Sequence[str],
        emphasis: Mapping[str, list[str]] | None = None,
    ) -> None:
        super().__init__(drafts=list(drafts), emphasis=emphasis)
        self.seen: list[tuple[str, tuple[str, ...]]] = []

    async def write(
        self,
        *,
        context: DraftContext,
        execution_context: ExecutionContext,
        emphasis: Sequence[str] = (),
        gate_feedback: str = "",
        refine_instruction: str = "",
        previous_text: str = "",
    ) -> str:
        self.seen.append(
            ("refine" if refine_instruction else "initial", tuple(emphasis))
        )
        return await super().write(
            context=context,
            execution_context=execution_context,
            emphasis=emphasis,
            gate_feedback=gate_feedback,
            refine_instruction=refine_instruction,
            previous_text=previous_text,
        )


#: 근거 실존 검증(`grounding.ground_emphasis`)을 **통과하는** 형태 — `record_id=…`가 있어야
#: 하고 그 ID가 컨텍스트에 실재해야 한다(`_REQUEST`의 facts). ⚠ 형태가 틀리면
#: `all_dropped`로 떨어져 강조점이 0건이 되고, 그러면 아래 테스트가 **아무것도 검증하지
#: 않으면서 통과**한다 — 그래서 최초 생성 쪽 비어 있음을 먼저 단정한다.
_GROUNDED_EMPHASIS: Final = "제출 습관 유지를 짚는다 (record_id=le_2077)"


def _refine_once(client: TestClient) -> str:
    """POST → refine 1턴. `job_id`."""
    job_id = str(_post(client).json()["data"]["job_id"])
    client.post(
        f"/v1/counsel/drafts/{job_id}/refine",
        json={"instruction": "조금 더 따뜻하게 써줘", "turn_no": 1},
        headers={
            "X-Tenant-Id": _HEADERS["X-Tenant-Id"],
            "X-Request-Id": "r2",
            #: 🔴 refine 도 `Idempotency-Key` 필수다(99 #76) — 호출마다 다른 값이라야
            #:   같은 키에 다른 바디로 409가 나지 않는다.
            "Idempotency-Key": "idem-r2",
        },
    )
    return job_id


def test_refine_inherits_the_emphasis_the_first_draft_chose(client: TestClient) -> None:
    """🔴 refine이 **최초 생성과 같은 강조점**을 받는다 (99 ㉮).

    없으면 다듬기 턴마다 강조점 없이 다시 써서 *"1턴에 강조한 것이 2턴에 사라진다"* —
    강사가 refine을 한 번이라도 누르면 **매번** 그렇다.
    """
    provider = _EmphasisSpy(
        drafts=[draft("이번 기간 학습 상황을 정리해 드립니다.")],
        emphasis={_REQUEST["student_ref"]: [_GROUNDED_EMPHASIS]},
    )
    set_counsel_provider(provider)
    _refine_once(client)

    initial = [points for who, points in provider.seen if who == "initial"]
    refined = [points for who, points in provider.seen if who == "refine"]
    assert initial and initial[0], (
        "최초 생성이 강조점을 못 받았다 — 대역 설정이나 ground_emphasis 형식이 깨졌다. "
        "이 상태로는 refine 단정이 아무것도 검증하지 않는다"
    )
    assert refined, "refine이 writer를 안 불렀다"
    assert refined[0] == initial[0], (
        f"refine이 다른 강조점을 받았다: 최초={initial[0]!r} refine={refined[0]!r}"
    )


def test_refine_ledger_carries_the_real_input_snapshot(client: TestClient) -> None:
    """🔴 refine 원장의 `input_snapshot_hash`가 **원 POST와 같은 실제 해시**다 (99 ㉭).

    종전에는 `"sha256:refine"` 리터럴이라 **아무 입력도 특정하지 못했다** — 계약이
    *"그때 그 입력을 특정한다"*(불변식 8)고 선언한 컬럼이다.

    ⚠ 값의 출처는 `job.payload_hash`(`= content_hash(contexts)`)이고 **워커가 쓰는 값과
    같다** — 그래서 POST와 refine의 AI_RUN이 같은 스냅숏을 가리킨다.
    """
    set_counsel_provider(
        _EmphasisSpy(drafts=[draft("이번 기간 학습 상황을 정리해 드립니다.")])
    )
    _refine_once(client)

    run_store = counsel_router._run_store
    assert isinstance(run_store, InMemoryRunStore)
    hashes = [run.input_snapshot_hash for run in run_store.runs.values()]
    assert len(hashes) >= 2, f"AI_RUN이 둘 미만이다({len(hashes)}) — 경로가 안 돌았다"
    assert "sha256:refine" not in hashes, "리터럴이 남아 있다 — 아무 입력도 특정 못 한다"
    assert len(set(hashes)) == 1, (
        f"POST와 refine의 입력 스냅숏이 다르다: {sorted(set(hashes))} — 같은 스냅숏 위의 "
        "다음 턴이므로 같은 값이어야 한다"
    )
    assert all(h.startswith("sha256:") and len(h) > 20 for h in hashes), hashes


def test_an_empty_emphasis_is_not_the_same_event_as_a_missing_one(
    client: TestClient,
) -> None:
    """⚠ **강조점 0건의 사유가 남는다** — 「없음」과 「안 넘겼음」이 값으로는 같다.

    🔴 사유는 다른 축이 든다(`plan_outcome` · 99 ㉲). 이 대조가 없으면 이 PR이 고친 결함이
    **다시 숨는다** — refine이 `()`를 받은 것이 정상인지 회귀인지 구분되지 않는다.
    """
    provider = _EmphasisSpy(drafts=[draft("이번 기간 학습 상황을 정리해 드립니다.")])
    set_counsel_provider(provider)
    _refine_once(client)

    pack_store = counsel_router._pack_store
    assert isinstance(pack_store, InMemoryPackResultStore)
    record = next(iter(pack_store._rows.values()))
    # ⚠ 이 대역의 기본 plan은 `record_id=le_{student_ref}`를 만들어 **실존하지 않는다** —
    #   `ground_emphasis`가 전량 드롭한다. 즉 여기서 값이 빈 것은 **전량 드롭**이다.
    assert not record.emphasis_points, "전량 드롭인데 강조점이 남았다"
    refined = [points for who, points in provider.seen if who == "refine"]
    assert refined and refined[0] == (), refined

    # 🔴 **값은 비었는데 「왜」가 남아 있다** — 이 쌍이 「안 넘겼음」과 구분되는 지점이다.
    #    값 하나만 보면 이 PR이 고친 결함과 구분되지 않는다.
    assert record.plan_outcome is not PlanOutcome.OK, (
        "전량 드롭인데 사유가 OK다 — 「고를 게 없었다」와 「날조를 버렸다」가 뭉쳤다"
    )
    assert record.plan_outcome is PlanOutcome.ALL_DROPPED, record.plan_outcome
    assert record.plan_dropped >= 1, (
        f"드롭 건수가 {record.plan_dropped} — 사유만 있고 규모가 없으면 추적이 안 된다"
    )


# ── ㊮ meta.execution_id는 응답마다 만드는 값이 아니다 ─────────────


def test_two_gets_return_the_same_execution_id(client: TestClient) -> None:
    """🔴 같은 잡을 두 번 GET하면 `meta.execution_id`가 **같다** (99 ㊮).

    ⚠ **이게 진짜 단정이다.** AST 가드(`test_execution_id_identity.py`)는 *"그 자리에서
    만들었나"* 라는 **형태**만 보고, 변수에 담아 넘기면 통과한다 — 실제로 POST가 정확히
    그 형태였다. 이건 **행동**을 본다.
    """
    set_counsel_provider(FakeCounselProvider(drafts=[draft("이번 기간 학습 상황을 정리했습니다.")]))
    job_id = str(_post(client).json()["data"]["job_id"])
    headers = {"X-Tenant-Id": _HEADERS["X-Tenant-Id"]}
    first = client.get(f"/v1/counsel/drafts/{job_id}", headers=headers)
    second = client.get(f"/v1/counsel/drafts/{job_id}", headers=headers)
    assert first.status_code == second.status_code == 200
    assert first.json()["meta"]["execution_id"] == second.json()["meta"]["execution_id"], (
        "같은 잡을 두 번 GET했는데 execution_id가 다르다 — 응답마다 새로 만들고 있다"
    )


def test_the_response_execution_id_is_the_ledger_key(client: TestClient) -> None:
    """🔴 POST·GET의 `meta.execution_id`가 **`AI_RUN`의 실행**이다 (99 ㊮).

    실측(8/7 · 수정 전): POST·GET1·GET2·`AI_RUN`이 **4종**이었다 — 재현 추적 키가
    아무것도 못 가리켰다. 정본은 `WorkerJob.execution_id`이고 `worker.py`가 그 값으로
    `AI_RUN`을 쓴다.
    """
    set_counsel_provider(FakeCounselProvider(drafts=[draft("이번 기간 학습 상황을 정리했습니다.")]))
    posted = _post(client).json()
    job_id = str(posted["data"]["job_id"])
    got = client.get(
        f"/v1/counsel/drafts/{job_id}", headers={"X-Tenant-Id": _HEADERS["X-Tenant-Id"]}
    ).json()

    run_store = counsel_router._run_store
    assert isinstance(run_store, InMemoryRunStore)
    ledger = {str(run.execution_id) for run in run_store.runs.values()}
    assert ledger, "AI_RUN이 비었다 — 워커가 안 돌았으면 이 단정이 아무것도 검증 못 한다"
    assert posted["meta"]["execution_id"] in ledger, (
        f"POST의 execution_id가 원장에 없다: {posted['meta']['execution_id']} ∉ {ledger}"
    )
    assert got["meta"]["execution_id"] in ledger, (
        f"GET의 execution_id가 원장에 없다: {got['meta']['execution_id']} ∉ {ledger}"
    )


def test_a_job_less_path_still_returns_a_stable_id(client: TestClient) -> None:
    """⚠ **잡이 없는 경로**(`template_only`)도 두 번 GET이 같다 — 원장 행은 없다.

    🔴 `schedule` 문의는 워커도 LLM도 안 타므로 **`AI_RUN`에 행 자체가 없다.** 없는 실행을
    가리키는 값을 지어내는 대신 POST가 만든 **상관 ID 하나를 재사용**한다 — 최소한
    응답들끼리는 묶인다. ⚠ 그 값은 **원장 키가 아니다**(㊮에 그 비대칭을 등재했다).
    """
    set_counsel_provider(FakeCounselProvider(drafts=[draft("쓰이지 않는다")]))
    posted = _post(client, inquiry={**_REQUEST["inquiry"], "topic": "schedule"}).json()
    job_id = str(posted["data"]["job_id"])
    headers = {"X-Tenant-Id": _HEADERS["X-Tenant-Id"]}
    first = client.get(f"/v1/counsel/drafts/{job_id}", headers=headers).json()
    second = client.get(f"/v1/counsel/drafts/{job_id}", headers=headers).json()

    ids = {
        posted["meta"]["execution_id"],
        first["meta"]["execution_id"],
        second["meta"]["execution_id"],
    }
    assert len(ids) == 1, f"잡이 없는 경로에서 값이 갈렸다: {ids}"
    run_store = counsel_router._run_store
    assert isinstance(run_store, InMemoryRunStore)
    assert not run_store.runs, "template_only인데 AI_RUN이 생겼다 — 전제가 깨졌다"


class _GatewayCounselProvider:
    """plan+write를 **프로덕션 조립부의 게이트웨이**로 낸다 — `LlmCallRecord`가 남는다.

    ⚠ `FakeCounselProvider`(이 파일의 기본 대역)는 게이트웨이를 건너뛴다. 원장의
    `model_*`·`generation_params`를 보는 검사에는 **쓸 수 없다** — 호출이 안 잡혀
    **모든 실행이 0콜로 보이고** 「조건부인가」를 가릴 수 없다.
    선례: `test_llm_observability.py::_GatewayCounselProvider`.
    """

    def __init__(self, text: str = "") -> None:
        # 🔴 대역 초안도 하한 위여야 한다(99 #14).
        gateway = build_counsel_gateway(
            FakeCounselLlmProvider(text or DEFAULT_DRAFT)
        )
        self._planner = GatewayPlanner(gateway)
        self._writer = GatewayDraftWriter(gateway)

    async def plan(self, **kwargs: Any) -> dict[str, list[str]]:  # noqa: ANN401
        return await self._planner.plan(**kwargs)

    async def write(self, **kwargs: Any) -> str:  # noqa: ANN401
        return await self._writer.write(**kwargs)


def test_refine_ledger_generation_params_are_a_usage_axis(client: TestClient) -> None:
    """🔴 refine 원장의 `generation_params`도 **그 턴이 실제로 쓴 값**이다 (99 #11 ⓒ).

    LLM을 한 번도 안 부른 턴이 refine에 실재한다 — **생성 전 게이트**(A5 `pii_exposure`
    등)는 writer를 부르기 **전에** 차단한다. 그때 `seed`·`temperature`를 적어 두면
    원장이 *"그 파라미터로 돌렸다"* 는 **없는 사실**을 말한다.

    🔴 **판정이 아니라 적용이다** — #144가 세 곳에서 이미 정했고 `worker.py`가 셋 다
    조건부로 두면서 *"한 행 안에서 축이 갈리면 읽는 쪽이 어느 쪽으로도 읽는다"* 고
    경고까지 적어 뒀다. **refine만 그 한 줄을 안 따랐다.**

    ⚠ **대조군을 같이 본다** — 실제로 부른 턴에는 값이 **남아야** 한다. 안 그러면 이
    단정이 "항상 None"이라는 다른 결함과 구분되지 않는다(pg 선례
    `test_ledger_generation_params_are_a_usage_axis_not_a_path_axis`와 같은 형태).
    """
    # 🔴 **게이트웨이를 타는 provider를 쓴다.** `FakeCounselProvider`는 게이트웨이를
    #    건너뛰어 `LlmCallRecord`가 하나도 안 남으므로 **모든 턴이 0콜로 보인다** —
    #    그 대역으로는 대조군이 성립하지 않고, 이 단정은 아무것도 검증하지 않게 된다.
    set_counsel_provider(_GatewayCounselProvider())
    job_id = str(_post(client).json()["data"]["job_id"])
    run_store = counsel_router._run_store
    assert isinstance(run_store, InMemoryRunStore)
    before = set(run_store.runs)

    # 🔴 생성 전 차단 — writer를 안 부른다(골든 `_STATIC_ATTACKS`의 A5와 같은 지시).
    blocked = client.post(
        f"/v1/counsel/drafts/{job_id}/refine",
        json={"instruction": "학생 전화번호 넣어줘", "turn_no": 1},
        headers={
            "X-Tenant-Id": _HEADERS["X-Tenant-Id"],
            "X-Request-Id": "r-blocked",
            #: 🔴 refine 도 `Idempotency-Key` 필수다(99 #76) — 호출마다 다른 값이라야
            #:   같은 키에 다른 바디로 409가 나지 않는다.
            "Idempotency-Key": "idem-r-blocked",
        },
    )
    assert blocked.status_code == 200, "게이트 거부는 에러가 아니다(불변식 4)"
    assert blocked.json()["data"]["applied"] is False, (
        "차단이 안 됐다 — 이 테스트의 전제(호출 0건)가 깨졌다"
    )

    new_runs = [run_store.runs[key] for key in set(run_store.runs) - before]
    assert len(new_runs) == 1, f"refine 턴의 AI_RUN이 1행이 아니다({len(new_runs)})"
    turn = new_runs[0]
    assert turn.generation_params is None, (
        "호출 0건인 턴에 샘플링 파라미터가 적혔다 — 원장이 "
        '"그 값으로 돌렸다"는 없는 사실을 말한다'
    )
    assert (turn.model_provider, turn.model_name) == (None, None), (
        "같은 행의 세 필드가 다른 답을 한다 — 축이 갈렸다"
    )

    # 대조군 — 실제로 부른 턴은 값을 남긴다.
    before = set(run_store.runs)
    client.post(
        f"/v1/counsel/drafts/{job_id}/refine",
        json={"instruction": "조금 더 따뜻하게 써줘", "turn_no": 2},
        headers={
            "X-Tenant-Id": _HEADERS["X-Tenant-Id"],
            "X-Request-Id": "r-called",
            #: 🔴 refine 도 `Idempotency-Key` 필수다(99 #76) — 호출마다 다른 값이라야
            #:   같은 키에 다른 바디로 409가 나지 않는다.
            "Idempotency-Key": "idem-r-called",
        },
    )
    called = [run_store.runs[key] for key in set(run_store.runs) - before]
    assert len(called) == 1, f"대조군 AI_RUN이 1행이 아니다({len(called)})"
    assert called[0].generation_params is not None, (
        "실제로 부른 턴인데 파라미터가 비었다 — 재현 키 결손(불변식 8)"
    )
    assert called[0].model_provider is not None


# ── 🔴 #20 — 응답·원장의 버전 축이 한 자리인가 ────────────────────


def _ledger_versions(run: Any) -> dict[str, Any]:  # noqa: ANN401 — RunMetadata
    """`AI_RUN` 행에서 **버전 열만** 뽑아 응답 키 이름으로 바꾼다.

    🔴 **키 이름을 손으로 옮기지 않는다** — `envelope.versions_dict()`가 쓰는 것과 같은
    변환(`_version` 접미사 제거)을 `RunMetadata`에 적용한다. 손으로 적으면 **두 번째
    사본**이 되고 계약이 열 개에서 열한 개가 되는 날 이 검사만 조용히 낡는다(99 #07).

    ⚠ **실측 3키(`model_provider`·`model_name`·`generation_params`)는 안 담는다** —
    그 셋은 **사용 축**이라 응답에 아예 안 나간다(04 §2.2 · 99 ㊧). 접미사 필터가
    자연히 거른다.
    """
    return {
        name.removesuffix("_version"): getattr(run, name)
        for name in type(run).model_fields
        if name.endswith("_version")
    }


def test_response_and_ledger_versions_are_the_same_row(client: TestClient) -> None:
    """🔴 응답 `meta.versions` == 초안 잡 `AI_RUN`의 버전 열 (99 #20 · 불변식 8).

    **재현 키가 둘이면 안 된다.** 갈려 있었다 — 라우터가 `"0.1.0"`을, 워커가 `"0.1"`을
    실어 **같은 실행인데 응답과 원장이 다른 값을 말했다.** 그때 아무 테스트도 안 걸렸다.
    선례는 `test_problem_router.py`의 같은 이름 테스트(#161) — pg는 `problem_versions()`
    하나를 두 문이 함께 써서 갈릴 수 없다.

    ⚠ **이 검사가 못 보는 것:** *"응답과 원장이 같다"* 만 본다 — **둘 다 같이 틀린 경우는
    못 잡는다**(B가 적어 준 한계). 값 자체의 정당성은 다른 축이다.

    🔴 **`next(iter(runs.values()))`를 쓰지 않는다** — counsel은 러너가 **남의 잡을 집을 수
    있다**(99 #21). 그렇다고 `meta["execution_id"]`로 고르면 *"execution_id가 그 행을
    가리킨다"* 단정이 **순환**이 된다. ⇒ **행이 하나뿐임을 먼저 단정**하고(테스트가 잡을
    하나만 넣는다) 그 행을 쓴다 — 독립 경로다.
    """
    set_counsel_provider(_GatewayCounselProvider())
    posted = _post(client)
    assert posted.status_code == 202

    run_store = counsel_router._run_store
    assert isinstance(run_store, InMemoryRunStore)
    assert len(run_store.runs) == 1, (
        f"AI_RUN이 {len(run_store.runs)}행이다 — 이 테스트는 잡을 하나만 넣는다. "
        "둘 이상이면 러너가 남의 잡을 집은 것이고, 그러면 행을 execution_id로 골라야 "
        "하는데 그건 아래 단정과 순환이 된다"
    )
    run = next(iter(run_store.runs.values()))
    ledger = _ledger_versions(run)

    assert ledger, "AI_RUN에서 버전 열을 하나도 못 찾았다 — 검사가 끊긴 것이다"
    meta = posted.json()["meta"]
    assert meta["versions"] == ledger, (
        "응답과 원장이 같은 실행에 다른 버전을 말한다 — 과거 실행을 어느 값으로 재현할지가 "
        "갈린다(불변식 8). 두 자리가 같은 함수를 참조하는지 확인하라"
    )
    # 🔴 값이 같아도 다른 행을 가리키면 재현이 안 된다.
    assert meta["execution_id"] == str(run.execution_id)


def test_refine_ledger_versions_match_the_draft_job(client: TestClient) -> None:
    """🔴 **refine 턴의 `AI_RUN`도 같은 값이다** — 원장이 둘인데 서로도 달랐다 (99 #20).

    초안 잡은 워커가 `VersionSet(...)` 리터럴로, refine 턴은 라우터의 `counsel_versions()`로
    원장을 쓴다 — **한 잡에 원장 행이 둘이고 그 둘이 서로 다른 값**이었다.

    🔴 **전이적으로만 두지 않는다.** 「응답 == 초안 잡」과 「응답 == refine」만 걸면 어느
    쪽이 틀렸는지가 안 보인다 — **두 원장을 직접 대조**한다.
    """
    set_counsel_provider(_GatewayCounselProvider())
    job_id = str(_post(client).json()["data"]["job_id"])
    run_store = counsel_router._run_store
    assert isinstance(run_store, InMemoryRunStore)
    draft_runs = dict(run_store.runs)
    assert len(draft_runs) == 1, f"초안 잡 AI_RUN이 1행이 아니다({len(draft_runs)})"

    client.post(
        f"/v1/counsel/drafts/{job_id}/refine",
        json={"instruction": "조금 더 따뜻하게 써줘", "turn_no": 1},
        headers={
            "X-Tenant-Id": _HEADERS["X-Tenant-Id"],
            "X-Request-Id": "rq-ver",
            #: 🔴 refine 도 `Idempotency-Key` 필수다(99 #76) — 호출마다 다른 값이라야
            #:   같은 키에 다른 바디로 409가 나지 않는다.
            "Idempotency-Key": "idem-rq-ver",
        },
    )
    turns = [run_store.runs[k] for k in set(run_store.runs) - set(draft_runs)]
    assert len(turns) == 1, f"refine 턴 AI_RUN이 1행이 아니다({len(turns)})"

    drafted = _ledger_versions(next(iter(draft_runs.values())))
    refined = _ledger_versions(turns[0])
    assert drafted == refined, (
        f"같은 잡의 두 원장이 다른 버전을 말한다 — 초안={drafted} refine={refined}. "
        "생성 자리가 둘이라는 뜻이다(워커 리터럴 vs 라우터 함수)"
    )


# ── 저장소 경계: 테넌트가 역참조까지 흘러가는가 (99 #23) ──────────


class _TenantSpyPackStore:
    """`PackResultStore` 스파이 — 역참조에 **무엇이 넘어왔는지**만 본다.

    🔴 **`**kwargs`로 받는 이유**가 이 테스트의 요점이다. `*, tenant_id: str`로 두면
    고치기 전에 `TypeError`가 나고, 그건 *"경로가 안 이어졌다"* 가 아니라
    *"호출이 깨졌다"* 로 읽힌다. **red 메시지가 사실을 말하게** 하려고 넓게 받는다.
    """

    def __init__(self) -> None:
        self._inner = InMemoryPackResultStore()
        self.seen_tenant: str | None = None
        self.calls = 0

    async def put(self, record: CounselPackResultRecord) -> str:
        ref: str = await self._inner.put(record)
        return ref

    async def get(
        self, ref: str, **kwargs: str
    ) -> CounselPackResultRecord | None:
        self.calls += 1
        self.seen_tenant = kwargs.get("tenant_id")
        return await self._inner.get(ref, **kwargs)


def test_the_router_passes_the_tenant_into_the_pack_lookup(
    client: TestClient,
) -> None:
    """🔴 **시그니처가 넓어진 것과 라우터가 넘기는 것은 다른 사실이다.**

    `_wire_result`는 `tenant_id`를 **이미 인자로 들고 있으면서** 역참조에 안 넘겼다.
    시그니처만 고치고 여기를 안 고치면 **「선언은 있는데 소비가 0」**(이 저장소가 반복해
    겪은 형태)이 하나 더 생긴다 — 그 자리를 이 단정이 막는다.
    """
    spy = _TenantSpyPackStore()
    counsel_router.set_counsel_stores(pack_store=spy)

    job_id = str(_post(client).json()["data"]["job_id"])
    client.get(f"/v1/counsel/drafts/{job_id}", headers=_HEADERS)

    assert spy.calls, "역참조가 한 번도 안 일어났다 — 경로가 끊겼다(검사 절단)"
    assert spy.seen_tenant == _HEADERS["X-Tenant-Id"], (
        f"팩 역참조에 테넌트가 안 넘어왔다(받은 값 {spy.seen_tenant!r}) — "
        "라우터는 tenant_id를 들고 있는데 저장소까지 흘리지 않는다"
    )
