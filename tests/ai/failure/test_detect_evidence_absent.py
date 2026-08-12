"""**부재형 신호가 무관한 기록을 근거로 인용한다** — R2·R3·R5 (99 #43).

🔴 **R2·R3·R5는 「기록이 없거나 상태가 바뀌었다」는 사실로 발화한다.** 그런데 그 사실을
증명할 백엔드 원본 레코드가 요청에 **없었다** — 그래서 엔진은 근거가 비면
`_latest_records()`로 **그 학생의 가장 최근 아무 `learning_event`** 를 대신 인용했다.

⚠ **그 폴백은 「임의 무관 대체 금지」를 지키려던 것**이었다(09 §3 A판정 7/22 — 첫 매치가
아니라 최근 기록). 하지만 **최근이라고 무관하지 않은 것은 아니다**:

| 규칙 | 주장 | 인용된 것 |
| --- | --- | --- |
| R2 | *"예정 과제를 안 냈다"* | 과거 **`solve`** 기록 |
| R3 | *"이번 주 학습이 비었다"* | **과거 주**의 학습 기록 |
| R5 | *"휴원에서 복귀했다"* | 과거 **`solve`** 기록 |

🔴 **불변식 2는 「evidence 1건 이상」이 아니라 「그 주장을 뒷받침하는 근거」다.** 개수를
채우는 것과 근거를 대는 것은 다르다 — BE가 `record_id`로 원본을 열면 **주장과 무관한 행**이
나온다.

이 파일은 **구현 전 그 상태를 고정한다**(red). 구현 뒤에는 세 케이스가 전부
**발화하지 않고 `authoritative_evidence_missing`으로 skip**돼야 한다.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Final

import pytest

from ai.contracts.detection import (
    DetectRequest,
    DetectResponse,
    RuleId,
    SignalType,
)
from ai.detection.engine import detect

_WEEK: Final = "2026-08-10"
_TENANT_CLASS: Final = "cl_a1"
_STUDENT: Final = "st_absent"

#: 09 §3 A판정이 막으려던 것 — 이 사유로 skip돼야 한다(자유 문자열 복제 금지).
_SKIP_REASON: Final = "authoritative_evidence_missing"


def _monday(iso: str) -> datetime:
    return datetime.fromisoformat(f"{iso}T09:00:00+00:00")


def _solve(record_id: str, *, weeks_ago: int, correct: bool = True) -> dict[str, Any]:
    """과거 `solve` 하나 — **어느 규칙의 주장과도 무관한 기록**이다."""
    occurred = _monday(_WEEK) - timedelta(weeks=weeks_ago)
    return {
        "record_id": record_id,
        "student_ref": _STUDENT,
        "type": "solve",
        "occurred_at": occurred.astimezone(UTC).isoformat(),
        "correct": correct,
        "duration_sec": 60,
        "passage_word_count": 300,
        "source": "trackA",
    }


def _request(
    *,
    events: list[dict[str, Any]],
    status: str = "enrolled",
    enrolled_weeks: int = 12,
    extra: dict[str, Any] | None = None,
) -> DetectRequest:
    body: dict[str, Any] = {
        "snapshot_meta": {
            "week_start": _WEEK,
            "snapshot_hash": "sha256:test",
            "term_context": "normal",
            "classes": [{"class_ref": _TENANT_CLASS}],
        },
        "students": [
            {
                "student_ref": _STUDENT,
                "class_ref": _TENANT_CLASS,
                "enrolled_weeks": enrolled_weeks,
                "status": status,
                "consent": "granted",
            }
        ],
        "learning_events": events,
        "alert_context": [],
    }
    body.update(extra or {})
    return DetectRequest.model_validate(body)


def _signals_of(response: DetectResponse, rule: RuleId) -> tuple[Any, ...]:
    return tuple(s for s in response.signals if s.rule_id is rule)


def _skip_count(response: DetectResponse, rule: RuleId, reason: str) -> int:
    return sum(
        row.students
        for row in response.stats.rules_skipped
        if row.rule_id is rule and row.reason == reason
    )


def _cited_record_ids(response: DetectResponse) -> set[str]:
    return {item.record_id for signal in response.signals for item in signal.evidence}


# ───────────────────────── R2 ─────────────────────────


def test_r2_does_not_cite_a_past_solve_as_missing_submission() -> None:
    """🔴 R2는 *"예정 과제를 안 냈다"* 인데 **과거 `solve`** 를 근거로 인용했다.

    ⚠ **과제 집계가 없으면 애초에 「예정 과제가 있었는가」를 모른다** —
    `WeekFeatures.submitted: bool` 하나로는 *"과제가 있었는데 안 냈다"* 와
    *"과제가 없었다"* 가 **구분되지 않는다.**
    """
    events = [_solve("ev_past_solve", weeks_ago=6)]
    response = detect(_request(events=events))

    assert not _signals_of(response, RuleId.R2), (
        "과제 집계가 없는데 R2가 발화했다 — 무엇을 근거로 미제출을 주장하는가"
    )
    assert _skip_count(response, RuleId.R2, _SKIP_REASON) >= 1, (
        f"R2가 {_SKIP_REASON}으로 기록되지 않았다 — 조용한 미판정 금지"
    )
    assert "ev_past_solve" not in _cited_record_ids(response), (
        "과거 solve가 미제출 근거로 인용됐다"
    )


# ───────────────────────── R3 ─────────────────────────


def test_r3_does_not_cite_an_older_week_as_a_learning_gap() -> None:
    """🔴 R3는 *"이번 주 학습이 비었다"* 인데 **과거 주 기록**을 근거로 인용했다.

    ⚠ **0건도 실존하는 집계 레코드로 증명해야 한다** — 「기록이 없다」와 「집계가 0이다」는
    다른 사실이고, 앞은 **증명할 수 없다.**
    """
    events = [_solve(f"ev_old_{i}", weeks_ago=w) for i, w in enumerate(range(2, 8))]
    response = detect(_request(events=events))

    assert not _signals_of(response, RuleId.R3), (
        "주간 집계가 없는데 R3가 발화했다 — 이번 주가 비었다는 것을 무엇이 증명하는가"
    )
    assert _skip_count(response, RuleId.R3, _SKIP_REASON) >= 1
    assert not (_cited_record_ids(response) & {e["record_id"] for e in events}), (
        "과거 주 기록이 학습 공백 근거로 인용됐다"
    )


# ───────────────────────── R5 ─────────────────────────


def test_r5_does_not_cite_a_past_solve_as_a_return_transition() -> None:
    """🔴 R5는 *"휴원에서 복귀했다"* 인데 **과거 `solve`** 를 근거로 인용했다.

    ⚠ `students[].status == returned` **하나**로 발화했다 — 상태 필드는 **현재 값**이고
    **전환이 언제 있었는지**를 말하지 않는다. 그 전환 이력이 요청에 없었다.
    """
    events = [_solve("ev_before_pause", weeks_ago=5)]
    response = detect(_request(events=events, status="returned"))

    assert not _signals_of(response, RuleId.R5), (
        "상태 전환 이력이 없는데 R5가 발화했다 — 복귀를 무엇이 증명하는가"
    )
    assert _skip_count(response, RuleId.R5, _SKIP_REASON) >= 1
    assert "ev_before_pause" not in _cited_record_ids(response), (
        "과거 solve가 복귀 근거로 인용됐다"
    )


# ───────────────────────── 절단 가드 ─────────────────────────


def test_the_student_is_actually_evaluated() -> None:
    """🔴 **판정 대상에서 빠지면 위 셋은 공짜로 통과한다** — 제외가 아니라 skip이어야 한다."""
    response = detect(_request(events=[_solve("ev_guard", weeks_ago=3)]))
    assert response.stats.students_evaluated == 1, (
        "학생이 판정 대상이 아니다 — 위 검사들이 아무것도 안 보고 있다"
    )
    assert response.stats.excluded_under_2w == 0


@pytest.mark.parametrize(
    ("rule", "signal_type"),
    [
        (RuleId.R2, SignalType.SUBMIT_DROP),
        (RuleId.R3, SignalType.VOLUME_GAP),
        (RuleId.R5, SignalType.RETURN_CARE),
    ],
)
def test_the_three_absence_rules_are_the_subject(
    rule: RuleId, signal_type: SignalType
) -> None:
    """이 파일이 다루는 규칙 셋을 이름으로 못 박는다 — 매핑이 바뀌면 red."""
    from ai.contracts.detection import RULE_SIGNAL_MAP  # noqa: PLC0415

    assert RULE_SIGNAL_MAP[rule] is signal_type
