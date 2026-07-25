"""OpenAI 호환 provider 어댑터 — 팀 로컬 서버(Gemma 계열) (B-5 확정 7/23).

사양 원본: `contracts/llm.py`(LLMProvider 인터페이스 — 양자 파일, 변경 금지) ·
`docs/policies/error_codes.md` §4(Llm* → HTTP 매핑).
소유: 염준영/B (llm/providers/) — 이 어댑터는 A 초안 + B 승인 PR.

**벤더 독립 경계:** `openai` import는 이 파일(과 llm/providers/)에만 둔다 —
capability·contracts는 벤더를 모른다(grep 게이트가 강제).

**재시도 없음:** 어댑터는 백오프·재호출을 자체 구현하지 않는다. 게이트웨이가
tenacity로 재시도 대상 오류를 받아 처리한다(contracts/llm.py LLMProvider 규약).
그래서 여기선 실패를 **계약 예외**(Llm*)로만 올리고, SDK 예외가 밖으로 새지 않게 한다.

**PII:** 프롬프트·응답 본문은 로그에 남기지 않는다 — 비용·토큰·지연 메타만
(마스킹은 redaction 층 소유, 이 계층은 무관).
"""

from __future__ import annotations

import logging
import time
from functools import lru_cache
from typing import Any

from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AsyncOpenAI,
    OpenAIError,
)
from pydantic_settings import BaseSettings, SettingsConfigDict

from ai.contracts.execution import ExecutionContext
from ai.contracts.llm import (
    CallOutcome,
    LlmError,
    LLMRequest,
    LLMResult,
    LlmTimeout,
    LlmUnavailable,
    ParseFailed,
    TokenUsage,
)

logger = logging.getLogger(__name__)

#: provider 식별자 — LLM_CALL.provider에 기록된다.
PROVIDER_NAME = "local-openai-compat"

#: GenerationParams가 temperature를 주지 않을 때(None) 쓰는 provider 기본값.
#: (인터페이스에 없는 값이 아니라 "미지정"일 때의 기본 — docstring 명시가 규약)
_DEFAULT_TEMPERATURE = 0.7


class LocalLlmSettings(BaseSettings):
    """로컬 LLM 서버 접속 설정 — env(.env, 노션 공유)로 주입. 하드코딩 금지(§1)."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    local_llm_base_url: str = "http://localhost:8000/v1"
    """OpenAI 호환 chat/completions 엔드포인트(…/v1)."""

    local_llm_api_key: str = "local"
    """로컬 서버가 키를 요구하지 않으면 임의 비밀값 — 빈 문자열은 SDK가 거부."""

    local_llm_model: str = "gemma"
    """서버에 등록된 모델명 — 실제 값은 .env로 주입."""

    local_llm_timeout_s: float = 10.0
    """동기 상한 10s(error_codes §1 · 504 TIMEOUT)."""


@lru_cache
def get_llm_settings() -> LocalLlmSettings:
    """설정 싱글턴 — 매 호출 재파싱 방지."""
    return LocalLlmSettings()


class OpenAICompatProvider:
    """LLMProvider 구현 — 팀 로컬 OpenAI 호환 서버 어댑터.

    client·settings는 주입 가능(테스트는 mock client를 꽂아 실서버 없이 결정론화).
    """

    def __init__(
        self,
        *,
        settings: LocalLlmSettings | None = None,
        client: AsyncOpenAI | None = None,
    ) -> None:
        self._settings = settings or get_llm_settings()
        self._client = client or AsyncOpenAI(
            base_url=self._settings.local_llm_base_url,
            api_key=self._settings.local_llm_api_key,
            timeout=self._settings.local_llm_timeout_s,
        )

    @property
    def name(self) -> str:
        return PROVIDER_NAME

    def _build_kwargs(self, request: LLMRequest) -> dict[str, Any]:
        """계약 인터페이스(GenerationParams)가 정의한 경로로만 파라미터를 받는다."""
        params = request.generation_params
        kwargs: dict[str, Any] = {
            "model": self._settings.local_llm_model,
            "messages": [{"role": "user", "content": request.prompt}],
            "temperature": (
                params.temperature
                if params is not None and params.temperature is not None
                else _DEFAULT_TEMPERATURE
            ),
        }
        if params is not None:
            if params.top_p is not None:
                kwargs["top_p"] = params.top_p
            if params.max_tokens is not None:
                kwargs["max_tokens"] = params.max_tokens
            if params.seed is not None:
                kwargs["seed"] = params.seed
        return kwargs

    async def complete(
        self, request: LLMRequest, context: ExecutionContext
    ) -> LLMResult:
        """프롬프트 1회 호출. SDK 예외는 전부 계약 예외로 감싼다(밖으로 안 샌다)."""
        start = time.monotonic()
        try:
            response = await self._client.chat.completions.create(
                **self._build_kwargs(request)
            )
        except APITimeoutError as exc:
            raise LlmTimeout("로컬 LLM 타임아웃") from exc
        except APIConnectionError as exc:
            raise LlmUnavailable("로컬 LLM 연결 실패") from exc
        except APIStatusError as exc:
            if exc.status_code >= 500:
                raise LlmUnavailable(f"로컬 LLM 서버 오류 {exc.status_code}") from exc
            raise LlmError(f"로컬 LLM 요청 거부 {exc.status_code}") from exc
        except OpenAIError as exc:
            raise LlmError("로컬 LLM 호출 실패") from exc

        latency_ms = int((time.monotonic() - start) * 1000)
        content = response.choices[0].message.content if response.choices else None
        if content is None or not content.strip():
            # 빈 응답(content 없음/공백) = 재시도 대상. 게이트웨이가 재호출한다.
            # 계약의 재시도 대상 예외는 ParseFailed(≤3회) — 빈 응답을 여기에 매핑.
            raise ParseFailed("로컬 LLM 빈 응답(content 없음/공백)")

        usage = response.usage
        token_usage = TokenUsage(
            tokens_in=usage.prompt_tokens if usage is not None else 0,
            tokens_out=usage.completion_tokens if usage is not None else 0,
            cost_usd=0.0,  # 로컬 서버 — 토큰당 API 원가 없음
        )
        # 본문 금지 — 메타만 로깅(PII·비용).
        logger.debug(
            "llm.complete provider=%s model=%s tokens_in=%d tokens_out=%d latency_ms=%d",
            PROVIDER_NAME,
            self._settings.local_llm_model,
            token_usage.tokens_in,
            token_usage.tokens_out,
            latency_ms,
        )
        return LLMResult(
            outcome=CallOutcome.OK,
            text=content,
            provider=PROVIDER_NAME,
            model=self._settings.local_llm_model,
            usage=token_usage,
            latency_ms=latency_ms,
        )
