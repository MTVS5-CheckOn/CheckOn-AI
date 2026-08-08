"""PROBLEM_ITEM 스냅숏 정본화 + COUNSEL_PACK_RESULT 신설.

Revision ID: 0007_item_snapshot_pack_result
Revises: 0006_inquiry_class_natural_key
Create Date: 2026-08-08

⚠ **리비전 id는 32자를 넘기면 안 된다** — `alembic_version.version_num`이
`varchar(32)`라 DDL은 다 돌고 **버전 기록에서만** `StringDataRightTruncation`으로 죽는다.
종전 `0007_problem_item_snapshot_and_pack_result`(42자)가 실 PG 왕복에서 그렇게 걸렸다.
기존 여섯은 19~30자다.

근거: `docs/part_b/09_integration_proposals.md` §2-20(결손 12건) + A 판정
(`docs/handoff/2026-08-08_schema_verdict_to_B.md` 결정 ①②③) + ㉕ 본문 설계
(`docs/handoff/2026-08-08_pack_result_table_design.md` §3).

⚠ **이 파일은 `db/models.py` diff의 기계적 산출물이다** — `02_ownership.md` §4-1이
마이그레이션을 양자 목록에서 뺐고, 모델 diff가 든 이 PR의 승인으로 끝난다.

🔴 **`server_default` 없이 NOT NULL을 붙인다 — 행 0건이 전제다.**
`problem_item`은 정의·마이그레이션(0003)만 있고 **프로덕션 소비가 0건**이다
(`ProblemItem` ORM 참조는 `db/models.py` 선언 하나뿐 · 저장소 미구현이라 쓰는 경로가
없었다 — 그것이 §2-20을 낳은 원인이다). 0005가 같은 판단을 같은 이유로 적었다.
⚠ 전제가 깨지면 이 마이그레이션은 **조용히 틀리지 않고 시끄럽게 실패한다**(NOT NULL 위반).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0007_item_snapshot_pack_result"
down_revision: str | None = "0006_inquiry_class_natural_key"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ITEM = "problem_item"
_SLOT_UNIQUE = "uq_problem_item_slot"
_PACK = "counsel_pack_result"
_PACK_INDEX = "ix_counsel_pack_result_tenant_created"

#: 본문 투영 — `StoredProblemItem.item`이 `None`인 슬롯에서 전부 NULL이 된다(결정 ③ ⓒ).
#: `difficulty_calib_ver`는 본문이 아니라 **저장 시점에 대응 값이 없어서**다(§2-20 #5).
_NULLABLE_PROJECTIONS: tuple[tuple[str, Any], ...] = (
    ("area_tag", sa.String()),
    ("type_tag", sa.String()),
    ("item_format", sa.String()),
    ("stem", sa.Text()),
    ("choices", postgresql.JSONB(astext_type=sa.Text())),
    ("answer", postgresql.JSONB(astext_type=sa.Text())),
    ("rationale", sa.Text()),
    ("difficulty_est", sa.Numeric()),
    ("difficulty_calib_ver", sa.String()),
)


def upgrade() -> None:
    # ① 조회 키 — Protocol이 `get(set_id, slot_index)`를 요구하는데 컬럼이 없었다.
    op.add_column(_ITEM, sa.Column("slot_index", sa.Integer(), nullable=False))
    # ② 무손실 보존 — 스냅숏이 정본이고 나머지 컬럼은 파생 투영이 된다.
    op.add_column(
        _ITEM,
        sa.Column("snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    )
    op.create_unique_constraint(_SLOT_UNIQUE, _ITEM, ["set_id", "slot_index"])

    # ③ 본문 없는 슬롯 — 투영은 원본이 없을 때 NULL이다.
    for name, column_type in _NULLABLE_PROJECTIONS:
        op.alter_column(_ITEM, name, existing_type=column_type, nullable=True)

    # ㉕ — `AGENT_RUN.result_ref`(`pack://`)가 가리키던 대상. FK는 두지 않는다(다형 참조).
    # ⚠ 테이블명은 **문자열 리터럴**로 둔다 — `test_migration_parity`가 정적 파싱으로
    #   `op.create_table("…")`를 세므로 상수를 넘기면 검사가 조용히 이 테이블을 놓친다.
    op.create_table(
        "counsel_pack_result",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("class_ref", sa.String(), nullable=False),
        sa.Column("plan_outcome", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_counsel_pack_result")),
    )
    op.create_index(_PACK_INDEX, _PACK, ["tenant_id", "created_at"])


def downgrade() -> None:
    op.drop_index(_PACK_INDEX, table_name=_PACK)
    op.drop_table("counsel_pack_result")

    # 복원도 server_default 없이 한다 — 같은 이유(행 0건 전제).
    for name, column_type in _NULLABLE_PROJECTIONS:
        op.alter_column(_ITEM, name, existing_type=column_type, nullable=False)

    op.drop_constraint(_SLOT_UNIQUE, _ITEM, type_="unique")
    op.drop_column(_ITEM, "snapshot")
    op.drop_column(_ITEM, "slot_index")
