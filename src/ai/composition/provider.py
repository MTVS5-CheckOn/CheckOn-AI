"""브리핑 LLM provider 선택 — settings로 fake↔openai_compat (store_backend 선례).

브리핑은 `contracts.llm.LLMProvider` Protocol에만 의존한다(양자 파일 무변경). 실 벤더
어댑터(openai_compat)는 별도 PR 소유 — 이 파일은 **settings 스위치를 선반영**해 두어
어댑터 머지 후 배선이 설정 한 줄이 되게 한다. CI·테스트·데모 기본은 fake.

FakeBriefProvider는 결정론 — 프롬프트의 signal_type을 읽어 `detection/brief.py`의
템플릿 텍스트를 반환한다. 그래서 fake 경로에서 골든·데모가 무변경이다(게이트 통과 →
gate_passed=True·fallback_used=False, 텍스트=기존 템플릿).
"""

from __future__ import annotations

import re
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

from ai.contracts.execution import ExecutionContext
from ai.contracts.llm import (
    CallOutcome,
    LLMProvider,
    LLMRequest,
    LLMResult,
    TokenUsage,
)

_FAKE = "fake"
_OPENAI_COMPAT = "openai_compat"
_DRAFT_RE = re.compile(r"초안:\s*(.+)")


class BriefingSettings(BaseSettings):
    """브리핑 provider 설정 — env `LLM_PROVIDER`로 주입(기본 fake)."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    llm_provider: str = _FAKE
    """"fake"(기본·CI·데모) | "openai_compat"(실 벤더 — 어댑터 PR 머지 후 활성)."""


@lru_cache
def get_briefing_settings() -> BriefingSettings:
    return BriefingSettings()


class FakeBriefProvider:
    """결정론 fake — 프롬프트의 '초안'(엔진 brief)을 그대로 echo한다.

    초안을 그대로 돌려주므로 게이트를 통과하고(숫자·금칙어·토큰·길이 모두 초안 기준),
    fake 경로에서 골든·데모가 무변경이다(brief = 엔진 초안, gate_passed=True·fallback_used=False).
    """

    @property
    def name(self) -> str:
        return "fake-brief"

    async def complete(
        self, request: LLMRequest, context: ExecutionContext
    ) -> LLMResult:
        match = _DRAFT_RE.search(request.prompt)
        text = match.group(1).strip() if match else ""
        return LLMResult(
            outcome=CallOutcome.OK,
            text=text,
            provider=self.name,
            model="template",
            usage=TokenUsage(tokens_in=0, tokens_out=0, cost_usd=0.0),
            latency_ms=0,
        )


def build_brief_provider(settings: BriefingSettings | None = None) -> LLMProvider:
    """settings로 provider 선택. 기본 fake — openai_compat은 어댑터 PR 머지 후 활성."""
    settings = settings or get_briefing_settings()
    if settings.llm_provider == _OPENAI_COMPAT:
        # 어댑터 PR(llm/providers/openai_compat) 머지 후 아래 두 줄로 배선:
        #   from ai.llm.providers.openai_compat import OpenAICompatProvider
        #   return OpenAICompatProvider()
        raise NotImplementedError(
            "openai_compat provider는 어댑터 PR 미머지 — 99 15 후속(현재 기본 fake)"
        )
    return FakeBriefProvider()
