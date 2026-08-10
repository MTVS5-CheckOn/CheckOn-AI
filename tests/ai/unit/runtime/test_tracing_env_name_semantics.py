"""`active_tracing_env_names()`가 **「켜졌다」와 「적혀 있다」를 가르는가** (6차 실측 2026-08-10).

🔴 **실측 오탐이다.** 6차 스모크를 지시서대로 `LANGSMITH_TRACING=false`로 돌렸더니:

```text
external_tracing_active()  = False               ← 정본 판정: 비활성 (맞다)
active_tracing_env_names() = ('LANGSMITH_TRACING',)   ← 「활성 env」라며 이름을 냈다
```

⚠ **끄려고 명시한 것이 경고를 만든다.** 리포트 0절은 이 목록이 비었을 때만
「전부 비활성 ✅」을 찍으므로, **가장 안전하게 실행한 회차가 「⚠ 추적 env 활성」으로 기록된다.**

원인은 `os.environ.get(name)` — **`"false"`는 빈 문자열이 아니라 참**이다.
**이름과 docstring은 「켜져 있는」인데 보는 것은 「적혀 있는」이었다**(로그 85 계열).

🔴 **좁히되 fail-closed로 좁힌다** — 명시적으로 거짓인 값만 「꺼짐」이고, **모르는 값은 켜진 것**이다
(`"local"`처럼 수집이 도는 값을 놓치면 안 된다). 종전 회차가 env를 아예 안 걸어
**목록이 비어 있었기 때문에** 이 오탐은 지금까지 안 보였다.
"""

from __future__ import annotations

import pytest

from ai.runtime.tracing import TRACING_ENV_SYNONYMS, active_tracing_env_names

_NAME = "LANGSMITH_TRACING"


def test_the_probed_name_is_one_the_helper_actually_reads() -> None:
    """🔴 절단 가드 — 시험용 이름이 동의어 목록 밖이면 이 파일은 아무것도 안 본다."""
    assert _NAME in TRACING_ENV_SYNONYMS


@pytest.mark.parametrize("value", ["false", "False", "FALSE", "0", "no", "off", " false "])
def test_an_explicitly_disabled_env_is_not_reported_as_active(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    """🔴 **끈 것을 켜졌다고 하지 않는다** — 6차가 이 오탐을 리포트에 실을 뻔했다."""
    monkeypatch.setenv(_NAME, value)
    assert _NAME not in active_tracing_env_names(), (
        f"{_NAME}={value!r}인데 활성으로 보고한다 — 끄려고 명시한 것이 경고가 된다"
    )


def test_an_unset_env_is_not_reported_as_active(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in TRACING_ENV_SYNONYMS:
        monkeypatch.delenv(name, raising=False)
    assert active_tracing_env_names() == ()


@pytest.mark.parametrize("value", ["true", "1", "yes", "on", "local", "maybe", "2"])
def test_anything_not_explicitly_false_is_reported_as_active(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    """🔴 **fail-closed** — 모르는 값은 켜진 것으로 본다.

    ⚠ `"local"`은 라이브러리가 **수집이 도는 상태**로 취급한다. 화이트리스트로 좁히면
    새 값이 나올 때 **조용히 안 잡힌다** ⇒ **거짓인 값만 열거하고 나머지는 전부 활성**이다.
    """
    monkeypatch.setenv(_NAME, value)
    assert _NAME in active_tracing_env_names(), (
        f"{_NAME}={value!r}를 비활성으로 봤다 — 모르는 값은 켜진 것으로 봐야 한다"
    )


def test_only_the_enabling_name_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    """둘 이상 걸렸을 때 **범인만** 나온다 — 에러 메시지가 무고한 이름을 섞지 않는다."""
    for name in TRACING_ENV_SYNONYMS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "true")
    assert active_tracing_env_names() == ("LANGCHAIN_TRACING_V2",)
