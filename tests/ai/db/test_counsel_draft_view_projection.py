"""상담 읽기 모델의 스냅숏 정본과 조회 투영 규약."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest

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
from ai.db.counsel_draft_view import (
    NON_PROJECTED_COLUMNS,
    counsel_draft_view_projection,
)
from ai.db.models import CounselDraftView


def _view_snapshot(*, job_id: str = "job-1") -> dict[str, Any]:
    return {
        "view": CounselDraftJobView(
            job_id=job_id,
            status=JobPhase.RUNNING.value,
        ).model_dump(mode="json"),
        "execution_id": str(uuid.UUID("00000000-0000-0000-0000-000000000101")),
        "correlation_id": str(uuid.UUID("00000000-0000-0000-0000-000000000102")),
    }


def _draft_snapshot() -> dict[str, Any]:
    context = DraftContext(
        student_ref="student-1",
        guardian_ref="guardian-1",
        label_snapshot=LabelSnapshot(
            comm=CommStyle.DATA,
            sensitivity=Sensitivity.ANXIOUS,
            interest=Interest.GRADE,
            frequency=Frequency.MONTHLY,
        ),
        facts=(EvidenceFact(label="최근 정답률", value="82%", record_id="record-1"),),
        evidence_summaries=("최근 학습 기록 요약",),
        period_label="2026년 8월",
        fallback_text="확인 가능한 학습 기록을 안내합니다.",
        inquiry_text="최근 학습 흐름을 알려주세요.",
    )
    citation = Citation(cite_id="cite-1", record_id="record-1", summary="학습 기록")
    return {
        "context": context.model_dump(mode="json"),
        "citations": [citation.model_dump(mode="json")],
        "text": "최근 기록에 근거한 상담 초안입니다.",
        "snapshot_hash": "sha256:read-model-fixture",
        "emphasis": ["최근 정답률"],
    }


def test_projection_is_derived_from_key_and_view_snapshot() -> None:
    derived = counsel_draft_view_projection(
        ("tenant-1", "job-1"),
        view_snapshot=_view_snapshot(),
        draft_snapshot=_draft_snapshot(),
    )

    assert derived == {
        "tenant_id": "tenant-1",
        "job_id": "job-1",
        "status": JobPhase.RUNNING.value,
        "execution_id": uuid.UUID("00000000-0000-0000-0000-000000000101"),
    }


def test_projection_covers_every_snapshot_derived_orm_column() -> None:
    derived = counsel_draft_view_projection(
        ("tenant-1", "job-1"),
        view_snapshot=_view_snapshot(),
        draft_snapshot=None,
    )
    orm_columns = set(CounselDraftView.__table__.columns.keys())

    assert orm_columns
    assert set(derived) == orm_columns - NON_PROJECTED_COLUMNS


@pytest.mark.parametrize(
    ("view_snapshot", "draft_snapshot", "expected_status", "expected_execution_id"),
    [
        (
            _view_snapshot(),
            None,
            JobPhase.RUNNING.value,
            uuid.UUID("00000000-0000-0000-0000-000000000101"),
        ),
        (None, _draft_snapshot(), JobPhase.SUCCEEDED.value, None),
    ],
)
def test_each_snapshot_can_exist_independently(
    view_snapshot: dict[str, Any] | None,
    draft_snapshot: dict[str, Any] | None,
    expected_status: str,
    expected_execution_id: uuid.UUID | None,
) -> None:
    derived = counsel_draft_view_projection(
        ("tenant-1", "job-1"),
        view_snapshot=view_snapshot,
        draft_snapshot=draft_snapshot,
    )

    assert derived["status"] == expected_status
    assert derived["execution_id"] == expected_execution_id
    assert CounselDraftView(
        id=uuid.uuid4(),
        updated_at=datetime.now(UTC),
        view_snapshot=view_snapshot,
        draft_snapshot=draft_snapshot,
        **derived,
    )


def test_mismatched_job_id_is_rejected() -> None:
    with pytest.raises(ValueError, match="job_id"):
        counsel_draft_view_projection(
            ("tenant-1", "job-2"),
            view_snapshot=_view_snapshot(job_id="job-1"),
            draft_snapshot=None,
        )


def test_empty_row_is_rejected() -> None:
    with pytest.raises(ValueError, match="하나는 있어야"):
        counsel_draft_view_projection(
            ("tenant-1", "job-1"),
            view_snapshot=None,
            draft_snapshot=None,
        )
