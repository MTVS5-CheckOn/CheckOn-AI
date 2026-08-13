"""`alembic.ini`가 **개발자 기기의 로케일과 무관하게** 읽히는지 고정한다.

🔴 **이건 스타일 규칙이 아니라 배포 명령의 가동 조건이다.** alembic은 이 파일을
`configparser`의 `encoding="locale"`로 읽는다 — Windows에서 그 값은 **ANSI 코드페이지**이고
한국어 Windows에서는 `cp949`다(`chcp 65001`을 걸어도 바뀌지 않는다). 그래서 이 파일에
한글 주석이 한 줄만 있어도 `alembic upgrade head`가 **DB에 닿기도 전에**
`UnicodeDecodeError`로 죽는다.

⚠ **Linux·macOS CI에서는 로케일이 UTF-8이라 이 결함이 안 보인다** — 그래서 "돌려 보고
확인"이 아니라 **바이트로** 고정한다. 파일이 순수 ASCII면 어떤 단일바이트 ANSI
코드페이지로 읽어도 같은 문자열이 나온다.

⚠ **저장소 규약은 한국어 주석 허용이다**(CLAUDE.md §6) — 이 파일 **하나만** 예외이고
그 사유는 `alembic.ini` 머리말에 ASCII로 적혀 있다. 설명은 `docs/06_erd.md`에 둔다.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

import pytest

_ALEMBIC_INI: Final = Path(__file__).resolve().parents[3] / "alembic.ini"

#: alembic이 실제로 쓰는 인코딩의 대표값 — 한국어·일본어·중국어 Windows의 ANSI CP.
_ANSI_CODE_PAGES: Final = ("cp949", "cp932", "cp1252")


def test_the_deploy_config_is_pure_ascii() -> None:
    """🔴 **한글 한 글자가 배포 명령을 죽인다** — 그래서 바이트를 본다."""
    raw = _ALEMBIC_INI.read_bytes()
    offenders = sorted({byte for byte in raw if byte > 0x7F})
    assert not offenders, (
        f"alembic.ini에 비-ASCII 바이트가 있다({[hex(b) for b in offenders]}). "
        "한국어 Windows(cp949)에서 `alembic upgrade head`가 UnicodeDecodeError로 죽는다 "
        "— 설명은 docs/06_erd.md에 두고 이 파일은 ASCII로 유지한다"
    )


@pytest.mark.parametrize("code_page", _ANSI_CODE_PAGES)
def test_the_deploy_config_decodes_under_ansi_code_pages(code_page: str) -> None:
    """🔴 **alembic이 하는 그대로** — 로케일 인코딩으로 디코딩되는지 값으로 본다."""
    raw = _ALEMBIC_INI.read_bytes()
    decoded = raw.decode(code_page)
    assert "script_location" in decoded, (
        f"{code_page}로 읽으면 설정이 온전하지 않다 — 배포 명령이 이 경로를 못 찾는다"
    )
