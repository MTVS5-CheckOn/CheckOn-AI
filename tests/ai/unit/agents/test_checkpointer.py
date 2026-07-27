"""LangGraph PostgresSaver 연결 경계 검증."""

import pytest

from ai.agents.checkpointer import checkpoint_connection_string
from ai.db.settings import DbSettings


def test_asyncpg_url_is_converted_to_psycopg_connection_string() -> None:
    settings = DbSettings(
        database_url="postgresql+asyncpg://worker:secret@db.internal:5432/checkon"
    )

    assert (
        checkpoint_connection_string(settings)
        == "postgresql://worker:secret@db.internal:5432/checkon"
    )


def test_explicit_checkpoint_url_takes_precedence() -> None:
    settings = DbSettings(
        database_url="postgresql+asyncpg://localhost/checkon",
        agent_checkpoint_database_url="postgresql://checkpoint:secret@checkpoint-db/checkon",
    )

    assert (
        checkpoint_connection_string(settings)
        == "postgresql://checkpoint:secret@checkpoint-db/checkon"
    )


def test_explicit_checkpoint_url_is_validated_and_normalized() -> None:
    settings = DbSettings(
        database_url="postgresql+asyncpg://localhost/checkon",
        agent_checkpoint_database_url=(
            "postgresql+asyncpg://checkpoint:secret@checkpoint-db/checkon"
        ),
    )

    assert (
        checkpoint_connection_string(settings)
        == "postgresql://checkpoint:secret@checkpoint-db/checkon"
    )


def test_non_postgres_database_is_rejected() -> None:
    settings = DbSettings(database_url="sqlite+aiosqlite:///local.db")

    with pytest.raises(ValueError, match="PostgreSQL"):
        checkpoint_connection_string(settings)


def test_non_postgres_explicit_checkpoint_url_is_rejected() -> None:
    settings = DbSettings(
        database_url="postgresql+asyncpg://localhost/checkon",
        agent_checkpoint_database_url="sqlite:///checkpoint.db",
    )

    with pytest.raises(ValueError, match="PostgreSQL"):
        checkpoint_connection_string(settings)
