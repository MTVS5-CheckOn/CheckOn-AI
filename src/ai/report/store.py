"""리포트 조회·리비전 저장 포트와 저장 레코드."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, Self, runtime_checkable
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ai.contracts.diagnosis import MisconceptionReport, WeaknessMap
from ai.contracts.problem_generation import ItemResult
from ai.contracts.report import ReportBlock, ReportMetricInput


class ReportSourceSnapshot(BaseModel):
    """guardian 데이터 조립에 필요한 불변 입력 스냅숏."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    weakness_map: WeaknessMap
    misconceptions: MisconceptionReport
    item_results: tuple[ItemResult, ...] = Field(min_length=1)
    metrics: tuple[ReportMetricInput, ...] = ()
    cell_min_items: int = Field(ge=1)


class StoredReport(BaseModel):
    """인메모리와 후속 영속 저장소가 공유하는 리포트 전체 스냅숏."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    report_id: UUID
    tenant_id: str = Field(min_length=1)
    guardian_ref: str = Field(min_length=1)
    source: ReportSourceSnapshot
    blocks: tuple[ReportBlock, ...] = Field(min_length=1)
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="after")
    def validate_report(self) -> Self:
        if self.created_at.tzinfo is None or self.updated_at.tzinfo is None:
            raise ValueError("리포트 시각에는 timezone이 필요하다")
        if self.updated_at < self.created_at:
            raise ValueError("updated_at은 created_at보다 이를 수 없다")
        sequences = tuple(block.seq for block in self.blocks)
        if sequences != tuple(sorted(sequences)) or len(sequences) != len(set(sequences)):
            raise ValueError("리포트 블록 seq는 중복 없이 오름차순이어야 한다")
        return self


class ReportRevisionConflict(RuntimeError):
    """리포트 블록의 낙관적 잠금 충돌."""

    def __init__(self, *, current_revision_no: int) -> None:
        super().__init__("stale_base_revision")
        self.current_revision_no = current_revision_no


class ReportBlockNotFound(LookupError):
    """테넌트 범위의 리포트에 요청한 블록이 없음."""


@runtime_checkable
class ReportStore(Protocol):
    """tenant 격리와 전체 리비전 이력을 보존하는 저장 경계."""

    async def put(self, report: StoredReport) -> None:
        """내부 생성 경로가 만든 최초 리포트를 멱등 저장한다."""
        ...

    async def list(self, *, tenant_id: str) -> tuple[StoredReport, ...]:
        """한 tenant의 리포트만 생성 시각·ID 순으로 읽는다."""
        ...

    async def get(self, *, tenant_id: str, report_id: UUID) -> StoredReport | None:
        """다른 tenant의 같은 ID는 부재로 취급한다."""
        ...

    async def update_block(
        self,
        *,
        tenant_id: str,
        report_id: UUID,
        block_id: UUID,
        base_revision_no: int,
        updated_block: ReportBlock,
        updated_at: datetime,
    ) -> StoredReport | None:
        """현재 리비전에서 정확히 한 rev 늘어난 블록으로 교체한다."""
        ...


__all__ = [
    "ReportBlockNotFound",
    "ReportRevisionConflict",
    "ReportSourceSnapshot",
    "ReportStore",
    "StoredReport",
]
