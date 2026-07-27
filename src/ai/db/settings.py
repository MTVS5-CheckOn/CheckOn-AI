"""DB 접속 설정 — pydantic-settings (하드코딩 금지, 03_coding_rules §1).

소유: 박진희 (db). DATABASE_URL을 환경/.env에서 주입한다 — os.environ 직접 접근 금지.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class DbSettings(BaseSettings):
    """DB 설정. `.env` 또는 환경 변수로 접속정보와 저장소 종류를 주입한다."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://localhost/checkon_ai"
    """async 드라이버(asyncpg) URL. 실배포는 .env로 주입."""

    agent_checkpoint_database_url: str | None = None
    """LangGraph PostgresSaver용 psycopg URL.

    미지정 시 ``database_url``에서 SQLAlchemy 드라이버 표기만 제거해 사용한다.
    체크포인트를 별도 DB로 격리할 때만 환경 변수로 명시한다.
    """

    store_backend: str = "memory"
    """저장소 선택 — "memory"(기본·테스트/데모, DB 없이 동작) | "pg"(실 DB 적재).

    database_url에 기본값이 있어 URL 유무로는 백엔드를 못 고른다 — 명시 플래그로 고른다.
    실 DB 연동(99 ⑫) 전까지 기본 memory라 CI는 DB 없이 통과한다.
    """


@lru_cache
def get_db_settings() -> DbSettings:
    """설정 싱글턴 — 매 호출 재파싱 방지."""
    return DbSettings()
