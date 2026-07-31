"""C-3 브리핑 재생성 루프가 시간 예산을 재확인하지 않는다 — 예산 이후에도 계속 호출한다.

결함(점검 4-6a): `make_brief`는 **진입 시 1회만** `now() >= deadline`을 본다. 게이트
실패 재생성은 그 검사 뒤에 일어나므로, 1회차에서 예산을 다 써도 2·3회차 LLM 호출이
그대로 나간다(호출당 수 초 × 잔여 횟수). 라우터가 예산을 준 의미가 사라진다.

규정: `docs/part_a/05_tone_mapping.md` §6-4 — 예산이 있는 경로는 **재생성 루프의 매
반복 진입 시 LLM 호출 전에** 재검사하고, 초과면 호출 없이 폴백한다.

라벨은 기존 `budget_exhausted`를 재사용한다 — 새 어휘를 만들지 않는다(어휘 선점 규칙).
"""

from __future__ import annotations

import asyncio
from uuid import UUID

from ai.composition.briefing import MAX_REGEN, make_brief
from ai.composition.briefing_context import BriefingContext
from ai.contracts.detection import DISPLAY_LABELS, Brief, Lifecycle, SignalType
from ai.contracts.execution import Capability, ExecutionContext, VersionSet
from ai.contracts.llm import CallOutcome, LLMRequest, LLMResult, TokenUsage
from ai.detection.segments import Segment

#: 호출 1회가 태우는 시간(초) — 실 LLM 한 호출이 예산을 통째로 넘기는 상황을 모사한다.
_CALL_COST = 20.0
_DEADLINE = 15.0

#: 게이트에 반드시 걸리는 본문 — 근거에 없는 숫자(재생성을 유발한다).
_UNGROUNDED = "정답률이 83퍼센트까지 떨어졌어요."


class _ClockedProvider:
    """호출할 때마다 시계를 전진시키는 LLM 대역 — 시간이 **호출로만** 흐른다.

    시계 주입(03 §3)을 지키면서 "예산을 태운 건 LLM 호출"이라는 인과를 그대로 만든다.
    """

    def __init__(self) -> None:
        self.calls = 0
        self.elapsed = 0.0

    @property
    def name(self) -> str:
        return "clocked"

    def now(self) -> float:
        return self.elapsed

    async def complete(
        self, request: LLMRequest, context: ExecutionContext
    ) -> LLMResult:
        del request, context
        self.calls += 1
        self.elapsed += _CALL_COST
        return LLMResult(
            outcome=CallOutcome.OK,
            text=_UNGROUNDED,
            provider=self.name,
            model="mock",
            usage=TokenUsage(tokens_in=0, tokens_out=0, cost_usd=0.0),
            latency_ms=0,
        )


def _ctx() -> BriefingContext:
    return BriefingContext(
        signal_type=SignalType.ACC_DROP,
        display_label=DISPLAY_LABELS[SignalType.ACC_DROP],
        lifecycle=Lifecycle.NEW,
        segment=Segment.NORMAL,
        facts=(),
        evidence_summaries=("근거 기록",),
        fallback_text="정답률이 평소보다 눈에 띄게 떨어진 상태가 이어지고 있어요.",
    )


def _execution_context() -> ExecutionContext:
    return ExecutionContext(
        execution_id=UUID("00000000-0000-4000-8000-000000000041"),
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


def _run(provider: _ClockedProvider) -> tuple[Brief, str]:
    return asyncio.run(
        make_brief(
            _ctx(),
            provider,
            context=_execution_context(),
            now=provider.now,
            deadline=_DEADLINE,
        )
    )


def test_regen_stops_when_budget_is_exhausted_mid_loop() -> None:
    """🔴 C-3 재현 — 수정 전에는 예산을 넘긴 뒤에도 2·3회차 호출이 나간다."""
    provider = _ClockedProvider()
    brief, outcome = _run(provider)

    assert provider.calls == 1, "예산 초과 후에는 LLM을 부르지 않는다"
    assert outcome == "budget_exhausted"
    assert brief.fallback_used
    assert not brief.gate_passed


def test_budget_label_is_reused_not_reinvented() -> None:
    """라벨은 진입 시 검사와 **같은 문자열**이다 — 새 어휘를 만들지 않는다."""
    spent = _ClockedProvider()
    spent.elapsed = _DEADLINE + 1.0  # 진입 시점에 이미 소진
    _, entry_outcome = _run(spent)

    provider = _ClockedProvider()
    _, loop_outcome = _run(provider)

    assert entry_outcome == loop_outcome == "budget_exhausted"
    assert spent.calls == 0


def test_budget_intact_still_uses_the_full_regen_limit() -> None:
    """역케이스 — 예산이 넉넉하면 상한까지 그대로 돈다(재검사가 루프를 조기 종료시키지 않는다)."""

    class _FreeProvider(_ClockedProvider):
        async def complete(
            self, request: LLMRequest, context: ExecutionContext
        ) -> LLMResult:
            result = await super().complete(request, context)
            self.elapsed = 0.0  # 시간을 태우지 않는 호출
            return result

    provider = _FreeProvider()
    _, outcome = _run(provider)
    assert provider.calls == MAX_REGEN
    assert outcome.startswith("gate_exhausted")
