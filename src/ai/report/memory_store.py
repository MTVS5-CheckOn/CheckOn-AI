"""리포트 저장 포트의 tenant 격리 인메모리 구현."""

from __future__ import annotations

import asyncio
from datetime import datetime
from uuid import UUID

from ai.contracts.report import ReportBlock
from ai.report.store import (
    ReportBlockNotFound,
    ReportRevisionConflict,
    StoredReport,
)


class InMemoryReportStore:
    """테스트·초기 HTTP 수직 슬라이스용 전체 리비전 저장소."""

    def __init__(self) -> None:
        self._records: dict[tuple[str, UUID], StoredReport] = {}
        self._lock = asyncio.Lock()

    async def put(self, report: StoredReport) -> None:
        key = (report.tenant_id, report.report_id)
        async with self._lock:
            existing = self._records.get(key)
            if existing is not None and existing != report:
                raise ValueError(f"리포트 불변 스냅숏 충돌: {report.report_id}")
            self._records[key] = report

    async def list(self, *, tenant_id: str) -> tuple[StoredReport, ...]:
        async with self._lock:
            records = [
                report
                for (record_tenant, _report_id), report in self._records.items()
                if record_tenant == tenant_id
            ]
            return tuple(
                sorted(records, key=lambda report: (report.created_at, str(report.report_id)))
            )

    async def get(self, *, tenant_id: str, report_id: UUID) -> StoredReport | None:
        async with self._lock:
            return self._records.get((tenant_id, report_id))

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
        key = (tenant_id, report_id)
        async with self._lock:
            report = self._records.get(key)
            if report is None:
                return None
            block_index = next(
                (index for index, block in enumerate(report.blocks) if block.block_id == block_id),
                None,
            )
            if block_index is None:
                raise ReportBlockNotFound(str(block_id))
            current = report.blocks[block_index]
            if current.active_revision_no != base_revision_no:
                raise ReportRevisionConflict(current_revision_no=current.active_revision_no)
            if updated_block.block_id != block_id:
                raise ValueError("수정 블록 ID가 저장 키와 다르다")
            if (
                updated_block.seq != current.seq
                or updated_block.block_type is not current.block_type
                or updated_block.ai_rewrite_warning != current.ai_rewrite_warning
            ):
                raise ValueError("블록 수정은 본문 리비전 이외의 메타를 바꿀 수 없다")
            if updated_block.active_revision_no != base_revision_no + 1:
                raise ValueError("수정 블록은 리비전을 정확히 하나 늘려야 한다")
            if updated_block.revisions[:-1] != current.revisions:
                raise ValueError("기존 리비전 이력을 바꾸거나 버릴 수 없다")
            if updated_at.tzinfo is None or updated_at < report.updated_at:
                raise ValueError("updated_at은 timezone이 있고 기존 시각보다 이르지 않아야 한다")

            blocks = list(report.blocks)
            blocks[block_index] = updated_block
            updated = report.model_copy(update={"blocks": tuple(blocks), "updated_at": updated_at})
            self._records[key] = updated
            return updated


__all__ = ["InMemoryReportStore"]
