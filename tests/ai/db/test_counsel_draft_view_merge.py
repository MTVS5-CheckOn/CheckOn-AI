"""읽기 모델 저장의 **병합 규약** — 한쪽 저장이 반대쪽 스냅숏을 지우지 않는가 (99 ㉿ · #182 후속).

🔴 **이 읽기 모델은 INSERT-only가 아니다.** 같은 `(tenant_id, job_id)`가 **갱신**된다 —
`_view_cache`는 POST와 GET이, `_drafts`는 생성 turn이 각각 **다른 시점에** 쓴다.
그래서 `PgPackResultStore`의 충돌 규약(**있으면 실패**)을 복사하면 안 된다.

⚠ **행 전체 교체가 가장 쉬운 오답이다** — draft를 저장하면서 `view_snapshot`을 안 실으면
**직전에 저장된 뷰가 null로 덮인다.** 그러면 GET은 404가 되고, 증상은 축출과 구별되지 않는다
(㉿ ⓐ와 같은 얼굴인데 원인이 다르다).

🔴 **파생 넷은 병합 **뒤**의 두 스냅숏에서 유도해야 한다.** draft만 갱신할 때 파생을
draft 쪽에서 뽑으면 **살아 있는 뷰의 `status`·`execution_id`가 지워진다** —
draft-only 판정(`succeeded` · `execution_id=None`)은 **뷰가 없을 때만** 정직하다.
"""

from __future__ import annotations

import uuid
from typing import Any, Final

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
from ai.contracts.counsel import Citation
from ai.db.counsel_read_model import merge_snapshots

_KEY: Final = ("t_merge", "job-merge-1")
_EXECUTION: Final = uuid.UUID("00000000-0000-0000-0000-0000000003a1")


def _view_snapshot(*, status: JobPhase = JobPhase.SUCCEEDED) -> dict[str, Any]:
    return {
        "view": {"job_id": _KEY[1], "status": status.value, "result": None},
        "execution_id": str(_EXECUTION),
        "correlation_id": str(uuid.UUID("00000000-0000-0000-0000-0000000003b2")),
    }


def _draft_snapshot(*, text: str = "본문") -> dict[str, Any]:
    """🔴 **계약 모델로 만든다** — 손으로 적은 dict는 계약이 바뀌면 조용히 낡는다."""
    context = DraftContext(
        student_ref="student-merge",
        guardian_ref="guardian-merge",
        label_snapshot=LabelSnapshot(
            comm=CommStyle.NARRATIVE,
            sensitivity=Sensitivity.DIRECT,
            interest=Interest.ATTITUDE,
            frequency=Frequency.FREQUENT,
        ),
        facts=(EvidenceFact(label="학습 참여", value="꾸준함", record_id="record-merge"),),
        evidence_summaries=("수업 참여 기록",),
        period_label="2026년 8월",
        fallback_text="확인 가능한 기록을 안내합니다.",
    )
    return {
        "context": context.model_dump(mode="json"),
        "citations": [
            Citation(
                cite_id="cite-merge", record_id="record-merge", summary="수업 참여 기록"
            ).model_dump(mode="json")
        ],
        "text": text,
        "snapshot_hash": "sha256:merge",
        "emphasis": ["학습 참여"],
    }


def test_saving_a_draft_keeps_the_existing_view() -> None:
    """🔴 **핵심 조건** — draft만 갱신해도 최종 행에 뷰가 남아야 한다."""
    row = merge_snapshots(
        _KEY,
        existing_view=_view_snapshot(),
        existing_draft=None,
        new_draft=_draft_snapshot(),
    )
    assert row["view_snapshot"] == _view_snapshot(), "draft 저장이 뷰를 지웠다"
    assert row["draft_snapshot"] == _draft_snapshot()


def test_saving_a_view_keeps_the_existing_draft() -> None:
    """반대 순서 — view만 갱신해도 초안이 남아야 한다."""
    row = merge_snapshots(
        _KEY,
        existing_view=None,
        existing_draft=_draft_snapshot(),
        new_view=_view_snapshot(),
    )
    assert row["draft_snapshot"] == _draft_snapshot(), "뷰 저장이 초안을 지웠다"
    assert row["view_snapshot"] == _view_snapshot()


def test_the_projection_follows_the_surviving_view() -> None:
    """🔴 **draft만 갱신할 때 파생을 draft에서 뽑으면 안 된다.**

    뷰가 `running`인데 draft 저장이 파생을 `succeeded`로 덮으면 **GET이 거짓 상태를 준다.**
    """
    row = merge_snapshots(
        _KEY,
        existing_view=_view_snapshot(status=JobPhase.RUNNING),
        existing_draft=None,
        new_draft=_draft_snapshot(),
    )
    assert row["status"] == JobPhase.RUNNING.value, (
        f"살아 있는 뷰의 상태가 draft 저장에 덮였다: {row['status']}"
    )
    assert row["execution_id"] == _EXECUTION, "살아 있는 뷰의 실행 키가 지워졌다"


def test_a_draft_only_row_is_succeeded_with_no_execution() -> None:
    """뷰가 **없을 때만** draft-only 판정이 정직하다(#182 A 승인 근거)."""
    row = merge_snapshots(
        _KEY, existing_view=None, existing_draft=None, new_draft=_draft_snapshot()
    )
    assert row["status"] == JobPhase.SUCCEEDED.value
    assert row["execution_id"] is None


def test_resaving_the_same_snapshot_is_idempotent() -> None:
    once = merge_snapshots(
        _KEY, existing_view=None, existing_draft=None, new_view=_view_snapshot()
    )
    twice = merge_snapshots(
        _KEY,
        existing_view=once["view_snapshot"],
        existing_draft=once["draft_snapshot"],
        new_view=_view_snapshot(),
    )
    assert once == twice


def test_writing_nothing_is_refused() -> None:
    """🔴 **둘 다 없는 저장 요청은 실패한다** — DB엔 CHECK 제약이 없다(실측 8/10).

    ⚠ #182 리뷰에서 확인했다: 두 스냅숏이 모두 null인 행이 **PG에 그냥 들어간다.**
    막는 자리는 여기뿐이므로 **저장소가 이 함수를 유일한 입구로** 써야 한다.
    """
    with pytest.raises(ValueError):
        merge_snapshots(_KEY, existing_view=None, existing_draft=None)


def test_a_snapshot_for_another_job_is_refused() -> None:
    """캐시 키와 `view_snapshot.job_id`가 다르면 실패 — 남의 잡을 이 행에 못 넣는다."""
    with pytest.raises(ValueError):
        merge_snapshots(
            ("t_merge", "other-job"),
            existing_view=None,
            existing_draft=None,
            new_view=_view_snapshot(),
        )
