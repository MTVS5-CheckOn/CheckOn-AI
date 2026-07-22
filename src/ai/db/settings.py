"""DB 접속 설정 — pydantic-settings (하드코딩 금지, 03_coding_rules §1).

소유: 박진희 (db). DATABASE_URL을 환경/.env에서 주입한다 — os.environ 직접 접근 금지.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class DbSettings(BaseSettings):
    """DB 설정. `.env`의 `DATABASE_URL` 또는 환경 변수로 주입."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://localhost/checkon_ai"
    """async 드라이버(asyncpg) URL. 실배포는 .env로 주입."""


@lru_cache
def get_db_settings() -> DbSettings:
    """설정 싱글턴 — 매 호출 재파싱 방지."""
    return DbSettings()
