"""PG 재시작 복구 대상 테넌트 조회 계약."""

from __future__ import annotations

import pytest
from sqlalchemy.dialects import postgresql

from ai.db.repositories.problem_job_queue import (
    build_pending_problem_tenants_query,
)


def test_pending_query_is_pg_only_bounded_and_deterministic() -> None:
    statement = build_pending_problem_tenants_query(limit=37)
    sql = str(
        statement.compile(
            dialect=postgresql.dialect(),  # type: ignore[no-untyped-call]
            compile_kwargs={"literal_binds": True},
        )
    )

    assert "agent_run.agent_kind = 'problem_generation'" in sql
    assert "agent_run.status = 'queued'" in sql
    assert "agent_run.status IN ('leased', 'running')" in sql
    assert "agent_run.lease_expires_at <= now()" in sql
    assert "ORDER BY agent_run.tenant_id" in sql
    assert "LIMIT 37" in sql


def test_pending_query_rejects_unbounded_zero_limit() -> None:
    with pytest.raises(ValueError, match="1 이상"):
        build_pending_problem_tenants_query(limit=0)
