"""로컬 PR 전 검증기가 skip이나 검사 축을 조용히 잃지 않는지 확인한다."""

from __future__ import annotations

import socket
from pathlib import Path
from unittest.mock import MagicMock

import psycopg
import pytest
from sqlalchemy.engine import make_url

from ai.db.settings import DbSettings
from ai.evaluation import pre_pr_verify
from ai.evaluation.pre_pr_verify import (
    PrePrVerificationError,
    assert_only_real_llm_tests_skipped,
    require_postgres,
    verification_environment,
    verification_steps,
)

_EXPECTED_SKIPS = (
    ("tests.ai.integration.test_briefing_smoke", "test_real_provider_single_brief"),
    ("tests.ai.integration.test_llm_smoke", "test_openai_single_roundtrip"),
    (
        "tests.ai.integration.test_pg_real_llm_smoke",
        "test_t1_problem_generation_real_llm_roundtrip",
    ),
    #: 🔴 **(8/19) 상담 축 실측 장치** — `tone_map` 24조합 스윕(99 #106·#107·#108 의 출처).
    #: ⚠ **여기와 `pre_pr_verify._REAL_LLM_SKIPS` 두 곳을 다 고쳐야 한다** — 한 곳에서
    #: 파생시키지 않는 것이 **의도**다(새 실 LLM 검사를 늘릴 때 **두 번 멈춰 생각하게**
    #: 만든다). 실제로 이 회차에 그 게이트가 먼저 막았다.
    (
        "tests.ai.integration.test_counsel_tone_smoke",
        "test_b1_the_twenty_four_tone_combinations",
    ),
    (
        "tests.ai.integration.test_counsel_tone_smoke",
        "test_b2_reproducibility_of_one_combination",
    ),
    #: 🔴 **(8/20) 2차 회차** — B군 빈도 축(#79 판정 재료).
    (
        "tests.ai.integration.test_counsel_tone_smoke",
        "test_c1_frequency_sample_of_thirty",
    ),
)


def _write_junit(path: Path, skipped: tuple[tuple[str, str], ...]) -> None:
    cases = "".join(
        f'<testcase classname="{module}" name="{name}"><skipped/></testcase>'
        for module, name in skipped
    )
    path.write_text(
        f"<testsuites><testsuite>{cases}</testsuite></testsuites>",
        encoding="utf-8",
    )


def test_verification_runs_every_former_ci_axis(tmp_path: Path) -> None:
    steps = verification_steps(tmp_path / "integration.xml")

    assert [step.label for step in steps] == [
        "Ruff",
        "Mypy",
        "Pytest (offline)",
        "Pytest (PostgreSQL integration)",
    ]
    commands = tuple(part for step in steps for part in step.command)
    assert "--no-cache" in commands
    assert "--no-incremental" in commands
    assert "integration" in commands


def test_exact_real_llm_skip_set_is_accepted(tmp_path: Path) -> None:
    junit_path = tmp_path / "integration.xml"
    _write_junit(junit_path, _EXPECTED_SKIPS)

    assert_only_real_llm_tests_skipped(junit_path)


@pytest.mark.parametrize(
    "skipped",
    [
        _EXPECTED_SKIPS
        + (("tests.ai.integration.test_pg_store_roundtrip", "test_pg_roundtrip"),),
        _EXPECTED_SKIPS[:-1],
    ],
)
def test_unexpected_or_missing_skip_is_rejected(
    tmp_path: Path,
    skipped: tuple[tuple[str, str], ...],
) -> None:
    junit_path = tmp_path / "integration.xml"
    _write_junit(junit_path, skipped)

    with pytest.raises(PrePrVerificationError, match="skip 계약 불일치"):
        assert_only_real_llm_tests_skipped(junit_path)


