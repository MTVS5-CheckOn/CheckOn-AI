"""버전 고정 문학 작품 풀의 순수 계약과 저작권 만료 판정."""

from __future__ import annotations

from datetime import date
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ai.contracts.problem_generation import LiteratureGenre


class LiteraturePoolEntry(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    slug: str = Field(min_length=1)
    title: str = Field(min_length=1)
    author: str = Field(min_length=1)
    author_death_year: int | None = Field(default=None, gt=0)
    era: str = Field(min_length=1)
    genre: LiteratureGenre
    note: str
    source: str = Field(min_length=1)
    source_ref: str = Field(min_length=1)
    revision_id: int = Field(gt=0)
    collected_at: date
    content_sha256: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    char_count: int = Field(gt=0)
    file: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_public_domain_basis(self) -> Self:
        if self.author_death_year is None:
            if not self.note.strip():
                raise ValueError("사망연도 없는 작품에는 만료 판단 사유 note가 필요하다")
        elif not copyright_expired(
            death_year=self.author_death_year,
            as_of_year=self.collected_at.year,
        ):
            raise ValueError("보호기간이 끝나지 않은 작품은 문학 풀에 넣을 수 없다")
        return self


class LiteraturePoolIndex(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: str = Field(min_length=1)
    source_policy: str = Field(min_length=1)
    license_note: str = Field(min_length=1)
    min_chars: int = Field(gt=0)
    collected_at: date
    works: tuple[LiteraturePoolEntry, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_unique_entries(self) -> Self:
        slugs = [work.slug for work in self.works]
        files = [work.file for work in self.works]
        if len(set(slugs)) != len(slugs):
            raise ValueError("문학 풀 slug는 중복될 수 없다")
        if len(set(files)) != len(files):
            raise ValueError("문학 풀 본문 파일은 중복될 수 없다")
        return self


class LiteratureWork(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    metadata: LiteraturePoolEntry
    content: str = Field(min_length=1)


class LiteraturePool(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: str = Field(min_length=1)
    works: tuple[LiteratureWork, ...] = Field(min_length=1)


def copyright_expired(*, death_year: int, as_of_year: int) -> bool:
    """수집연도 시작 전에 사후 70년이 완전히 지났는지 보수적으로 판정한다."""

    return death_year + 70 < as_of_year


__all__ = [
    "LiteraturePool",
    "LiteraturePoolEntry",
    "LiteraturePoolIndex",
    "LiteratureWork",
    "copyright_expired",
]
