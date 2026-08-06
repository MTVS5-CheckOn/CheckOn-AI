"""OpenAI 호환 provider 어댑터 — 접속 대상은 env가 정한다 (B-5 확정 7/23).

🔴 **(8/6) 접속 설정 env 접두를 `OPENAI_*`로 교체했다.** 종전 팀 로컬 서버(Gemma 계열)가
502로 내려가 측정이 막혔다. **구 접두 키는 `.env`에 남아 있지만 코드는 읽지 않는다** —
경위·되돌리는 법은 99 ⓟ에 있다(README `.env` 키 표도 두 벌을 나란히 적는다).
⚠ **어댑터 이름·`LLM_PROVIDER=openai_compat` 값은 그대로다.** 그건 벤더가 아니라 **규격**을
뜻하고, OpenAI도 로컬 OpenAI 호환 서버도 같은 규격이다.

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

#: rate limit — **일시 실패**로 본다(재시도 대상). 아래 `complete()` 참조.
_RATE_LIMITED = 429


class OpenAiSettings(BaseSettings):
    """OpenAI 호환 백엔드 접속 설정 — env(.env, 노션 공유)로 주입. 하드코딩 금지(§1).

    ⚠ 이름이 `OpenAi*`인 것은 **규격**(OpenAI 호환 API)을 뜻하지 벤더 고정이 아니다 —
    같은 설정으로 로컬 호환 서버도 가리킬 수 있다(`OPENAI_BASE_URL`만 바꾸면 된다).
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    openai_api_key: str = "local"
    """API 키. 키를 요구하지 않는 서버면 임의 비밀값 — 빈 문자열은 SDK가 거부."""

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

    ⚠ 이름이 `openai_*`라고 벤더가 고정된 게 아니다(클래스 docstring 참조) — 로컬 호환
    서버를 쓰려면 이 env만 그 주소로 바꾸면 된다.
    """

    openai_timeout_s: float = 15.0
    """호출 전체(총) 상한 15s(error_codes §1 · 504 TIMEOUT). asyncio.timeout으로 강제.

    10s→15s: v2 프리뷰에서 근거 기반 문장(더 긴 생성)이 10s를 넘겨 폴백되는 사례가 있어
    상향. httpx 구간 타임아웃도 이 값으로 두되, 실제 상한은 complete()의 asyncio.timeout.
    """

    openai_disable_thinking: bool = False
    """추론(thinking) 비활성 — **벤더 특화 확장이라 opt-in이다**(기본 꺼짐).

    켜면 요청에 `extra_body={"chat_template_kwargs": {"enable_thinking": False}}`가 붙는다.
    이건 **표준 OpenAI 파라미터가 아니라 vLLM/Qwen chat_template 확장**이다.

    🔴 **(8/6) 기본을 True → False로 뒤집었다.** 실측:

        disable_thinking=True  → 400 `Unknown parameter: 'chat_template_kwargs'`
        disable_thinking=False → 정상 응답

    표준 API는 모르는 body 파라미터를 거부한다. **벤더 확장을 기본으로 보내면 그 확장을
    아는 서버 외에는 전부 400**이라, 기본 ON은 "로컬 서버 전용" 가정이 코드에 박힌
    상태였다. 확장은 아는 서버에서만 켜는 게 맞다.

    ⚠ **켜야 하는 경우가 실재한다** — 팀 로컬 서버(mtp 계열)는 추론모델이라 영어 CoT를
    `message.content` 앞에 수백 토큰 뱉고, 그게 `max_tokens`를 소진해 content가 빈 채로
    잘린다(v2 프리뷰 실측 · 지연도 크다). 그 서버로 돌릴 때는 `OPENAI_DISABLE_THINKING=true`
    를 함께 준다 — 주소만 바꾸고 이걸 빼면 브리핑이 빈 응답으로 폴백한다.
    """


@lru_cache
def get_llm_settings() -> OpenAiSettings:
    """설정 싱글턴 — 매 호출 재파싱 방지."""
    return OpenAiSettings()


class OpenAICompatProvider:
    """LLMProvider 구현 — 팀 로컬 OpenAI 호환 서버 어댑터.

    client·settings는 주입 가능(테스트는 mock client를 꽂아 실서버 없이 결정론화).
    """

    def __init__(
        self,
        *,
        name: str = PROVIDER_NAME,
        settings: OpenAiSettings | None = None,
        client: AsyncOpenAI | None = None,
    ) -> None:
        normalized_name = name.strip()
        if not normalized_name:
            raise ValueError("provider name은 비어 있을 수 없다")
        self._name = normalized_name
        self._settings = settings or get_llm_settings()
        self._client = client or AsyncOpenAI(
            base_url=self._settings.openai_base_url,
            api_key=self._settings.openai_api_key,
            timeout=self._settings.openai_timeout_s,
            max_retries=0,  # 무재시도 확정 — SDK 기본 2회 재시도를 끈다(정책 불일치 수정)
        )

    @property
    def name(self) -> str:
        return self._name

    def _build_kwargs(self, request: LLMRequest) -> dict[str, Any]:
        """계약 인터페이스(GenerationParams)가 정의한 경로로만 파라미터를 받는다."""
        params = request.generation_params
        kwargs: dict[str, Any] = {
            "model": self._settings.openai_model,
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
        if self._settings.openai_disable_thinking:
            # 벤더 특화(계약 밖) — 서버 chat_template의 thinking을 끈다. 이 옵션은 어댑터에만
            # 존재하고 상위(composition)로 새지 않는다(벤더 독립 유지). enable_thinking=False.
            kwargs["extra_body"] = {"chat_template_kwargs": {"enable_thinking": False}}
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
