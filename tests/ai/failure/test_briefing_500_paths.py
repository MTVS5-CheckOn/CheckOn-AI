"""4-2·4-3 재현 — 결정론 판정이 멀쩡한데 브리핑이 응답 전체를 500으로 떨어뜨린다.

두 경로 모두 **정상 경로**다(예외 상황이 아니다).

**4-2 FOLLOW_UP KeyError** — `_LIFECYCLE_KO`에 `NEW`·`ONGOING`만 있고 `render_evidence_block`이
직접 인덱싱한다. `Lifecycle.FOLLOW_UP`(해소 후 2주 내 재발 — 정상 판정) 신호 1건이면
KeyError. 이 호출은 `make_brief`의 폴백 경계 **밖**이라 템플릿 폴백도 못 받는다.

**4-3 plain LlmError 폴백 누락** — `make_brief`의 catch가
`(LlmUnavailable, LlmTimeout, ParseFailed)`뿐인데 `openai_compat.py:172`는 4xx를 **plain
`LlmError`** 로 올린다. 컨텍스트 한도 초과 400 한 번에 판정이 멀쩡한 채 500.
🔴 `RedactionBlocked`도 `LlmError` 서브클래스이므로(`test_llm.py:134`) catch를 넓히면
"재시도 금지+알럿" 의미가 빨려 들어간다 — except 절 순서가 곧 계약이다.
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from uuid import UUID

import pytest

from ai.composition.briefing import make_brief
from ai.composition.briefing_context import (
    BriefingContext,
    EvidenceFact,
    render_evidence_block,
)
from ai.contracts.detection import DISPLAY_LABELS, Brief, Lifecycle, SignalType
from ai.contracts.execution import Capability, ExecutionContext, VersionSet
from ai.contracts.llm import (
    CallOutcome,
    LlmError,
    LLMProvider,
    LLMRequest,
    LLMResult,
    RedactionBlocked,
    TokenUsage,
)
from ai.detection.segments import Segment

_FALLBACK = "정답률이 평소보다 눈에 띄게 떨어진 상태가 이어지고 있어요."


def _run[T](coro: Coroutine[object, object, T]) -> T:
    return asyncio.run(coro)


def _context() -> ExecutionContext:
    return ExecutionContext(
        execution_id=UUID("00000000-0000-4000-8000-0000000000b1"),
        tenant_id="t1",
        capability=Capability.COMPOSITION,
        input_snapshot_hash="h",
        versions=VersionSet(
            pipeline_version="0.1",
            engine_version="0.1",
            schema_version="0.1",
            contract_version="0.1",
            prompt_version="0.2",
        ),
    )


def _ctx(*, lifecycle: Lifecycle = Lifecycle.NEW) -> BriefingContext:
    return BriefingContext(
        signal_type=SignalType.ACC_DROP,
        display_label=DISPLAY_LABELS[SignalType.ACC_DROP],
        lifecycle=lifecycle,
        segment=Segment.NORMAL,
        facts=(EvidenceFact("이번 주 정답률", "62%"),),
        evidence_summaries=("근거 기록",),
        fallback_text=_FALLBACK,
    )


class _RaisingProvider:
    """지정한 예외를 올리는 provider — 호출 횟수를 센다(재시도 금지 확인)."""

    def __init__(self, error: Exception) -> None:
        self._error = error
        self.calls = 0

    @property
    def name(self) -> str:
        return "raising"

    async def complete(self, request: LLMRequest, context: ExecutionContext) -> LLMResult:
        self.calls += 1
        raise self._error


class _OkProvider:
    def __init__(self, text: str) -> None:
        self._text = text
        self.calls = 0

    @property
    def name(self) -> str:
        return "ok"

    async def complete(self, request: LLMRequest, context: ExecutionContext) -> LLMResult:
        self.calls += 1
        return LLMResult(
            outcome=CallOutcome.OK,
            text=self._text,
            provider=self.name,
            model="mock",
            usage=TokenUsage(tokens_in=0, tokens_out=0, cost_usd=0.0),
            latency_ms=0,
        )


def _make(ctx: BriefingContext, provider: LLMProvider) -> tuple[Brief, str]:
    return _run(
        make_brief(ctx, provider, context=_context(), now=lambda: 0.0, deadline=45.0)
    )


# ── 4-2: FOLLOW_UP lifecycle ──────────────────────────────────────


def test_follow_up_renders_evidence_block() -> None:
    """해소 후 재발(FOLLOW_UP) 신호가 근거 블록 렌더를 통과한다 — 현재는 KeyError."""
    block = render_evidence_block(_ctx(lifecycle=Lifecycle.FOLLOW_UP))
    assert "지속성:" in block


def test_follow_up_brief_path_does_not_raise() -> None:
    """FOLLOW_UP 신호 1건이 브리핑 경로 전체를 죽이지 않는다(폴백 밖 호출)."""
    brief, _outcome = _make(_ctx(lifecycle=Lifecycle.FOLLOW_UP), _OkProvider("한 문장이에요"))
    assert brief.text


def test_lifecycle_ko_covers_every_lifecycle() -> None:
    """enum이 자랄 때 런타임 KeyError가 아니라 **CI에서** 잡힌다.

    `.get` 침묵 폴백보다 값대조가 낫다 — 문구 누락은 조용히 넘길 일이 아니다.
    """
    from ai.composition.briefing_context import _LIFECYCLE_KO

    assert set(_LIFECYCLE_KO) == set(Lifecycle)


# ── 4-3: plain LlmError 폴백 ──────────────────────────────────────


def test_plain_llm_error_falls_back_to_template() -> None:
    """4xx(컨텍스트 한도 초과 등)는 plain LlmError다 — 템플릿 폴백으로 수렴해야 한다."""
    provider = _RaisingProvider(LlmError("400 context length exceeded"))
    brief, outcome = _make(_ctx(), provider)

    assert brief.fallback_used is True
    assert brief.text == _FALLBACK
    assert outcome == "llm_failed"
    assert provider.calls == 1  # 재시도 없이 즉시 폴백(분기표)


def test_redaction_blocked_does_not_leak_into_llm_failed() -> None:
    """🔴 `RedactionBlocked`는 `LlmError` 서브클래스다 — 넓힌 catch에 빨려들면 안 된다.

    "재시도 금지+알럿"(error_codes §3)이 `llm_failed`로 뭉개지면 알럿 신호를 잃는다.
    """
    provider = _RaisingProvider(RedactionBlocked("마스킹 불확실"))
    brief, outcome = _make(_ctx(), provider)

    assert brief.fallback_used is True
    assert outcome == "redaction_blocked", "RedactionBlocked가 llm_failed로 뭉개졌다"
    assert provider.calls == 1


def test_redaction_blocked_is_llm_error_subclass() -> None:
    """전제 고정 — 계보가 바뀌면 위 분기 순서의 근거가 사라진다."""
    assert issubclass(RedactionBlocked, LlmError)


@pytest.mark.parametrize("error_message", ["400 bad request", "413 payload too large"])
def test_other_4xx_shapes_also_fall_back(error_message: str) -> None:
    """4xx 계열 전반 — 특정 메시지에만 걸리는 폴백이 아니다."""
    brief, outcome = _make(_ctx(), _RaisingProvider(LlmError(error_message)))
    assert brief.fallback_used is True
    assert outcome == "llm_failed"
