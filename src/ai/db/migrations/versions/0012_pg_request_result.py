"""문제 생성 요청·결과의 프로세스 간 영속 경계.

Revision ID: 0012_pg_request_result
Revises: 0011_signal_comparison
Create Date: 2026-08-21
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0012_pg_request_result"
down_revision: str | None = "0011_signal_comparison"
branch_labels: None = None
depends_on: None = None


def upgrade() -> None:
    op.create_table(
        "problem_generation_request",
        sa.Column("ref", sa.String(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("ref", name="pk_problem_generation_request"),
    )
    op.create_index(
        "ix_problem_generation_request_tenant_created",
        "problem_generation_request",
        ["tenant_id", "created_at"],
    )
    op.create_table(
        "problem_generation_result",
        sa.Column("ref", sa.String(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("ref", name="pk_problem_generation_result"),
        sa.UniqueConstraint("job_id", name="uq_problem_generation_result_job_id"),
    )
    op.create_index(
        "ix_problem_generation_result_tenant_created",
        "problem_generation_result",
        ["tenant_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_problem_generation_result_tenant_created",
        table_name="problem_generation_result",
    )
    op.drop_table("problem_generation_result")
    op.drop_index(
        "ix_problem_generation_request_tenant_created",
        table_name="problem_generation_request",
    )
    op.drop_table("problem_generation_request")
