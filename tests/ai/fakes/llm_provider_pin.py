"""테스트 전역 플러그인 — **오프라인 회귀의 provider 선택을 명시로 만든다** (99 #57).

🔴 **개발자의 로컬 `.env` 하나가 스위트를 뒤집고 있었다.** `BriefingSettings`·
`CounselSettings`·`ClassifySettings`가 전부 `env_file=".env"`로 `llm_provider`를 읽는데,
맥 `.env`의 `LLM_PROVIDER`가 선언 기본값 `fake`를 덮어 **상시 6건 red**였다(실측 8/13).

⚠ **그 6건 중 둘이 하필 「실물을 안 부른다」를 지키는 테스트다** —
`test_default_provider_is_fake` · `test_ci_default_makes_no_real_llm_call`.
🔴 **그 둘이 red인 동안 그 보장은 없었다.** 가드가 빨간불이면 가드가 아니다.

━━ 왜 `.env`를 고치지 않았나 ━━

`.env`에서 `LLM_PROVIDER`를 지우면 진희님 개발 환경이 망가진다(실물로 브리핑을 확인해야
한다). 그리고 **다음 사람의 `.env`는 또 다르다** — 개인 설정으로 회귀가 흔들리는 **구조**가
결함이지 그 사람의 값이 잘못된 게 아니다. ⇒ 고칠 곳은 `.env`가 아니라 **테스트 격리**다.

━━ 어떻게 막나 — 프로세스 env가 `.env`를 이긴다 ━━

`pydantic-settings`의 우선순위는 **init 인자 > 프로세스 env > `.env` > 기본값**이다.
그래서 여기서 `LLM_PROVIDER=fake`를 **프로세스 env에 걸면** `.env`의 값이 안 읽힌다.
파일을 건드리지 않고 파일의 영향만 없앤다.

⚠ **`setdefault`다 — 막되 열 수 있게.** 실물이 필요한 실행은 그대로 통한다::

    LLM_PROVIDER=openai_compat uv run pytest …   # 셸 env가 이미 있으면 핀은 비켜선다

🔴 다만 **실 LLM 호출의 허용 스위치는 따로다** — `CHECKON_ALLOW_REAL_LLM`(99 #32 ·
`runtime/real_llm.py`). 이 핀은 *"어느 provider를 조립하나"* 만 정하고 *"실물을 불러도
되나"* 는 안 건드린다. 두 축을 섞으면 하나가 다른 하나를 조용히 연다.

━━ 🔴 이 핀이 기본값 검사를 가리지 못한다 ━━

핀을 걸면 `Settings().llm_provider == "fake"`는 **핀 때문에** 통과한다 — 선언 기본값이
무엇이든. 그래서 기본값 검사는 `model_fields["llm_provider"].default`를 **직접** 본다
(`test_briefing.py` · `test_counsel_service_wiring.py`에서 그렇게 고쳤다).
`store_backend_pin.py`가 99 #38에서 같은 이유로 같은 형태를 택했다 — 선례 그대로다.

⚠ **conftest가 아니라 플러그인이다** — `tests/conftest.py`로 두면 mypy가 `conftest` 모듈
   중복으로 막는다(`store_backend_pin.py` 주석 참조) ⇒ `addopts`의 `-p`로 싣는다.

⚠ **아직 누수 가드는 없다.** `store_backend_pin`은 `monkeypatch.delenv`가 세션을 오염시킨
   실측이 있어 복구 픽스처를 달았는데, `LLM_PROVIDER`를 지우거나 바꾸는 테스트는 **현재
   0건이다**(전수 확인). 생기면 그때 같은 형태로 단다 — 없는 사고에 기계를 세우지 않는다.
   대신 `test_llm_provider_pin.py`가 핀의 실효를 잠근다.
"""

from __future__ import annotations

import os
from typing import Final

#: 세 capability가 공유하는 env 이름 — 셋 다 `llm_provider` 필드라 접두가 없다.
PIN_NAME: Final = "LLM_PROVIDER"
PIN_VALUE: Final = "fake"

#: 🔴 **import 시점에 건다** — 테스트 모듈이 `get_*_settings()`를 부르기 전이어야 한다.
#: 셋 다 `lru_cache`라 한 번 읽히면 그 값이 굳는다(`store_backend_pin` 실측과 같은 함정).
os.environ.setdefault(PIN_NAME, PIN_VALUE)
