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
    FieldMissing,
    LlmError,
    ParseFailed,
    RedactionBlocked,
)
from ai.contracts.problem_generation import (
    PROBLEM_GENERATION_ITEM_ATTEMPT_LIMIT,
    DifficultyBand,
    GeneratedItem,
    ItemResult,
    PassageDraft,
    PassageRequest,
    ProblemFailureReason,
    ProblemGenerationOutcome,
    ProblemGenerationState,
    ProblemItemStatus,
    ProblemRequest,
    RejectedInsufficientOutcome,
    ReviewReason,
    SetStopReason,
    SourceMaterialDraft,
    TargetSource,
    WorkExcerpt,
    assert_problem_generation_state_transition,
)
from ai.contracts.taxonomy import AreaTag, TypeTag
from ai.problem_generation.application.cross_solver import BlindCrossSolver
from ai.problem_generation.application.generator import (
    ProblemGenerator,
    build_candidate_snapshot,
    schema_issues_from_field_missing,
)
from ai.problem_generation.application.literature_selector import (
    LiteratureSelectionUnavailable,
    LiteratureSelector,
    attach_work_excerpt,
)
from ai.problem_generation.application.passage_generator import (
    PassageDraftRejected,
    PassageGenerationUnavailable,
    PassageGenerator,
    SourceMaterialDraftRejected,
    SourceMaterialGenerationUnavailable,
    SourceMaterialGenerator,
    attach_passage_draft,
    attach_source_material_draft,
)
from ai.problem_generation.application.ports import (
    CandidateStore,
    ProblemItemStore,
    ProblemSetStore,
)
from ai.problem_generation.domain.cross_solve import validate_cross_solve
from ai.problem_generation.domain.difficulty import (
    classify_t1_difficulty,
    distance_to_requested_midpoint,
    estimate_t1_difficulty,
    needs_difficulty_regeneration,
)
from ai.problem_generation.domain.external_corpus import ExternalCorpusIndex
from ai.problem_generation.domain.identity import (
    canonical_json,
    item_stem_hash,
    problem_item_id,
    request_hash,
)
from ai.problem_generation.domain.models import (
    CandidateSnapshot,
    RetryContext,
    SchemaValidationIssue,
    TargetPlan,
)
from ai.problem_generation.domain.policy import (
    BannedTopicsConfig,
    VerifyConfig,
    requires_reference_before_source_procurement,
    supports_source_procurement,
)
from ai.problem_generation.domain.rules import (
    RuleValidationResult,
    RuleValidator,
    has_reference_data,
)
from ai.runtime.errors import DomainException
from ai.runtime.redaction import redact

type DiagnosisCallable = Callable[[ProblemRequest], Awaitable[DiagnosisResult]]

SOURCE_PROCUREMENT_NOT_IMPLEMENTED = "source_procurement_not_implemented"

_PROCURE_SOURCE = "procure_source"
_BEGIN_ATTEMPT = "begin_attempt"
_BEGIN_DIFFICULTY_REGEN = "begin_difficulty_regen"
_RUN_ATTEMPT = "run_attempt"

#: 한 시도가 쓰는 super-step 수 — 진입 노드(`begin_attempt`·`begin_difficulty_regen`) 하나와
#: `run_attempt` 하나. 🔴 **그래프 모양에서 나오는 값이라 코드가 든다** — 노드를 늘리면 여기도
#: 늘려야 하고, 안 늘리면 아래 상한이 정상 실행을 자른다(설정 파일로 뺄 값이 아니다).
_STEPS_PER_ATTEMPT = 2

#: START 진입·요청당 자료 조달 노드·종단 판정이 쓰는 여유분.
_GRAPH_STEP_MARGIN = 3


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
    schema_issues: tuple[SchemaValidationIssue, ...] = ()
    previous_stem_hash: str | None = None


