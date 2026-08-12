"""OpenAI 호환 provider 어댑터 단위 검사 — 오류 매핑·설정 주입·빈 응답 (B-5 어댑터).

실서버 없이 fake client를 주입해 결정론화한다(스모크는 integration 마커 별도).
검증 초점: SDK 예외가 밖으로 새지 않고 전부 계약 예외(Llm*)로 매핑되는가 ·
빈 응답이 재시도 대상(ParseFailed)인가 · 파라미터가 인터페이스 경로로만 전달되는가.
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from types import SimpleNamespace
from uuid import UUID

import httpx
import pytest
from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
)

from ai.contracts.execution import Capability, ExecutionContext, GenerationParams, VersionSet
from ai.contracts.llm import (
    CallOutcome,
    LlmError,
    LLMProvider,
    LLMRequest,
    LlmTimeout,
    LlmUnavailable,
    ModelRole,
    ParseFailed,
)
from ai.llm.providers.openai_compat import (
    PROVIDER_NAME,
    OpenAICompatProvider,
    OpenAiSettings,
)

_REQ = httpx.Request("POST", "http://local/v1/chat/completions")


def _run[T](coro: Coroutine[object, object, T]) -> T:
    return asyncio.run(coro)


def _settings() -> OpenAiSettings:
    return OpenAiSettings.model_validate(
        {
            "openai_base_url": "http://local/v1",
            "openai_api_key": "k",
            "openai_model": "test-model",
        }
    )


def _context() -> ExecutionContext:
    return ExecutionContext(
        execution_id=UUID("00000000-0000-4000-8000-000000000009"),
        tenant_id="teacher_alias_001",
        capability=Capability.COMPOSITION,
        input_snapshot_hash="sha256:abc",
        versions=VersionSet(
            pipeline_version="v2.1",
            engine_version="rules-1.0",
            schema_version="0.1",
            contract_version="0.1",
            prompt_version="v0.1",
        ),
    )


def _request(params: GenerationParams | None = None) -> LLMRequest:
    return LLMRequest(
        role=ModelRole.GENERATOR,
        prompt="[마스킹 통과 프롬프트]",
        prompt_id="composition/reply",
        prompt_version="v0.1",
        generation_params=params,
    )


class _FakeCompletions:
    def __init__(self, *, result: object = None, error: Exception | None = None) -> None:
        self._result = result
        self._error = error
        self.last_kwargs: dict[str, object] | None = None

    async def create(self, **kwargs: object) -> object:
        self.last_kwargs = kwargs
        if self._error is not None:
            raise self._error
        return self._result


class _FakeClient:
    def __init__(self, *, result: object = None, error: Exception | None = None) -> None:
        self.completions = _FakeCompletions(result=result, error=error)
        self.chat = SimpleNamespace(completions=self.completions)


def _ok_response(content: str | None) -> SimpleNamespace:
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
        usage=SimpleNamespace(prompt_tokens=12, completion_tokens=7),
    )


def _provider(*, result: object = None, error: Exception | None = None) -> OpenAICompatProvider:
    client = _FakeClient(result=result, error=error)
    return OpenAICompatProvider(settings=_settings(), client=client)  # type: ignore[arg-type]


def test_satisfies_provider_protocol() -> None:
    assert isinstance(_provider(result=_ok_response("hi")), LLMProvider)
    assert _provider(result=_ok_response("hi")).name == PROVIDER_NAME


def test_happy_path_returns_ok_result() -> None:
    provider = _provider(result=_ok_response("안녕하세요, 한 문장입니다."))
    result = _run(provider.complete(_request(), _context()))
    assert result.outcome is CallOutcome.OK
    assert result.text == "안녕하세요, 한 문장입니다."
    assert result.provider == PROVIDER_NAME
    assert result.model == "test-model"
    assert result.usage.tokens_in == 12
    assert result.usage.tokens_out == 7
    assert result.usage.cost_usd == 0.0  # 미측정 — 원가 없음이 아니다(99 ⓠ)


def test_injected_name_identifies_provider_and_result() -> None:
    provider = OpenAICompatProvider(
        name="openai-verifier",
        settings=_settings(),
        client=_FakeClient(result=_ok_response("검증 결과")),  # type: ignore[arg-type]
    )

    result = _run(provider.complete(_request(), _context()))

    assert provider.name == "openai-verifier"
    assert result.provider == "openai-verifier"


def test_blank_injected_name_is_rejected() -> None:
    with pytest.raises(ValueError, match="provider name"):
        OpenAICompatProvider(
            name="  ",
            settings=_settings(),
            client=_FakeClient(result=_ok_response("검증 결과")),  # type: ignore[arg-type]
        )


def test_timeout_maps_to_llm_timeout() -> None:
    provider = _provider(error=APITimeoutError(request=_REQ))
    with pytest.raises(LlmTimeout):
        _run(provider.complete(_request(), _context()))


def test_connection_error_maps_to_unavailable() -> None:
    provider = _provider(error=APIConnectionError(message="down", request=_REQ))
    with pytest.raises(LlmUnavailable):
        _run(provider.complete(_request(), _context()))


def test_server_5xx_maps_to_unavailable() -> None:
    err = APIStatusError("boom", response=httpx.Response(503, request=_REQ), body=None)
    provider = _provider(error=err)
    with pytest.raises(LlmUnavailable):
        _run(provider.complete(_request(), _context()))


def test_client_4xx_maps_to_llm_error_not_unavailable() -> None:
    """4xx는 서버 장애가 아니라 요청 거부 — 공통 LlmError로 감싼다(재시도 대상 아님)."""
    err = AuthenticationError(
        "bad key", response=httpx.Response(401, request=_REQ), body=None
    )
    provider = _provider(error=err)
    with pytest.raises(LlmError) as exc:
        _run(provider.complete(_request(), _context()))
    assert not isinstance(exc.value, (LlmUnavailable, LlmTimeout))


def test_rate_limit_429_maps_to_unavailable_so_it_is_retried() -> None:
    """🔴 **429는 4xx지만 재시도 대상이다**(8/6 신설).

    종전에는 5xx만 `LlmUnavailable`이고 나머지 4xx는 전부 `LlmError`(무재시도)였다.
    로컬 서버는 rate limit이 없어 문제가 안 됐지만 외부 API는 평가셋 연속 호출에서 429를
    맞는다 — 그때 무재시도로 떨어지면 **일시적 제한이 영구 실패처럼** 폴백된다.
    게이트웨이는 `LlmUnavailable`·`LlmTimeout`만 재시도하므로(B 소유 · 무접촉) 판정을
    **어댑터에서** 바꾼다.
    """
    err = APIStatusError("slow down", response=httpx.Response(429, request=_REQ), body=None)
    provider = _provider(error=err)
    with pytest.raises(LlmUnavailable):
        _run(provider.complete(_request(), _context()))


@pytest.mark.parametrize("status", [400, 401, 403, 404])
def test_other_4xx_stay_non_retryable(status: int) -> None:
    """⚠ 429 외의 4xx는 **그대로 둔다** — 재시도해도 안 풀리는 요청 문제다."""
    err = APIStatusError("nope", response=httpx.Response(status, request=_REQ), body=None)
    provider = _provider(error=err)
    with pytest.raises(LlmError) as exc:
        _run(provider.complete(_request(), _context()))
    assert not isinstance(exc.value, (LlmUnavailable, LlmTimeout))


def test_cost_usd_is_zero_and_that_means_unmeasured() -> None:
    """🔴 `cost_usd=0.0`은 **미측정**이지 "원가 없음"이 아니다(8/6 · 99 ⓠ).

    종전 근거는 로컬 서버라 토큰당 과금이 없다는 것이었는데, 외부 유료 백엔드로 바뀌면 그
    전제가 깨진다. 단가표는 만들지 않았다 — 모델·시점 종속이라 유지 부담이고 지금 쓰이지
    않는다. **이 값을 합산해 "LLM 원가 0원" 리포트를 내면 안 된다.**

    ⚠ 값 자체는 그대로 0.0이므로 동작 단정만으로는 의미 변화를 못 잡는다 — 그래서 주석에
    "미측정"이 남아 있는지 함께 본다(소스 문자열 검사는 이 한 방향만: 낡은 문구의 부재를
    검사하면 이 docstring이 스스로 걸린다).
    """
    import inspect

    from ai.llm.providers import openai_compat

    provider = _provider(result=_ok_response("답변"))
    result = _run(provider.complete(_request(), _context()))
    assert result.usage.cost_usd == 0.0

    source = inspect.getsource(openai_compat.OpenAICompatProvider.complete)
    assert "미측정" in source, "cost_usd=0.0의 의미(미측정)가 주석에서 사라졌다"


def test_empty_content_maps_to_retryable_parse_failed() -> None:
    """빈 응답(None) = 재시도 대상 → ParseFailed(게이트웨이가 재호출)."""
    provider = _provider(result=_ok_response(None))
    with pytest.raises(ParseFailed):
        _run(provider.complete(_request(), _context()))


def test_whitespace_content_maps_to_parse_failed() -> None:
    provider = _provider(result=_ok_response("   \n  "))
    with pytest.raises(ParseFailed):
        _run(provider.complete(_request(), _context()))


def test_generation_params_passed_through_interface_path() -> None:
    """temperature 등은 GenerationParams 경로로만 전달 — 미지정 temperature는 provider 기본값."""
    params = GenerationParams(temperature=0.2, top_p=0.9, max_tokens=256, seed=7)
    provider = _provider(result=_ok_response("ok"))
    _run(provider.complete(_request(params), _context()))
    kwargs = provider._client.chat.completions.last_kwargs  # type: ignore[attr-defined]
    assert kwargs is not None
    assert kwargs["model"] == "test-model"
    assert kwargs["temperature"] == 0.2
    assert kwargs["top_p"] == 0.9
    assert kwargs["seed"] == 7
    assert kwargs["messages"] == [{"role": "user", "content": "[마스킹 통과 프롬프트]"}]


def test_max_tokens_is_sent_as_max_completion_tokens() -> None:
    """🔴 **계약 `max_tokens` → 벤더 키 `max_completion_tokens`**(8/6).

    `gpt-5.4-mini`가 구 키를 거부한다 — 8/6 1차 실측에서 브리핑 21/21이 이 400으로 죽었다::

        Unsupported parameter: 'max_tokens' is not supported with this model.
        Use 'max_completion_tokens' instead.

    ⚠ 계약 필드명(`GenerationParams.max_tokens`)은 **바꾸지 않았다** — 벤더 규격 번역은
    어댑터의 일이다(모듈 docstring "벤더 독립 경계"). 그래서 이 테스트가 **양쪽을 동시에**
    고정한다: 계약은 `max_tokens`로 받고 벤더에는 `max_completion_tokens`로 나간다.
    """
    provider = _provider(result=_ok_response("ok"))
    _run(provider.complete(_request(GenerationParams(max_tokens=128)), _context()))
    kwargs = provider._client.chat.completions.last_kwargs  # type: ignore[attr-defined]

    assert kwargs["max_completion_tokens"] == 128
    assert "max_tokens" not in kwargs, "구 키로 되돌아갔다 — 전 호출이 400이 된다"


def test_no_token_cap_sends_neither_key() -> None:
    """상한 미지정이면 어느 키도 안 나간다 — 8/6에 상담 경로가 400을 피한 이유다(99 ⓧ)."""
    provider = _provider(result=_ok_response("ok"))
    _run(provider.complete(_request(GenerationParams(temperature=0.0)), _context()))
    kwargs = provider._client.chat.completions.last_kwargs  # type: ignore[attr-defined]

    assert "max_completion_tokens" not in kwargs
    assert "max_tokens" not in kwargs


def test_default_temperature_when_params_absent() -> None:
    provider = _provider(result=_ok_response("ok"))
    _run(provider.complete(_request(), _context()))
    kwargs = provider._client.chat.completions.last_kwargs  # type: ignore[attr-defined]
    assert kwargs is not None
    assert kwargs["temperature"] == 0.7  # provider 기본값 상수
    assert "top_p" not in kwargs  # 미지정은 서버 기본값에 맡김


def test_vendor_extra_body_is_not_sent_by_default() -> None:
    """🔴 **벤더 확장은 기본으로 보내지 않는다**(8/6 기본값 반전).

    `chat_template_kwargs`는 표준 OpenAI 파라미터가 아니라 vLLM/Qwen chat_template 확장이다.
    기본으로 보내면 그 확장을 모르는 표준 API가 **400 `Unknown parameter`로 거부**한다(실측).
    """
    provider = _provider(result=_ok_response("ok"))
    _run(provider.complete(_request(), _context()))
    kwargs = provider._client.chat.completions.last_kwargs  # type: ignore[attr-defined]
    assert kwargs is not None
    assert "extra_body" not in kwargs


def test_default_timeout_is_total_15s() -> None:
    """기본 상한은 15s(전체 기준) — v2 프리뷰 실측 반영으로 10s에서 상향."""
    assert OpenAiSettings().openai_timeout_s == 15.0


def test_settings_surface_is_openai_only() -> None:
    """폐기된 로컬 서버 설정을 다시 실행 선택지로 열지 않는다."""
    assert set(OpenAiSettings.model_fields) == {
        "openai_api_key",
        "openai_model",
        "openai_base_url",
        "openai_timeout_s",
    }
    assert OpenAICompatProvider(settings=_settings(), client=_FakeClient()).name == (
        "openai-compat"
    )


def test_real_client_disables_sdk_retries() -> None:
    """실 클라이언트는 SDK 내장 재시도를 끈다(무재시도 확정) — 네트워크 없이 생성만."""
    provider = OpenAICompatProvider(settings=_settings())
    assert provider._client.max_retries == 0


class _SlowCompletions:
    """create가 전체 상한보다 오래 걸리는 fake — asyncio.timeout 강제 검증용."""

    async def create(self, **kwargs: object) -> object:
        await asyncio.sleep(1.0)
        return _ok_response("느린 응답")


def test_total_timeout_maps_to_llm_timeout() -> None:
    """호출이 전체 상한을 넘기면 asyncio.timeout이 끊어 LlmTimeout으로 매핑된다."""
    settings = OpenAiSettings.model_validate(
        {
            "openai_base_url": "http://local/v1",
            "openai_api_key": "k",
            "openai_model": "test-model",
            "openai_timeout_s": 0.05,
        }
    )
    client = SimpleNamespace(chat=SimpleNamespace(completions=_SlowCompletions()))
    provider = OpenAICompatProvider(settings=settings, client=client)  # type: ignore[arg-type]
    with pytest.raises(LlmTimeout):
        _run(provider.complete(_request(), _context()))


def test_settings_injection_overrides_model() -> None:
    settings = OpenAiSettings(openai_model="other-model")
    provider = OpenAICompatProvider(
        settings=settings,
        client=_FakeClient(result=_ok_response("ok")),  # type: ignore[arg-type]
    )
    result = _run(provider.complete(_request(), _context()))
    assert result.model == "other-model"
