"""문제출제 LLM provider 선택과 역할별 게이트웨이 조립."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Self

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from ai.contracts.llm import LLMProvider, ModelRole
from ai.llm.gateway import LlmCallRecorder, LlmGateway
from ai.llm.providers.openai_compat import (
    OpenAiSettings,
    build_openai_compat_provider,
    get_llm_settings,
)
from ai.llm.settings import LlmSettings
from ai.problem_generation.domain.policy import VerifyConfig
from ai.runtime.env_files import ENV_FILES
from ai.runtime.trace_masking import RedactionTripwireTraceHook

LOCAL_GENERATOR_PROVIDER_NAME = "pg-local-generator"
LOCAL_VERIFIER_PROVIDER_NAME = "pg-local-verifier"
EXTERNAL_VERIFIER_PROVIDER_NAME = "pg-openai-verifier"


class ProblemProviderSettings(BaseSettings):
    """문제출제 verifier 전용 OpenAI 호환 접속 설정."""

    model_config = SettingsConfigDict(env_file=ENV_FILES, extra="ignore")

    openai_base_url: str | None = None
    openai_api_key: SecretStr | None = None
    openai_model: str | None = None
    openai_timeout_s: float = Field(default=15.0, gt=0)

    @field_validator(
        "openai_base_url",
        "openai_api_key",
        "openai_model",
        mode="before",
    )
    @classmethod
    def normalize_optional_text(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @model_validator(mode="after")
    def validate_dedicated_verifier(self) -> Self:
        required = {
            "OPENAI_BASE_URL": self.openai_base_url,
            "OPENAI_API_KEY": self.openai_api_key,
            "OPENAI_MODEL": self.openai_model,
        }
        configured = {name for name, value in required.items() if value is not None}
        if configured and len(configured) != len(required):
            missing = ", ".join(sorted(set(required) - configured))
            raise ValueError(f"verifier LLM 설정은 함께 주입해야 한다. 누락: {missing}")
        return self

    @property
    def has_dedicated_verifier(self) -> bool:
        return self.openai_base_url is not None


@lru_cache
def get_problem_provider_settings() -> ProblemProviderSettings:
    """환경변수를 한 번 읽어 문제출제 provider 설정으로 반환한다."""

    return ProblemProviderSettings()


@dataclass(frozen=True, slots=True)
class ProblemProviders:
    """generator·verifier 역할에 배정할 provider 묶음."""

    generator: LLMProvider
    verifier: LLMProvider
    has_dedicated_verifier: bool


def build_problem_providers(
    *,
    settings: ProblemProviderSettings | None = None,
    local_settings: OpenAiSettings | None = None,
) -> ProblemProviders:
    """두 역할 provider를 조립한다.

    verifier 전용 env 3종이 모두 있으면 별도 OpenAI 호환 endpoint를 사용한다.
    아직 없다면 동일한 로컬 설정으로 별도 인스턴스를 만들되 역할 식별자는 분리한다.
    """

    resolved = settings or get_problem_provider_settings()
    local = local_settings or get_llm_settings()
    generator = build_openai_compat_provider(
        name=LOCAL_GENERATOR_PROVIDER_NAME,
        settings=local,
    )
    if not resolved.has_dedicated_verifier:
        return ProblemProviders(
            generator=generator,
            verifier=build_openai_compat_provider(
                name=LOCAL_VERIFIER_PROVIDER_NAME,
                settings=local,
            ),
            has_dedicated_verifier=False,
        )

    assert resolved.openai_base_url is not None
    assert resolved.openai_api_key is not None
    assert resolved.openai_model is not None
    #: 🔴 `SecretStr` 을 그대로 넘긴다 — 여기서 `.get_secret_value()` 로 풀면 받는 쪽이
    #:  다시 `SecretStr` 로 감싸는 동안 **평문 `str` 이 한 번 생긴다**(99 #122).
    #:  푸는 자리는 클라이언트 생성 한 곳(`build_openai_compat_provider`)으로만 좁힌다.
    verifier_settings = OpenAiSettings(
        openai_base_url=resolved.openai_base_url,
        openai_api_key=resolved.openai_api_key,
        openai_model=resolved.openai_model,
        openai_timeout_s=resolved.openai_timeout_s,
        _env_file=None,
    )
    return ProblemProviders(
        generator=generator,
        verifier=build_openai_compat_provider(
            name=EXTERNAL_VERIFIER_PROVIDER_NAME,
            settings=verifier_settings,
        ),
        has_dedicated_verifier=True,
    )


def build_problem_gateway(
    *,
    verify_config: VerifyConfig,
    recorder: LlmCallRecorder,
    providers: ProblemProviders | None = None,
    provider_settings: ProblemProviderSettings | None = None,
    local_settings: OpenAiSettings | None = None,
    llm_settings: LlmSettings | None = None,
) -> LlmGateway:
    """문제출제 role·재시도·마스킹 훅을 단일 지점에서 게이트웨이에 주입한다.

    ``llm_settings``는 기존 gateway 생성자 호환용으로 전달할 뿐 추적 활성 판정에는
    관여하지 않는다. 추적 판정의 정본은 ``external_tracing_active()``다.
    """

    resolved_providers = providers or build_problem_providers(
        settings=provider_settings,
        local_settings=local_settings,
    )
    return LlmGateway(
        {
            ModelRole.GENERATOR: resolved_providers.generator,
            ModelRole.VERIFIER: resolved_providers.verifier,
        },
        recorder=recorder,
        transport_retry={
            ModelRole.GENERATOR: verify_config.transport_retry,
            ModelRole.VERIFIER: verify_config.transport_retry,
        },
        trace_masking_hook=RedactionTripwireTraceHook(),
        settings=llm_settings,
    )


__all__ = [
    "EXTERNAL_VERIFIER_PROVIDER_NAME",
    "LOCAL_GENERATOR_PROVIDER_NAME",
    "LOCAL_VERIFIER_PROVIDER_NAME",
    "ProblemProviderSettings",
    "ProblemProviders",
    "build_problem_gateway",
    "build_problem_providers",
    "get_problem_provider_settings",
]
