"""`STORE_BACKEND` 오타가 **영속성을 조용히 끄지 못하는가** (99 #38).

🔴 **종전 구조는 오타를 「안전한 폴백」으로 대접했다.**

```python
store_backend: str = "memory"
if settings.store_backend == "pg":
    return Pg...
return InMemory...      # ← pgg · PG · postgres · "memory " · "" 가 전부 여기로
```

⚠ **PG 기본 플립 이후에는 그것이 안전이 아니다.** 설정 오타 하나로 **잡 원장·실행 원장·
멱등 저장·읽기 모델·`AGENT_STEP` 영속성이 한꺼번에, 조용히** 사라진다.
**아무것도 안 터지고 로그도 안 남는다** — 다음 재기동에서 데이터가 없다는 것으로만 안다.

🔴 **선택기가 아니라 설정 경계에서 막는다.** 팩토리마다 `if unknown: raise`를 복제하면
새 팩토리가 생길 때 **한쪽만 낡는다**(99 #02). 정본은 **설정 타입 하나**다.

⚠ **오타를 친절하게 추측하지 않는다** — `PG`를 `pg`로, `memory ` 를 `memory`로 보정하면
**「내가 뭘 켰는지」가 설정과 달라진다.** 대소문자·공백 자동 보정도 하지 않는다.
"""

from __future__ import annotations

from typing import Final

import pytest
from pydantic import ValidationError

from ai.db.settings import DbSettings, get_db_settings

#: 정확히 이 둘만 허용한다.
_ALLOWED: Final = ("memory", "pg")
#: 🔴 **전부 종전에는 memory로 조용히 내려갔다.**
_REJECTED: Final = ("pgg", "PG", "postgres", "memory ", "")


@pytest.mark.parametrize("value", _ALLOWED)
def test_the_two_supported_backends_are_accepted(value: str) -> None:
    assert DbSettings(store_backend=value).store_backend == value


@pytest.mark.parametrize("value", _REJECTED)
def test_an_unsupported_backend_is_refused_at_the_settings_boundary(value: str) -> None:
    """🔴 **미등록 값은 설정 단계에서 거부된다** — memory로 강등되지 않는다.

    ⚠ 종전 검사는 *"미등록 값 → memory fail-safe"* 를 **정답으로 고정**하고 있었다.
    그건 결함을 계약으로 굳힌 것이다 — 여기서 뒤집는다.
    """
    with pytest.raises(ValidationError):
        DbSettings(store_backend=value)


@pytest.mark.parametrize("value", _REJECTED)
def test_the_environment_path_is_refused_too(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    """🔴 **실제 프로세스 환경으로 들어와도 막혀야 한다.**

    ⚠ `model_validate()`만 보면 **운영에서 값이 들어오는 경로**를 안 본 것이다.
    ⚠ `get_db_settings()`는 `lru_cache`라 **비우지 않으면 옛 설정을 돌려준다** —
    그것을 「검증됐다」로 오인하면 검사가 아무것도 안 본다.
    """
    monkeypatch.setenv("STORE_BACKEND", value)
    get_db_settings.cache_clear()
    try:
        with pytest.raises(ValidationError):
            get_db_settings()
    finally:
        monkeypatch.delenv("STORE_BACKEND", raising=False)
        get_db_settings.cache_clear()


@pytest.mark.parametrize("value", _ALLOWED)
def test_the_environment_path_accepts_the_two(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("STORE_BACKEND", value)
    get_db_settings.cache_clear()
    try:
        assert get_db_settings().store_backend == value
    finally:
        monkeypatch.delenv("STORE_BACKEND", raising=False)
        get_db_settings.cache_clear()


def test_the_declared_default_is_still_memory() -> None:
    """⚠ **이 PR은 기본값을 플립하지 않는다** — 검증과 플립을 한 커밋에 묶지 않는다.

    🔴 **`DbSettings()`를 만들어 보면 안 된다.** 그건 `.env`·환경 변수를 **읽고** 오므로
    `STORE_BACKEND=memory`가 걸려 있으면 **코드에 적힌 기본값을 한 번도 안 본다.**
    ⇒ 다음 회차에 선언 기본값을 `pg`로 바꿔도 **검사가 memory로 green**이 되어
    **플립 검사가 자기 목적을 놓친다.** 실측으로 확인했다(§뒤집기).

    ⇒ **모델에 선언된 기본값**을 직접 본다. 환경 변수 파싱은 **위의 별도 검사**가 든다 —
    두 축을 한 검사에 합치면 **어느 쪽이 깨졌는지** 모른다.
    """
    assert DbSettings.model_fields["store_backend"].default == "memory"


def test_the_error_does_not_leak_the_connection_settings() -> None:
    """🔴 오류 문면에 **DB URL·비밀번호**를 싣지 않는다."""
    with pytest.raises(ValidationError) as caught:
        DbSettings(
            store_backend="pgg",
            database_url="postgresql+asyncpg://user:secret@host/db",
        )
    message = str(caught.value)
    assert "secret" not in message, f"오류가 접속정보를 흘린다: {message[:300]}"
    assert "asyncpg" not in message


def test_a_typo_is_not_guessed_into_a_backend() -> None:
    """⚠ **친절한 추측 금지** — `PG`를 `pg`로 바꾸면 설정과 실제가 갈린다."""
    for close_miss in ("PG", "Memory", " pg"):
        with pytest.raises(ValidationError):
            DbSettings(store_backend=close_miss)
