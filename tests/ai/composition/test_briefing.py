"""브리핑 문장화 분기표 — LLM 실패·게이트 소진·마스킹·예산·판정 무변 (09 §3 · 분기표 8종).

v2: 입력은 엔진 초안이 아니라 구조화 근거 패키지(BriefingContext)다. grounding은 초안이
아니라 컨텍스트가 제공한 수치 집합이며, 폴백은 여전히 엔진 결정론 템플릿(fallback_text)이다.
FakeProvider·mock으로 결정론화(pytest-asyncio 없이 asyncio.run). 실 LLM 경로는
integration 스모크(test_briefing_smoke)로 분리.
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from uuid import UUID

import pytest

from ai.composition.briefing import MAX_REGEN, make_brief
from ai.composition.briefing_context import BriefingContext, EvidenceFact
from ai.composition.provider import (
    BriefingSettings,
    FakeBriefProvider,
    build_brief_provider,
)
from ai.contracts.detection import DISPLAY_LABELS, Brief, Lifecycle, SignalType
from ai.contracts.execution import Capability, ExecutionContext, VersionSet
from ai.contracts.llm import (
    CallOutcome,
    LLMProvider,
    LLMRequest,
    LLMResult,
    LlmTimeout,
    LlmUnavailable,
    ParseFailed,
    TokenUsage,
)
from ai.detection.brief import build_brief
from ai.detection.segments import Segment

_FALLBACK = "정답률이 평소보다 눈에 띄게 떨어진 상태가 이어지고 있어요."


def _run[T](coro: Coroutine[object, object, T]) -> T:
    return asyncio.run(coro)


def _context() -> ExecutionContext:
    return ExecutionContext(
        execution_id=UUID("00000000-0000-4000-8000-000000000011"),
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


def _ctx(
    *,
    signal_type: SignalType = SignalType.ACC_DROP,
    lifecycle: Lifecycle = Lifecycle.NEW,
    facts: tuple[EvidenceFact, ...] = (),
    fallback: str = _FALLBACK,
) -> BriefingContext:
    """단위 테스트용 근거 패키지 — grounding 수치·폴백·signal_type을 직접 지정."""
    return BriefingContext(
        signal_type=signal_type,
        display_label=DISPLAY_LABELS[signal_type],
        lifecycle=lifecycle,
        segment=Segment.NORMAL,
        facts=facts,
        evidence_summaries=("근거 기록",),
        fallback_text=fallback,
    )


class _MockProvider:
    """호출 시 예외 또는 고정 텍스트 반환. 호출 횟수를 센다(재시도 검증)."""

    def __init__(self, *, text: str | None = None, error: Exception | None = None) -> None:
        self._text = text
        self._error = error
        self.calls = 0

    @property
    def name(self) -> str:
        return "mock"

    async def complete(self, request: LLMRequest, context: ExecutionContext) -> LLMResult:
        self.calls += 1
        if self._error is not None:
            raise self._error
        return LLMResult(
            outcome=CallOutcome.OK,
            text=self._text,
            provider=self.name,
            model="mock",
            usage=TokenUsage(tokens_in=0, tokens_out=0, cost_usd=0.0),
            latency_ms=0,
        )


def _make(
    ctx: BriefingContext, provider: LLMProvider, *, now: float = 0.0, deadline: float = 45.0
) -> tuple[Brief, str]:
    return _run(
        make_brief(ctx, provider, context=_context(), now=lambda: now, deadline=deadline)
    )


# ── 분기표 #7 provider 선택 ────────────────────────────────


def test_default_provider_is_fake() -> None:
    """🔴 **선언 기본값**을 본다 — 환경이 아니라.

    종전에는 `BriefingSettings().llm_provider`를 봤는데 그건 `.env`·프로세스 env를 **읽는**
    값이라, 개발자 `.env`의 `LLM_PROVIDER` 하나로 **상시 red**였다(99 #57). 그리고
    `llm_provider_pin`을 깐 지금은 반대 방향으로 무의미해진다 — **핀 때문에** 통과하므로
    선언 기본값이 `openai_compat`로 바뀌어도 초록이다.

    ⇒ `model_fields[...].default`를 직접 본다. 환경을 안 읽으므로 **핀에 면역**이고,
    이 테스트가 지키려던 *"기본값은 fake다"* 를 실제로 지킨다(99 #38이 `store_backend_pin`
    에서 같은 이유로 택한 형태).
    """
    default = BriefingSettings.model_fields["llm_provider"].default
    assert default == "fake"
    provider = build_brief_provider(BriefingSettings(llm_provider=default))
    assert isinstance(provider, FakeBriefProvider)


def test_openai_compat_wired() -> None:
    """openai_compat 선택 시 실 어댑터를 반환한다(#15 develop 머지). 생성만 — 접속 없음(lazy)."""
    provider = build_brief_provider(BriefingSettings(llm_provider="openai_compat"))
    assert isinstance(provider, LLMProvider)
    assert provider.name == "openai-compat"


# ── 분기표 #8 fake = 신호 유형 기본 템플릿, fallback_used=False ──


def test_fake_returns_template_brief() -> None:
    """v2 fake: 프롬프트의 '신호 유형' 라벨로 엔진 기본 템플릿을 반환(초안 echo 아님)."""
    ctx = _ctx(
        signal_type=SignalType.TYPE_BIAS,
        facts=(EvidenceFact("해당 유형 오답", "12문항 중 8문항"),),
    )
    brief, outcome = _make(ctx, FakeBriefProvider())
    assert brief.text == build_brief(SignalType.TYPE_BIAS).text  # 기본 템플릿(detail 없음)
    assert brief.gate_passed is True
    assert brief.fallback_used is False
    assert outcome == "ok"


# ── 분기표 #1 LLM 실패 → 재시도 없이 즉시 폴백 ─────────────


@pytest.mark.parametrize(
    "error",
    [LlmUnavailable("down"), LlmTimeout("slow"), ParseFailed("bad")],
)
def test_llm_failure_falls_back_without_retry(error: Exception) -> None:
    ctx = _ctx()
    provider = _MockProvider(error=error)
    brief, outcome = _make(ctx, provider)
    assert brief.fallback_used is True
    assert brief.text == ctx.fallback_text  # 엔진 결정론 템플릿 재사용
    assert outcome == "llm_failed"
    assert provider.calls == 1  # 재시도 없음(게이트웨이 후속)


# ── 분기표 #3 게이트 실패 → 재생성 ≤3 → 소진 폴백 ─────────


def test_gate_exhaustion_after_max_regen() -> None:
    ctx = _ctx()  # facts 없음 → allowed 비어 근거 밖 숫자 전부 차단
    provider = _MockProvider(text="풀이 시간이 21일째 늘고 있어요")  # 근거에 없는 숫자 21
    brief, outcome = _make(ctx, provider)
    assert brief.fallback_used is True
    assert brief.gate_passed is False
    assert outcome.startswith("gate_exhausted")
    assert provider.calls == MAX_REGEN  # 정확히 3회 재생성


# ── 분기표 #4 ⟪⟫ 토큰 잔존 → 게이트 실패 경로 ─────────────


def test_token_leak_triggers_gate_failure() -> None:
    ctx = _ctx()
    provider = _MockProvider(text="⟪이름1⟫ 학생 정답률이 떨어져요")
    brief, outcome = _make(ctx, provider)
    assert brief.fallback_used is True
    assert "token_leak" in outcome
    assert provider.calls == MAX_REGEN


# ── 기호 게이트(v2 신규) — LaTeX·마크다운 잔존 → 실패 ──────


def test_symbol_triggers_gate_failure() -> None:
    """v1 프리뷰에서 관찰된 $\\times$ 류 LaTeX 유출을 게이트가 차단한다(재생성 소진→폴백)."""
    ctx = _ctx(
        signal_type=SignalType.TYPE_BIAS,
        facts=(EvidenceFact("해당 유형 오답", "12문항 중 8문항"),),
    )
    provider = _MockProvider(text="literature $\\times$ infer 유형 오답이 몰려요")
    brief, outcome = _make(ctx, provider)
    assert brief.fallback_used is True
    assert "symbol" in outcome
    assert provider.calls == MAX_REGEN


def test_multiplication_sign_allowed() -> None:
    """곱하기 기호 ×(U+00D7)는 정상 문자 — 게이트를 통과한다(LaTeX만 차단)."""
    ctx = _ctx(
        signal_type=SignalType.TYPE_BIAS,
        facts=(EvidenceFact("해당 유형 오답", "12문항 중 8문항"),),
    )
    provider = _MockProvider(text="문학×추론 유형에서 12문항 중 8문항 오답이 몰려요")
    brief, outcome = _make(ctx, provider)
    assert brief.fallback_used is False
    assert brief.gate_passed is True
    assert "×" in brief.text


# ── 분기표 #5 시간 예산 소진 → 호출 없이 폴백 ──────────────


def test_budget_exhausted_skips_llm() -> None:
    ctx = _ctx()
    provider = _MockProvider(text="아무 문장")
    brief, outcome = _make(ctx, provider, now=100.0, deadline=45.0)  # now > deadline
    assert brief.fallback_used is True
    assert outcome == "budget_exhausted"
    assert provider.calls == 0  # LLM 미호출


# ── 분기표 #2 마스킹 불확실 → 미전송(fail-closed) ──────────


def test_redaction_uncertain_blocks_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    from ai.runtime.redaction import RedactionResult

    def _uncertain(_text: str) -> RedactionResult:
        return RedactionResult(masked_text="[redacted]", uncertain=True)

    monkeypatch.setattr("ai.composition.briefing.redact", _uncertain)
    ctx = _ctx()
    provider = _MockProvider(text="아무 문장")
    brief, outcome = _make(ctx, provider)
    assert brief.fallback_used is True
    assert outcome == "redaction_blocked"
    assert provider.calls == 0  # fail-closed — LLM에 안 보냄


# ── 분기표 #8 grounding — 컨텍스트가 제공한 수치만 허용 ─────


def test_grounded_number_passes_gate() -> None:
    ctx = _ctx(facts=(EvidenceFact("이번 주 정답률", "62%"),))  # 근거에 62
    provider = _MockProvider(text="비문학 정답률이 62%까지 내려왔어요")  # 62는 grounded
    brief, outcome = _make(ctx, provider)
    assert brief.fallback_used is False
    assert brief.gate_passed is True
    assert "62" in brief.text
