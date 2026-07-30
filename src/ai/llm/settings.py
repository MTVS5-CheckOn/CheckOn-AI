"""LLM 추적 설정 — 환경 직접 접근 없이 pydantic-settings로 주입한다."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class LlmSettings(BaseSettings):
    """LLM 플랫폼 설정. `.env` 또는 환경 변수에서 추적 활성 여부를 읽는다."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    langsmith_tracing: bool = False


@lru_cache
def get_llm_settings() -> LlmSettings:
    """설정 싱글턴 — 게이트웨이 생성마다 환경을 다시 파싱하지 않는다."""
    return LlmSettings()