class ProblemGenerationWorkflow:
    """진단을 주입받아 생성·3단계 게이트·저장을 완주한다."""

    def __init__(
        self,
        *,
        diagnosis: DiagnosisCallable,
        graph_context: GraphContextService,
        generator: ProblemGenerator,
        passage_generator: PassageGenerator,
        source_material_generator: SourceMaterialGenerator,
        literature_selector: LiteratureSelector,
        cross_solver: BlindCrossSolver,
        candidate_store: CandidateStore,
        item_store: ProblemItemStore,
        set_store: ProblemSetStore,
        checkpointer: BaseCheckpointSaver[Any],
        verify_config: VerifyConfig,
        banned_topics: BannedTopicsConfig,
        external_corpus: ExternalCorpusIndex | None = None,
    ) -> None:
        self._diagnosis = diagnosis
        self._graph_context = graph_context
        self._generator = generator
        self._passage_generator = passage_generator
        self._source_material_generator = source_material_generator
        self._literature_selector = literature_selector
        self._cross_solver = cross_solver
        self._candidate_store = candidate_store
        self._item_store = item_store
        self._set_store = set_store
        self._checkpointer = checkpointer
        self._verify_config = verify_config
        self._rule_validator = RuleValidator(
            banned_topics,
            duplicate_similarity_max=self._verify_config.dup_similarity_max,
            external_corpus=external_corpus,
            external_similarity_max=self._verify_config.external_similarity_max,
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
        if (
            self._passage_generator.banned_topics_version
            != self._rule_validator.banned_topics_version
        ):
            raise ProblemWorkflowConfigurationError(
                "지문 생성기와 규칙 검증기의 금칙 설정 버전이 다르다"
            )
        if (
            self._source_material_generator.banned_topics_version
            != self._rule_validator.banned_topics_version
        ):
            raise ProblemWorkflowConfigurationError(
                "생성 자료기와 규칙 검증기의 금칙 설정 버전이 다르다"
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
        await self._set_store.create(
            set_id=set_id,
            request=request,
            execution_context=execution_context,
            diagnostic_purpose=any(target.diagnostic_purpose for target in prepared),
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
        except PassageGenerationUnavailable:
            return RejectedInsufficientOutcome(
                status_reason="승인된 기준 자료로 T2 지문을 생성할 수 없다"
            )
        except SourceMaterialGenerationUnavailable:
            return RejectedInsufficientOutcome(
                status_reason="승인된 기준 자료로 화법과작문·매체 자료를 생성할 수 없다"
            )
        except PassageDraftRejected:
            return RejectedInsufficientOutcome(
                status_reason="T2 생성 자료를 승인 근거로 고정할 수 없다"
            )
        except SourceMaterialDraftRejected:
            return RejectedInsufficientOutcome(
                status_reason="화법과작문·매체 생성 자료를 승인 근거로 고정할 수 없다"
            )
        except LlmError:
            return RejectedInsufficientOutcome(
                status_reason="자료 생성 서비스를 사용할 수 없다"
            )
        except LiteratureSelectionUnavailable:
            return RejectedInsufficientOutcome(
                status_reason="요청 조건에 맞는 저작권 만료 문학 원문을 선택할 수 없다"
            )
        final_state = ProblemGenerationState.model_validate(result)
        outcome = final_state.to_result()
        await self._set_store.finalize(outcome)
        return outcome

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

        async def procure_source(
            state: ProblemGenerationState,
        ) -> dict[str, object]:
            passage_request = request.passage
            if passage_request is not None:
                if isinstance(passage_request, PassageRequest):
                    if state.passage_draft is not None:
                        return {}
                elif state.source_material_draft is not None:
                    return {}
                target = targets[0]
                type_tag = request.type_tags[0]
                context_pack = await self._resolve_context(
                    request=request,
                    type_tag=type_tag,
                    target=target,
                )
                if requires_reference_before_source_procurement(
                    request.area_tag
                ) and not has_reference_data(context_pack):
                    raise GraphContextReferenceInsufficient
                # 🔴 **보낼 수 없는 자료를 문항 생성으로 넘기지 않는다.**
                #   생성 자료에 인명 후보가 한 문장에 둘 이상 들어가면 전송 직전에
                #   `RedactionBlocked` 가 나고 슬롯이 통째 드롭된다. 그런데 **자료는 세트당
                #   한 번만 만들어지므로** 문항 재시도로는 같은 자료가 다시 온다 — 걸러야
                #   하는 자리가 여기다.
                # ⚠ 마스킹 규칙을 무르는 게 아니라 **보낼 수 있는 자료를 고르는** 것이다 —
                #   문학 선택기의 `sendable_spans` 와 같은 패턴이다.
                # 🔴 재생성이 다른 결과를 낸다는 근거는 `llm/determinism.py` 에 있다 —
                #   같은 seed 로도 출력이 갈린다(8/13 실측 · 8회에 3종). seed 축은 안 건드린다.
                # ⚠ 상한은 `regen_max` 를 따른다(불변식 6). 소진되면 숨기지 않고 올린다.
                # ⚠ 재생성 횟수는 `AI_RUN` 의 LLM 호출 수로 관측된다 — 이 모듈에는 로거가
                #   없고, 관측 하나 때문에 모듈 규약을 넓히지 않는다.
                attempts = self._verify_config.source_redaction_retry_max + 1
                if isinstance(passage_request, PassageRequest):
                    for _ in range(attempts):
                        draft = await self._passage_generator.generate(
                            passage_request=passage_request,
                            context_pack=context_pack,
                            execution_context=execution_context,
                        )
                        # ⚠ **원문이 아니라 직렬화 형태로 본다** — 프롬프트에는 draft 가
                        #   `canonical_json` 으로 실린다. 원문에서는 문단이 개행으로
                        #   갈리지만 JSON 안에서는 이스케이프돼 문장 경계가 달라지고,
                        #   원문만 보면 통과한 자료가 실제로는 막힌다(실측).
                        if not redact(canonical_json(draft.model_dump(mode="json"))).uncertain:
                            return _checked_update(state, passage_draft=draft)
                    raise RedactionBlocked("생성 지문이 재생성 상한까지 마스킹 불확실하다")
                for _ in range(attempts):
                    material = await self._source_material_generator.generate_source_material(
                        source_request=passage_request,
                        context_pack=context_pack,
                        execution_context=execution_context,
                    )
                    if not redact(canonical_json(material.model_dump(mode="json"))).uncertain:
                        return _checked_update(state, source_material_draft=material)
                raise RedactionBlocked("생성 자료가 재생성 상한까지 마스킹 불확실하다")

            work_selection = request.work_selection
            if work_selection is not None and state.work_excerpt is None:
                excerpt = self._literature_selector.select(work_selection)
                return _checked_update(state, work_excerpt=excerpt)
            return {}

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
            if isinstance(request.passage, PassageRequest) and state.passage_draft is None:
                raise RuntimeError("T2 문항 생성 전에 passage_draft가 준비되지 않았다")
            if (
                request.passage is not None
                and not isinstance(request.passage, PassageRequest)
                and state.source_material_draft is None
            ):
                raise RuntimeError(
                    "화법과작문·매체 문항 생성 전에 source_material_draft가 준비되지 않았다"
                )
            if request.work_selection is not None and state.work_excerpt is None:
                raise RuntimeError("T3 문항 생성 전에 work_excerpt가 준비되지 않았다")

            try:
                context_pack = await self._resolve_context(
                    request=request,
                    type_tag=type_tag,
                    target=target,
                    passage_draft=state.passage_draft,
                    source_material_draft=state.source_material_draft,
                    work_excerpt=state.work_excerpt,
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
            # 🔴 첫 차단에 드롭하지 않고 재생성 예산을 쓴다 — 아래 교차 풀이 분기와 같은 규율.
            #   생성 프롬프트에 실리는 한국어 문면이 밀도 규칙에 걸리면 막히는데, 다시 뽑으면
            #   대개 풀린다. 종전에는 예산을 한 번도 안 쓰고 슬롯을 통째 버렸다.
            # ⚠ 규칙을 무르는 게 아니다 — 막힌 프롬프트는 한 번도 전송되지 않는다.
            except RedactionBlocked:
                feedback[(state.cursor, state.item_attempt)] = _AttemptFeedback(
                    failed_checks=("generator:RedactionBlocked",),
                )
                if state.fallback_ref is not None:
                    return await self._restore_fallback(state)
                if state.item_attempt == PROBLEM_GENERATION_ITEM_ATTEMPT_LIMIT:
                    return await self._finalize_drop(
                        state,
                        reason=ProblemFailureReason.GENERATION_EXHAUSTED,
                        detail="redaction 불확실로 생성 호출이 차단됨 — 시도 소진",
                    )
                return {}
            except LlmError as error:
                feedback[(state.cursor, state.item_attempt)] = _AttemptFeedback(
                    failed_checks=(f"generator:{type(error).__name__}",),
                    schema_issues=(
                        schema_issues_from_field_missing(error)
                        if isinstance(error, FieldMissing)
                        else ()
                    ),
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

            misconception_failures = (
                self._cross_solver.misconception_structure_failures(item)
            )
            if misconception_failures:
                feedback[(state.cursor, state.item_attempt)] = _AttemptFeedback(
                    failed_checks=misconception_failures,
                    previous_stem_hash=item_stem_hash(item),
                )
                if state.fallback_ref is not None:
                    return await self._restore_fallback(state)
                if state.item_attempt == PROBLEM_GENERATION_ITEM_ATTEMPT_LIMIT:
                    return await self._finalize_drop(
                        state,
                        reason=ProblemFailureReason.GENERATION_EXHAUSTED,
                        detail="오개념 구조 검증 실패로 생성 시도 소진",
                    )
                return {}

            try:
                solve_result = await self._cross_solver.solve(
                    item=item,
                    target_skill_node_id=target.skill_node_id,
                    execution_context=execution_context,
                )
                misconception_check = await self._cross_solver.check_misconceptions(
                    item=item,
                    execution_context=execution_context,
                )
            # 교차 검증은 ParseFailed만 재시도한다. LlmError보다 반드시 먼저 잡아야 한다.
            # 🔴 **redaction 차단을 첫 판에 확정으로 읽지 않는다.**
            #   교차 검증 프롬프트에는 **방금 생성된 문항 본문**이 실린다. 그 한국어 문장에
            #   인명 후보가 한 문장에 둘 이상 들어가면 전송이 막히는데, 그건 **이 문항의
            #   표현 문제**이지 검증 불가가 아니다 — 문항을 다시 뽑으면 대개 풀린다.
            # ⚠ 그래서 `ParseFailed` 와 **같은 자리**에 둔다: 시도 예산을 쓰고, 소진됐을 때만
            #   `verification_unavailable` 로 확정한다(불변식 6 · 상한은 그대로).
            # ⚠ 마스킹을 무르는 게 아니다 — 차단된 프롬프트는 **한 번도 전송되지 않는다.**
            except RedactionBlocked:
                feedback[(state.cursor, state.item_attempt)] = _AttemptFeedback(
                    failed_checks=("verifier:RedactionBlocked",),
                    previous_stem_hash=item_stem_hash(item),
                )
                if state.fallback_ref is not None:
                    return await self._restore_fallback(state)
                if state.item_attempt == PROBLEM_GENERATION_ITEM_ATTEMPT_LIMIT:
                    return await self._finalize_verification_unavailable(
                        state,
                        item=item,
                        detail="교차 풀이 redaction 불확실 — 시도 소진",
                    )
                return {}
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
                misconception_check,
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
        builder.add_node(_PROCURE_SOURCE, procure_source)
        builder.add_node(_BEGIN_ATTEMPT, begin_attempt)
        builder.add_node(_BEGIN_DIFFICULTY_REGEN, begin_difficulty_regen)
        builder.add_node(_RUN_ATTEMPT, run_attempt)
        builder.add_edge(START, _PROCURE_SOURCE)
        builder.add_edge(_PROCURE_SOURCE, _BEGIN_ATTEMPT)
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
        # enqueue 문 앞 이후의 도달 불가 이중 방어이며, 같은 순수 판정을 공유한다.
        if not supports_source_procurement(
            area_tag=request.area_tag,
            has_passage_request=request.passage is not None,
            has_work_selection=request.work_selection is not None,
        ):
            raise ProblemSourceUnsupported(
                "지원되는 자료 조달 조합은 language+자료 없음, reading+PassageRequest, "
                "literature+WorkSelection, speech_writing·media+생성 자료 요청이다",
                {
                    "reason": SOURCE_PROCUREMENT_NOT_IMPLEMENTED,
                    "area_tag": request.area_tag.value,
                    "passage": request.passage is not None,
                    "work_selection": request.work_selection is not None,
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
        passage_draft: PassageDraft | None = None,
        source_material_draft: SourceMaterialDraft | None = None,
        work_excerpt: WorkExcerpt | None = None,
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
                "taxonomy_version": request.taxonomy_version,
                "verify_config_version": self._verify_config.version,
                "evidence_required": True,
                "source_request": (
                    request.passage.model_dump(mode="json")
                    if request.passage is not None
                    else None
                ),
                "work_selection": (
                    request.work_selection.model_dump(mode="json")
                    if request.work_selection is not None
                    else None
                ),
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
        if passage_draft is not None:
            return attach_passage_draft(context_pack, passage_draft)
        if source_material_draft is not None:
            return attach_source_material_draft(context_pack, source_material_draft)
        if work_excerpt is not None:
            return attach_work_excerpt(context_pack, work_excerpt)
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
            schema_issues=previous.schema_issues if previous is not None else (),
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
            detail = (
                "R-1 어휘 대조 구현 안 됨 — LexiconLookup 미배선"
                if "R-1:어휘_대조_미구현" in result.failed_checks
                else "R-1 기준 자료를 검증할 수 없음"
            )
            return await self._finalize_verification_unavailable(
                state,
                item=item,
                detail=detail,
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
            # ⚠ **어느 규칙이 떨어졌는지 싣는다.** 종전 문구는 "규칙 검증 실패"뿐이라
            #   `R-1:기준_자료_없음`(자료가 없다)과 `R-1:근거_참조_불일치`(모델이 승인 밖
            #   ref 를 인용했다)를 **응답만 보고 가를 수 없었다** — 원인이 정반대인데
            #   같은 문장이 나왔다. 검사명은 규칙 식별자라 개인정보가 아니다.
            return await self._finalize_drop(
                state,
                reason=reason,
                detail=(
                    "규칙 검증 실패로 생성 시도 소진: "
                    + ", ".join(result.failed_checks)
                ),
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
        if request.area_tag is AreaTag.LITERATURE:
            return ReviewReason.T3_LITERATURE
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
        item_result = ItemResult(
            status=ProblemItemStatus.DROPPED,
            attempt_no=state.item_attempt,
            failure_reason=reason,
            failure_detail=detail,
        )
        await self._item_store.save(
            set_id=state.set_id,
            slot_index=state.cursor,
            result=item_result,
            candidate_ref=None,
            item=None,
        )
        return self._complete_slot(state, item_result)

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
