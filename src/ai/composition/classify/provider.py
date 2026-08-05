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
    """분류 게이트웨이 — role은 **`ModelRole.CLASSIFIER`**다.

    🔴 종전에 `GENERATOR`였고 "`ModelRole`에 `CLASSIFIER`가 없어서"라는 주석이 붙어 있었는데
    **사실이 아니었다.** `contracts/llm.py:22`의 `ModelRole`은 6종(generator·verifier·
    mapper·**classifier**·narrator·counselor)이고 `CLASSIFIER`("문의 분류·태깅 제안")는
    초기 커밋부터 있다. 양자 승인 파일을 건드릴 필요조차 없었다 — 8/5 정정(99 ㊻ B-4).

    role이 틀리면 조용히 두 가지가 깨진다: ① LLM_CALL.role이 실제 역할과 다르게 적재돼
    역할별 원가 회계가 생성 비용에 섞인다 ② 역할별 모델 라우팅·전송 재시도를 분류에만
    다르게 줄 수 없다(게이트웨이는 role로만 고른다).
    """
    return LlmGateway(
        {ModelRole.CLASSIFIER: provider or build_classify_provider()},
        transport_retry={ModelRole.CLASSIFIER: CLASSIFIER_TRANSPORT_RETRY},
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
