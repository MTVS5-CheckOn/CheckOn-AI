"""정답·해설·근거를 제거한 독립 교차 풀이 호출."""

from __future__ import annotations

from ai.contracts.execution import ExecutionContext
from ai.contracts.llm import LLMRequest, ModelRole, RedactionBlocked
from ai.contracts.problem_generation import (
    GeneratedItem,
    MisconceptionCheckResult,
    SolveResult,
)
from ai.llm.determinism import deterministic_params
from ai.llm.gateway import LlmGateway
from ai.llm.prompts.loader import LoadedPromptTemplate, load_prompt_template
from ai.llm.structured import parse
from ai.problem_generation.application.generator import require_successful_text
from ai.problem_generation.domain.cross_solve import validate_misconception_structure
from ai.problem_generation.domain.identity import canonical_json
from ai.problem_generation.domain.policy import MisconceptionTagsConfig
from ai.runtime.redaction import redact

_CROSS_SOLVE_PROMPT_ID = "pg.cross_solve.v1"
_MISCONCEPTION_CHECK_PROMPT_ID = "pg.misconception_check.v1"


class BlindCrossSolver:
    """generator의 정답 정보를 전달하지 않는 verifier 경계."""

    def __init__(
        self,
        gateway: LlmGateway,
        *,
        misconception_tags: MisconceptionTagsConfig,
        prompt: LoadedPromptTemplate | None = None,
        misconception_prompt: LoadedPromptTemplate | None = None,
    ) -> None:
        self._gateway = gateway
        self._misconception_tags = misconception_tags
        self._prompt = prompt or load_prompt_template(_CROSS_SOLVE_PROMPT_ID)
        self._misconception_prompt = misconception_prompt or load_prompt_template(
            _MISCONCEPTION_CHECK_PROMPT_ID
        )
        if self._prompt.role is not ModelRole.VERIFIER:
            raise ValueError("교차 풀이 프롬프트 role은 verifier여야 한다")
        if self._prompt.response_schema_name != SolveResult.__name__:
            raise ValueError("교차 풀이 프롬프트 응답 스키마가 SolveResult가 아니다")
        if self._misconception_prompt.role is not ModelRole.VERIFIER:
            raise ValueError("오개념 의미 검증 프롬프트 role은 verifier여야 한다")
        if (
            self._misconception_prompt.response_schema_name
            != MisconceptionCheckResult.__name__
        ):
            raise ValueError(
                "오개념 의미 검증 프롬프트 응답 스키마가 "
                "MisconceptionCheckResult가 아니다"
            )

    @property
    def prompt_version(self) -> str:
        return self._prompt.version

    @property
    def misconception_prompt_version(self) -> str:
        return self._misconception_prompt.version

    def misconception_structure_failures(
        self,
        item: GeneratedItem,
    ) -> tuple[str, ...]:
        """LLM 호출 전에 오개념 필드의 구조 위반을 반환한다."""

        return validate_misconception_structure(item, self._misconception_tags)

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
                generation_params=deterministic_params(),
                response_schema_name=self._prompt.response_schema_name,
            ),
            execution_context,
        )
        return parse(require_successful_text(result), SolveResult)

    async def check_misconceptions(
        self,
        *,
        item: GeneratedItem,
        execution_context: ExecutionContext,
    ) -> MisconceptionCheckResult:
        """정답을 제외한 선지의 오답 사유와 닫힌 어휘 의미를 검증한다."""

        vocabulary = tuple(
            {
                "id": tag.id,
                "label_ko": tag.label_ko,
                "description": tag.description,
            }
            for tag in self._misconception_tags.tags_for(item.area_tag)
        )
        wrong_choices = tuple(
            {
                "choice_no": choice.no,
                "text": choice.text,
                "why_wrong": choice.why_wrong,
                "misconception_tag": choice.misconception_tag,
            }
            for choice in sorted(item.choices, key=lambda choice: choice.no)
            if choice.no != item.answer.correct_no
        )
        prompt_text = self._misconception_prompt.render(
            {
                "misconception_payload_json": canonical_json(
                    {
                        "area_tag": item.area_tag.value,
                        "vocabulary": vocabulary,
                        "wrong_choices": wrong_choices,
                    }
                )
            }
        )
        redacted = redact(prompt_text)
        if redacted.uncertain:
            raise RedactionBlocked("오개념 의미 검증 프롬프트의 개인정보 마스킹이 불확실하다")

        result = await self._gateway.complete(
            LLMRequest(
                role=ModelRole.VERIFIER,
                prompt=redacted.masked_text,
                prompt_id=self._misconception_prompt.prompt_id,
                prompt_version=self._misconception_prompt.version,
                generation_params=deterministic_params(),
                response_schema_name=self._misconception_prompt.response_schema_name,
            ),
            execution_context,
        )
        return parse(require_successful_text(result), MisconceptionCheckResult)


__all__ = ["BlindCrossSolver"]
