"""문의 분류 3축 + 강사 정정 보관 — INQUIRY_CLASS를 평가셋 테이블로.

Revision ID: 0005_inquiry_class_axes
Revises: 0004_expectation_layer
Create Date: 2026-08-05

근거: 99 ⓑ(P2-b) · 99 ㊾(분류 3축 분리 — `complaint`가 `topic`에서 `sentiment`로
이동) · `part_a/03` §C7(오분류 수정 이력의 평가셋 순환) · `08` §7.
`db/models.py`는 양자 승인 13파일이며 **B 승인 완료**(조건 4건 + A 추가 1건 + 후속 5건).

**semantics — 3축은 AI 예측 고정이다.**
`topic`·`sentiment`·`urgency`는 분류(ⓑ)의 판정이고 **절대 덮어쓰지 않는다**. 강사 정정은
`corrected_*`에 따로 쌓는다 — 예측을 정정값으로 덮으면 (입력·예측·정답) 3요소가 깨져
평가셋 목적 자체가 사라진다(조건 1). 그래서 컬럼 1줄이 아니라 이 형태다.

**`corrected_by_teacher`는 컬럼이 아니라 파생값이 된다**(조건 2)::

    corrected_by_teacher := (corrected_topic IS NOT NULL
                          OR corrected_sentiment IS NOT NULL
                          OR corrected_urgency IS NOT NULL)

불리언 하나로는 **어느 축이 뒤집혔는지** 못 남긴다(3축은 독립이다). 같은 이유로
스칼라 `confidence`도 축별 3개로 쪼갠다 — `contracts/classify.AxisConfidence`와 대칭이다.

**`reviewed_at`이 필요한 이유.** `corrected_* IS NULL`은 뜻이 둘이다 — "AI가 맞아서
안 고쳤다"와 "아직 안 봤다". 구분하지 못하면 미검토 건이 전부 정답으로 세어져
**정확도가 과대평가된다**. `reviewed_at IS NULL`이면 평가셋 분모에서 뺀다. 시각으로
두는 것은 검토 지연(분류 → 검토) 자체가 파일럿 관측치이기 때문이다.

🔴 **기록 규약 2건 — 적재 코드(P2-c)가 반드시 지킨다**(조건 4).
① 예측과 **같은 값**으로 "정정"되면 `corrected_*`는 **NULL을 유지**한다. 강사가 드롭다운을
   열어 같은 값을 다시 골라도 쓰지 않는다 — 쓰면 오답으로 집계돼 재분류율이 부풀려지고
   `reviewed_at`으로 분리한 의미가 무너진다.
② 재검토 시 `reviewed_at`은 **마지막 검토 시각으로 갱신**하고 이력은 남기지 않는다.
   검토 이력이 필요해지면 별도 테이블 안건으로 연다.
CHECK `ck_inquiry_class_corrected_requires_review`가 ①의 절반(정정이 있으면 검토 시각도
있어야 한다)을 DB 층에서 강제한다.

🔴 **NOT NULL 4개를 `server_default` 없이 추가한다.** 적재 코드가 0건이라 실행 시점
행은 0건이 전제이며, **행이 있으면 이 마이그레이션은 그 자리에서 실패한다 — 그게
의도다.** 센티넬 기본값으로 조용히 채우면 backfill 행과 진짜 값을 구분할 수 없고
방어가 주석 하나로 남는다(조건 5). 실패가 곧 검출이다.

⚠ `(tenant_id, inquiry_ref)` 유니크·인덱스는 **넣지 않는다** — 재분류 멱등성(같은 문의를
다시 분류하면 갱신인가 새 행인가)과 함께 P2-c에서 정한다(승인 명시).
⚠ 폴백 건(`classified=False`)은 **적재하지 않는다** — 판정이 없는 건에 enum 값을 채우면
불변식 2 위반이고 평가셋이 오염된다. 폴백률 관측은 구조화 로그로 둔다(99 등재).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_inquiry_class_axes"
down_revision: str | None = "0004_expectation_layer"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "inquiry_class"
#: ⚠ 명명 규칙(`db/base.py`)이 `ck_%(table_name)s_` 접두를 붙이므로 **접두 없이** 적는다
#:   — `AgentRun`의 `worker_kind`·`phase` 선례와 같다. 최종 이름은
#:   `ck_inquiry_class_corrected_requires_review`다.
_CHECK = "corrected_requires_review"

#: 정정 3축 — 전부 nullable(그 축을 안 바꿨다는 뜻).
_CORRECTED = ("corrected_topic", "corrected_sentiment", "corrected_urgency")

#: 축별 확신도 — 스칼라 `confidence`를 대체한다.
_CONFIDENCE = ("confidence_topic", "confidence_sentiment", "confidence_urgency")


def upgrade() -> None:
    # ① AI 예측 축 — NOT NULL · server_default 없음(위 docstring 참조).
    op.add_column(_TABLE, sa.Column("sentiment", sa.String(), nullable=False))
    for name in _CONFIDENCE:
        op.add_column(_TABLE, sa.Column(name, sa.Numeric(), nullable=False))

    # ② 강사 정정 축 — NULL이 "그 축은 안 바꿈"이다.
    for name in _CORRECTED:
        op.add_column(_TABLE, sa.Column(name, sa.String(), nullable=True))
    op.add_column(
        _TABLE,
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
    )

    # ③ 정정이 있으면 검토 시각도 있어야 한다 — 기록 규약 ①의 DB 층 강제.
    op.create_check_constraint(
        _CHECK,
        _TABLE,
        "(corrected_topic IS NULL AND corrected_sentiment IS NULL "
        "AND corrected_urgency IS NULL) OR reviewed_at IS NOT NULL",
    )

    # ④ 대체된 종전 컬럼 제거 — 스칼라 confidence·불리언 corrected_by_teacher.
    #   ⚠ 0001의 원본 정의는 건드리지 않는다(과거 리비전 불변) — 제거는 여기서만 한다.
    op.drop_column(_TABLE, "confidence")
    op.drop_column(_TABLE, "corrected_by_teacher")


def downgrade() -> None:
    # 복원도 server_default 없이 한다 — 같은 이유(행 0건 전제).
    op.add_column(
        _TABLE, sa.Column("corrected_by_teacher", sa.Boolean(), nullable=False)
    )
    op.add_column(_TABLE, sa.Column("confidence", sa.Numeric(), nullable=False))

    op.drop_constraint(_CHECK, _TABLE, type_="check")

    op.drop_column(_TABLE, "reviewed_at")
    for name in reversed(_CORRECTED):
        op.drop_column(_TABLE, name)
    for name in reversed(_CONFIDENCE):
        op.drop_column(_TABLE, name)
    op.drop_column(_TABLE, "sentiment")
