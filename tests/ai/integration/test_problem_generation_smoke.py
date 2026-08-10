"""문제출제 조립 진입점의 오프라인 end-to-end 스모크."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from uuid import UUID

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from ai.contracts.diagnosis import (
    CellVerdict,
    DiagnosisResult,
    DiagnosisStatus,
    NodeVerdict,
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
    PassageDomain,
    PassageDraft,
    PassageRequest,
    ProblemItemStatus,
    ProblemRequest,
    ProblemSetResult,
    ProblemSetStatus,
    SentenceComplexity,
    SolveResult,
    TargetKind,
    TargetSource,
)
from ai.contracts.taxonomy import AreaTag, ItemFormat, TypeTag
from ai.llm.gateway import LlmGateway
from ai.problem_generation.bootstrap import build_problem_workflow
from ai.problem_generation.infrastructure.config import load_verify_config
from ai.problem_generation.infrastructure.memory_store import (
    InMemoryCandidateStore,
    InMemoryProblemItemStore,
)
from ai.runtime.tracing import TRACING_ENV_SYNONYMS

_FAKES_DIR = Path(__file__).parents[1] / "fakes"
sys.path.insert(0, str(_FAKES_DIR))

from fake_graph_context import FakeGraphContextService  # noqa: E402
from fake_provider import FakeProvider  # noqa: E402

_GRAPH_VERSION = "curriculum-graph.v1"
_SKILL_NODE_ID = "grammar.sentence-structure"
_SNAPSHOT_HASH = "snapshot-pg-bootstrap-smoke"
_TAXONOMY_VERSION = "taxonomy-v1"


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
            prompt_version="v3",
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
        evidence_anchor_ids=("grammar:rule-1",),
    )


def _reading_item_json() -> str:
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
                ref="grammar:rule-1",
                quote="오늘 수업은 비문학 독해였어요.",
            ),
        ),
    )
    return item.model_dump_json()


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
        graph_context=FakeGraphContextService(),
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
    assert json.loads(context_json)["retrieval_trace"]["passage_draft"] == (
        passage.model_dump(mode="json")
    )
    assert len(verifier_provider.requests) == 1

    second = await workflow.run(request, execution_context)
    assert second == first
    assert len(generator_provider.requests) == 2
    assert len(verifier_provider.requests) == 1


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
