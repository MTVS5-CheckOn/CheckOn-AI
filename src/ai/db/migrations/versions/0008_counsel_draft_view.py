"""COUNSEL_DRAFT_VIEW 읽기 모델 자리 신설.

Revision ID: 0008_counsel_draft_view
Revises: 0007_item_snapshot_pack_result
Create Date: 2026-08-10

`view_snapshot`과 `draft_snapshot`은 독립적으로 채워지고 축출되므로 둘 다 nullable이다.
이 리비전은 자리만 만들며 `_view_cache`·`_drafts` 배선은 A의 후속 축이다.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0008_counsel_draft_view"
down_revision: str | None = "0007_item_snapshot_pack_result"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UNIQUE = "uq_counsel_draft_view_job"


def upgrade() -> None:
    # 테이블명은 정적 migration parity 검사가 볼 수 있게 문자열 리터럴로 둔다.
    op.create_table(
        "counsel_draft_view",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("job_id", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("execution_id", sa.Uuid(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "view_snapshot",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "draft_snapshot",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_counsel_draft_view")),
        sa.UniqueConstraint("tenant_id", "job_id", name=_UNIQUE),
    )


def downgrade() -> None:
    op.drop_table("counsel_draft_view")
