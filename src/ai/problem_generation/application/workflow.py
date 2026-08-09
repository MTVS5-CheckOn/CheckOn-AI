"""M2 문제출제의 비동기 StateGraph 수직 슬라이스."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Hashable
from dataclasses import dataclass
from typing import Any
from uuid import uuid5

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph

from ai.contracts.diagnosis import (
    DiagnosisResult,
    DiagnosisStatus,
    NodeVerdict,
)
from ai.contracts.execution import Capability, ExecutionContext
from ai.contracts.graphrag import (
    ContextLockedFields,
    ContextPack,
    GraphContextOperation,
    GraphContextRequest,
    GraphContextService,
)
from ai.contracts.llm import (
    LlmError,
    ParseFailed,
    RedactionBlocked,
)
from ai.contracts.problem_generation import (
    PROBLEM_GENERATION_ITEM_ATTEMPT_LIMIT,
    DifficultyBand,
    GeneratedItem,
    ItemResult,
    ProblemFailureReason,
    ProblemGenerationOutcome,
    ProblemGenerationState,
    ProblemItemStatus,
    ProblemRequest,
    RejectedInsufficientOutcome,
    ReviewReason,
    SetStopReason,
    TargetSource,
    assert_problem_generation_state_transition,
)
from ai.contracts.taxonomy import TypeTag
from ai.problem_generation.application.cross_solver import BlindCrossSolver
from ai.problem_generation.application.generator import (
    ProblemGenerator,
    build_candidate_snapshot,
)
from ai.problem_generation.application.ports import CandidateStore, ProblemItemStore
from ai.problem_generation.domain.cross_solve import validate_cross_solve
from ai.problem_generation.domain.difficulty import (
    classify_t1_difficulty,
    distance_to_requested_midpoint,
    estimate_t1_difficulty,
    needs_difficulty_regeneration,
)
from ai.problem_generation.domain.identity import (
    item_stem_hash,
    problem_item_id,
    request_hash,
)
from ai.problem_generation.domain.models import (
    CandidateSnapshot,
    RetryContext,
    TargetPlan,
)
from ai.problem_generation.domain.policy import (
    SUPPORTED_AREAS,
    BannedTopicsConfig,
    VerifyConfig,
)
from ai.problem_generation.domain.rules import (
    RuleValidationResult,
    RuleValidator,
    has_reference_data,
)
from ai.problem_generation.infrastructure.config import (
    load_banned_topics,
    load_verify_config,
)
from ai.runtime.errors import DomainException

type DiagnosisCallable = Callable[[ProblemRequest], Awaitable[DiagnosisResult]]

SOURCE_PROCUREMENT_NOT_IMPLEMENTED = "source_procurement_not_implemented"

_BEGIN_ATTEMPT = "begin_attempt"
_BEGIN_DIFFICULTY_REGEN = "begin_difficulty_regen"
_RUN_ATTEMPT = "run_attempt"

#: 한 시도가 쓰는 super-step 수 — 진입 노드(`begin_attempt`·`begin_difficulty_regen`) 하나와
#: `run_attempt` 하나. 🔴 **그래프 모양에서 나오는 값이라 코드가 든다** — 노드를 늘리면 여기도
#: 늘려야 하고, 안 늘리면 아래 상한이 정상 실행을 자른다(설정 파일로 뺄 값이 아니다).
_STEPS_PER_ATTEMPT = 2

#: START 진입과 종단 판정이 쓰는 여유분.
_GRAPH_STEP_MARGIN = 2


def graph_recursion_limit(*, count: int, config: VerifyConfig) -> int:
    """그래프 super-step 상한을 **계약 상한에서 유도한다** (불변식 6 · 99 #08 ⓑ).

    🔴 **라이브러리 기본값에 기대지 않는다.** 실측(langgraph 1.2.9 · 8/9):
    `_internal/_config.py`의 `DEFAULT_RECURSION_LIMIT`이 **10007**이고
    `LANGGRAPH_DEFAULT_RECURSION_LIMIT` **환경변수로 덮인다.** 즉 지금 상한은
    ⓐ 사실상 무한이라 불변식 6의 방어가 없고 ⓑ 우리 저장소 밖에서 바뀔 수 있다.
    ⚠ 종전 langgraph는 이 값이 **25**였다 — 핀이 되돌아가면 `count=13`부터 정상 요청이
    `GraphRecursionError`로 죽는다. 유도값을 명시해 두면 **버전·환경과 무관해진다.**

    ⚠ 이 값은 「루프를 막는 장치」가 아니다 — 진짜 상한은 상태기계(cursor 단조 증가 ·
    `item_attempt` ≤ `item_attempt_limit`)가 든다. 여기는 **그 상한이 깨졌을 때 걸리는
    마지막 그물**이라, 정상 최대치보다 크고 폭주보다는 작아야 한다.
    """

    per_slot = (
        config.item_attempt_limit + config.difficulty_regen_max
    ) * _STEPS_PER_ATTEMPT
    return count * per_slot + _GRAPH_STEP_MARGIN


class ProblemWorkflowConfigurationError(DomainException):
    """실행 컨텍스트와 고정 버전 또는 입력이 일치하지 않음."""

    code = "INVALID_SCHEMA"
    http_status = 400


class ProblemTenantMismatch(ProblemWorkflowConfigurationError):
    """요청과 실행 컨텍스트의 테넌트가 일치하지 않음."""

    code = "TENANT_MISMATCH"
    http_status = 403


class ProblemSourceUnsupported(ProblemWorkflowConfigurationError):
    """요청한 자료 조달 방식을 현재 워크플로가 지원하지 않음."""

    code = "INVALID_SCHEMA"
    http_status = 400


class ProblemExecutionContextMismatch(DomainException):
    """라우터가 다른 capability의 실행 컨텍스트를 조립함."""

    code = "INTERNAL"
    http_status = 500


class GraphContextError(RuntimeError):
    """GraphRAG 문맥을 검증 가능한 형태로 얻지 못함."""


class GraphContextUnavailable(GraphContextError):
    """GraphRAG 기준 자료 또는 서비스가 일시적으로 없음."""


class GraphContextReferenceInsufficient(GraphContextError):
    """자동 개인화 목표에 승인된 기준 자료가 없음."""



@dataclass(frozen=True, slots=True)
class _AttemptFeedback:
    failed_checks: tuple[str, ...]
    previous_stem_hash: str | None = None


class ProblemGenerationWorkflow:
    """진단을 주입받아 생성·3단계 게이트·저장을 완주한다."""

    def __init__(
        self,
        *,
        diagnosis: DiagnosisCallable,
        graph_context: GraphContextService,
        generator: ProblemGenerator,
        cross_solver: BlindCrossSolver,
        candidate_store: CandidateStore,
        item_store: ProblemItemStore,
        checkpointer: BaseCheckpointSaver[Any],
        verify_config: VerifyConfig | None = None,
        banned_topics: BannedTopicsConfig | None = None,
    ) -> None:
        self._diagnosis = diagnosis
        self._graph_context = graph_context
        self._generator = generator
        self._cross_solver = cross_solver
        self._candidate_store = candidate_store
        self._item_store = item_store
        self._checkpointer = checkpointer
        self._verify_config = verify_config or load_verify_config()
        self._rule_validator = RuleValidator(
            banned_topics or load_banned_topics(),
            duplicate_similarity_max=self._verify_config.dup_similarity_max,
        )

        if (
            self._verify_config.item_attempt_limit
            != PROBLEM_GENERATION_ITEM_ATTEMPT_LIMIT
        ):
            raise ProblemWorkflowConfigurationError(
                "verify_config의 item_attempt 상한이 공용 계약과 다르다"
            )
        if self._generator.prompt_version != self._cross_solver.prompt_version:
            raise ProblemWorkflowConfigurationError(
                "generator와 verifier 프롬프트 버전이 다르다"
            )

    async def run(
        self,
        request: ProblemRequest,
        execution_context: ExecutionContext,
    ) -> ProblemGenerationOutcome:
        """요청을 정상 도메인 결과까지 실행한다."""

        self._validate_execution(request, execution_context)
        prepared = await self._prepare_targets(request, execution_context)
        if isinstance(prepared, RejectedInsufficientOutcome):
            return prepared

        set_id = uuid5(
            execution_context.execution_id,
            f"{request.tenant_id}:{request.request_id}:{request.idempotency_key}",
        )
        initial = ProblemGenerationState(
            request_ref=f"problem-request:{request.request_id}",
            request_hash=request_hash(request),
            set_id=set_id,
            target_source=request.target_source,
            requested_count=request.count,
        )
        graph = self.build_graph(
            request=request,
            execution_context=execution_context,
            targets=prepared,
        )
        try:
            result = await graph.ainvoke(
                initial,
                config={
                    "configurable": {"thread_id": str(set_id)},
                    "recursion_limit": graph_recursion_limit(
                        count=request.count, config=self._verify_config
                    ),
                },
            )
        except GraphContextReferenceInsufficient:
            return RejectedInsufficientOutcome(
                status_reason="출제 목표에 승인된 기준 자료가 없다"
            )
        final_state = ProblemGenerationState.model_validate(result)
        return final_state.to_result()

    def build_graph(
        self,
        *,
        request: ProblemRequest,
        execution_context: ExecutionContext,
        targets: tuple[TargetPlan, ...],
        interrupt_before: tuple[str, ...] = (),
    ) -> Any:  # noqa: ANN401
        """호출 전 attempt 체크포인트를 남기는 비동기 StateGraph를 만든다."""

        feedback: dict[tuple[int, int], _AttemptFeedback] = {}

        async def begin_attempt(
            state: ProblemGenerationState,
        ) -> dict[str, object]:
            if state.item_attempt >= PROBLEM_GENERATION_ITEM_ATTEMPT_LIMIT:
                raise RuntimeError("소진된 item_attempt 뒤에 새 생성을 시작할 수 없다")
            return _checked_update(
                state,
                item_attempt=state.item_attempt + 1,
            )

        async def begin_difficulty_regen(
            state: ProblemGenerationState,
        ) -> dict[str, object]:
            if state.fallback_ref is None or state.difficulty_regen_used:
                raise RuntimeError("난이도 재생성 전이 조건이 충족되지 않았다")
            if state.item_attempt >= PROBLEM_GENERATION_ITEM_ATTEMPT_LIMIT:
                raise RuntimeError("item_attempt 예산 없이 난이도 재생성을 호출할 수 없다")
            return _checked_update(
                state,
                item_attempt=state.item_attempt + 1,
                difficulty_regen_used=True,
            )

        async def run_attempt(
            state: ProblemGenerationState,
        ) -> dict[str, object]:
            if state.item_attempt == 0:
                raise RuntimeError("외부 호출 전에 item_attempt가 체크포인트되지 않았다")
            existing = await self._existing_result(state)
            if existing is not None:
                if existing.attempt_no > state.item_attempt:
                    return {}
                return self._complete_slot(state, existing)
            target = targets[state.cursor % len(targets)]
            type_tag = request.type_tags[state.cursor % len(request.type_tags)]

            try:
                context_pack = await self._resolve_context(
                    request=request,
                    type_tag=type_tag,
                    target=target,
                )
            except (GraphContextUnavailable, TimeoutError) as error:
                if state.fallback_ref is not None:
                    return await self._restore_fallback(state)
                return await self._finalize_verification_unavailable(
                    state,
                    item=None,
                    detail=f"기준 자료 조회 불가: {type(error).__name__}",
                    failure_reason=ProblemFailureReason.SOURCE_UNVERIFIED,
                )

            if not has_reference_data(context_pack):
                if state.fallback_ref is not None:
                    return await self._restore_fallback(state)
                if (
                    request.target_source is TargetSource.WEAKNESS_AUTO
                    and state.cursor == 0
                ):
                    raise GraphContextReferenceInsufficient
                return await self._finalize_verification_unavailable(
                    state,
                    item=None,
                    detail="R-1 기준 자료가 없어 검증할 수 없음",
                    failure_reason=ProblemFailureReason.SOURCE_UNVERIFIED,
                )

            retry_context = self._retry_context(state, feedback, request)
            try:
                item = await self._generator.generate(
                    request=request,
                    type_tag=type_tag,
                    skill_node_id=target.skill_node_id,
                    context_pack=context_pack,
                    retry_context=retry_context,
                    execution_context=execution_context,
                )
            # 생성은 파싱 실패와 서비스 실패가 같은 재생성 예산을 쓴다.
            # 교차 풀이는 파싱 실패만 재시도하므로 아래 교차 풀이 분기와 의도적으로 다르다.
            except RedactionBlocked:
                if state.fallback_ref is not None:
                    return await self._restore_fallback(state)
                return await self._finalize_drop(
                    state,
                    reason=ProblemFailureReason.GENERATION_EXHAUSTED,
                    detail="redaction 불확실로 생성 호출이 차단됨",
                )
            except LlmError as error:
                feedback[(state.cursor, state.item_attempt)] = _AttemptFeedback(
                    failed_checks=(f"generator:{type(error).__name__}",)
                )
                if state.fallback_ref is not None:
                    return await self._restore_fallback(state)
                if state.item_attempt == PROBLEM_GENERATION_ITEM_ATTEMPT_LIMIT:
                    return await self._finalize_drop(
                        state,
                        reason=ProblemFailureReason.GENERATION_EXHAUSTED,
                        detail=f"생성 시도 소진: {type(error).__name__}",
                    )
                return {}

            rule_result = self._rule_validator.validate(
                item=item,
                request=request,
                type_tag=type_tag,
                skill_node_id=target.skill_node_id,
                context_pack=context_pack,
                previous_items=await self._previous_items(state),
            )
            if not rule_result.passed:
                return await self._handle_rule_failure(
                    state=state,
                    item=item,
                    result=rule_result,
                    feedback=feedback,
                )

            try:
                solve_result = await self._cross_solver.solve(
                    item=item,
                    target_skill_node_id=target.skill_node_id,
                    execution_context=execution_context,
                )
            # 교차 풀이는 ParseFailed만 재시도한다. LlmError보다 반드시 먼저 잡아야 한다.
            except RedactionBlocked:
                if state.fallback_ref is not None:
                    return await self._restore_fallback(state)
                return await self._finalize_verification_unavailable(
                    state,
                    item=item,
                    detail="교차 풀이 redaction 불확실",
                )
            except ParseFailed as error:
                feedback[(state.cursor, state.item_attempt)] = _AttemptFeedback(
                    failed_checks=(f"verifier:{type(error).__name__}",),
                    previous_stem_hash=item_stem_hash(item),
                )
                if state.fallback_ref is not None:
                    return await self._restore_fallback(state)
                if state.item_attempt == PROBLEM_GENERATION_ITEM_ATTEMPT_LIMIT:
                    return await self._finalize_verification_unavailable(
                        state,
                        item=item,
                        detail=f"교차 풀이 파싱 시도 소진: {type(error).__name__}",
                    )
                return {}
            except LlmError as error:
                if state.fallback_ref is not None:
                    return await self._restore_fallback(state)
                return await self._finalize_verification_unavailable(
                    state,
                    item=item,
                    detail=f"교차 풀이 서비스 불가: {type(error).__name__}",
                )

            cross_result = validate_cross_solve(
                item,
                solve_result,
                self._verify_config,
            )
            if not cross_result.passed:
                feedback[(state.cursor, state.item_attempt)] = _AttemptFeedback(
                    failed_checks=cross_result.failed_checks,
                    previous_stem_hash=item_stem_hash(item),
                )
                if state.fallback_ref is not None:
                    return await self._restore_fallback(state)
                if state.item_attempt == PROBLEM_GENERATION_ITEM_ATTEMPT_LIMIT:
                    return await self._finalize_drop(
                        state,
                        reason=ProblemFailureReason.GENERATION_EXHAUSTED,
                        detail="교차 풀이 불일치로 생성 시도 소진",
                    )
                return {}

            difficulty_est = estimate_t1_difficulty(
                item=item,
                solve=solve_result,
                config=self._verify_config,
            )
            difficulty_band = classify_t1_difficulty(
                difficulty_est,
                self._verify_config,
            )
            candidate = build_candidate_snapshot(
                set_id=state.set_id,
                slot_index=state.cursor,
                attempt_no=state.item_attempt,
                item=item,
                solve_result=solve_result,
                context_pack_id=context_pack.context_pack_id,
                difficulty_est=difficulty_est,
                difficulty_band=difficulty_band,
            )
            candidate_ref = await self._candidate_store.put(candidate)
            return await self._release_candidate(
                state=state,
                request=request,
                target=target,
                candidate=candidate,
                candidate_ref=candidate_ref,
                low_confidence=cross_result.low_confidence,
            )

        def route_after_attempt(state: ProblemGenerationState) -> str:
            if state.is_terminal:
                return END
            if state.item_attempt == 0:
                return _BEGIN_ATTEMPT
            if state.fallback_ref is not None and not state.difficulty_regen_used:
                return _BEGIN_DIFFICULTY_REGEN
            return _BEGIN_ATTEMPT

        builder = StateGraph(ProblemGenerationState)
        builder.add_node(_BEGIN_ATTEMPT, begin_attempt)
        builder.add_node(_BEGIN_DIFFICULTY_REGEN, begin_difficulty_regen)
        builder.add_node(_RUN_ATTEMPT, run_attempt)
        builder.add_edge(START, _BEGIN_ATTEMPT)
        builder.add_edge(_BEGIN_ATTEMPT, _RUN_ATTEMPT)
        builder.add_edge(_BEGIN_DIFFICULTY_REGEN, _RUN_ATTEMPT)
        routes: dict[Hashable, str] = {
            END: END,
            _BEGIN_ATTEMPT: _BEGIN_ATTEMPT,
            _BEGIN_DIFFICULTY_REGEN: _BEGIN_DIFFICULTY_REGEN,
        }
        builder.add_conditional_edges(_RUN_ATTEMPT, route_after_attempt, routes)
        return builder.compile(
            checkpointer=self._checkpointer,
            interrupt_before=list(interrupt_before),
        )

    async def _prepare_targets(
        self,
        request: ProblemRequest,
        execution_context: ExecutionContext,
    ) -> tuple[TargetPlan, ...] | RejectedInsufficientOutcome:
        if request.target_source is TargetSource.TEACHER_MANUAL:
            if request.manual_targets is None:
                raise AssertionError("ProblemRequest 계약상 수동 목표가 있어야 한다")
            return tuple(TargetPlan(skill_node_id=value) for value in request.manual_targets)

        result = await self._diagnosis(request)
        if result.status is DiagnosisStatus.REJECTED_INSUFFICIENT:
            return RejectedInsufficientOutcome(
                status_reason=result.status_reason or "판정 가능한 약점 목표가 없다"
            )
        weakness_map = result.weakness_map
        if weakness_map is None:
            raise AssertionError("DiagnosisResult 계약상 weakness_map이 있어야 한다")
        versions = execution_context.versions
        if weakness_map.snapshot_hash != request.snapshot_hash:
            raise ProblemWorkflowConfigurationError(
                "진단 weakness_map의 snapshot_hash가 요청과 다르다"
            )
        if weakness_map.taxonomy_version != request.taxonomy_version:
            raise ProblemWorkflowConfigurationError(
                "진단 weakness_map의 taxonomy_version이 요청과 다르다"
            )
        if weakness_map.graph_version != versions.graph_version:
            raise ProblemWorkflowConfigurationError(
                "진단 weakness_map의 graph_version이 실행 버전과 다르다"
            )
        if weakness_map.config_version != self._verify_config.version:
            raise ProblemWorkflowConfigurationError(
                "진단 weakness_map의 config_version이 검증 설정과 다르다"
            )

        confirmed = tuple(
            TargetPlan(skill_node_id=node_id)
            for node_id, node in sorted(weakness_map.nodes.items())
            if node.verdict is NodeVerdict.WEAK_CONFIRMED
        )
        suspect = tuple(
            TargetPlan(skill_node_id=node_id, diagnostic_purpose=True)
            for node_id, node in sorted(weakness_map.nodes.items())
            if node.verdict is NodeVerdict.SUSPECT
        )
        known = {target.skill_node_id for target in (*confirmed, *suspect)}
        propagated = tuple(
            TargetPlan(skill_node_id=node_id, diagnostic_purpose=True)
            for node_id in sorted(weakness_map.propagated)
            if node_id not in known
        )
        targets = confirmed + suspect + propagated
        if not targets:
            return RejectedInsufficientOutcome(
                status_reason="출제 가능한 약점 노드가 없다"
            )
        return targets

    def _validate_execution(
        self,
        request: ProblemRequest,
        execution_context: ExecutionContext,
    ) -> None:
        versions = execution_context.versions
        # **트랙 제한이 아니라 자료 조달 방식 제한이다**(05 §1.0·§1.2).
        # 게이트·프롬프트는 전 영역 공용이고, 지금 구현된 조달 방식은 "자료 없음"뿐이다.
        # "생성"(지문·담화·매체를 LLM이 만든다)과 "저작물"(풀에서 선택) 노드가 없어서
        # 자료를 동반한 요청을 받을 수 없다. 생성 노드 1개가 붙으면 T2 본문·T4·T5가
        # 함께 열린다 — 트랙마다 파이프라인을 다시 만드는 구조가 아니다.
        # ⚠ 생성 노드를 붙일 때 이 조건문도 같이 풀어야 한다 — `area_tag` 검사는
        # '자료가 필요 없는 유일한 영역'의 대리이지 트랙 제한이 아니다. 조건이 OR라
        # `area_tag=language`이면서 `passage` 없음, 둘 다 만족해야 통과한다.
        # enqueue 문 앞 이후의 도달 불가 이중 방어이며, 같은 조건이 두 자리에 있다.
        if request.area_tag not in SUPPORTED_AREAS or request.passage is not None:
            raise ProblemSourceUnsupported(
                "자료 조달 방식이 '자료 없음'인 요청만 처리할 수 있다 "
                "— 생성·저작물 노드 미구현(05 §1.2)",
                {
                    "reason": SOURCE_PROCUREMENT_NOT_IMPLEMENTED,
                    "area_tag": request.area_tag.value,
                    "passage": request.passage is not None,
                },
            )
        if execution_context.capability is not Capability.PROBLEM_GENERATION:
            raise ProblemExecutionContextMismatch(
                "problem_generation 실행 컨텍스트가 아니다"
            )
        if execution_context.tenant_id != request.tenant_id:
            raise ProblemTenantMismatch("요청과 실행의 tenant_id가 다르다")
        if execution_context.input_snapshot_hash != request.snapshot_hash:
            raise ProblemWorkflowConfigurationError(
                "요청과 실행의 input_snapshot_hash가 다르다"
            )
        if versions.taxonomy_version != request.taxonomy_version:
            raise ProblemWorkflowConfigurationError(
                "요청과 실행의 taxonomy_version이 다르다"
            )
        if not versions.graph_version:
            raise ProblemWorkflowConfigurationError(
                "problem_generation에는 graph_version이 필요하다"
            )
        if versions.verify_config_version != self._verify_config.version:
            raise ProblemWorkflowConfigurationError(
                "실행 verify_config_version이 로드 설정과 다르다"
            )
        if versions.prompt_version != self._generator.prompt_version:
            raise ProblemWorkflowConfigurationError(
                "실행 prompt_version이 로드 프롬프트와 다르다"
            )

    async def _resolve_context(
        self,
        *,
        request: ProblemRequest,
        type_tag: TypeTag,
        target: TargetPlan,
    ) -> ContextPack:
        graph_request = GraphContextRequest(
            tenant_id=request.tenant_id,
            target_source=request.target_source,
            weakness_map_id=request.weakness_map_id,
            target_skill_node_ids=(target.skill_node_id,),
            locked_fields=ContextLockedFields(
                target_ref=request.target_ref,
                area_tag=request.area_tag,
                type_tags=(type_tag,),
                skill_node_id=target.skill_node_id,
                item_format=request.item_format,
            ),
            policy_constraints={
                "banned_topics_version": self._rule_validator.banned_topics_version,
                "verify_config_version": self._verify_config.version,
                "evidence_required": True,
            },
        )
        context_pack = await self._graph_context.resolve_generation_context(
            graph_request
        )
        if (
            context_pack.operation is not GraphContextOperation.GENERATE
            or context_pack.tenant_id != graph_request.tenant_id
            or context_pack.target_source is not graph_request.target_source
            or context_pack.weakness_map_id != graph_request.weakness_map_id
            or context_pack.target_skill_node_ids != graph_request.target_skill_node_ids
            or context_pack.locked_fields != graph_request.locked_fields
        ):
            raise GraphContextError("GraphContextService가 요청과 다른 ContextPack을 반환했다")
        return context_pack

    async def _previous_items(
        self,
        state: ProblemGenerationState,
    ) -> tuple[GeneratedItem, ...]:
        previous: list[GeneratedItem] = []
        successful = {
            ProblemItemStatus.VERIFIED,
            ProblemItemStatus.NEEDS_REVIEW,
        }
        for slot_index, result in enumerate(state.items):
            if result.status not in successful:
                continue
            stored = await self._item_store.get(state.set_id, slot_index)
            if stored.item is None:
                raise RuntimeError("검증 완료 문항 저장본에 item 본문이 없다")
            previous.append(stored.item)
        return tuple(previous)

    async def _existing_result(
        self,
        state: ProblemGenerationState,
    ) -> ItemResult | None:
        try:
            stored = await self._item_store.get(state.set_id, state.cursor)
        except LookupError:
            return None
        return stored.result

    def _retry_context(
        self,
        state: ProblemGenerationState,
        feedback: dict[tuple[int, int], _AttemptFeedback],
        request: ProblemRequest,
    ) -> RetryContext:
        previous = feedback.get((state.cursor, state.item_attempt - 1))
        direction: str | None = None
        if state.fallback_ref is not None:
            requested = request.requested_difficulty
            if requested is None:
                raise AssertionError("난이도 재생성에는 요청 밴드가 필요하다")
            direction = (
                "increase"
                if requested is DifficultyBand.HIGH
                else "decrease"
                if requested is DifficultyBand.LOW
                else "toward_medium"
            )
        return RetryContext(
            attempt_no=state.item_attempt,
            failed_checks=previous.failed_checks if previous is not None else (),
            previous_stem_hash=(
                previous.previous_stem_hash if previous is not None else None
            ),
            difficulty_direction=direction,
        )

    async def _handle_rule_failure(
        self,
        *,
        state: ProblemGenerationState,
        item: GeneratedItem,
        result: RuleValidationResult,
        feedback: dict[tuple[int, int], _AttemptFeedback],
    ) -> dict[str, object]:
        if state.fallback_ref is not None:
            return await self._restore_fallback(state)
        if not result.verification_available:
            return await self._finalize_verification_unavailable(
                state,
                item=item,
                detail="R-1 기준 자료를 검증할 수 없음",
                failure_reason=ProblemFailureReason.SOURCE_UNVERIFIED,
            )
        if result.banned_topic:
            return await self._finalize_drop(
                state,
                reason=ProblemFailureReason.BANNED_TOPIC,
                detail="R-5 금칙 오염",
            )
        feedback[(state.cursor, state.item_attempt)] = _AttemptFeedback(
            failed_checks=result.failed_checks,
            previous_stem_hash=item_stem_hash(item),
        )
        if state.item_attempt == PROBLEM_GENERATION_ITEM_ATTEMPT_LIMIT:
            reason = (
                ProblemFailureReason.SOURCE_UNVERIFIED
                if result.source_unverified
                else ProblemFailureReason.GENERATION_EXHAUSTED
            )
            return await self._finalize_drop(
                state,
                reason=reason,
                detail="규칙 검증 실패로 생성 시도 소진",
            )
        return {}

    async def _release_candidate(
        self,
        *,
        state: ProblemGenerationState,
        request: ProblemRequest,
        target: TargetPlan,
        candidate: CandidateSnapshot,
        candidate_ref: str,
        low_confidence: bool,
    ) -> dict[str, object]:
        requested = request.requested_difficulty
        mismatch = needs_difficulty_regeneration(
            candidate.difficulty_band,
            requested,
            self._verify_config,
        )
        can_regenerate = (
            self._verify_config.difficulty_regen_enabled
            and self._verify_config.difficulty_regen_max == 1
            and not state.difficulty_regen_used
            and state.item_attempt < PROBLEM_GENERATION_ITEM_ATTEMPT_LIMIT
        )
        if mismatch and can_regenerate:
            return _checked_update(state, fallback_ref=candidate_ref)

        selected = candidate
        selected_ref = candidate_ref
        difficulty_review = False
        if state.fallback_ref is not None and state.difficulty_regen_used:
            fallback = await self._candidate_store.get(state.fallback_ref)
            if mismatch:
                selected_ref, selected = self._choose_closer(
                    request=request,
                    fallback_ref=state.fallback_ref,
                    fallback=fallback,
                    regenerated_ref=candidate_ref,
                    regenerated=candidate,
                )
                difficulty_review = True
        elif mismatch and self._verify_config.difficulty_regen_enabled:
            difficulty_review = True

        reason = self._review_reason(
            state=state,
            request=request,
            target=target,
            candidate=selected,
            low_confidence=low_confidence,
            difficulty_review=difficulty_review,
        )
        return await self._finalize_candidate(
            state,
            candidate_ref=selected_ref,
            candidate=selected,
            review_reason=reason,
        )

    def _choose_closer(
        self,
        *,
        request: ProblemRequest,
        fallback_ref: str,
        fallback: CandidateSnapshot,
        regenerated_ref: str,
        regenerated: CandidateSnapshot,
    ) -> tuple[str, CandidateSnapshot]:
        requested = request.requested_difficulty
        if requested is None:
            raise AssertionError("난이도 재생성 후보 비교에는 요청 밴드가 필요하다")
        return min(
            ((fallback_ref, fallback), (regenerated_ref, regenerated)),
            key=lambda entry: (
                distance_to_requested_midpoint(
                    entry[1].difficulty_est,
                    requested,
                    self._verify_config,
                ),
                entry[1].attempt_no,
            ),
        )

    def _review_reason(
        self,
        *,
        state: ProblemGenerationState,
        request: ProblemRequest,
        target: TargetPlan,
        candidate: CandidateSnapshot,
        low_confidence: bool,
        difficulty_review: bool,
    ) -> ReviewReason | None:
        if difficulty_review:
            return ReviewReason.DIFFICULTY_BAND_MISMATCH
        candidate_low = (
            candidate.solve_result.confidence
            < self._verify_config.cross_confidence_high
            or candidate.solve_result.alignment_confidence
            < self._verify_config.alignment_confidence_min
        )
        if low_confidence or candidate_low:
            return ReviewReason.LOW_CONFIDENCE
        if target.diagnostic_purpose:
            return ReviewReason.DIAGNOSTIC_PURPOSE
        if (
            request.target_source is TargetSource.TEACHER_MANUAL
            and not any(
                item.status
                in {ProblemItemStatus.VERIFIED, ProblemItemStatus.NEEDS_REVIEW}
                for item in state.items
            )
        ):
            return ReviewReason.MANUAL_TARGET_FIRST
        return None

    async def _restore_fallback(
        self,
        state: ProblemGenerationState,
    ) -> dict[str, object]:
        if state.fallback_ref is None:
            raise AssertionError("복귀할 fallback_ref가 없다")
        fallback = await self._candidate_store.get(state.fallback_ref)
        return await self._finalize_candidate(
            state,
            candidate_ref=state.fallback_ref,
            candidate=fallback,
            review_reason=ReviewReason.DIFFICULTY_BAND_MISMATCH,
        )

    async def _finalize_candidate(
        self,
        state: ProblemGenerationState,
        *,
        candidate_ref: str,
        candidate: CandidateSnapshot,
        review_reason: ReviewReason | None,
    ) -> dict[str, object]:
        status = (
            ProblemItemStatus.NEEDS_REVIEW
            if review_reason is not None
            else ProblemItemStatus.VERIFIED
        )
        item_result = ItemResult(
            item_id=problem_item_id(state.set_id, state.cursor),
            status=status,
            attempt_no=candidate.attempt_no,
            difficulty_est=candidate.difficulty_est,
            difficulty_band=candidate.difficulty_band,
            review_reason=review_reason,
        )
        await self._item_store.save(
            set_id=state.set_id,
            slot_index=state.cursor,
            result=item_result,
            candidate_ref=candidate_ref,
            item=candidate.item,
        )
        return self._complete_slot(state, item_result)

    async def _finalize_verification_unavailable(
        self,
        state: ProblemGenerationState,
        *,
        item: GeneratedItem | None,
        detail: str,
        failure_reason: ProblemFailureReason | None = None,
    ) -> dict[str, object]:
        item_result = ItemResult(
            item_id=problem_item_id(state.set_id, state.cursor),
            status=ProblemItemStatus.VERIFICATION_UNAVAILABLE,
            attempt_no=state.item_attempt,
            failure_reason=failure_reason,
            failure_detail=detail,
        )
        await self._item_store.save(
            set_id=state.set_id,
            slot_index=state.cursor,
            result=item_result,
            candidate_ref=None,
            item=item,
        )
        return self._complete_slot(state, item_result)

    async def _finalize_drop(
        self,
        state: ProblemGenerationState,
        *,
        reason: ProblemFailureReason,
        detail: str,
    ) -> dict[str, object]:
        return self._complete_slot(
            state,
            ItemResult(
                status=ProblemItemStatus.DROPPED,
                attempt_no=state.item_attempt,
                failure_reason=reason,
                failure_detail=detail,
            ),
        )

    def _complete_slot(
        self,
        state: ProblemGenerationState,
        item_result: ItemResult,
    ) -> dict[str, object]:
        items = (*state.items, item_result)
        cursor = state.cursor + 1
        stop_reason = _stop_reason(items, cursor, self._verify_config)
        return _checked_update(
            state,
            cursor=cursor,
            items=items,
            item_attempt=0,
            fallback_ref=None,
            difficulty_regen_used=False,
            stop_reason=stop_reason,
        )


def _stop_reason(
    items: tuple[ItemResult, ...],
    processed_count: int,
    config: VerifyConfig,
) -> SetStopReason | None:
    if processed_count >= 3:
        dropped = sum(item.status is ProblemItemStatus.DROPPED for item in items)
        if dropped / processed_count > config.set_drop_ratio_max:
            return SetStopReason.DROP_RATIO_EXCEEDED

    streak = 0
    for item in reversed(items):
        if item.status is not ProblemItemStatus.VERIFICATION_UNAVAILABLE:
            break
        streak += 1
    if streak >= config.verify_outage_streak_max:
        return SetStopReason.VERIFIER_OUTAGE
    return None


def _checked_update(
    state: ProblemGenerationState,
    **changes: object,
) -> dict[str, object]:
    payload = state.model_dump(mode="python")
    payload.update(changes)
    current = ProblemGenerationState.model_validate(payload)
    assert_problem_generation_state_transition(state, current)
    return changes



__all__ = [
    "DiagnosisCallable",
    "GraphContextError",
    "GraphContextUnavailable",
    "ProblemExecutionContextMismatch",
    "ProblemGenerationWorkflow",
    "ProblemSourceUnsupported",
    "ProblemTenantMismatch",
    "ProblemWorkflowConfigurationError",
    "TargetPlan",
    "graph_recursion_limit",
]
