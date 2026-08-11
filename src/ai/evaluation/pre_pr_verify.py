"""PR을 올리기 전 로컬에서 자동 CI와 같은 범위를 검증한다.

GitHub Actions를 쓰지 않는 대신 이 명령 하나가 정적 검사, 기본 회귀, 실 PostgreSQL
통합 검사를 순서대로 실행한다. 실 LLM은 비용·외부 전송 축이므로 절대 이 검증에 섞지
않고, 대응 스모크 세 건이 명시적으로 skip됐는지까지 확인한다.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Final
from xml.etree import ElementTree

from sqlalchemy.engine import make_url

from ai.db.settings import DbSettings
from ai.runtime.real_llm import REAL_LLM_OPTIN_ENV, real_llm_optin

_REPO_ROOT: Final = Path(__file__).resolve().parents[3]
_LOCAL_DATABASE_HOSTS: Final = frozenset({"localhost", "127.0.0.1", "::1"})
_LOCAL_DATABASE_NAME: Final = "checkon_ai"
_REAL_LLM_SKIPS: Final = frozenset(
    {
        (
            "tests.ai.integration.test_briefing_smoke",
            "test_real_provider_single_brief",
        ),
        (
            "tests.ai.integration.test_llm_smoke",
            "test_local_server_single_roundtrip",
        ),
        (
            "tests.ai.integration.test_pg_real_llm_smoke",
            "test_t1_problem_generation_real_llm_roundtrip",
        ),
    }
)


class PrePrVerificationError(RuntimeError):
    """검증을 계속하거나 통과로 판정할 수 없는 상태."""


@dataclass(frozen=True, slots=True)
class VerificationStep:
    """한 검증 단계의 표시 이름과 argv."""

    label: str
    command: tuple[str, ...]


def verification_steps(junit_path: Path) -> tuple[VerificationStep, ...]:
    """자동 CI가 보던 범위를 로컬 명령으로 고정한다."""
    return (
        VerificationStep(
            "Ruff",
            ("uv", "run", "--frozen", "ruff", "check", ".", "--no-cache"),
        ),
        VerificationStep(
            "Mypy",
            ("uv", "run", "--frozen", "mypy", "--no-incremental"),
        ),
        VerificationStep(
            "Pytest (offline)",
            ("uv", "run", "--frozen", "pytest", "-p", "no:cacheprovider"),
        ),
        VerificationStep(
            "Pytest (PostgreSQL integration)",
            (
                "uv",
                "run",
                "--frozen",
                "pytest",
                "-m",
                "integration",
                "-rs",
                "-p",
                "no:cacheprovider",
                f"--junitxml={junit_path}",
            ),
        ),
    )


def skipped_test_ids(junit_path: Path) -> frozenset[tuple[str, str]]:
    """JUnit 결과에서 skip된 테스트를 (모듈, 함수)로 읽는다."""
    root = ElementTree.parse(junit_path).getroot()  # noqa: S314 - 로컬 pytest 산출물
    return frozenset(
        (case.attrib.get("classname", ""), case.attrib.get("name", ""))
        for case in root.iter("testcase")
        if case.find("skipped") is not None
    )


def assert_only_real_llm_tests_skipped(junit_path: Path) -> None:
    """실 LLM 세 건 외의 integration skip을 통과로 숨기지 않는다."""
    actual = skipped_test_ids(junit_path)
    if actual == _REAL_LLM_SKIPS:
        return
    unexpected = sorted(actual - _REAL_LLM_SKIPS)
    missing = sorted(_REAL_LLM_SKIPS - actual)
    details: list[str] = []
    if unexpected:
        details.append(f"예상 밖 skip={unexpected}")
    if missing:
        details.append(f"실 LLM 보호 skip 미확인={missing}")
    raise PrePrVerificationError("integration skip 계약 불일치: " + " · ".join(details))


def require_postgres(database_url: str) -> None:
    """폐기 가능한 로컬 PG가 없는 실행을 integration 검증으로 부르지 않는다."""
    url = make_url(database_url)
    if url.get_backend_name() != "postgresql":
        raise PrePrVerificationError("DATABASE_URL은 PostgreSQL이어야 한다")
    host = url.host or "localhost"
    if host not in _LOCAL_DATABASE_HOSTS or url.database != _LOCAL_DATABASE_NAME:
        raise PrePrVerificationError(
            "integration은 스키마를 재생성한다. 원격·공용 DB를 거부하며 "
            f"로컬 전용 {_LOCAL_DATABASE_NAME} DB만 허용한다"
        )
    port = url.port or 5432
    try:
        with socket.create_connection((host, port), timeout=3.0):
            pass
    except OSError as exc:
        raise PrePrVerificationError(
            f"PostgreSQL에 연결할 수 없다({host}:{port}). 먼저 docker compose up -d를 실행한다"
        ) from exc


def _run(step: VerificationStep, *, env: dict[str, str]) -> None:
    print(f"\n== {step.label} ==", flush=True)
    completed = subprocess.run(  # noqa: S603 - argv가 코드 상수이며 shell을 쓰지 않는다
        step.command,
        cwd=_REPO_ROOT,
        env=env,
        check=False,
    )
    if completed.returncode != 0:
        raise PrePrVerificationError(
            f"{step.label} 실패(exit={completed.returncode})"
        )


def main() -> int:
    """검증 전량 통과일 때만 0을 반환한다."""
    try:
        if real_llm_optin():
            raise PrePrVerificationError(
                f"{REAL_LLM_OPTIN_ENV}가 활성이다. PR 전 검증에서는 실 LLM 호출을 허용하지 않는다"
            )

        settings = DbSettings()
        if settings.agent_checkpoint_database_url is not None:
            require_postgres(settings.agent_checkpoint_database_url)
        env = os.environ.copy()
        env.pop(REAL_LLM_OPTIN_ENV, None)
        env.pop("AGENT_CHECKPOINT_DATABASE_URL", None)
        env["DATABASE_URL"] = settings.database_url
        env["STORE_BACKEND"] = "memory"
        env["LLM_PROVIDER"] = "fake"

        with tempfile.TemporaryDirectory(prefix="checkon-pre-pr-") as temp_dir:
            junit_path = Path(temp_dir) / "integration.xml"
            for step in verification_steps(junit_path):
                if step.label == "Pytest (PostgreSQL integration)":
                    require_postgres(settings.database_url)
                _run(step, env=env)
            assert_only_real_llm_tests_skipped(junit_path)
    except PrePrVerificationError as exc:
        print(f"\nPR 전 검증 실패: {exc}", file=sys.stderr)
        return 1

    print(
        "\nPR 전 검증 통과: ruff · mypy · offline pytest · 실 PG integration "
        "(실 LLM 보호 skip 3건)",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
