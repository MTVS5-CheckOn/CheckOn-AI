"""문제출제 조립 진입점의 오프라인 end-to-end 스모크."""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import UUID

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from ai.contracts.diagnosis import (
    CellVerdict,
    DiagnosisEvent,
    DiagnosisInput,
    DiagnosisResult,
    DiagnosisStatus,
    NodeVerdict,
    Period,
    WeaknessCell,
    WeaknessMap,
    WeaknessNode,
)
from ai.contracts.execution import Capability, ExecutionContext, VersionSet
from ai.contracts.llm import ModelRole
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
    ProblemItemStatus,
    ProblemRequest,
    ProblemSetResult,
    ProblemSetStatus,
    SentenceComplexity,
    SolveResult,
    SourceMaterialDraft,
    SourceMaterialRequest,
    SpeechWritingSourceKind,
    SpeechWritingSourceRequest,
    TargetKind,
    TargetSource,
    WorkSelection,
)
from ai.contracts.taxonomy import AreaTag, ItemFormat, TypeTag
from ai.diagnosis.diagnoser import DiagnosisConfig, diagnose
from ai.diagnosis.skill_graph import load_skill_graph
from ai.llm.gateway import LlmGateway
from ai.problem_generation.application import workflow as workflow_module
from ai.problem_generation.application.literature_selector import LiteratureSelector
from ai.problem_generation.application.passage_generator import generated_material_ref
from ai.problem_generation.bootstrap import build_problem_workflow
from ai.problem_generation.infrastructure.config import load_verify_config
from ai.problem_generation.infrastructure.graph_context import (
    AreaDelegatingGraphContextService,
)
from ai.problem_generation.infrastructure.literature_pool import load_literature_pool
from ai.problem_generation.infrastructure.memory_store import (
    InMemoryCandidateStore,
    InMemoryProblemItemStore,
)
from ai.runtime.tracing import TRACING_ENV_SYNONYMS

_FAKES_DIR = Path(__file__).parents[1] / "fakes"
sys.path.insert(0, str(_FAKES_DIR))

from fake_graph_context import FakeGraphContextService  # noqa: E402
from fake_provider import FakeProvider  # noqa: E402

_GRAPH_VERSION = "curriculum-five-area-v1"
_SKILL_NODE_ID = "grammar.sentence-structure"
_SNAPSHOT_HASH = "snapshot-pg-bootstrap-smoke"
_TAXONOMY_VERSION = "v1"
_GRAPH_PATH = (
    Path(__file__).resolve().parents[3]
    / "src"
    / "ai"
    / "diagnosis"
    / "data"
    / "curriculum_graph.yaml"
)


async def _diagnose(_: ProblemRequest) -> DiagnosisResult:
    return DiagnosisResult(
        status=DiagnosisStatus.GENERATED,
        weakness_map=WeaknessMap(
            graph_version=_GRAPH_VERSION,
            taxonomy_version=_TAXONOMY_VERSION,
            config_version="verify-config.v1",
            snapshot_hash=_SNAPSHOT_HASH,
            cells={
                "language×infer": WeaknessCell(
                    acc=0.4,
                    n=10,
                    verdict=CellVerdict.WEAK,
                    severity=0.8,
                )
            },
            nodes={
                _SKILL_NODE_ID: WeaknessNode(
                    verdict=NodeVerdict.WEAK_CONFIRMED,
                    basis=("cell:language×infer",),
                )
            },
        ),
    )


def _request() -> ProblemRequest:
    return ProblemRequest(
        request_id="req-pg-bootstrap-smoke",
        idempotency_key="idem-pg-bootstrap-smoke",
        tenant_id="tenant-pg-smoke",
        target_kind=TargetKind.STUDENT,
        target_ref="student-pg-smoke",
        target_source=TargetSource.WEAKNESS_AUTO,
        snapshot_hash=_SNAPSHOT_HASH,
        taxonomy_version=_TAXONOMY_VERSION,
        area_tag=AreaTag.LANGUAGE,
        type_tags=(TypeTag.INFER,),
        item_format=ItemFormat.MCQ,
        count=1,
    )


