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
    LocalLlmSettings,
    OpenAICompatProvider,
)

_REQ = httpx.Request("POST", "http://local/v1/chat/completions")


def _run[T](coro: Coroutine[object, object, T]) -> T:
    return asyncio.run(coro)


def _settings() -> LocalLlmSettings:
    return LocalLlmSettings.model_validate(
        {
            "local_llm_base_url": "http://local/v1",
            "local_llm_api_key": "k",
            "local_llm_model": "gemma-test",
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
    assert result.model == "gemma-test"
    assert result.usage.tokens_in == 12
    assert result.usage.tokens_out == 7
    assert result.usage.cost_usd == 0.0  # 로컬 서버 원가 없음


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
    assert kwargs["model"] == "gemma-test"
    assert kwargs["temperature"] == 0.2
    assert kwargs["top_p"] == 0.9
    assert kwargs["max_tokens"] == 256
    assert kwargs["seed"] == 7
    assert kwargs["messages"] == [{"role": "user", "content": "[마스킹 통과 프롬프트]"}]


def test_default_temperature_when_params_absent() -> None:
    provider = _provider(result=_ok_response("ok"))
    _run(provider.complete(_request(), _context()))
    kwargs = provider._client.chat.completions.last_kwargs  # type: ignore[attr-defined]
    assert kwargs is not None
    assert kwargs["temperature"] == 0.7  # provider 기본값 상수
    assert "top_p" not in kwargs  # 미지정은 서버 기본값에 맡김


def test_thinking_disabled_by_default_via_extra_body() -> None:
    """기본은 추론 끔 — enable_thinking=False를 벤더 경로(extra_body)로 전달."""
    provider = _provider(result=_ok_response("ok"))
    _run(provider.complete(_request(), _context()))
    kwargs = provider._client.chat.completions.last_kwargs  # type: ignore[attr-defined]
    assert kwargs is not None
    assert kwargs["extra_body"] == {"chat_template_kwargs": {"enable_thinking": False}}


def test_thinking_toggle_off_omits_extra_body() -> None:
    """env로 추론을 켜면(disable=False) extra_body를 보내지 않는다(추론 필요 용도 대비)."""
    settings = LocalLlmSettings.model_validate(
        {
            "local_llm_base_url": "http://local/v1",
            "local_llm_api_key": "k",
            "local_llm_model": "gemma-test",
            "local_llm_disable_thinking": False,
        }
    )
    client = _FakeClient(result=_ok_response("ok"))
    provider = OpenAICompatProvider(settings=settings, client=client)  # type: ignore[arg-type]
    _run(provider.complete(_request(), _context()))
    assert "extra_body" not in client.completions.last_kwargs  # type: ignore[operator]


def test_default_timeout_is_total_15s() -> None:
    """기본 상한은 15s(전체 기준) — v2 프리뷰 실측 반영으로 10s에서 상향."""
    assert LocalLlmSettings().local_llm_timeout_s == 15.0


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
    settings = LocalLlmSettings.model_validate(
        {
            "local_llm_base_url": "http://local/v1",
            "local_llm_api_key": "k",
            "local_llm_model": "gemma-test",
            "local_llm_timeout_s": 0.05,
        }
    )
    client = SimpleNamespace(chat=SimpleNamespace(completions=_SlowCompletions()))
    provider = OpenAICompatProvider(settings=settings, client=client)  # type: ignore[arg-type]
    with pytest.raises(LlmTimeout):
        _run(provider.complete(_request(), _context()))


def test_settings_injection_overrides_model() -> None:
    settings = LocalLlmSettings(local_llm_model="other-model")
    provider = OpenAICompatProvider(
        settings=settings,
        client=_FakeClient(result=_ok_response("ok")),  # type: ignore[arg-type]
    )
    result = _run(provider.complete(_request(), _context()))
    assert result.model == "other-model"
