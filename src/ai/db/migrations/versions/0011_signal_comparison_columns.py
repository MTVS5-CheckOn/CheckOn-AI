"""SIGNAL에 비교값 4컬럼 (99 #59·#60 · 안 D).

Revision ID: 0011_signal_comparison
Revises: 0010_counsel_ctx_draft_body
Create Date: 2026-08-14

응답 `signals[]`에 `metric`·`observed`·`baseline`·`sample_size`가 나가는데 **원장에는
안 남아**, 강사가 이의를 제기해도 *"그때 평소값이 얼마였나"* 를 우리 원장으로 답할 수
없었다. 재현이 반쪽이었다(불변식 8).

🔴 **전부 nullable이다** — `0010`의 `content`(NOT NULL)와 다르다. 근거가 둘이다:

  ⓐ **규칙마다 채울 수 있는 것이 달라 값의 부재가 정상이다** — `return_care`는 넷 다
     비고(사건형), `submit_drop`·`type_bias`는 `baseline`이 빈다(임계 비교형).
  ⓑ **기존 행에 채울 참값이 없다** — 그때 무엇과 비교했는지는 지나간 실행의 사실이라
     지금 만들어 넣을 수 없다. `NOT NULL`로 세우면 기존 행이 있는 DB에서 업그레이드가
     실패하거나, 통과시키려고 **거짓 기본값**을 박게 된다.

⚠ `evidence_item`은 **안 건드린다** — 그 테이블은 쓰는 코드가 0건이다
(`db/models.py`의 `EvidenceItem` docstring · 2026-08-14 A·B 전수).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

#: 🔴 32자 이내 — `alembic_version.version_num`이 그 길이다
#:  (`test_revision_ids_fit_the_alembic_version_column`).
revision: str = "0011_signal_comparison"
down_revision: str | None = "0010_counsel_ctx_draft_body"
branch_labels: None = None
depends_on: None = None


def upgrade() -> None:
    op.add_column("signal", sa.Column("metric", sa.String(), nullable=True))
    op.add_column("signal", sa.Column("observed", sa.Numeric(), nullable=True))
    op.add_column("signal", sa.Column("baseline", sa.Numeric(), nullable=True))
    op.add_column("signal", sa.Column("sample_size", sa.Integer(), nullable=True))


def downgrade() -> None:
    #: ⚠ 자기가 만든 것만 되돌린다(0010 선례). 역순은 규약이 아니라 관례다.
    op.drop_column("signal", "sample_size")
    op.drop_column("signal", "baseline")
    op.drop_column("signal", "observed")
    op.drop_column("signal", "metric")
