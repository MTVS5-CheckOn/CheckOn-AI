"""로컬 PR 전 검증기가 skip이나 검사 축을 조용히 잃지 않는지 확인한다."""

from __future__ import annotations

from pathlib import Path

import pytest

from ai.evaluation.pre_pr_verify import (
    PrePrVerificationError,
    assert_only_real_llm_tests_skipped,
    require_postgres,
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
    ],
)
def test_remote_or_non_verification_database_is_rejected(database_url: str) -> None:
    with pytest.raises(PrePrVerificationError, match="원격·공용 DB를 거부"):
        require_postgres(database_url)
