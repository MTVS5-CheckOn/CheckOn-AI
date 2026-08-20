"""OpenAI API provider 어댑터 — 접속 대상은 `OPENAI_*` env가 정한다.

🔴 **(8/6) 실서비스 백엔드는 OpenAI 단일로 재확정됐다.** 폐기된 서버의 구 설정은
코드가 읽지 않으며 현재 실행 선택지가 아니다. 변경 이력은 99 ⓟ에 보존한다.
⚠ `LLM_PROVIDER=openai_compat` 값은 기존 어댑터 식별자이므로 유지한다.

사양 원본: `contracts/llm.py`(LLMProvider 인터페이스 — 양자 파일, 변경 금지) ·
`docs/policies/error_codes.md` §4(Llm* → HTTP 매핑).
소유: 염준영/B (llm/providers/) — 이 어댑터는 A 초안 + B 승인 PR.

**벤더 독립 경계:** `openai` import는 이 파일(과 llm/providers/)에만 둔다 —
capability·contracts는 벤더를 모른다(grep 게이트가 강제).

**재시도 없음(무재시도·즉시 폴백 확정):** 어댑터는 백오프·재호출을 자체 구현하지 않고,
**SDK 내장 재시도도 끈다(max_retries=0)**. 게이트웨이가 tenacity로 재시도 대상 오류를
받아 처리한다(contracts/llm.py LLMProvider 규약). 그래서 여기선 실패를 **계약 예외**(Llm*)로만
올리고, SDK 예외가 밖으로 새지 않게 한다.

**타임아웃 = 전체(총) 상한(브리핑 v2 프리뷰 실측에서 발견한 정책 불일치 수정):**
openai SDK(2.48)에 float `timeout`을 주면 `httpx.Timeout(t)` = connect/read/write/pool가
**각각** t초로 잡힐 뿐 **호출 전체의 벽시계 상한이 아니다**. 게다가 SDK 기본 `max_retries=2`가
붙어, read-timeout이 나도 조용히 2회까지 재시도한다 — v2 프리뷰에서 "10s 타임아웃인데 평균
14s가 통과"·"타임아웃 사례가 21~31s"였던 원인이 바로 이 재시도(≈timeout×시도횟수)였다.
그래서 (1) `max_retries=0`으로 재시도를 끄고, (2) 호출을 `asyncio.timeout`으로 감싸
**전체 기준 상한**을 강제한다(httpx 구간 타임아웃은 하한 방어로 함께 둔다).

**PII:** 프롬프트·응답 본문은 로그에 남기지 않는다 — 비용·토큰·지연 메타만
(마스킹은 redaction 층 소유, 이 계층은 무관).
"""

from __future__ import annotations

import asyncio
import logging
import time
from functools import lru_cache
from types import SimpleNamespace
from typing import Any, NoReturn

from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AsyncOpenAI,
    OpenAIError,
)
from pydantic import SecretStr
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
from ai.runtime.env_files import ENV_FILES
from ai.runtime.real_llm import RealLlmOptInRequired, build_real_openai_client

logger = logging.getLogger(__name__)

#: provider 식별자 — LLM_CALL.provider에 기록된다.
PROVIDER_NAME = "openai-compat"

#: rate limit — **일시 실패**로 본다(재시도 대상). 아래 `complete()` 참조.
_RATE_LIMITED = 429


