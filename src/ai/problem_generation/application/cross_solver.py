"""정답·해설·근거를 제거한 독립 교차 풀이 호출."""

from __future__ import annotations

from ai.contracts.execution import ExecutionContext, GenerationParams
from ai.contracts.llm import LLMRequest, ModelRole, RedactionBlocked
from ai.contracts.problem_generation import GeneratedItem, SolveResult
from ai.llm.gateway import LlmGateway
from ai.llm.prompts.loader import LoadedPromptTemplate, load_prompt_template
from ai.llm.structured import parse
from ai.problem_generation.application.generator import require_successful_text
from ai.problem_generation.domain.identity import canonical_json
from ai.runtime.redaction import redact

_CROSS_SOLVE_PROMPT_ID = "pg.cross_solve.v1"


class BlindCrossSolver:
    """generator의 정답 정보를 전달하지 않는 verifier 경계."""

    def __init__(
        self,
        gateway: LlmGateway,
        prompt: LoadedPromptTemplate | None = None,
    ) -> None:
        self._gateway = gateway
        self._prompt = prompt or load_prompt_template(_CROSS_SOLVE_PROMPT_ID)
        if self._prompt.role is not ModelRole.VERIFIER:
            raise ValueError("교차 풀이 프롬프트 role은 verifier여야 한다")
        if self._prompt.response_schema_name != SolveResult.__name__:
            raise ValueError("교차 풀이 프롬프트 응답 스키마가 SolveResult가 아니다")

    @property
    def prompt_version(self) -> str:
        return self._prompt.version

    async def solve(
        self,
        *,
        item: GeneratedItem,
        target_skill_node_id: str,
        execution_context: ExecutionContext,
    ) -> SolveResult:
        blind_item = {
            "area_tag": item.area_tag.value,
            "type_tag": item.type_tag.value,
            "item_format": item.item_format.value,
            "skill_node_id": item.skill_node_id,
            "stem": item.stem,
            "choices": tuple(
                {"no": choice.no, "text": choice.text} for choice in item.choices
            ),
        }
        target_metadata = {
            "target_skill_node_id": target_skill_node_id,
            "area_tag": item.area_tag.value,
            "type_tag": item.type_tag.value,
        }
        prompt_text = self._prompt.render(
            {
                "blind_item_json": canonical_json(blind_item),
                "target_metadata_json": canonical_json(target_metadata),
            }
        )
        redacted = redact(prompt_text)
        if redacted.uncertain:
            raise RedactionBlocked("교차 풀이 프롬프트의 개인정보 마스킹이 불확실하다")

        result = await self._gateway.complete(
            LLMRequest(
                role=ModelRole.VERIFIER,
                prompt=redacted.masked_text,
                prompt_id=self._prompt.prompt_id,
                prompt_version=self._prompt.version,
                generation_params=GenerationParams(temperature=0.0),
                response_schema_name=self._prompt.response_schema_name,
            ),
            execution_context,
        )
        return parse(require_successful_text(result), SolveResult)


__all__ = ["BlindCrossSolver"]
