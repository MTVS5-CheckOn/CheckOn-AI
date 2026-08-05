"""INQUIRY_CLASS 자연키 — (tenant_id, inquiry_ref) 유니크.

Revision ID: 0006_inquiry_class_natural_key
Revises: 0005_inquiry_class_axes
Create Date: 2026-08-05

근거: 99 ⓑ(P2-c) · 0005에서 "유니크·인덱스는 **재분류 멱등성과 함께 P2-c에서 정한다**"로
이월된 항목(B 승인 명시). `db/models.py`는 양자 승인 파일이며 이 제약 1개 추가는
**P2-b 승인 범위의 연장**이다(다른 모델·컬럼 무접촉 · PR에서 B 리뷰).

**이 유니크가 P2-c 설계 두 가지의 전제다.**

① **캐시** — `POST /v1/classify`는 `(tenant_id, inquiry_ref)`로 먼저 조회하고, 행이 있으면
   저장된 예측을 그대로 돌려준다(**LLM 0회**). `04`:132가 분류를 "카운트 제외 · 캐시"로
   규정한 것과 정합하며, 태깅 쪽 "동일 패턴은 캐시 히트"(`04`:418) 선례와 같은 형태다.
   부수 효과로 재시도가 멱등키 없이도 안전해지고, 서버가 `seed`를 존중하는지 미확인인
   상태(99 ㊼)에서도 **같은 문의에 항상 같은 답**이 나간다.

② **검토 보호** — `reviewed_at`이 선 행을 나중 예측이 덮으면 (예측·정답) 쌍이 어긋나
   **평가셋이 깨진다**. 자연키가 있어야 "그 행"을 지목해 갱신 대상에서 뺄 수 있다.

⚠ 문의 1건 = 분류 1건이다. 같은 문의를 다시 분류하면 **새 행이 아니라 캐시 히트**이며,
  이 제약이 그 규약을 DB 층에서 강제한다.

🔴 `server_default`를 쓰지 않는다 — 0005와 같은 규율이다(적재 시점 행 0건 전제).
   유니크 위반이 있으면 이 마이그레이션은 그 자리에서 실패한다. 그게 검출이다.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0006_inquiry_class_natural_key"
down_revision: str | None = "0005_inquiry_class_axes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "inquiry_class"
#: ⚠ `uq` 명명 규칙(`db/base.py`)은 `%(constraint_name)s`를 보간하지 **않는다**
#:   (`ck`와 다르다) — 전체 이름을 그대로 적는다. `uq_passage_type_stat_scope` 선례.
_UNIQUE = "uq_inquiry_class_scope"


def upgrade() -> None:
    op.create_unique_constraint(_UNIQUE, _TABLE, ["tenant_id", "inquiry_ref"])


def downgrade() -> None:
    op.drop_constraint(_UNIQUE, _TABLE, type_="unique")
