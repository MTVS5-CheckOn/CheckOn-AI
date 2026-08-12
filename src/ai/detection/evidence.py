"""규칙별 근거 해소 — **어떤 규칙이 무엇을 인용하는가** (99 #43).

🔴 **종전에는 규칙과 근거가 분리돼 있지 않았다.** 엔진이 `evidence_weeks`로 그 주의
`learning_event`를 긁고, **비면 「가장 최근 아무 기록」으로 대신**했다:

```python
if not record_ids:
    record_ids = _latest_records(...)   # ← R2가 과거 solve를, R5가 복귀 전 solve를 인용
```

⚠ 그 폴백은 *"임의 무관 대체 금지"* 를 지키려던 것이었다(09 §3 A판정 7/22 — 첫 매치가
아니라 **최근** 기록). 🔴 **그러나 최근이라고 무관하지 않은 것은 아니다.** 불변식 2는
「evidence 1건 이상」이 아니라 **「그 주장을 뒷받침하는 근거」**다 — 개수를 채우는 것과
근거를 대는 것은 다르다.

⇒ **규칙마다 resolver를 둔다.** 매핑은 **닫혀 있다**: 규칙이 늘었는데 resolver를 안 붙이면
**등록 검사가 red**다. *"등록 안 된 규칙 → 최신 아무 기록"* 같은 폴백은 두지 않는다.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Final

from ai.contracts.detection import (
    AssignmentWindowEvidence,
    DetectRequest,
    EnrollmentTransitionEvidence,
    EvidenceItem,
    RuleId,
    StudentStatus,
    WeeklyActivityEvidence,
)

#: 🔴 **정본 근거가 없어 규칙을 판정하지 않았다** — 닫힌 상수다(09 §3 ②).
#: ⚠ 자유 문자열을 호출부마다 복제하면 `stats.rules_skipped` 집계가 조용히 갈린다.
SKIP_AUTHORITATIVE_EVIDENCE_MISSING: Final = "authoritative_evidence_missing"

#: 학습 기록 evidence의 논리 테이블명 — 응답 `EvidenceItem.source_table`.
_LEARNING_EVENT_TABLE: Final = "learning_event"


@dataclass(frozen=True)
class StudentEvidence:
    """한 학생의 **정본 근거 묶음** — 판정과 인용이 같은 레코드를 본다.

    ⚠ 판정에 쓴 값과 응답 evidence가 갈리면 BE가 원본을 열었을 때 숫자가 안 맞는다.
    그래서 규칙은 이 묶음에서 **값과 레코드를 함께** 꺼낸다.
    """

    assignment_windows: Mapping[date, AssignmentWindowEvidence] = field(
        default_factory=dict
    )
    weekly_activity: Mapping[date, WeeklyActivityEvidence] = field(default_factory=dict)
    returned_transitions: tuple[EnrollmentTransitionEvidence, ...] = ()

    def returns_in_week(self, week_monday: date) -> tuple[EnrollmentTransitionEvidence, ...]:
        """그 주에 일어난 **복귀 전환**만 — 다른 주의 복귀는 이번 주 R5 근거가 아니다."""
        return tuple(
            item
            for item in self.returned_transitions
            if _monday_of(item.occurred_at.date()) == week_monday
        )


#: 근거가 하나도 없는 학생 — `Mapping.get`이 매번 `None`을 내지 않게 한다.
EMPTY_EVIDENCE: Final = StudentEvidence()


def _monday_of(day: date) -> date:
    return day - timedelta(days=day.weekday())


def build_student_evidence(request: DetectRequest) -> dict[str, StudentEvidence]:
    """요청의 `detection_evidence`를 학생별로 색인한다.

    ⚠ **동의하지 않은 학생은 여기서 빠진다** — 판정·브리핑·원장 어디에도 도달하지 않게
    입구에서 거른다(불변식 3의 같은 방향). `students[]`에 없는 alias는 계약 검증이 이미 막았다.
    """
    from ai.contracts.detection import CONSENT_GRANTED  # noqa: PLC0415

    allowed = {
        student.student_ref
        for student in request.students
        if student.consent == CONSENT_GRANTED
    }
    windows: dict[str, dict[date, AssignmentWindowEvidence]] = defaultdict(dict)
    activity: dict[str, dict[date, WeeklyActivityEvidence]] = defaultdict(dict)
    transitions: dict[str, list[EnrollmentTransitionEvidence]] = defaultdict(list)

    for item in request.detection_evidence:
        if item.student_ref not in allowed:
            continue
        if isinstance(item, AssignmentWindowEvidence):
            windows[item.student_ref][item.week_start] = item
        elif isinstance(item, WeeklyActivityEvidence):
            activity[item.student_ref][item.week_start] = item
        elif item.to_status is StudentStatus.RETURNED:
            #: ⚠ R5가 쓰는 것은 **복귀 전환뿐**이다 — 다른 전환은 담지 않는다(#22 회피).
            transitions[item.student_ref].append(item)

    refs = set(windows) | set(activity) | set(transitions)
    return {
        ref: StudentEvidence(
            assignment_windows=dict(windows.get(ref, {})),
            weekly_activity=dict(activity.get(ref, {})),
            returned_transitions=tuple(
                sorted(transitions.get(ref, []), key=lambda t: (t.occurred_at, t.record_id))
            ),
        )
        for ref in refs
    }


# ───────────────────────── 규칙별 resolver ─────────────────────────


def _learning_event_items(record_ids: Sequence[str], label: str) -> tuple[EvidenceItem, ...]:
    return tuple(
        EvidenceItem(
            source_table=_LEARNING_EVENT_TABLE,
            record_id=record_id,
            summary=f"{label} 근거 기록",
        )
        for record_id in record_ids
    )


@dataclass(frozen=True)
class EvidenceRequest:
    """resolver 입력 — 한 규칙이 근거를 만들 때 볼 수 있는 전부."""

    student_ref: str
    label: str
    """`signal_type.value` — 학습 기록 요약 문구 재료."""

    evidence_weeks: tuple[date, ...]
    learning_events: Mapping[tuple[str, date], list[str]]
    student_evidence: StudentEvidence


type EvidenceResolver = Callable[[EvidenceRequest], tuple[EvidenceItem, ...]]


def _from_learning_events(request: EvidenceRequest) -> tuple[EvidenceItem, ...]:
    """R1·R4·R6 — 판정에 쓴 **그 주의 학습 기록**이 곧 근거다(현행 유지)."""
    record_ids: list[str] = []
    for week_monday in request.evidence_weeks:
        record_ids.extend(
            request.learning_events.get((request.student_ref, week_monday), [])
        )
    return _learning_event_items(list(dict.fromkeys(record_ids)), request.label)


def resolve_r2_evidence(request: EvidenceRequest) -> tuple[EvidenceItem, ...]:
    """R2 — **발화에 쓴 과제 집계 레코드만** 인용한다.

    🔴 `solve`·`attend`·`consult`·`submit` 이벤트를 대신 인용하지 않는다.
    ⚠ 요약 숫자는 **입력 값 그대로**다 — 판정과 문면이 갈리면 BE가 원본을 열었을 때 안 맞는다.
    """
    items: list[EvidenceItem] = []
    for week_monday in request.evidence_weeks:
        window = request.student_evidence.assignment_windows.get(week_monday)
        if window is None:
            continue
        items.append(
            EvidenceItem(
                source_table=window.source_table,
                record_id=window.record_id,
                summary=f"예정 과제 {window.expected_count}건 중 제출 {window.submitted_count}건",
            )
        )
    return tuple(items)


def resolve_r3_evidence(request: EvidenceRequest) -> tuple[EvidenceItem, ...]:
    """R3 — **그 주 학습량 집계**만 인용한다. 0건도 실존 레코드다."""
    items: list[EvidenceItem] = []
    for week_monday in request.evidence_weeks:
        activity = request.student_evidence.weekly_activity.get(week_monday)
        if activity is None:
            continue
        items.append(
            EvidenceItem(
                source_table=activity.source_table,
                record_id=activity.record_id,
                summary=f"해당 주 학습 활동 {activity.activity_count}건",
            )
        )
    return tuple(items)


def resolve_r5_evidence(request: EvidenceRequest) -> tuple[EvidenceItem, ...]:
    """R5 — **그 주의 복귀 전환 이력**만 인용한다. 과거 학습 이벤트는 근거가 아니다."""
    items: list[EvidenceItem] = []
    for week_monday in request.evidence_weeks:
        for transition in request.student_evidence.returns_in_week(week_monday):
            items.append(  # noqa: PERF401 — 이중 루프라 컴프리헨션이 더 안 읽힌다
                EvidenceItem(
                    source_table=transition.source_table,
                    record_id=transition.record_id,
                    summary="휴원 후 복귀 상태 전환 기록",
                )
            )
    return tuple(items)


#: 🔴 **닫힌 매핑** — 규칙이 늘었는데 resolver를 안 붙이면 등록 검사가 red다.
#: ⚠ *"등록 안 된 규칙 → 최신 아무 기록"* 폴백을 두지 않는다. 그 폴백이 이 안건의 원인이다.
RULE_EVIDENCE_RESOLVERS: Final[Mapping[RuleId, EvidenceResolver]] = {
    RuleId.R1: _from_learning_events,
    RuleId.R2: resolve_r2_evidence,
    RuleId.R3: resolve_r3_evidence,
    RuleId.R4: _from_learning_events,
    RuleId.R5: resolve_r5_evidence,
    RuleId.R6: _from_learning_events,
}


def resolve_evidence(rule_id: RuleId, request: EvidenceRequest) -> tuple[EvidenceItem, ...]:
    """규칙의 근거를 만든다 — 🔴 **미등록 규칙은 폴백이 아니라 예외**다."""
    resolver = RULE_EVIDENCE_RESOLVERS.get(rule_id)
    if resolver is None:  # pragma: no cover — 등록 검사가 먼저 잡는다
        raise KeyError(f"근거 resolver가 등록되지 않은 규칙이다: {rule_id.value}")
    return resolver(request)
