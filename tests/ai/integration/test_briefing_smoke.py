"""실 LLM 브리핑 스모크 — 어댑터로 1회 문장화 (integration · env 있으면 실행).

기본 실행 제외(pytest addopts `-m 'not integration'`). 실 벤더 어댑터(openai_compat)는
별도 PR 소유라 이 브랜치엔 없다 — provider 배선이 안 되면(NotImplementedError) skip한다.
어댑터 머지 + OPENAI_* env 설정 후에만 실제로 돈다.
"""

from __future__ import annotations

import asyncio
from uuid import UUID

import pytest

from ai.composition.briefing import make_brief
from ai.composition.briefing_context import BriefingContext, EvidenceFact
from ai.composition.provider import BriefingSettings, build_brief_provider
from ai.contracts.detection import DISPLAY_LABELS, Lifecycle, SignalType
from ai.contracts.execution import Capability, ExecutionContext, VersionSet
from ai.contracts.llm import LlmError
from ai.detection.segments import Segment
from ai.llm.providers.openai_compat import get_llm_settings
from ai.runtime.real_llm import real_llm_skip_reason

pytestmark = pytest.mark.integration


def _ctx() -> BriefingContext:
    """R4 숨은 위기 근거 패키지 — 실 LLM이 근거로 한 문장을 구성한다(v2)."""
    return BriefingContext(
        signal_type=SignalType.HIDDEN_RISK,
        display_label=DISPLAY_LABELS[SignalType.HIDDEN_RISK],
        lifecycle=Lifecycle.NEW,
        segment=Segment.NORMAL,
        facts=(
            EvidenceFact("정답률 변동(평소 대비)", "3%p 이내로 유지"),
            EvidenceFact("문제 풀이 시간(평소 대비)", "180%"),
        ),
        evidence_summaries=("hidden_risk 근거 기록",),
        fallback_text="점수는 버티고 있지만 문제를 붙잡는 시간이 늘고 있어요.",
    )


def _context() -> ExecutionContext:
    return ExecutionContext(
        execution_id=UUID("00000000-0000-4000-8000-0000000000ab"),
        tenant_id="t_smoke",
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


def test_real_provider_single_brief() -> None:
    """실 provider로 1회 문장화 — 결과는 게이트를 통과하거나(신규 문장) 폴백(초안)."""
    # 🔴 **종전에는 게이트가 아예 없었다**(99 #32) — `llm_provider="openai_compat"`를 강제해
    #    `.env`가 실서버를 가리키면 **바로 호출**했다. 그래서 사고 집계에서 빠졌다.
    reason = real_llm_skip_reason(get_llm_settings().openai_base_url)
    if reason is not None:
        pytest.skip(reason)
    try:
        provider = build_brief_provider(BriefingSettings(llm_provider="openai_compat"))
    except NotImplementedError:
        pytest.skip("openai_compat 어댑터 미배선(별도 PR) — 스모크 skip")

    try:
        brief, _outcome = asyncio.run(
            make_brief(
                _ctx(),
                provider,
                context=_context(),
                now=lambda: 0.0,
                deadline=45.0,
            )
        )
    except LlmError as exc:
        pytest.skip(f"OpenAI 미가용 — {type(exc).__name__}")

    assert brief.text  # 비어있지 않은 한 줄
    assert "⟪" not in brief.text and "⟫" not in brief.text
