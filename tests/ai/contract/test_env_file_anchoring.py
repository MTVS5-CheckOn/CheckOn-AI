"""🔴 **모든 `BaseSettings` 는 `.env` 를 어디서 기동하든 찾는다** (99 #73).

━━ 무엇을 지키나 ━━

`env_file=".env"` 는 상대경로다. 저장소 루트가 아닌 데서 기동하면 **파일을 못 찾고**,
pydantic-settings 는 그것을 **오류로 만들지 않는다** — 조용히 선언 기본값으로 떨어진다.

    `CHECKON_ALLOW_REAL_LLM` → False   ⇒ 브리핑 전건 템플릿 폴백 (8/14 사고와 같은 증상)
    `DATABASE_URL`           → 기본값  ⇒ 엉뚱한 DB 를 본다

🔴 **증상이 사고와 똑같고 원인만 다르다.** #267 이 「`os.environ` 만 봤다」를 고쳤으므로
«고쳤다» 는 판단이 서고, **아무도 여기를 다시 안 본다.** 실제로 `real_llm.py`·`console.py`
docstring 이 *"어떤 기동 방식으로도 산다"* 고 **적어 두고 있었다** — 거짓이었다.

⚠ **윈도우에서만 터진다** — 서비스 등록·작업 스케줄러는 작업 디렉터리가 저장소 루트가
아니다. 맥·리눅스에서 `cd` 해서 띄우는 사람은 **평생 안 본다.** 99 #72 와 같은 계열이다:
**개발자 기기에서만 green.**

⚠ **이 검사는 실 `.env` 를 읽지 않는다** — 저장소의 `.env` 는 기기마다 다르고(99 #57·#63)
워크트리에는 아예 없다. 앵커의 **모양**과 pydantic-settings 의 **우선순위**만 잰다.
"""

from __future__ import annotations

import ast
import os
from pathlib import Path
from typing import Final

import pytest
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from ai.runtime.env_files import ENV_FILES

_ROOT: Final = Path(__file__).resolve().parents[3]
_SRC: Final = _ROOT / "src" / "ai"

#: 정본 헬퍼가 사는 자리 — 자기 자신은 리터럴을 쓸 수밖에 없다(설명 문면).
_HELPER: Final = "env_files.py"


def _settings_config_calls() -> list[tuple[Path, ast.Call]]:
    """`SettingsConfigDict(...)` 호출 전수 — 🔴 **docstring 은 안 본다.**

    ⚠ 텍스트 `grep` 으로 하면 안 된다. 이 저장소의 docstring 이 `env_file=".env"` 를
    **설명으로** 적고 있어서 정정을 쓰는 순간 검사가 운다(선례: `test_real_llm_optin_guard`
    의 `_OLD_GATE` 가 같은 이유로 `ast` 로 갔다). ⇒ 처음부터 코드만 본다.
    """
    found: list[tuple[Path, ast.Call]] = []
    for path in sorted(_SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
            if name == "SettingsConfigDict":
                found.append((path, node))
    return found


def test_the_census_is_not_empty() -> None:
    """🔴 검사 경로가 끊기면 통과가 아니라 실패다."""
    calls = _settings_config_calls()
    assert len(calls) >= 10, (
        f"`SettingsConfigDict` 자리를 {len(calls)}개밖에 못 찾았다 — 경로가 틀렸나: {_SRC}"
    )


def test_no_settings_uses_a_relative_dotenv() -> None:
    """🔴 **`env_file` 은 전부 `ENV_FILES` 다** — 리터럴 `".env"` 를 적지 마라.

    ⚠ 이 검사가 없으면 다음 사람이 새 `Settings` 를 만들면서 관례대로 `".env"` 를 적고,
    그 자리만 **작업 디렉터리에 의존**하게 된다. 그리고 **맥에서는 초록**이라 안 보인다.
    """
    stale = [
        f"{path.relative_to(_ROOT)}:{node.lineno}"
        for path, node in _settings_config_calls()
        for kw in node.keywords
        if kw.arg == "env_file" and not isinstance(kw.value, ast.Name)
    ]
    assert not stale, (
        f"`env_file` 에 리터럴을 적었다: {stale} — "
        "`from ai.runtime.env_files import ENV_FILES` 를 쓰라 (99 #73)"
    )


def test_every_settings_declares_an_env_file() -> None:
    """⚠ `env_file` 을 아예 안 준 `Settings` 는 `.env` 를 **한 번도** 안 본다.

    ⇒ 새로 만드는 사람이 빠뜨리면 그 설정만 조용히 env 전용이 된다. 전수로 잠근다.
    """
    missing = [
        f"{path.relative_to(_ROOT)}:{node.lineno}"
        for path, node in _settings_config_calls()
        if path.name != _HELPER
        and not any(kw.arg == "env_file" for kw in node.keywords)
    ]
    assert not missing, f"`env_file` 이 없는 설정: {missing} — `.env` 를 안 본다 (99 #73)"


def test_the_anchor_is_absolute_and_points_at_the_repo_root() -> None:
    """앵커는 **절대경로**이고 저장소 루트의 `.env` 를 가리킨다."""
    anchor, relative = ENV_FILES
    assert Path(anchor).is_absolute(), f"앵커가 상대경로다: {anchor} — 기동 위치에 또 의존한다"
    assert Path(anchor) == _ROOT / ".env", f"앵커가 저장소 루트가 아니다: {anchor}"
    assert relative == ".env", "CWD 쪽 항목이 사라지면 임시 `.env` 로 값을 만드는 검사가 죽는다"


def test_the_anchor_survives_a_different_working_directory(tmp_path: Path) -> None:
    """🔴 **이게 사고의 자리다** — 저장소 루트가 아닌 데서 기동해도 값이 산다.

    ⚠ 실 `.env` 를 안 쓴다 — 합성 앵커로 **우선순위 자체**를 잰다(모듈 docstring).
    """
    anchor_dir = tmp_path / "repo"
    anchor_dir.mkdir()
    (anchor_dir / ".env").write_text("ANCHORED_DEMO=from-anchor\n", encoding="utf-8")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()

    class _Demo(BaseSettings):
        model_config = SettingsConfigDict(
            env_file=(anchor_dir / ".env", ".env"), extra="ignore", populate_by_name=True
        )

        value: str = Field(default="(안읽힘)", alias="ANCHORED_DEMO")

    os.chdir(elsewhere)
    assert _Demo().value == "from-anchor", (
        "🔴 작업 디렉터리를 옮기니 `.env` 가 안 읽힌다 — 이 상태가 브리핑 전건 폴백이다"
    )

    #: 🔴 **CWD 가 이겨야 한다** — 안 그러면 `monkeypatch.chdir` + 임시 `.env` 로 값을
    #: 만드는 검사·세션 핀이 **저장소의 진짜 `.env`** 를 보게 되어 기기마다 갈린다(#57·#63).
    (elsewhere / ".env").write_text("ANCHORED_DEMO=from-cwd\n", encoding="utf-8")
    assert _Demo().value == "from-cwd", "CWD 의 `.env` 가 앵커를 못 이긴다 — 검사들이 죽는다"


@pytest.fixture(autouse=True)
def _restore_cwd() -> object:
    """⚠ `os.chdir` 를 쓰는 검사가 있어 원래 자리로 돌려놓는다 — 다른 검사가 흔들린다."""
    origin = Path.cwd()
    yield None
    os.chdir(origin)
