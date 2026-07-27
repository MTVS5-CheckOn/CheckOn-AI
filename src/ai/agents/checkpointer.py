"""LangGraph 공식 PostgresSaver의 연결·스키마 초기화 경계."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from sqlalchemy.engine import make_url

from ai.db.settings import DbSettings, get_db_settings


def checkpoint_connection_string(settings: DbSettings) -> str:
    """SQLAlchemy async URL을 PostgresSaver가 받는 psycopg URL로 변환한다."""

    raw_url = settings.agent_checkpoint_database_url or settings.database_url
    url = make_url(raw_url)
    if url.get_backend_name() != "postgresql":
        raise ValueError("LangGraph 체크포인터는 PostgreSQL URL만 지원한다")
    return url.set(drivername="postgresql").render_as_string(hide_password=False)


@asynccontextmanager
async def open_checkpointer(
    settings: DbSettings | None = None,
) -> AsyncIterator[AsyncPostgresSaver]:
    """요청 수명보다 긴 애플리케이션 수명주기에서 PostgresSaver를 연다."""

    resolved = settings or get_db_settings()
    connection_string = checkpoint_connection_string(resolved)
    async with AsyncPostgresSaver.from_conn_string(connection_string) as saver:
        yield saver


async def setup_checkpointer_schema(settings: DbSettings | None = None) -> None:
    """배포·마이그레이션 단계에서 LangGraph 체크포인트 테이블을 초기화한다."""

    async with open_checkpointer(settings) as saver:
        await saver.setup()
