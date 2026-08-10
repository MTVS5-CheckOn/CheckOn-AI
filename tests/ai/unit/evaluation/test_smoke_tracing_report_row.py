"""리포트 0절이 **끈 추적을 경고로 적는가** (6차 실측 2026-08-10).

🔴 `active_tracing_env_names()` 하나만 고치면 값은 맞아지지만, **리포트가 그 값을 어떻게
읽는지**는 여전히 안 보인다. 0절은 목록이 **비었을 때만** 「전부 비활성 ✅」을 찍고
아니면 `⚠ [...]`를 찍는다 ⇒ **판정 문면 자체를 회귀로 잡는다.**

⚠ 이 회차의 실제 실행 조건이 `LANGSMITH_TRACING=false`였다 — **지시서가 그렇게 명시했다.**
가장 안전하게 돌린 회차가 **「⚠ 추적 env 활성」으로 기록될 뻔했다.**
"""

from __future__ import annotations

import pytest

from ai.evaluation.counsel_llm_smoke import _tracing_cell
from ai.runtime.tracing import TRACING_ENV_SYNONYMS, active_tracing_env_names

_WARN = "⚠"
_CLEAR = "전부 비활성"


def _preflight_like(monkeypatch: pytest.MonkeyPatch, **env: str) -> dict[str, object]:
    """`_preflight()`가 담는 것과 **같은 방식으로** 목록을 만든다 — 손사본이 아니다."""
    for name in TRACING_ENV_SYNONYMS:
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    return {"tracing_env_active": list(active_tracing_env_names())}


def test_an_explicit_false_does_not_warn(monkeypatch: pytest.MonkeyPatch) -> None:
    """🔴 **6차의 실행 조건 그대로** — 경고가 뜨면 안 된다."""
    cell = _tracing_cell(_preflight_like(monkeypatch, LANGSMITH_TRACING="false"))
    assert _WARN not in cell, f"끈 추적을 경고로 적는다: {cell}"
    assert _CLEAR in cell, f"비활성이라고 말하지 않는다: {cell}"


def test_nothing_set_does_not_warn(monkeypatch: pytest.MonkeyPatch) -> None:
    """종전 회차의 조건(아예 unset) — 이 경로는 원래 정상이었다."""
    cell = _tracing_cell(_preflight_like(monkeypatch))
    assert _WARN not in cell and _CLEAR in cell


def test_a_real_activation_still_warns(monkeypatch: pytest.MonkeyPatch) -> None:
    """🔴 **뒤집기 — 좁히다가 경고를 죽이면 안 된다.** 진짜 켜지면 이름과 함께 경고한다."""
    cell = _tracing_cell(_preflight_like(monkeypatch, LANGCHAIN_TRACING_V2="true"))
    assert _WARN in cell, f"진짜 활성인데 경고가 없다: {cell}"
    assert "LANGCHAIN_TRACING_V2" in cell, f"어떤 env인지 안 적는다: {cell}"
    assert "true" not in cell, f"🔴 값이 문면에 샜다(근처에 API 키가 있다): {cell}"
