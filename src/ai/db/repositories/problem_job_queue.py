"""재시작 뒤 문제 생성 잡을 다시 발견하는 PostgreSQL 조회."""

from __future__ import annotations

from sqlalchemy import Select, and_, distinct, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ai.contracts.agents import JobPhase, WorkerKind
from ai.db.models import AgentRun

_LEASED_PHASES: tuple[JobPhase, ...] = (
    JobPhase.LEASED,
    JobPhase.RUNNING,
)


def build_pending_problem_tenants_query(*, limit: int) -> Select[tuple[str]]:
    """대기 또는 회수 대상 PG 테넌트의 제한·결정론 조회를 만든다."""

    if limit < 1:
        raise ValueError("limit은 1 이상이어야 한다")
    return (
        select(distinct(AgentRun.tenant_id))
        .where(
            AgentRun.agent_kind == WorkerKind.PROBLEM_GENERATION.value,
            or_(
                AgentRun.status == JobPhase.QUEUED.value,
                and_(
                    AgentRun.status.in_([phase.value for phase in _LEASED_PHASES]),
                    AgentRun.lease_expires_at <= func.now(),
                ),
            ),
        )
        .order_by(AgentRun.tenant_id)
        .limit(limit)
    )


async def pending_problem_tenants(
    *,
    sessionmaker: async_sessionmaker[AsyncSession],
    limit: int,
) -> list[str]:
    """대기 또는 회수 대상 PG 잡이 있는 테넌트를 결정론 순서로 제한 조회한다."""

    statement = build_pending_problem_tenants_query(limit=limit)
    async with sessionmaker() as session:
        result = await session.execute(statement)
        return [tenant_id for tenant_id in result.scalars()]


__all__ = ["build_pending_problem_tenants_query", "pending_problem_tenants"]
