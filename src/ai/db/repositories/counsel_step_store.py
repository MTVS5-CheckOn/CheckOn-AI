"""counsel `AGENT_STEP` PG adapter — **counsel 타입으로 받고 counsel 타입으로 준다** (99 #37).

🔴 **probe의 `AgentStepRecord`를 쓰지 않는다.** 필드 8개가 같아도 **별개 타입**이고,
capability 간 직접 import는 규율 위반이다. ⚠ `cast`·`type: ignore`·포괄적 `Any`로
mypy만 침묵시키지 않는다 — **경계에서 실제로 변환한다.**

⚠ **SQL은 여기 없다** — `agent_step_store.PgAgentStepStore`가 든다. 복제하면
한쪽만 고쳐진다(99 #02).

⚠ **⑱을 닫지 않는다** — 두 레코드를 `contracts/agents.py`로 **승격할지는 별도 판정**이고
그 파일은 양자 승인이다. 이 파일은 **승격 없이** PG 적재·조립만 정상화한다.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ai.composition.counsel.stores import AgentStepRecord
from ai.db.repositories.agent_step_store import AgentStepSnapshot, PgAgentStepStore


class PgCounselAgentStepSink:
    """counsel `AgentStepSink` 계약의 PG 구현."""

    def __init__(self, *, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._store = PgAgentStepStore(sessionmaker=sessionmaker)

    async def record(self, step: AgentStepRecord) -> None:
        await self._store.insert(_to_snapshot(step))

    async def steps(self, agent_run_id: UUID) -> tuple[AgentStepRecord, ...]:
        return tuple(
            _from_snapshot(snapshot)
            for snapshot in await self._store.select_by_run(agent_run_id)
        )


def _to_snapshot(step: AgentStepRecord) -> AgentStepSnapshot:
    """🔴 **필드를 이름으로 옮긴다** — 필드가 늘면 여기서 mypy가 막는다."""
    return AgentStepSnapshot(
        id=step.id,
        agent_run_id=step.agent_run_id,
        seq=step.seq,
        node_name=step.node_name,
        tool_called=step.tool_called,
        tool_args_masked=step.tool_args_masked,
        llm_call_id=step.llm_call_id,
        outcome=step.outcome,
    )


def _from_snapshot(snapshot: AgentStepSnapshot) -> AgentStepRecord:
    return AgentStepRecord(**snapshot)


__all__ = ["PgCounselAgentStepSink"]
