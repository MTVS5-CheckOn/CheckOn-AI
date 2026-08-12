"""상담 입력 묶음 테이블 + DRAFT 본문 컬럼 (㉻ · 지시서 73 §2-3).

Revision ID: 0010_counsel_ctx_draft_body
Revises: 0009_drop_agent_run_ai_fk
Create Date: 2026-08-12

㉻의 결손 둘을 함께 닫는다:

  ⓐ `ContextBundleRecord`가 앉을 테이블이 없어 다른 프로세스 워커가
     `context_bundle_missing`으로 죽었다 → `counsel_context_bundle` 신설.
  ⓑ `draft`에 본문 컬럼이 없어 늦게 성공한 잡의 GET이 `result=None`이었다
     → `draft.content` 추가.

🔴 **`content`를 `NOT NULL`로 세운다.** 근거는 **둘이고 각각 범위가 있다**:

  ⓐ **0010 이전 프로덕션 저장소 호출 경로 0건** — `Draft` ORM을 참조하는 코드가 정의
     한 곳뿐이다(2026-08-12 전수). ⇒ **애플리케이션이 이 테이블에 쓴 적이 없다.**
  ⓑ **검사한 실 PG의 `draft` 0행.**

⇒ **현재 배포 대상에서 무손실 추가가 가능하다**는 판정이다.
⚠ **「어떤 환경에도 행이 있을 수 없다」가 아니다** — ⓐ는 애플리케이션 경로를 증명할 뿐
**수동 SQL·과거 실험 DB까지 논리적으로 배제하지 못한다.**

🔴 **그래서 `server_default`를 두지 않는다.** 빈 문자열 backfill은 *"초안이 있다"* 는 거짓
데이터이고, 그러면 게이트 거부와 정상 초안이 **같은 값**을 갖는다. 행이 있는 DB에서는
이 마이그레이션이 **실패해 배포를 멈춘다** — 그게 거짓 본문보다 안전하다.
⚠ **적용 전 확인:** `SELECT count(*) FROM draft;` 가 0인지 본다. **자동 삭제·backfill은
하지 않는다.** 0007이 같은 「행 0건 전제」로 NOT NULL을 세운 선례다.

⚠ **리비전 id가 파일명보다 짧다** — `alembic_version.version_num`이 `varchar(32)`인데
`0010_counsel_context_draft_content`는 34자다. 그 경우 **DDL은 전부 돌고 버전 기록
UPDATE에서만** 죽어 DB가 「스키마는 새것, 기록은 옛것」으로 남는다(99 #26 실측).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

#: ⚠ 32자 이하 — 파일명(34자)을 그대로 쓰면 버전 기록 UPDATE에서 죽는다.
revision: str = "0010_counsel_ctx_draft_body"
down_revision: str | None = "0009_drop_agent_run_ai_fk"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: ⚠ **리터럴로 적는다** — `test_migration_parity`가 소스 텍스트를 정규식으로 훑어
#:   `op.create_table("…")`을 센다. 상수로 빼면 그 대조가 **이 테이블만 조용히 비껴간다.**
_INDEX = "ix_counsel_context_bundle_tenant_created"


def upgrade() -> None:
    op.create_table(
        "counsel_context_bundle",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("class_ref", sa.String(), nullable=False),
        sa.Column("contexts", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("content_hash", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_counsel_context_bundle"),
    )
    op.create_index(_INDEX, "counsel_context_bundle", ["tenant_id", "created_at"])
    #: 🔴 행 0건 전제 — `server_default` 없이 NOT NULL이다(위 docstring).
    op.add_column("draft", sa.Column("content", sa.Text(), nullable=False))


def downgrade() -> None:
    #: ⚠ **자기가 만든 것만** 되돌린다 — AI_RUN FK 다섯과 #201 변경은 무접촉이다.
    op.drop_column("draft", "content")
    op.drop_index(_INDEX, table_name="counsel_context_bundle")
    op.drop_table("counsel_context_bundle")
