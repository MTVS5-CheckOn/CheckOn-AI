"""`snapshot_hash` canonical payload — **AI 쪽 참조 구현** (04 부록 A · 99 #43).

🔴 **해시를 만드는 주체는 백엔드다.** 이 모듈은 *"무엇을 해시 대상에 넣는가"* 를 코드로
못 박아 **Java 구현과 대조할 수 있게** 하는 참조다 — AI가 요청 해시를 재계산해서 검증하지
않는다(내부 백엔드 전용 신뢰 · 04 §2.2).

⚠ **`detection_evidence`는 판정 입력이므로 대상에 들어간다**(99 #43). 안 넣으면 **같은
멱등키에 근거만 다른 요청**이 같은 해시를 받아 **이전 결과가 그대로 재반환**된다 —
근거가 바뀌었는데 판정이 안 바뀐다.

⚠ **필드를 안 보낸 기존 요청의 canonical payload는 종전과 바이트 동일**하다 — 빈 배열은
키 자체를 만들지 않는다(하위 호환).
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Final

from ai.contracts.detection import (
    AssignmentWindowEvidence,
    DetectionEvidence,
    DetectRequest,
    EnrollmentTransitionEvidence,
    WeeklyActivityEvidence,
)

#: 정렬 키 — `(kind, student_ref, 기준 시각 또는 week_start, source_table, record_id)`.
#: 🔴 **배열 입력 순서가 달라도 같은 해시**여야 한다(04 부록 A의 다른 배열과 같은 규약).
_SORT_FIELDS: Final = ("kind", "student_ref", "at", "source_table", "record_id")


def _evidence_at(item: DetectionEvidence) -> str:
    """정렬·해시에 쓰는 기준 시각 — 집계는 `week_start`, 전환은 `occurred_at`."""
    if isinstance(item, EnrollmentTransitionEvidence):
        return item.occurred_at.isoformat()
    return item.week_start.isoformat()


def _evidence_row(item: DetectionEvidence) -> dict[str, Any]:
    """한 근거의 canonical dict — 🔴 **kind별 필드만** 담는다(거짓 조합 방지)."""
    row: dict[str, Any] = {
        "kind": item.kind.value,
        "student_ref": item.student_ref,
        "at": _evidence_at(item),
        "source_table": item.source_table,
        "record_id": item.record_id,
    }
    if isinstance(item, AssignmentWindowEvidence):
        row["expected_count"] = item.expected_count
        row["submitted_count"] = item.submitted_count
    elif isinstance(item, WeeklyActivityEvidence):
        row["activity_count"] = item.activity_count
    else:
        row["from_status"] = item.from_status.value
        row["to_status"] = item.to_status.value
    return row


def canonical_snapshot_payload(request: DetectRequest) -> dict[str, Any]:
    """04 부록 A의 해시 입력 — 정렬된 판정 입력만 담는다.

    ⚠ `snapshot_hash` 자체는 대상이 아니다(자기 참조). `classes`도 아니다 —
    부록 A가 `week_start`·`term_context`만 든다.
    """
    payload: dict[str, Any] = {
        "snapshot_meta": {
            "week_start": request.snapshot_meta.week_start,
            "term_context": request.snapshot_meta.term_context.value,
        },
        "students": [
            student.model_dump(mode="json")
            for student in sorted(request.students, key=lambda s: s.student_ref)
        ],
        "learning_events": [
            event.model_dump(mode="json")
            for event in sorted(request.learning_events, key=lambda e: e.record_id)
        ],
        "alert_context": [
            item.model_dump(mode="json")
            for item in sorted(
                request.alert_context, key=lambda a: (a.student_ref, a.signal_type.value)
            )
        ],
    }
    if request.detection_evidence:
        #: ⚠ **빈 배열이면 키를 안 만든다** — 기존 요청의 canonical payload가 바뀌면
        #:   이미 발급된 멱등 키가 전부 어긋난다(하위 호환).
        rows = [_evidence_row(item) for item in request.detection_evidence]
        payload["detection_evidence"] = sorted(
            rows, key=lambda row: tuple(str(row[field]) for field in _SORT_FIELDS)
        )
    return payload


def canonical_snapshot_hash(request: DetectRequest) -> str:
    """참조 해시 — `sha256:` 접두 + canonical JSON(키 정렬 · 공백 제거 · UTF-8)."""
    body = json.dumps(
        canonical_snapshot_payload(request),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return "sha256:" + hashlib.sha256(body.encode("utf-8")).hexdigest()
