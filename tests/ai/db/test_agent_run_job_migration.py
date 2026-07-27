"""AGENT_RUN WorkerJob 원장 증분 마이그레이션 정적 검사."""

from __future__ import annotations

import re
from pathlib import Path
from typing import cast

from sqlalchemy import Table

from ai.db.models import AgentRun

_MIGRATION = (
    Path(__file__).resolve().parents[3]
    / "src"
    / "ai"
    / "db"
    / "migrations"
    / "versions"
    / "0002_agent_run_job_ledger.py"
)

_WORKER_JOB_COLUMNS = {
    "operation",
    "payload_ref",
    "payload_hash",
    "priority_class",
    "dispatch_attempt",
    "lease_generation",
    "recovery_count",
    "max_recovery_attempts",
    "lease_owner",
    "lease_acquired_at",
    "lease_expires_at",
    "checkpoint_ref",
    "result_ref",
    "error_code",
    "queued_at",
    "started_at",
    "finished_at",
}


def _source() -> str:
    return _MIGRATION.read_text(encoding="utf-8")


def test_agent_run_model_has_worker_job_ledger_columns() -> None:
    columns = set(AgentRun.__table__.columns.keys())
    assert _WORKER_JOB_COLUMNS <= columns


def test_migration_extends_initial_revision() -> None:
    source = _source()
    assert 'revision: str = "0002_agent_run_job_ledger"' in source
    assert 'down_revision: str | None = "0001_initial_schema"' in source


def test_upgrade_and_downgrade_cover_every_new_column() -> None:
    source = _source()
    upgrade, downgrade = source.split("def downgrade()", maxsplit=1)
    added = set(
        re.findall(
            r'op\.add_column\(\s*"agent_run",\s*sa\.Column\("(\w+)"',
            upgrade,
        )
    )
    assert added == _WORKER_JOB_COLUMNS
    assert 'op.drop_column("agent_run", column_name)' in downgrade
    for column_name in _WORKER_JOB_COLUMNS:
        assert f'"{column_name}",' in downgrade


def test_legacy_backfill_is_deterministic_and_fail_closed() -> None:
    source = _source()
    assert "sha256(convert_to(state_checkpoint::text, 'UTF8'))" in source
    assert "legacy_agent_run_requires_restart" in source
    assert "checkpoint_ref = NULL" in source
    assert "agent-run://legacy/" in source
    assert "RAISE EXCEPTION" in source


def test_queue_and_recovery_indexes_match_model() -> None:
    table = cast(Table, AgentRun.__table__)
    model_indexes = {index.name for index in table.indexes}
    assert model_indexes == {
        "ix_agent_run_dispatch_queue",
        "ix_agent_run_lease_expiry",
    }
    source = _source()
    for index_name in model_indexes:
        assert f'op.create_index(\n        "{index_name}"' in source
        assert f'op.drop_index("{index_name}"' in source


def test_state_checkpoint_is_only_observation_cache() -> None:
    comment = AgentRun.__table__.columns["state_checkpoint"].comment
    assert comment is not None
    assert "관측 캐시" in comment
    assert "PostgresSaver" in comment
    assert "정본" in comment
