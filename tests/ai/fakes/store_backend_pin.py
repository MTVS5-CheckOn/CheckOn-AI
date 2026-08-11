"""테스트 전역 플러그인 — **오프라인 회귀의 저장소 선택을 명시로 만든다** (99 #39).

🔴 **기본값이 `pg`로 뒤집혔다**(8/12 · `db/settings.py`). 종전 회귀 2 489건은 전부
「기본값 = memory」를 **암묵 전제**로 쓰였고, 플립 직후 그대로 돌리면 **151건이 red**다
(실측) — DB 없는 오프라인 검사가 PG 세션을 열려고 하기 때문이다.

⇒ **전제를 없애지 않고 명시로 바꾼다.** 여기서 `STORE_BACKEND=memory`를 **환경에 건다**.
`memory`는 플립 이후에도 **명시적으로 고르는 테스트·데모 백엔드**이므로(설정 문서 참조)
이건 회피가 아니라 그 선택을 코드로 적은 것이다.

🔴 **이 핀이 기본값 검사를 가리지 못한다.** 두 가지로 막는다.
  ⓐ 선언 기본값 검사는 `DbSettings.model_fields[...].default`를 **직접** 본다 —
     환경 변수를 안 읽으므로 이 핀에 **면역**이다(99 #38에서 그 형태로 바꾼 이유가 이것이다).
  ⓑ 플립 전용 검사는 **이 변수를 지우고**(`monkeypatch.delenv`) 돈다 — 지우지 않으면
     자기가 재려는 것을 자기가 가린다(그쪽에 절단 가드가 있다).

⚠ **conftest가 아니라 플러그인이다** — `tests/conftest.py`로 두면 mypy가
   **`conftest` 모듈 중복**으로 막는다(`tests/ai/unit/llm/conftest.py`와 같은 이름 · 실측).
   pyproject의 `pythonpath` 주석이 이미 그 함정을 적어 뒀다 ⇒ `addopts`의 `-p`로 싣는다.

⚠ **`setdefault`다** — 명시적으로 `STORE_BACKEND=pg`를 주고 돌리는 실행은 그대로 존중한다.
⚠ `pre_pr_verify`도 같은 값을 걸고 있다(그쪽은 하위 프로세스 env). 둘 다 있는 것이 맞다 —
   이 파일은 **맨손 `uv run pytest`** 를, 그쪽은 **검증 명령**을 각각 덮는다.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from typing import Final

import pytest

from ai.db.settings import get_db_settings

_PIN_NAME: Final = "STORE_BACKEND"
_PIN_VALUE: Final = "memory"

#: 🔴 **import 시점에 건다** — 테스트 모듈이 `get_db_settings()`를 부르기 전이어야 한다.
#: `lru_cache`라 한 번 읽히면 그 값이 굳는다(99 #38 실측).
os.environ.setdefault(_PIN_NAME, _PIN_VALUE)


@pytest.fixture(autouse=True)
def _keep_the_backend_pin_from_leaking() -> Iterator[None]:
    """🔴 **핀을 지운 검사가 뒤에 오는 검사를 오염시키지 못하게 한다** (99 #39).

    플립 전에는 `monkeypatch.delenv("STORE_BACKEND")`가 **무해**했다 — 변수를 지우면
    선언 기본값이 `memory`라 핀이 있으나 없으나 같았기 때문이다. 플립 뒤에는 그 정리
    코드가 **세션 전체를 pg로 바꾼다.** 실측: 전량 실행에서 `test_counsel_step_sink_wiring`
    한 건이 red였고, **단독 실행은 통과**했다(오염이라 그 자리에서는 안 보인다).

    🔴 **환경 변수만 보면 못 잡는다**(실측). `monkeypatch`가 변수는 되돌려 주지만
    `get_db_settings`는 **`lru_cache`** 라, 정리 코드가 `cache_clear()` **뒤에**
    저장소를 다시 조립하면 **캐시가 pg 설정으로 다시 채워진 채** 남는다 —
    환경은 `memory`인데 **설정 싱글턴은 pg**인 상태다. 그래서 **캐시 안의 값**까지 본다.

    ⚠ **어긋났을 때만 손댄다** — 매 검사마다 `cache_clear()`를 부르면 2 600건이 `.env`를
    다시 읽는다. 캐시가 비어 있으면 조회조차 하지 않는다(채우면 그게 부작용이다).
    ⚠ 플립 전용 검사는 **검사 안에서** 핀을 지우므로 이 복구는 그 뒤에 온다 — 자기가
    재려는 것을 가리지 않는다.
    """
    yield
    cached_disagrees = bool(
        get_db_settings.cache_info().currsize
    ) and get_db_settings().store_backend != _PIN_VALUE
    if os.environ.get(_PIN_NAME) != _PIN_VALUE or cached_disagrees:
        os.environ[_PIN_NAME] = _PIN_VALUE
        get_db_settings.cache_clear()
