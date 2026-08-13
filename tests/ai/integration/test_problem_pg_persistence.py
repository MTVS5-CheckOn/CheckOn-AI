"""PG 기본 문제생성의 부모·슬롯 영속과 재시작 조회 회귀."""

from __future__ import annotations

import asyncio
import json
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from ai.api.app import create_app
from ai.api.routers import diagnosis as diagnosis_router
from ai.api.routers import problem as problem_router
from ai.contracts.diagnosis import DiagnosisResult
from ai.contracts.graphrag import GraphContextService
from ai.contracts.problem_generation import (
    Answer,
    Choice,
    EvidenceAnchor,
    EvidenceKind,
    GeneratedItem,
    LiteratureGenre,
    MediaSourceKind,
    MediaSourceRequest,
    PassageDomain,
    PassageDraft,
    PassageRequest,
    ProblemRequest,
    SentenceComplexity,
    SolveResult,
    SourceMaterialDraft,
    SpeechWritingSourceKind,
    SpeechWritingSourceRequest,
    WorkSelection,
)
from ai.contracts.taxonomy import AreaTag, ItemFormat, TypeTag
from ai.db.repositories.run_store import InMemoryRunStore, default_llm_call_collector
from ai.db.session import get_engine
from ai.db.settings import get_db_settings
from ai.db.store_factory import build_run_store, reset_shared_agent_runtime
from ai.problem_generation.application.literature_selector import LiteratureSelector
from ai.problem_generation.application.passage_generator import generated_material_ref
from ai.problem_generation.assembly import problem_runtime_stores
from ai.problem_generation.infrastructure.graph_context import (
    AreaDelegatingGraphContextService,
)
from ai.problem_generation.infrastructure.literature_pool import load_literature_pool
from ai.problem_generation.provider import ProblemProviders

pytestmark = pytest.mark.integration

_FAKES_DIR = Path(__file__).parents[1] / "fakes"
sys.path.insert(0, str(_FAKES_DIR))

from fake_graph_context import FakeGraphContextService  # noqa: E402
from fake_provider import FakeProvider  # noqa: E402
from test_problem_router import (  # noqa: E402
    _SKILL_NODE_ID,
    _body,
    _generated_item_json,
    _solve_result_json,
)

_HTTP_FIXTURE_DIR = Path(__file__).parents[1] / "contract" / "fixtures" / "http"
_HTTP_PLACEHOLDERS = {
    "execution_id": "00000000-0000-4000-8000-0000000000e0",
    "job_id": "00000000-0000-4000-8000-0000000000b0",
    "set_id": "00000000-0000-4000-8000-000000000050",
    "item_id": "00000000-0000-4000-8000-000000000010",
}
_FLOW_NODE_ID = "language.grammar.phoneme.system"


async def _unused_diagnosis(_: ProblemRequest) -> DiagnosisResult:
    raise AssertionError("teacher_manual 요청은 진단을 호출하지 않아야 한다")


