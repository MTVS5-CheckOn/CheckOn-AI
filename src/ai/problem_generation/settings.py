"""문항 생성 설정 — pydantic-settings (하드코딩 금지, 03_coding_rules §1).

소유: 염준영 (problem_generation). 외부 대조 코퍼스는 저장소에 반입하지 않으므로
경로를 환경/.env 에서 주입한다 — `os.environ` 직접 접근 금지.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

from ai.runtime.env_files import ENV_FILES


class ProblemGenerationSettings(BaseSettings):
    """문항 생성 실행 설정."""

    model_config = SettingsConfigDict(env_file=ENV_FILES, extra="ignore")

    external_corpus_root: Path | None = None
    """R-8 외부 대조 코퍼스(AI Hub 71857) 루트.

    🔴 **미지정이 곧 「검사 안 함」이다** — 조용한 통과가 아니라
    `RuleValidationResult.external_reference_checked=False` 로 드러난다(06 §1 —
    *"코퍼스 없이 여는 것을 금지한다"*). 자료를 패키지에 넣지 않는 이유는 CLAUDE.md §3
    (`local_data/` 반입 금지)과 05 §1.1.4(비상업 전제 · W18)다.
    """


@lru_cache
def get_problem_generation_settings() -> ProblemGenerationSettings:
    """설정 싱글턴 — 매 호출 재파싱 방지."""
    return ProblemGenerationSettings()
