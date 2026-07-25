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
    return LocalLlmSettings(
        local_llm_base_url="http://local/v1",
        local_llm_api_key="k",
        local_llm_model="gemma-test",
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


def test_settings_injection_overrides_model() -> None:
    settings = LocalLlmSettings(local_llm_model="other-model")
    provider = OpenAICompatProvider(
        settings=settings,
        client=_FakeClient(result=_ok_response("ok")),  # type: ignore[arg-type]
    )
    result = _run(provider.complete(_request(), _context()))
    assert result.model == "other-model"
