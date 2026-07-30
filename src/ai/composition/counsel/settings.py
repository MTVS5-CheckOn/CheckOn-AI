"""counsel_pack 설정 — 임계값을 코드에 박지 않는다.

03 §1: "값이 바뀌면 코드 diff가 생기면 위치가 틀린 것".
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class CounselSettings(BaseSettings):
    """상담팩 운영 파라미터 — env 주입."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    counsel_llm_failure_circuit: int = 3
    """연속 LLM 실패 학생 수 임계 → paused. 기본 3은 `langgraph_state.md` §1.3
    ("LLM 연속 실패 3학생(서킷)")의 기준 수치를 그대로 옮긴 값이다."""


@lru_cache
def get_counsel_settings() -> CounselSettings:
    return CounselSettings()
