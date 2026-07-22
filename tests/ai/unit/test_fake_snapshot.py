"""evaluation/fake_snapshot.py 검증 — 결정론 · 계약 통과 · 무결성 · 픽스처 의도.

빌더는 평가·테스트 전용이며, 여기서 그 산출물이 DetectRequest 계약을 통과하고
결정론적임을 고정한다 (사양: docs/part_a/08_evaluation_plan.md §2).
"""

from datetime import datetime

import pytest

from ai.contracts.detection import DetectRequest, SignalType, StudentStatus, TermContext
from ai.evaluation.fake_snapshot import (
    StudentPlan,
    build_detect_request,
    declining,
    fixture_composite_risk,
    fixture_stable,
    fixture_with_history,
    intermittent,
    spike,
    to_payload,
)


def test_same_seed_same_snapshot() -> None:
    """같은 seed·week_start → 바이트 동일 스냅숏 (결정론)."""
    a = fixture_composite_risk(week_start="2026-07-13", seed=7)
    b = fixture_composite_risk(week_start="2026-07-13", seed=7)
    assert a == b
    assert to_payload(a) == to_payload(b)


def test_different_seed_differs() -> None:
    """seed가 다르면 산출물이 달라진다(난수가 실제로 관여)."""
    a = fixture_composite_risk(seed=1)
    b = fixture_composite_risk(seed=2)
    assert a != b


def test_output_passes_contract() -> None:
    """빌더 산출물은 DetectRequest 계약을 통과한다 — 재검증도 동일 인스턴스."""
    req = fixture_stable()
    assert isinstance(req, DetectRequest)
    assert DetectRequest.model_validate(to_payload(req)) == req


def test_extra_field_rejected() -> None:
    """계약이 곧 스키마 — snapshot_meta에 계약 밖 필드가 들어오면 거부된다."""
    payload = to_payload(fixture_stable())
    payload["snapshot_meta"]["injected"] = "x"
    with pytest.raises(ValueError, match="injected"):
        DetectRequest.model_validate(payload)


def test_record_id_unique() -> None:
    """record_id는 스냅숏 내 유일 (근거 역추적 키 — 09 §2)."""
    req = fixture_composite_risk()
    ids = [event.record_id for event in req.learning_events]
    assert len(ids) == len(set(ids))


def test_student_ref_referential_integrity() -> None:
    """learning_events·alert_context의 student_ref가 students에 전부 실존."""
    req = fixture_with_history()
    known = {student.student_ref for student in req.students}
    assert all(event.student_ref in known for event in req.learning_events)
    assert all(item.student_ref in known for item in req.alert_context)


def test_no_real_name_fields() -> None:
    """불변식 3 — 페이로드 어디에도 실명·연락처류 키가 없다. 식별자는 alias 형태."""
    payload = to_payload(fixture_with_history())
    flat = repr(payload)
    for banned in ("name", "phone", "email", "실명", "연락처"):
        assert banned not in flat
    for student in payload["students"]:
        assert student["student_ref"].startswith("st_")
        assert student["class_ref"].startswith("cl_")
    for event in payload["learning_events"]:
        assert event["record_id"].startswith("le_")


def test_json_payload_shape() -> None:
    """05 §1 형태 — snake_case 키 · occurred_at은 ISO-8601 +09:00 (KST)."""
    payload = to_payload(fixture_stable())
    assert set(payload) == {"snapshot_meta", "students", "learning_events", "alert_context"}
    solve = next(e for e in payload["learning_events"] if e["type"] == "solve")
    assert solve["occurred_at"].endswith("+09:00")
    # 파싱 가능한 tz-aware ISO여야 한다
    assert datetime.fromisoformat(solve["occurred_at"]).utcoffset() is not None
    assert solve["item_format"] == "mcq"


def test_newcomer_fixture_under_2_weeks() -> None:
    """신규생(enrolled_weeks<2) 픽스처가 의도한 값으로 생성된다."""
    req = build_detect_request(
        week_start="2026-07-13",
        seed=1,
        students=[StudentPlan(student_ref="st_new_1", class_ref="cl_a1", enrolled_weeks=1)],
    )
    assert req.students[0].enrolled_weeks == 1


