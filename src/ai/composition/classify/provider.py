"""분류 게이트웨이 조립부 — provider 선택 + role 등록 (03 §2 "외부 의존은 주입").

`composition/provider.py`(브리핑)와 같은 규약이다. 게이트웨이를 **여기서만** 만든다 —
`test_trace_masking_hook.py`가 조립부 화이트리스트 밖의 `LlmGateway(...)` 생성을 정적으로
막는다.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Final

from pydantic_settings import BaseSettings, SettingsConfigDict

from ai.contracts.execution import ExecutionContext
from ai.contracts.llm import (
    CallOutcome,
    LLMProvider,
    LLMRequest,
    LLMResult,
    ModelRole,
    TokenUsage,
)
from ai.llm.gateway import LlmGateway
from ai.runtime.trace_masking import RedactionTripwireTraceHook

_FAKE: Final = "fake"
_OPENAI_COMPAT: Final = "openai_compat"

#: 분류 전송 재시도 — 브리핑·counsel과 동일하게 0회(무재시도·즉시 폴백).
CLASSIFIER_TRANSPORT_RETRY: Final = 0

#: fake가 돌려주는 결정론 판정 — enum 안의 값이라 파싱을 통과한다(CI·데모용).
_FAKE_OUTPUT: Final = (
    '{"topic": "etc", "sentiment": "normal", "urgency": "normal",'
    ' "confidence": {"topic": 0.5, "sentiment": 0.5, "urgency": 0.5}}'
)


class ClassifySettings(BaseSettings):
    """분류 provider 설정 — env `LLM_PROVIDER`로 주입(기본 fake)."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    llm_provider: str = _FAKE


@lru_cache
def get_classify_settings() -> ClassifySettings:
    return ClassifySettings()


class FakeClassifyProvider:
    """결정론 fake — LLM을 호출하지 않는다. CI 기본값(실 호출 0)."""

    @property
    def name(self) -> str:
        return "fake-classify"

    async def complete(
        self, request: LLMRequest, context: ExecutionContext
    ) -> LLMResult:
        del request, context
        return LLMResult(
            outcome=CallOutcome.OK,
            text=_FAKE_OUTPUT,
            provider=self.name,
            model="template",
            usage=TokenUsage(tokens_in=0, tokens_out=0, cost_usd=0.0),
            latency_ms=0,
        )


def build_classify_provider(settings: ClassifySettings | None = None) -> LLMProvider:
    """settings로 provider 선택. 기본 fake — `openai_compat`이면 실 로컬 LLM.

    벤더 독립 유지: `openai` import는 어댑터 안에만 있고 여기선 구현을 선택만 한다.
    """
    settings = settings or get_classify_settings()
    if settings.llm_provider == _OPENAI_COMPAT:
        from ai.llm.providers.openai_compat import OpenAICompatProvider

        return OpenAICompatProvider()
    return FakeClassifyProvider()


def build_classify_gateway(provider: LLMProvider | None = None) -> LlmGateway:
    """분류 게이트웨이 — role은 **GENERATOR**다.

    ⚠ **`ModelRole`에 `CLASSIFIER`가 없어서**다. 분류는 생성도 검증도 아니지만 게이트웨이가
    role로 모델을 라우팅하므로 값이 필요하고, `contracts/llm.py`는 **양자 승인 파일**이라
    값을 추가하지 않았다. ⇒ 지금은 **분류가 생성용 모델로 라우팅된다**(값싼 모델로 가는 게
    맞다) — `CLASSIFIER` 신설을 제안한 상태다(99 D).
    """
    return LlmGateway(
        {ModelRole.GENERATOR: provider or build_classify_provider()},
        transport_retry={ModelRole.GENERATOR: CLASSIFIER_TRANSPORT_RETRY},
        trace_masking_hook=RedactionTripwireTraceHook(),
    )


__all__ = [
    "CLASSIFIER_TRANSPORT_RETRY",
    "ClassifySettings",
    "FakeClassifyProvider",
    "build_classify_gateway",
    "build_classify_provider",
    "get_classify_settings",
]
