"""counsel `AGENT_STEP`이 **실 PG를 무손실로 왕복하는가** (99 #37).

🔴 **필드가 같다는 이유로 probe 타입을 쓰지 않는다** — counsel adapter는
**counsel `AgentStepRecord`를 받고 counsel `AgentStepRecord`를 돌려준다.**
⚠ 부모 `AGENT_RUN`은 **실제 저장소**로 만든다 — 손으로 INSERT하지 않는다.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any, Final
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from ai.agents.supervisor import Supervisor
from ai.composition.counsel.stores import AgentStepRecord as CounselAgentStepRecord
from ai.contracts.agents import OperationKind, PriorityClass, WorkerJob, WorkerKind
from ai.db.repositories.agent_job import PgJobStore
from ai.db.repositories.counsel_step_store import PgCounselAgentStepSink
from ai.db.settings import get_db_settings

pytestmark = pytest.mark.integration

_NOW: Final = datetime(2026, 8, 12, 9, 0, tzinfo=UTC)
_HASH: Final = "sha256:" + "c" * 64


def _tenant() -> str:
    return f"t_step_{uuid.uuid4().hex[:8]}"


type _Scenario = Callable[[async_sessionmaker[AsyncSession]], Awaitable[None]]


def _run(scenario: _Scenario, *, tenant: str) -> None:
    async def go() -> str:
        engine = create_async_engine(get_db_settings().database_url, poolclass=NullPool)
        try:
            async with engine.connect():
                pass
        except Exception:  # noqa: BLE001
            await engine.dispose()
            return "skip"
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with asyncio.timeout(20.0):
                await scenario(sessions)
        finally:
            async with sessions() as session, session.begin():
                await session.execute(
                    text(
                        "DELETE FROM agent_step WHERE agent_run_id IN"
                        " (SELECT id FROM agent_run WHERE tenant_id = :t)"
                    ),
                    {"t": tenant},
                )
                await session.execute(
                    text("DELETE FROM agent_run WHERE tenant_id = :t"), {"t": tenant}
                )
            await engine.dispose()
        return "ok"

    if asyncio.run(go()) == "skip":
        pytest.skip("실 PG 미가용 — docker compose up -d")


async def _parent_job(
    sessions: async_sessionmaker[AsyncSession], tenant: str
) -> WorkerJob:
    """🔴 **실제 저장소로** 부모 `AGENT_RUN`을 만든다 — 손 INSERT를 쓰지 않는다."""
    supervisor = Supervisor(
        store=PgJobStore(sessionmaker=sessions),
        lease_duration=timedelta(minutes=5),
        priority_aging_interval=timedelta(minutes=1),
        clock=lambda: _NOW,
    )
    return await supervisor.enqueue(
        WorkerJob(
            job_id=uuid.uuid4(),
            execution_id=uuid.uuid4(),
            tenant_id=tenant,
            worker_kind=WorkerKind.COUNSEL_PACK,
            operation=OperationKind.COUNSEL_PACK_GENERATE,
            payload_ref="context://step-roundtrip",
            payload_hash=_HASH,
            priority_class=PriorityClass.BATCH,
            queued_at=_NOW,
        )
    )


def _step(
    agent_run_id: UUID,
    *,
    seq: int,
    llm_call_id: UUID | None = None,
    args: dict[str, Any] | None = None,
) -> CounselAgentStepRecord:
    return CounselAgentStepRecord(
        id=uuid.uuid4(),
        agent_run_id=agent_run_id,
        seq=seq,
        node_name=f"node-{seq}",
        tool_called=None if seq % 2 else f"tool-{seq}",
        tool_args_masked=args if args is not None else {"seq": seq},
        llm_call_id=llm_call_id,
        outcome="ok",
    )


def test_a_counsel_step_round_trips_with_every_field() -> None:
    """🔴 **8필드 정확 대조** — 하나라도 빠지면 저수준 적재가 값을 깎는 것이다."""
    tenant = _tenant()

    async def scenario(sessions: async_sessionmaker[AsyncSession]) -> None:
        job = await _parent_job(sessions, tenant)
        sink = PgCounselAgentStepSink(sessionmaker=sessions)
        call_id = uuid.uuid4()
        original = _step(
            job.job_id,
            seq=1,
            llm_call_id=call_id,
            args={"nested": {"a": [1, 2], "b": None}, "flat": "값"},
        )
        await sink.record(original)

        rows = await sink.steps(job.job_id)
        assert len(rows) == 1, f"행이 1건이 아니다: {rows}"
        restored = rows[0]
        #: 🔴 **counsel 타입이어야 한다** — probe 레코드를 돌려주면 계약이 갈린다.
        assert type(restored) is CounselAgentStepRecord, type(restored).__name__
        assert restored == original, f"왕복에서 값이 바뀌었다:\n{restored}\n{original}"
        assert restored.tool_args_masked == original.tool_args_masked
        assert restored.llm_call_id == call_id

    _run(scenario, tenant=tenant)


def test_a_null_llm_call_id_round_trips() -> None:
    """`None`도 왕복해야 한다 — 「호출 없음」과 「값을 잃었다」는 다른 사실이다."""
    tenant = _tenant()

    async def scenario(sessions: async_sessionmaker[AsyncSession]) -> None:
        job = await _parent_job(sessions, tenant)
        sink = PgCounselAgentStepSink(sessionmaker=sessions)
        await sink.record(_step(job.job_id, seq=1, llm_call_id=None))
        rows = await sink.steps(job.job_id)
        assert rows[0].llm_call_id is None

    _run(scenario, tenant=tenant)


def test_steps_come_back_in_seq_order() -> None:
    """🔴 **역순으로 넣어도 `seq` 오름차순**으로 나와야 한다."""
    tenant = _tenant()

    async def scenario(sessions: async_sessionmaker[AsyncSession]) -> None:
        job = await _parent_job(sessions, tenant)
        sink = PgCounselAgentStepSink(sessionmaker=sessions)
        for seq in (3, 1, 2):
            await sink.record(_step(job.job_id, seq=seq))
        assert [row.seq for row in await sink.steps(job.job_id)] == [1, 2, 3]

    _run(scenario, tenant=tenant)


def test_another_jobs_steps_are_not_returned() -> None:
    """다른 `agent_run_id`의 행은 안 나온다 — 부모를 통해 격리된다."""
    tenant = _tenant()

    async def scenario(sessions: async_sessionmaker[AsyncSession]) -> None:
        mine = await _parent_job(sessions, tenant)
        other = await _parent_job(sessions, tenant)
        sink = PgCounselAgentStepSink(sessionmaker=sessions)
        await sink.record(_step(mine.job_id, seq=1))
        await sink.record(_step(other.job_id, seq=1))
        rows = await sink.steps(mine.job_id)
        assert len(rows) == 1
        assert rows[0].agent_run_id == mine.job_id

    _run(scenario, tenant=tenant)


def test_an_orphan_step_is_not_translated_into_success() -> None:
    """🔴 **부모 없는 `agent_run_id`는 실패해야 한다** — FK 위반을 성공으로 번역하지 않는다.

    ⚠ `agent_step.agent_run_id → agent_run.id` FK는 **살아 있다**(G1은 `agent_run.run_id`만
    걷었다). 여기서 조용히 넘어가면 **스텝이 어디에도 안 붙은 채** 있다고 믿게 된다.
    """
    tenant = _tenant()

    async def scenario(sessions: async_sessionmaker[AsyncSession]) -> None:
        sink = PgCounselAgentStepSink(sessionmaker=sessions)
        with pytest.raises(Exception):  # noqa: B017 — 드라이버 예외 종류를 안 좁힌다
            await sink.record(_step(uuid.uuid4(), seq=1))

    _run(scenario, tenant=tenant)