def test_no_consent_and_paused_have_no_events() -> None:
    """무동의·휴원 학생은 목록엔 있으나 learning_events를 만들지 않는다."""
    req = build_detect_request(
        week_start="2026-07-13",
        seed=1,
        students=[
            StudentPlan(student_ref="st_noconsent", class_ref="cl_a1", consent="revoked"),
            StudentPlan(student_ref="st_paused", class_ref="cl_a1", status=StudentStatus.PAUSED),
        ],
    )
    refs = {s.student_ref for s in req.students}
    assert refs == {"st_noconsent", "st_paused"}
    assert req.learning_events == ()


def test_returned_status_preserved() -> None:
    """복귀(returned) 상태가 그대로 실린다 — R5 복귀 케어 재료."""
    req = build_detect_request(
        week_start="2026-07-13",
        seed=1,
        students=[
            StudentPlan(student_ref="st_ret", class_ref="cl_a1", status=StudentStatus.RETURNED),
        ],
    )
    assert req.students[0].status is StudentStatus.RETURNED


def test_term_context_carried() -> None:
    req = build_detect_request(
        week_start="2026-07-13",
        seed=1,
        students=[StudentPlan(student_ref="st_1", class_ref="cl_a1")],
        term_context=TermContext.VACATION,
    )
    assert req.snapshot_meta.term_context is TermContext.VACATION


def test_history_fixture_lifecycle_material() -> None:
    """이력 픽스처가 ongoing(open)·follow_up(resolved+2주내) 재료를 담는다 (09 §4)."""
    req = fixture_with_history()
    by_student = {item.student_ref: item for item in req.alert_context}
    assert by_student["st_ongoing"].status.value == "open"
    followup = by_student["st_followup"]
    assert followup.status.value == "resolved"
    assert followup.resolved_at is not None
    assert followup.followed_up is False
    assert followup.signal_type is SignalType.ACC_DROP


def test_trajectory_helpers() -> None:
    """궤적 헬퍼가 길이 weeks·의도한 방향을 만든다."""
    assert len(declining(10, 0.8, 0.6)) == 10
    assert declining(10, 0.8, 0.6)[0] > declining(10, 0.8, 0.6)[-1]
    dur = spike(10, base=180, peak=340, from_week=7)
    assert dur[0] == 180 and dur[-1] >= 340 - 1
    submits = intermittent(10, fail_from=7)
    assert submits[:7] == (True,) * 7
    assert submits[7:] == (False, False, False)


def test_bias_creates_type_concentration() -> None:
    """bias 셀에 오답이 몰린다 — R6(유형 편중) 재료."""
    from ai.contracts.taxonomy import AreaTag, TypeTag

    req = build_detect_request(
        week_start="2026-07-13",
        seed=5,
        students=[
            StudentPlan(
                student_ref="st_bias",
                class_ref="cl_a1",
                weeks=8,
                solves_per_week=12,
                bias=(AreaTag.LITERATURE, TypeTag.INFER),
            )
        ],
    )
    bias_solves = [
        e
        for e in req.learning_events
        if e.type.value == "solve"
        and e.area_tag is AreaTag.LITERATURE
        and e.type_tag is TypeTag.INFER
    ]
    assert bias_solves, "편중 셀 문항이 생성돼야 한다"
    wrong = [e for e in bias_solves if e.correct is False]
    # 편중 셀 정답률이 낮으므로 오답이 다수여야 한다
    assert len(wrong) > len(bias_solves) / 2


def test_trajectory_length_mismatch_rejected() -> None:
    """궤적 길이가 weeks와 다르면 빌드 단계에서 실패한다."""
    with pytest.raises(ValueError, match="궤적 길이"):
        build_detect_request(
            week_start="2026-07-13",
            seed=1,
            students=[
                StudentPlan(student_ref="st_1", class_ref="cl_a1", weeks=10, accuracy=(0.8, 0.7)),
            ],
        )
