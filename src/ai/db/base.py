"""SQLAlchemy DeclarativeBase + naming convention.

소유: 공통 계약 (A+B 확인 완료 — 02_ownership §4 양자 12곳). 제약·인덱스 이름 규칙을
고정해 alembic autogenerate를 안정화한다(이름이 결정론적이어야 diff가 깨끗하다).
"""

from __future__ import annotations

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

#: 제약·인덱스 이름 규칙 — autogenerate 안정성.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """전 ORM 모델의 베이스. ERD 26테이블(db/models.py)이 이를 상속한다."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)
