"""Alembic 마이그레이션 환경 — async 엔진 기반.

DB URL은 ai.db.settings에서 주입한다(alembic.ini에 하드코딩 금지). target_metadata는
ai.db.models(Base.metadata) — 26테이블이 여기 등록된다. offline 모드는 DB 없이 SQL만
생성하므로 로컬 PG가 없어도 마이그레이션을 검증할 수 있다(99 ⑫).
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy.engine import Connection

from ai.db.models import Base
from ai.db.settings import get_db_settings

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _url() -> str:
    return get_db_settings().database_url


def run_migrations_offline() -> None:
    """offline — DB 접속 없이 SQL 스크립트만 생성한다."""
    context.configure(
        url=_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def _do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    """online — async 엔진으로 실제 DB에 적용한다."""
    from ai.db.session import get_engine

    engine = get_engine()
    async with engine.connect() as connection:
        await connection.run_sync(_do_run_migrations)
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
