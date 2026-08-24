"""리포트 문장화 provider 선택과 프로덕션 조립."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

# 🔴 A 판정 ⓒ(2026-08-25): ReportNarrator가 DeterministicTextGate를 필수로 받지만
# 프로덕션 주입 주체가 없어 이 경계를 넘는다고 명시하고 이름 하나만 가져온다.
# check_brief_gate의 소비 축이 셋째가 되면 ⓐ(gates/ 승격)로 가며 이 예외를 걷는다.
# 그 전에 토큰·기호·숫자 EXACT·길이인 ⓘ 일반 검사와 buffer_lexicon 금칙어인 ⓙ 브리핑
# 고유 검사를 먼저 가른다. ⓙ가 섞인 채라면 통째로 옮기지 않고 공용/고유 게이트로 분리한다.
from ai.composition.briefing_gate import check_brief_gate
from ai.contracts.execution import ExecutionContext
from ai.contracts.llm import (
    CallOutcome,
    LLMProvider,
    LLMRequest,
    LLMResult,
    ModelRole,
    TokenUsage,
)
from ai.contracts.report_narration import ReportNarrationDraft
from ai.db.repositories.llm_payload import capture_payloads
from ai.db.repositories.run_store import default_llm_call_collector
from ai.llm.gateway import LlmCallRecorder, LlmGateway
from ai.report.narration import ReportNarrator
from ai.runtime.env_files import ENV_FILES
from ai.runtime.trace_masking import RedactionTripwireTraceHook

logger = logging.getLogger(__name__)

_FAKE = "fake"
_OPENAI_COMPAT = "openai_compat"
_REPORTER_TRANSPORT_RETRY = 0
_FAKE_RESPONSE = ReportNarrationDraft(
    content="학습 기록을 함께 살펴보겠습니다.",
    numbers_used=(),
).model_dump_json()


class ReportProviderSettings(BaseSettings):
    """리포트 provider 설정 — env `LLM_PROVIDER`로 주입하며 기본은 fake다."""

    model_config = SettingsConfigDict(env_file=ENV_FILES, extra="ignore")

    llm_provider: str = _FAKE


@lru_cache
def get_report_provider_settings() -> ReportProviderSettings:
    return ReportProviderSettings()


class FakeReportProvider:
    """게이트웨이 경로를 지키는 결정론 리포트 대역."""

    @property
    def name(self) -> str:
        return "fake-report"

    async def complete(
        self, request: LLMRequest, context: ExecutionContext
    ) -> LLMResult:
        del request, context
        return LLMResult(
            outcome=CallOutcome.OK,
            text=_FAKE_RESPONSE,
            provider=self.name,
            model="template",
            usage=TokenUsage(tokens_in=0, tokens_out=0, cost_usd=0.0),
            latency_ms=0,
        )


def build_report_llm_provider(
    settings: ReportProviderSettings | None = None,
) -> LLMProvider:
    """`LLM_PROVIDER`로 fake와 openai_compat 구현을 명시적으로 선택한다."""

    settings = settings or get_report_provider_settings()
    if settings.llm_provider == _OPENAI_COMPAT:
        from ai.llm.providers.openai_compat import (  # noqa: PLC0415
            build_openai_compat_provider,
        )

        return build_openai_compat_provider()
    provider = FakeReportProvider()
    logger.warning(
        "report provider=fake — LLM_PROVIDER=%r이라 결정론 Fake로 조립한다. "
        "실 LLM 호출은 0건이고 산출물에는 provider=%r가 남는다(사후 구분용). "
        "실 경로는 LLM_PROVIDER=%s.",
        settings.llm_provider,
        provider.name,
        _OPENAI_COMPAT,
    )
    return provider


def build_report_gateway(
    provider: LLMProvider | None = None,
    *,
    recorder: LlmCallRecorder | None = None,
) -> LlmGateway:
    """reporter role과 15콜 상한을 보존하는 문장화 게이트웨이를 조립한다."""

    return LlmGateway(
        {ModelRole.REPORTER: capture_payloads(provider or build_report_llm_provider())},
        recorder=recorder or default_llm_call_collector(),
        transport_retry={ModelRole.REPORTER: _REPORTER_TRANSPORT_RETRY},
        trace_masking_hook=RedactionTripwireTraceHook(),
    )


def _system_utc_now() -> datetime:
    return datetime.now(UTC)


def build_report_narrator(
    *,
    clock: Callable[[], datetime] = _system_utc_now,
    provider: LLMProvider | None = None,
    recorder: LlmCallRecorder | None = None,
) -> ReportNarrator:
    """provider·게이트·시계를 갖춘 프로덕션 리포트 문장화 서비스를 만든다."""

    return ReportNarrator(
        build_report_gateway(provider, recorder=recorder),
        text_gate=check_brief_gate,
        clock=clock,
    )


__all__ = [
    "FakeReportProvider",
    "ReportProviderSettings",
    "build_report_gateway",
    "build_report_llm_provider",
    "build_report_narrator",
    "get_report_provider_settings",
]
