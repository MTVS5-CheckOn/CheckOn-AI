"""읽기 모델 스냅숏 픽스처 — **세 테스트 파일이 같은 것을 쓴다** (99 #02).

🔴 **손으로 적은 dict를 파일마다 두지 않는다.** 처음에 그렇게 적었다가 `DraftContext`
5필드 불일치로 red를 맞았고, 그 뒤 파일이 셋이 됐다 — **계약이 바뀌면 셋이 따로 낡는다.**
⇒ **계약 모델에서 만들고 한 곳에 둔다.**
"""

from __future__ import annotations

import uuid
from typing import Any

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


def view_snapshot(
    job_id: str,
    *,
    status: JobPhase = JobPhase.SUCCEEDED,
    execution_id: uuid.UUID | None = None,
    correlation_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    """`_CachedView`의 영속 표현 — `execution_id`를 안 주면 매번 새 값이다."""
    return {
        "view": {"job_id": job_id, "status": status.value, "result": None},
        "execution_id": str(execution_id or uuid.uuid4()),
        "correlation_id": str(correlation_id or uuid.uuid4()),
    }


def draft_snapshot(*, text: str = "본문", snapshot_hash: str = "sha256:fixture") -> dict[str, Any]:
    """`_DraftState`의 영속 표현 — 🔴 **계약 모델로 만든다.**"""
    context = DraftContext(
        student_ref="student-fixture",
        guardian_ref="guardian-fixture",
        label_snapshot=LabelSnapshot(
            comm=CommStyle.NARRATIVE,
            sensitivity=Sensitivity.DIRECT,
            interest=Interest.ATTITUDE,
            frequency=Frequency.FREQUENT,
        ),
        facts=(EvidenceFact(label="학습 참여", value="꾸준함", record_id="record-fixture"),),
        evidence_summaries=("수업 참여 기록",),
        period_label="2026년 8월",
        fallback_text="확인 가능한 기록을 안내합니다.",
    )
    return {
        "context": context.model_dump(mode="json"),
        "citations": [
            Citation(
                cite_id="cite-fixture", record_id="record-fixture", summary="수업 참여 기록"
            ).model_dump(mode="json")
        ],
        "text": text,
        "snapshot_hash": snapshot_hash,
        "emphasis": ["학습 참여"],
    }
