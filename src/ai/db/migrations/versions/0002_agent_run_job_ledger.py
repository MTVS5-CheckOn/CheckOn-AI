"""AGENT_RUN을 슈퍼바이저 WorkerJob 실행 원장으로 확장한다.

Revision ID: 0002_agent_run_job_ledger
Revises: 0001_initial_schema
Create Date: 2026-07-27
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0002_agent_run_job_ledger"
down_revision: str | None = "0001_initial_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_STATE_CACHE_COMMENT = (
    "마스킹된 관측 캐시. LangGraph state 정본은 별도 PostgresSaver 테이블이며 "
    "이 컬럼으로 재개하지 않는다."
)


def upgrade() -> None:
    op.add_column("agent_run", sa.Column("operation", sa.String(), nullable=True))
    op.add_column("agent_run", sa.Column("payload_ref", sa.String(), nullable=True))
    op.add_column("agent_run", sa.Column("payload_hash", sa.String(), nullable=True))
    op.add_column("agent_run", sa.Column("priority_class", sa.String(), nullable=True))
    op.add_column("agent_run", sa.Column("dispatch_attempt", sa.Integer(), nullable=True))
    op.add_column("agent_run", sa.Column("lease_generation", sa.Integer(), nullable=True))
    op.add_column("agent_run", sa.Column("recovery_count", sa.Integer(), nullable=True))
    op.add_column(
        "agent_run",
        sa.Column("max_recovery_attempts", sa.Integer(), nullable=True),
    )
    op.add_column("agent_run", sa.Column("lease_owner", sa.String(), nullable=True))
    op.add_column(
        "agent_run",
        sa.Column("lease_acquired_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "agent_run",
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column("agent_run", sa.Column("checkpoint_ref", sa.String(), nullable=True))
    op.add_column("agent_run", sa.Column("result_ref", sa.String(), nullable=True))
    op.add_column("agent_run", sa.Column("error_code", sa.String(), nullable=True))
    op.add_column(
        "agent_run",
        sa.Column("queued_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "agent_run",
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "agent_run",
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.execute(
        sa.text(
            """
            DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1
                    FROM agent_run
                    WHERE agent_kind NOT IN (
                        'counsel_pack',
                        'mapping_probe',
                        'problem_generation'
                    )
                ) THEN
                    RAISE EXCEPTION
                        'agent_run에 라우팅할 수 없는 legacy agent_kind가 있습니다';
                END IF;
                IF EXISTS (
                    SELECT 1
                    FROM agent_run
                    WHERE status NOT IN (
                        'queued',
                        'leased',
                        'running',
                        'paused',
                        'done',
                        'succeeded',
                        'failed',
                        'cancelled'
                    )
                ) THEN
                    RAISE EXCEPTION
                        'agent_run에 변환할 수 없는 legacy status가 있습니다';
                END IF;
            END
            $$;
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE agent_run
            SET
                operation = CASE agent_kind
                    WHEN 'counsel_pack' THEN 'counsel_pack.generate'
                    WHEN 'mapping_probe' THEN 'mapping_probe.resolve'
                    WHEN 'problem_generation' THEN 'problem_set.generate'
                END,
                payload_ref = 'agent-run://legacy/' || id::text || '/payload',
                payload_hash = 'sha256:' || encode(
                    sha256(convert_to(state_checkpoint::text, 'UTF8')),
                    'hex'
                ),
                priority_class = CASE agent_kind
                    WHEN 'counsel_pack' THEN 'batch'
                    ELSE 'standard'
                END,
                dispatch_attempt = CASE WHEN status = 'queued' THEN 0 ELSE 1 END,
                lease_generation = CASE WHEN status = 'queued' THEN 0 ELSE 1 END,
                recovery_count = 0,
                max_recovery_attempts = 3,
                checkpoint_ref = NULL,
                -- 완료된 legacy 행은 AgentRun ID로 연결된 DRAFT/MAPPING_SPEC를 찾는
                -- 감사 locator다. 재실행 payload·checkpoint resolver로 사용하지 않는다.
                result_ref = CASE
                    WHEN status IN ('done', 'succeeded')
                    THEN 'agent-run://legacy/' || id::text || '/result'
                    ELSE NULL
                END,
                error_code = CASE
                    WHEN status IN ('queued', 'leased', 'running', 'paused')
                    THEN 'legacy_agent_run_requires_restart'
                    WHEN status = 'failed' THEN 'legacy_agent_run_failed'
                    WHEN status = 'cancelled' THEN 'legacy_agent_run_cancelled'
                    ELSE NULL
                END,
                queued_at = updated_at,
                started_at = CASE
                    WHEN status IN ('running', 'paused', 'done', 'succeeded', 'failed')
                    THEN updated_at
                    ELSE NULL
                END,
                finished_at = updated_at,
                status = CASE
                    WHEN status IN ('done', 'succeeded') THEN 'succeeded'
                    WHEN status = 'cancelled' THEN 'cancelled'
                    ELSE 'failed'
                END
            """
        )
    )

    for column_name in (
        "operation",
        "payload_ref",
        "payload_hash",
        "priority_class",
        "dispatch_attempt",
        "lease_generation",
        "recovery_count",
        "max_recovery_attempts",
        "queued_at",
    ):
        op.alter_column("agent_run", column_name, nullable=False)
    op.alter_column("agent_run", "state_checkpoint", comment=_STATE_CACHE_COMMENT)

    op.create_check_constraint(
        "worker_kind",
        "agent_run",
        "agent_kind IN ('counsel_pack', 'mapping_probe', 'problem_generation')",
    )
    op.create_check_constraint(
        "phase",
        "agent_run",
        "status IN ('queued', 'leased', 'running', 'paused', 'succeeded', 'failed', 'cancelled')",
    )
    op.create_check_constraint(
        "priority",
        "agent_run",
        "priority_class IN ('batch', 'standard', 'interactive')",
    )
    op.create_check_constraint(
        "operation_route",
        "agent_run",
        "(agent_kind = 'counsel_pack' AND operation = 'counsel_pack.generate') OR "
        "(agent_kind = 'mapping_probe' AND operation = 'mapping_probe.resolve') OR "
        "(agent_kind = 'problem_generation' AND operation IN "
        "('problem_set.generate', 'problem_item.refine', 'problem_item.reverify'))",
    )
    op.create_check_constraint(
        "payload_hash",
        "agent_run",
        "payload_hash ~ '^sha256:[0-9a-f]{64}$'",
    )
    op.create_check_constraint(
        "attempts",
        "agent_run",
        "dispatch_attempt >= 0 "
        "AND dispatch_attempt = lease_generation "
        "AND recovery_count >= 0 "
        "AND max_recovery_attempts >= 1 "
        "AND recovery_count <= max_recovery_attempts",
    )
    op.create_check_constraint(
        "lease",
        "agent_run",
        "(status IN ('leased', 'running') "
        "AND lease_owner IS NOT NULL "
        "AND lease_acquired_at IS NOT NULL "
        "AND lease_expires_at IS NOT NULL "
        "AND lease_expires_at > lease_acquired_at) "
        "OR (status NOT IN ('leased', 'running') "
        "AND lease_owner IS NULL "
        "AND lease_acquired_at IS NULL "
        "AND lease_expires_at IS NULL)",
    )
    op.create_check_constraint(
        "finished",
        "agent_run",
        "(status IN ('succeeded', 'failed', 'cancelled') AND finished_at IS NOT NULL) "
        "OR (status NOT IN ('succeeded', 'failed', 'cancelled') AND finished_at IS NULL)",
    )
    op.create_check_constraint(
        "success_result",
        "agent_run",
        "(status = 'succeeded' AND result_ref IS NOT NULL) OR status <> 'succeeded'",
    )
    op.create_check_constraint(
        "result_phase",
        "agent_run",
        "status IN ('succeeded', 'failed', 'cancelled') OR result_ref IS NULL",
    )
    op.create_check_constraint(
        "failure_error",
        "agent_run",
        "(status = 'failed' AND error_code IS NOT NULL) OR status <> 'failed'",
    )
    op.create_check_constraint(
        "error_phase",
        "agent_run",
        "status IN ('failed', 'cancelled') OR error_code IS NULL",
    )
    op.create_check_constraint(
        "resume_fields",
        "agent_run",
        "(status IN ('running', 'paused') "
        "AND started_at IS NOT NULL "
        "AND checkpoint_ref IS NOT NULL) "
        "OR status NOT IN ('running', 'paused')",
    )
    op.create_check_constraint(
        "start_fields",
        "agent_run",
        "(status <> 'succeeded' OR started_at IS NOT NULL) "
        "AND (status <> 'queued' OR dispatch_attempt <> 0 "
        "OR (started_at IS NULL AND checkpoint_ref IS NULL))",
    )
    op.create_check_constraint(
        "time_order",
        "agent_run",
        "(started_at IS NULL OR started_at >= queued_at) "
        "AND (finished_at IS NULL OR finished_at >= queued_at) "
        "AND (started_at IS NULL OR finished_at IS NULL OR finished_at >= started_at) "
        "AND (lease_acquired_at IS NULL OR lease_acquired_at >= queued_at)",
    )
    op.create_index(
        "ix_agent_run_dispatch_queue",
        "agent_run",
        [
            "tenant_id",
            "agent_kind",
            "status",
            "priority_class",
            "queued_at",
            "id",
        ],
    )
    op.create_index(
        "ix_agent_run_lease_expiry",
        "agent_run",
        ["tenant_id", "agent_kind", "status", "lease_expires_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_agent_run_lease_expiry", table_name="agent_run")
    op.drop_index("ix_agent_run_dispatch_queue", table_name="agent_run")
    for constraint_name in (
        "time_order",
        "start_fields",
        "resume_fields",
        "error_phase",
        "failure_error",
        "result_phase",
        "success_result",
        "finished",
        "lease",
        "attempts",
        "payload_hash",
        "operation_route",
        "priority",
        "phase",
        "worker_kind",
    ):
        op.drop_constraint(constraint_name, "agent_run", type_="check")
    op.alter_column("agent_run", "state_checkpoint", comment=None)
    for column_name in (
        "finished_at",
        "started_at",
        "queued_at",
        "error_code",
        "result_ref",
        "checkpoint_ref",
        "lease_expires_at",
        "lease_acquired_at",
        "lease_owner",
        "lease_generation",
        "max_recovery_attempts",
        "recovery_count",
        "dispatch_attempt",
        "priority_class",
        "payload_hash",
        "payload_ref",
        "operation",
    ):
        op.drop_column("agent_run", column_name)
