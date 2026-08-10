"""`AGENT_RUN` ↔ `AI_RUN` 원장 완전성 **조회** — 읽기 전용 (99 #36 G2).

🔴 **매 쓰기 저장소가 아니다.** 쓰기 경로에 넣으면 **FK를 애플리케이션으로 옮기는 것**이고,
모든 쓰기에 비용을 물리면서도 **「나중에 생길 원장」을 못 기다린다.** 이 점검은 **사후 축**이다.

⚠ **데이터를 고치지 않는다** — `SELECT`만 한다.
🔴 **테넌트가 인자다** — 전 테넌트 전수 조회·필터 없는 조인·남의 식별자 노출을 하지 않는다
(CLAUDE.md §4 · 99 #23).

판정은 여기 없다 — `db/ledger_completeness.judge_ledger_row()`가 든다. **조회와 판정을 섞으면
「무엇을 봤는가」와 「무엇으로 판단했는가」가 한 함수에 묻힌다.**
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from sqlalchemy import Select, func, select

from ai.contracts.agents import JobPhase, WorkerKind
from ai.contracts.execution import Capability
from ai.db.counsel_read_model import SessionFactory
from ai.db.ledger_completeness import LedgerObservation
from ai.db.models import AgentRun, AgentStep, AiRun


def observation_query(tenant_id: str) -> Select[tuple[object, ...]]:
    """관측 행 조회 — 🔴 **테넌트 술어가 없으면 만들 수 없는 모양**이다.

    ⚠ `AI_RUN` 조인도 **테넌트를 함께 건다** — `execution_id`만으로 이으면
    남의 테넌트 행이 붙을 수 있고, 그러면 「원장이 있다」가 거짓이 된다.
    """
    if not tenant_id:
        raise ValueError("원장 점검은 테넌트 범위를 반드시 받는다")
    steps = (
        select(
            AgentStep.agent_run_id.label("agent_run_id"),
            func.count().label("step_count"),
            func.count(AgentStep.llm_call_id).label("steps_with_llm_call"),
        )
        .group_by(AgentStep.agent_run_id)
        .subquery()
    )
    return (
        select(
            AgentRun.tenant_id,
            AgentRun.id,
            AgentRun.run_id,
            AgentRun.agent_kind,
            AgentRun.status,
            AgentRun.started_at,
            AgentRun.error_code,
            AgentRun.result_ref,
            func.coalesce(steps.c.step_count, 0),
            func.coalesce(steps.c.steps_with_llm_call, 0),
            AiRun.execution_id,
            AiRun.tenant_id.label("ai_run_tenant_id"),
            AiRun.capability,
        )
        .outerjoin(steps, steps.c.agent_run_id == AgentRun.id)
        #: 🔴 **테넌트를 조인 조건에 함께 건다** — 남의 원장이 붙으면 안 된다.
        .outerjoin(
            AiRun,
            (AiRun.execution_id == AgentRun.run_id)
            & (AiRun.tenant_id == AgentRun.tenant_id),
        )
        .where(AgentRun.tenant_id == tenant_id)
        .order_by(AgentRun.queued_at)
    )


class PgLedgerAudit:
    """읽기 전용 원장 점검 조회."""

    def __init__(self, sessionmaker: SessionFactory) -> None:
        self._sessions = sessionmaker

    async def observe(self, *, tenant_id: str) -> Sequence[LedgerObservation]:
        async with self._sessions() as session:
            rows = (await session.execute(observation_query(tenant_id))).all()
        return [_to_observation(row) for row in rows]


def _to_observation(row: Any) -> LedgerObservation:  # noqa: ANN401 — SQLAlchemy Row
    (
        tenant,
        job_id,
        run_id,
        agent_kind,
        status,
        started_at,
        error_code,
        result_ref,
        step_count,
        steps_with_llm_call,
        execution_id,
        ai_run_tenant,
        capability,
    ) = row
    return LedgerObservation(
        tenant_id=str(tenant),
        job_id=job_id,
        run_id=run_id,
        agent_kind=WorkerKind(agent_kind),
        status=JobPhase(status),
        started_at_is_set=started_at is not None,
        error_code=None if error_code is None else str(error_code),
        result_ref=None if result_ref is None else str(result_ref),
        step_count=int(step_count),
        steps_with_llm_call=int(steps_with_llm_call),
        ai_run_execution_id=execution_id,
        ai_run_tenant_id=None if ai_run_tenant is None else str(ai_run_tenant),
        ai_run_capability=None if capability is None else Capability(str(capability)),
    )


__all__ = ["PgLedgerAudit", "observation_query"]
