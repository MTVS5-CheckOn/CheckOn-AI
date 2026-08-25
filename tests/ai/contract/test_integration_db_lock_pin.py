"""🔴 `-m integration` 배타 잠금이 **경로와 무관하게** 걸리는지 — 99 #251 · №108.

🔴 **왜 이 검사가 필요한가** — 잠금은 «안 걸려도 대개 green」이다(겹칠 때만 문제).
⚠ 🔴 그래서 `addopts` 에서 빠져도 **아무도 안 죽는다** — #57 이 실제로 당한 형태다.
⇒ `pyproject.toml` 의 `addopts` 를 **직접 읽는다**(플러그인을 import 하지 않는다).

🔴 그리고 «오프라인까지 직렬화하지 않는가» 를 같이 잰다 — 그건 잠금의 **범위**이고
넓어지면 전 실행이 느려진다.
"""

from __future__ import annotations

import inspect
import tomllib
from pathlib import Path
from typing import Final

import pytest
from integration_db_lock import wants_integration

from ai.db.settings import DbSettings

#: 🔴 이름을 상수로 적는다 — 플러그인이 사라지면 이 검사가 **먼저** 죽어야 한다.
PLUGIN: Final = "integration_db_lock"


def _addopts() -> str:
    root = Path(inspect.getfile(DbSettings)).parents[3]
    config = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    addopts: str = config["tool"]["pytest"]["ini_options"]["addopts"]
    return addopts


def test_the_lock_is_registered_in_addopts() -> None:
    """🔴 `pre_pr_verify` 가 아니라 `pytest` 자체에 붙어야 한다(#224 선례)."""
    addopts = _addopts()
    assert f"-p {PLUGIN}" in addopts, addopts


def test_the_default_run_is_not_serialized() -> None:
    """🔴 기본 실행은 **안 잠근다** — 오프라인까지 직렬화하면 전부 느려진다."""
    addopts = _addopts()
    assert "not integration" in addopts, addopts
    assert not wants_integration("not integration and not external")


@pytest.mark.parametrize(
    ("markexpr", "locked"),
    [
        ("integration", True),
        ("integration and not external", True),
        ("not integration and not external", False),
        ("not integration", False),
        ("", False),
        ("gates", False),
        #: 🔴 공백이 늘어난 형태로도 같은 판정이어야 한다.
        ("  integration   and not external ", True),
    ],
)
def test_only_integration_runs_take_the_lock(markexpr: str, locked: bool) -> None:
    assert wants_integration(markexpr) is locked
