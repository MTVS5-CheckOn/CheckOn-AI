"""async 엔진·세션 팩토리.

소유: 박진희 (db). import 시점에 DB에 접속하지 않는다(lazy) — 오프라인 테스트·마이그레이션
생성이 DB 없이 가능해야 한다. 접속은 실제 세션 사용 시점에.
"""

from __future__ import annotations

from functools import lru_cache

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from ai.db.settings import get_db_settings


@lru_cache
def get_engine() -> AsyncEngine:
    """async 엔진 싱글턴 — 첫 호출 시 생성(접속은 실제 쿼리 때)."""
    return create_async_engine(get_db_settings().database_url, future=True)


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    """세션 팩토리 — 저장소가 이걸로 세션을 연다."""
    return async_sessionmaker(get_engine(), expire_on_commit=False)
