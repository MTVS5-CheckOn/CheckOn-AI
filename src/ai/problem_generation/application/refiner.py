"""기존 문항을 강사 지시에 따라 수정하고 전체 검증을 다시 수행한다."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, model_validator

from ai.contracts.execution import ExecutionContext
from ai.contracts.gates import BlockedReason
from ai.contracts.graphrag import (
    ContextLockedFields,
    GraphContextOperation,
    GraphContextRequest,
    GraphContextService,
)
from ai.contracts.llm import FieldMissing, LLMRequest, ModelRole, ParseFailed
from ai.contracts.problem_generation import (
    PROBLEM_GENERATION_ITEM_ATTEMPT_LIMIT,
    DifficultyBand,
    GeneratedItem,
    ProblemItemStatus,
    ProblemRequest,
    ReviewReason,
    SolveResult,
)
from ai.llm.determinism import deterministic_params
from ai.llm.gateway import LlmGateway
from ai.llm.prompts.loader import LoadedPromptTemplate, load_prompt_template
from ai.llm.structured import parse
from ai.problem_generation.application.cross_solver import BlindCrossSolver
from ai.problem_generation.application.generator import (
    generated_item_schema_json,
    hydrate_evidence_quotes,
    render_area_spec,
    require_successful_text,
)
from ai.problem_generation.domain.cross_solve import validate_cross_solve
from ai.problem_generation.domain.difficulty import (
    classify_t1_difficulty,
    estimate_t1_difficulty,
    needs_difficulty_regeneration,
)
from ai.problem_generation.domain.external_corpus import ExternalCorpusIndex
from ai.problem_generation.domain.identity import canonical_json
from ai.problem_generation.domain.policy import (
    AreaSpecs,
    BannedTopicsConfig,
    VerifyConfig,
)
from ai.problem_generation.domain.rules import RuleValidator
from ai.runtime.redaction import redact

_REFINE_PROMPT_ID = "pg.refine.v1"


class ProblemRefineOutcome(BaseModel):
    """수정 1턴의 적용 또는 정상 차단 결과."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    applied: bool
    item: GeneratedItem | None = None
    solve_result: SolveResult | None = None
    blocked_reason: BlockedReason | None = None
    failed_checks: tuple[str, ...] = ()
    release_status: ProblemItemStatus | None = None
    review_reason: ReviewReason | None = None
    difficulty_est: float | None = None
    difficulty_band: DifficultyBand | None = None

    @model_validator(mode="after")
    def validate_outcome(self) -> ProblemRefineOutcome:
        if self.applied:
            if self.item is None or self.solve_result is None:
                raise ValueError("적용된 수정에는 문항과 교차 풀이 결과가 필요하다")
            if self.blocked_reason is not None or self.failed_checks:
                raise ValueError("적용된 수정에는 차단 정보를 기록하지 않는다")
            if (
                self.release_status is None
                or self.difficulty_est is None
                or self.difficulty_band is None
            ):
                raise ValueError("적용된 수정에는 release 재판정 결과가 필요하다")
            if (
                self.release_status is ProblemItemStatus.NEEDS_REVIEW
                and self.review_reason is None
            ):
                raise ValueError("검토 필요 수정에는 review_reason이 필요하다")
            if (
                self.release_status is ProblemItemStatus.VERIFIED
                and self.review_reason is not None
            ):
                raise ValueError("검증 완료 수정에는 review_reason을 기록하지 않는다")
        elif self.blocked_reason is None:
            raise ValueError("차단된 수정에는 blocked_reason이 필요하다")
        return self


