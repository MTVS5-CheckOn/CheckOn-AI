"""T2 지문 생성 호출과 ContextPack 파생 결합."""

from __future__ import annotations

from uuid import uuid5

from ai.contracts.execution import ExecutionContext
from ai.contracts.graphrag import ContextPack
from ai.contracts.llm import LLMRequest, ModelRole, ParseFailed, RedactionBlocked
from ai.contracts.problem_generation import PassageDraft, PassageRequest
from ai.llm.determinism import deterministic_params
from ai.llm.gateway import LlmGateway
from ai.llm.prompts.loader import LoadedPromptTemplate, load_prompt_template
from ai.llm.structured import parse
from ai.problem_generation.application.generator import require_successful_text
from ai.problem_generation.domain.identity import canonical_json, sha256_hex
from ai.problem_generation.domain.policy import BannedTopicsConfig
from ai.runtime.redaction import redact

_PASSAGE_PROMPT_ID = "pg.passage.v1"
_GENERATION_UNAVAILABLE_OUTPUTS = frozenset(
    {"generation_unavailable", '"generation_unavailable"'}
)


class PassageConfigurationMismatch(ValueError):
    """요청과 주입된 지문 생성 설정의 버전이 다른 경우."""


class PassageDraftRejected(ParseFailed):
    """구조는 맞지만 요청·승인 근거 계약을 위반한 지문 초안."""


class PassageGenerationUnavailable(PassageDraftRejected):
    """모델이 승인 근거로 지문을 만들 수 없다고 명시한 경우."""


class PassageGenerator:
    """버전 프롬프트·redaction·구조화 파서를 거치는 T2 지문 생성기."""

    def __init__(
        self,
        gateway: LlmGateway,
        prompt: LoadedPromptTemplate | None = None,
        *,
        banned_topics: BannedTopicsConfig,
    ) -> None:
        self._gateway = gateway
        self._prompt = prompt or load_prompt_template(_PASSAGE_PROMPT_ID)
        self._banned_topics = banned_topics
        if self._prompt.role is not ModelRole.GENERATOR:
            raise ValueError("지문 생성 프롬프트 role은 generator여야 한다")
        if self._prompt.response_schema_name != PassageDraft.__name__:
            raise ValueError("지문 생성 프롬프트 응답 스키마가 PassageDraft가 아니다")

    @property
    def prompt_version(self) -> str:
        return self._prompt.version

    @property
    def banned_topics_version(self) -> str:
        return self._banned_topics.version

    async def generate(
        self,
        *,
        passage_request: PassageRequest,
        context_pack: ContextPack,
        execution_context: ExecutionContext,
    ) -> PassageDraft:
        if passage_request.banned_topics_version != self._banned_topics.version:
            raise PassageConfigurationMismatch(
                "PassageRequest의 banned_topics_version이 로드 설정과 다르다"
            )

        prompt_text = self._prompt.render(
            {
                "context_pack_json": canonical_json(
                    context_pack.model_dump(mode="json")
                ),
                "passage_request_json": canonical_json(
                    passage_request.model_dump(mode="json")
                ),
                "banned_topics_json": canonical_json(
                    _banned_topics_prompt_payload(self._banned_topics)
                ),
            }
        )
        redacted = redact(prompt_text)
        if redacted.uncertain:
            raise RedactionBlocked("지문 생성 프롬프트의 개인정보 마스킹이 불확실하다")

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
        response_text = require_successful_text(result).strip()
        if response_text in _GENERATION_UNAVAILABLE_OUTPUTS:
            raise PassageGenerationUnavailable(
                "승인 근거만으로 지문을 생성할 수 없다"
            )

        draft = parse(response_text, PassageDraft)
        self._validate_draft(draft, passage_request, context_pack)
        return draft

    def _validate_draft(
        self,
        draft: PassageDraft,
        request: PassageRequest,
        context_pack: ContextPack,
    ) -> None:
        if draft.paragraph_count != request.paragraph_count:
            raise PassageDraftRejected(
                "PassageDraft.paragraph_count가 PassageRequest와 다르다"
            )
        _require_approved_evidence(draft, context_pack)
        normalized_text = draft.passage_text.casefold()
        if any(term.casefold() in normalized_text for term in self._banned_topics.all_terms):
            raise PassageDraftRejected("PassageDraft 본문에 금칙 소재가 포함됐다")


def attach_passage_draft(
    context_pack: ContextPack,
    draft: PassageDraft,
) -> ContextPack:
    """원 ContextPack 해시와 초안을 묶어 새 결정론 ContextPack을 만든다."""

    existing_raw = context_pack.retrieval_trace.get("passage_draft")
    if existing_raw is not None:
        try:
            existing = PassageDraft.model_validate(existing_raw)
        except ValueError as error:
            raise PassageDraftRejected(
                "ContextPack의 기존 passage_draft가 유효하지 않다"
            ) from error
        if existing == draft:
            return context_pack
        raise PassageDraftRejected("ContextPack의 passage_draft를 바꿀 수 없다")
    _require_approved_evidence(draft, context_pack)
    draft_payload = draft.model_dump(mode="json")
    draft_hash = sha256_hex(canonical_json(draft_payload))
    derived_id = uuid5(
        context_pack.context_pack_id,
        f"passage-draft:{context_pack.context_pack_hash}:{draft_hash}",
    )
    payload = context_pack.model_dump(mode="json")
    payload["retrieval_trace"] = {
        **context_pack.retrieval_trace,
        "passage_draft": draft_payload,
    }
    payload["context_pack_id"] = str(derived_id)
    payload.pop("context_pack_hash")
    payload["context_pack_hash"] = sha256_hex(canonical_json(payload))
    return ContextPack.model_validate(payload)


def _require_approved_evidence(
    draft: PassageDraft,
    context_pack: ContextPack,
) -> None:
    raw_refs = context_pack.retrieval_trace.get("allowed_evidence_refs")
    if not isinstance(raw_refs, list) or any(
        not isinstance(ref, str) or not ref for ref in raw_refs
    ):
        raise PassageDraftRejected("ContextPack의 승인 근거 참조가 유효하지 않다")
    unknown_refs = set(draft.evidence_anchor_ids) - set(raw_refs)
    if unknown_refs:
        raise PassageDraftRejected(
            "PassageDraft가 승인되지 않은 근거를 참조한다: "
            + ", ".join(sorted(unknown_refs))
        )


def _banned_topics_prompt_payload(
    config: BannedTopicsConfig,
) -> dict[str, object]:
    """redaction 오탐을 일으키는 설명문을 빼고 실행 금칙값만 직렬화한다."""

    return {
        "version": config.version,
        "categories": [
            {
                "id": category.id,
                "match_terms": list(category.match_terms),
            }
            for category in config.categories
        ],
        "prompt_injection_patterns": list(config.prompt_injection_patterns),
    }


__all__ = [
    "PassageConfigurationMismatch",
    "PassageDraftRejected",
    "PassageGenerationUnavailable",
    "PassageGenerator",
    "attach_passage_draft",
]
