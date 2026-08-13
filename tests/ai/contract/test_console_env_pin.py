"""콘솔 플래그 핀이 실효인가 — 그리고 **선언 기본값을 가리지 않는가** (99 #63).

🔴 **핀은 두 방향으로 틀릴 수 있다.**

    ① 안 걸린다  → 개발자 `.env`가 「기본 꺼짐」 가드를 다시 뒤집는다 (원래 사고)
    ② 너무 걸린다 → 선언 기본값이 `True`로 바뀌어도 **초록**이다 (가드가 죽는다)

②가 더 위험하다 — 화면이 초록이라 아무도 안 본다.

🔴 **핀 모듈을 import 하지 않는다.** `import console_env_pin` 하는 순간 그 import가
`env_file`을 끊어 **자기를 통과시킨다** — 99 #57에서 실제로 당한 형태다(`addopts`에서 핀을
빼고 돌려도 6/6 초록이었다). ⇒ `pyproject.toml`의 `addopts`를 **직접 읽는다.**
"""

from __future__ import annotations

import inspect
import tomllib
from pathlib import Path
from typing import Final

import pytest

from ai.runtime.console import ConsoleSettings

#: 🔴 **플러그인을 import하지 않고** 이름을 여기 상수로 적는다(위 docstring 참조).
PLUGIN: Final = "console_env_pin"

#: 「기본 꺼짐」이어야 하는 플래그 — 켜지면 화면·개인정보 노출 성격이 바뀐다.
_MUST_DEFAULT_OFF: Final = ("console_error_log", "console_llm_log", "console_ok_log")


def _addopts() -> str:
    root = Path(inspect.getfile(ConsoleSettings)).parents[3]
    config = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    addopts: str = config["tool"]["pytest"]["ini_options"]["addopts"]
    return addopts


def test_the_pin_is_registered_in_addopts() -> None:
    """🔴 `pyproject`의 `-p console_env_pin`이 빠지면 여기서 잡힌다.

    핀이 없으면 `.env`의 `CONSOLE_*`가 다시 이기고 「기본 꺼짐」 가드 3건이 red로 돌아간다.
    그러면 `pre_pr_verify`가 offline 단계에서 멈춰 **integration 단계가 아예 안 돈다.**
    """
    addopts = _addopts()
    assert f"-p {PLUGIN}" in addopts, addopts


def test_the_pin_actually_severs_the_dotenv_file() -> None:
    """핀이 `.env` 파일을 실제로 끊었다 — 값이 아니라 **경로**를 본다.

    ⚠ 값을 단언하면(`console_error_log is False`) 개발자 `.env`가 우연히 비어 있을 때도
    통과한다 — **핀이 없어도 초록**이다. 끊긴 것 자체를 본다.
    """
    assert ConsoleSettings.model_config.get("env_file") is None


@pytest.mark.parametrize("field", _MUST_DEFAULT_OFF)
def test_declared_default_is_off_regardless_of_the_pin(field: str) -> None:
    """🔴 **선언 기본값**이 꺼짐이다 — 핀이 아니라.

    `model_fields[...].default`는 환경도 `.env`도 안 읽으므로 핀에 **면역**이다. 이 단언이
    없으면 누군가 기본값을 `True`로 바꿔도 스위트가 초록이다 — 「아무것도 안 하면 조용하다」는
    약속이 조용히 거짓이 된다(99 #57이 `store_backend_pin`에서 택한 형태 그대로).
    """
    assert ConsoleSettings.model_fields[field].default is False


def test_an_explicit_shell_setting_still_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    """⚠ **막되 열 수 있게** — 끊은 것은 `.env` 파일 하나지 설정 경로 전체가 아니다.

    콘솔을 켜야 하는 검사·실행은 셸 env로 그대로 켤 수 있어야 한다::

        CONSOLE_ERROR_LOG=1 uv run pytest …

    무조건 꺼짐으로 고정하면 이 테스트가 빨간불이 된다.
    """
    monkeypatch.setenv("CONSOLE_ERROR_LOG", "1")

    assert ConsoleSettings().console_error_log is True
