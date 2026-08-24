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
            "test_openai_single_roundtrip",
        ),
        (
            "tests.ai.integration.test_pg_real_llm_smoke",
            "test_t1_problem_generation_real_llm_roundtrip",
        ),
        #: 🔴 **(8/19) 상담 축 실측 장치** — `tone_map` 24조합이 실 LLM 에서 갈리는지를
        #: 재는 회차용이다(99 #106·#107·#108 이 여기서 나왔다). 옵트인 없이는 skip 이고
        #: 기본 회귀에서는 deselect 된다.
        #: ⚠ **이 목록에 넣는 것이 계약이다** — 안 넣으면 `pre_pr_verify` 가
        #: *"예상 밖 skip"* 으로 막는다. 그 게이트의 값이 **조용한 skip 을 막는 것**이라
        #: 새 실 LLM 검사는 여기 등록해야 한다(실제로 이 회차에 막혔다).
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
            (
                "uv",
                "run",
                "--frozen",
                "pytest",
                "-p",
                "no:cacheprovider",
                #: 🔴 **실패 이름을 남기려고 붙였다**(2026-08-24 · 99 #224).
                #: 재현 안 되는 1회 red 가 **두 번** 났는데 **두 번 다 이름을 못 잡았다**
                #: (#219 ⓒ · №85). ⇒ 다음에 나면 여기서 잡힌다.
                f"--junitxml={junit_path.with_name('offline.xml')}",
            ),
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
            f"PostgreSQL에 연결할 수 없다({host}:{port}). "
            "README 「PR 전 로컬 검증」의 DB 기동 절차를 따른다"
            "(로컬 DB 정의는 저장소에 없다 · `.gitignore`)"
        ) from exc


#: 🔴 실패 목록을 남길 자리 — **비추적**이다(`.gitignore:52` 에 `local_data/` 가 있다 · 확인함).
#: ⚠ 🔴 저장소에 커밋하지 않는다 — 실패 문면에 본문이 실릴 수 있다(불변식 3 · 99 #80).
_FAILURE_LOG_DIR: Final = _REPO_ROOT / "local_data"


def failed_test_ids(junit_path: Path) -> tuple[str, ...]:
    """junit XML 에서 **실패·오류 노드 이름**만 뽑는다(본문은 안 뽑는다)."""
    if not junit_path.exists():
        return ()
    root = ElementTree.parse(junit_path).getroot()  # noqa: S314 - 로컬 pytest 산출물
    return tuple(
        f"{case.get('classname', '')}::{case.get('name', '')}"
        for case in root.iter("testcase")
        if case.find("failure") is not None or case.find("error") is not None
    )


def _record_failures(step: VerificationStep) -> Path | None:
    """🔴 **실패한 검사 이름을 파일로 남긴다**(2026-08-24 · 99 #224).

    ⚠ 🔴 재현을 시도하지 않는다 — 반복 실행은 시간이 얼마나 들지 모른다. 이 함수가
    하는 일은 «**다음에 났을 때 잡히게**» 뿐이다. 재현 안 되는 1회 red 가 **두 번**
    났고 **두 번 다 이름을 못 잡았다**(#219 ⓒ · №85) — 그게 이 자리의 이유다.
    🔴 **이름과 오류 첫 줄까지다** — 본문·인용문은 안 싣는다(불변식 3).
    """
    junit = next(
        (
            Path(arg.removeprefix("--junitxml="))
            for arg in step.command
            if arg.startswith("--junitxml=")
        ),
        None,
    )
    if junit is None:
        return None
    names = failed_test_ids(junit)
    if not names:
        return None
    if not _FAILURE_LOG_DIR.is_dir():
        #: 🔴 디렉터리를 만들지 않는다 — 사용자 로컬 자리다. 없으면 화면 출력까지다.
        print("  🔴 실패 검사:", *names, sep="\n    ", flush=True)
        return None
    target = _FAILURE_LOG_DIR / "pre_pr_verify_failures.txt"
    target.write_text(
        f"[{step.label}] 실패 {len(names)}건\n" + "\n".join(names) + "\n",
        encoding="utf-8",
    )
    print("  🔴 실패 검사:", *names, sep="\n    ", flush=True)
    return target


def _run(step: VerificationStep, *, env: dict[str, str]) -> None:
    print(f"\n== {step.label} ==", flush=True)
    completed = subprocess.run(  # noqa: S603 - argv가 코드 상수이며 shell을 쓰지 않는다
        step.command,
        cwd=_REPO_ROOT,
        env=env,
        check=False,
    )
    if completed.returncode != 0:
        recorded = _record_failures(step)
        raise PrePrVerificationError(
            f"{step.label} 실패(exit={completed.returncode})"
            + (f" — 실패 목록: {recorded}" if recorded else "")
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
