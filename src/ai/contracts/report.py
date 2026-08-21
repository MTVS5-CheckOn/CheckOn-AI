"""리포트 스튜디오의 공용 경계 계약 초안.

⚠ 양자 승인 대상 초안 — 독립 ``report`` capability가 A 소유 composition 내부 타입에
의존하지 않도록 경계에 둔다. 발송·승인과 LLM 호출 계약은 이 파일의 범위가 아니다.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

type NonEmptyStr = Annotated[str, Field(min_length=1)]
type NumberUsed = Annotated[int, Field(ge=0)]


class ReportBlockKind(StrEnum):
    """리포트 본문을 구성하는 닫힌 블록 어휘."""

    GREETING = "greeting"
    FACT = "fact"
    CHART_ANALYSIS = "chart_analysis"
    SUGGESTION = "suggestion"
    CLOSING = "closing"


class ReportRevisionKind(StrEnum):
    """블록 리비전을 만든 행위 — 문항 수정 리비전과 같은 감사 축."""

    AI_DRAFT = "ai_draft"
    AI_REWRITE = "ai_rewrite"
    TEACHER_EDIT = "teacher_edit"
    ROLLBACK = "rollback"


class ReportEvidenceRef(BaseModel):
    """리포트 블록이 인용한 백엔드 원본의 논리 참조."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_table: NonEmptyStr
    record_id: NonEmptyStr
    summary: NonEmptyStr


class ReportBlockRevision(BaseModel):
    """블록 한 리비전의 불변 스냅샷."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    revision_no: int = Field(ge=0)
    revision_kind: ReportRevisionKind
    ai_original: NonEmptyStr
    teacher_edit: NonEmptyStr | None = None
    revert_to_revision_no: int | None = Field(default=None, ge=0)
    evidence: tuple[ReportEvidenceRef, ...] = Field(min_length=1)
    numbers_used: tuple[NumberUsed, ...]
    gate_passed: bool = False

    @property
    def display_content(self) -> str:
        """화면 후보 문면. 강사 수정본이 있으면 AI 원문보다 우선한다."""

        return self.teacher_edit if self.teacher_edit is not None else self.ai_original

    @model_validator(mode="after")
    def validate_revision(self) -> Self:
        if len(set(self.numbers_used)) != len(self.numbers_used):
            raise ValueError("numbers_used는 중복될 수 없다")
        if self.revision_kind is ReportRevisionKind.TEACHER_EDIT:
            if self.teacher_edit is None or self.revert_to_revision_no is not None:
                raise ValueError("teacher_edit 리비전에는 강사 수정본만 필요하다")
        elif self.revision_kind is ReportRevisionKind.ROLLBACK:
            if (
                self.revert_to_revision_no is None
                or self.revert_to_revision_no >= self.revision_no
            ):
                raise ValueError("rollback은 현재보다 이전 리비전을 가리켜야 한다")
        elif self.teacher_edit is not None or self.revert_to_revision_no is not None:
            raise ValueError("AI 리비전에는 강사 수정본이나 복귀 대상이 있을 수 없다")
        return self


class ReportBlock(BaseModel):
    """AI 원문·강사 수정본을 리비전별로 보존하는 리포트 블록."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    block_id: UUID
    seq: int = Field(ge=0)
    block_type: ReportBlockKind
    revisions: tuple[ReportBlockRevision, ...] = Field(min_length=1)
    active_revision_no: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_revisions(self) -> Self:
        revision_numbers = [revision.revision_no for revision in self.revisions]
        if revision_numbers != list(range(len(self.revisions))):
            raise ValueError("리비전은 0부터 빠짐없이 오름차순이어야 한다")
        if self.revisions[0].revision_kind is not ReportRevisionKind.AI_DRAFT:
            raise ValueError("첫 리비전은 ai_draft여야 한다")
        if any(
            revision.revision_kind is ReportRevisionKind.AI_DRAFT
            for revision in self.revisions[1:]
        ):
            raise ValueError("ai_draft 리비전은 처음 한 번만 존재할 수 있다")
        if self.active_revision_no != revision_numbers[-1]:
            raise ValueError("활성 리비전은 감사 이력의 마지막 리비전이어야 한다")

        return self

    @property
    def active_revision(self) -> ReportBlockRevision:
        """현재 화면 후보 리비전."""

        return self.revisions[self.active_revision_no]

    def apply_teacher_edit(
        self,
        content: str,
        *,
        numbers_used: tuple[int, ...],
    ) -> Self:
        """강사 수정본을 새 리비전으로 추가하고 게이트를 다시 닫는다."""

        current = self.active_revision
        revision = ReportBlockRevision(
            revision_no=len(self.revisions),
            revision_kind=ReportRevisionKind.TEACHER_EDIT,
            ai_original=current.ai_original,
            teacher_edit=content,
            evidence=current.evidence,
            numbers_used=numbers_used,
            gate_passed=False,
        )
        return self.model_copy(
            update={
                "revisions": (*self.revisions, revision),
                "active_revision_no": revision.revision_no,
            }
        )

    def apply_ai_rewrite(
        self,
        content: str,
        *,
        numbers_used: tuple[int, ...],
    ) -> Self:
        """AI 재작성 범위의 강사 수정본을 비운 새 리비전을 추가한다."""

        current = self.active_revision
        revision = ReportBlockRevision(
            revision_no=len(self.revisions),
            revision_kind=ReportRevisionKind.AI_REWRITE,
            ai_original=content,
            teacher_edit=None,
            evidence=current.evidence,
            numbers_used=numbers_used,
            gate_passed=False,
        )
        return self.model_copy(
            update={
                "revisions": (*self.revisions, revision),
                "active_revision_no": revision.revision_no,
            }
        )

    def restore_revision(self, revision_no: int) -> Self:
        """보존된 스냅샷을 rollback 리비전으로 복원하고 게이트를 다시 닫는다."""

        if revision_no < 0 or revision_no >= len(self.revisions):
            raise ValueError("복귀 대상 리비전이 없다")
        target = self.revisions[revision_no]
        restored = ReportBlockRevision(
            revision_no=len(self.revisions),
            revision_kind=ReportRevisionKind.ROLLBACK,
            ai_original=target.ai_original,
            teacher_edit=target.teacher_edit,
            revert_to_revision_no=revision_no,
            evidence=target.evidence,
            numbers_used=target.numbers_used,
            gate_passed=False,
        )
        return self.model_copy(
            update={
                "revisions": (*self.revisions, restored),
                "active_revision_no": restored.revision_no,
            }
        )


class ReportArtifact(BaseModel):
    """게이트 통과 블록만 담을 수 있는 학부모 리포트 산출 계약."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    report_id: UUID
    tenant_id: NonEmptyStr
    guardian_ref: NonEmptyStr
    blocks: tuple[ReportBlock, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_blocks(self) -> Self:
        sequences = [block.seq for block in self.blocks]
        if sequences != sorted(sequences) or len(set(sequences)) != len(sequences):
            raise ValueError("리포트 블록 seq는 중복 없이 오름차순이어야 한다")
        for block in self.blocks:
            revision = block.active_revision
            if not revision.gate_passed:
                raise ValueError("게이트를 통과하지 않은 블록은 리포트 산출에 넣을 수 없다")
        return self


__all__ = [
    "ReportArtifact",
    "ReportBlock",
    "ReportEvidenceRef",
    "ReportBlockKind",
    "ReportBlockRevision",
    "ReportRevisionKind",
]
