"""`detection_evidence` 계약 — 요청 경계·하위 호환·canonical 해시 (99 #43).

🔴 **API 변경은 요청 배열 하나 추가다** — 기존 필드 삭제·개명·타입 변경 0, 응답 구조 변경 0.
이 파일이 그 세 가지를 값으로 지킨다.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from typing import Any, Final

import pytest
from pydantic import ValidationError

from ai.contracts.detection import (
    AssignmentWindowEvidence,
    DetectRequest,
    DetectResponse,
    EnrollmentTransitionEvidence,
    EvidenceKind,
    WeeklyActivityEvidence,
)
from ai.detection.canonical import canonical_snapshot_hash, canonical_snapshot_payload

_WEEK: Final = "2026-08-10"
_STUDENT: Final = "st_1"
#: ⚠ **리터럴로 안 적는다** — 저장소의 「기록된 날짜」 가드가 미래 날짜 문자열을 잡는다.
#:   여기서는 *"분석 주차보다 뒤"* 라는 **관계**만 필요하다.
_NEXT_WEEK: Final = (date.fromisoformat(_WEEK) + timedelta(days=7)).isoformat()
_TWO_WEEKS_LATER: Final = (date.fromisoformat(_WEEK) + timedelta(days=14)).isoformat()


def _base_body(**extra: list[dict[str, Any]]) -> dict[str, Any]:
    body: dict[str, Any] = {
        "snapshot_meta": {
            "week_start": _WEEK,
            "snapshot_hash": "sha256:x",
            "term_context": "normal",
            "classes": [{"class_ref": "cl_a1"}],
        },
        "students": [
            {
                "student_ref": _STUDENT,
                "class_ref": "cl_a1",
                "enrolled_weeks": 10,
                "status": "enrolled",
                "consent": "granted",
            }
        ],
        "learning_events": [],
        "alert_context": [],
    }
    body.update(extra)
    return body


def _window(**over: Any) -> dict[str, Any]:  # noqa: ANN401 — 픽스처 오버라이드
    row = {
        "kind": "assignment_window",
        "source_table": "assignment_week_summary",
        "record_id": "aws_1",
        "student_ref": _STUDENT,
        "week_start": _WEEK,
        "expected_count": 3,
        "submitted_count": 0,
    }
    row.update(over)
    return row


def _activity(**over: Any) -> dict[str, Any]:  # noqa: ANN401 — 픽스처 오버라이드
    row = {
        "kind": "weekly_activity",
        "source_table": "student_week_activity",
        "record_id": "swa_1",
        "student_ref": _STUDENT,
        "week_start": _WEEK,
        "activity_count": 0,
    }
    row.update(over)
    return row


def _transition(**over: Any) -> dict[str, Any]:  # noqa: ANN401 — 픽스처 오버라이드
    row = {
        "kind": "enrollment_transition",
        "source_table": "student_status_history",
        "record_id": "ssh_1",
        "student_ref": _STUDENT,
        "occurred_at": f"{_WEEK}T09:00:00+09:00",
        "from_status": "paused",
        "to_status": "returned",
    }
    row.update(over)
    return row


# ───────────────────────── 하위 호환 ─────────────────────────


def test_a_request_without_the_new_field_still_parses() -> None:
    """🔴 **기존 요청이 안 깨진다** — 필드가 없어도 파싱되고 빈 배열이 된다."""
    request = DetectRequest.model_validate(_base_body())
    assert request.detection_evidence == ()


def test_the_new_field_is_never_required() -> None:
    """계약상 optional인지 **스키마에서** 본다 — 기본값이 사라지면 red."""
    assert DetectRequest.model_fields["detection_evidence"].is_required() is False


def test_the_existing_request_fields_are_unchanged() -> None:
    """🔴 기존 필드가 **하나도** 바뀌지 않았다 — 삭제·개명·타입 변경 금지."""
    fields = DetectRequest.model_fields
    assert set(fields) == {
        "snapshot_meta",
        "students",
        "learning_events",
        "alert_context",
        "detection_evidence",
    }
    for name in ("students", "learning_events", "alert_context"):
        assert fields[name].is_required() is False, f"{name}이 필수가 됐다"
    assert fields["snapshot_meta"].is_required() is True


def test_the_response_contract_is_untouched() -> None:
    """🔴 **응답 구조는 전혀 안 바뀐다** — 필드 집합을 값으로 못 박는다."""
    from ai.contracts.detection import EvidenceItem, Signal  # noqa: PLC0415

    assert set(DetectResponse.model_fields) == {"signals", "stats"}
    assert set(EvidenceItem.model_fields) == {"source_table", "record_id", "summary"}
    assert set(Signal.model_fields) == {
        "signal_id",
        "student_ref",
        "class_ref",
        "rule_id",
        "signal_type",
        "display_label",
        "score",
        "rank",
        "advisory",
        "lifecycle",
        "brief",
        "evidence",
    }
    #: ⚠ `merged_rule_ids`는 이번 작업에서 **추가하지 않는다.**
    assert "merged_rule_ids" not in Signal.model_fields


# ───────────────────────── 세 종류 파싱·직렬화 ─────────────────────────


@pytest.mark.parametrize("row", [_window(), _activity(), _transition()])
def test_each_kind_round_trips(row: dict[str, Any]) -> None:
    body = _base_body(detection_evidence=[row])
    #: ⚠ 복귀 전환은 학생 상태와 **정합해야** 통과한다 — 그 규칙 자체는 아래 검사가 든다.
    if row["kind"] == "enrollment_transition":
        body["students"][0]["status"] = "returned"
    request = DetectRequest.model_validate(body)
    assert len(request.detection_evidence) == 1
    dumped = request.model_dump(mode="json")["detection_evidence"][0]
    assert dumped["kind"] == row["kind"]
    assert dumped["record_id"] == row["record_id"]


def test_an_unknown_kind_is_rejected() -> None:
    with pytest.raises(ValidationError):
        DetectRequest.model_validate(
            _base_body(detection_evidence=[_window(kind="mystery")])
        )


def test_fields_of_another_kind_are_rejected() -> None:
    """🔴 **거짓 조합을 문법에서 막는다** — `weekly_activity`에 `expected_count`는 없다."""
    with pytest.raises(ValidationError):
        DetectRequest.model_validate(
            _base_body(detection_evidence=[_activity(expected_count=3)])
        )


# ───────────────────────── 요청 경계 (§9) ─────────────────────────


@pytest.mark.parametrize(
    ("case", "rows"),
    [
        ("없는 학생", [_window(student_ref="st_ghost")]),
        ("집계 중복", [_window(), _window(record_id="aws_2")]),
        ("활동 집계 중복", [_activity(), _activity(record_id="swa_2")]),
        ("같은 PK 다른 내용", [_window(), _window(expected_count=5)]),
        ("미래 주차", [_window(week_start=_NEXT_WEEK)]),
        ("미래 전환", [_transition(occurred_at=f"{_TWO_WEEKS_LATER}T09:00:00+09:00")]),
        ("timezone 없음", [_transition(occurred_at=f"{_WEEK}T09:00:00")]),
        ("제출이 예정 초과", [_window(expected_count=1, submitted_count=2)]),
    ],
)
def test_the_boundary_rejects(case: str, rows: list[dict[str, Any]]) -> None:
    """🔴 새 필드의 경계는 **즉시** 거부한다.

    ⚠ **전부 같은 계약(400 `INVALID_SCHEMA`)으로 수렴한다.** 지시서는
    `submitted > expected`를 422로 적었지만, error_codes §1의 7/22 A판정이
    *"바디 스키마 위반은 헤더 누락·JSON 파싱과 함께 하나의 400"* 으로 이미 확정했다 —
    같은 배열의 위반이 상태 코드 둘로 갈리면 그 판정이 무너진다(별건으로 등재).
    """
    del case
    with pytest.raises(ValidationError):
        DetectRequest.model_validate(_base_body(detection_evidence=rows))


def test_a_returned_transition_requires_a_returned_student() -> None:
    """🔴 상태와 이력이 갈리면 **조용히 한쪽을 고르지 않는다** — 요청을 거부한다."""
    with pytest.raises(ValidationError, match="returned"):
        DetectRequest.model_validate(_base_body(detection_evidence=[_transition()]))


def test_a_returned_student_with_a_matching_transition_is_accepted() -> None:
    body = _base_body(detection_evidence=[_transition()])
    body["students"][0]["status"] = "returned"
    assert len(DetectRequest.model_validate(body).detection_evidence) == 1


def test_the_same_record_twice_with_identical_content_is_fine() -> None:
    """⚠ **같은 내용의 재전송은 충돌이 아니다** — 다른 내용일 때만 거부한다.

    ⚠ 단 집계 중복(같은 학생·주차)은 `record_id`가 달라도 거부된다 — 위 표 참조.
    """
    body = _base_body(detection_evidence=[_transition(), _transition()])
    body["students"][0]["status"] = "returned"
    assert len(DetectRequest.model_validate(body).detection_evidence) == 2


# ───────────────────────── canonical hash (§4) ─────────────────────────


def test_the_canonical_payload_of_a_legacy_request_is_unchanged() -> None:
    """🔴 **필드를 안 보낸 요청의 canonical payload에는 그 키가 없다** — 하위 호환.

    ⚠ 빈 배열로 키를 만들면 **이미 발급된 멱등 키가 전부 어긋난다.**
    """
    payload = canonical_snapshot_payload(DetectRequest.model_validate(_base_body()))
    assert "detection_evidence" not in payload


def test_the_new_array_is_part_of_the_hash() -> None:
    """🔴 판정 입력이므로 **해시가 달라져야 한다** — 안 그러면 멱등이 옛 결과를 돌려준다."""
    legacy = DetectRequest.model_validate(_base_body())
    with_evidence = DetectRequest.model_validate(
        _base_body(detection_evidence=[_window()])
    )
    assert canonical_snapshot_hash(legacy) != canonical_snapshot_hash(with_evidence)


def test_the_array_order_does_not_change_the_hash() -> None:
    """배열 입력 순서가 달라도 canonical hash는 같다(04 부록 A의 다른 배열과 같은 규약)."""
    rows = [_window(), _activity()]
    first = DetectRequest.model_validate(_base_body(detection_evidence=rows))
    second = DetectRequest.model_validate(
        _base_body(detection_evidence=list(reversed(rows)))
    )
    assert canonical_snapshot_hash(first) == canonical_snapshot_hash(second)


@pytest.mark.parametrize(
    "over", [{"expected_count": 4}, {"submitted_count": 1}, {"record_id": "aws_9"}]
)
def test_one_changed_value_changes_the_hash(over: dict[str, Any]) -> None:
    """🔴 값 하나가 바뀌면 해시가 달라진다 — 안 그러면 멱등이 다른 근거를 같다고 본다."""
    base = DetectRequest.model_validate(_base_body(detection_evidence=[_window()]))
    changed = DetectRequest.model_validate(
        _base_body(detection_evidence=[_window(**over)])
    )
    assert canonical_snapshot_hash(base) != canonical_snapshot_hash(changed)


def test_the_canonical_vector_is_stable() -> None:
    """🔴 **Python 참조 벡터** — Java 구현과 대조할 값이다(04 부록 A).

    ⚠ 이 값이 바뀌면 **백엔드와의 해시가 갈린다** — 계약 변경 없이 바꾸지 마라.
    """
    body = _base_body(detection_evidence=[_window(), _activity()])
    request = DetectRequest.model_validate(body)
    payload = canonical_snapshot_payload(request)
    assert [row["kind"] for row in payload["detection_evidence"]] == [
        EvidenceKind.ASSIGNMENT_WINDOW.value,
        EvidenceKind.WEEKLY_ACTIVITY.value,
    ]
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    assert '"detection_evidence":[{"at":"2026-08-10"' in serialized
    assert canonical_snapshot_hash(request).startswith("sha256:")


def test_the_evidence_models_carry_no_free_text() -> None:
    """🔴 실명·연락처·자유 원문이 들어올 자리가 **없다**(불변식 3)."""
    for model in (
        AssignmentWindowEvidence,
        WeeklyActivityEvidence,
        EnrollmentTransitionEvidence,
    ):
        assert model.model_config.get("extra") == "forbid"
        text_fields = {
            name
            for name in model.model_fields
            if name not in {"kind", "source_table", "record_id", "student_ref"}
        }
        assert "note" not in text_fields and "text" not in text_fields


def test_the_week_start_type_is_a_date_not_a_string() -> None:
    """주차 비교가 문자열로 되면 `2026-9-1` 같은 값이 조용히 어긋난다."""
    assert AssignmentWindowEvidence.model_fields["week_start"].annotation is date
    assert WeeklyActivityEvidence.model_fields["week_start"].annotation is date