def _normalize_http_fixture(value: object) -> object:
    if isinstance(value, dict):
        return {
            key: (
                _HTTP_PLACEHOLDERS[key]
                if key in _HTTP_PLACEHOLDERS and isinstance(item, str)
                else _normalize_http_fixture(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_normalize_http_fixture(item) for item in value]
    return value


def _assert_http_fixture(name: str, payload: object) -> None:
    stored = json.loads(
        (_HTTP_FIXTURE_DIR / f"{name}.json").read_text(encoding="utf-8")
    )
    assert stored == _normalize_http_fixture(payload)


def _headers(tenant_id: str) -> dict[str, str]:
    suffix = uuid.uuid4().hex
    return {
        "X-Tenant-Id": tenant_id,
        "X-Request-Id": f"request-{suffix}",
        "Idempotency-Key": f"idem-{suffix}",
    }


def _prepare_pg(
    *,
    generator_steps: tuple[str, ...] | None = None,
    verifier_steps: tuple[str, ...] | None = None,
    graph_context: GraphContextService | None = None,
) -> tuple[FakeProvider, FakeProvider]:
    reset_shared_agent_runtime()
    problem_router.reset_problem_router()
    generator = FakeProvider(
        generator_steps or (_generated_item_json(),), name="pg-generator"
    )
    verifier = FakeProvider(
        verifier_steps or (_solve_result_json(),), name="pg-verifier"
    )
    problem_router.set_problem_providers(
        ProblemProviders(
            generator=generator,
            verifier=verifier,
            has_dedicated_verifier=False,
        )
    )
    problem_router.set_problem_services(
        graph_context=graph_context or FakeGraphContextService(),
        diagnosis=_unused_diagnosis,
    )
    problem_router.set_problem_stores(problem_runtime_stores())
    problem_router.set_problem_run_store(build_run_store())
    default_llm_call_collector().reset()
    return generator, verifier


async def _persisted_counts(set_id: str, *, database_url: str) -> tuple[int, int]:
    engine = create_async_engine(database_url, poolclass=NullPool)
    try:
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with sessions() as session:
            parent = await session.scalar(
                text("SELECT count(*) FROM problem_set WHERE id = CAST(:set_id AS uuid)"),
                {"set_id": set_id},
            )
            items = await session.scalar(
                text("SELECT count(*) FROM problem_item WHERE set_id = CAST(:set_id AS uuid)"),
                {"set_id": set_id},
            )
        return int(parent or 0), int(items or 0)
    finally:
        await engine.dispose()


async def _persisted_snapshot(set_id: str, *, database_url: str) -> dict[str, object]:
    engine = create_async_engine(database_url, poolclass=NullPool)
    try:
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with sessions() as session:
            snapshot = await session.scalar(
                text(
                    "SELECT snapshot FROM problem_item "
                    "WHERE set_id = CAST(:set_id AS uuid)"
                ),
                {"set_id": set_id},
            )
        assert isinstance(snapshot, dict)
        return snapshot
    finally:
        await engine.dispose()


def _generated_source_item_json(
    *,
    area_tag: AreaTag,
    evidence_kind: EvidenceKind,
    evidence_ref: str,
    quote: str,
) -> str:
    return GeneratedItem(
        area_tag=area_tag,
        type_tag=TypeTag.INFER,
        item_format=ItemFormat.MCQ,
        skill_node_id=_SKILL_NODE_ID,
        stem="제시된 자료를 바탕으로 적절한 것을 고르시오.",
        choices=tuple(
            Choice(
                no=no,
                text=f"생성 자료 선택지 {no}",
                why_wrong=None if no == 1 else f"{no}번은 생성 자료와 다르다.",
            )
            for no in range(1, 6)
        ),
        answer=Answer(correct_no=1),
        rationale="생성 자료 내부 근거에 따르면 1번이 옳다.",
        evidence=(
            EvidenceAnchor(
                kind=evidence_kind,
                ref=evidence_ref,
                quote=quote,
            ),
        ),
    ).model_dump_json()


def _flow_item_json() -> str:
    return GeneratedItem(
        area_tag=AreaTag.LANGUAGE,
        type_tag=TypeTag.INFER,
        item_format=ItemFormat.MCQ,
        skill_node_id=_FLOW_NODE_ID,
        stem="음운 체계의 관계를 추론한 것으로 옳은 것을 고르시오.",
        choices=tuple(
            Choice(
                no=no,
                text=f"음운 체계 선택지 {no}",
                why_wrong=None if no == 1 else f"{no}번은 승인 근거와 다르다.",
            )
            for no in range(1, 6)
        ),
        answer=Answer(correct_no=1),
        rationale="승인된 근거와 대조하면 1번이 옳다.",
        evidence=(
            EvidenceAnchor(kind=EvidenceKind.GRAMMAR_RULE, ref="grammar:rule-1"),
        ),
    ).model_dump_json()


def _flow_solve_json() -> str:
    return SolveResult(
        chosen=1,
        reasoning="승인 근거와 선택지를 독립적으로 대조했다.",
        confidence=0.95,
        target_skill_node_id=_FLOW_NODE_ID,
        measured_skill_node_id=_FLOW_NODE_ID,
        aligned=True,
        alignment_confidence=0.95,
        alignment_reason="요청한 커리큘럼 노드와 일치한다.",
    ).model_dump_json()


def _generated_case(
    area_tag: AreaTag,
) -> tuple[dict[str, object], str, EvidenceKind, str]:
    if area_tag is AreaTag.READING:
        text_value = "생태계의 구성 요소는 서로 영향을 주고받는다.\n\n상호 작용은 균형을 만든다."
        reading_request = PassageRequest(
            domain=PassageDomain.SCIENCE,
            word_count=300,
            sentence_complexity=SentenceComplexity.STANDARD,
            paragraph_count=2,
            banned_topics_version="pg-banned-v1",
        )
        reading_draft = PassageDraft(
            passage_text=text_value,
            paragraph_count=2,
            evidence_anchor_ids=("generated_source",),
        )
        return (
            reading_request.model_dump(mode="json"),
            reading_draft.model_dump_json(),
            EvidenceKind.PASSAGE_SPAN,
            text_value,
        )
    source_request: SpeechWritingSourceRequest | MediaSourceRequest
    if area_tag is AreaTag.SPEECH_WRITING:
        source_request = SpeechWritingSourceRequest(
            source_kind=SpeechWritingSourceKind.PRESENTATION,
            banned_topics_version="pg-banned-v1",
        )
    else:
        source_request = MediaSourceRequest(
            source_kind=MediaSourceKind.PAIRED,
            banned_topics_version="pg-banned-v1",
        )
    text_value = "학생이 두 자료의 관점을 비교해 청중에게 설명했다."
    source_draft = SourceMaterialDraft(
        material_text=text_value,
        evidence_anchor_ids=("generated_source",),
    )
    return (
        source_request.model_dump(mode="json"),
        source_draft.model_dump_json(),
        EvidenceKind.SOURCE_CLAIM,
        text_value,
    )


def test_pg_post_persists_items_and_cache_miss_recovers_reads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant_id = f"tenant-pg-{uuid.uuid4().hex[:10]}"
    headers = _headers(tenant_id)
    monkeypatch.setenv("STORE_BACKEND", "pg")
    monkeypatch.setenv("PG_DRAIN_ENABLED", "false")
    get_db_settings.cache_clear()
    get_engine.cache_clear()
    database_url = get_db_settings().database_url
    _prepare_pg()

    try:
        with TestClient(create_app(), raise_server_exceptions=False) as client:
            posted = client.post("/v1/problems", headers=headers, json=_body())
            assert posted.status_code == 202, posted.text
            job_id = posted.json()["data"]["job_id"]
            first_job = client.get(
                f"/v1/problems/{job_id}", headers={"X-Tenant-Id": tenant_id}
            )
            assert first_job.status_code == 200, first_job.text
            execution_id = posted.json()["meta"]["execution_id"]
            assert first_job.json()["meta"]["execution_id"] == execution_id
            set_id = first_job.json()["data"]["result"]["set_id"]
            assert asyncio.run(
                _persisted_counts(set_id, database_url=database_url)
            ) == (1, 1)

            problem_router._views.clear()  # noqa: SLF001 — 프로세스 재시작 캐시 소실 재현

            restarted_job = client.get(
                f"/v1/problems/{job_id}", headers={"X-Tenant-Id": tenant_id}
            )
            restarted_items = client.get(
                f"/v1/problems/{set_id}/items", headers={"X-Tenant-Id": tenant_id}
            )
            hidden_job = client.get(
                f"/v1/problems/{job_id}", headers={"X-Tenant-Id": "tenant-other"}
            )
            hidden_items = client.get(
                f"/v1/problems/{set_id}/items",
                headers={"X-Tenant-Id": "tenant-other"},
            )

        assert restarted_job.status_code == 200, restarted_job.text
        assert restarted_items.status_code == 200, restarted_items.text
        assert restarted_job.json()["meta"]["execution_id"] == execution_id
        assert restarted_items.json()["meta"]["execution_id"] == execution_id
        assert hidden_job.status_code == 404
        assert hidden_items.status_code == 404
    finally:
        monkeypatch.setenv("STORE_BACKEND", "memory")
        get_db_settings.cache_clear()
        get_engine.cache_clear()
        reset_shared_agent_runtime()
        problem_router.reset_problem_router()


def test_pg_cache_loss_recovers_original_request_for_revision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant_id = f"tenant-revision-{uuid.uuid4().hex[:10]}"
    headers = _headers(tenant_id)
    monkeypatch.setenv("STORE_BACKEND", "pg")
    monkeypatch.setenv("PG_DRAIN_ENABLED", "false")
    get_db_settings.cache_clear()
    get_engine.cache_clear()
    _prepare_pg(
        generator_steps=(_generated_item_json(), _generated_item_json()),
        verifier_steps=(_solve_result_json(), _solve_result_json()),
    )

    try:
        with TestClient(create_app(), raise_server_exceptions=False) as client:
            posted = client.post("/v1/problems", headers=headers, json=_body())
            assert posted.status_code == 202, posted.text
            job_id = posted.json()["data"]["job_id"]
            job = client.get(
                f"/v1/problems/{job_id}", headers={"X-Tenant-Id": tenant_id}
            )
            assert job.status_code == 200, job.text
            set_id = job.json()["data"]["result"]["set_id"]

            problem_router._views.clear()  # noqa: SLF001 — 프로세스 재시작 캐시 소실 재현
            restored_detail = client.get(
                f"/v1/problems/{set_id}/items/0",
                headers={"X-Tenant-Id": tenant_id},
            )
            revised = client.post(
                f"/v1/problems/{set_id}/items/0/revisions",
                headers={
                    **headers,
                    "X-Request-Id": f"request-revision-{uuid.uuid4().hex}",
                    "Idempotency-Key": f"idem-revision-{uuid.uuid4().hex}",
                },
                json={
                    "base_revision_no": 0,
                    "revision_kind": "ai_refine",
                    "instruction": "발문을 더 명확하게 다듬어 주세요.",
                },
            )

        assert revised.status_code == 200, revised.text
        assert revised.json()["data"]["revision"]["revision_no"] == 1
        assert restored_detail.status_code == 200, restored_detail.text
        assert "job_id" not in restored_detail.json()["data"]
        _assert_http_fixture(
            "get_problem_items.detail.cache_lost", restored_detail.json()
        )
    finally:
        monkeypatch.setenv("STORE_BACKEND", "memory")
        get_db_settings.cache_clear()
        get_engine.cache_clear()
        reset_shared_agent_runtime()
        problem_router.reset_problem_router()


def test_step1_selection_to_step4_teacher_manual_flow_survives_cache_loss(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant_id = f"tenant-flow-{uuid.uuid4().hex[:10]}"
    headers = _headers(tenant_id)
    monkeypatch.setenv("STORE_BACKEND", "pg")
    monkeypatch.setenv("PG_DRAIN_ENABLED", "false")
    get_db_settings.cache_clear()
    get_engine.cache_clear()
    diagnosis_router.reset_diagnosis_router()
    diagnosis_router.set_diagnosis_run_store(InMemoryRunStore())
    generator, verifier = _prepare_pg(
        generator_steps=(_flow_item_json(), _flow_item_json(), _flow_item_json()),
        verifier_steps=(_flow_solve_json(), _flow_solve_json(), _flow_solve_json()),
    )
    occurred_at = datetime(2026, 8, 14, 9, 0, tzinfo=UTC).isoformat()
    diagnosis_body = {
        "student_ref": "student-flow",
        "period": {"from_date": "2026-08-01", "to_date": "2026-08-14"},
        "as_of": occurred_at,
        "snapshot_hash": "sha256:step1-step4-flow",
        "events": [
            {
                "event_id": f"flow-{index:03d}",
                "area_tag": "language",
                "type_tag": "infer",
                "correct": index < 4,
                "occurred_at": occurred_at,
                "tag_confirmed": True,
                "skill_node_id": _FLOW_NODE_ID,
            }
            for index in range(20)
        ],
    }

    try:
        with TestClient(create_app(), raise_server_exceptions=False) as client:
            # 1~3. Step1 진단 → 5×4 셀과 선택 가능한 노드 회수.
            diagnosed = client.post(
                "/v1/diagnosis", headers=headers, json=diagnosis_body
            )
            assert diagnosed.status_code == 200, diagnosed.text
            diagnosis_data = diagnosed.json()["data"]
            assert len(diagnosis_data["grid"]["cells"]) == 20
            diagnosed_node_ids = tuple(diagnosis_data["weakness_map"]["nodes"])
            assert diagnosed_node_ids, "Step1 진단 응답에 Step2에서 선택할 노드가 없다"
            selected_node_id = diagnosed_node_ids[0]

            # 4~6. Step2가 고른 진단 응답 노드를 teacher_manual 요청에 싣는다.
            problem_body = _body(target_ref="student-flow")
            problem_body.update(
                {
                    "manual_targets": [selected_node_id],
                    "snapshot_hash": diagnosis_data["weakness_map"]["snapshot_hash"],
                }
            )
            posted = client.post("/v1/problems", headers=_headers(tenant_id), json=problem_body)
            assert posted.status_code == 202, posted.text
            job_id = posted.json()["data"]["job_id"]

            # Retry-After의 비종단/종단 계약은 problem_router·HTTP fixture 전용 검사가 맡는다.
            job = client.get(
                f"/v1/problems/{job_id}", headers={"X-Tenant-Id": tenant_id}
            )
            assert job.status_code == 200, job.text
            assert job.json()["data"]["status"] == "succeeded"
            result = job.json()["data"]["result"]
            set_id = result["set_id"]

            # 9~10. Step3 목록·상세와 캐시 존재 job_id를 확인한다.
            listed = client.get(
                f"/v1/problems/{set_id}/items", headers={"X-Tenant-Id": tenant_id}
            )
            detailed = client.get(
                f"/v1/problems/{set_id}/items/0",
                headers={"X-Tenant-Id": tenant_id},
            )
            assert listed.status_code == detailed.status_code == 200
            assert detailed.json()["data"]["job_id"] == job_id

            # 11~12. AI 수정 뒤 Step4가 사용할 검증본을 읽어도 추가 호출은 없다.
            first_revision = client.post(
                f"/v1/problems/{set_id}/items/0/revisions",
                headers=_headers(tenant_id),
                json={
                    "base_revision_no": 0,
                    "revision_kind": "ai_refine",
                    "instruction": "발문을 더 명확하게 다듬어 주세요.",
                },
            )
            assert first_revision.status_code == 200, first_revision.text
            calls_after_revision = (len(generator.requests), len(verifier.requests))
            step4_detail = client.get(
                f"/v1/problems/{set_id}/items/0",
                headers={"X-Tenant-Id": tenant_id},
            )
            assert step4_detail.json()["data"]["verification"]["release_decision"] == "passed"
            assert (len(generator.requests), len(verifier.requests)) == calls_after_revision

            # 13. 캐시 소실 뒤 원 요청 정본으로 재진입하고 테넌트 은닉을 유지한다.
            problem_router._views.clear()  # noqa: SLF001 — 프로세스 재시작 캐시 소실 재현
            second_revision = client.post(
                f"/v1/problems/{set_id}/items/0/revisions",
                headers=_headers(tenant_id),
                json={
                    "base_revision_no": 1,
                    "revision_kind": "ai_refine",
                    "instruction": "선지 표현도 더 분명하게 다듬어 주세요.",
                },
            )
            restored_detail = client.get(
                f"/v1/problems/{set_id}/items/0",
                headers={"X-Tenant-Id": tenant_id},
            )
            hidden = client.get(
                f"/v1/problems/{set_id}/items/0",
                headers={"X-Tenant-Id": "tenant-other"},
            )

        assert second_revision.status_code == 200, second_revision.text
        assert second_revision.json()["data"]["revision"]["revision_no"] == 2
        assert restored_detail.status_code == 200, restored_detail.text
        assert "job_id" not in restored_detail.json()["data"]
        assert restored_detail.json()["data"]["current_revision_no"] == 2
        assert hidden.status_code == 404
    finally:
        monkeypatch.setenv("STORE_BACKEND", "memory")
        get_db_settings.cache_clear()
        get_engine.cache_clear()
        reset_shared_agent_runtime()
        diagnosis_router.reset_diagnosis_router()
        problem_router.reset_problem_router()


def test_pg_dropped_slot_survives_cache_loss_with_reasons(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant_id = f"tenant-drop-{uuid.uuid4().hex[:10]}"
    headers = _headers(tenant_id)
    monkeypatch.setenv("STORE_BACKEND", "pg")
    monkeypatch.setenv("PG_DRAIN_ENABLED", "false")
    get_db_settings.cache_clear()
    get_engine.cache_clear()
    database_url = get_db_settings().database_url
    _prepare_pg(generator_steps=("not-json", "not-json", "not-json"))

    try:
        with TestClient(create_app(), raise_server_exceptions=False) as client:
            posted = client.post("/v1/problems", headers=headers, json=_body())
            assert posted.status_code == 202, posted.text
            job_id = posted.json()["data"]["job_id"]
            first_job = client.get(
                f"/v1/problems/{job_id}", headers={"X-Tenant-Id": tenant_id}
            )
            set_id = first_job.json()["data"]["result"]["set_id"]
            assert asyncio.run(
                _persisted_counts(set_id, database_url=database_url)
            ) == (1, 1)

            problem_router._views.clear()  # noqa: SLF001 — 프로세스 재시작 캐시 소실 재현
            restarted_job = client.get(
                f"/v1/problems/{job_id}", headers={"X-Tenant-Id": tenant_id}
            )
            restarted_items = client.get(
                f"/v1/problems/{set_id}/items", headers={"X-Tenant-Id": tenant_id}
            )

        assert restarted_job.status_code == 200, restarted_job.text
        result = restarted_job.json()["data"]["result"]
        assert result["items"][0]["status"] == "dropped"
        assert result["dropped_reasons"] == ["generation_exhausted"]
        assert restarted_items.status_code == 200, restarted_items.text
        assert restarted_items.json()["data"]["status_counts"]["dropped"] == 1
    finally:
        monkeypatch.setenv("STORE_BACKEND", "memory")
        get_db_settings.cache_clear()
        get_engine.cache_clear()
        reset_shared_agent_runtime()
        problem_router.reset_problem_router()


@pytest.mark.parametrize(
    "area_tag",
    [AreaTag.READING, AreaTag.SPEECH_WRITING, AreaTag.MEDIA],
)
def test_generated_source_areas_persist_internal_evidence_to_pg(
    area_tag: AreaTag,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant_id = f"tenant-{area_tag.value}-{uuid.uuid4().hex[:10]}"
    headers = _headers(tenant_id)
    source_request, draft_json, evidence_kind, source_text = _generated_case(area_tag)
    evidence_ref = (
        generated_material_ref(kind="passage_span", text=source_text)
        if area_tag is AreaTag.READING
        else generated_material_ref(kind="source_claim", text=source_text)
    )
    item_json = _generated_source_item_json(
        area_tag=area_tag,
        evidence_kind=evidence_kind,
        evidence_ref=evidence_ref,
        quote="모델이 낸 자료 밖 인용",
    )
    monkeypatch.setenv("STORE_BACKEND", "pg")
    monkeypatch.setenv("PG_DRAIN_ENABLED", "false")
    get_db_settings.cache_clear()
    get_engine.cache_clear()
    database_url = get_db_settings().database_url
    _prepare_pg(
        generator_steps=(draft_json, item_json),
        graph_context=AreaDelegatingGraphContextService(),
    )
    body = _body(area_tag=area_tag.value)
    body["passage"] = source_request

    try:
        with TestClient(create_app(), raise_server_exceptions=False) as client:
            posted = client.post("/v1/problems", headers=headers, json=body)
            assert posted.status_code == 202, posted.text
            job_id = posted.json()["data"]["job_id"]
            job = client.get(
                f"/v1/problems/{job_id}",
                headers={"X-Tenant-Id": tenant_id},
            )
            assert job.status_code == 200, job.text
            result = job.json()["data"]["result"]
            assert result["status"] == "generated", result
            set_id = result["set_id"]

        assert asyncio.run(
            _persisted_counts(set_id, database_url=database_url)
        ) == (1, 1)
        snapshot = asyncio.run(
            _persisted_snapshot(set_id, database_url=database_url)
        )
        item = snapshot["item"]
        assert isinstance(item, dict)
        evidence = item["evidence"]
        assert isinstance(evidence, list) and len(evidence) == 1
        assert evidence[0]["ref"] == evidence_ref
        assert evidence[0]["quote"] == source_text
    finally:
        monkeypatch.setenv("STORE_BACKEND", "memory")
        get_db_settings.cache_clear()
        get_engine.cache_clear()
        reset_shared_agent_runtime()
        problem_router.reset_problem_router()


def test_literature_expired_excerpt_persists_exact_original_to_pg(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant_id = f"tenant-literature-{uuid.uuid4().hex[:10]}"
    headers = _headers(tenant_id)
    selection = WorkSelection(
        genre=LiteratureGenre.MODERN_NOVEL,
        era="근대",
        concept_keywords=("달",),
    )
    excerpt = LiteratureSelector(load_literature_pool()).select(selection)
    item_json = _generated_source_item_json(
        area_tag=AreaTag.LITERATURE,
        evidence_kind=EvidenceKind.WORK_SPAN,
        evidence_ref=excerpt.evidence_ref,
        quote="모델이 변형한 원문",
    )
    monkeypatch.setenv("STORE_BACKEND", "pg")
    monkeypatch.setenv("PG_DRAIN_ENABLED", "false")
    get_db_settings.cache_clear()
    get_engine.cache_clear()
    database_url = get_db_settings().database_url
    _prepare_pg(
        generator_steps=(item_json,),
        graph_context=AreaDelegatingGraphContextService(),
    )
    body = _body(area_tag=AreaTag.LITERATURE.value)
    body["work_selection"] = selection.model_dump(mode="json")

    try:
        with TestClient(create_app(), raise_server_exceptions=False) as client:
            posted = client.post("/v1/problems", headers=headers, json=body)
            assert posted.status_code == 202, posted.text
            job_id = posted.json()["data"]["job_id"]
            job = client.get(
                f"/v1/problems/{job_id}",
                headers={"X-Tenant-Id": tenant_id},
            )
            assert job.status_code == 200, job.text
            result = job.json()["data"]["result"]
            assert result["status"] == "generated", result
            set_id = result["set_id"]

        assert asyncio.run(
            _persisted_counts(set_id, database_url=database_url)
        ) == (1, 1)
        snapshot = asyncio.run(
            _persisted_snapshot(set_id, database_url=database_url)
        )
        item = snapshot["item"]
        assert isinstance(item, dict)
        evidence = item["evidence"]
        assert isinstance(evidence, list) and len(evidence) == 1
        assert evidence[0]["ref"] == excerpt.evidence_ref
        assert evidence[0]["quote"] == excerpt.quote
    finally:
        monkeypatch.setenv("STORE_BACKEND", "memory")
        get_db_settings.cache_clear()
        get_engine.cache_clear()
        reset_shared_agent_runtime()
        problem_router.reset_problem_router()