class OpenAiSettings(BaseSettings):
    """OpenAI API 접속 설정 — `OPENAI_*` env로 주입. 하드코딩 금지(§1)."""

    model_config = SettingsConfigDict(env_file=ENV_FILES, extra="ignore")

    openai_api_key: SecretStr = SecretStr("missing")
    """OpenAI API 키. 실호출은 명시적 opt-in과 유효한 scope를 모두 요구한다.

    🔴 **`SecretStr` 인 이유는 `repr` 이다.** 평문 `str` 이면 이 설정 객체가 찍히는 모든
    자리에 키가 그대로 나온다 — pytest 실패 메시지의 assertion 표현이 대표적이고, 그건
    **CI 빌드 로그에 남는다**(A 실측 2026-08-20 · 99 #122). `SecretStr` 은 pydantic 이
    `repr` 을 `SecretStr('**********')` 로 가려 준다.
    ⚠ **값을 쓸 때만** `.get_secret_value()` 로 푼다 — 푸는 자리를 하나로 좁혀 둔다.
    """

    openai_model: str = "gpt-5.4-mini"
    """서버·벤더에 등록된 모델명 — 실제 값은 `OPENAI_MODEL`로 주입."""

    openai_base_url: str = "http://localhost:8000/v1"
    """OpenAI 호환 chat/completions 엔드포인트(…/v1). **실값은 `OPENAI_BASE_URL`이 준다.**

    🔴 **이 리터럴은 "env가 없을 때"의 폴백이지 접속 대상이 아니다.** pydantic-settings가
    필드명 → 같은 이름의 env(대소문자 무시)를 읽고, `.env`·환경 변수가 **항상 이 값을
    이긴다**(`model_config`의 `env_file=".env"`).

    🔴 **왜 하필 localhost인가 — 두 가지 다 의도다.**

    ① **미설정을 안전하게 만든다(fail-safe).** 기본을 `https://api.openai.com/v1`처럼 실
       엔드포인트로 두면 **env를 빠뜨린 실행이 조용히 외부로 프롬프트를 보낸다.** 개인정보
       경계(불변식 3)에서 그건 최악의 기본값이다 — 닿는 곳이 없는 주소가 맞다.
    ② **"미설정" 신호로 실제로 쓰인다.** `evaluation/counsel_llm_smoke.py`·
       `tests/ai/integration/test_llm_smoke.py`·`test_pg_real_llm_smoke.py`가
       `"localhost" in openai_base_url`로 **실서버 미설정을 판정해 skip**한다. 값을 바꾸려면
       그 세 곳을 함께 고쳐야 한다.

    실호출에서는 `https://api.openai.com/v1`을 명시 주입한다. 기본 localhost는 env 누락이
    외부 전송으로 이어지지 않게 하는 fail-closed 값일 뿐이다.
    """

    openai_timeout_s: float = 15.0
    """호출 전체(총) 상한 15s(error_codes §1 · 504 TIMEOUT). asyncio.timeout으로 강제.

    10s→15s: v2 프리뷰에서 근거 기반 문장(더 긴 생성)이 10s를 넘겨 폴백되는 사례가 있어
    상향. httpx 구간 타임아웃도 이 값으로 두되, 실제 상한은 complete()의 asyncio.timeout.
    """


@lru_cache
def get_llm_settings() -> OpenAiSettings:
    """설정 싱글턴 — 매 호출 재파싱 방지."""
    return OpenAiSettings()


