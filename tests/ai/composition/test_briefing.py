"""브리핑 문장화 분기표 — LLM 실패·게이트 소진·마스킹·예산·판정 무변 (09 §3 · 분기표 8종).

FakeProvider·mock으로 결정론화(pytest-asyncio 없이 asyncio.run). 실 LLM 경로는
integration 스모크(test_briefing_smoke)로 분리.
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from uuid import UUID

import pytest

from ai.composition.briefing import MAX_REGEN, make_brief
from ai.composition.provider import (
    BriefingSettings,
    FakeBriefProvider,
    build_brief_provider,
)
from ai.contracts.detection import (
    DISPLAY_LABELS,
    Brief,
    EvidenceItem,
    Lifecycle,
    RuleId,
    Signal,
    SignalType,
)
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
            prompt_version="0.1",
        ),
    )


def _signal(draft: str = "정답률이 평소보다 떨어진 상태가 이어지고 있어요.") -> Signal:
    return Signal(
        signal_id="s1",
        student_ref="st_1",
        class_ref="cl_a",
        rule_id=RuleId.R1,
        signal_type=SignalType.ACC_DROP,
        display_label=DISPLAY_LABELS[SignalType.ACC_DROP],
        score=0.5,
        rank=1,
        lifecycle=Lifecycle.NEW,
        brief=Brief(text=draft, gate_passed=True, fallback_used=False),
        evidence=(EvidenceItem(source_table="learning_event", record_id="le_1", summary="근거"),),
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
    signal: Signal, provider: LLMProvider, *, now: float = 0.0, deadline: float = 45.0
) -> tuple[Brief, str]:
    return _run(
        make_brief(signal, provider, context=_context(), now=lambda: now, deadline=deadline)
    )


# ── 분기표 #7 provider 선택 ────────────────────────────────


def test_default_provider_is_fake() -> None:
    assert BriefingSettings().llm_provider == "fake"
    assert isinstance(build_brief_provider(), FakeBriefProvider)


def test_openai_compat_not_wired_yet() -> None:
    """어댑터 PR 미머지 — 선택 시 명시적 미구현(기본은 fake)."""
    with pytest.raises(NotImplementedError):
        build_brief_provider(BriefingSettings(llm_provider="openai_compat"))


# ── 분기표 #8 fake 성공 = 초안 echo, fallback_used=False ────


def test_fake_success_echoes_draft() -> None:
    signal = _signal("특정 영역·유형에 오답이 몰리고 있어요. (literature×infer 오답 8/12)")
    brief, outcome = _make(signal, FakeBriefProvider())
    assert brief.text == signal.brief.text  # 초안 그대로 → 데모 무변경
    assert brief.gate_passed is True
    assert brief.fallback_used is False
    assert outcome == "ok"


# ── 분기표 #1 LLM 실패 → 재시도 없이 즉시 폴백 ─────────────


@pytest.mark.parametrize(
    "error",
    [LlmUnavailable("down"), LlmTimeout("slow"), ParseFailed("bad")],
)
def test_llm_failure_falls_back_without_retry(error: Exception) -> None:
    signal = _signal()
    provider = _MockProvider(error=error)
    brief, outcome = _make(signal, provider)
    assert brief.fallback_used is True
    assert brief.text == signal.brief.text  # 템플릿(엔진 초안) 재사용
    assert outcome == "llm_failed"
    assert provider.calls == 1  # 재시도 없음(게이트웨이 후속)


# ── 분기표 #3 게이트 실패 → 재생성 ≤3 → 소진 폴백 ─────────


def test_gate_exhaustion_after_max_regen() -> None:
    signal = _signal()
    provider = _MockProvider(text="풀이 시간이 21일째 늘고 있어요")  # 초안에 없는 숫자 21
    brief, outcome = _make(signal, provider)
    assert brief.fallback_used is True
    assert brief.gate_passed is False
    assert outcome.startswith("gate_exhausted")
    assert provider.calls == MAX_REGEN  # 정확히 3회 재생성


# ── 분기표 #4 ⟪⟫ 토큰 잔존 → 게이트 실패 경로 ─────────────


def test_token_leak_triggers_gate_failure() -> None:
    signal = _signal()
    provider = _MockProvider(text="⟪이름1⟫ 학생 정답률이 떨어져요")
    brief, outcome = _make(signal, provider)
    assert brief.fallback_used is True
    assert "token_leak" in outcome
    assert provider.calls == MAX_REGEN


# ── 분기표 #5 시간 예산 소진 → 호출 없이 폴백 ──────────────


def test_budget_exhausted_skips_llm() -> None:
    signal = _signal()
    provider = _MockProvider(text="아무 문장")
    brief, outcome = _make(signal, provider, now=100.0, deadline=45.0)  # now > deadline
    assert brief.fallback_used is True
    assert outcome == "budget_exhausted"
    assert provider.calls == 0  # LLM 미호출


# ── 분기표 #2 마스킹 불확실 → 미전송(fail-closed) ──────────


def test_redaction_uncertain_blocks_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    from ai.runtime.redaction import RedactionResult

    def _uncertain(_text: str) -> RedactionResult:
        return RedactionResult(masked_text="[redacted]", uncertain=True)

    monkeypatch.setattr("ai.composition.briefing.redact", _uncertain)
    signal = _signal()
    provider = _MockProvider(text="아무 문장")
    brief, outcome = _make(signal, provider)
    assert brief.fallback_used is True
    assert outcome == "redaction_blocked"
    assert provider.calls == 0  # fail-closed — LLM에 안 보냄


# ── 분기표 #8 grounding — 초안 숫자만 허용 ─────────────────


def test_grounded_number_passes_gate() -> None:
    signal = _signal("풀이 시간이 3주째 늘고 있어요")  # 초안에 3
    provider = _MockProvider(text="비문학 풀이가 3주째 느려지고 있어요")  # 3은 grounded
    brief, outcome = _make(signal, provider)
    assert brief.fallback_used is False
    assert brief.gate_passed is True
    assert "3" in brief.text
