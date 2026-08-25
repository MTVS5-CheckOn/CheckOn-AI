"""라벨 확정의 주간 집계 — 개인 참조 0.

Revision ID: 0013_label_confirmation_stat
Revises: 0012_pg_request_result
Create Date: 2026-08-25

🔴 승인: 염준영 · 2026-08-25 · 회신. 닫는 것 — 99 #239(누적 자리 = 테이블).
⚠ 🔴 `guardian_ref` 도 FK 도 없다 — 이 행은 「조합의 속성」이지 학부모 데이터가 아니다.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0013_label_confirmation_stat"
down_revision: str | None = "0012_pg_request_result"
branch_labels: None = None
depends_on: None = None


def upgrade() -> None:
    op.create_table(
        "label_confirmation_stat",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("week_start", sa.Date(), nullable=False),
        sa.Column("axis", sa.String(), nullable=False),
        sa.Column("suggested_value", sa.String(), nullable=False),
        sa.Column("confirmed_value", sa.String(), nullable=False),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_label_confirmation_stat"),
        #: 🔴 **이 유니크가 곧 UPSERT 키다.** 별도 인덱스를 안 만든다 —
        #: 선두 두 칸(`tenant_id`·`week_start`)이 그대로 축출·조회 축이다.
        sa.UniqueConstraint(
            "tenant_id",
            "week_start",
            "axis",
            "suggested_value",
            "confirmed_value",
            "action",
            name="uq_label_confirmation_stat_bucket",
        ),
    )


def downgrade() -> None:
    op.drop_table("label_confirmation_stat")
