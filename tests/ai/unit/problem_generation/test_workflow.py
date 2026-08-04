"""M2 문제출제 오프라인 수직 슬라이스와 실패 예산."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from uuid import UUID

import pytest
from fake_graph_context import (
    FakeGraphContextService,
    FakeGraphStep,
)
from fake_provider import FakeProvider, FakeStep
from langgraph.checkpoint.memory import InMemorySaver
from problem_snapshot import SnapshotDiagnosisFake

from ai.contracts.diagnosis import DiagnosisResult, DiagnosisStatus
from ai.contracts.execution import Capability, ExecutionContext, VersionSet
from ai.contracts.llm import LlmUnavailable, ModelRole
from ai.contracts.problem_generation import (
    Answer,
    Choice,
    DifficultyBand,
    EvidenceAnchor,
    EvidenceKind,
    GeneratedItem,
    ItemResult,
    ProblemFailureReason,
    ProblemGenerationState,
    ProblemItemStatus,
    ProblemRequest,
    ProblemSetResult,
    ProblemSetStatus,
    ReviewReason,
    SetStopReason,
    SolveResult,
    TargetKind,
    TargetSource,
)
from ai.contracts.taxonomy import AreaTag, ItemFormat, TypeTag
from ai.evaluation.fake_snapshot import fixture_stable
from ai.llm.gateway import LlmGateway
from ai.problem_generation.application.cross_solver import BlindCrossSolver
from ai.problem_generation.application.generator import ProblemGenerator
from ai.problem_generation.application.workflow import (
    ProblemGenerationWorkflow,
    ProblemWorkflowConfigurationError,
)
from ai.problem_generation.domain.identity import problem_item_id
from ai.problem_generation.domain.models import TargetPlan
from ai.problem_generation.domain.policy import (
    DifficultyRange,
    T1DifficultyBandMap,
    VerifyConfig,
)
from ai.problem_generation.infrastructure.config import load_verify_config
from ai.problem_generation.infrastructure.memory_store import (
    InMemoryCandidateStore,
    InMemoryProblemItemStore,
)

_GRAPH_VERSION = "curriculum-graph.v1"
_TAXONOMY_VERSION = "taxonomy-v1"
_SKILL_NODE_ID = "grammar.sentence-structure"


class _WorkflowHarness:
    def __init__(
        self,
        *,
        generator_steps: Sequence[FakeStep],
        verifier_steps: Sequence[FakeStep],
        graph_steps: Sequence[FakeGraphStep] = (("grammar:rule-1",),),
        difficulty_regen_enabled: bool = False,
        difficulty_band_tolerance: int | None = None,
        diagnosis: SnapshotDiagnosisFake | None = None,
        verify_config: VerifyConfig | None = None,
    ) -> None:
        self.snapshot = fixture_stable()
        self.diagnosis = diagnosis or SnapshotDiagnosisFake(
            snapshot=self.snapshot,
            skill_node_id=_SKILL_NODE_ID,
            graph_version=_GRAPH_VERSION,
            taxonomy_version=_TAXONOMY_VERSION,
            config_version="verify-config.v1",
        )
        self.generator_provider = FakeProvider(
            generator_steps,
            name="fake-generator",
        )
        self.verifier_provider = FakeProvider(
            verifier_steps,
            name="fake-verifier",
        )
        gateway = LlmGateway(
            {
                ModelRole.GENERATOR: self.generator_provider,
                ModelRole.VERIFIER: self.verifier_provider,
            },
            transport_retry={
                ModelRole.GENERATOR: 0,
                ModelRole.VERIFIER: 0,
            },
        )
        self.graph = FakeGraphContextService(graph_steps)
        self.candidates = InMemoryCandidateStore()
        self.items = InMemoryProblemItemStore()
        config = (verify_config or load_verify_config()).model_copy(
            update={
                "difficulty_regen_enabled": difficulty_regen_enabled,
                **(
                    {"difficulty_band_tolerance": difficulty_band_tolerance}
                    if difficulty_band_tolerance is not None
                    else {}
                ),
            }
        )
        self.generator = ProblemGenerator(gateway)
        self.cross_solver = BlindCrossSolver(gateway)
        self.workflow = ProblemGenerationWorkflow(
            diagnosis=self.diagnosis,
            graph_context=self.graph,
            generator=self.generator,
            cross_solver=self.cross_solver,
            candidate_store=self.candidates,
            item_store=self.items,
            checkpointer=InMemorySaver(),
            verify_config=config,
        )

    def request(
        self,
        *,
        count: int = 1,
        target_source: TargetSource = TargetSource.WEAKNESS_AUTO,
        requested_difficulty: DifficultyBand | None = None,
    ) -> ProblemRequest:
        common: dict[str, object] = {
            "request_id": "req-workflow",
            "idempotency_key": "idem-workflow",
            "tenant_id": "tenant-a",
            "target_kind": TargetKind.STUDENT,
            "target_ref": "st_stable_1",
            "target_source": target_source,
            "snapshot_hash": self.snapshot.snapshot_meta.snapshot_hash,
            "taxonomy_version": _TAXONOMY_VERSION,
            "area_tag": AreaTag.LANGUAGE,
            "type_tags": (TypeTag.INFER,),
            "item_format": ItemFormat.MCQ,
            "count": count,
            "requested_difficulty": requested_difficulty,
        }
        if target_source is TargetSource.TEACHER_MANUAL:
            common["manual_targets"] = (_SKILL_NODE_ID,)
        return ProblemRequest.model_validate(common)

    def context(self) -> ExecutionContext:
        return ExecutionContext(
            execution_id=UUID("11111111-1111-1111-1111-111111111111"),
            tenant_id="tenant-a",
            capability=Capability.PROBLEM_GENERATION,
            input_snapshot_hash=self.snapshot.snapshot_meta.snapshot_hash,
            versions=VersionSet(
                pipeline_version="pipeline-v1",
                engine_version="engine-v1",
                schema_version="schema-v1",
                contract_version="contract-v1",
                prompt_version="v1",
                graph_version=_GRAPH_VERSION,
                taxonomy_version=_TAXONOMY_VERSION,
                verify_config_version="verify-config.v1",
            ),
        )


def _item_json(
    marker: str,
    *,
    duplicate_choices: bool = False,
    evidence_refs: tuple[str, ...] = ("grammar:rule-1",),
) -> str:
    item = GeneratedItem(
        area_tag=AreaTag.LANGUAGE,
        type_tag=TypeTag.INFER,
        item_format=ItemFormat.MCQ,
        skill_node_id=_SKILL_NODE_ID,
        stem=f"다음 중 옳은 것을 고르시오. 문항 {marker}",
        choices=tuple(
            Choice(
                no=no,
                text=(
                    "중복 선지"
                    if duplicate_choices
                    else f"{marker}의 고유 선지 {no}"
                ),
                why_wrong=None if no == 1 else f"{no}번은 근거와 다르다.",
            )
            for no in range(1, 6)
        ),
        answer=Answer(correct_no=1),
        rationale="승인된 근거에 따른 해설이다.",
        evidence=tuple(
            EvidenceAnchor(kind=EvidenceKind.GRAMMAR_RULE, ref=ref)
            for ref in evidence_refs
        ),
    )
    return item.model_dump_json()


def _solve_json(
    *,
    chosen: int = 1,
    confidence: float = 0.95,
    aligned: bool = True,
) -> str:
    return SolveResult(
        chosen=chosen,
        reasoning="정답을 독립적으로 확인했다.",
        confidence=confidence,
        target_skill_node_id=_SKILL_NODE_ID,
        measured_skill_node_id=_SKILL_NODE_ID,
        aligned=aligned,
        alignment_confidence=0.95,
        alignment_reason="목표 노드와 일치한다.",
    ).model_dump_json()


def _run(
    harness: _WorkflowHarness,
    request: ProblemRequest,
) -> ProblemSetResult:
    result = asyncio.run(harness.workflow.run(request, harness.context()))
    assert isinstance(result, ProblemSetResult)
    return result


def test_fake_snapshot_to_generation_store_result_vertical_slice() -> None:
    harness = _WorkflowHarness(
        generator_steps=(_item_json("첫"),),
        verifier_steps=(_solve_json(),),
    )

    result = _run(harness, harness.request())

    assert result.status is ProblemSetStatus.GENERATED
    assert result.items[0].status is ProblemItemStatus.VERIFIED
    assert len(harness.diagnosis.requests) == 1
    assert len(harness.graph.requests) == 1
    assert len(asyncio.run(harness.candidates.list_all())) == 1
    stored = asyncio.run(harness.items.list_all())
    assert len(stored) == 1
    assert stored[0].item is not None


def test_same_idempotency_request_reuses_checkpoint_and_saved_result() -> None:
    harness = _WorkflowHarness(
        generator_steps=(_item_json("멱등"),),
        verifier_steps=(_solve_json(),),
    )
    request = harness.request()

    first = _run(harness, request)
    second = _run(harness, request)

    assert second == first
    assert len(harness.generator_provider.requests) == 1
    assert len(harness.verifier_provider.requests) == 1


def test_manual_target_skips_diagnosis_and_marks_first_success_review() -> None:
    harness = _WorkflowHarness(
        generator_steps=(_item_json("수동"),),
        verifier_steps=(_solve_json(),),
    )

    result = _run(
        harness,
        harness.request(target_source=TargetSource.TEACHER_MANUAL),
    )

    assert not harness.diagnosis.requests
    assert result.items[0].status is ProblemItemStatus.NEEDS_REVIEW
    assert result.items[0].review_reason is ReviewReason.MANUAL_TARGET_FIRST


def test_generation_failures_stop_at_three_attempts_without_fourth_call() -> None:
    harness = _WorkflowHarness(
        generator_steps=("not-json", "not-json", "not-json"),
        verifier_steps=(),
    )

    result = _run(harness, harness.request())

    assert result.status is ProblemSetStatus.FAILED
    assert result.items[0].status is ProblemItemStatus.DROPPED
    assert result.items[0].attempt_no == 3
    assert len(harness.generator_provider.requests) == 3
    assert not harness.verifier_provider.requests


def test_same_set_duplicate_stem_is_rejected_within_shared_attempt_budget() -> None:
    duplicate = _item_json("동일문두")
    harness = _WorkflowHarness(
        generator_steps=(
            duplicate,
            duplicate,
            duplicate,
            duplicate,
        ),
        verifier_steps=(_solve_json(),),
    )

    result = _run(harness, harness.request(count=2))

    assert result.status is ProblemSetStatus.PARTIAL_SUCCESS
    assert result.items[0].status is ProblemItemStatus.VERIFIED
    assert result.items[1].status is ProblemItemStatus.DROPPED
    assert result.items[1].attempt_no == 3
    assert len(harness.generator_provider.requests) == 4
    assert len(harness.verifier_provider.requests) == 1


def test_difficulty_regeneration_uses_shared_budget_and_selects_fit() -> None:
    harness = _WorkflowHarness(
        generator_steps=(
            _item_json("낮음"),
            _item_json(
                "중간",
                evidence_refs=("grammar:rule-1", "grammar:rule-2"),
            ),
        ),
        verifier_steps=(_solve_json(), _solve_json()),
        graph_steps=(("grammar:rule-1", "grammar:rule-2"),),
        difficulty_regen_enabled=True,
        difficulty_band_tolerance=0,
    )

    result = _run(
        harness,
        harness.request(requested_difficulty=DifficultyBand.MEDIUM),
    )

    assert result.items[0].status is ProblemItemStatus.VERIFIED
    assert result.items[0].attempt_no == 2
    assert result.items[0].difficulty_band is DifficultyBand.MEDIUM
    assert len(harness.generator_provider.requests) == 2
    assert len(asyncio.run(harness.candidates.list_all())) == 2


def test_failed_difficulty_regeneration_restores_immutable_first_candidate() -> None:
    harness = _WorkflowHarness(
        generator_steps=(
            _item_json("보존"),
            _item_json("실패", duplicate_choices=True),
        ),
        verifier_steps=(_solve_json(),),
        difficulty_regen_enabled=True,
    )

    result = _run(
        harness,
        harness.request(requested_difficulty=DifficultyBand.HIGH),
    )

    restored = result.items[0]
    assert restored.status is ProblemItemStatus.NEEDS_REVIEW
    assert restored.review_reason is ReviewReason.DIFFICULTY_BAND_MISMATCH
    assert restored.attempt_no == 1
    assert len(harness.generator_provider.requests) == 2
    assert len(harness.verifier_provider.requests) == 1
    candidates = asyncio.run(harness.candidates.list_all())
    assert len(candidates) == 1
    stored = asyncio.run(harness.items.list_all())[0]
    assert stored.item is not None
    assert "문항 보존" in stored.item.stem


def test_two_outside_band_candidates_choose_closer_to_requested_midpoint() -> None:
    harness = _WorkflowHarness(
        generator_steps=(
            _item_json("낮음"),
            _item_json(
                "더가까움",
                evidence_refs=("grammar:rule-1", "grammar:rule-2"),
            ),
        ),
        verifier_steps=(_solve_json(), _solve_json()),
        graph_steps=(("grammar:rule-1", "grammar:rule-2"),),
        difficulty_regen_enabled=True,
        difficulty_band_tolerance=0,
    )

    result = _run(
        harness,
        harness.request(requested_difficulty=DifficultyBand.HIGH),
    )

    final = result.items[0]
    assert final.status is ProblemItemStatus.NEEDS_REVIEW
    assert final.review_reason is ReviewReason.DIFFICULTY_BAND_MISMATCH
    assert final.attempt_no == 2
    stored = asyncio.run(harness.items.list_all())[0]
    assert stored.item is not None
    assert "문항 더가까움" in stored.item.stem


def test_equal_midpoint_distance_keeps_lower_attempt_number() -> None:
    tie_config = load_verify_config().model_copy(
        update={
            "difficulty_band_map": {
                "T1": T1DifficultyBandMap(
                    low=DifficultyRange(min=1.0, max=1.5),
                    medium=DifficultyRange(min=1.7, max=1.8),
                    high=DifficultyRange(min=2.0, max=2.5),
                )
            }
        }
    )
    harness = _WorkflowHarness(
        generator_steps=(
            _item_json("동점첫번째"),
            _item_json(
                "동점두번째",
                evidence_refs=("grammar:rule-1", "grammar:rule-2"),
            ),
        ),
        verifier_steps=(_solve_json(), _solve_json()),
        graph_steps=(("grammar:rule-1", "grammar:rule-2"),),
        difficulty_regen_enabled=True,
        difficulty_band_tolerance=0,
        verify_config=tie_config,
    )

    result = _run(
        harness,
        harness.request(requested_difficulty=DifficultyBand.MEDIUM),
    )

    assert result.items[0].attempt_no == 1
    stored = asyncio.run(harness.items.list_all())[0]
    assert stored.item is not None
    assert "문항 동점첫번째" in stored.item.stem


def test_verifier_outage_during_difficulty_regeneration_restores_fallback() -> None:
    harness = _WorkflowHarness(
        generator_steps=(_item_json("원본"), _item_json("재생성")),
        verifier_steps=(_solve_json(), LlmUnavailable("verifier down")),
        difficulty_regen_enabled=True,
    )

    result = _run(
        harness,
        harness.request(requested_difficulty=DifficultyBand.HIGH),
    )

    final = result.items[0]
    assert final.status is ProblemItemStatus.NEEDS_REVIEW
    assert final.review_reason is ReviewReason.DIFFICULTY_BAND_MISMATCH
    assert final.attempt_no == 1
    assert len(harness.generator_provider.requests) == 2
    assert len(harness.verifier_provider.requests) == 2


def test_no_budget_for_difficulty_regeneration_never_creates_fourth_call() -> None:
    harness = _WorkflowHarness(
        generator_steps=("not-json", "not-json", _item_json("세번째")),
        verifier_steps=(_solve_json(),),
        difficulty_regen_enabled=True,
    )

    result = _run(
        harness,
        harness.request(requested_difficulty=DifficultyBand.HIGH),
    )

    final = result.items[0]
    assert final.status is ProblemItemStatus.NEEDS_REVIEW
    assert final.review_reason is ReviewReason.DIFFICULTY_BAND_MISMATCH
    assert final.attempt_no == 3
    assert len(harness.generator_provider.requests) == 3


def test_missing_reference_data_is_fail_closed_and_stops_after_streak() -> None:
    harness = _WorkflowHarness(
        generator_steps=(),
        verifier_steps=(),
        graph_steps=((),),
    )

    result = _run(harness, harness.request(count=5))

    assert result.status is ProblemSetStatus.FAILED
    assert result.stop_reason is SetStopReason.VERIFIER_OUTAGE
    assert result.processed_count == 3
    assert result.unstarted_count == 2
    assert all(
        item.status is ProblemItemStatus.VERIFICATION_UNAVAILABLE
        for item in result.items
    )
    assert not harness.generator_provider.requests
    assert not harness.verifier_provider.requests


def test_verifier_outage_is_domain_result_not_execution_exception() -> None:
    harness = _WorkflowHarness(
        generator_steps=(_item_json("검증불능"),),
        verifier_steps=(LlmUnavailable("verifier down"),),
    )

    result = _run(harness, harness.request())

    assert result.status is ProblemSetStatus.FAILED
    assert result.items[0].status is ProblemItemStatus.VERIFICATION_UNAVAILABLE
    stored = asyncio.run(harness.items.list_all())[0]
    assert stored.item is not None


def test_attempt_is_checkpointed_before_external_generation_call() -> None:
    harness = _WorkflowHarness(
        generator_steps=(_item_json("중단"),),
        verifier_steps=(_solve_json(),),
    )
    request = harness.request(target_source=TargetSource.TEACHER_MANUAL)
    context = harness.context()
    set_id = UUID("22222222-2222-2222-2222-222222222222")
    initial = ProblemGenerationState(
        request_ref=f"problem-request:{request.request_id}",
        request_hash="sha256:" + "a" * 64,
        set_id=set_id,
        target_source=request.target_source,
        requested_count=1,
    )
    graph = harness.workflow.build_graph(
        request=request,
        execution_context=context,
        targets=(TargetPlan(skill_node_id=_SKILL_NODE_ID),),
        interrupt_before=("run_attempt",),
    )

    state = asyncio.run(
        graph.ainvoke(
            initial,
            config={"configurable": {"thread_id": str(set_id)}},
        )
    )

    checkpoint = ProblemGenerationState.model_validate(state)
    assert checkpoint.item_attempt == 1
    assert not harness.generator_provider.requests

    resumed_state = asyncio.run(
        graph.ainvoke(
            None,
            config={"configurable": {"thread_id": str(set_id)}},
        )
    )
    resumed = ProblemGenerationState.model_validate(resumed_state)
    assert resumed.is_terminal
    assert len(harness.generator_provider.requests) == 1


def test_saved_slot_result_is_reconnected_without_repeating_llm_call() -> None:
    harness = _WorkflowHarness(
        generator_steps=(),
        verifier_steps=(),
    )
    request = harness.request(target_source=TargetSource.TEACHER_MANUAL)
    context = harness.context()
    set_id = UUID("33333333-3333-3333-3333-333333333333")
    saved_result = ItemResult(
        item_id=problem_item_id(set_id, 0),
        status=ProblemItemStatus.VERIFICATION_UNAVAILABLE,
        attempt_no=3,
        failure_reason=ProblemFailureReason.SOURCE_UNVERIFIED,
        failure_detail="결과 저장 뒤 체크포인트 전 장애",
    )
    asyncio.run(
        harness.items.save(
            set_id=set_id,
            slot_index=0,
            result=saved_result,
            candidate_ref=None,
            item=None,
        )
    )
    initial = ProblemGenerationState(
        request_ref=f"problem-request:{request.request_id}",
        request_hash="sha256:" + "b" * 64,
        set_id=set_id,
        target_source=request.target_source,
        requested_count=1,
    )
    graph = harness.workflow.build_graph(
        request=request,
        execution_context=context,
        targets=(TargetPlan(skill_node_id=_SKILL_NODE_ID),),
    )

    state = asyncio.run(
        graph.ainvoke(
            initial,
            config={"configurable": {"thread_id": str(set_id)}},
        )
    )

    final = ProblemGenerationState.model_validate(state)
    assert final.items == (saved_result,)
    assert not harness.graph.requests
    assert not harness.generator_provider.requests
    assert not harness.verifier_provider.requests


def test_rejected_insufficient_remains_normal_domain_outcome() -> None:
    async def insufficient(_: ProblemRequest) -> DiagnosisResult:
        return DiagnosisResult(
            status=DiagnosisStatus.REJECTED_INSUFFICIENT,
            status_reason="판정 가능한 셀이 없다",
        )

    harness = _WorkflowHarness(
        generator_steps=(),
        verifier_steps=(),
    )
    harness.workflow = ProblemGenerationWorkflow(
        diagnosis=insufficient,
        graph_context=harness.graph,
        generator=harness.generator,
        cross_solver=harness.cross_solver,
        candidate_store=harness.candidates,
        item_store=harness.items,
        checkpointer=InMemorySaver(),
    )

    result = asyncio.run(harness.workflow.run(harness.request(), harness.context()))

    assert result.outcome == "rejected_insufficient"
    assert result.status == "rejected_insufficient"
    assert not harness.generator_provider.requests


def test_workflow_rejects_requests_needing_unimplemented_material_source() -> None:
    """자료 조달 방식이 '자료 없음'인 요청만 받는다 — `05` §1.0·§1.2.

    트랙 제한이 아니다. 게이트·프롬프트는 전 영역 공용이고, 막는 것은
    "생성"·"저작물" 조달 노드가 아직 없다는 사실 하나다. 자료를 동반한 요청은
    LLM을 부르기 전에 막혀야 한다.
    """
    harness = _WorkflowHarness(
        generator_steps=(),
        verifier_steps=(),
    )
    unsupported = harness.request().model_copy(
        update={"area_tag": AreaTag.READING}
    )

    with pytest.raises(
        ProblemWorkflowConfigurationError,
        match="자료 조달 방식이",
    ):
        asyncio.run(harness.workflow.run(unsupported, harness.context()))

    assert not harness.generator_provider.requests
