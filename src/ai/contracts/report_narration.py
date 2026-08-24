"""리포트 본문 문장화 LLM 응답 계약."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ReportNarrationDraft(BaseModel):
    """LLM이 반환하는 문장과 그 문장에 쓴 정수 목록."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    content: str = Field(min_length=1)
    numbers_used: tuple[int, ...]

    @field_validator("numbers_used")
    @classmethod
    def validate_unique_numbers(cls, value: tuple[int, ...]) -> tuple[int, ...]:
        if len(value) != len(set(value)):
            raise ValueError("numbers_used는 중복될 수 없다")
        if any(number < 0 for number in value):
            raise ValueError("numbers_used는 음수일 수 없다")
        return value


__all__ = ["ReportNarrationDraft"]
