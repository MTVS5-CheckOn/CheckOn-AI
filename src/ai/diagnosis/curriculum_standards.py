"""2022 개정 국어과 교육과정 고등학교 성취기준 **코드 레지스트리** 로드.

🔴 **여기에 성취기준 문면은 없다.** 정본(교육부 고시 제2022-33호 [별책 5])의 라이선스가
확인되지 않아 코드(식별자)만 담는다(05 §1.1.4 · W15). 이 모듈의 쓸모는 문면 제공이
아니라 **인용 어휘의 정본**이다 — 그래프 노드가 다는 `curriculum-2022:<code>` 근거가
실재하는 코드인지 대조한다.
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Self

import yaml  # type: ignore[import-untyped]  # PyYAML은 공식 타입 정보를 제공하지 않는다.
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

#: 노드 `source_refs` 가 성취기준을 인용할 때 쓰는 접두.
CURRICULUM_REF_PREFIX = "curriculum-2022:"

DEFAULT_STANDARDS_PATH = Path(__file__).resolve().parent / "data" / "curriculum_standards.yaml"


class StandardsLoadError(ValueError):
    """성취기준 코드 레지스트리를 안전하게 사용할 수 없음."""


class SubjectTrack(StrEnum):
    """2022 개정 고등 국어 과목 구분."""

    COMMON = "common"
    GENERAL_ELECTIVE = "general_elective"
    CAREER_ELECTIVE = "career_elective"
    CONVERGENCE_ELECTIVE = "convergence_elective"


class CurriculumSubject(BaseModel):
    """한 과목의 성취기준 코드 묶음."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    prefix: str = Field(min_length=1)
    name: str = Field(min_length=1)
    track: SubjectTrack
    codes: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_codes(self) -> Self:
        if len(set(self.codes)) != len(self.codes):
            raise ValueError("성취기준 코드는 중복될 수 없다")
        wrong = [code for code in self.codes if not code.startswith(f"{self.prefix}-")]
        if wrong:
            raise ValueError(f"과목 접두와 다른 코드가 있다: {wrong}")
        return self


class CurriculumStandards(BaseModel):
    """고등학교 성취기준 코드 레지스트리."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    version: str = Field(min_length=1)
    source: str = Field(min_length=1)
    subjects: tuple[CurriculumSubject, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_subjects(self) -> Self:
        prefixes = [subject.prefix for subject in self.subjects]
        if len(set(prefixes)) != len(prefixes):
            raise ValueError("과목 접두는 중복될 수 없다")
        return self

    @property
    def codes(self) -> frozenset[str]:
        return frozenset(code for subject in self.subjects for code in subject.codes)

    def subject_of(self, code: str) -> CurriculumSubject | None:
        return next(
            (subject for subject in self.subjects if code in subject.codes), None
        )


def load_curriculum_standards(
    path: Path = DEFAULT_STANDARDS_PATH,
) -> CurriculumStandards:
    """레지스트리를 읽어 검증된 코드 집합으로 돌려준다."""

    try:
        raw: object = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as error:
        raise StandardsLoadError(f"성취기준 레지스트리를 읽을 수 없다: {path}") from error
    try:
        return CurriculumStandards.model_validate(raw)
    except ValidationError as error:
        raise StandardsLoadError(f"성취기준 레지스트리가 스키마를 위반한다: {path}") from error


def cited_codes(source_refs: tuple[str, ...]) -> tuple[str, ...]:
    """`source_refs` 에서 성취기준 인용만 골라 코드로 되돌린다."""

    return tuple(
        ref.removeprefix(CURRICULUM_REF_PREFIX)
        for ref in source_refs
        if ref.startswith(CURRICULUM_REF_PREFIX)
    )


__all__ = [
    "CURRICULUM_REF_PREFIX",
    "DEFAULT_STANDARDS_PATH",
    "CurriculumStandards",
    "CurriculumSubject",
    "StandardsLoadError",
    "SubjectTrack",
    "cited_codes",
    "load_curriculum_standards",
]
