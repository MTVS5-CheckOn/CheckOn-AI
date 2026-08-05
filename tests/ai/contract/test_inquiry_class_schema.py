"""`InquiryClass` 스키마 계약 — 평가셋 3요소 보존 (99 ⓑ · B 양자 승인).

🔴 **이 테이블의 목적은 평가셋이다** — (입력 · 예측 · 정답) 3요소가 있어야 성립한다.
3축을 정정값으로 덮어쓰면 **예측이 소실**돼 남는 건 정답뿐이고, 그건 평가셋이 아니다.
그래서 예측 축과 `corrected_*`가 **따로** 있어야 한다는 것이 스키마 계약이다.

DB는 in-memory SQLite로 만든다 — CHECK 제약은 SQLite도 강제하므로 PG 없이 검증된다.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal
from typing import cast

import pytest
from sqlalchemy import DateTime, Table, create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ai.db.base import Base
from ai.db.models import InquiryClass

#: `__table__`은 선언형에서 `FromClause`로 타이핑돼 있어 한 번 좁혀 쓴다.
_TABLE = cast(Table, InquiryClass.__table__)

_PREDICTION = ("topic", "sentiment", "urgency")
_CONFIDENCE = ("confidence_topic", "confidence_sentiment", "confidence_urgency")
_CORRECTED = ("corrected_topic", "corrected_sentiment", "corrected_urgency")


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine, tables=[_TABLE])
    with Session(engine) as db:
        yield db


def _row(**overrides: object) -> InquiryClass:
    values: dict[str, object] = {
        "id": uuid.uuid4(),
        "tenant_id": "t1",
        "inquiry_ref": "iq_1",
        "topic": "grade",
        "sentiment": "complaint",
        "urgency": "immediate",
        "confidence_topic": Decimal("0.9"),
        "confidence_sentiment": Decimal("0.8"),
        "confidence_urgency": Decimal("0.7"),
    }
    values.update(overrides)
    return InquiryClass(**values)


# ── 스키마 형태 ──────────────────────────────────────────────────


def test_prediction_axes_are_not_nullable() -> None:
    """AI 예측 3축 + 축별 확신도는 필수 — 판정 없는 건은 애초에 적재하지 않는다."""
    columns = _TABLE.columns
    for name in (*_PREDICTION, *_CONFIDENCE):
        assert not columns[name].nullable, name


def test_corrected_axes_are_nullable() -> None:
    """NULL = **그 축은 안 바꿈**. 축이 독립이라 축마다 따로 비어 있을 수 있다."""
    columns = _TABLE.columns
    for name in (*_CORRECTED, "reviewed_at"):
        assert columns[name].nullable, name


def test_corrected_by_teacher_is_not_a_column() -> None:
    """🔴 불리언 하나로는 **어느 축이 뒤집혔는지** 못 남긴다 — 파생값으로 내렸다(조건 2)."""
    assert "corrected_by_teacher" not in _TABLE.columns


def test_scalar_confidence_is_gone() -> None:
    """확신도는 축별 3개다 — `contracts/classify.AxisConfidence`와 대칭(조건 2)."""
    assert "confidence" not in _TABLE.columns


def test_reviewed_at_is_timezone_aware() -> None:
    """`_TZ`(=`DateTime(timezone=True)`) — `StyleProfile.updated_at`과 대칭(조건 3)."""
    column_type = _TABLE.columns["reviewed_at"].type
    assert isinstance(column_type, DateTime)
    assert column_type.timezone is True


# ── 🔴 CHECK 제약 — 기록 규약의 DB 층 강제 ──────────────────────


def test_correction_without_review_is_rejected(session: Session) -> None:
    """🔴 정정이 있는데 `reviewed_at`이 비면 거부된다.

    `corrected_* IS NULL`은 뜻이 둘이다 — "AI가 맞아서 안 고쳤다"와 "아직 안 봤다".
    검토 시각 없이 정정만 쌓이면 그 구분이 무너져 **정확도가 과대평가된다**.
    """
    session.add(_row(corrected_topic="schedule"))
    with pytest.raises(IntegrityError):
        session.flush()


def test_correction_with_review_is_accepted(session: Session) -> None:
    session.add(
        _row(corrected_topic="schedule", reviewed_at=datetime.now(UTC))
    )
    session.flush()  # 예외 없이 통과


def test_review_without_correction_is_accepted(session: Session) -> None:
    """검토했는데 안 고쳤다 = **AI 정답** — 평가셋 분자에 들어가는 정상 상태다."""
    session.add(_row(reviewed_at=datetime.now(UTC)))
    session.flush()


def test_neither_review_nor_correction_is_accepted(session: Session) -> None:
    """미검토 건 — 적재는 되고 **평가셋 분모에서 빠진다**."""
    session.add(_row())
    session.flush()


# ── 예측 보존 ────────────────────────────────────────────────────


def test_prediction_survives_correction(session: Session) -> None:
    """🔴 정정해도 **예측이 남는다** — 이게 평가셋 3요소의 핵심이다.

    3축을 덮어썼다면 여기서 `topic`이 `schedule`로 바뀌어 있을 것이고, 그 순간
    "AI가 뭐라고 했었나"를 영영 알 수 없게 된다.
    """
    session.add(
        _row(corrected_topic="schedule", reviewed_at=datetime.now(UTC))
    )
    session.flush()
    row = session.query(InquiryClass).one()

    assert row.topic == "grade"  # 예측 보존
    assert row.corrected_topic == "schedule"  # 정답 별도
    assert row.corrected_sentiment is None  # 안 바꾼 축은 NULL
