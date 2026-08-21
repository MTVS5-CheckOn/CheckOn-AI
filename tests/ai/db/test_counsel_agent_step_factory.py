"""counsel `AGENT_STEP` 싱크가 **백엔드를 따라가는가** (99 #37).

🔴 **결손은 타입 불일치가 아니라 배선이었다**(실측 8/11): counsel 라우터의 `_step_sink`
**초기값과 `reset_counsel_stores()`가 둘 다 인메모리**이고, `build_agent_step_sink()`는
**docstring부터 `mapping_probe` 전용**이라 counsel 조립부는 **그 빌더를 아예 안 탔다.**
⇒ `STORE_BACKEND=pg`를 켜도 counsel 스텝이 **PG에 안 앉았다.**

⚠ **초기 조립과 reset을 따로 본다** — 한쪽만 고친 반쪽 수정이 통과하면 안 된다(99 #02).
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from ai.composition.counsel.stores import (
    AgentStepSink as CounselAgentStepSink,
)
from ai.composition.counsel.stores import (
    InMemoryAgentStepSink as InMemoryCounselAgentStepSink,
)
from ai.db.repositories.counsel_step_store import PgCounselAgentStepSink
from ai.db.settings import DbSettings
from ai.db.store_factory import build_counsel_agent_step_sink

#: ⚠ 🔴 **(8/22) 검사 둘을 뺐다** — `test_the_probe_builder_is_left_alone` ·
#: `test_the_two_sinks_are_different_types`. 둘 다 **probe 축 sink 가 존재한다**는 전제 위에
#: 서 있었는데, import 축 개발 중단으로 `build_agent_step_sink`·`PgAgentStepSink` 가
#: **사라졌다**(99 #187).
#: 🔴 **그 둘이 지키던 것은 「두 축이 한 함수로 뭉치지 않는다」였고, 축이 하나가 되면서
#: 그 위험 자체가 없어졌다** — 되살아나면 그때 다시 세워야 한다(import 축 부활 시).
#: ⚠ 아래 counsel 쪽 검사들은 그대로다 — 이 파일의 본래 대상이다.


def test_memory_backend_gives_the_counsel_in_memory_sink() -> None:
    sink = build_counsel_agent_step_sink(DbSettings(store_backend="memory"))
    assert isinstance(sink, InMemoryCounselAgentStepSink)


def test_pg_backend_gives_the_counsel_pg_sink() -> None:
    """🔴 **probe 싱크를 돌려주면 안 된다** — 반환 타입이 counsel 계약이어야 한다."""
    sink = build_counsel_agent_step_sink(DbSettings(store_backend="pg"))
    assert isinstance(sink, PgCounselAgentStepSink), type(sink).__name__


def test_an_unknown_backend_never_reaches_this_factory() -> None:
    """🔴 **미등록 값은 여기 오기 전에 거부된다**(99 #38) — memory로 강등되지 않는다.

    ⚠ **이 검사는 뒤집힌 것이다.** 종전엔 *"미등록 값 → memory fail-safe"* 를 **정답으로
    고정**했는데, 그건 **오타 하나로 영속성이 조용히 꺼지는 결함**을 계약으로 굳힌 것이었다.
    ⇒ 이제 **설정 경계**가 막고, 팩토리는 **막힌 뒤의 두 값만** 본다.
    """
    with pytest.raises(ValidationError):
        DbSettings(store_backend="unknown-backend")


def test_creating_the_pg_sink_does_not_touch_the_database() -> None:
    """⚠ 생성만으로 접속하지 않아야 **DB 없는 환경에서 import·조립이 깨지지 않는다.**"""
    assert build_counsel_agent_step_sink(DbSettings(store_backend="pg")) is not None



def test_the_counsel_sink_satisfies_the_counsel_protocol() -> None:
    """🔴 **`cast`·`type: ignore` 없이** counsel 계약을 만족해야 한다."""
    sink: CounselAgentStepSink = build_counsel_agent_step_sink(
        DbSettings(store_backend="pg")
    )
    assert hasattr(sink, "record") and hasattr(sink, "steps")
    del sink