class ProblemItemRefiner:
    """마스킹→수정 생성→규칙 검증→blind 교차 풀이를 한 경로로 묶는다."""

    def __init__(
        self,
        *,
        gateway: LlmGateway,
        graph_context: GraphContextService,
        verify_config: VerifyConfig,
        banned_topics: BannedTopicsConfig,
        area_specs: AreaSpecs,
        prompt: LoadedPromptTemplate | None = None,
        external_corpus: ExternalCorpusIndex | None = None,
    ) -> None:
        self._gateway = gateway
        self._graph_context = graph_context
        self._verify_config = verify_config
        self._banned_topics = banned_topics
        self._area_specs = area_specs
        self._prompt = prompt or load_prompt_template(_REFINE_PROMPT_ID)
        self._rules = RuleValidator(
            banned_topics,
            duplicate_similarity_max=verify_config.dup_similarity_max,
            external_corpus=external_corpus,
            external_similarity_max=verify_config.external_similarity_max,
        )
        self._cross_solver = BlindCrossSolver(gateway)
        if self._prompt.role is not ModelRole.GENERATOR:
            raise ValueError("문항 수정 프롬프트 role은 generator여야 한다")
        if self._prompt.response_schema_name != GeneratedItem.__name__:
            raise ValueError("문항 수정 프롬프트 응답 스키마가 GeneratedItem이 아니다")

    async def refine(
        self,
        *,
        original: GeneratedItem,
        request: ProblemRequest,
        instruction: str,
        execution_context: ExecutionContext,
    ) -> ProblemRefineOutcome:
        # 🔴 **영역 제한을 없앴다(5영역).** 종전에는 여기서 `language` 만 통과시켜, 출제는
        #   5영역이 열렸는데 수정은 언어 하나만 되는 구멍이 있었다. 수정 컨텍스트가 이제
        #   생성 경로와 같은 축으로 갈리고(`graph_context`), 생성·저작물 트랙은 **원 문항이
        #   이미 승인받은 근거만 재사용**한다 — 수정은 근거를 새로 조달하는 자리가 아니다.
        # ⚠ 근거를 새로 지어내는 것은 여전히 막힌다(불변식 2) — 허용 집합이 원 문항의
        #   것으로 닫혀 있어 `R-1:근거_참조_불일치` 가 잡는다.
        if original.skill_node_id is None:
            raise ValueError("수정할 문항에 skill_node_id가 없다")

        masked_instruction = redact(instruction)
        if masked_instruction.uncertain:
            return ProblemRefineOutcome(
                applied=False,
                blocked_reason=BlockedReason.PII_EXPOSURE,
            )
        normalized = masked_instruction.masked_text.casefold()
        if any(
            pattern.casefold() in normalized
            for pattern in self._banned_topics.prompt_injection_patterns
        ):
            return ProblemRefineOutcome(
                applied=False,
                blocked_reason=BlockedReason.PROMPT_INJECTION,
            )

        graph_request = GraphContextRequest(
            tenant_id=request.tenant_id,
            target_source=request.target_source,
            weakness_map_id=request.weakness_map_id,
            target_skill_node_ids=(original.skill_node_id,),
            locked_fields=ContextLockedFields(
                target_ref=request.target_ref,
                area_tag=original.area_tag,
                type_tags=(original.type_tag,),
                skill_node_id=original.skill_node_id,
                item_format=original.item_format,
            ),
            current_item_snapshot=original.model_dump(mode="json"),
            redacted_instruction=masked_instruction.masked_text,
            policy_constraints={
                "banned_topics_version": self._banned_topics.version,
                "verify_config_version": self._verify_config.version,
                "evidence_required": True,
            },
        )
        context = await self._graph_context.resolve_revision_context(graph_request)
        if (
            context.operation is not GraphContextOperation.REFINE
            or context.tenant_id != graph_request.tenant_id
            or context.locked_fields != graph_request.locked_fields
            or context.current_item_snapshot != graph_request.current_item_snapshot
            or context.redacted_instruction != graph_request.redacted_instruction
        ):
            raise ValueError("GraphContextService가 수정 요청과 다른 ContextPack을 반환했다")

        prompt_text = self._prompt.render(
            {
                "area_spec_block": render_area_spec(
                    self._area_specs.spec_for(original.area_tag)
                ),
                "context_pack_json": canonical_json(context.model_dump(mode="json")),
                "current_item_json": canonical_json(original.model_dump(mode="json")),
                "instruction_json": canonical_json(masked_instruction.masked_text),
                "response_schema_json": generated_item_schema_json(),
            }
        )
        redacted = redact(prompt_text)
        if redacted.uncertain:
            # 🔴 **게이트 거부는 에러가 아니다(불변식 4).** 종전에는 여기서 `RedactionBlocked`
            #   를 올렸고 라우터의 `except LlmError` 가 `domain_error_for` 로 넘겨 **HTTP 500**
            #   이 나갔다 — 실측(2026-08-20 종단): 5영역 대화 2턴 중 **3건이 500**이었다.
            # 🔴 **같은 사유인데 결과가 갈리고 있었다.** 바로 위에서 **강사 지시**가 마스킹
            #   불확실이면 `PII_EXPOSURE` 로 정상 반환하는데 **프롬프트 전체**가 걸리면 500이다.
            #   강사에게는 둘 다 「마스킹 때문에 못 고쳤다」인데 한쪽만 장애로 보인다.
            # ⚠ 마스킹을 무르는 게 아니다 — 차단된 프롬프트는 **한 번도 전송되지 않는다.**
            #   바뀌는 것은 그 사실을 500으로 알릴지 200 + 차단 사유로 알릴지다.
            return ProblemRefineOutcome(
                applied=False,
                blocked_reason=BlockedReason.PII_EXPOSURE,
            )
        # 🔴 **파싱 실패를 첫 판에 500으로 올리지 않는다 — 생성 경로와 같은 규율.**
        #   `parse()` 가 던지는 `ParseFailed`·`FieldMissing` 은 라우터의 `except LlmError`
        #   가 `domain_error_for` 로 넘겨 **500 INTERNAL** 이 된다. 그런데 생성 경로는 같은
        #   예외에 **재생성 예산을 쓰고**(workflow `except LlmError`) 소진돼야 도메인 결과로
        #   끝낸다 — 같은 사건인데 수정 쪽만 장애로 나가는 비대칭이었다.
        #   실측(2026-08-20 종단 · speech_writing 8회): **2회가 500**, 나머지는 200.
        # ⚠ 상한은 문항 시도 계약(`PROBLEM_GENERATION_ITEM_ATTEMPT_LIMIT`)을 따른다(불변식 6).
        # ⚠ 소진되면 **정상 차단**으로 끝낸다 — 게이트 거부는 에러가 아니다(불변식 4).
        revised = None
        for _ in range(PROBLEM_GENERATION_ITEM_ATTEMPT_LIMIT):
            result = await self._gateway.complete(
                LLMRequest(
                    role=ModelRole.GENERATOR,
                    prompt=redacted.masked_text,
                    prompt_id=self._prompt.prompt_id,
                    prompt_version=self._prompt.version,
                    response_schema_name=self._prompt.response_schema_name,
                    generation_params=deterministic_params(),
                ),
                execution_context,
            )
            try:
                revised = hydrate_evidence_quotes(
                    parse(require_successful_text(result), GeneratedItem), context
                )
                break
            except (ParseFailed, FieldMissing):
                continue
        if revised is None:
            return ProblemRefineOutcome(
                applied=False,
                blocked_reason=BlockedReason.ANSWER_INTEGRITY,
                failed_checks=("refine:ParseFailed",),
            )
        rule_result = self._rules.validate(
            item=revised,
            request=request,
            type_tag=original.type_tag,
            skill_node_id=original.skill_node_id,
            context_pack=context,
        )
        if not rule_result.passed:
            reason = (
                BlockedReason.BANNED_TOPIC
                if rule_result.banned_topic
                else BlockedReason.EVIDENCE_MISSING
                if rule_result.source_unverified
                else BlockedReason.ANSWER_INTEGRITY
            )
            return ProblemRefineOutcome(
                applied=False,
                blocked_reason=reason,
                failed_checks=rule_result.failed_checks,
            )

        solve = await self._cross_solver.solve(
            item=revised,
            target_skill_node_id=original.skill_node_id,
            execution_context=execution_context,
        )
        cross_result = validate_cross_solve(revised, solve, self._verify_config)
        if not cross_result.passed:
            return ProblemRefineOutcome(
                applied=False,
                blocked_reason=BlockedReason.ANSWER_INTEGRITY,
                failed_checks=cross_result.failed_checks,
            )
        difficulty_est = estimate_t1_difficulty(
            item=revised,
            solve=solve,
            config=self._verify_config,
        )
        difficulty_band = classify_t1_difficulty(
            difficulty_est, self._verify_config
        )
        difficulty_mismatch = needs_difficulty_regeneration(
            difficulty_band,
            request.requested_difficulty,
            self._verify_config,
        )
        review_reason = (
            ReviewReason.DIFFICULTY_BAND_MISMATCH
            if difficulty_mismatch
            else ReviewReason.LOW_CONFIDENCE
            if cross_result.low_confidence
            else None
        )
        return ProblemRefineOutcome(
            applied=True,
            item=revised,
            solve_result=solve,
            release_status=(
                ProblemItemStatus.NEEDS_REVIEW
                if review_reason is not None
                else ProblemItemStatus.VERIFIED
            ),
            review_reason=review_reason,
            difficulty_est=difficulty_est,
            difficulty_band=difficulty_band,
        )


__all__ = ["ProblemItemRefiner", "ProblemRefineOutcome"]
