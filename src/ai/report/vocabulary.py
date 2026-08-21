"""리포트 블록 닫힌 어휘의 엄격 YAML 로더."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal, Self

import yaml  # type: ignore[import-untyped]
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from ai.contracts.report import ReportBlockKind

DEFAULT_BLOCK_KINDS_PATH = Path(__file__).resolve().parent / "data" / "block_kinds.yaml"


class ReportVocabularyError(ValueError):
    """리포트 블록 어휘를 읽거나 검증할 수 없음."""


class ReportBlockVocabulary(BaseModel):
    """버전이 고정된 리포트 블록 어휘 문서."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    version: Literal["report-block-kinds.v1"]
    block_kinds: tuple[ReportBlockKind, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_block_kinds(self) -> Self:
        if len(set(self.block_kinds)) != len(self.block_kinds):
            raise ValueError("리포트 블록 어휘는 중복될 수 없다")
        expected = set(ReportBlockKind)
        found = set(self.block_kinds)
        if found != expected:
            missing = sorted(kind.value for kind in expected - found)
            unknown = sorted(kind.value for kind in found - expected)
            raise ValueError(f"리포트 블록 어휘가 완전하지 않다: 누락={missing} 미등록={unknown}")
        return self


def load_report_block_vocabulary(
    path: Path = DEFAULT_BLOCK_KINDS_PATH,
) -> ReportBlockVocabulary:
    """어휘 파일을 읽어 5종 완전성·중복·추가 필드를 실패 닫힘으로 검증한다."""

    try:
        raw: object = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise ReportVocabularyError(f"리포트 블록 어휘 파일을 읽을 수 없다: {path}") from error
    except yaml.YAMLError as error:
        raise ReportVocabularyError(f"리포트 블록 어휘 YAML 오류: {error}") from error
    try:
        return ReportBlockVocabulary.model_validate(raw)
    except ValidationError as error:
        raise ReportVocabularyError(f"리포트 블록 어휘 스키마 오류: {error}") from error


@lru_cache
def default_report_block_vocabulary() -> ReportBlockVocabulary:
    """패키지 동봉 어휘를 프로세스당 한 번 검증한다."""

    return load_report_block_vocabulary()


__all__ = [
    "DEFAULT_BLOCK_KINDS_PATH",
    "ReportBlockVocabulary",
    "ReportVocabularyError",
    "default_report_block_vocabulary",
    "load_report_block_vocabulary",
]
