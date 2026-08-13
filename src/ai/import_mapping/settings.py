"""Import 파라미터 — 하드코딩 금지(CLAUDE.md §6), env 주입 가능.

임계·상한을 설정으로 분리한다: 값이 바뀌면 코드 diff가 아니라 설정에서 바뀐다.
CONFIDENCE_REVIEW 등은 08 threshold 시트와 연동 예정(10_import_spec §6.3 [제안]).
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from ai.runtime.env_files import ENV_FILES


class ImportSettings(BaseSettings):
    """Import 결정론 파라미터 — env `IMPORT_*`로 주입."""

    model_config = SettingsConfigDict(env_file=ENV_FILES, extra="ignore")

    import_confidence_review: float = Field(default=0.9, ge=0.0, le=1.0)
    """이 값 미만이면 needs_review 플래그 + probing 기동 대상(§3.4)."""

    import_probe_loop_max: int = Field(default=5, ge=1)
    """조사 에이전트 도구 루프 상한(불변식 6 · 01 §3). 실행은 후속(agent PR)."""

    import_sample_max_rows: int = Field(default=20, ge=1)
    """프로파일 샘플 상한 행 수(§3.1 · 03 I1 마스킹 샘플 ≤20)."""


@lru_cache
def get_import_settings() -> ImportSettings:
    """설정 싱글턴 — 매 호출 재파싱 방지."""
    return ImportSettings()
