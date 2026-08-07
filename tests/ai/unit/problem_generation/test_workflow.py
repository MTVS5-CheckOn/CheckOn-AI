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
from langgraph.errors import GraphRecursionError
from problem_snapshot import SnapshotDiagnosisFake

from ai.contracts.diagnosis import DiagnosisResult, DiagnosisStatus
from ai.contracts.execution import Capability, ExecutionContext, VersionSet
from ai.contracts.llm import (
    FieldMissing,
    LlmError,
    LlmTimeout,
    LlmUnavailable,
    ModelRole,
    ParseFailed,
    RedactionBlocked,
)
from ai.contracts.problem_generation import (
    Answer,
    Choice,
    DifficultyBand,
    EvidenceAnchor,
    EvidenceKind,
    GeneratedItem,
    ItemResult,
    PassageDomain,
    PassageRequest,
    ProblemFailureReason,
    ProblemGenerationState,
    ProblemItemStatus,
    ProblemRequest,
    ProblemSetResult,
    ProblemSetStatus,
    ReviewReason,
    SentenceComplexity,
    SolveResult,
    TargetKind,
    TargetSource,
)
from ai.contracts.taxonomy import AreaTag, ItemFormat, TypeTag
from ai.evaluation.fake_snapshot import fixture_stable
from ai.llm.determinism import DETERMINISTIC_TEMPERATURE, LLM_SEED
from ai.llm.gateway import LlmGateway
from ai.problem_generation.application import workflow as workflow_module
from ai.problem_generation.application.cross_solver import BlindCrossSolver
from ai.problem_generation.application.generator import ProblemGenerator
from ai.problem_generation.application.workflow import (
    SOURCE_PROCUREMENT_NOT_IMPLEMENTED,
    ProblemExecutionContextMismatch,
    ProblemGenerationWorkflow,
    ProblemSourceUnsupported,
    ProblemTenantMismatch,
    ProblemWorkflowConfigurationError,
    graph_recursion_limit,
)
from ai.problem_generation.domain.identity import problem_item_id
from ai.problem_generation.domain.models import TargetPlan
from ai.problem_generation.domain.policy import (
    DifficultyRange,
    T1DifficultyBandMap,
    VerifyConfig,
)
from ai.problem_generation.infrastructure.config import load_verify_config
from ai.problem_generation.infrastructure.graph_context import (
    GrammarNormGraphContextService,
)
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
                prompt_version="v2",
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


type _LlmErrorRoute = tuple[int, str]

_GENERATION_LLM_ERROR_ROUTES: dict[type[LlmError], _LlmErrorRoute] = {
    LlmError: (3, "생성 시도 소진"),
    LlmUnavailable: (3, "생성 시도 소진"),
    LlmTimeout: (3, "생성 시도 소진"),
    RedactionBlocked: (1, "redaction 불확실"),
    ParseFailed: (3, "생성 시도 소진"),
    FieldMissing: (3, "생성 시도 소진"),
}

_CROSS_SOLVE_LLM_ERROR_ROUTES: dict[type[LlmError], _LlmErrorRoute] = {
    LlmError: (1, "교차 풀이 서비스 불가"),
    LlmUnavailable: (1, "교차 풀이 서비스 불가"),
    LlmTimeout: (1, "교차 풀이 서비스 불가"),
    RedactionBlocked: (1, "교차 풀이 redaction 불확실"),
    ParseFailed: (3, "교차 풀이 파싱 시도 소진"),
    FieldMissing: (3, "교차 풀이 파싱 시도 소진"),
}


def _all_llm_error_types() -> set[type[LlmError]]:
    discovered: set[type[LlmError]] = {LlmError}
    pending = list(LlmError.__subclasses__())
    while pending:
        error_type = pending.pop()
        if error_type in discovered:
            continue
        discovered.add(error_type)
        pending.extend(error_type.__subclasses__())
    return {
        error_type
        for error_type in discovered
        if error_type.__module__ == LlmError.__module__
    }


def _route_params(
    routes: dict[type[LlmError], _LlmErrorRoute],
) -> list[object]:
    return [
        pytest.param(error_type, attempts, detail, id=error_type.__name__)
        for error_type, (attempts, detail) in sorted(
            routes.items(), key=lambda entry: entry[0].__name__
        )
    ]


def test_llm_error_route_tables_cover_recursive_hierarchy() -> None:
    discovered = _all_llm_error_types()
    for table_name, routes in (
        ("_GENERATION_LLM_ERROR_ROUTES", _GENERATION_LLM_ERROR_ROUTES),
        ("_CROSS_SOLVE_LLM_ERROR_ROUTES", _CROSS_SOLVE_LLM_ERROR_ROUTES),
    ):
        missing = discovered - routes.keys()
        unexpected = routes.keys() - discovered
        assert not missing and not unexpected, (
            f"contracts.llm의 LlmError 전칭 표 {table_name}가 계층과 다르다. "
            f"표에 없는 예외={sorted(cls.__name__ for cls in missing)}, "
            f"계층에 없는 표 항목={sorted(cls.__name__ for cls in unexpected)}. "
            f"tests/ai/unit/problem_generation/test_workflow.py의 {table_name}에 "
            "새 예외의 처리 갈래를 결정해 추가하라."
        )


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
    generation_params = harness.generator_provider.requests[0].generation_params
    assert generation_params is not None
    assert generation_params.temperature == DETERMINISTIC_TEMPERATURE
    assert generation_params.seed == LLM_SEED
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


