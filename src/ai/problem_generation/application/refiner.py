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
from ai.contracts.llm import LLMRequest, ModelRole, RedactionBlocked
from ai.contracts.problem_generation import GeneratedItem, ProblemRequest, SolveResult
from ai.contracts.taxonomy import AreaTag
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

    @model_validator(mode="after")
    def validate_outcome(self) -> ProblemRefineOutcome:
        if self.applied:
            if self.item is None or self.solve_result is None:
                raise ValueError("적용된 수정에는 문항과 교차 풀이 결과가 필요하다")
            if self.blocked_reason is not None or self.failed_checks:
                raise ValueError("적용된 수정에는 차단 정보를 기록하지 않는다")
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
        if request.area_tag is not AreaTag.LANGUAGE:
            raise NotImplementedError("현재 ai_refine은 language 문항만 지원한다")
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
        redacted_prompt = redact(prompt_text)
        if redacted_prompt.uncertain:
            raise RedactionBlocked("문항 수정 프롬프트의 개인정보 마스킹이 불확실하다")
        result = await self._gateway.complete(
            LLMRequest(
                role=ModelRole.GENERATOR,
                prompt=redacted_prompt.masked_text,
                prompt_id=self._prompt.prompt_id,
                prompt_version=self._prompt.version,
                response_schema_name=self._prompt.response_schema_name,
                generation_params=deterministic_params(),
            ),
            execution_context,
        )
        revised = hydrate_evidence_quotes(
            parse(require_successful_text(result), GeneratedItem), context
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
        return ProblemRefineOutcome(applied=True, item=revised, solve_result=solve)


__all__ = ["ProblemItemRefiner", "ProblemRefineOutcome"]