@pytest.mark.parametrize(
    "database_url",
    [
        "postgresql+asyncpg://checkon:checkon@db.example.com/checkon_ai",
        "postgresql+asyncpg://checkon:checkon@localhost/checkon_prod",
        "postgresql+asyncpg://checkon:checkon@localhost:5434/checkon_ai",
        "postgresql+asyncpg://checkon:checkon@db.example.com/checkon_ai_test",
    ],
)
def test_remote_or_non_verification_database_is_rejected(database_url: str) -> None:
    with pytest.raises(PrePrVerificationError, match="원격·공용 DB를 거부"):
        require_postgres(database_url)


def test_settings_isolate_test_database_without_changing_deployment_url() -> None:
    deployed = "postgresql+asyncpg://checkon:canary@localhost:5434/checkon_ai"
    settings = DbSettings(_env_file=None, database_url=deployed, test_database_url=None)

    assert settings.database_url == deployed
    assert make_url(settings.verification_database_url) == make_url(deployed).set(
        database="checkon_ai_test"
    )
    assert "canary" not in repr(settings)


@pytest.mark.parametrize("host", ["localhost", "127.0.0.1", "[::1]"])
def test_local_test_database_is_accepted(host: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(socket, "create_connection", MagicMock())
    connect = MagicMock()
    monkeypatch.setattr(psycopg, "connect", connect)

    require_postgres(f"postgresql+asyncpg://checkon@{host}:5434/checkon_ai_test")

    connect.assert_called_once()


@pytest.mark.parametrize("field", ["test_database_url", "agent_checkpoint_database_url"])
def test_gate_rejects_deployment_database_before_any_subprocess(
    field: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    settings = DbSettings(_env_file=None).model_copy(
        update={
            "test_database_url": "postgresql+asyncpg://localhost/checkon_ai_test",
            "agent_checkpoint_database_url": None,
            field: "postgresql+asyncpg://localhost:5434/checkon_ai",
        }
    )
    monkeypatch.setattr(pre_pr_verify, "DbSettings", lambda: settings)
    monkeypatch.setattr(pre_pr_verify, "real_llm_optin", lambda: False)
    monkeypatch.setattr(socket, "create_connection", MagicMock())
    monkeypatch.setattr(psycopg, "connect", MagicMock())
    run = MagicMock()
    monkeypatch.setattr(pre_pr_verify, "_run", run)

    assert pre_pr_verify.main() == 1

    run.assert_not_called()
    assert "2026-08-27 배포 DB checkon_ai의 산출물 소실" in capsys.readouterr().err


def test_test_database_and_checkpoint_are_inherited_by_children(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    test_url = "postgresql+asyncpg://checkon:canary@localhost:5434/checkon_ai_test"
    settings = DbSettings(
        _env_file=None, test_database_url=test_url, agent_checkpoint_database_url=None
    )
    require = MagicMock()
    monkeypatch.setattr(pre_pr_verify, "require_postgres", require)
    original = {"DATABASE_URL": "deployment", "AGENT_CHECKPOINT_DATABASE_URL": "deployment"}

    env = verification_environment(settings, env=original)

    require.assert_called_once_with(test_url)
    assert env["DATABASE_URL"] == env["TEST_DATABASE_URL"] == test_url
    assert make_url(env["AGENT_CHECKPOINT_DATABASE_URL"]) == make_url(test_url).set(
        drivername="postgresql"
    )
    assert original["DATABASE_URL"] == "deployment"


def test_missing_test_database_explains_creation_without_exposing_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(socket, "create_connection", MagicMock())
    monkeypatch.setattr(
        psycopg, "connect", MagicMock(side_effect=psycopg.OperationalError("canary"))
    )
    with pytest.raises(PrePrVerificationError, match="CREATE DATABASE") as error:
        require_postgres("postgresql+asyncpg://checkon:canary@localhost/checkon_ai_test")
    assert "canary" not in str(error.value)


def test_connection_query_cannot_redirect_test_database() -> None:
    with pytest.raises(PrePrVerificationError, match="query로 덮어쓸 수 없다"):
        require_postgres("postgresql+asyncpg://localhost/checkon_ai_test?dbname=checkon_ai")
