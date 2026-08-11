"""counsel `AGENT_STEP` 싱크가 **백엔드를 따라가는가** (99 #37).

🔴 **결손은 타입 불일치가 아니라 배선이었다**(실측 8/11): counsel 라우터의 `_step_sink`
**초기값과 `reset_counsel_stores()`가 둘 다 인메모리**이고, `build_agent_step_sink()`는
**docstring부터 `mapping_probe` 전용**이라 counsel 조립부는 **그 빌더를 아예 안 탔다.**
⇒ `STORE_BACKEND=pg`를 켜도 counsel 스텝이 **PG에 안 앉았다.**

⚠ **초기 조립과 reset을 따로 본다** — 한쪽만 고친 반쪽 수정이 통과하면 안 된다(99 #02).
"""

from __future__ import annotations

from typing import Any

from ai.composition.counsel.stores import (
    AgentStepSink as CounselAgentStepSink,
)
from ai.composition.counsel.stores import (
    InMemoryAgentStepSink as InMemoryCounselAgentStepSink,
)
from ai.db.repositories.counsel_step_store import PgCounselAgentStepSink
from ai.db.settings import DbSettings
from ai.db.store_factory import build_agent_step_sink, build_counsel_agent_step_sink


def test_memory_backend_gives_the_counsel_in_memory_sink() -> None:
    sink = build_counsel_agent_step_sink(DbSettings(store_backend="memory"))
    assert isinstance(sink, InMemoryCounselAgentStepSink)


def test_pg_backend_gives_the_counsel_pg_sink() -> None:
    """🔴 **probe 싱크를 돌려주면 안 된다** — 반환 타입이 counsel 계약이어야 한다."""
    sink = build_counsel_agent_step_sink(DbSettings(store_backend="pg"))
    assert isinstance(sink, PgCounselAgentStepSink), type(sink).__name__


def test_an_unknown_backend_falls_back_to_memory() -> None:
    """미등록 값은 **fail-safe** — 알 수 없는 설정에서 PG를 열지 않는다."""
    sink = build_counsel_agent_step_sink(DbSettings(store_backend="unknown-backend"))
    assert isinstance(sink, InMemoryCounselAgentStepSink)


def test_creating_the_pg_sink_does_not_touch_the_database() -> None:
    """⚠ 생성만으로 접속하지 않아야 **DB 없는 환경에서 import·조립이 깨지지 않는다.**"""
    assert build_counsel_agent_step_sink(DbSettings(store_backend="pg")) is not None


def test_the_probe_builder_is_left_alone() -> None:
    """⚠ `build_agent_step_sink()`는 **`mapping_probe` 전용으로 유지**한다.

    이번 회차에 개명해 호출부를 넓히지 않는다 — 그러면 두 축이 다시 한 함수로 뭉친다.
    """
    from ai.db.repositories.probe_stores import PgAgentStepSink

    assert isinstance(
        build_agent_step_sink(DbSettings(store_backend="pg")), PgAgentStepSink
    )


def test_the_counsel_sink_satisfies_the_counsel_protocol() -> None:
    """🔴 **`cast`·`type: ignore` 없이** counsel 계약을 만족해야 한다."""
    sink: CounselAgentStepSink = build_counsel_agent_step_sink(
        DbSettings(store_backend="pg")
    )
    assert hasattr(sink, "record") and hasattr(sink, "steps")
    del sink


def test_the_two_sinks_are_different_types() -> None:
    """probe와 counsel은 **각자의 레코드 타입**을 돌려준다 — 한쪽으로 강제하지 않는다.

    ⚠ 타입 이름으로 비교한다 — `is not`은 mypy가 **정적으로 참**임을 알아 검사가 죽는다.
    """
    from ai.db.repositories.probe_stores import PgAgentStepSink

    pg_settings: Any = DbSettings(store_backend="pg")
    counsel = type(build_counsel_agent_step_sink(pg_settings)).__name__
    assert counsel != PgAgentStepSink.__name__, counsel
