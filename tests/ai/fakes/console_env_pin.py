"""테스트 전역 플러그인 — **콘솔 플래그를 개발자 `.env`에서 끊는다** (99 #63).

🔴 **개발자의 로컬 `.env` 하나가 「기본 꺼짐」 가드를 뒤집고 있었다.** 맥 `.env`에
`CONSOLE_ERROR_LOG=1`·`CONSOLE_LLM_LOG=1`이 들어가면서(콘솔 관측을 쓰라고 만든 기능이니
**정상적인 사용**이다) `test_console_observability.py` **3건이 상시 red**가 됐다.

🔴 **그 셋의 존재 이유가 「플래그가 없으면 아무것도 출력되지 않는다」를 지키는 것이다** —
red인 동안 그 보장이 없었다. **가드가 red면 가드가 아니다**(99 #57의 같은 문장).

⚠ **그리고 저장소 표준 검증이 통째로 막혔다** — `pre_pr_verify`가 offline 단계에서
멈추니 그 뒤 PostgreSQL integration 단계가 **아예 안 돈다**(2026-08-13 준영님 PR-π 보고).
앞으로 모든 PR이 같은 자리에서 걸린다. 이 핀의 목적이 그 복구다.

━━ 🔴 `setdefault` 핀으로는 안 된다 — PR-ω와 여기가 갈린다 ━━

99 #57은 `os.environ.setdefault("LLM_PROVIDER", "fake")`로 풀렸다. 여기서는 **안 된다**::

    monkeypatch.delenv("CONSOLE_LLM_LOG")   # 테스트가 「플래그 없음」을 만든다
      → 프로세스 env에서 사라진다
      → 🔴 `.env`의 `=1`이 다시 이긴다        # 핀이 있으나 없으나 같다

`pydantic-settings` 우선순위는 **init 인자 > 프로세스 env > `.env` > 선언 기본값**이라,
프로세스 env를 **지우는** 순간 `.env`가 올라온다. ⇒ **`.env` 자체를 끊어야 한다.**

━━ 어떻게 — `env_file`을 떼어 낸다 ━━

`ConsoleSettings.model_config["env_file"] = None`. 실측(2026-08-13)::

    .env 그대로        → True   🔴 `.env`가 이긴다
    env_file=None      → False  ✅ 선언 기본값
    명시 주입          → True   ✅ 여전히 이긴다
    프로세스 env       → True   ✅ 여전히 이긴다

🔴 **막되 열 수 있게** — 콘솔을 켜야 하는 검사는 `monkeypatch.setenv`로 자기 값을 세우면
그대로 통한다. 끊은 것은 **파일 하나**지 설정 경로 전체가 아니다.

⚠ **`.env`를 고치지 않는다** — 진희님이 콘솔을 켜 두는 것은 정상이고, **개인 설정으로
회귀가 흔들리는 구조**가 결함이다(99 #57과 같은 판단). 다음 사람의 `.env`는 또 다르다.

⚠ **conftest가 아니라 플러그인이다** — `tests/conftest.py`로 두면 mypy가 `conftest` 모듈
중복으로 막는다(`store_backend_pin.py`·`llm_provider_pin.py` 주석과 같은 함정).
"""

from __future__ import annotations

from typing import Final

from ai.runtime.console import ConsoleSettings, get_console_settings

#: 이 핀이 끊는 설정 클래스의 이름 — 검사 메시지용.
PINNED_SETTINGS: Final = ConsoleSettings.__name__

#: 🔴 **import 시점에 끊는다** — 테스트 모듈이 `get_console_settings()`를 부르기 전이어야
#: 한다. `lru_cache`라 한 번 읽히면 그 값이 굳는다(`store_backend_pin` 실측과 같은 함정).
ConsoleSettings.model_config["env_file"] = None
get_console_settings.cache_clear()
