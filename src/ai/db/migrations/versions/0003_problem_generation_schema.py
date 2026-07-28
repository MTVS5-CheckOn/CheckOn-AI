"""문제생성 저장 계층 8테이블 추가

Revision ID: 0003_problem_generation_schema
Revises: 0002_agent_run_job_ledger
Create Date: 2026-07-28 17:41:12.398985
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0003_problem_generation_schema"
down_revision: str | None = "0002_agent_run_job_ledger"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "difficulty_calib",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("params", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("approved_by_ref", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_difficulty_calib")),
    )
    op.create_table(
        "weakness_map",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("student_ref", sa.String(), nullable=False),
        sa.Column("week_start", sa.Date(), nullable=False),
        sa.Column("graph_version", sa.String(), nullable=False),
        sa.Column("taxonomy_version", sa.String(), nullable=False),
        sa.Column("config_version", sa.String(), nullable=False),
        sa.Column("snapshot_hash", sa.String(), nullable=False),
        sa.Column("cells", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("nodes", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("propagated", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("overall_low", sa.Boolean(), nullable=False),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["run_id"], ["ai_run.execution_id"], name=op.f("fk_weakness_map_run_id_ai_run")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_weakness_map")),
        sa.UniqueConstraint(
            "tenant_id", "student_ref", "graph_version", "week_start", name="uq_weakness_map_scope"
        ),
    )
    op.create_table(
        "passage",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("source_kind", sa.String(), nullable=False),
        sa.Column("source_ref", sa.String(), nullable=True),
        sa.Column("license_ref", sa.String(), nullable=True),
        sa.Column("area_tag", sa.String(), nullable=False),
        sa.Column("topic", sa.String(), nullable=False),
        sa.Column("word_count", sa.Integer(), nullable=False),
        sa.Column("complexity", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("llm_call_id", sa.Uuid(), nullable=True),
        sa.ForeignKeyConstraint(
            ["llm_call_id"], ["llm_call.id"], name=op.f("fk_passage_llm_call_id_llm_call")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_passage")),
    )
    op.create_table(
        "problem_set",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("target_kind", sa.String(), nullable=False),
        sa.Column("target_ref", sa.String(), nullable=False),
        sa.Column("target_source", sa.String(), nullable=False),
        sa.Column("weakness_map_id", sa.Uuid(), nullable=True),
        sa.Column("request", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("summary", sa.String(), nullable=True),
        sa.Column("stop_reason", sa.String(), nullable=True),
        sa.Column("diagnostic_purpose", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["run_id"], ["ai_run.execution_id"], name=op.f("fk_problem_set_run_id_ai_run")
        ),
        sa.ForeignKeyConstraint(
            ["weakness_map_id"],
            ["weakness_map.id"],
            name=op.f("fk_problem_set_weakness_map_id_weakness_map"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_problem_set")),
    )
    op.create_table(
        "item_candidate",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("set_id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("slot_index", sa.Integer(), nullable=False),
        sa.Column("attempt_no", sa.Integer(), nullable=False),
        sa.Column("snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("gate_summary", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("difficulty_est", sa.Numeric(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["set_id"], ["problem_set.id"], name=op.f("fk_item_candidate_set_id_problem_set")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_item_candidate")),
        sa.UniqueConstraint(
            "tenant_id", "set_id", "slot_index", "attempt_no", name="uq_item_candidate_scope"
        ),
    )
    op.create_table(
        "problem_item",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("set_id", sa.Uuid(), nullable=False),
        sa.Column("passage_id", sa.Uuid(), nullable=True),
        sa.Column("area_tag", sa.String(), nullable=False),
        sa.Column("type_tag", sa.String(), nullable=False),
        sa.Column("item_format", sa.String(), nullable=False),
        sa.Column("skill_node_id", sa.String(), nullable=True),
        sa.Column("stem", sa.Text(), nullable=False),
        sa.Column("choices", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("answer", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("difficulty_est", sa.Numeric(), nullable=False),
        sa.Column("difficulty_fit", sa.Numeric(), nullable=True),
        sa.Column("difficulty_calib_ver", sa.String(), nullable=False),
        sa.Column("review_badge", sa.Boolean(), nullable=False),
        sa.Column("current_revision_no", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("drop_reason", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(
            ["passage_id"], ["passage.id"], name=op.f("fk_problem_item_passage_id_passage")
        ),
        sa.ForeignKeyConstraint(
            ["set_id"], ["problem_set.id"], name=op.f("fk_problem_item_set_id_problem_set")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_problem_item")),
    )
    op.create_table(
        "item_revision",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("item_id", sa.Uuid(), nullable=False),
        sa.Column("turn_no", sa.Integer(), nullable=False),
        sa.Column("revision_kind", sa.String(), nullable=False),
        sa.Column("instruction", sa.Text(), nullable=True),
        sa.Column("result_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("diff", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("verifications_passed", sa.Boolean(), nullable=False),
        sa.Column("blocked_reason", sa.String(), nullable=True),
        sa.Column("llm_call_id", sa.Uuid(), nullable=True),
        sa.ForeignKeyConstraint(
            ["item_id"], ["problem_item.id"], name=op.f("fk_item_revision_item_id_problem_item")
        ),
        sa.ForeignKeyConstraint(
            ["llm_call_id"], ["llm_call.id"], name=op.f("fk_item_revision_llm_call_id_llm_call")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_item_revision")),
    )
    op.create_table(
        "verification_result",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("item_id", sa.Uuid(), nullable=False),
        sa.Column("stage", sa.String(), nullable=False),
        sa.Column("passed", sa.Boolean(), nullable=False),
        sa.Column("detail", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("llm_call_id", sa.Uuid(), nullable=True),
        sa.Column("attempt_no", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["item_id"],
            ["problem_item.id"],
            name=op.f("fk_verification_result_item_id_problem_item"),
        ),
        sa.ForeignKeyConstraint(
            ["llm_call_id"],
            ["llm_call.id"],
            name=op.f("fk_verification_result_llm_call_id_llm_call"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_verification_result")),
    )
def downgrade() -> None:
    op.drop_table("verification_result")
    op.drop_table("item_revision")
    op.drop_table("problem_item")
    op.drop_table("item_candidate")
    op.drop_table("problem_set")
    op.drop_table("passage")
    op.drop_table("weakness_map")
    op.drop_table("difficulty_calib")
