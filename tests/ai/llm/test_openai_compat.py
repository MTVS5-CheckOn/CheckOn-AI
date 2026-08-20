"""OpenAI 호환 provider 어댑터 단위 검사 — 오류 매핑·설정 주입·빈 응답 (B-5 어댑터).

실서버 없이 fake client를 주입해 결정론화한다(스모크는 integration 마커 별도).
검증 초점: SDK 예외가 밖으로 새지 않고 전부 계약 예외(Llm*)로 매핑되는가 ·
빈 응답이 재시도 대상(ParseFailed)인가 · 파라미터가 인터페이스 경로로만 전달되는가.
"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Coroutine
from types import SimpleNamespace
from typing import Final
from uuid import UUID

import httpx
import pytest
from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AsyncOpenAI,
    AuthenticationError,
)

from ai.composition.briefing import BRIEF_GEN_PARAMS
from ai.composition.classify.classifier import CLASSIFY_GEN_PARAMS
from ai.composition.counsel.provider import COUNSEL_GEN_PARAMS
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
from ai.llm.determinism import LLM_SEED, deterministic_params
from ai.llm.providers.openai_compat import (
    PROVIDER_NAME,
    OpenAICompatProvider,
    OpenAiSettings,
    build_openai_compat_provider,
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


class _FakeClient(AsyncOpenAI):
    """SDK 클라이언트 주입 경계를 만족하는 네트워크 없는 테스트 대역."""

    def __init__(self, *, result: object = None, error: Exception | None = None) -> None:
        self.fake_completions = _FakeCompletions(result=result, error=error)
        object.__setattr__(
            self,
            "chat",
            SimpleNamespace(completions=self.fake_completions),
        )


def _ok_response(content: str | None) -> SimpleNamespace:
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
        usage=SimpleNamespace(prompt_tokens=12, completion_tokens=7),
    )


def _provider(*, result: object = None, error: Exception | None = None) -> OpenAICompatProvider:
    client = _FakeClient(result=result, error=error)
    return OpenAICompatProvider(settings=_settings(), client=client)


def _fake_client(provider: OpenAICompatProvider) -> _FakeClient:
    assert isinstance(provider._client, _FakeClient)
    return provider._client


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
        client=_FakeClient(result=_ok_response("검증 결과")),
    )

    result = _run(provider.complete(_request(), _context()))

    assert provider.name == "openai-verifier"
    assert result.provider == "openai-verifier"


def test_blank_injected_name_is_rejected() -> None:
    with pytest.raises(ValueError, match="provider name"):
        OpenAICompatProvider(
            name="  ",
            settings=_settings(),
            client=_FakeClient(result=_ok_response("검증 결과")),
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
    kwargs = _fake_client(provider).fake_completions.last_kwargs
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
    kwargs = _fake_client(provider).fake_completions.last_kwargs

    assert kwargs is not None
    assert kwargs["max_completion_tokens"] == 128
    assert "max_tokens" not in kwargs, "구 키로 되돌아갔다 — 전 호출이 400이 된다"


def test_no_token_cap_sends_neither_key() -> None:
    """상한 미지정이면 어느 키도 안 나간다 — 8/6에 상담 경로가 400을 피한 이유다(99 ⓧ)."""
    provider = _provider(result=_ok_response("ok"))
    _run(provider.complete(_request(GenerationParams(temperature=0.0)), _context()))
    kwargs = _fake_client(provider).fake_completions.last_kwargs

    assert kwargs is not None
    assert "max_completion_tokens" not in kwargs
    assert "max_tokens" not in kwargs


def test_absent_params_send_no_sampling_keys() -> None:
    """🔴 **(8/13) 뒤집은 검사다** — 종전 이름은 `test_default_temperature_when_params_absent`.

    **무엇을 지키려던 검사였나:** *"`GenerationParams` 없이 호출해도 온도는 정해진다"* —
    어댑터가 `_DEFAULT_TEMPERATURE = 0.7`로 미지정을 메워, 호출자가 잊어도 샘플링이
    서버 기본에 흔들리지 않는다는 보장이었다.

    **왜 뒤집었나:** 그 폴백 때문에 **temperature만 뺄 수가 없었다.** `gpt-5.6-luna`가
    기본값 외 값을 400으로 거부하자(99 #51) 흡수할 자리가 없어졌다 — 보장이 그대로
    막다른 길이 됐다. 이제 미지정은 **서버 기본값에 맡긴다**(`top_p`가 원래 그랬듯이).

    ⚠ **되돌리지 마라.** "원래 0.7이었는데 왜 없지"로 폴백을 되살리면 전 경로가 다시 400이다.
    """
    kwargs = _sent_kwargs(None)
    assert "temperature" not in kwargs, "폴백 상수가 되살아났다 — 전 경로가 400이 된다"
    assert "top_p" not in kwargs  # 미지정은 서버 기본값에 맡김
    assert "seed" not in kwargs
    assert "max_completion_tokens" not in kwargs


# ── nullable 파라미터의 「값이 없으면 안 보낸다」 규칙 ────────────────────


#: 계약 필드 → 벤더 파라미터 키. **1:1이 아니다** — 이름 흡수가 어댑터의 일이다
#: (`max_tokens` → `max_completion_tokens`, 8/6).
_VENDOR_KEY: Final[dict[str, str]] = {
    "temperature": "temperature",
    "top_p": "top_p",
    "max_tokens": "max_completion_tokens",
    "seed": "seed",
}

#: 각 필드에 넣어 볼 유효값 — 계약의 제약(ge/le/gt)을 통과하는 값이어야 한다.
_A_VALUE: Final[dict[str, float | int]] = {
    "temperature": 0.2,
    "top_p": 0.9,
    "max_tokens": 256,
    "seed": 7,
}


#: 🔴 라우터가 `to_run_metadata(generation_params=...)`로 **원장에 넘기는** 상수들
#: (`api/routers/detect.py`·`classify.py`·`counsel.py` 실측 2026-08-13).
_LEDGER_RECORDED_PARAMS: Final[tuple[tuple[str, GenerationParams], ...]] = (
    ("BRIEF_GEN_PARAMS", BRIEF_GEN_PARAMS),
    ("COUNSEL_GEN_PARAMS", COUNSEL_GEN_PARAMS),
    ("CLASSIFY_GEN_PARAMS", CLASSIFY_GEN_PARAMS),
)


def _sent_kwargs(params: GenerationParams | None) -> dict[str, object]:
    """어댑터를 **실제로 태워** 전선에 나가는 kwargs를 얻는다."""
    provider = _provider(result=_ok_response("ok"))
    _run(provider.complete(_request(params), _context()))
    kwargs = _fake_client(provider).fake_completions.last_kwargs
    assert kwargs is not None
    return dict(kwargs)


def test_every_nullable_param_follows_the_same_omit_when_unset_rule() -> None:
    """🔴 **네 파라미터가 같은 규칙인가** — 「값이 없으면 키를 안 만든다」.

    ⚠ **「temperature가 kwargs에 없나」를 묻는 검사가 아니다.** 그건 수정의 동어반복이고,
    다음에 벤더가 **다른** 파라미터를 좁히면 또 못 잡는다. 이 검사가 무는 것은
    *"계약의 nullable 필드 전부가 하나의 규칙을 따르는가"* 다.

    🔴 **8/13 이전에는 red다** — `temperature`만 `_DEFAULT_TEMPERATURE = 0.7` 폴백이 있어
    값이 없어도 전선에 나갔다. 폴백 상수가 있으면 **그 파라미터는 영영 뺄 수 없고**,
    벤더가 허용값을 좁히는 날(`gpt-5.6-luna`의 `temperature` 400) 흡수할 방법이 없다.

    ⚠ **이 검사는 시간으로 만료하지 않는다.** `GenerationParams`에 nullable 필드가 늘면
    아래 절단 가드가 red를 내고, **사람이 `_VENDOR_KEY`에 손으로 더해야** green이 돌아온다.
    안 더하면 그 파라미터만 규칙이 어긋난 채 통과한다 — 그게 이 안건의 재발 경로다.
    """
    fields = tuple(GenerationParams.model_fields)

    # ── 절단 가드 ──
    assert fields, "🔴 규칙 축을 잃었다 — 검사 대상이 0건이면 이 검사는 아무것도 안 문다"
    assert set(_VENDOR_KEY) == set(fields), (
        "🔴 계약과 검사 목록이 갈렸다 — `GenerationParams`에 필드가 늘거나 줄었다. "
        f"계약={sorted(fields)} 검사={sorted(_VENDOR_KEY)}. "
        "목록을 리터럴로 박아 두면 새 파라미터만 규칙이 어긋난 채 통과한다."
    )
    assert set(_A_VALUE) == set(fields), "유효값 표가 계약과 갈렸다"

    # ① 전부 미지정 → 벤더 키가 **하나도** 안 나간다.
    unset = _sent_kwargs(GenerationParams())
    for name in fields:
        assert _VENDOR_KEY[name] not in unset, (
            f"{name}: 값이 없는데 전선에 나갔다 — 폴백 상수가 있으면 뺄 수 없다"
        )

    # ② 하나만 지정 → **그 키만** 나간다(명시 지정은 그대로 실린다).
    for name in fields:
        sent = _sent_kwargs(GenerationParams(**{name: _A_VALUE[name]}))
        assert sent[_VENDOR_KEY[name]] == _A_VALUE[name], f"{name}: 명시 지정이 사라졌다"
        for other in fields:
            if other != name:
                assert _VENDOR_KEY[other] not in sent, (
                    f"{name}만 지정했는데 {other}가 따라 나갔다"
                )


def test_deterministic_params_sends_seed_but_not_temperature() -> None:
    """🔴 재현 축의 정본은 **seed**다 — 어댑터까지 태워서 묻는다.

    ⚠ `deterministic_params()`의 **필드만** 보면 어댑터의 폴백을 못 본다 — 필드가 None이어도
    어댑터가 0.7을 채우면 전선에는 `temperature`가 실린다. 그래서 조립 결과를 본다.

    근거는 8/4 실측이다(`composition/determinism.py` 머리말) — `temperature=0.0`인데 같은
    입력이 **다른 출력**을 냈고 원인은 `seed` 미전달이었다. **재현을 만든 것은 seed였다.**
    """
    sent = _sent_kwargs(deterministic_params(max_tokens=128))

    assert sent["seed"] == LLM_SEED
    assert sent["max_completion_tokens"] == 128
    assert "temperature" not in sent, (
        "🔴 gpt-5.6-luna가 400을 낸다 — Only the default (1) value is supported"
    )


def test_ledger_params_never_claim_a_value_the_wire_did_not_carry() -> None:
    """🔴 **원장이 거짓말하지 않는가** — 작업 C(8/13).

    `AI_RUN.generation_params`에 남는 것은 **요청값**이다 — 라우터가 `BRIEF_GEN_PARAMS`
    같은 상수를 `to_run_metadata()`에 그대로 넘긴다(실측 2026-08-13 · `api/routers/detect.py`·
    `classify.py`). **전송값(`_build_kwargs` 결과)이 아니다.** 두 축이 갈리면 원장은
    *"그 파라미터로 돌렸다"* 고 말하는데 전선은 다른 값을 나른다.

    🔴 **8/13 이전이 정확히 그랬다** — `params=None`인 실행에서 어댑터는 `0.7`을 보냈는데
    원장에는 아무것도 안 남았다. #48과 같은 형태다: 「없다」와 「이 값이다」가 뒤바뀌면
    관측이 무력화된다. **끄는 것보다 거짓말하는 것이 나쁘다.**

    ⇒ 이 검사가 두 축을 묶는다: 원장에 **null이면 전선에 없어야** 하고, 원장에 **값이 있으면
    전선에 같은 값이 있어야** 한다. 어댑터에 폴백 상수를 다시 두면 여기서 red가 난다.
    """
    for name, params in _LEDGER_RECORDED_PARAMS:
        recorded = params.model_dump(mode="json")
        sent = _sent_kwargs(params)
        assert set(recorded) == set(_VENDOR_KEY), f"{name}: 계약 필드와 갈렸다"
        for field, value in recorded.items():
            key = _VENDOR_KEY[field]
            if value is None:
                assert key not in sent, (
                    f"{name}: 원장은 {field}=null인데 전선은 {sent.get(key)!r}을 날랐다"
                    " — 원장이 거짓말한다"
                )
            else:
                assert sent[key] == value, (
                    f"{name}: 원장은 {field}={value!r}인데 전선은 {sent.get(key)!r}이다"
                )


def test_vendor_extra_body_is_not_sent_by_default() -> None:
    """🔴 **벤더 확장은 기본으로 보내지 않는다**(8/6 기본값 반전).

    `chat_template_kwargs`는 표준 OpenAI 파라미터가 아니라 vLLM/Qwen chat_template 확장이다.
    기본으로 보내면 그 확장을 모르는 표준 API가 **400 `Unknown parameter`로 거부**한다(실측).
    """
    provider = _provider(result=_ok_response("ok"))
    _run(provider.complete(_request(), _context()))
    kwargs = _fake_client(provider).fake_completions.last_kwargs
    assert kwargs is not None
    assert "extra_body" not in kwargs


def test_default_timeout_is_total_15s() -> None:
    """기본 상한은 15s(전체 기준) — v2 프리뷰 실측 반영으로 10s에서 상향.

    🔴 **`_env_file=None`으로 `.env`를 끊는다.** 종전에는 그냥 `OpenAiSettings()`였는데
    그건 **선언 기본값이 아니라 이 기기의 `.env` 값**을 읽는다 — 운영에서 타임아웃을
    올리면 이 검사가 빨개졌고, 검사 이름이 말하는 「기본값」과 보는 값이 달랐다.
    ⚠ 같은 함정을 `env_files.py`가 이미 한 번 기록했다(작업 디렉터리 의존 · 99 #73).
    """
    assert OpenAiSettings(_env_file=None).openai_timeout_s == 15.0


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


def test_provider_requires_an_injected_client() -> None:
    """provider 생성자는 실 client를 만들지 않고 주입만 받는다."""
    parameter = inspect.signature(OpenAICompatProvider).parameters["client"]
    assert parameter.default is inspect.Parameter.empty


def test_real_provider_is_blocked_without_optin(monkeypatch: pytest.MonkeyPatch) -> None:
    """실 provider를 조립해도 opt-in 없이는 SDK client 생성과 HTTP 호출이 모두 차단된다."""
    monkeypatch.delenv("CHECKON_ALLOW_REAL_LLM", raising=False)
    provider = build_openai_compat_provider(settings=_settings())
    with pytest.raises(LlmUnavailable, match="CHECKON_ALLOW_REAL_LLM"):
        _run(provider.complete(_request(), _context()))


def test_real_client_disables_sdk_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    """실 클라이언트는 SDK 내장 재시도를 끈다(무재시도 확정) — 네트워크 없이 생성만."""
    monkeypatch.setenv("CHECKON_ALLOW_REAL_LLM", "1")
    provider = build_openai_compat_provider(settings=_settings())
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
        client=_FakeClient(result=_ok_response("ok")),
    )
    result = _run(provider.complete(_request(), _context()))
    assert result.model == "other-model"
