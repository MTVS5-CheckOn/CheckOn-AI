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
        "enrolled_seconds": 604800,
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


def test_the_response_contract_only_grew_and_never_shrank() -> None:
    """🔴 응답 구조는 **더해지기만** 한다 — 기존 필드 삭제·개명 0.

    ⚠ **이 검사의 이름과 취지가 2026-08-13에 바뀌었다**(99 #59·#60). 종전 이름은
    `test_the_response_contract_is_untouched`였고 *"응답 구조 변경 0"* 을 못 박았는데,
    그 약속은 **#43(요청에 `detection_evidence` 추가)의 범위**에서 한 것이다 — 그 PR이
    응답을 안 건드린다는 뜻이었지 응답을 영원히 동결한다는 뜻이 아니었다.

    🔴 **그래도 지켜야 하는 것은 남는다** — 백엔드가 이미 읽고 있는 필드는 **사라지지도
    이름이 바뀌지도 않는다.** 그래서 `==`(동결)이 아니라 `<=`(부분집합)로 잠근다:
    새 필드는 통과하고 **삭제·개명은 red**다.
    """
    from ai.contracts.detection import EvidenceItem, Signal  # noqa: PLC0415

    assert set(DetectResponse.model_fields) == {"signals", "stats"}
    #: 🔴 백엔드가 지금 읽고 있는 셋 — `summary`는 deprecated지만 **지우지 않는다**(#59).
    assert {"source_table", "record_id", "summary"} <= set(EvidenceItem.model_fields)
    #: ⚠ 새로 더한 것은 여기 적어 둔다 — 다음 사람이 diff 없이 무엇이 늘었는지 안다.
    assert {"role", "observed", "sample_size", "occurred_on"} <= set(
        EvidenceItem.model_fields
    )
    assert {
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
    } <= set(Signal.model_fields)
    #: 🔴 2026-08-13에 더한 비교값(99 #60 · 안 D) — 기존 필드는 하나도 안 건드렸다.
    assert {"metric", "observed", "baseline", "sample_size"} <= set(Signal.model_fields)
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


@pytest.mark.parametrize("value", [1, 604800])
def test_weekly_activity_accepts_enrolled_seconds_boundaries(value: int) -> None:
    request = DetectRequest.model_validate(
        _base_body(detection_evidence=[_activity(enrolled_seconds=value)])
    )
    activity = request.detection_evidence[0]
    assert isinstance(activity, WeeklyActivityEvidence)
    assert activity.enrolled_seconds == value


@pytest.mark.parametrize("value", [-1, 0, 604801])
def test_weekly_activity_rejects_enrolled_seconds_outside_week(value: int) -> None:
    with pytest.raises(ValidationError):
        DetectRequest.model_validate(
            _base_body(detection_evidence=[_activity(enrolled_seconds=value)])
        )


def test_weekly_activity_requires_enrolled_seconds() -> None:
    field = WeeklyActivityEvidence.model_fields["enrolled_seconds"]
    assert field.is_required()

    missing = _activity()
    del missing["enrolled_seconds"]
    with pytest.raises(ValidationError):
        DetectRequest.model_validate(_base_body(detection_evidence=[missing]))


def test_weekly_activity_still_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        DetectRequest.model_validate(
            _base_body(detection_evidence=[_activity(unexpected_field=1)])
        )


