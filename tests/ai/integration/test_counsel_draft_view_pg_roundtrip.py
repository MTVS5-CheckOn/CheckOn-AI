"""`COUNSEL_DRAFT_VIEW` 두 JSONB 정본의 실제 PostgreSQL 무손실 왕복."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any, Final

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from ai.contracts.agents import JobPhase
from ai.contracts.composition import (
    CommStyle,
    DraftContext,
    EvidenceFact,
    Frequency,
    Interest,
    LabelSnapshot,
    Sensitivity,
)
from ai.contracts.counsel import Citation, CounselDraftJobView
from ai.db.counsel_draft_view import counsel_draft_view_projection
from ai.db.models import CounselDraftView

pytestmark = pytest.mark.integration

_NOW: Final = datetime(2026, 8, 10, 9, 30, tzinfo=UTC)
_Scenario = Callable[[async_sessionmaker[AsyncSession]], Awaitable[None]]


async def _with_pg(scenario: _Scenario) -> str:
    from ai.db.models import Base
    from ai.db.settings import get_db_settings

    engine = create_async_engine(get_db_settings().database_url, poolclass=NullPool)
    try:
        try:
            async with engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
        except Exception:  # noqa: BLE001 — 접속 불가 → skip
            return "skip"
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        await scenario(async_sessionmaker(engine, expire_on_commit=False))
    finally:
        await engine.dispose()
    return "ok"


def _run(scenario: _Scenario) -> None:
    if asyncio.run(_with_pg(scenario)) == "skip":
        pytest.skip("실 PG 미가용 — docker compose -f compose.dev.yml up")


def _snapshots(*, job_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    view_snapshot = {
        "view": CounselDraftJobView(
            job_id=job_id,
            status=JobPhase.SUCCEEDED.value,
        ).model_dump(mode="json"),
        "execution_id": str(uuid.UUID("00000000-0000-0000-0000-000000000201")),
        "correlation_id": str(uuid.UUID("00000000-0000-0000-0000-000000000202")),
    }
    context = DraftContext(
        student_ref="student-pg",
        guardian_ref="guardian-pg",
        label_snapshot=LabelSnapshot(
            comm=CommStyle.NARRATIVE,
            sensitivity=Sensitivity.DIRECT,
            interest=Interest.ATTITUDE,
            frequency=Frequency.FREQUENT,
        ),
        facts=(EvidenceFact(label="학습 참여", value="꾸준함", record_id="record-pg"),),
        evidence_summaries=("수업 참여 기록",),
        period_label="2026년 8월",
        fallback_text="확인 가능한 기록을 안내합니다.",
    )
    draft_snapshot = {
        "context": context.model_dump(mode="json"),
        "citations": [
            Citation(
                cite_id="cite-pg",
                record_id="record-pg",
                summary="수업 참여 기록",
            ).model_dump(mode="json")
        ],
        "text": "수업 참여 기록을 바탕으로 작성한 초안입니다.",
        "snapshot_hash": "sha256:pg-roundtrip",
        "emphasis": ["학습 참여"],
    }
    return view_snapshot, draft_snapshot


async def _insert_and_read(
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    tenant_id: str,
    job_id: str,
    view_snapshot: dict[str, Any] | None,
    draft_snapshot: dict[str, Any] | None,
) -> CounselDraftView:
    derived = counsel_draft_view_projection(
        (tenant_id, job_id),
        view_snapshot=view_snapshot,
        draft_snapshot=draft_snapshot,
    )
    row_id = uuid.uuid4()
    async with sessionmaker() as session:
        session.add(
            CounselDraftView(
                id=row_id,
                updated_at=_NOW,
                view_snapshot=view_snapshot,
                draft_snapshot=draft_snapshot,
                **derived,
            )
        )
        await session.commit()
    async with sessionmaker() as session:
        return (
            await session.execute(
                select(CounselDraftView).where(CounselDraftView.id == row_id)
            )
        ).scalar_one()


def test_both_snapshots_round_trip_losslessly_and_match_projections() -> None:
    job_id = f"job-both-{uuid.uuid4()}"
    view_snapshot, draft_snapshot = _snapshots(job_id=job_id)

    def scenario(sessionmaker: async_sessionmaker[AsyncSession]) -> Awaitable[None]:
        async def inner() -> None:
            row = await _insert_and_read(
                sessionmaker,
                tenant_id="tenant-pg",
                job_id=job_id,
                view_snapshot=view_snapshot,
                draft_snapshot=draft_snapshot,
            )
            assert row.view_snapshot == view_snapshot
            assert row.draft_snapshot == draft_snapshot
            derived = counsel_draft_view_projection(
                (row.tenant_id, row.job_id),
                view_snapshot=row.view_snapshot,
                draft_snapshot=row.draft_snapshot,
            )
            for name, expected in derived.items():
                assert getattr(row, name) == expected

        return inner()

    _run(scenario)


@pytest.mark.parametrize("present", ["view", "draft"])
def test_each_nullable_snapshot_inserts_independently(present: str) -> None:
    job_id = f"job-{present}-{uuid.uuid4()}"
    view_snapshot, draft_snapshot = _snapshots(job_id=job_id)

    def scenario(sessionmaker: async_sessionmaker[AsyncSession]) -> Awaitable[None]:
        async def inner() -> None:
            row = await _insert_and_read(
                sessionmaker,
                tenant_id="tenant-pg",
                job_id=job_id,
                view_snapshot=view_snapshot if present == "view" else None,
                draft_snapshot=draft_snapshot if present == "draft" else None,
            )
            assert (row.view_snapshot is not None) is (present == "view")
            assert (row.draft_snapshot is not None) is (present == "draft")

        return inner()

    _run(scenario)
