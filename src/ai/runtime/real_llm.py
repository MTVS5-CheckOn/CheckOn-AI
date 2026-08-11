"""실 LLM 호출의 **명시적 허용** 게이트 — 모르면 안 부른다 (99 #32).

🔴 **사고(2026-08-09):** `pytest -m integration`이 사용자 키로 실 OpenAI API를 호출했다.
스모크들의 skip 조건이 `"localhost" in openai_base_url`이었고 `.env`의 `OPENAI_BASE_URL`이
**기본값을 덮어** 그 조건이 거짓이 됐다.

⚠ `llm/providers/openai_compat.py`가 *"기본을 localhost로 둬서 미설정을 안전하게 만든다
(fail-safe)"* 로 의도를 적어 뒀고 **그 판단은 옳다 — 기본값은 안 바꿨다.**
🔴 **다만 기본값이 안전한 것은 기본값이 쓰일 때뿐이고** `env_file=".env"`가 그 전제를 깬다.
⇒ **안전은 기본값이 아니라 「명시적 허용」에 걸어야 한다.**

    지금:  localhost가 아니면      → 부른다    🔴 fail-open (사고)
    바꿔:  명시적 opt-in이 없으면  → 안 부른다  ✅ fail-closed (불변식 3의 결)

🔴 **`.env`를 읽지 않는다 — 프로세스 env만 본다.** `.env`는 **한 번 넣으면 남고** 이 사고가
정확히 그 형태였다. 셸 env(`CHECKON_ALLOW_REAL_LLM=1 uv run pytest …`)는 **그 명령에만**
붙는다. ⚠ 그래서 `pydantic-settings`를 쓰지 않는다(그쪽은 `.env`를 읽는다) —
선례는 `runtime/tracing.py`가 `os.environ`을 직접 보는 것이다.

⚠ **PR 전 로컬 검증은 이 변수가 켜져 있으면 시작 전에 실패한다.** 실 LLM 스모크는
사람이 비용과 범위를 승인한 별도 명령에서만 연다.
"""

from __future__ import annotations

import os
from typing import Final

#: 🔴 **실 LLM 호출의 유일한 허용 스위치.** 이 이름을 바꾸면 문서·로컬 검증이 같이 낡는다.
REAL_LLM_OPTIN_ENV: Final = "CHECKON_ALLOW_REAL_LLM"

#: ⚠ **켜는 값을 좁게 둔다** — 오타(`"0"`·`"false"`·빈 값)로 열리면 fail-closed가 아니다.
_TRUTHY: Final = frozenset({"1", "true", "yes", "on"})


def real_llm_optin() -> bool:
    """실 LLM 호출이 **명시적으로 허용**됐는가 — 프로세스 env만 본다."""
    return os.environ.get(REAL_LLM_OPTIN_ENV, "").strip().lower() in _TRUTHY


def real_llm_skip_reason(base_url: str) -> str | None:
    """실 호출을 **하지 말아야 하는 사유** — 없으면 `None`.

    🔴 **판정은 opt-in 하나다.** 종전에는 `base_url`이 판정이었는데 그것이 사고의 원인이라
    **판정에서 뺐다** — 다만 사유 문면에 **어디를 가리키고 있었는지**를 실어 다음 사람이
    *"내 `.env`가 실서버를 가리키는군"* 을 알게 한다(값이 아니라 호스트만 남긴다).

    ⚠ **연결 실패로 인한 skip은 여기 축이 아니다** — 그건 호출자의 `except`가 계속 다룬다.
    이 함수는 *"부를지 말지"* 만 답하고 *"불렀는데 안 되면"* 은 안 다룬다.
    """
    if real_llm_optin():
        return None
    return (
        f"실 LLM 호출은 {REAL_LLM_OPTIN_ENV}=1 없이는 하지 않는다 — skip "
        f"(대상 호스트 {_host_of(base_url)} · 99 #32). "
        f"⚠ 켜려면 셸에서 그 명령에만 붙여라 — `.env`에 넣으면 남는다"
    )


def _host_of(base_url: str) -> str:
    """URL에서 호스트만 — 🔴 경로·쿼리·키가 로그에 남지 않게 한다."""
    without_scheme = base_url.split("://", 1)[-1]
    return without_scheme.split("/", 1)[0] or "?"