class OpenAICompatProvider:
    """LLMProvider 구현 — OpenAI API 어댑터.

    client는 필수 주입이다. 실 client는 ``build_openai_compat_provider``만 만들고,
    테스트는 mock client를 직접 꽂아 opt-in이나 실서버 없이 결정론화한다.
    """

    def __init__(
        self,
        *,
        client: AsyncOpenAI,
        name: str = PROVIDER_NAME,
        settings: OpenAiSettings | None = None,
    ) -> None:
        normalized_name = name.strip()
        if not normalized_name:
            raise ValueError("provider name은 비어 있을 수 없다")
        self._name = normalized_name
        self._settings = settings or get_llm_settings()
        self._client = client

    @property
    def name(self) -> str:
        return self._name

    def _build_kwargs(self, request: LLMRequest) -> dict[str, Any]:
        """계약 인터페이스(GenerationParams)가 정의한 경로로만 파라미터를 받는다.

        🔴 **여기가 계약 → 벤더 규격 번역 지점이다.** 계약 필드명과 벤더 파라미터명이
        1:1일 필요는 없다 — 다르면 **여기서 흡수한다**. 그게 이 어댑터의 존재 이유이고
        (모듈 docstring "벤더 독립 경계"), `contracts/execution.py`가 양자 승인 파일인
        이유이기도 하다. 벤더가 이름을 바꿨다고 계약을 따라 바꾸면 벤더가 계약으로
        새어 들어온다.
        """
        params = request.generation_params
        kwargs: dict[str, Any] = {
            "model": self._settings.openai_model,
            "messages": [{"role": "user", "content": request.prompt}],
        }
        if params is not None:
            # 🔴 (8/13) **temperature도 「값이 없으면 안 보낸다」로 통일한다.**
            #   종전에는 이 자리에 폴백 상수(0.7)가 있어 **네 파라미터 중 temperature만
            #   뺄 수가 없었다.** 폴백이 있으면 벤더가 허용값을 좁히는 날 흡수할 방법이 없다.
            #   `gpt-5.6-luna` 400 원문(2026-08-13 직접 curl · param="temperature"):
            #     Unsupported value: 'temperature' does not support 0 with this
            #     model. Only the default (1) value is supported.
            #   ⚠ **모델별 분기를 만들지 않았다** — 8/6 max_tokens **교체**와 같은 처방이다.
            #     설정 플래그로 나누면 99 ⓢ의 죽은 분기가 된다.
            #   🔴 **폴백 상수를 다시 두지 마라** — 그게 이 안건의 원인이다. 재도입하면
            #     `test_every_nullable_param_follows_the_same_omit_when_unset_rule`이 red다.
            if params.temperature is not None:
                kwargs["temperature"] = params.temperature
            if params.top_p is not None:
                kwargs["top_p"] = params.top_p
            if params.max_tokens is not None:
                # 🔴 (8/6) 계약의 `max_tokens` → 벤더 키 **`max_completion_tokens`**.
                #   `gpt-5.4-mini`가 구 키를 거부한다. 8/6 1차 실측의 400 원문:
                #     Unsupported parameter: 'max_tokens' is not supported with this
                #     model. Use 'max_completion_tokens' instead.
                #   브리핑(128)이 21/21 죽었고 분류(256)도 같은 경로였다. 상담 초안·plan은
                #   `max_tokens`가 None이라 이 줄을 안 타서 무사했다(21회 성공).
                #   ⚠ **되돌리지 말 것** — 구 키로 돌리면 전 호출이 400이다.
                #   ⚠ **설정 플래그로 분기하지 않았다.** "구 서버는 max_tokens"로 나누고
                #     싶어지지만 그게 제거된 thinking 확장 플래그가 죽은 분기가 된 경로다
                #     (99 ⓢ). 로컬 서버는 폐기 확정이라(B-5 재확정) 교체가 맞다.
                kwargs["max_completion_tokens"] = params.max_tokens
            if params.seed is not None:
                kwargs["seed"] = params.seed
        # 표준 OpenAI API에서 지원하지 않는 thinking 비활성화 확장은 제거됨 · 99 ⓢ
        return kwargs

    async def complete(
        self, request: LLMRequest, context: ExecutionContext
    ) -> LLMResult:
        """프롬프트 1회 호출. SDK 예외는 전부 계약 예외로 감싼다(밖으로 안 샌다)."""
        start = time.monotonic()
        try:
            # 전체(총) 상한 강제 — SDK float timeout은 구간별(connect/read/…)이라 벽시계
            # 총량을 보장하지 못한다. asyncio.timeout이 호출 전체를 하나로 감싼다.
            async with asyncio.timeout(self._settings.openai_timeout_s):
                response = await self._client.chat.completions.create(
                    **self._build_kwargs(request)
                )
        except (APITimeoutError, TimeoutError) as exc:
            raise LlmTimeout("LLM 타임아웃(전체 상한)") from exc
        except RealLlmOptInRequired as exc:
            raise LlmUnavailable(str(exc)) from exc
        except APIConnectionError as exc:
            raise LlmUnavailable("LLM 연결 실패") from exc
        except APIStatusError as exc:
            # 🔴 (8/6) **429는 재시도 대상이다.** 종전에는 5xx만 `LlmUnavailable`이고 4xx는
            # 전부 `LlmError`(무재시도)였다 — 로컬 서버는 rate limit이 없어 문제가 안 됐지만
            # 외부 API는 평가셋 연속 호출에서 429를 맞는다. 그때 무재시도로 떨어지면
            # **일시적 제한이 영구 실패처럼** 폴백된다.
            # ⚠ 다른 4xx(400·401·403·404)는 그대로 둔다 — 재시도해도 안 풀린다.
            #   판정은 **어댑터에서** 한다(게이트웨이는 B 소유 · 무접촉).
            if exc.status_code >= 500 or exc.status_code == _RATE_LIMITED:
                raise LlmUnavailable(f"LLM 일시 실패 {exc.status_code}") from exc
            raise LlmError(f"LLM 요청 거부 {exc.status_code}") from exc
        except OpenAIError as exc:
            raise LlmError("LLM 호출 실패") from exc

        latency_ms = int((time.monotonic() - start) * 1000)
        content = response.choices[0].message.content if response.choices else None
        if content is None or not content.strip():
            # 빈 응답은 ParseFailed — 상위 소비자의 재시도 예산(블록 ≤3·item_attempt)이 소진한다.
            raise ParseFailed("LLM 빈 응답(content 없음/공백)")

        usage = response.usage
        token_usage = TokenUsage(
            tokens_in=usage.prompt_tokens if usage is not None else 0,
            tokens_out=usage.completion_tokens if usage is not None else 0,
            # 🔴 (8/6) **0.0은 "원가 없음"이 아니라 "미측정"이다.** 종전 근거는 "로컬 서버 —
            # 토큰당 API 원가 없음"이었는데 외부 유료 API로 바뀌면 그 전제가 깨진다.
            # 단가표를 만들지 않은 이유: 모델·시점 종속이라 유지 부담이고 지금 쓰이지 않는다.
            # ⚠ 이 값을 합산해 "LLM 원가 0원" 리포트를 내면 안 된다(99 ⓠ).
            cost_usd=0.0,
        )
        # 본문 금지 — 메타만 로깅(PII·비용).
        logger.debug(
            "llm.complete provider=%s model=%s tokens_in=%d tokens_out=%d latency_ms=%d",
            self.name,
            self._settings.openai_model,
            token_usage.tokens_in,
            token_usage.tokens_out,
            latency_ms,
        )
        return LLMResult(
            outcome=CallOutcome.OK,
            text=content,
            provider=self.name,
            model=self._settings.openai_model,
            usage=token_usage,
            latency_ms=latency_ms,
        )


