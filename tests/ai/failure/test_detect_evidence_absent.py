"""4-1 재현 — 무이벤트 학생의 부재형 신호가 /v1/detect 전체를 500으로 떨어뜨린다.

**정상 경로다.** `status=returned`(복귀 첫 주 — 활동 0건이 당연)·동의 있음·재원 2주+ 학생
1명이면 R5(복귀 케어)가 발화하는데, 판정 창에도 과거에도 학습 기록이 없어
`_latest_records` 폴백까지 비고 `evidence=()`로 `Signal`을 만들다 ValidationError가 터진다
(`Signal.evidence`가 `min_length=1` — 불변식 2). 그 학생 1명 때문에 **반 전체 응답**이 500이다.

수렴 규칙은 09 §3 A판정 ②: "관련 기록이 전무하면 **신호를 생성하지 않는다**(계약상
evidence ≥ 1)". 스키마를 완화하지 않고 호출부가 물러선다.
"""

from __future__ import annotations

from datetime import UTC, datetime

from ai.contracts.detection import (
    ClassRef,
    DetectRequest,
    EventSource,
    EventType,
    LearningEvent,
    RuleId,
    SignalType,
    SnapshotMeta,
    StudentInput,
    StudentStatus,
    TermContext,
)
from ai.detection.engine import detect
from ai.detection.thresholds import default_threshold_config

_WEEK = "2026-07-13"
_CONSENT = "granted"


def _request(
    students: tuple[StudentInput, ...], events: tuple[LearningEvent, ...] = ()
) -> DetectRequest:
    class_refs = tuple({s.class_ref for s in students})
    return DetectRequest(
        snapshot_meta=SnapshotMeta(
            week_start=_WEEK,
            snapshot_hash="sha256:" + "e" * 64,
            term_context=TermContext.NORMAL,
            classes=tuple(ClassRef(class_ref=ref) for ref in sorted(class_refs)),
        ),
        students=students,
        learning_events=events,
        alert_context=(),
    )


def _returned_student(ref: str = "st_returned", class_ref: str = "cl_a1") -> StudentInput:
    """복귀 첫 주 학생 — 활동 0건이 **정상**이다(휴원 후 복귀 직후)."""
    return StudentInput(
        student_ref=ref,
        class_ref=class_ref,
        enrolled_weeks=8,  # 재원 2주+ → 판정 대상
        status=StudentStatus.RETURNED,  # R5 발화 조건
        consent=_CONSENT,
    )


def _event(ref: str, day: str, record_id: str) -> LearningEvent:
    return LearningEvent(
        record_id=record_id,
        student_ref=ref,
        occurred_at=datetime.fromisoformat(f"{day}T09:00:00").replace(tzinfo=UTC),
        type=EventType.SOLVE,
        source=EventSource.TRACK_A,
        correct=True,
    )


# ── 재현: 기록 전무 학생 1명이 반 전체를 죽인다 ────────────────────


def test_no_event_returned_student_does_not_raise() -> None:
    """핵심 재현 — 예외 없이 응답이 나와야 한다(현재는 ValidationError → 500)."""
    response = detect(
        _request((_returned_student(),)), config=default_threshold_config()
    )
    assert response is not None


def test_no_event_returned_student_yields_no_signal() -> None:
    """근거가 전무하면 신호를 만들지 않는다 — 09 §3 A판정 ②."""
    response = detect(
        _request((_returned_student(),)), config=default_threshold_config()
    )
    assert all(s.signal_type is not SignalType.RETURN_CARE for s in response.signals)


def test_evidence_absent_skip_is_recorded_in_stats() -> None:
    """조용한 드롭 금지 — 스킵 사유가 stats.rules_skipped에 결정론으로 남는다."""
    response = detect(
        _request((_returned_student(),)), config=default_threshold_config()
    )
    skips = {(s.rule_id, s.reason): s.students for s in response.stats.rules_skipped}
    assert (RuleId.R5, "evidence_absent") in skips
    assert skips[(RuleId.R5, "evidence_absent")] == 1


def test_other_students_still_get_signals() -> None:
    """기록 전무 학생 1명이 **같은 반 다른 학생의 신호를 죽이지 않는다**(반 전체 500 방지)."""
    others = _returned_student(ref="st_returned_b")
    response = detect(
        _request(
            (_returned_student(), others),
            events=(_event("st_returned_b", "2026-07-14", "le_b1"),),
        ),
        config=default_threshold_config(),
    )
    raised = {s.student_ref for s in response.signals}
    assert "st_returned_b" in raised  # 기록 있는 복귀 학생은 R5가 나간다
    assert "st_returned" not in raised  # 기록 전무 학생만 빠진다


# ── 기존 `_latest_records` 폴백 동작 무변경 ────────────────────────


def test_latest_record_fallback_still_cites_past_event() -> None:
    """판정 창엔 없고 과거에만 기록이 있으면 **가장 최근 기록**을 인용한다(09 §3 ② 전단).

    이 경로는 이번 픽스로 바뀌지 않아야 한다 — 스킵은 "폴백까지 빈" 경우만이다.
    """
    response = detect(
        _request(
            (_returned_student(),),
            events=(_event("st_returned", "2026-06-15", "le_old"),),  # 4주 전
        ),
        config=default_threshold_config(),
    )
    care = [s for s in response.signals if s.signal_type is SignalType.RETURN_CARE]
    assert care, "과거 기록이 있으면 R5가 나가야 한다"
    assert [e.record_id for e in care[0].evidence] == ["le_old"]


def test_rank_stays_contiguous_when_signal_is_skipped() -> None:
    """스킵이 rank 구멍을 만들지 않는다 — 랭킹 이전 탈락(슬롯 미소비).

    같은 반에 기록 전무 학생 + 기록 있는 학생을 섞어도 rank는 1부터 연속이다.
    """
    response = detect(
        _request(
            (
                _returned_student(ref="st_a"),  # 기록 전무 → 스킵
                _returned_student(ref="st_b"),
                _returned_student(ref="st_c"),
            ),
            events=(
                _event("st_b", "2026-07-14", "le_b1"),
                _event("st_c", "2026-07-14", "le_c1"),
            ),
        ),
        config=default_threshold_config(),
    )
    ranks = sorted(s.rank for s in response.signals)
    assert ranks == list(range(1, len(ranks) + 1)), f"rank에 구멍이 있다: {ranks}"
