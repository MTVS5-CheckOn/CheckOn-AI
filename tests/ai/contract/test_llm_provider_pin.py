"""provider 핀이 실효인가 — 그리고 **선언 기본값을 가리지 않는가** (99 #57).

🔴 **핀은 두 방향으로 틀릴 수 있다.**

    ① 안 걸린다  → 개발자 `.env`가 회귀를 다시 뒤집는다 (원래 사고)
    ② 너무 걸린다 → 선언 기본값이 `openai_compat`로 바뀌어도 **초록**이다 (가드가 죽는다)

②가 더 위험하다 — 화면이 초록이라 아무도 안 본다. 그래서 둘 다 잠근다.
"""

from __future__ import annotations

import importlib
import inspect
import os
import tomllib
from pathlib import Path

import pytest
from pydantic_settings import BaseSettings

from ai.composition.classify.provider import ClassifySettings
from ai.composition.counsel.settings import CounselSettings
from ai.composition.provider import BriefingSettings

#: 🔴 **플러그인을 import하지 않고** 이름을 여기 적는다. `from llm_provider_pin import …`를
#: 쓰면 그 import가 `os.environ.setdefault`를 실행해 **테스트가 자기를 통과시킨다** —
#: 실측(8/13): `addopts`에서 핀을 빼고 돌려도 이 파일은 6/6 초록이었다. **동어반복이다.**
PIN_NAME = "LLM_PROVIDER"
PIN_VALUE = "fake"
PLUGIN = "llm_provider_pin"

_SETTINGS: tuple[type[BaseSettings], ...] = (
    BriefingSettings,
    CounselSettings,
    ClassifySettings,
)


def test_the_pin_is_registered_in_addopts() -> None:
    """🔴 `pyproject`의 `-p llm_provider_pin`이 빠지면 여기서 잡힌다.

    핀이 없으면 `.env`의 `LLM_PROVIDER`가 다시 이기고 **상시 6건 red**로 돌아간다.
    그 중 둘이 「실물을 안 부른다」를 지키는 가드라, 조용히 빠지면 보장까지 같이 빠진다.

    ⚠ **환경 변수를 보지 않고 설정 파일을 본다.** 환경을 보면 이 파일이 플러그인을
    import하는 것만으로 통과해 버린다(위 상수 주석의 실측).
    """
    root = Path(inspect.getfile(BriefingSettings)).parents[3]
    config = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    addopts = config["tool"]["pytest"]["ini_options"]["addopts"]

    assert f"-p {PLUGIN}" in addopts, addopts


def test_the_pin_takes_effect_in_the_process_env() -> None:
    """핀이 실제로 프로세스 env에 걸려 `.env`를 이긴다.

    ⚠ 이 단언 하나만으로는 약하다(위 참조) — `test_the_pin_is_registered_in_addopts`와
    **짝**이어야 배선과 효과가 둘 다 잠긴다.
    """
    assert os.environ.get(PIN_NAME) == PIN_VALUE


@pytest.mark.parametrize("settings_cls", _SETTINGS, ids=lambda cls: cls.__name__)
def test_declared_default_is_fake_regardless_of_the_pin(
    settings_cls: type[BaseSettings],
) -> None:
    """🔴 **선언 기본값**이 fake다 — 핀이 아니라.

    `model_fields[...].default`는 환경을 안 읽으므로 핀에 **면역**이다. 이 단언이 없으면
    누군가 기본값을 `openai_compat`로 바꿔도 핀 덕분에 스위트가 초록이다 —
    「기본값은 안전하다」는 전제가 조용히 거짓이 된다(99 #32가 정확히 그 형태였다).
    """
    assert settings_cls.model_fields["llm_provider"].default == PIN_VALUE


def test_the_pin_yields_to_an_explicit_choice(monkeypatch: pytest.MonkeyPatch) -> None:
    """⚠ **막되 열 수 있게** — 실물이 필요한 실행(윈도우 실측류)은 그대로 통해야 한다.

    `setdefault`라 셸에서 준 값이 이미 있으면 핀은 비켜선다::

        LLM_PROVIDER=openai_compat uv run pytest …

    무조건 덮어쓰기로 바꾸면 이 테스트가 빨간불이 된다.
    """
    monkeypatch.setenv(PIN_NAME, "openai_compat")
    module = importlib.import_module(PLUGIN)

    importlib.reload(module)  # 핀을 다시 걸어 본다 — 이미 값이 있으므로 안 덮어야 한다

    assert os.environ[PIN_NAME] == "openai_compat"


def test_the_pin_does_not_open_the_real_llm_switch() -> None:
    """🔴 **두 축을 섞지 않는다** — 이 핀은 *"어느 provider를 조립하나"* 만 정한다.

    실 호출 허용은 `CHECKON_ALLOW_REAL_LLM` 하나다(99 #32 · `runtime/real_llm.py`).
    핀이 그 스위치까지 건드리면 하나가 다른 하나를 조용히 연다.
    """
    from ai.runtime.real_llm import real_llm_optin

    assert real_llm_optin() is False
