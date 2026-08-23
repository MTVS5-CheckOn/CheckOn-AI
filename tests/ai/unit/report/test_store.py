"""리포트 저장 포트의 tenant·리비전 불변식."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from uuid import UUID

import pytest

from ai.contracts.diagnosis import CellVerdict, MisconceptionReport, WeaknessCell, WeaknessMap
from ai.contracts.problem_generation import DifficultyBand, ItemResult, ProblemItemStatus
from ai.contracts.report import (
    ReportBlock,
    ReportBlockKind,
    ReportBlockRevision,
    ReportEvidenceRef,
    ReportRevisionKind,
)
from ai.report.memory_store import InMemoryReportStore
from ai.report.store import ReportRevisionConflict, ReportSourceSnapshot, ReportStore, StoredReport

REPORT_ID = UUID("00000000-0000-4000-8000-000000000401")
BLOCK_ID = UUID("00000000-0000-4000-8000-000000000402")
NOW = datetime(2026, 8, 24, 9, 0, tzinfo=UTC)


def _report(tenant_id: str = "tenant-a") -> StoredReport:
    evidence = (ReportEvidenceRef(source_table="feature_week", record_id="row-1", summary="근거"),)
    block = ReportBlock(
        block_id=BLOCK_ID,
        seq=0,
        block_type=ReportBlockKind.FACT,
        revisions=(
            ReportBlockRevision(
                revision_no=0,
                revision_kind=ReportRevisionKind.AI_DRAFT,
                ai_original="최초 AI 원문",
                evidence=evidence,
                numbers_used=(),
                gate_passed=True,
            ),
        ),
        active_revision_no=0,
    )
    return StoredReport(
        report_id=REPORT_ID,
        tenant_id=tenant_id,
        guardian_ref="guardian-1",
        source=ReportSourceSnapshot(
            weakness_map=WeaknessMap(
                graph_version="graph-v1",
                taxonomy_version="v1",
                config_version="config-v1",
                snapshot_hash="sha256:report-store",
                cells={
                    "reading×fact": WeaknessCell(
                        acc=0.5, n=4, verdict=CellVerdict.WEAK, severity=0.5
                    )
                },
            ),
            misconceptions=MisconceptionReport(),
            item_results=(
                ItemResult(
                    item_id=UUID("00000000-0000-4000-8000-000000000403"),
                    status=ProblemItemStatus.VERIFIED,
                    attempt_no=1,
                    difficulty_band=DifficultyBand.MEDIUM,
                ),
            ),
            cell_min_items=1,
        ),
        blocks=(block,),
        created_at=NOW,
        updated_at=NOW,
    )


def test_in_memory_store_satisfies_report_store_port() -> None:
    assert isinstance(InMemoryReportStore(), ReportStore)


def test_store_hides_other_tenant_records() -> None:
    async def scenario() -> None:
        store = InMemoryReportStore()
        await store.put(_report())

        assert await store.get(tenant_id="tenant-b", report_id=REPORT_ID) is None
        assert await store.list(tenant_id="tenant-b") == ()

    asyncio.run(scenario())


def test_update_preserves_history_and_rejects_stale_base() -> None:
    async def scenario() -> None:
        store = InMemoryReportStore()
        original = _report()
        await store.put(original)
        edited = original.blocks[0].apply_teacher_edit("강사 수정본", numbers_used=())

        saved = await store.update_block(
            tenant_id="tenant-a",
            report_id=REPORT_ID,
            block_id=BLOCK_ID,
            base_revision_no=0,
            updated_block=edited,
            updated_at=NOW,
        )

        assert saved is not None
        assert saved.blocks[0].revisions[0] == original.blocks[0].revisions[0]
        assert len(saved.blocks[0].revisions) == 2
        with pytest.raises(ReportRevisionConflict) as raised:
            await store.update_block(
                tenant_id="tenant-a",
                report_id=REPORT_ID,
                block_id=BLOCK_ID,
                base_revision_no=0,
                updated_block=edited,
                updated_at=NOW,
            )
        assert raised.value.current_revision_no == 1

    asyncio.run(scenario())
