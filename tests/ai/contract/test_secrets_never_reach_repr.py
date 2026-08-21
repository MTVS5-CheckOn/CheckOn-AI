"""🔴 비밀로 판정된 설정 필드가 **하나도** `repr` 에 안 나온다 (99 #176 · 로그 176).

⚠ **왜 전수인가** — `#122` 가 `OPENAI_API_KEY` 의 유출 경로(`repr`)를 실측해 닫았고 카나리
검사까지 세웠다. 🔴 **그런데 그 검사는 `OpenAiSettings` 하나에만 붙어 있었다** — 같은 경로를
쓰는 다른 비밀은 **아무도 안 물었다**(로그 169: *"전용 검사가 없는 축은 안 재고 있다는 뜻"*).
전수로 물으니 `DbSettings.database_url`·`.agent_checkpoint_database_url` 이 평문 `str` 이었고,
프로덕션 URL 에는 **비밀번호가 들어 있다**.

🔴 **계약은 「찍어도 안 나온다」이고 타입이 아니다.** `SecretStr` 이든 `Field(repr=False)` 든
수단이고, 수단을 바꿔도 이 검사는 그대로 서야 한다(#122 의 검사가 같은 문장을 적었다).
⚠ 그래서 목록은 **「비밀인 필드」**이지 「`SecretStr` 인 필드」가 아니다.

⚠ `tests/ai/llm/test_openai_compat.py` 의 `test_api_key_never_appears_in_a_settings_repr` 와
**겹친다 — 지우지 않는다.** 그건 B 축(공급자) 검사이고 이건 전수 검사다.
🔴 **겹치는 것이 문제가 아니라 안 겹치는 자리가 문제였다.**
"""

from __future__ import annotations

from typing import Final

import pytest
from pydantic_settings import BaseSettings

from ai.db.settings import DbSettings
from ai.llm.providers.openai_compat import OpenAiSettings
from ai.problem_generation.infrastructure.stdict import StdictSettings
from ai.problem_generation.provider import ProblemProviderSettings

_CANARY: Final = "CANARY-c0ffee-DO-NOT-PRINT"

#: 🔴 **비밀 필드의 정본 목록** — 「값이 새면 남이 우리 자원에 접근할 수 있는가」로 골랐다.
#: ⚠ 타임아웃·풀 크기·모델 이름·상한값은 **비밀이 아니다**(가릴 필요 없는 값을 가리면
#: 디버깅이 어려워진다). 접속 URL 은 **자격증명이 들어갈 수 있는 것만** 넣었다 —
#: `openai_base_url` 둘은 🔴 **가리지 않기로 판정했다**(8/21 · 99 #176) — 자격증명이 아니라
#: 구성 정보이고 **장애 조사에 필요하다**(«어느 엔드포인트로 보냈나» · #133 선례).
#: 위험은 `repr` 이 아니라 **저장소 문면**이고 그 축은 **99 #177** 이다.
#: ⚠ `stdict_base_url` 은 공개 API 라 애초에 비밀이 아니다.
_SECRET_FIELDS: Final[tuple[tuple[type[BaseSettings], str], ...]] = (
    (DbSettings, "database_url"),
    (DbSettings, "agent_checkpoint_database_url"),
    (OpenAiSettings, "openai_api_key"),
    (StdictSettings, "stdict_api_key"),
    (ProblemProviderSettings, "openai_api_key"),
)


@pytest.mark.parametrize(
    ("settings_cls", "field"),
    _SECRET_FIELDS,
    ids=[f"{c.__name__}.{f}" for c, f in _SECRET_FIELDS],
)
def test_a_secret_field_never_reaches_repr(settings_cls: type[BaseSettings], field: str) -> None:
    """카나리를 넣고 객체를 찍는다 — 카나리가 나오면 red.

    🔴 **타입을 묻지 않는다.** `SecretStr` 로 가리든 `repr=False` 로 빼든 통과하고,
    평문 `str` 로 되돌리면 red 다 — **재는 것은 유출 경로이지 수단이 아니다.**
    ⚠ 실측(8/21): `repr=False` 이전의 `DbSettings` 는 카나리를 **그대로** 냈다.
    """
    settings = settings_cls(**{field: _CANARY})  # type: ignore[arg-type]
    printed = repr(settings)
    assert _CANARY not in printed, (
        f"🔴 `{settings_cls.__name__}.{field}` 이 `repr` 에 **평문으로** 나온다. "
        f"이 설정 객체가 찍히는 모든 자리(대표적으로 pytest 실패 메시지의 assertion 표현)에 "
        f"비밀이 실리고, CI 에서 나면 **빌드 로그에 영구히 남는다**(99 #122 의 실측 경로). "
        f"⇒ 읽는 곳이 적으면 `SecretStr`, 많으면 `Field(repr=False)` 로 닫아라"
    )
    assert _CANARY not in str(settings), (
        f"`{settings_cls.__name__}.{field}` 이 `str()` 로 샌다 — `repr` 만 막으면 반쪽이다"
    )


def test_a_public_setting_is_not_hidden() -> None:
    """🔴 **오탐 시험** — 비밀이 아닌 값은 `repr` 에 **나와야 한다**.

    ⚠ 이 검사가 없으면 «다 가리는 설정» 도 위 검사를 통과한다 — 앵커 폭이 증명 안 된다.
    🔴 **가릴 필요 없는 값을 가리면 디버깅이 어려워진다**: 커넥션 예산(99 #127)을 볼 때
    `db_pool_size`·`app_workers` 가 안 보이면 관계식을 눈으로 못 맞춘다.
    """
    printed = repr(DbSettings(db_pool_size=7, app_workers=3))
    assert "db_pool_size=7" in printed, f"풀 크기는 비밀이 아니다 — 보여야 한다: {printed}"
    assert "app_workers=3" in printed, f"워커 수는 비밀이 아니다 — 보여야 한다: {printed}"
