"""리포트 스튜디오의 공용 경계 계약 초안.

⚠ 양자 승인 대상 초안 — 독립 ``report`` capability가 A 소유 composition 내부 타입에
의존하지 않도록 경계에 둔다. 발송·승인과 LLM 호출 계약은 이 파일의 범위가 아니다.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Final, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ai.contracts.diagnosis import CellVerdict
from ai.contracts.problem_generation import DifficultyBand
from ai.contracts.taxonomy import AreaTag, TypeTag

type NonEmptyStr = Annotated[str, Field(min_length=1)]
type NumberUsed = Annotated[int, Field(ge=0)]

AI_REWRITE_WARNING: Final[Literal["AI로 다시 쓰면 그 범위의 강사 수정분은 덮입니다"]] = (
    "AI로 다시 쓰면 그 범위의 강사 수정분은 덮입니다"
)


class ReportAudience(StrEnum):
    """리포트 사양서 §4가 명시한 audience 값."""

    GUARDIAN = "guardian"
    TEACHER_ONLY = "teacher_only"


class ReportValueStatus(StrEnum):
    """0과 데이터 부재, 정책상 미산출을 구분하는 상태."""

    AVAILABLE = "available"
    NO_DATA = "no_data"
    NOT_PRODUCED = "not_produced"


class ReportDataBlockKind(StrEnum):
    """LLM 없이 조립하는 리포트 스튜디오 데이터 블록."""

    WEAKNESS_GRID = "weakness_grid"
    REPRESENTATIVE_TYPES = "representative_types"
    AREA_ACHIEVEMENT = "area_achievement"
    MISCONCEPTION_FREQUENCY = "misconception_frequency"
    DIFFICULTY_DISTRIBUTION = "difficulty_distribution"
    ITEM_COUNTS = "item_counts"


class ReportUnproducedMetric(StrEnum):
    """현재 입력·정책으로 만들 수 없어 명시적으로 비워 두는 축."""

    NATIONAL_PERCENTILE = "national_percentile"
    WEEKLY_ACCURACY_INTERVENTION = "weekly_accuracy_intervention"
    RECENT_SIX_WEEK_BASELINE = "recent_six_week_baseline"


class ReportNumber(BaseModel):
    """출처를 잃지 않는 리포트 수치."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: NonEmptyStr
    value: int | float
    evidence: tuple[ReportEvidenceRef, ...] = Field(min_length=1)


