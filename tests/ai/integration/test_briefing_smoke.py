"""실 LLM 브리핑 스모크 — 어댑터로 1회 문장화 (integration · env 있으면 실행).

기본 실행 제외(pytest addopts `-m 'not integration'`). 실 벤더 어댑터(openai_compat)는
별도 PR 소유라 이 브랜치엔 없다 — provider 배선이 안 되면(NotImplementedError) skip한다.
어댑터 머지 + LOCAL_LLM_* env 설정 후에만 실제로 돈다.
"""

from __future__ import annotations

import asyncio
from uuid import UUID

import pytest

from ai.composition.briefing import make_brief
from ai.composition.provider import BriefingSettings, build_brief_provider
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
from ai.contracts.llm import LlmError

pytestmark = pytest.mark.integration


def _signal() -> Signal:
    return Signal(
        signal_id="s1",
        student_ref="st_1",
        class_ref="cl_a",
        rule_id=RuleId.R4,
        signal_type=SignalType.HIDDEN_RISK,
        display_label=DISPLAY_LABELS[SignalType.HIDDEN_RISK],
        score=0.6,
        rank=1,
        lifecycle=Lifecycle.NEW,
        brief=Brief(
            text="점수는 버티고 있지만 문제를 붙잡는 시간이 늘고 있어요.",
            gate_passed=True,
            fallback_used=False,
        ),
        evidence=(EvidenceItem(source_table="learning_event", record_id="le_1", summary="근거"),),
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
    try:
        provider = build_brief_provider(BriefingSettings(llm_provider="openai_compat"))
    except NotImplementedError:
        pytest.skip("openai_compat 어댑터 미배선(별도 PR) — 스모크 skip")

    signal = _signal()
    try:
        brief, _outcome = asyncio.run(
            make_brief(
                signal,
                provider,
                context=_context(),
                now=lambda: 0.0,
                deadline=45.0,
            )
        )
    except LlmError as exc:
        pytest.skip(f"로컬 LLM 미가용 — {type(exc).__name__}")

    assert brief.text  # 비어있지 않은 한 줄
    assert "⟪" not in brief.text and "⟫" not in brief.text
