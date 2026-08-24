"""리포트 provider 선택·표기·조립 불변식."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from uuid import UUID

import pytest

from ai.contracts.execution import Capability, ExecutionContext, VersionSet
from ai.contracts.llm import LLMRequest, ModelRole
from ai.report.provider import (
    FakeReportProvider,
    ReportProviderSettings,
    build_report_gateway,
    build_report_llm_provider,
    build_report_narrator,
)


def _context() -> ExecutionContext:
    return ExecutionContext(
        execution_id=UUID("00000000-0000-4000-8000-000000000951"),
        tenant_id="tenant-a",
        capability=Capability.COMPOSITION,
        input_snapshot_hash="sha256:report-provider",
        versions=VersionSet(
            pipeline_version="0.1.0",
            engine_version="report-0.1",
            schema_version="0.1",
            contract_version="0.1",
            prompt_version="report-narration.v1",
        ),
    )


def test_report_provider_default_is_fake_without_reading_the_environment() -> None:
    """선언 기본값을 고정해 개발자 `.env`와 무관하게 CI가 실 LLM을 부르지 않게 한다."""

    default = ReportProviderSettings.model_fields["llm_provider"].default
    assert default == "fake"


def test_choosing_fake_warns_and_marks_its_output(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """fake 선택은 배선 누락과 달리 경고와 provider 이름으로 사후 식별된다."""

    with caplog.at_level(logging.WARNING, logger="ai.report.provider"):
        provider = build_report_llm_provider(
            ReportProviderSettings(llm_provider="fake", _env_file=None)
        )
    recorded: list[str] = []
    gateway = build_report_gateway(
        provider,
        recorder=lambda call, context: recorded.append(call.provider),
    )
    result = asyncio.run(
        gateway.complete(
            LLMRequest(
                role=ModelRole.REPORTER,
                prompt="결정론 리포트 문장을 반환하세요.",
                prompt_id="report.greeting.v1",
                prompt_version="report-narration.v1",
            ),
            _context(),
        )
    )

    assert provider.name == "fake-report"
    assert result.provider == "fake-report"
    assert recorded == ["fake-report"]
    assert "LLM_PROVIDER" in caplog.text
    assert "fake-report" in caplog.text


def test_report_narrator_builder_uses_the_injected_clock() -> None:
    """프로덕션 조립부가 라우터의 시계 주입 seam을 보존한다."""

    now = datetime(2026, 8, 25, 9, 0, tzinfo=UTC)
    narrator = build_report_narrator(clock=lambda: now, provider=FakeReportProvider())

    assert narrator._clock() == now
