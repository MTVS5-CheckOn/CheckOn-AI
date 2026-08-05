"""문제출제 provider 역할 분리와 게이트웨이 조립 계약."""

from __future__ import annotations

import pytest
from pydantic import SecretStr, ValidationError

from ai.contracts.llm import ModelRole
from ai.llm.providers.openai_compat import LocalLlmSettings, OpenAICompatProvider
from ai.problem_generation.infrastructure.config import load_verify_config
from ai.problem_generation.provider import (
    EXTERNAL_VERIFIER_PROVIDER_NAME,
    LOCAL_GENERATOR_PROVIDER_NAME,
    LOCAL_VERIFIER_PROVIDER_NAME,
    ProblemProviderSettings,
    build_problem_gateway,
    build_problem_providers,
)


def _local_settings() -> LocalLlmSettings:
    return LocalLlmSettings.model_validate(
        {
            "local_llm_base_url": "http://local.test/v1",
            "local_llm_api_key": "local-secret",
            "local_llm_model": "local-model",
        }
    )


def _ignore_record(*_: object) -> None:
    return None


def test_local_fallback_builds_distinct_role_providers() -> None:
    providers = build_problem_providers(
        settings=ProblemProviderSettings.model_validate({}),
        local_settings=_local_settings(),
    )

    assert providers.generator.name == LOCAL_GENERATOR_PROVIDER_NAME
    assert providers.verifier.name == LOCAL_VERIFIER_PROVIDER_NAME
    assert providers.generator.name != providers.verifier.name
    assert providers.generator is not providers.verifier
    assert not providers.has_dedicated_verifier


def test_dedicated_verifier_uses_external_settings() -> None:
    providers = build_problem_providers(
        settings=ProblemProviderSettings.model_validate(
            {
                "openai_base_url": "https://api.openai.test/v1",
                "openai_api_key": SecretStr("external-secret"),
                "openai_model": "gpt-test",
                "openai_timeout_s": 23.0,
            }
        ),
        local_settings=_local_settings(),
    )

    verifier = providers.verifier
    assert isinstance(verifier, OpenAICompatProvider)
    assert verifier.name == EXTERNAL_VERIFIER_PROVIDER_NAME
    assert verifier._settings.local_llm_base_url == "https://api.openai.test/v1"
    assert verifier._settings.local_llm_model == "gpt-test"
    assert verifier._settings.local_llm_timeout_s == 23.0
    assert verifier._settings.local_llm_disable_thinking is False
    assert providers.has_dedicated_verifier


def test_dedicated_verifier_is_selected_from_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.openai.test/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "external-secret")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-env-test")
    monkeypatch.setenv("OPENAI_TIMEOUT_S", "19")

    settings = ProblemProviderSettings(_env_file=None)
    providers = build_problem_providers(
        settings=settings,
        local_settings=_local_settings(),
    )

    assert settings.has_dedicated_verifier
    assert providers.has_dedicated_verifier
    assert providers.verifier.name == EXTERNAL_VERIFIER_PROVIDER_NAME
    assert isinstance(providers.verifier, OpenAICompatProvider)
    assert providers.verifier._settings.local_llm_model == "gpt-env-test"
    assert providers.verifier._settings.local_llm_timeout_s == 19.0


def test_partial_dedicated_verifier_settings_fail_closed() -> None:
    with pytest.raises(ValidationError, match="OPENAI_MODEL"):
        ProblemProviderSettings.model_validate(
            {
                "openai_base_url": "https://api.openai.test/v1",
                "openai_api_key": SecretStr("external-secret"),
            }
        )


@pytest.mark.parametrize("transport_retry", [0, 1])
def test_gateway_uses_verify_config_transport_retry(transport_retry: int) -> None:
    verify_config = load_verify_config().model_copy(
        update={"transport_retry": transport_retry}
    )
    gateway = build_problem_gateway(
        verify_config=verify_config,
        recorder=_ignore_record,
        provider_settings=ProblemProviderSettings.model_validate({}),
        local_settings=_local_settings(),
    )

    expected_attempts = transport_retry + 1
    assert gateway._attempts_for(ModelRole.GENERATOR) == expected_attempts
    assert gateway._attempts_for(ModelRole.VERIFIER) == expected_attempts
    assert gateway._attempts_for(ModelRole.NARRATOR) == 2


def test_invalid_verify_config_transport_retry_fails_during_assembly() -> None:
    invalid = load_verify_config().model_copy(update={"transport_retry": 2})

    with pytest.raises(ValueError, match="0..1"):
        build_problem_gateway(
            verify_config=invalid,
            recorder=_ignore_record,
            provider_settings=ProblemProviderSettings.model_validate({}),
            local_settings=_local_settings(),
        )
