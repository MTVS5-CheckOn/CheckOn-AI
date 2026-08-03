"""기대치 입력 층 — 지문×유형 실측 누적 + 이중 집계 방지 원장.

Revision ID: 0004_expectation_layer
Revises: 0003_problem_generation_schema
Create Date: 2026-08-03

근거: `part_a/04_threshold_config.md` §1 "기대치 입력 층" · `part_a/13` §4-3-4 ·
99 결정 로그 33(승우 합의로 `passage_ref` 제공 확정).

⚠ 두 테이블 모두 **학생 식별자를 저장하지 않는다** — 조합·스냅숏 단위 사실만 남긴다
(개인정보 최소 수집). 전 테이블 `tenant_id` 필수는 저장소 공통 규율이다.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_expectation_layer"
down_revision: str | None = "0003_problem_generation_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "passage_type_stat",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("passage_ref", sa.String(), nullable=False),
        sa.Column("type_tag", sa.String(), nullable=False),
        sa.Column("responses", sa.Integer(), nullable=False),
        sa.Column("corrects", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_passage_type_stat")),
        sa.UniqueConstraint(
            "tenant_id",
            "passage_ref",
            "type_tag",
            name="uq_passage_type_stat_scope",
        ),
    )
    op.create_table(
        "expectation_ingest",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("snapshot_hash", sa.String(), nullable=False),
        sa.Column("events_applied", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_expectation_ingest")),
        sa.UniqueConstraint(
            "tenant_id", "snapshot_hash", name="uq_expectation_ingest_scope"
        ),
    )


def downgrade() -> None:
    op.drop_table("expectation_ingest")
    op.drop_table("passage_type_stat")