@pytest.mark.parametrize(
    ("error_type", "expected_attempts", "expected_detail"),
    _route_params(_GENERATION_LLM_ERROR_ROUTES),
)
def test_all_llm_error_types_during_generation_follow_declared_route(
    error_type: type[LlmError],
    expected_attempts: int,
    expected_detail: str,
) -> None:
    harness = _WorkflowHarness(
        generator_steps=tuple(
            error_type("provider failure") for _ in range(expected_attempts)
        ),
        verifier_steps=(),
    )

    result = _run(harness, harness.request())

    assert result.status is ProblemSetStatus.FAILED
    assert result.items[0].status is ProblemItemStatus.DROPPED
    assert result.items[0].failure_reason is ProblemFailureReason.GENERATION_EXHAUSTED
    assert result.items[0].attempt_no == expected_attempts
    assert result.items[0].failure_detail is not None
    assert expected_detail in result.items[0].failure_detail
    assert len(harness.generator_provider.requests) == expected_attempts
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

    result = asyncio.run(harness.workflow.run(harness.request(count=5), harness.context()))

    assert result.outcome == "rejected_insufficient"
    assert result.status == "rejected_insufficient"
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


@pytest.mark.parametrize(
    ("error_type", "expected_attempts", "expected_detail"),
    _route_params(_CROSS_SOLVE_LLM_ERROR_ROUTES),
)
def test_all_llm_error_types_during_cross_solve_follow_declared_route(
    error_type: type[LlmError],
    expected_attempts: int,
    expected_detail: str,
) -> None:
    harness = _WorkflowHarness(
        generator_steps=tuple(
            _item_json(f"교차풀이-{attempt}") for attempt in range(expected_attempts)
        ),
        verifier_steps=tuple(
            error_type("cross solve failure") for _ in range(expected_attempts)
        ),
    )

    result = _run(harness, harness.request())

    assert result.items[0].status is ProblemItemStatus.VERIFICATION_UNAVAILABLE
    assert result.items[0].attempt_no == expected_attempts
    assert result.items[0].failure_detail is not None
    assert expected_detail in result.items[0].failure_detail
    assert len(harness.generator_provider.requests) == expected_attempts
    assert len(harness.verifier_provider.requests) == expected_attempts


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


@pytest.mark.parametrize(
    "request_update",
    (
        {"area_tag": AreaTag.READING},
        {
            "area_tag": AreaTag.READING,
            "passage": PassageRequest(
                domain=PassageDomain.SCIENCE,
                word_count=500,
                sentence_complexity=SentenceComplexity.STANDARD,
                paragraph_count=3,
                banned_topics_version="pg-banned-topics.v1",
            ),
        },
    ),
)
def test_workflow_rejects_requests_needing_unimplemented_material_source(
    request_update: dict[str, object],
) -> None:
    """자료 조달 방식이 '자료 없음'인 요청만 받는다 — `05` §1.0·§1.2.

    트랙 제한이 아니다. 게이트·프롬프트는 전 영역 공용이고, 막는 것은
    "생성"·"저작물" 조달 노드가 아직 없다는 사실 하나다. 자료를 동반한 요청은
    LLM을 부르기 전에 막혀야 한다.
    """
    harness = _WorkflowHarness(
        generator_steps=(),
        verifier_steps=(),
    )
    unsupported = harness.request().model_copy(update=request_update)

    with pytest.raises(ProblemSourceUnsupported) as raised:
        asyncio.run(harness.workflow.run(unsupported, harness.context()))

    error = raised.value
    assert type(error) is ProblemSourceUnsupported
    assert error.code == "INVALID_SCHEMA"
    assert error.http_status == 400
    assert error.detail == {
        "reason": SOURCE_PROCUREMENT_NOT_IMPLEMENTED,
        "area_tag": AreaTag.READING.value,
        "passage": unsupported.passage is not None,
    }
    assert not harness.generator_provider.requests


def test_unmapped_reference_node_converges_to_rejected_insufficient() -> None:
    harness = _WorkflowHarness(generator_steps=(), verifier_steps=())
    workflow = ProblemGenerationWorkflow(
        diagnosis=harness.diagnosis,
        graph_context=GrammarNormGraphContextService(),
        generator=harness.generator,
        cross_solver=harness.cross_solver,
        candidate_store=harness.candidates,
        item_store=harness.items,
        checkpointer=InMemorySaver(),
    )

    result = asyncio.run(workflow.run(harness.request(), harness.context()))

    assert result.outcome == "rejected_insufficient"
    assert result.status == "rejected_insufficient"
    assert not harness.generator_provider.requests