def _reading_request() -> ProblemRequest:
    return ProblemRequest(
        request_id="req-pg-reading-smoke",
        idempotency_key="idem-pg-reading-smoke",
        tenant_id="tenant-pg-smoke",
        target_kind=TargetKind.STUDENT,
        target_ref="student-pg-smoke",
        target_source=TargetSource.TEACHER_MANUAL,
        manual_targets=(_SKILL_NODE_ID,),
        snapshot_hash=_SNAPSHOT_HASH,
        taxonomy_version=_TAXONOMY_VERSION,
        area_tag=AreaTag.READING,
        type_tags=(TypeTag.INFER,),
        item_format=ItemFormat.MCQ,
        count=1,
        passage=PassageRequest(
            domain=PassageDomain.SCIENCE,
            topic_hint="생태계의 상호 작용",
            word_count=500,
            sentence_complexity=SentenceComplexity.STANDARD,
            paragraph_count=2,
            banned_topics_version="pg-banned-v1",
        ),
    )


def _literature_selection() -> WorkSelection:
    return WorkSelection(
        genre=LiteratureGenre.MODERN_NOVEL,
        era="근대",
        concept_keywords=("달",),
    )


def _literature_request() -> ProblemRequest:
    return ProblemRequest(
        request_id="req-pg-literature-smoke",
        idempotency_key="idem-pg-literature-smoke",
        tenant_id="tenant-pg-smoke",
        target_kind=TargetKind.STUDENT,
        target_ref="student-pg-smoke",
        target_source=TargetSource.TEACHER_MANUAL,
        manual_targets=("literature.structure.composition",),
        snapshot_hash=_SNAPSHOT_HASH,
        taxonomy_version=_TAXONOMY_VERSION,
        area_tag=AreaTag.LITERATURE,
        type_tags=(TypeTag.INFER,),
        item_format=ItemFormat.MCQ,
        count=1,
        work_selection=_literature_selection(),
    )


def _execution_context() -> ExecutionContext:
    return ExecutionContext(
        execution_id=UUID("22222222-2222-4222-8222-222222222222"),
        tenant_id="tenant-pg-smoke",
        capability=Capability.PROBLEM_GENERATION,
        input_snapshot_hash=_SNAPSHOT_HASH,
        versions=VersionSet(
            pipeline_version="pipeline-v1",
            engine_version="engine-v1",
            schema_version="schema-v1",
            contract_version="contract-v1",
            prompt_version="v4",
            graph_version=_GRAPH_VERSION,
            taxonomy_version=_TAXONOMY_VERSION,
            verify_config_version="verify-config.v1",
        ),
    )


def _generated_item_json() -> str:
    item = GeneratedItem(
        area_tag=AreaTag.LANGUAGE,
        type_tag=TypeTag.INFER,
        item_format=ItemFormat.MCQ,
        skill_node_id=_SKILL_NODE_ID,
        stem="다음 중 문장 구조에 대한 설명으로 옳은 것을 고르시오.",
        choices=tuple(
            Choice(
                no=no,
                text=f"문장 구조에 대한 선택지 {no}",
                why_wrong=None if no == 1 else f"{no}번은 문법 근거와 다르다.",
            )
            for no in range(1, 6)
        ),
        answer=Answer(correct_no=1),
        rationale="승인된 문법 근거에 따르면 1번이 옳다.",
        evidence=(
            EvidenceAnchor(
                kind=EvidenceKind.GRAMMAR_RULE,
                ref="grammar:rule-1",
            ),
        ),
    )
    return item.model_dump_json()


def _passage_draft() -> PassageDraft:
    return PassageDraft(
        passage_text=(
            "오늘 수업은 비문학 독해였어요.\n\n"
            "글을 읽고 핵심 내용을 정리했어요."
        ),
        paragraph_count=2,
        evidence_anchor_ids=("generated_source",),
    )


