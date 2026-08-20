"""HTTP 계약 픽스처 — Kafka-HTTP adapter가 붙을 표면의 정본.

백엔드가 2026-08-12에 **AI는 HTTP만 제공하고 별도 adapter가 Kafka를 전담**하는 구조로
확정했다(`PROBLEM_STUDIO_AI_TEAM_HANDOFF.md` §2). 그러면 계약의 정본은 이벤트가 아니라
**HTTP 요청·응답**이고, adapter는 그 형태를 보고 만들어야 한다.

🔴 **손으로 적은 예시를 주지 않는다.** 여기 픽스처는 대부분 **실제 앱을 돌려** 얻은
응답이고, 나머지는 **응답 모델에서 직접** 만든다. 응답이 바뀌면 이 테스트가 먼저 죽는다 —
"문서의 예시가 낡았다"가 성립하지 않는 구조다.

**재생성:** 계약이 정당하게 바뀌었으면 `WRITE_HTTP_FIXTURES=1 uv run pytest
tests/ai/contract/test_http_fixtures.py`로 다시 쓰고 **diff를 리뷰**한다. 무심코 돌리면
계약 변경이 조용히 통과하므로 기본값은 항상 대조다.

🔴 503은 status가 아니라 code로 분기한다 — SERVICE_NOT_READY(준비 미완)와
LLM_UPSTREAM_DOWN(벤더 장애)이 같은 status를 쓴다.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Final
from uuid import UUID

from fastapi.testclient import TestClient

from ai.api.app import create_app
from ai.api.routers import counsel as counsel_router
from ai.api.routers import diagnosis as diagnosis_router
from ai.api.routers import problem as problem_router
from ai.composition.counsel.assembly import open_counsel_pack_runner
from ai.composition.counsel.enqueue import CounselPackEnqueuer
from ai.composition.counsel.settings import get_counsel_settings
from ai.composition.counsel.stores import InMemoryDraftResultStore
from ai.contracts.agents import JobPhase
from ai.contracts.counsel import CounselDraftRequest
from ai.contracts.diagnosis import DiagnosisResult
from ai.contracts.problem_generation import (
    Answer,
    Choice,
    EvidenceAnchor,
    EvidenceKind,
    GeneratedItem,
    ProblemItemStatus,
    ProblemRequest,
    SolveResult,
)
from ai.contracts.taxonomy import AreaTag, ItemFormat, TypeTag
from ai.db.repositories.run_store import InMemoryRunStore
from ai.db.store_factory import reset_shared_agent_runtime
from ai.problem_generation.assembly import problem_runtime_stores
from ai.problem_generation.provider import ProblemProviders

_FAKES_DIR = Path(__file__).parents[1] / "fakes"
sys.path.insert(0, str(_FAKES_DIR))

from fake_graph_context import FakeGraphContextService  # noqa: E402
from fake_provider import FakeProvider  # noqa: E402

FIXTURE_DIR: Final = Path(__file__).parent / "fixtures" / "http"
_WRITE: Final = os.environ.get("WRITE_HTTP_FIXTURES") == "1"

_TENANT: Final = "tn_0123456789abcdef0123456789abcdef"
_STUDENT: Final = "st_0123456789abcdef0123456789abcdef"
_SKILL_NODE_ID: Final = "grammar.sentence-structure"
_HEADERS: Final = {
    "X-Tenant-Id": _TENANT,
    "X-Request-Id": "req-http-fixture-0001",
    "Idempotency-Key": "pg_0198f100000070008000000000000001",
}

#: 실행마다 달라지는 값 — 픽스처에서는 고정 토큰으로 바꾼다. adapter는 이 값들을
#: **형식만** 신뢰해야 하고 특정 값에 의존하면 안 된다.
_PLACEHOLDER: Final = {
    "execution_id": "00000000-0000-4000-8000-0000000000e0",
    "job_id": "00000000-0000-4000-8000-0000000000j0".replace("j", "b"),
    "set_id": "00000000-0000-4000-8000-000000000050",
    "item_id": "00000000-0000-4000-8000-000000000010",
    #: 🔴 counsel `result.generated_at` 은 **실시간**이다(`_clock()`) — 정규화 안 하면
    #: 픽스처가 매 실행 흔들린다. ⚠ 기존 18개 픽스처에 이 키는 **0건**이라(실측)
    #: 여기 추가해도 그쪽 대조는 바뀌지 않는다.
    #: 🔴 **자리표시자는 원본의 형태를 지켜야 한다.** 종전 값(`…T00:00:00Z`)은 실제 응답의
    #: **마이크로초를 감췄다**(`2026-08-19T08:59:45.176713Z`). 픽스처의 존재 이유가
    #: 「BE 가 이 형태를 보고 만든다」인데 형태를 잘못 보여 주면, BE 가
    #: `yyyy-MM-dd'T'HH:mm:ss'Z'` 패턴을 짜고 **픽스처로는 통과하고 운영에서 깨진다.**
    #: ⚠ UUID 셋은 원래 형태를 지키고 있었다 — 이 키만 규율에서 빠져 있었다.
    "generated_at": "2026-01-01T00:00:00.000000Z",
}
_BE_REQUIRED_FLOW_FIXTURES: Final = {
    "POST problems request": "post_problems.request",
    "POST problems 202": "post_problems.202",
    "GET job queued": "get_problem.queued",
    "GET job succeeded": "get_problem.succeeded",
    "GET job 404": "get_problem.404",
    "GET items list": "get_problem_items.list",
    "GET items detail": "get_problem_items.detail",
    "GET items detail after cache loss": "get_problem_items.detail.cache_lost",
    "GET items partial success": "get_problem_items.partial_success",
    "POST problems 400 missing header": "post_problems.400.missing_header",
    "POST problems 400 source procurement": (
        "post_problems.400.source_procurement_not_implemented"
    ),
    "POST problems 400 unsupported type": "post_problems.400.type_tag_not_supported",
    "POST problems 409 conflict": "post_problems.409.idempotency_conflict",
    "POST diagnosis request": "post_diagnosis.request",
    "POST diagnosis 200": "post_diagnosis.200",
    "POST diagnosis 200 rejected insufficient": (
        "post_diagnosis.200.rejected_insufficient"
    ),
    "POST diagnosis 400": "post_diagnosis.400.unknown_skill_node",
    #: counsel 3엔드포인트 — 🔴 **종전에는 이 축이 통째로 비어 있었다**(99 #96 · 실측
    #: 2026-08-19: 이 파일에 `counsel` 문자열 0건 · 픽스처 디렉터리에 counsel 파일 0개).
    #: 그래서 BE 가 코딩할 대상이 **손으로 적은 예시뿐**이었고 그게 틀렸는지 아무도 안 쟀다.
    "POST counsel drafts request": "post_counsel_drafts.request",
    "POST counsel drafts 202 succeeded": "post_counsel_drafts.202.succeeded",
    "POST counsel drafts 202 queued": "post_counsel_drafts.202.queued",
    "POST counsel drafts 400 missing header": "post_counsel_drafts.400.missing_header",
    "POST counsel drafts 400 unknown label": "post_counsel_drafts.400.unknown_label",
    "POST counsel drafts 409 conflict": "post_counsel_drafts.409.idempotency_conflict",
    "GET counsel draft generated": "get_counsel_draft.generated",
    "GET counsel draft template only": "get_counsel_draft.template_only",
    "GET counsel draft rejected insufficient": (
        "get_counsel_draft.rejected_insufficient"
    ),
    "GET counsel draft body missing": "get_counsel_draft.draft_body_missing",
    "GET counsel draft 404": "get_counsel_draft.404",
    "POST counsel refine request": "post_counsel_refine.request",
    "POST counsel refine 200 applied": "post_counsel_refine.200.applied",
    "POST counsel refine 200 blocked": "post_counsel_refine.200.blocked",
    "POST counsel refine 400 missing idempotency key": (
        "post_counsel_refine.400.missing_idempotency_key"
    ),
}


def _normalize(value: object) -> object:
    if isinstance(value, dict):
        return {
            key: (
                _PLACEHOLDER[key]
                if key in _PLACEHOLDER and isinstance(item, str)
                else _normalize(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_normalize(item) for item in value]
    return value


def _fixture(name: str, payload: object) -> None:
    """정규화한 payload를 픽스처와 대조한다 — 없으면 만들라고 알려 준다."""

    path = FIXTURE_DIR / f"{name}.json"
    normalized = _normalize(payload)
    if _WRITE:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(normalized, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        return
    assert path.exists(), (
        f"HTTP 픽스처가 없다: {path}\n"
        "계약을 새로 추가했다면 WRITE_HTTP_FIXTURES=1로 한 번 돌려 만들고 diff를 리뷰하라."
    )
    stored: object = json.loads(path.read_text(encoding="utf-8"))
    assert stored == normalized, (
        f"HTTP 응답이 픽스처와 갈렸다: {path}\n"
        "adapter가 이 형태를 보고 만들어졌다 — 바꿔야 한다면 백엔드에 통보가 선행이다."
    )


def test_be_required_flow_fixture_mapping_is_complete() -> None:
    """BE 필수 **32흐름**은 이름이 아니라 실제 JSON 파일에 일대일로 연결된다.

    ⚠ 종전 docstring 은 "16흐름"인데 단언은 17이었다 — **이미 갈려 있었다.** 이 회차에
    그 숫자를 만지므로 실제 값으로 맞춘다(problem·diagnosis 17 + counsel 15).

    🔴 **총계를 `len(...)` 으로 빼지 않는다.** 이 단언의 목적은 「흐름을 실수로 지웠는지」를
    잡는 것이고, `len()` 으로 빼면 **지워도 통과한다.**
    """

    assert len(_BE_REQUIRED_FLOW_FIXTURES) == 32
    assert len(set(_BE_REQUIRED_FLOW_FIXTURES.values())) == 32
    missing = {
        flow: fixture
        for flow, fixture in _BE_REQUIRED_FLOW_FIXTURES.items()
        if not (FIXTURE_DIR / f"{fixture}.json").is_file()
    }
    assert not missing, f"BE 필수 흐름에 대응하는 HTTP 픽스처가 없다: {missing}"


def test_problem_studio_openapi_paths_match_the_fixture_flows() -> None:
    """픽스처가 약속한 호출 경로·정상 상태가 실제 앱 OpenAPI에도 있어야 한다."""

    paths = create_app().openapi()["paths"]
    expected = {
        ("/v1/problems", "post", "202"),
        ("/v1/problems/{job_id}", "get", "200"),
        ("/v1/problems/{set_id}/items", "get", "200"),
        ("/v1/problems/{set_id}/items/{slot_index}", "get", "200"),
        ("/v1/diagnosis", "post", "200"),
    }

    for path, method, status in expected:
        assert method in paths[path]
        assert status in paths[path][method]["responses"]


# --------------------------------------------------------------------------
# 앱 구동 준비 — 결정론 Fake로 같은 응답이 나오게 한다
# --------------------------------------------------------------------------


def _generated_item_json() -> str:
    return GeneratedItem(
        area_tag=AreaTag.LANGUAGE,
        type_tag=TypeTag.CONCEPT,
        item_format=ItemFormat.MCQ,
        skill_node_id=_SKILL_NODE_ID,
        stem="문장 성분의 개념과 종류를 설명한 것으로 옳은 것을 고르시오.",
        choices=tuple(
            Choice(
                no=no,
                text=f"문장 구조 선택지 {no}",
                why_wrong=None if no == 1 else f"{no}번은 문법 근거와 다르다.",
                misconception_tag=None if no == 1 else "application_target_substitution",
            )
            for no in range(1, 6)
        ),
        answer=Answer(correct_no=1),
        rationale="승인된 문법 근거에 따르면 1번이 옳다.",
        evidence=(EvidenceAnchor(kind=EvidenceKind.GRAMMAR_RULE, ref="grammar:rule-1"),),
    ).model_dump_json()


def _solve_result_json() -> str:
    return SolveResult(
        chosen=1,
        reasoning="문법 근거를 독립적으로 확인했다.",
        confidence=0.95,
        target_skill_node_id=_SKILL_NODE_ID,
        measured_skill_node_id=_SKILL_NODE_ID,
        aligned=True,
        alignment_confidence=0.95,
        alignment_reason="목표 문법 노드와 일치한다.",
    ).model_dump_json()


async def _unused_diagnosis(_: ProblemRequest) -> DiagnosisResult:
    raise AssertionError("teacher_manual 요청은 진단을 호출하지 않는다")


def _prepare(*, calls: int = 1) -> None:
    reset_shared_agent_runtime()
    problem_router.reset_problem_router()
    problem_router.set_problem_providers(
        ProblemProviders(
            generator=FakeProvider(
                tuple(_generated_item_json() for _ in range(calls)),
                name="http-fixture-generator",
            ),
            verifier=FakeProvider(
                tuple(_solve_result_json() for _ in range(calls)),
                name="http-fixture-verifier",
            ),
            has_dedicated_verifier=False,
        )
    )
    problem_router.set_problem_services(
        graph_context=FakeGraphContextService(), diagnosis=_unused_diagnosis
    )
    problem_router.set_problem_stores(problem_runtime_stores())
    problem_router.set_problem_run_store(InMemoryRunStore())


def _post_body(
    *,
    area_tag: str = "language",
    type_tags: tuple[str, ...] = ("concept",),
    count: int = 1,
) -> dict[str, Any]:
    return {
        "target_kind": "student",
        "target_ref": _STUDENT,
        "target_source": "teacher_manual",
        "manual_targets": [_SKILL_NODE_ID],
        "snapshot_hash": "sha256:" + "b" * 64,
        "taxonomy_version": "v1",
        "area_tag": area_tag,
        "type_tags": list(type_tags),
        "item_format": "mcq",
        "count": count,
        "requested_difficulty": "medium",
    }


# --------------------------------------------------------------------------
# POST /v1/problems
# --------------------------------------------------------------------------


def test_post_problems_request_and_accepted_response() -> None:
    _prepare()
    body = _post_body()

    with TestClient(create_app()) as client:
        response = client.post("/v1/problems", headers=_HEADERS, json=body)

    assert response.status_code == 202
    _fixture("post_problems.request", body)
    _fixture("post_problems.202", response.json())


def test_reading_request_shape_carries_the_passage_spec() -> None:
    """🔴 `language` 밖으로 나갈 때 함께 와야 하는 자료 요청의 정식 형태(AI-BE-06).

    응답은 `language`와 같은 구조라 요청만 고정한다 — 이 픽스처의 값은
    `ProblemRequest`로 검증되므로 형태가 틀리면 여기서 죽는다.
    """
    body = {
        **_post_body(area_tag="reading", type_tags=("infer",), count=2),
        "passage": {
            "area_tag": "reading",
            "domain": "science",
            "topic_hint": "열역학 제2법칙",
            "word_count": 900,
            "sentence_complexity": "standard",
            "paragraph_count": 4,
            "banned_topics_version": "v1",
        },
    }
    ProblemRequest.model_validate(
        {**body, "request_id": "r", "idempotency_key": "k", "tenant_id": _TENANT}
    )

    _fixture("post_problems.request.reading", body)


def test_error_bodies_the_adapter_must_not_retry() -> None:
    """400·409는 재시도 대상이 아니다 — 호출자가 고쳐야 하는 요청이다."""
    _prepare()

    with TestClient(create_app()) as client:
        missing_header = client.post("/v1/problems", json=_post_body())
        reserved_tag = client.post(
            "/v1/problems", headers=_HEADERS, json=_post_body(type_tags=("apply",))
        )
        missing_source = client.post(
            "/v1/problems",
            headers=_HEADERS,
            json=_post_body(area_tag="reading", type_tags=("infer",)),
        )
        client.post("/v1/problems", headers=_HEADERS, json=_post_body())
        conflict = client.post(
            "/v1/problems", headers=_HEADERS, json=_post_body(count=2)
        )

    assert missing_header.status_code == 400
    assert reserved_tag.status_code == 400
    assert missing_source.status_code == 400
    assert conflict.status_code == 409

    _fixture("post_problems.400.missing_header", missing_header.json())
    _fixture("post_problems.400.type_tag_not_supported", reserved_tag.json())
    _fixture(
        "post_problems.400.source_procurement_not_implemented", missing_source.json()
    )
    _fixture("post_problems.409.idempotency_conflict", conflict.json())


# --------------------------------------------------------------------------
# GET /v1/problems/{job_id}
# --------------------------------------------------------------------------


def test_get_problem_terminal_and_pending_fixtures() -> None:
    """adapter의 polling 종료 조건은 `data.status`가 종단인지 하나다."""
    _prepare()

    with TestClient(create_app()) as client:
        posted = client.post("/v1/problems", headers=_HEADERS, json=_post_body())
        job_id = posted.json()["data"]["job_id"]
        succeeded = client.get(
            f"/v1/problems/{job_id}", headers={"X-Tenant-Id": _TENANT}
        )
        absent = client.get(
            f"/v1/problems/{UUID(int=0)}", headers={"X-Tenant-Id": _TENANT}
        )

    assert succeeded.status_code == 200
    assert succeeded.json()["data"]["status"] == JobPhase.SUCCEEDED.value
    assert absent.status_code == 404
    # 🔴 아직 안 끝난 잡의 `Retry-After`는 종단 응답에는 없다 — 있으면 adapter가 계속 돈다.
    assert "Retry-After" not in succeeded.headers

    _fixture("get_problem.succeeded", succeeded.json())
    _fixture("get_problem.404", absent.json())


def test_pending_status_tells_the_adapter_when_to_come_back() -> None:
    """비종단 응답에는 `Retry-After`가 붙는다(AI-BE-01)."""
    _prepare()
    view = problem_router.ProblemJobView(
        job_id=_PLACEHOLDER["job_id"], status=JobPhase.QUEUED
    )
    versions = problem_router.problem_failure_versions()

    from ai.api.envelope import success_envelope

    envelope = success_envelope(
        data=view.model_dump(mode="json"),
        execution_id=_PLACEHOLDER["execution_id"],
        versions=versions,
    )
    _fixture("get_problem.queued", envelope)


# --------------------------------------------------------------------------
# GET /v1/problems/{job_id}/items
# --------------------------------------------------------------------------


def test_items_fixtures_from_a_real_run() -> None:
    """🔴 **목록과 상세가 별개 엔드포인트다** — 그리고 키가 `set_id`다(`job_id`가 아니다).

    adapter 호출 순서: POST → `GET {job_id}` → `data.result.set_id` 회수 →
    `GET {set_id}/items` → 문항 수만큼 `GET {set_id}/items/{slot_index}`.
    목록에는 발문·선지가 **없다.**
    """
    _prepare()

    with TestClient(create_app()) as client:
        posted = client.post("/v1/problems", headers=_HEADERS, json=_post_body())
        job_id = posted.json()["data"]["job_id"]
        fetched = client.get(f"/v1/problems/{job_id}", headers={"X-Tenant-Id": _TENANT})
        set_id = fetched.json()["data"]["result"]["set_id"]
        listed = client.get(
            f"/v1/problems/{set_id}/items", headers={"X-Tenant-Id": _TENANT}
        )
        detail = client.get(
            f"/v1/problems/{set_id}/items/0", headers={"X-Tenant-Id": _TENANT}
        )

    assert listed.status_code == 200
    assert detail.status_code == 200
    assert detail.json()["data"]["job_id"] == job_id
    listed_item = listed.json()["data"]["items"][0]
    assert "stem" not in listed_item, (
        "목록이 본문을 갖게 됐다면 adapter의 N+1 호출 전제가 바뀐 것이다 — BE 통보가 선행이다"
    )

    _fixture("get_problem_items.list", listed.json())
    _fixture("get_problem_items.detail", detail.json())


def test_items_partial_success_fixture() -> None:
    """부분 성공은 실행으로 만들기 어려워 **목록 응답 형태를 직접** 조립한다.

    ⚠ 실측이 아니라 **형태 예시**다 — 상태 4종이 한 응답에 모두 나오는 모습을 adapter가
    보게 하는 것이 목적이고, 실측 픽스처는 `get_problem_items.list`다.
    """
    from ai.api.envelope import success_envelope

    _fixture(
        "get_problem_items.partial_success",
        success_envelope(
            data={
                "set_id": _PLACEHOLDER["set_id"],
                "status_counts": {
                    "verified": 1,
                    "needs_review": 1,
                    "dropped": 1,
                    "verification_unavailable": 1,
                },
                "items": [
                    {
                        "slot_index": 0,
                        "item_id": _PLACEHOLDER["item_id"],
                        "status": ProblemItemStatus.VERIFIED.value,
                        "current_revision_no": 0,
                        "review_reason": None,
                        "failure_reason": None,
                    },
                    {
                        "slot_index": 1,
                        "item_id": _PLACEHOLDER["item_id"],
                        "status": ProblemItemStatus.NEEDS_REVIEW.value,
                        "current_revision_no": 1,
                        "review_reason": "manual_target_first",
                        "failure_reason": None,
                    },
                    {
                        "slot_index": 2,
                        "item_id": _PLACEHOLDER["item_id"],
                        "status": ProblemItemStatus.VERIFICATION_UNAVAILABLE.value,
                        "current_revision_no": 0,
                        "review_reason": None,
                        "failure_reason": None,
                    },
                    # 🔴 폐기 문항은 `item_id`가 없다 — 저장소에 애초에 안 앉는다.
                    {
                        "slot_index": 3,
                        "item_id": None,
                        "status": ProblemItemStatus.DROPPED.value,
                        "current_revision_no": 0,
                        "review_reason": None,
                        "failure_reason": "generation_exhausted",
                    },
                ],
            },
            execution_id=_PLACEHOLDER["execution_id"],
            versions=problem_router.problem_failure_versions(),
        ),
    )


# --------------------------------------------------------------------------
# POST /v1/diagnosis
# --------------------------------------------------------------------------


def _diagnosis_body(
    events: list[dict[str, Any]], *, snapshot_hash: str = "sha256:" + "c" * 64
) -> dict[str, Any]:
    return {
        "student_ref": _STUDENT,
        "period": {"from_date": "2026-06-17", "to_date": "2026-08-12"},
        "as_of": "2026-08-12T09:00:00+00:00",
        "snapshot_hash": snapshot_hash,
        "events": events,
    }


def _diagnosis_events() -> list[dict[str, Any]]:
    def block(
        prefix: str, area: str, type_tag: str, total: int, correct: int
    ) -> list[dict[str, Any]]:
        return [
            {
                "event_id": f"{prefix}-{index:03d}",
                "area_tag": area,
                "type_tag": type_tag,
                "correct": index < correct,
                "occurred_at": "2026-08-01T09:00:00+00:00",
                "tag_confirmed": True,
                "skill_node_id": None,
            }
            for index in range(total)
        ]

    return [
        *block("ok", "language", "concept", 20, 18),
        *block("weak", "language", "infer", 20, 4),
        *block("thin", "reading", "fact", 3, 1),
    ]


def test_diagnosis_grid_fixture_covers_all_four_cell_states() -> None:
    diagnosis_router.reset_diagnosis_router()
    body = _diagnosis_body(_diagnosis_events())

    with TestClient(create_app()) as client:
        response = client.post("/v1/diagnosis", headers=_HEADERS, json=body)

    assert response.status_code == 200
    cells = {cell["key"]: cell for cell in response.json()["data"]["grid"]["cells"]}
    assert cells["language×concept"]["verdict"] == "ok"
    assert cells["language×infer"]["verdict"] == "weak"
    assert cells["reading×fact"]["verdict"] == "unknown"
    assert cells["media×fact"]["verdict"] is None

    _fixture("post_diagnosis.request", body)
    _fixture("post_diagnosis.200", response.json())


def test_diagnosis_error_fixtures() -> None:
    """🔴 **멱등 동일성 판정이 `snapshot_hash`다** — 바디 전체 해시가 아니다.

    `/v1/detect`와 같은 규약이고 `WeaknessMap.snapshot_hash`가 재현 키라서다. adapter가
    다른 입력에 같은 `snapshot_hash`를 재사용하면 **앞선 결과가 그대로 재반환된다** —
    이 테스트가 그 함정을 밟았다가 서로 다른 값으로 갈랐다.
    """
    diagnosis_router.reset_diagnosis_router()
    ghost = _diagnosis_body(
        [
            {
                "event_id": f"ghost-{index:03d}",
                "area_tag": "language",
                "type_tag": "concept",
                "correct": index < 6,
                "occurred_at": "2026-08-01T09:00:00+00:00",
                "tag_confirmed": True,
                "skill_node_id": "language.grammar.does-not-exist",
            }
            for index in range(12)
        ],
        snapshot_hash="sha256:" + "d" * 64,
    )

    with TestClient(create_app()) as client:
        empty = client.post(
            "/v1/diagnosis",
            headers={**_HEADERS, "Idempotency-Key": "pg_" + "0" * 32},
            json=_diagnosis_body([], snapshot_hash="sha256:" + "e" * 64),
        )
        bad_node = client.post(
            "/v1/diagnosis",
            headers={**_HEADERS, "Idempotency-Key": "pg_" + "1" * 32},
            json=ghost,
        )

    assert empty.status_code == 200
    assert bad_node.status_code == 400

    _fixture("post_diagnosis.200.rejected_insufficient", empty.json())
    _fixture("post_diagnosis.400.unknown_skill_node", bad_node.json())


# --------------------------------------------------------------------------
# /v1/counsel/drafts — 상담 초안 3엔드포인트 (99 #96)
# --------------------------------------------------------------------------
#
# 🔴 **이 축은 종전에 통째로 비어 있었다.** 실측(2026-08-19): 이 파일에 `counsel` 문자열
# **0건** · 픽스처 디렉터리에 counsel 파일 **0개**. 그래서 BE 가 코딩할 대상은 apidog 로
# 보낸 **손으로 적은 예시뿐**이었고, 그게 틀렸는지 **아무도 안 쟀다** — 이 파일 첫 문단이
# 금지한 바로 그 상태다("문서의 예시가 낡았다"가 성립하는 구조).
#
# ⚠ **요청 바디는 이 파일이 짓는다** — `_post_body()` 와 같은 관례다. 응답이 아니라
# **BE 가 보내는 것**이라 손으로 적되, 실제로 POST 해서 202 가 나와야 통과하므로
# 모양이 틀리면 여기서 죽는다.

_COUNSEL_TENANT: Final = "tn_counsel_fixture"
_COUNSEL_HEADERS: Final = {
    "X-Tenant-Id": _COUNSEL_TENANT,
    "X-Request-Id": "req-counsel-fixture-0001",
    "Idempotency-Key": "iq_fixture_0001",
}


def _counsel_body(**overrides: object) -> dict[str, Any]:
    """§4-① 요청 예시 — 계약 문서의 모양을 그대로 든다."""
    body: dict[str, Any] = {
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
    body.update(overrides)
    return body


def _counsel_headers(**overrides: str) -> dict[str, str]:
    return {**_COUNSEL_HEADERS, **overrides}


def _prepare_counsel() -> None:
    reset_shared_agent_runtime()
    counsel_router.reset_counsel_stores()


def _enqueue_counsel_jobs_ahead(count: int) -> None:
    """큐에 **앞선 잡**을 넣어 내 잡이 `queued` 로 나가게 한다(FIFO · 99 #21 의 잔여).

    ⚠ 리터럴을 박지 않는다 — 호출자가 상한 `K` 기준으로 정한다.
    """

    async def enqueue() -> None:
        request = CounselDraftRequest.model_validate(_counsel_body())
        for index in range(count):
            await CounselPackEnqueuer(
                supervisor=counsel_router._build_supervisor(),
                context_store=counsel_router._context_store,
                now=counsel_router._clock,
            ).enqueue(
                tenant_id=_COUNSEL_TENANT,
                class_ref=request.class_ref,
                contexts={
                    f"stu_ahead{index}": counsel_router._draft_context(request)
                },
            )

    asyncio.run(enqueue())


def test_counsel_post_request_and_accepted_responses() -> None:
    """① POST — 요청 예시 · 202(워커가 돈 쪽) · 202(`queued`).

    🔴 **202 의 `data` 는 항상 2키다**(`job_id`·`status`) — 초안 실물은 절대 안 실린다.
    그 단언은 아래 `test_counsel_data_keys_differ_across_the_three_endpoints` 가 든다.
    """
    _prepare_counsel()
    body = _counsel_body()

    with TestClient(create_app()) as client:
        succeeded = client.post(
            "/v1/counsel/drafts", headers=_counsel_headers(), json=body
        )

    assert succeeded.status_code == 202, succeeded.text
    assert succeeded.json()["data"]["status"] == "succeeded", succeeded.json()

    # `queued` — 앞선 잡을 상한(K)만큼 쌓으면 회전이 전부 그쪽에 쓰인다.
    _prepare_counsel()
    _enqueue_counsel_jobs_ahead(get_counsel_settings().counsel_inline_drain_max)
    with TestClient(create_app()) as client:
        queued = client.post(
            "/v1/counsel/drafts",
            headers=_counsel_headers(**{"Idempotency-Key": "iq_fixture_queued"}),
            json=body,
        )

    assert queued.status_code == 202, queued.text
    assert queued.json()["data"]["status"] == "queued", queued.json()

    _fixture("post_counsel_drafts.request", body)
    _fixture("post_counsel_drafts.202.succeeded", succeeded.json())
    _fixture("post_counsel_drafts.202.queued", queued.json())


def test_counsel_post_error_bodies() -> None:
    """① 400(헤더 누락 · 미지 라벨) · 409(같은 키 다른 바디).

    ⚠ **400 과 409 는 어댑터가 재시도하면 안 되는 몸통이다** — 재시도해도 같은 답이다.
    """
    _prepare_counsel()

    with TestClient(create_app()) as client:
        missing_header = client.post(
            "/v1/counsel/drafts",
            headers={
                key: value
                for key, value in _counsel_headers().items()
                if key != "Idempotency-Key"
            },
            json=_counsel_body(),
        )
        unknown_label = client.post(
            "/v1/counsel/drafts",
            headers=_counsel_headers(**{"Idempotency-Key": "iq_fixture_label"}),
            json=_counsel_body(labels=["urgent"]),
        )
        first = client.post(
            "/v1/counsel/drafts",
            headers=_counsel_headers(**{"Idempotency-Key": "iq_fixture_conflict"}),
            json=_counsel_body(),
        )
        conflict = client.post(
            "/v1/counsel/drafts",
            headers=_counsel_headers(**{"Idempotency-Key": "iq_fixture_conflict"}),
            json=_counsel_body(class_ref="cl_다름"),
        )

    assert missing_header.status_code == 400, missing_header.text
    assert unknown_label.status_code == 400, unknown_label.text
    assert first.status_code == 202, first.text
    assert conflict.status_code == 409, conflict.text

    _fixture("post_counsel_drafts.400.missing_header", missing_header.json())
    _fixture("post_counsel_drafts.400.unknown_label", unknown_label.json())
    _fixture("post_counsel_drafts.409.idempotency_conflict", conflict.json())


def test_counsel_get_draft_fixtures() -> None:
    """② GET — `generated` · `template_only` · `rejected_insufficient` · 404.

    🔴 **초안 실물이 나오는 자리는 여기 하나다.** 202 에는 절대 안 실린다.
    ⚠ `template_only`·`rejected_insufficient` 는 **워커·LLM 미실행** 경로다 — 잡이 없다.
    """
    _prepare_counsel()

    with TestClient(create_app()) as client:
        posted = client.post(
            "/v1/counsel/drafts", headers=_counsel_headers(), json=_counsel_body()
        )
        assert posted.status_code == 202, posted.text
        generated = client.get(
            f"/v1/counsel/drafts/{posted.json()['data']['job_id']}",
            headers=_counsel_headers(),
        )

        schedule_body = _counsel_body()
        schedule_body["inquiry"] = {**schedule_body["inquiry"], "topic": "schedule"}
        schedule = client.post(
            "/v1/counsel/drafts",
            headers=_counsel_headers(**{"Idempotency-Key": "iq_fixture_schedule"}),
            json=schedule_body,
        )
        assert schedule.status_code == 202, schedule.text
        template_only = client.get(
            f"/v1/counsel/drafts/{schedule.json()['data']['job_id']}",
            headers=_counsel_headers(),
        )

        #: 인용 가능한 근거 = `record_id` 가 있는 fact. 전부 `null` 이면 0건이다.
        bare_body = _counsel_body()
        bare_body["context"] = {
            **bare_body["context"],
            "facts": [{"record_id": None, "summary": "출처 없는 요약"}],
        }
        bare = client.post(
            "/v1/counsel/drafts",
            headers=_counsel_headers(**{"Idempotency-Key": "iq_fixture_bare"}),
            json=bare_body,
        )
        assert bare.status_code == 202, bare.text
        rejected = client.get(
            f"/v1/counsel/drafts/{bare.json()['data']['job_id']}",
            headers=_counsel_headers(),
        )

        not_found = client.get(
            "/v1/counsel/drafts/00000000-0000-4000-8000-00000000ffff",
            headers=_counsel_headers(),
        )

    assert generated.status_code == 200, generated.text
    assert generated.json()["data"]["result"]["draft_status"] == "generated"
    assert template_only.json()["data"]["result"]["draft_status"] == "template_only"
    assert (
        rejected.json()["data"]["result"]["draft_status"] == "rejected_insufficient"
    )
    assert not_found.status_code == 404, not_found.text

    _fixture("get_counsel_draft.generated", generated.json())
    _fixture("get_counsel_draft.template_only", template_only.json())
    _fixture("get_counsel_draft.rejected_insufficient", rejected.json())
    _fixture("get_counsel_draft.404", not_found.json())


def _drain_counsel_queue() -> None:
    """워커 대역 — 남은 잡을 끝까지 돌린다(라우터를 안 지난다).

    ⚠ **늦은 성공**을 만들 때만 쓴다. POST 가 그 자리에서 끝나면 뷰가 완성된 result 와
    함께 캐시돼 GET 이 복원 경로를 **다시 안 지난다.**
    """

    async def run() -> None:
        provider = counsel_router.require_counsel_provider()
        async with open_counsel_pack_runner(
            supervisor=counsel_router._build_supervisor(),
            context_store=counsel_router._context_store,
            step_sink=counsel_router._step_sink,
            draft_store=counsel_router._draft_store,
            pack_store=counsel_router._pack_store,
            planner=provider,
            writer=provider,
            regen_max=counsel_router._REGEN_MAX,
            lease_owner="http-fixture-counsel-worker",
            run_store=counsel_router._run_store,
        ) as runner:
            for _ in range(get_counsel_settings().counsel_inline_drain_max + 2):
                if await runner.run_next(tenant_id=_COUNSEL_TENANT) is None:
                    break

    asyncio.run(run())


def _drop_every_counsel_draft_row() -> int:
    """🔴 **실제 저장소의 초안 행을 지운다** — 보존 만료·초안 저장소 휘발의 대역.

    ⚠ 저장소를 스텁으로 갈아 끼우지 않는다 — 실제 구현의 행을 비운다. 지운 건수를
    돌려주므로 0건이면 「본문 부재」를 **못 만든 것**이고 그 뒤 픽스처는 의미가 없다.
    """
    store = counsel_router._draft_store
    assert isinstance(store, InMemoryDraftResultStore), (
        f"인메모리 초안 저장소가 아니다({type(store).__name__}) — 이 대역이 성립하지 않는다"
    )
    rows: dict[Any, Any] = store._rows  # noqa: SLF001
    dropped = len(rows)
    rows.clear()
    return dropped


def test_counsel_get_draft_body_missing_fixture() -> None:
    """🔴 **잡은 성공인데 본문이 없다** — 200 + `llm_failed`/`draft_body_missing`(99 #75).

    apidog 문서가 이 조합을 BE 에게 예시로 줬다. **그 예시가 참인지 여기서 잰다.**
    ⚠ 도달 경로가 좁다 — POST 가 그 자리에서 끝나면 뷰가 완성돼 복원 경로를 안 지나므로
    **늦은 성공**(앞선 잡 K개 → 미종단 POST → 워커 대역 완료)을 만들어야 한다.
    ⚠ 행동 단언은 `tests/ai/failure/test_counsel_draft_body_missing.py` 가 이미 든다 —
    여기는 **BE 가 읽는 JSON 모양**을 고정한다.
    """
    _prepare_counsel()
    _enqueue_counsel_jobs_ahead(get_counsel_settings().counsel_inline_drain_max)

    with TestClient(create_app()) as client:
        posted = client.post(
            "/v1/counsel/drafts",
            headers=_counsel_headers(**{"Idempotency-Key": "iq_fixture_body_missing"}),
            json=_counsel_body(),
        )
        assert posted.status_code == 202, posted.text
        assert posted.json()["data"]["status"] != "succeeded", (
            f"POST 가 그 자리에서 끝났다({posted.json()['data']['status']}) — "
            "늦은 성공 시나리오가 아니다"
        )
        job_id = posted.json()["data"]["job_id"]
        _drain_counsel_queue()
        assert _drop_every_counsel_draft_row() >= 1, (
            "워커가 끝났는데 초안 행이 0건이다 — 「본문 부재」를 만들지 못했다"
        )
        missing = client.get(
            f"/v1/counsel/drafts/{job_id}", headers=_counsel_headers()
        )

    assert missing.status_code == 200, (
        f"본문 부재가 {missing.status_code}로 나갔다 — 잡힌 정상 성공이 5xx다(99 #75)"
    )
    result = missing.json()["data"]["result"]
    assert result is not None, "복원 경로를 안 탔다 — 이 픽스처가 눈이 멀었다"
    assert result["draft_status"] == "llm_failed", result
    assert result["status_reason"] == "draft_body_missing", result
    assert result["text"] is None, "없는 본문을 지어냈다"

    _fixture("get_counsel_draft.draft_body_missing", missing.json())


def test_counsel_refine_fixtures() -> None:
    """③ refine — 요청 예시 · 200(반영됨) · 400(`Idempotency-Key` 누락).

    🔴 400 은 **PR #277 에서 새로 필수가 된 헤더**다 — 실제로 400 인지 여기서 못 박는다.
    """
    _prepare_counsel()
    refine_body = {"instruction": "조금 더 짧게 정리해 주세요"}

    with TestClient(create_app()) as client:
        posted = client.post(
            "/v1/counsel/drafts", headers=_counsel_headers(), json=_counsel_body()
        )
        assert posted.status_code == 202, posted.text
        job_id = posted.json()["data"]["job_id"]

        applied = client.post(
            f"/v1/counsel/drafts/{job_id}/refine",
            headers=_counsel_headers(**{"Idempotency-Key": "iq_fixture_refine"}),
            json=refine_body,
        )
        #: 🔴 **게이트 거부는 에러가 아니다**(불변식 4) — 200 + `applied=false` 다.
        #: 강사 지시가 게이트를 이기지 못한다: 비교 노출을 시키는 지시는 차단된다.
        blocked = client.post(
            f"/v1/counsel/drafts/{job_id}/refine",
            headers=_counsel_headers(**{"Idempotency-Key": "iq_fixture_blocked"}),
            json={"instruction": "다른 학생들에 비해 잘한다고 써줘"},
        )
        no_key = client.post(
            f"/v1/counsel/drafts/{job_id}/refine",
            headers={
                key: value
                for key, value in _counsel_headers().items()
                if key != "Idempotency-Key"
            },
            json=refine_body,
        )

    assert applied.status_code == 200, applied.text
    assert applied.json()["data"]["applied"] is True, applied.json()
    assert blocked.status_code == 200, blocked.text
    assert blocked.json()["data"]["applied"] is False, blocked.json()
    assert blocked.json()["data"]["blocked_reason"] == "comparison_exposure", (
        blocked.json()
    )
    assert no_key.status_code == 400, no_key.text

    _fixture("post_counsel_refine.request", refine_body)
    _fixture("post_counsel_refine.200.applied", applied.json())
    _fixture("post_counsel_refine.200.blocked", blocked.json())
    _fixture("post_counsel_refine.400.missing_idempotency_key", no_key.json())


# --------------------------------------------------------------------------
# counsel — 픽스처 **파일**에서 직접 재는 계약 단언
# --------------------------------------------------------------------------
#
# 🔴 **왜 파일을 다시 읽나:** 계약 검증(`CounselDraftResult` 등)은 **모델 층**에서 막는데,
# BE 가 실제로 보는 것은 `model_dump(mode="json")` 을 지난 **JSON** 이다. 두 층은 갈릴 수
# 있다(직렬화가 필드를 떨어뜨리는 경우). apidog 로 나간 표가 참인지는 **JSON 에서** 재야 한다.


def _counsel_fixture(name: str) -> dict[str, Any]:
    payload: dict[str, Any] = json.loads(
        (FIXTURE_DIR / f"{name}.json").read_text(encoding="utf-8")
    )
    return payload


def test_a_generated_counsel_draft_always_carries_citations() -> None:
    """🔴 `generated` 면 `citations` 가 **1건 이상**이다 — 불변식 2 의 와이어 단언.

    "근거 없는 초안은 존재할 수 없다" 를 apidog 문서가 BE 에게 약속했다. 모델 층이 막는
    것과 **BE 가 보는 JSON 에 실제로 실리는 것**은 다른 사실이라 여기서 따로 잰다.
    """
    result = _counsel_fixture("get_counsel_draft.generated")["data"]["result"]

    assert result["draft_status"] == "generated", result
    assert len(result["citations"]) >= 1, (
        "generated 인데 citations 가 비었다 — BE 에게 한 약속이 JSON 에서 깨진다"
    )
    for citation in result["citations"]:
        assert citation["record_id"], citation


def test_counsel_data_keys_differ_across_the_three_endpoints() -> None:
    """🔴 세 엔드포인트의 `data` 키 집합이 **서로 다르다.**

    apidog 문서 §0-A 가 BE 에게 명시적으로 경고한 것이다 —
    *"묶으면 202 에 없는 `result` 를 BE 가 기다립니다."*
    **이 단언이 red 면 그 표가 거짓이다.**
    """
    for name in ("post_counsel_drafts.202.succeeded", "post_counsel_drafts.202.queued"):
        assert set(_counsel_fixture(name)["data"]) == {"job_id", "status"}, (
            f"{name} 의 202 data 가 2키가 아니다 — 초안 실물은 202 에 절대 안 실린다"
        )

    assert set(_counsel_fixture("get_counsel_draft.generated")["data"]) == {
        "job_id",
        "status",
        "result",
    }
    assert set(_counsel_fixture("post_counsel_refine.200.applied")["data"]) == {
        "applied",
        "text",
        "citations",
        "blocked_reason",
    }


def test_counsel_meta_versions_carry_every_key_on_all_three() -> None:
    """`meta.versions` 는 세 곳에서 **같은 모양**이다 — apidog 문서가 "10키가 항상 전부
    실림" 이라고 적었다.

    ⚠ **값이 아니라 키 집합을 잰다** — `null` 인 축(threshold·graph·taxonomy 등)도
    **키는 있어야** BE 가 `versions["graph"]` 로 안전하게 읽는다.
    """
    names = (
        "post_counsel_drafts.202.succeeded",
        "get_counsel_draft.generated",
        "post_counsel_refine.200.applied",
    )
    key_sets = [set(_counsel_fixture(name)["meta"]["versions"]) for name in names]

    assert len(key_sets[0]) == 10, sorted(key_sets[0])
    for name, keys in zip(names, key_sets, strict=True):
        assert keys == key_sets[0], f"{name} 의 versions 키가 다른 곳과 갈렸다"
        assert "execution_id" in _counsel_fixture(name)["meta"], name


def test_placeholders_keep_the_shape_of_what_they_replace() -> None:
    """🔴 **자리표시자는 원본의 형태를 지킨다** — 값 하나 고치고 끝내지 않는다.

    픽스처의 존재 이유는 「BE 가 **이 형태를 보고** 만든다」다. 자리표시자가 형태를 감추면
    픽스처로는 통과하고 **운영에서 깨지는** 코드가 나온다 — 실제로 `generated_at` 이
    `…T00:00:00Z` 라 **마이크로초를 감추고** 있었다(실제 응답: `…T08:59:45.176713Z`).

    ⚠ 다음 사람이 또 형태를 깨뜨리지 않게 **검사로 못 박는다.**
    """
    generated_at = _PLACEHOLDER["generated_at"]

    assert generated_at.endswith("Z"), (
        f"UTC 표기가 아니다({generated_at!r}) — 실제 응답은 `Z` 로 끝난다. "
        "`+00:00` 으로 바꾸면 BE 가 오프셋 표기를 기다린다"
    )
    assert re.fullmatch(r".*\.\d{6}Z", generated_at), (
        f"마이크로초 자리가 없다({generated_at!r}) — BE 가 초 단위 패턴을 짜면 "
        "픽스처로는 통과하고 운영에서 깨진다"
    )
    #: 🔴 모양만 맞고 **파싱이 안 되면** 아무 소용이 없다.
    parsed = datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
    assert parsed.tzinfo is not None, generated_at

    #: UUID 셋은 원래 형태를 지키고 있었다 — 회귀만 막는다.
    for key in ("execution_id", "job_id", "set_id", "item_id"):
        UUID(_PLACEHOLDER[key])