class ReportGridCellData(BaseModel):
    """영역×유형 한 칸. apply 수신량은 보존하되 판정은 산출하지 않는다."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    area_tag: AreaTag
    type_tag: TypeTag
    status: ReportValueStatus
    acc: float | None = Field(default=None, ge=0.0, le=1.0)
    n: int = Field(ge=0)
    verdict: CellVerdict | None = None
    severity: float | None = Field(default=None, gt=0.0, le=1.0)

    @model_validator(mode="after")
    def validate_status_payload(self) -> Self:
        if self.status is ReportValueStatus.AVAILABLE:
            if self.n == 0 or self.acc is None or self.verdict is None:
                raise ValueError("available 셀에는 관측 수치와 판정이 필요하다")
            return self
        if self.acc is not None or self.verdict is not None or self.severity is not None:
            raise ValueError("미산출 셀에는 판정 수치를 채울 수 없다")
        if self.status is ReportValueStatus.NO_DATA and self.n != 0:
            raise ValueError("no_data 셀의 문항 수는 0이어야 한다")
        return self


class ReportRepresentativeTypeData(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    area_tag: AreaTag
    type_tag: TypeTag
    acc: float = Field(ge=0.0, le=1.0)
    n: int = Field(ge=1)
    severity: float | None = Field(default=None, gt=0.0, le=1.0)


class ReportAreaAchievementData(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    area_tag: AreaTag
    status: ReportValueStatus
    acc: float | None = Field(default=None, ge=0.0, le=1.0)
    n: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_status_payload(self) -> Self:
        if self.status is ReportValueStatus.AVAILABLE and (self.acc is None or self.n == 0):
            raise ValueError("available 영역 성취도에는 관측 수치가 필요하다")
        if self.status is not ReportValueStatus.AVAILABLE and (self.acc is not None or self.n != 0):
            raise ValueError("no_data 영역 성취도에는 수치를 채울 수 없다")
        return self


class ReportMisconceptionEntry(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    group_key: NonEmptyStr
    misconception_tag: NonEmptyStr
    count: int = Field(ge=1)


class WeaknessGridPayload(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    cell_min_items: int = Field(ge=1)
    cells: tuple[ReportGridCellData, ...] = Field(min_length=1)


class RepresentativeTypesPayload(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    strength: ReportRepresentativeTypeData | None = None
    weakness: ReportRepresentativeTypeData | None = None

    @model_validator(mode="after")
    def validate_roles(self) -> Self:
        if self.strength is not None and self.strength.severity is not None:
            raise ValueError("대표 강점에는 취약 심각도를 기록하지 않는다")
        if self.weakness is not None and self.weakness.severity is None:
            raise ValueError("대표 취약 유형에는 심각도가 필요하다")
        return self


class AreaAchievementPayload(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    areas: tuple[ReportAreaAchievementData, ...] = Field(min_length=1)
    own_average: float | None = Field(default=None, ge=0.0, le=1.0)


class MisconceptionFrequencyPayload(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    by_area: tuple[ReportMisconceptionEntry, ...]
    by_node: tuple[ReportMisconceptionEntry, ...]
    excluded_missing_chosen_no: int = Field(ge=0)
    excluded_missing_misconception_tag: int = Field(ge=0)


class DifficultyDistributionPayload(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    counts: dict[DifficultyBand, Annotated[int, Field(ge=0)]]

    @model_validator(mode="after")
    def validate_complete_bands(self) -> Self:
        if set(self.counts) != set(DifficultyBand):
            raise ValueError("난이도 분포에는 하·중·상 전체 밴드가 필요하다")
        return self


class ItemCountsPayload(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    scored_items: int = Field(ge=0)
    judged_items: int = Field(ge=0)


type ReportDataPayload = (
    WeaknessGridPayload
    | RepresentativeTypesPayload
    | AreaAchievementPayload
    | MisconceptionFrequencyPayload
    | DifficultyDistributionPayload
    | ItemCountsPayload
)


class ReportDataBlock(BaseModel):
    """결정론 payload와 그 안에서 사용한 모든 수치의 출처 목록."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: ReportDataBlockKind
    payload: ReportDataPayload
    numbers_used: tuple[ReportNumber, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_payload_kind(self) -> Self:
        expected = {
            ReportDataBlockKind.WEAKNESS_GRID: WeaknessGridPayload,
            ReportDataBlockKind.REPRESENTATIVE_TYPES: RepresentativeTypesPayload,
            ReportDataBlockKind.AREA_ACHIEVEMENT: AreaAchievementPayload,
            ReportDataBlockKind.MISCONCEPTION_FREQUENCY: MisconceptionFrequencyPayload,
            ReportDataBlockKind.DIFFICULTY_DISTRIBUTION: DifficultyDistributionPayload,
            ReportDataBlockKind.ITEM_COUNTS: ItemCountsPayload,
        }[self.kind]
        if not isinstance(self.payload, expected):
            raise ValueError("데이터 블록 kind와 payload 타입이 일치하지 않는다")
        names = tuple(number.name for number in self.numbers_used)
        if len(names) != len(set(names)):
            raise ValueError("numbers_used 이름은 블록 안에서 중복될 수 없다")
        return self


class ReportStudioData(BaseModel):
    """조립 완료된 여섯 블록과 의도적으로 미산출인 세 축."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    blocks: tuple[ReportDataBlock, ...]
    unproduced: tuple[ReportUnproducedMetric, ...]

    @model_validator(mode="after")
    def validate_completeness(self) -> Self:
        kinds = tuple(block.kind for block in self.blocks)
        if kinds != tuple(ReportDataBlockKind):
            raise ValueError("결정론 리포트 블록은 정본 순서의 여섯 종류가 모두 필요하다")
        if self.unproduced != tuple(ReportUnproducedMetric):
            raise ValueError("미산출 축은 정본 순서의 세 종류가 모두 필요하다")
        return self


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


class ReportMetricInput(BaseModel):
    """audience 필터를 통과하기 전의 수치 입력 한 항목."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    metric_key: NonEmptyStr
    value: float
    audience: ReportAudience
    evidence: tuple[ReportEvidenceRef, ...] = Field(min_length=1)


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
    restored_by: NonEmptyStr | None = None
    restored_at: datetime | None = None

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
            if self.revert_to_revision_no is None or self.revert_to_revision_no >= self.revision_no:
                raise ValueError("rollback은 현재보다 이전 리비전을 가리켜야 한다")
            if self.restored_by is None or self.restored_at is None:
                raise ValueError("rollback에는 복귀 행위자와 시각이 필요하다")
        elif self.teacher_edit is not None or self.revert_to_revision_no is not None:
            raise ValueError("AI 리비전에는 강사 수정본이나 복귀 대상이 있을 수 없다")
        if self.revision_kind is not ReportRevisionKind.ROLLBACK and (
            self.restored_by is not None or self.restored_at is not None
        ):
            raise ValueError("복귀 감사 필드는 rollback 리비전에만 기록한다")
        return self


class ReportBlock(BaseModel):
    """AI 원문·강사 수정본을 리비전별로 보존하는 리포트 블록."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    block_id: UUID
    seq: int = Field(ge=0)
    block_type: ReportBlockKind
    revisions: tuple[ReportBlockRevision, ...] = Field(min_length=1)
    active_revision_no: int = Field(ge=0)
    ai_rewrite_warning: Literal["AI로 다시 쓰면 그 범위의 강사 수정분은 덮입니다"] = (
        AI_REWRITE_WARNING
    )

    @model_validator(mode="after")
    def validate_revisions(self) -> Self:
        revision_numbers = [revision.revision_no for revision in self.revisions]
        if revision_numbers != list(range(len(self.revisions))):
            raise ValueError("리비전은 0부터 빠짐없이 오름차순이어야 한다")
        if self.revisions[0].revision_kind is not ReportRevisionKind.AI_DRAFT:
            raise ValueError("첫 리비전은 ai_draft여야 한다")
        if any(
            revision.revision_kind is ReportRevisionKind.AI_DRAFT for revision in self.revisions[1:]
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

    def restore_revision(
        self,
        revision_no: int,
        *,
        restored_by: str,
        restored_at: datetime,
    ) -> Self:
        """보존된 스냅샷을 rollback 리비전으로 복원하고 게이트를 다시 닫는다."""

        if revision_no < 0 or revision_no >= len(self.revisions):
            raise ValueError("복귀 대상 리비전이 없다")
        target = self.revisions[revision_no]
        restored = ReportBlockRevision(
            revision_no=len(self.revisions),
            revision_kind=ReportRevisionKind.ROLLBACK,
            ai_original=target.ai_original,
            teacher_edit=None,
            revert_to_revision_no=revision_no,
            evidence=target.evidence,
            numbers_used=target.numbers_used,
            gate_passed=False,
            restored_by=restored_by,
            restored_at=restored_at,
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
    "AI_REWRITE_WARNING",
    "ReportArtifact",
    "ReportAudience",
    "ReportBlock",
    "ReportEvidenceRef",
    "ReportBlockKind",
    "ReportBlockRevision",
    "ReportDataBlock",
    "ReportDataBlockKind",
    "ReportGridCellData",
    "ReportAreaAchievementData",
    "ReportMisconceptionEntry",
    "ReportRepresentativeTypeData",
    "ReportMetricInput",
    "ReportNumber",
    "ReportRevisionKind",
    "ReportStudioData",
    "ReportUnproducedMetric",
    "ReportValueStatus",
    "AreaAchievementPayload",
    "DifficultyDistributionPayload",
    "ItemCountsPayload",
    "MisconceptionFrequencyPayload",
    "RepresentativeTypesPayload",
    "WeaknessGridPayload",
]
