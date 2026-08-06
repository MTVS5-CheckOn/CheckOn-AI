"""문항 생성 호출 — 도메인 규칙과 LLM 게이트웨이를 잇는다."""

from __future__ import annotations

from uuid import UUID

from ai.contracts.execution import ExecutionContext
from ai.contracts.graphrag import ContextPack
from ai.contracts.llm import (
    CallOutcome,
    FieldMissing,
    LLMRequest,
    LLMResult,
    LlmTimeout,
    LlmUnavailable,
    ModelRole,
    ParseFailed,
    RedactionBlocked,
)
from ai.contracts.problem_generation import (
    DifficultyBand,
    GeneratedItem,
    ProblemRequest,
    SolveResult,
)
from ai.contracts.taxonomy import TypeTag
from ai.llm.determinism import deterministic_params
from ai.llm.gateway import LlmGateway
from ai.llm.prompts.loader import LoadedPromptTemplate, load_prompt_template
from ai.llm.structured import parse
from ai.problem_generation.domain.identity import canonical_json, sha256_hex
from ai.problem_generation.domain.models import CandidateSnapshot, RetryContext
from ai.runtime.redaction import redact

_ITEM_PROMPT_ID = "pg.items.v1"


class ProblemGenerator:
    """버전 프롬프트·redaction·구조화 파서를 거치는 생성기."""

    def __init__(
        self,
        gateway: LlmGateway,
        prompt: LoadedPromptTemplate | None = None,
    ) -> None:
        self._gateway = gateway
        self._prompt = prompt or load_prompt_template(_ITEM_PROMPT_ID)
        if self._prompt.role is not ModelRole.GENERATOR:
            raise ValueError("문항 생성 프롬프트 role은 generator여야 한다")
        if self._prompt.response_schema_name != GeneratedItem.__name__:
            raise ValueError("문항 생성 프롬프트 응답 스키마가 GeneratedItem이 아니다")

    @property
    def prompt_version(self) -> str:
        return self._prompt.version

    async def generate(
        self,
        *,
        request: ProblemRequest,
        type_tag: TypeTag,
        skill_node_id: str,
        context_pack: ContextPack,
        retry_context: RetryContext,
        execution_context: ExecutionContext,
    ) -> GeneratedItem:
        generation_input = {
            "area_tag": request.area_tag.value,
            "type_tag": type_tag.value,
            "item_format": request.item_format.value,
            "skill_node_id": skill_node_id,
            "requested_difficulty": (
                request.requested_difficulty.value
                if request.requested_difficulty is not None
                else None
            ),
            "topic_hint": request.topic_hint,
        }
        prompt_text = self._prompt.render(
            {
                "context_pack_json": canonical_json(
                    context_pack.model_dump(mode="json")
                ),
                "generation_input_json": canonical_json(generation_input),
                "retry_context_json": canonical_json(
                    retry_context.model_dump(mode="json")
                ),
            }
        )
        redacted = redact(prompt_text)
        if redacted.uncertain:
            raise RedactionBlocked("문항 생성 프롬프트의 개인정보 마스킹이 불확실하다")

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
        return parse(require_successful_text(result), GeneratedItem)


def build_candidate_snapshot(
    *,
    set_id: UUID,
    slot_index: int,
    attempt_no: int,
    item: GeneratedItem,
    solve_result: SolveResult,
    context_pack_id: UUID,
    difficulty_est: float,
    difficulty_band: DifficultyBand,
) -> CandidateSnapshot:
    body = {
        "set_id": str(set_id),
        "slot_index": slot_index,
        "attempt_no": attempt_no,
        "item": item.model_dump(mode="json"),
        "solve_result": solve_result.model_dump(mode="json"),
        "context_pack_id": str(context_pack_id),
        "difficulty_est": difficulty_est,
        "difficulty_band": difficulty_band.value,
    }
    return CandidateSnapshot(
        set_id=set_id,
        slot_index=slot_index,
        attempt_no=attempt_no,
        item=item,
        solve_result=solve_result,
        context_pack_id=context_pack_id,
        difficulty_est=difficulty_est,
        difficulty_band=difficulty_band,
        snapshot_hash=sha256_hex(canonical_json(body)),
    )


def require_successful_text(result: LLMResult) -> str:
    if result.outcome is CallOutcome.TIMEOUT:
        raise LlmTimeout("LLM provider가 timeout 결과를 반환했다")
    if result.outcome is CallOutcome.PROVIDER_ERROR:
        raise LlmUnavailable("LLM provider가 오류 결과를 반환했다")
    if result.outcome is CallOutcome.REDACTION_BLOCKED:
        raise RedactionBlocked("LLM provider 호출이 redaction 단계에서 차단됐다")
    if result.outcome is CallOutcome.FIELD_MISSING:
        raise FieldMissing("LLM provider가 필수 필드 누락 결과를 반환했다")
    if result.outcome in {CallOutcome.PARSE_FAIL, CallOutcome.BAD_REF}:
        raise ParseFailed(f"LLM provider 결과를 사용할 수 없다: {result.outcome.value}")
    if result.outcome is not CallOutcome.OK or result.text is None:
        raise ParseFailed("LLM provider의 성공 본문이 없다")
    return result.text
