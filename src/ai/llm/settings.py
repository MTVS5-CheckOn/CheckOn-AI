"""LLM 추적 설정 — 환경 직접 접근 없이 pydantic-settings로 주입한다."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

from ai.runtime.env_files import ENV_FILES


class LlmSettings(BaseSettings):
    """LLM 플랫폼 설정. `.env` 또는 환경 변수에서 추적 활성 여부를 읽는다."""

    model_config = SettingsConfigDict(env_file=ENV_FILES, extra="ignore")

    #: ``.env``의 추적 설정을 스키마에 표기하기 위한 필드. **기동 가드는 이 값을 보지
    #: 않는다** — 판정 정본은 ``ai.runtime.tracing.external_tracing_active()`` 단일이며
    #: env·컨텍스트·run tree를 모두 덮는다(09 §2-16 후속 1).
    langsmith_tracing: bool = False


@lru_cache
def get_llm_settings() -> LlmSettings:
    """설정 싱글턴 — 게이트웨이 생성마다 환경을 다시 파싱하지 않는다."""
    return LlmSettings()