class _DeniedCompletions:
    """opt-in 없이 실 SDK client를 만들지 않기 위한 호출 차단 대역."""

    def __init__(self, reason: str) -> None:
        self._reason = reason

    async def create(self, **kwargs: object) -> NoReturn:
        del kwargs
        raise RealLlmOptInRequired(self._reason)


class _DeniedOpenAIClient(AsyncOpenAI):
    """SDK를 초기화하지 않고 ``chat.completions.create``에서 명시적으로 거부한다."""

    def __init__(self, reason: str) -> None:
        object.__setattr__(
            self,
            "chat",
            SimpleNamespace(completions=_DeniedCompletions(reason)),
        )


def build_openai_compat_provider(
    *,
    name: str = PROVIDER_NAME,
    settings: OpenAiSettings | None = None,
) -> OpenAICompatProvider:
    """중앙 실 client 관문을 거쳐 OpenAI 호환 provider를 조립한다."""
    resolved = settings or get_llm_settings()
    client = build_real_openai_client(
        AsyncOpenAI,
        denied_client_factory=_DeniedOpenAIClient,
        base_url=resolved.openai_base_url,
        api_key=resolved.openai_api_key.get_secret_value(),
        timeout_s=resolved.openai_timeout_s,
    )
    return OpenAICompatProvider(name=name, settings=resolved, client=client)
