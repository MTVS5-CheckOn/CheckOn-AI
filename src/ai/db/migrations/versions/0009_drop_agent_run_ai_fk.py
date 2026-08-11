"""AGENT_RUN.run_id의 AI_RUN 물리 FK 제거.

Revision ID: 0009_drop_agent_run_ai_fk
Revises: 0008_counsel_draft_view
Create Date: 2026-08-10

AGENT_RUN은 모델 실행 전에도 생성되므로 run_id는 실행 신원으로 유지하되, AI_RUN을
선행 부모로 요구하지 않는다. 나머지 AI_RUN FK는 실행 뒤 생기는 산출물 축이라 유지한다.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0009_drop_agent_run_ai_fk"
down_revision: str | None = "0008_counsel_draft_view"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CONSTRAINT = "fk_agent_run_run_id_ai_run"


def upgrade() -> None:
    op.drop_constraint(_CONSTRAINT, "agent_run", type_="foreignkey")


def downgrade() -> None:
    op.create_foreign_key(
        _CONSTRAINT,
        "agent_run",
        "ai_run",
        ["run_id"],
        ["execution_id"],
    )
