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
    settings = get_db_settings()
    #: 🔴 **명시한다** — 종전에는 기본값에 맡겨져 관계식을 세울 수 없었다(99 #127).
    return create_async_engine(
        settings.database_url,
        future=True,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
    )


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    """세션 팩토리 — 저장소가 이걸로 세션을 연다."""
    return async_sessionmaker(get_engine(), expire_on_commit=False)
