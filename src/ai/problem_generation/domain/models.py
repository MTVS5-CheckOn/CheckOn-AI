"""출제 도메인 값 객체 — 프레임워크 비의존."""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ai.contracts.problem_generation import (
    DifficultyBand,
    GeneratedItem,
    ItemResult,
    ProblemItemStatus,
    SolveResult,
)


class SchemaValidationIssue(BaseModel):
    """LLM에 되돌려도 되는 Pydantic 검증 실패의 최소 표현."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    path: str = Field(min_length=1)
    reason: str = Field(min_length=1)


class RetryContext(BaseModel):
    """다음 생성 시도에 전달하는 비민감 실패 요약."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    attempt_no: int = Field(ge=1, le=3)
    failed_checks: tuple[str, ...] = ()
    schema_issues: tuple[SchemaValidationIssue, ...] = ()
    previous_stem_hash: str | None = Field(
        default=None,
        pattern=r"^sha256:[0-9a-f]{64}$",
    )
    difficulty_direction: str | None = Field(default=None, min_length=1)


class CandidateSnapshot(BaseModel):
    """게이트 ①·②를 통과한 시도 1개의 불변 복귀본."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    set_id: UUID
    slot_index: int = Field(ge=0)
    attempt_no: int = Field(ge=1, le=3)
    item: GeneratedItem
    solve_result: SolveResult
    context_pack_id: UUID
    difficulty_est: float
    difficulty_band: DifficultyBand
    snapshot_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class StoredProblemItem(BaseModel):
    """M2에서 DB 대신 보존하는 슬롯 최종본."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    item_id: UUID
    set_id: UUID
    slot_index: int = Field(ge=0)
    result: ItemResult
    candidate_ref: str | None = Field(default=None, min_length=1)
    item: GeneratedItem | None = None

    @model_validator(mode="after")
    def validate_body(self) -> StoredProblemItem:
        verified_statuses = {
            ProblemItemStatus.VERIFIED,
            ProblemItemStatus.NEEDS_REVIEW,
        }
        if self.result.item_id != self.item_id:
            raise ValueError("저장 문항 ID와 ItemResult.item_id가 다르다")
        if self.result.status in verified_statuses and (
            self.item is None or self.candidate_ref is None
        ):
            raise ValueError("검증 완료 최종본에는 item과 candidate_ref가 필요하다")
        if self.result.status not in verified_statuses | {
            ProblemItemStatus.VERIFICATION_UNAVAILABLE
        }:
            raise ValueError("최종본 저장소에는 dropped 상태를 저장하지 않는다")
        return self

    @property
    def attempt_no(self) -> int:
        return self.result.attempt_no

    @property
    def status(self) -> ProblemItemStatus:
        return self.result.status


class TargetPlan(BaseModel):
    """슬롯이 순환 사용할 결정론 목표."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    skill_node_id: str = Field(min_length=1)
    diagnostic_purpose: bool = False
