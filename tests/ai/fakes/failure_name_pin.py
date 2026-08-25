"""테스트 전역 플러그인 — 🔴 **어느 경로로 돌리든 실패 이름이 남는다**(99 #224 · №105).

🔴 **왜 필요한가** — 재현 안 되는 1회 red 가 **세 번** 났고 **세 번 다 이름을 못 잡았다**
(#219 ⓒ · №85 · #444). №87 이 `pre_pr_verify` 에 기록을 붙였는데, 🔴 **세 번째는
`pytest` 를 직접 돌려서 그 자리를 우회했다.**

⇒ 🔴 **판정(№105): 규율이 아니라 `pytest` 자체에 붙인다.** `addopts` 의 `-p` 로 실리므로
**직접 실행이든 `pre_pr_verify` 든 같은 자리에 남는다.**
⚠ 🔴 «`pytest` 직접 실행을 막는다» 는 못 한다(사람은 직접 돌린다) · «규율로 적는다» 는
**세 번 실패한 방법**이다.

🔴 **겹치지 않는다** — 이 플러그인이 **유일한 기록자**다. `pre_pr_verify` 의
`_record_failures` 는 이제 **화면 출력만** 한다(그 자리에 그 사실을 적어 뒀다).
🔴 형식·경로·상한은 №87 것을 **그대로 재사용**한다(새 형식을 안 짓는다).

⚠ **conftest 가 아니라 플러그인이다** — `tests/conftest.py` 로 두면 mypy 가 **모듈 중복**
으로 막는다(`store_backend_pin` docstring 이 그 함정을 적어 뒀다).
🔴 **이름과 시각까지다** — 오류 본문·인용문은 안 싣는다(불변식 3 · 99 #80).
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from ai.evaluation.pre_pr_verify import (
    _FAILURE_LOG_DIR,
    _trim,
)

#: 🔴 №87 과 **같은 파일**에 쌓는다 — 두 경로의 기록이 한 줄기로 읽혀야 한다.
_TARGET_NAME = "pre_pr_verify_failures.txt"

_failed: list[str] = []


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    """실패·오류 노드 이름만 모은다 — 🔴 본문은 안 본다."""
    if report.when == "call" and report.failed:
        _failed.append(report.nodeid)
    elif report.when in {"setup", "teardown"} and report.failed:
        _failed.append(f"{report.nodeid} ({report.when})")


def pytest_sessionfinish(session: pytest.Session) -> None:
    """세션이 끝나면 **누적**한다 — 🔴 덮어쓰지 않는다(#224 보완).

    ⚠ 🔴 실패가 0건이면 **파일을 안 건드린다** — green 실행이 앞선 red 기록을 지우면
    «두 번째와 세 번째가 같은 검사인가» 를 못 본다.
    """
    if not _failed:
        return
    names = tuple(dict.fromkeys(_failed))
    print("\n  🔴 실패 검사:", *names, sep="\n    ", flush=True)
    if not _FAILURE_LOG_DIR.is_dir():
        #: 🔴 디렉터리를 만들지 않는다 — 사용자 로컬 자리다(`local_data/`). 화면까지다.
        return
    target = _FAILURE_LOG_DIR / _TARGET_NAME
    stamp = datetime.now(UTC).astimezone().isoformat(timespec="seconds")
    label = " ".join(session.config.invocation_params.args) or "pytest"
    entry = f"[{stamp}] [pytest] {label[:120]} 실패 {len(names)}건\n" + "".join(
        f"  {name}\n" for name in names
    )
    previous = target.read_text(encoding="utf-8") if target.exists() else ""
    target.write_text(_trim(previous + entry), encoding="utf-8")
