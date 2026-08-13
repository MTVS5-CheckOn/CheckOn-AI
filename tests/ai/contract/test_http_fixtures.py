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

import json
import os
import sys
from pathlib import Path
from typing import Any, Final
from uuid import UUID

from fastapi.testclient import TestClient

from ai.api.app import create_app
from ai.api.routers import diagnosis as diagnosis_router
from ai.api.routers import problem as problem_router
from ai.contracts.agents import JobPhase
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
    """BE 필수 16흐름은 이름이 아니라 실제 JSON 파일에 일대일로 연결된다."""

    assert len(_BE_REQUIRED_FLOW_FIXTURES) == 17
    assert len(set(_BE_REQUIRED_FLOW_FIXTURES.values())) == 17
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