def _reading_item_json() -> str:
    passage = _passage_draft().passage_text
    evidence_ref = generated_material_ref(kind="passage_span", text=passage)
    item = GeneratedItem(
        area_tag=AreaTag.READING,
        type_tag=TypeTag.INFER,
        item_format=ItemFormat.MCQ,
        skill_node_id=_SKILL_NODE_ID,
        stem="윗글의 내용과 일치하는 것을 고르시오.",
        choices=tuple(
            Choice(
                no=no,
                text=f"비문학 독해 선택지 {no}",
                why_wrong=None if no == 1 else f"{no}번은 글의 핵심 내용과 달라요.",
            )
            for no in range(1, 6)
        ),
        answer=Answer(correct_no=1),
        rationale="두 문단의 내용을 함께 보면 1번이 맞아요.",
        evidence=(
            EvidenceAnchor(
                kind=EvidenceKind.PASSAGE_SPAN,
                ref=evidence_ref,
                quote="모델이 낸 자료 밖 인용",
            ),
        ),
    )
    return item.model_dump_json()


def _literature_item_json() -> str:
    excerpt = LiteratureSelector(load_literature_pool()).select(_literature_selection())
    return GeneratedItem(
        area_tag=AreaTag.LITERATURE,
        type_tag=TypeTag.INFER,
        item_format=ItemFormat.MCQ,
        skill_node_id="literature.structure.composition",
        stem="윗글의 서술 방식으로 적절한 것을 고르시오.",
        choices=tuple(
            Choice(
                no=no,
                text=f"문학 작품 선택지 {no}",
                why_wrong=None if no == 1 else f"{no}번은 원문과 다르다.",
            )
            for no in range(1, 6)
        ),
        answer=Answer(correct_no=1),
        rationale="선택된 만료 원문에 따르면 1번이 옳다.",
        evidence=(
            EvidenceAnchor(
                kind=EvidenceKind.WORK_SPAN,
                ref=excerpt.evidence_ref,
                quote="모델이 변형한 원문",
            ),
        ),
    ).model_dump_json()