@pytest.mark.parametrize("invalid", [1.5, "604800", True])
def test_weekly_activity_requires_strict_integer_enrolled_seconds(
    invalid: object,
) -> None:
    with pytest.raises(ValidationError):
        DetectRequest.model_validate(
            _base_body(detection_evidence=[_activity(enrolled_seconds=invalid)])
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


#: 🔴 **Java 대조용 정확 벡터**(04 부록 A). 이 값이 바뀌면 **백엔드와 해시가 갈린다** —
#: 계약 변경 없이 고치지 마라. 재산정: `canonical_snapshot_hash(request)`.
_HASH_LEGACY: Final = (
    "sha256:4e90fe4929dced9d3fed2a4c8585766569dd7680a8c50a1be7e1f282c59d8e54"
)
_HASH_AGGREGATE: Final = (
    "sha256:af17b16ba3517d7507c697940b8c43e76acb211c3468243a5c541d4f92c2c179"
)
_HASH_TRANSITION: Final = (
    "sha256:103fd498b6bc7e09f0bc981acf8cde9a839981b81e398761d37af1a5a1ffb732"
)


def _returned_body(rows: list[dict[str, Any]]) -> dict[str, Any]:
    body = _base_body(detection_evidence=rows)
    body["students"][0]["status"] = "returned"
    return body


def test_the_legacy_vector_is_exact() -> None:
    """🔴 기존 필드만 있는 요청 — **접두 확인이 아니라 정확 값**이다."""
    request = DetectRequest.model_validate(_base_body())
    assert canonical_snapshot_hash(request) == _HASH_LEGACY


def test_the_aggregate_vector_is_exact() -> None:
    request = DetectRequest.model_validate(
        _base_body(detection_evidence=[_window(), _activity()])
    )
    assert canonical_snapshot_hash(request) == _HASH_AGGREGATE


def test_the_transition_vector_is_exact() -> None:
    request = DetectRequest.model_validate(_returned_body([_transition()]))
    assert canonical_snapshot_hash(request) == _HASH_TRANSITION


def test_the_canonical_json_text_is_pinned() -> None:
    """🔴 **전문도 고정한다** — 해시만 고정하면 어디가 달라졌는지 Java 쪽에서 못 찾는다."""
    request = DetectRequest.model_validate(
        _base_body(detection_evidence=[_window(), _activity()])
    )
    serialized = json.dumps(
        canonical_snapshot_payload(request),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    assert serialized == (
        '{"alert_context":[],"detection_evidence":['
        '{"at":"2026-08-10","expected_count":3,"kind":"assignment_window",'
        '"record_id":"aws_1","source_table":"assignment_week_summary",'
        '"student_ref":"st_1","submitted_count":0},'
        '{"activity_count":0,"at":"2026-08-10","enrolled_seconds":604800,'
        '"kind":"weekly_activity",'
        '"record_id":"swa_1","source_table":"student_week_activity",'
        '"student_ref":"st_1"}],"learning_events":[],'
        '"snapshot_meta":{"term_context":"normal","week_start":"2026-08-10"},'
        '"students":[{"class_ref":"cl_a1","consent":"granted","enrolled_weeks":10,'
        '"status":"enrolled","student_ref":"st_1"}]}'
    )


def test_an_explicit_empty_array_hashes_like_an_omitted_field() -> None:
    """🔴 **빈 배열과 생략은 같은 의미·같은 해시다**(계약 확정 8/12).

    ⚠ 모델이 둘 다 `()`로 받으므로 *"클라이언트가 빈 배열을 명시했는가"* 를 **복원할 수
    없다.** 복원 못 하는 구분을 해시에 넣으면 **BE와 AI가 서로 다른 값을 낼 수 있다** ⇒
    canonical payload에서 **생략**으로 단순화한다(문서·벡터 동일).
    """
    omitted = DetectRequest.model_validate(_base_body())
    explicit = DetectRequest.model_validate(_base_body(detection_evidence=[]))
    assert canonical_snapshot_hash(explicit) == canonical_snapshot_hash(omitted)
    assert canonical_snapshot_hash(explicit) == _HASH_LEGACY


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


# ───────────────────────── kind ↔ source_table (§4) ─────────────────────────

#: kind별 **정본 테이블 하나** — 자유 문자열이면 거짓 조합이 통과한다.
_CANON_TABLE: Final = {
    "assignment_window": "assignment_week_summary",
    "weekly_activity": "student_week_activity",
    "enrollment_transition": "student_status_history",
}


def _row_of(kind: str, **over: Any) -> dict[str, Any]:  # noqa: ANN401 — 픽스처 오버라이드
    builder = {
        "assignment_window": _window,
        "weekly_activity": _activity,
        "enrollment_transition": _transition,
    }[kind]
    return builder(**over)


@pytest.mark.parametrize("kind", sorted(_CANON_TABLE))
def test_the_matching_kind_and_table_is_accepted(kind: str) -> None:
    rows = [_row_of(kind)]
    body = _returned_body(rows) if kind == "enrollment_transition" else _base_body(
        detection_evidence=rows
    )
    parsed = DetectRequest.model_validate(body).detection_evidence[0]
    assert parsed.source_table == _CANON_TABLE[kind]


@pytest.mark.parametrize(
    ("kind", "wrong_table"),
    [
        (kind, table)
        for kind in sorted(_CANON_TABLE)
        for table in sorted(_CANON_TABLE.values())
        if table != _CANON_TABLE[kind]
    ],
)
def test_a_crossed_kind_and_table_is_rejected(kind: str, wrong_table: str) -> None:
    """🔴 **교차 조합 6종 전부 거부** — `kind=assignment_window` + 상태 이력 테이블 등.

    ⚠ JSON 타입은 **여전히 문자열**이다(BE DTO 무변경) — 허용값만 닫았다.
    """
    rows = [_row_of(kind, source_table=wrong_table)]
    body = _returned_body(rows) if kind == "enrollment_transition" else _base_body(
        detection_evidence=rows
    )
    with pytest.raises(ValidationError):
        DetectRequest.model_validate(body)


def test_the_response_evidence_carries_the_canonical_table() -> None:
    """응답 evidence의 `source_table`이 **입력 kind의 정본 값**과 정확히 일치한다."""
    from ai.detection.engine import detect  # noqa: PLC0415

    body = _returned_body([_transition()])
    response = detect(DetectRequest.model_validate(body))
    tables = {item.source_table for s in response.signals for item in s.evidence}
    assert tables == {_CANON_TABLE["enrollment_transition"]}, tables


def test_the_source_table_is_never_used_as_a_sql_identifier() -> None:
    """🔴 AI가 `source_table`을 **SQL 식별자로 쓰지 않는다** — 값이 쿼리에 안 들어간다.

    ⚠ 문자열 grep이 아니라 **정본 테이블명이 SQL 문면에 등장하는지**를 본다.
    """
    from pathlib import Path  # noqa: PLC0415

    src = Path(__file__).resolve().parents[3] / "src" / "ai"
    offenders: list[str] = []
    for path in sorted(src.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        for table in _CANON_TABLE.values():
            if f"FROM {table}" in text or f"from {table}" in text.lower().replace(
                "_", "_"
            ) and f"from {table}" in text:
                offenders.append(f"{path.name}:{table}")
    assert not offenders, f"정본 테이블명이 SQL에 쓰였다: {offenders}"
