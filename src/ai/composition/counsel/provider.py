"""counsel_pack의 LLM 접점 — plan·generate_draft Protocol + 결정론 Fake.

`probe/`의 `tools.py`·`planner.py` 자리에 해당한다(counsel_pack은 ReAct가 아니라 순차
그래프라 도구 호출이 없다). 실제 LLM 호출은 **전부 `llm/gateway` 경유**이며(03 §2),
이 모듈은 Protocol과 CI 기본값인 Fake만 둔다.

**redaction 경계:** gateway로 나가는 프롬프트는 전송 직전 `runtime/redaction.redact`를
거치고, `uncertain`이면 **전송하지 않는다**(fail-closed · 불변식 3). 이 규율은
`tests/ai/contract/test_composition_redaction.py`가 AST로 고정한다.

**불변식 1:** plan 노드는 확정 수치 안에서 강조점만 고른다 — 새 사실·새 수치를 만들지
않는다. 산출 강조점에는 근거 `record_id`가 동반돼야 한다(state 불변식 ①이 재검증).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Protocol, runtime_checkable

from ai.composition.counsel.prompt import (
    PROMPT_ID,
    PROMPT_VERSION,
    assemble_prompt,
    tone_rule_for,
)
from ai.contracts.composition import DraftContext
from ai.contracts.execution import ExecutionContext, GenerationParams
from ai.contracts.llm import (
    CallOutcome,
    LlmError,
    LLMRequest,
    LLMResult,
    ModelRole,
    TokenUsage,
)
from ai.llm.gateway import LlmGateway
from ai.runtime.redaction import redact

#: 초안 블록은 문단 단위라 브리핑(한 줄)보다 길다. 문장당 상한 × 문장 수로 산출한다 —
#: 값을 게이트에 박지 않고 tone_map의 sentences_per_block에서 파생시킨다(03 §1).
CHARS_PER_SENTENCE = 120


def max_chars_for(context: DraftContext) -> int:
    """이 조합의 블록 길이 상한 — tone_map의 문장 수에서 파생."""
    return tone_rule_for(context).sentences_per_block * CHARS_PER_SENTENCE


class RedactionBlockedError(LlmError):
    """마스킹 불확실 — 전송하지 않았다(fail-closed · 불변식 3)."""


@runtime_checkable
class CounselPlanner(Protocol):
    """plan 노드 — 학생별 강조점을 고른다(LLM 1회). 새 사실 생성 금지."""

    async def plan(
        self,
        *,
        contexts: Mapping[str, DraftContext],
        student_refs: Sequence[str],
        execution_context: ExecutionContext,
    ) -> dict[str, list[str]]:
        """student_ref → 강조점 목록(각 항목에 근거 `record_id` 동반)."""
        ...


@runtime_checkable
class DraftWriter(Protocol):
    """generate_draft 노드 — 블록 본문을 만든다."""

    async def write(
        self,
        *,
        context: DraftContext,
        execution_context: ExecutionContext,
    ) -> str:
        """조립·마스킹을 마친 프롬프트로 초안 본문을 받는다."""
        ...


class GatewayDraftWriter:
    """실 경로 — `llm/gateway`를 role=counselor로 호출한다.

    프롬프트 조립 → **redaction(fail-closed)** → gateway 순서를 지킨다.
    재시도를 여기서 돌리지 않는다 — 게이트 실패 재생성은 그래프가 ≤3으로 관리하고,
    전송 재시도는 조립부가 `transport_retry`로 주입한다(브리핑 narrator와 동일하게 0회).
    """

    def __init__(self, gateway: LlmGateway) -> None:
        self._gateway = gateway

    async def write(
        self,
        *,
        context: DraftContext,
        execution_context: ExecutionContext,
    ) -> str:
        redacted = redact(assemble_prompt(context))
        if redacted.uncertain:  # fail-closed — 불확실하면 LLM에 보내지 않는다
            raise RedactionBlockedError("상담 초안 프롬프트의 마스킹이 불확실하다")
        result = await self._gateway.complete(
            LLMRequest(
                role=ModelRole.COUNSELOR,
                prompt=redacted.masked_text,
                prompt_id=PROMPT_ID,
                prompt_version=PROMPT_VERSION,
                generation_params=GenerationParams(temperature=0.0),
            ),
            execution_context,
        )
        # outcome≠OK를 빈 문자열로 삼키면 장애가 게이트 실패(`gate_exhausted:empty`)로
        # **오분류**된다 — 그러면 서킷 카운터도 안 오르고 알럿이 뜨지 않는다.
        # LlmError로 승격해 `llm_failed` 경로(서킷 포함)로 태운다(error_codes §3).
        if result.outcome is not CallOutcome.OK:
            raise LlmError(f"counselor 호출 실패 outcome={result.outcome.value}")
        text = (result.text or "").strip()
        if not text:
            raise LlmError("counselor 응답이 비었다")
        return text


class FakeCounselProvider:
    """결정론 Fake — 시나리오 주입식. CI·테스트 기본값.

    LLM을 호출하지 않고 주입된 시나리오를 순서대로 소비한다. 시계·난수를 쓰지 않는다.
    """

    def __init__(
        self,
        *,
        drafts: Sequence[str | Exception] = (),
        emphasis: Mapping[str, list[str]] | None = None,
    ) -> None:
        self._drafts = tuple(drafts)
        self._emphasis = dict(emphasis or {})
        self.write_calls: list[str] = []
        self.plan_calls: list[tuple[str, ...]] = []

    async def plan(
        self,
        *,
        contexts: Mapping[str, DraftContext],
        student_refs: Sequence[str],
        execution_context: ExecutionContext,
    ) -> dict[str, list[str]]:
        self.plan_calls.append(tuple(student_refs))
        if self._emphasis:
            return {ref: list(self._emphasis.get(ref, [])) for ref in student_refs}
        # 기본 시나리오 — 컨텍스트가 실제로 제공한 근거로만 강조점을 만든다(새 사실 없음).
        planned: dict[str, list[str]] = {}
        for ref in student_refs:
            context = contexts.get(ref)
            if context is None or not context.facts:
                continue
            fact = context.facts[0]
            planned[ref] = [f"{fact.label} {fact.value} (record_id=le_{ref})"]
        return planned

    async def write(
        self,
        *,
        context: DraftContext,
        execution_context: ExecutionContext,
    ) -> str:
        index = len(self.write_calls)
        self.write_calls.append(context.student_ref)
        if index < len(self._drafts):
            step = self._drafts[index]
        elif self._drafts:
            step = self._drafts[-1]
        else:
            step = context.fallback_text
        if isinstance(step, Exception):
            raise step
        return step


class FakeCounselLlmProvider:
    """`LLMProvider` 대역 — gateway 경로의 CI 기본값(실 벤더 호출 없음).

    `composition/provider.FakeBriefProvider`와 같은 자리다. 결정론이며 시계·난수를 쓰지 않는다.
    """

    def __init__(self, text: str = "이번 주 학습 상황을 정리해 드립니다.") -> None:
        self._text = text

    @property
    def name(self) -> str:
        return "fake-counsel"

    async def complete(
        self, request: LLMRequest, context: ExecutionContext
    ) -> LLMResult:
        return LLMResult(
            outcome=CallOutcome.OK,
            text=self._text,
            provider=self.name,
            model="template",
            usage=TokenUsage(tokens_in=0, tokens_out=0, cost_usd=0.0),
            latency_ms=0,
        )


__all__ = [
    "CHARS_PER_SENTENCE",
    "CounselPlanner",
    "DraftWriter",
    "FakeCounselLlmProvider",
    "FakeCounselProvider",
    "GatewayDraftWriter",
    "RedactionBlockedError",
    "max_chars_for",
]