def _literature_solve_json() -> str:
    return SolveResult(
        chosen=1,
        reasoning="만료 원문을 독립적으로 확인했다.",
        confidence=0.95,
        target_skill_node_id="literature.structure.composition",
        measured_skill_node_id="literature.structure.composition",
        aligned=True,
        alignment_confidence=0.95,
        alignment_reason="목표 문학 노드와 일치한다.",
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


def _source_material_request(area_tag: AreaTag, skill_node_id: str) -> ProblemRequest:
    source_request: SourceMaterialRequest
    if area_tag is AreaTag.SPEECH_WRITING:
        source_request = SpeechWritingSourceRequest(
            source_kind=SpeechWritingSourceKind.PRESENTATION,
            topic_hint="교내 자원 절약",
            banned_topics_version="pg-banned-v1",
        )
    elif area_tag is AreaTag.MEDIA:
        source_request = MediaSourceRequest(
            source_kind=MediaSourceKind.PAIRED,
            topic_hint="온라인 정보 검증",
            banned_topics_version="pg-banned-v1",
        )
    else:
        raise AssertionError(f"생성 자료를 지원하지 않는 테스트 영역: {area_tag.value}")
    return ProblemRequest(
        request_id=f"req-pg-{area_tag.value}-smoke",
        idempotency_key=f"idem-pg-{area_tag.value}-smoke",
        tenant_id="tenant-pg-smoke",
        target_kind=TargetKind.STUDENT,
        target_ref="student-pg-smoke",
        target_source=TargetSource.WEAKNESS_AUTO,
        snapshot_hash=_SNAPSHOT_HASH,
        taxonomy_version=_TAXONOMY_VERSION,
        area_tag=area_tag,
        type_tags=(TypeTag.CRITIC,),
        item_format=ItemFormat.MCQ,
        count=1,
        passage=source_request,
    )


def _source_material_item_json(area_tag: AreaTag, skill_node_id: str) -> str:
    material_text = "학생 A가 승인 근거를 활용해 자료를 구성했다."
    evidence_ref = generated_material_ref(kind="source_claim", text=material_text)
    return GeneratedItem(
        area_tag=area_tag,
        type_tag=TypeTag.CRITIC,
        item_format=ItemFormat.MCQ,
        skill_node_id=skill_node_id,
        stem="제시된 자료의 정보 활용 방식으로 적절한 것을 고르시오.",
        choices=tuple(
            Choice(
                no=no,
                text=f"자료 활용 방식에 대한 선택지 {no}",
                why_wrong=None if no == 1 else f"{no}번은 승인 근거와 다르다.",
            )
            for no in range(1, 6)
        ),
        answer=Answer(correct_no=1),
        rationale="승인된 자료 근거에 따르면 1번이 옳다.",
        evidence=(
            EvidenceAnchor(
                kind=EvidenceKind.SOURCE_CLAIM,
                ref=evidence_ref,
                quote="모델이 낸 자료 밖 인용",
            ),
        ),
    ).model_dump_json()


def _source_material_solve_json(skill_node_id: str) -> str:
    return SolveResult(
        chosen=1,
        reasoning="자료와 선택지를 독립적으로 대조했다.",
        confidence=0.95,
        target_skill_node_id=skill_node_id,
        measured_skill_node_id=skill_node_id,
        aligned=True,
        alignment_confidence=0.95,
        alignment_reason="진단에서 산출한 비언어 영역 노드와 일치한다.",
    ).model_dump_json()


def _actual_diagnosis(
    area_tag: AreaTag,
    skill_node_id: str,
    comparison_node_id: str,
) -> DiagnosisResult:
    now = datetime(2026, 8, 12, tzinfo=UTC)
    events = tuple(
        DiagnosisEvent(
            event_id=f"weak-{index}",
            area_tag=area_tag,
            type_tag=TypeTag.CRITIC,
            skill_node_id=skill_node_id,
            correct=False,
            occurred_at=now,
            tag_confirmed=True,
        )
        for index in range(10)
    ) + tuple(
        DiagnosisEvent(
            event_id=f"ok-{index}",
            area_tag=area_tag,
            type_tag=TypeTag.FACT,
            skill_node_id=comparison_node_id,
            correct=True,
            occurred_at=now,
            tag_confirmed=True,
        )
        for index in range(10)
    )
    verify_config = load_verify_config()
    result = diagnose(
        DiagnosisInput(
            tenant_id="tenant-pg-smoke",
            student_ref="student-pg-smoke",
            period=Period(from_date=date(2026, 8, 1), to_date=date(2026, 8, 12)),
            as_of=now,
            snapshot_hash=_SNAPSHOT_HASH,
            events=events,
        ),
        load_skill_graph(_GRAPH_PATH, expected_taxonomy_version=_TAXONOMY_VERSION),
        DiagnosisConfig(
            cell_min_items=10,
            relative_cut_pp=verify_config.diag_relative_cut_pp,
            severity_saturation=verify_config.diag_severity_saturation,
            decay=verify_config.diag_decay,
            propagate_threshold=verify_config.diag_propagate_threshold,
            suspect_damping=verify_config.diag_suspect_damping,
            node_min_items=verify_config.node_min_items,
        ),
        graph_version=_GRAPH_VERSION,
        taxonomy_version=_TAXONOMY_VERSION,
        config_version=verify_config.version,
    )
    assert result.weakness_map is not None
    assert result.weakness_map.nodes[skill_node_id].verdict is NodeVerdict.WEAK_CONFIRMED
    return result


async def _run_source_material_smoke(
    area_tag: AreaTag,
    skill_node_id: str,
    comparison_node_id: str,
) -> None:
    diagnosis_result = _actual_diagnosis(
        area_tag,
        skill_node_id,
        comparison_node_id,
    )

    async def diagnose_request(_: ProblemRequest) -> DiagnosisResult:
        return diagnosis_result

    material = SourceMaterialDraft(
        material_text="학생 A가 승인 근거를 활용해 자료를 구성했다.",
        evidence_anchor_ids=("generated_source",),
    )
    generator_provider = FakeProvider(
        (material.model_dump_json(), _source_material_item_json(area_tag, skill_node_id)),
        name=f"fake-{area_tag.value}-generator",
    )
    verifier_provider = FakeProvider(
        (_source_material_solve_json(skill_node_id),),
        name=f"fake-{area_tag.value}-verifier",
    )
    gateway = LlmGateway(
        {
            ModelRole.GENERATOR: generator_provider,
            ModelRole.VERIFIER: verifier_provider,
        },
        transport_retry={
            ModelRole.GENERATOR: 0,
            ModelRole.VERIFIER: 0,
        },
    )
    item_store = InMemoryProblemItemStore()
    workflow = build_problem_workflow(
        gateway=gateway,
        graph_context=AreaDelegatingGraphContextService(),
        diagnosis=diagnose_request,
        candidate_store=InMemoryCandidateStore(),
        item_store=item_store,
        checkpointer=InMemorySaver(),
        verify_config=load_verify_config(),
    )

    result = await workflow.run(
        _source_material_request(area_tag, skill_node_id),
        _execution_context(),
    )

    assert isinstance(result, ProblemSetResult)
    assert result.status is ProblemSetStatus.GENERATED
    assert [request.prompt_id for request in generator_provider.requests] == [
        "pg.source_material.v1",
        "pg.items.v1",
    ]
    assert len(verifier_provider.requests) == 1
    stored = await item_store.list_all()
    assert len(stored) == 1
    assert stored[0].item is not None
    assert stored[0].item.evidence[0].quote == material.material_text


async def _run_smoke() -> None:
    generator_provider = FakeProvider(
        (_generated_item_json(),),
        name="fake-generator",
    )
    verifier_provider = FakeProvider(
        (_solve_result_json(),),
        name="fake-verifier",
    )
    gateway = LlmGateway(
        {
            ModelRole.GENERATOR: generator_provider,
            ModelRole.VERIFIER: verifier_provider,
        },
        transport_retry={
            ModelRole.GENERATOR: 0,
            ModelRole.VERIFIER: 0,
        },
    )
    candidate_store = InMemoryCandidateStore()
    item_store = InMemoryProblemItemStore()
    workflow = build_problem_workflow(
        gateway=gateway,
        graph_context=FakeGraphContextService(),
        diagnosis=_diagnose,
        candidate_store=candidate_store,
        item_store=item_store,
        checkpointer=InMemorySaver(),
        verify_config=load_verify_config(),
    )
    request = _request()
    execution_context = _execution_context()

    first = await workflow.run(request, execution_context)
    assert isinstance(first, ProblemSetResult)
    assert first.items
    assert first.status is ProblemSetStatus.GENERATED
    assert all(isinstance(item.status, ProblemItemStatus) for item in first.items)

    stored_after_first = await item_store.list_all()
    assert len(stored_after_first) == len(first.items)
    first_json = first.model_dump_json()

    second = await workflow.run(request, execution_context)
    assert second == first
    assert second.model_dump_json() == first_json
    assert await item_store.list_all() == stored_after_first
    assert len(generator_provider.requests) == 1
    assert len(verifier_provider.requests) == 1


async def _run_reading_smoke() -> None:
    passage = _passage_draft()
    generator_provider = FakeProvider(
        (passage.model_dump_json(), _reading_item_json()),
        name="fake-generator",
    )
    verifier_provider = FakeProvider(
        (_solve_result_json(),),
        name="fake-verifier",
    )
    gateway = LlmGateway(
        {
            ModelRole.GENERATOR: generator_provider,
            ModelRole.VERIFIER: verifier_provider,
        },
        transport_retry={
            ModelRole.GENERATOR: 0,
            ModelRole.VERIFIER: 0,
        },
    )
    item_store = InMemoryProblemItemStore()
    workflow = build_problem_workflow(
        gateway=gateway,
        graph_context=AreaDelegatingGraphContextService(),
        diagnosis=_diagnose,
        candidate_store=InMemoryCandidateStore(),
        item_store=item_store,
        checkpointer=InMemorySaver(),
        verify_config=load_verify_config(),
    )
    request = _reading_request()
    execution_context = _execution_context()

    first = await workflow.run(request, execution_context)
    assert isinstance(first, ProblemSetResult)
    assert first.status is ProblemSetStatus.GENERATED, [
        (item.status, item.failure_detail) for item in first.items
    ]
    assert [call.prompt_id for call in generator_provider.requests] == [
        "pg.passage.v1",
        "pg.items.v1",
    ]
    item_prompt = generator_provider.requests[1].prompt
    context_json = item_prompt.split("[ContextPack JSON]\n", 1)[1].split(
        "\n\n[생성 입력 JSON]",
        1,
    )[0]
    expected_passage = passage.model_copy(
        update={
            "evidence_anchor_ids": (
                generated_material_ref(
                    kind="passage_span",
                    text=passage.passage_text,
                ),
            )
        }
    )
    assert json.loads(context_json)["retrieval_trace"]["passage_draft"] == (
        expected_passage.model_dump(mode="json")
    )
    stored = await item_store.list_all()
    assert stored[0].item is not None
    assert stored[0].item.evidence[0].quote == passage.passage_text
    assert len(verifier_provider.requests) == 1

    second = await workflow.run(request, execution_context)
    assert second == first
    assert len(generator_provider.requests) == 2
    assert len(verifier_provider.requests) == 1


async def _run_literature_smoke() -> None:
    excerpt = LiteratureSelector(load_literature_pool()).select(_literature_selection())
    generator_provider = FakeProvider(
        (_literature_item_json(),),
        name="fake-literature-generator",
    )
    verifier_provider = FakeProvider(
        (_literature_solve_json(),),
        name="fake-literature-verifier",
    )
    gateway = LlmGateway(
        {
            ModelRole.GENERATOR: generator_provider,
            ModelRole.VERIFIER: verifier_provider,
        },
        transport_retry={
            ModelRole.GENERATOR: 0,
            ModelRole.VERIFIER: 0,
        },
    )
    item_store = InMemoryProblemItemStore()
    workflow = build_problem_workflow(
        gateway=gateway,
        graph_context=AreaDelegatingGraphContextService(),
        diagnosis=_diagnose,
        candidate_store=InMemoryCandidateStore(),
        item_store=item_store,
        checkpointer=InMemorySaver(),
        verify_config=load_verify_config(),
    )

    result = await workflow.run(_literature_request(), _execution_context())

    assert isinstance(result, ProblemSetResult)
    assert result.status is ProblemSetStatus.GENERATED
    assert [call.prompt_id for call in generator_provider.requests] == ["pg.items.v1"]
    stored = await item_store.list_all()
    assert len(stored) == 1
    assert stored[0].item is not None
    assert stored[0].item.evidence[0].ref == excerpt.evidence_ref
    assert stored[0].item.evidence[0].quote == excerpt.quote


def test_problem_workflow_bootstrap_completes_and_is_idempotent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in TRACING_ENV_SYNONYMS:
        monkeypatch.delenv(name, raising=False)

    asyncio.run(_run_smoke())


def test_reading_generates_passage_before_item_and_is_idempotent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in TRACING_ENV_SYNONYMS:
        monkeypatch.delenv(name, raising=False)

    asyncio.run(_run_reading_smoke())


def test_literature_uses_expired_original_and_persists_exact_quote() -> None:
    asyncio.run(_run_literature_smoke())


@pytest.mark.parametrize(
    "source_step",
    [
        "generation_unavailable",
        '{"passage_text":"근거 없는 첫 문단.\\n\\n근거 없는 둘째 문단.",'
        '"paragraph_count":2,"evidence_anchor_ids":[]}',
    ],
)
def test_reading_source_generation_failure_is_rejected_insufficient(
    source_step: str,
) -> None:
    generator_provider = FakeProvider(
        (source_step,),
        name="fake-reading-unavailable",
    )
    gateway = LlmGateway(
        {ModelRole.GENERATOR: generator_provider},
        transport_retry={ModelRole.GENERATOR: 0},
    )
    workflow = build_problem_workflow(
        gateway=gateway,
        graph_context=AreaDelegatingGraphContextService(),
        diagnosis=_diagnose,
        candidate_store=InMemoryCandidateStore(),
        item_store=InMemoryProblemItemStore(),
        checkpointer=InMemorySaver(),
        verify_config=load_verify_config(),
    )

    result = asyncio.run(workflow.run(_reading_request(), _execution_context()))

    assert result.outcome == "rejected_insufficient"
    assert len(generator_provider.requests) == 1


def test_generated_item_with_unapproved_ref_drops_after_retry_budget() -> None:
    passage = _passage_draft()
    valid_item = GeneratedItem.model_validate_json(_reading_item_json())
    invalid_item = valid_item.model_copy(
        update={
            "evidence": (
                EvidenceAnchor(
                    kind=EvidenceKind.PASSAGE_SPAN,
                    ref="generated:passage_span:unapproved",
                    quote=passage.passage_text,
                ),
            )
        }
    )
    generator_provider = FakeProvider(
        (
            passage.model_dump_json(),
            invalid_item.model_dump_json(),
            invalid_item.model_dump_json(),
            invalid_item.model_dump_json(),
        ),
        name="fake-reading-unapproved-item",
    )
    gateway = LlmGateway(
        {ModelRole.GENERATOR: generator_provider},
        transport_retry={ModelRole.GENERATOR: 0},
    )
    workflow = build_problem_workflow(
        gateway=gateway,
        graph_context=AreaDelegatingGraphContextService(),
        diagnosis=_diagnose,
        candidate_store=InMemoryCandidateStore(),
        item_store=InMemoryProblemItemStore(),
        checkpointer=InMemorySaver(),
        verify_config=load_verify_config(),
    )

    result = asyncio.run(workflow.run(_reading_request(), _execution_context()))

    assert isinstance(result, ProblemSetResult)
    assert result.items[0].status is ProblemItemStatus.DROPPED
    assert result.items[0].failure_reason is not None
    assert result.items[0].failure_reason.value == "source_unverified"
    assert len(generator_provider.requests) == 4


def test_second_reference_gate_blocks_generated_draft_without_anchor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    passage = _passage_draft()
    generator_provider = FakeProvider(
        (passage.model_dump_json(), _reading_item_json()),
        name="fake-reading-anchor-flip",
    )
    gateway = LlmGateway(
        {ModelRole.GENERATOR: generator_provider},
        transport_retry={ModelRole.GENERATOR: 0},
    )
    workflow = build_problem_workflow(
        gateway=gateway,
        graph_context=AreaDelegatingGraphContextService(),
        diagnosis=_diagnose,
        candidate_store=InMemoryCandidateStore(),
        item_store=InMemoryProblemItemStore(),
        checkpointer=InMemorySaver(),
        verify_config=load_verify_config(),
    )
    monkeypatch.setattr(
        workflow_module,
        "attach_passage_draft",
        lambda context_pack, _draft: context_pack,
    )

    result = asyncio.run(workflow.run(_reading_request(), _execution_context()))

    assert isinstance(result, ProblemSetResult)
    assert result.status is not ProblemSetStatus.GENERATED
    assert [call.prompt_id for call in generator_provider.requests] == [
        "pg.passage.v1"
    ]


@pytest.mark.parametrize(
    ("area_tag", "skill_node_id", "comparison_node_id"),
    [
        (
            AreaTag.SPEECH_WRITING,
            "speech_writing.writing.material",
            "speech_writing.speech.strategy",
        ),
        (
            AreaTag.MEDIA,
            "media.reception.credibility",
            "media.reception.information",
        ),
    ],
)
def test_non_language_weakness_node_reaches_source_generation_and_gates(
    monkeypatch: pytest.MonkeyPatch,
    area_tag: AreaTag,
    skill_node_id: str,
    comparison_node_id: str,
) -> None:
    for name in TRACING_ENV_SYNONYMS:
        monkeypatch.delenv(name, raising=False)

    asyncio.run(
        _run_source_material_smoke(area_tag, skill_node_id, comparison_node_id)
    )
