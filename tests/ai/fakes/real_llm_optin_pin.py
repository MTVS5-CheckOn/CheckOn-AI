"""테스트 전역 플러그인 — **pytest 세션에서는 `.env`의 실 LLM opt-in을 무시한다** (99 #32).

🔴 **이 핀이 이 저장소의 실 LLM 방어 한 겹이다. 지우면 게이트가 약해진다.**

━━ 왜 생겼나 — 막는 자리를 옮겼다 (2026-08-14) ━━

99 #32 사고는 `pytest -m integration`이 사용자 키로 실 OpenAI API를 부른 것이다.
그 처방으로 `real_llm_optin()`이 **`.env`를 아예 안 읽게** 만들었는데, 그러면
**운영 서버에서도 안 읽힌다** — 8/14 윈도우에서 브리핑 11건이 전건 `provider_error`로
죽었다(`.env`에 `CHECKON_ALLOW_REAL_LLM=1`이 있는데 코드가 안 봤다). 03 §1
「`os.environ` 직접 접근 금지」 위반이기도 했다.

⇒ 설정은 `.env`를 읽게 되돌리고(운영이 먹어야 한다), **막는 자리를 여기로 옮겼다.**
**막아야 할 것은 테스트지 설정이 아니었다.**

━━ 🔴 왜 이게 없으면 게이트가 약해지나 ━━

`evaluation/pre_pr_verify.py`는 하위 pytest 프로세스에서 이 키를 지운다::

    env.pop(REAL_LLM_OPTIN_ENV, None)     # 🔴 **프로세스 env만** 지운다

`.env`를 읽는 지금은 그 `pop` 직후 **`.env`가 다시 올라온다**(우선순위: 프로세스 env >
`.env` > 선언 기본값). ⇒ 기존 방어가 무력화된다. **이 핀이 그 구멍을 막는다.**

━━ 어떻게 — **두 조치**를 같이 한다 ━━

1. 🔴 **`env_file`을 끊는다** (`RealLlmSettings.model_config["env_file"] = None`) — 이게
   **하중을 받는 쪽**이다.
2. 프로세스 env에 `"0"`을 넣는다 — `pydantic-settings`가 **프로세스 env > `.env`**라
   이것도 이긴다.

🔴 **2만으로는 부족하다 — `console_env_pin`이 겪은 그 함정이다.** 테스트가
`monkeypatch.delenv(REAL_LLM_OPTIN_ENV)`로 「허용 없음」을 만드는 자리가 **5곳** 있고
(2026-08-14 실측 · `test_openai_compat.py`·`test_pg_real_llm_smoke.py` 포함),
프로세스 env를 **지우는** 순간 `.env`가 다시 올라온다::

    monkeypatch.delenv(REAL_LLM_OPTIN_ENV)   # 테스트가 「허용 없음」을 만든다
      → 프로세스 env에서 사라진다
      → 🔴 `.env`의 `=1`이 다시 이긴다        # 2번 조치가 있으나 없으나 같다

⇒ **`.env` 자체를 끊어야** 그 5곳이 선언 기본값 `False`로 떨어진다. 그래서 **그 파일들을
한 줄도 안 고치고** 지금 형태 그대로 안전하다(`tests/ai/llm/`은 B 소유라 특히 중요하다).

🔴 **`pytest.exit`으로 죽이지 않는다.** 그러면 `.env`에 값이 있는 개발자(= 운영 설정을
로컬에 둔 사람 = **정상적인 사용**)가 **아무 테스트도 못 돌린다.** 조용히 끄고,
켜고 싶으면 셸로 명시하게 하는 것이 맞다 — `console_env_pin`의 「막되 열 수 있게」와 같다.

    .env 에만 `=1`            → False  ✅ 이 핀이 덮는다
    셸에서 `=1 uv run pytest` → True   ✅ 프로세스 env가 이겨서 그대로 통한다
    테스트의 `monkeypatch`     → 그대로 ✅ 끊은 것은 **판정 하나**지 설정 경로가 아니다

⚠ **`.env`를 고치지 않는다** — 운영 설정을 `.env`에 두는 것은 옳고, **개인 설정으로
회귀가 흔들리는 구조**가 결함이다(99 #57·#63과 같은 판단).

⚠ **conftest가 아니라 플러그인이다** — `tests/conftest.py`로 두면 mypy가 `conftest` 모듈
중복으로 막는다(2026-08-14 실측: `Duplicate module named "conftest"` · exit 2).
`store_backend_pin`·`llm_provider_pin`·`console_env_pin`과 같은 함정이다.
"""

from __future__ import annotations

import os
from typing import Final

import pytest

from ai.runtime.real_llm import _TRUTHY, REAL_LLM_OPTIN_ENV, RealLlmSettings

#: 이 핀이 끊는 설정 클래스의 이름 — 검사 메시지용.
PINNED_SETTINGS: Final = RealLlmSettings.__name__

#: 🔴 **import 시점에 끊는다** — 테스트 모듈이 `real_llm_optin()`을 부르기 전이어야 한다.
#: ⚠ `setdefault`로는 안 된다(모듈 docstring 「두 조치」 절) — `delenv`가 `.env`를 되살린다.
RealLlmSettings.model_config["env_file"] = None


def pytest_configure(config: pytest.Config) -> None:
    """🔴 테스트에서는 **셸로 명시한 경우만** 실 LLM을 연다 (99 #32).

    `.env`에 `CHECKON_ALLOW_REAL_LLM=1`이 남아 있어도 테스트는 켜지지 않는다 —
    2026-08-09 사고가 **`.env`가 남아서** 났다.

    ⚠ 이건 **둘째 조치**다. 하중은 위 `env_file = None`이 받는다(모듈 docstring).
    여기서 `"0"`을 명시해 두면 하위 프로세스(`pre_pr_verify` → pytest)로도 전파된다.

    ⚠ 실 LLM 스모크는 **셸에서 그 명령에만** 붙여 연다::

        CHECKON_ALLOW_REAL_LLM=1 uv run --frozen pytest tests/…
    """
    if os.environ.get(REAL_LLM_OPTIN_ENV, "").strip().lower() not in _TRUTHY:
        os.environ[REAL_LLM_OPTIN_ENV] = "0"