def test_workflow_rejects_tenant_mismatch_with_typed_forbidden_error() -> None:
    harness = _WorkflowHarness(generator_steps=(), verifier_steps=())
    mismatched = harness.context().model_copy(update={"tenant_id": "tenant-b"})

    with pytest.raises(ProblemTenantMismatch) as raised:
        asyncio.run(harness.workflow.run(harness.request(), mismatched))

    error = raised.value
    assert type(error) is ProblemTenantMismatch
    assert error.code == "TENANT_MISMATCH"
    assert error.http_status == 403


def test_workflow_rejects_input_mismatch_with_configuration_error() -> None:
    harness = _WorkflowHarness(generator_steps=(), verifier_steps=())
    mismatched = harness.context().model_copy(
        update={"input_snapshot_hash": "sha256:different"}
    )

    with pytest.raises(ProblemWorkflowConfigurationError) as raised:
        asyncio.run(harness.workflow.run(harness.request(), mismatched))

    error = raised.value
    assert type(error) is ProblemWorkflowConfigurationError
    assert error.code == "INVALID_SCHEMA"
    assert error.http_status == 400


def test_workflow_rejects_capability_mismatch_as_internal_error() -> None:
    harness = _WorkflowHarness(generator_steps=(), verifier_steps=())
    mismatched = harness.context().model_copy(
        update={"capability": Capability.COMPOSITION}
    )

    with pytest.raises(ProblemExecutionContextMismatch) as raised:
        asyncio.run(harness.workflow.run(harness.request(), mismatched))

    error = raised.value
    assert type(error) is ProblemExecutionContextMismatch
    assert error.code == "INTERNAL"
    assert error.http_status == 500


def test_graph_recursion_limit_is_derived_not_borrowed_from_the_library() -> None:
    """🔴 그래프 super-step 상한을 **계약 상한에서 유도한다** (불변식 6 · 99 #08 ⓑ).

    실측(langgraph 1.2.9 · `_internal/_config.py:32`): 기본값이 **10007**이고
    `LANGGRAPH_DEFAULT_RECURSION_LIMIT` **환경변수로 덮인다** — 상한이 저장소 밖에 있다.
    ⚠ 종전 langgraph는 이 값이 **25**였다. 핀이 되돌아가면 계약 최대(`count=20`)가
    라이브러리 기본값을 넘어 **정상 요청이 `GraphRecursionError`로 죽는다.**
    그래서 「지금 안 죽는다」가 아니라 「유도값을 명시했다」가 이 테스트의 주장이다.
    """
    config = load_verify_config()

    one = graph_recursion_limit(count=1, config=config)
    two = graph_recursion_limit(count=2, config=config)
    per_slot = two - one

    # 한 슬롯은 최악의 경우 (최초 + 재생성) + 난이도 재생성만큼 돈다.
    assert per_slot == (config.item_attempt_limit + config.difficulty_regen_max) * 2
    assert one > per_slot, "여유분이 없으면 START·종단 판정에서 잘린다"

    # 🔴 계약 최대(`count` ≤ 20)가 종전 라이브러리 기본값(25)을 이미 넘는다.
    largest = graph_recursion_limit(count=20, config=config)
    assert largest > 25, (
        "계약 최대 요청이 종전 langgraph 기본값 25를 넘는다 — 유도값을 안 실으면 "
        "라이브러리 핀 하나로 정상 요청이 GraphRecursionError로 죽는다"
    )
    # 선형이라 요청 크기와 무관하게 「한 슬롯 최악」만 알면 된다.
    assert largest == one + 19 * per_slot


def test_the_recursion_limit_actually_reaches_the_graph() -> None:
    """🔴 **유도값이 실제로 그래프에 전달되는지**를 뒤집어서 본다.

    상한을 계산만 하고 `ainvoke` config에 안 실으면 이 값은 **선언만 있고 소비가 0**이다
    (99 ㊺ 부류). 유도 함수를 최소값으로 바꿔치면 실행이 `GraphRecursionError`로 끊겨야
    한다 — 안 끊기면 config 키가 그래프에 닿지 않는 것이다.
    """
    harness = _WorkflowHarness(
        generator_steps=(_item_json("한계"),),
        verifier_steps=(_solve_json(),),
    )
    request = harness.request(count=1, target_source=TargetSource.TEACHER_MANUAL)

    with (
        pytest.MonkeyPatch.context() as patch,
        pytest.raises(GraphRecursionError),
    ):
        patch.setattr(workflow_module, "graph_recursion_limit", lambda **_: 1)
        asyncio.run(harness.workflow.run(request, harness.context()))
