"""PostgreSQL 슈퍼바이저 JobStore의 매핑·SQL 안전성·fail-closed 검사."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import OperationalError
from sqlalchemy.sql import ClauseElement

from ai.agents.job_store import JobStore, JobStoreError
from ai.contracts.agents import (
    JobPhase,
    OperationKind,
    PriorityClass,
    WorkerJob,
    WorkerKind,
)
from ai.db.repositories.agent_job import (
    PgJobStore,
    _job_from_row,
    _row_from_job,
    build_lease_next_query,
    build_owned_lease_query,
)

_T0 = datetime(2026, 7, 27, 9, 0, tzinfo=UTC)
_JOB_ID = UUID("00000000-0000-4000-8000-000000000101")
_RUN_ID = UUID("00000000-0000-4000-8000-000000000102")
_HASH = f"sha256:{'a' * 64}"


def _run[T](awaitable: Awaitable[T]) -> T:
    return asyncio.run(awaitable)  # type: ignore[arg-type]


def _job() -> WorkerJob:
    return WorkerJob(
        job_id=_JOB_ID,
        execution_id=_RUN_ID,
        tenant_id="teacher_alias_1",
        worker_kind=WorkerKind.PROBLEM_GENERATION,
        operation=OperationKind.PROBLEM_SET_GENERATE,
        payload_ref="problem-set://input/1",
        payload_hash=_HASH,
        priority_class=PriorityClass.STANDARD,
        queued_at=_T0,
    )


class _RaisingSessionmaker:
    def __call__(self) -> _RaisingSessionmaker:
        return self

    async def __aenter__(self) -> _RaisingSessionmaker:
        raise OperationalError("boom", None, Exception("db down"))

    async def __aexit__(self, *exc: object) -> None:
        return None


def _pg_store() -> PgJobStore:
    return PgJobStore(sessionmaker=_RaisingSessionmaker())  # type: ignore[arg-type]


def _compiled(statement: ClauseElement) -> str:
    dialect = postgresql.dialect()  # type: ignore[no-untyped-call]
    return str(
        statement.compile(
            dialect=dialect,
            compile_kwargs={"literal_binds": False},
        )
    )


def test_pg_store_satisfies_job_store_protocol() -> None:
    assert isinstance(_pg_store(), JobStore)


def test_worker_job_round_trip_preserves_contract_fields() -> None:
    original = _job()
    row = _row_from_job(original)
    restored = _job_from_row(row)
    assert restored == original
    assert row.state_checkpoint == {}
    assert row.status == JobPhase.QUEUED.value


def test_lease_query_is_tenant_scoped_aged_and_skip_locked() -> None:
    statement = build_lease_next_query(
        tenant_id="teacher_alias_1",
        worker_kind=WorkerKind.PROBLEM_GENERATION,
        acquired_at=_T0 + timedelta(hours=2),
        priority_aging_interval=timedelta(hours=1),
    )
    sql = _compiled(statement)
    assert "agent_run.tenant_id =" in sql
    assert "agent_run.agent_kind =" in sql
    assert "agent_run.status =" in sql
    assert "least(" in sql
    assert "floor(" in sql
    assert "CASE WHEN" in sql
    assert "ORDER BY" in sql
    assert "agent_run.queued_at" in sql
    assert "agent_run.id" in sql
    assert "FOR UPDATE SKIP LOCKED" in sql


def test_owned_query_contains_all_fencing_conditions() -> None:
    statement = build_owned_lease_query(
        tenant_id="teacher_alias_1",
        job_id=_JOB_ID,
        lease_owner="worker-1",
        lease_generation=7,
        observed_at=_T0,
    )
    sql = _compiled(statement)
    assert "agent_run.tenant_id =" in sql
    assert "agent_run.id =" in sql
    assert "agent_run.status IN" in sql
    assert "agent_run.lease_owner =" in sql
    assert "agent_run.lease_generation =" in sql
    assert "agent_run.lease_expires_at >" in sql
    assert sql.endswith("FOR UPDATE")


def test_invalid_aging_interval_is_rejected_before_db() -> None:
    with pytest.raises(ValueError, match="priority_aging_interval"):
        build_lease_next_query(
            tenant_id="teacher_alias_1",
            worker_kind=WorkerKind.COUNSEL_PACK,
            acquired_at=_T0,
            priority_aging_interval=timedelta(0),
        )


def test_pg_get_failure_is_fail_closed() -> None:
    with pytest.raises(JobStoreError, match="DB 오류"):
        _run(_pg_store().get(tenant_id="teacher_alias_1", job_id=_JOB_ID))


def test_pg_add_failure_is_fail_closed() -> None:
    with pytest.raises(JobStoreError, match="DB 오류"):
        _run(_pg_store().add(_job()))


def test_add_rejects_noninitial_lease_generation_before_db() -> None:
    values = _job().model_dump(mode="python")
    values["lease_generation"] = 1
    noninitial = WorkerJob.model_validate(values)
    with pytest.raises(ValueError, match="최초 queued"):
        _run(_pg_store().add(noninitial))
