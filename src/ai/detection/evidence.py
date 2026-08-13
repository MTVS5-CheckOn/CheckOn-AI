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
    EvidenceRole,
    RuleId,
    StudentStatus,
    WeeklyActivityEvidence,
)

#: 🔴 **정본 근거가 없어 규칙을 판정하지 않았다** — 닫힌 상수다(09 §3 ②).
#: ⚠ 자유 문자열을 호출부마다 복제하면 `stats.rules_skipped` 집계가 조용히 갈린다.
SKIP_AUTHORITATIVE_EVIDENCE_MISSING: Final = "authoritative_evidence_missing"

#: 학습 기록 evidence의 논리 테이블명 — 응답 `EvidenceItem.source_table`.
_LEARNING_EVENT_TABLE: Final = "learning_event"


#: 🔴 R2가 거슬러 올라가는 **명시 상한**(불변식 6) — 05·09의 rolling 10주 계약과 같은 값.
MAX_ABSENCE_LOOKBACK_WEEKS: Final = 10
#: ⚠ **R3 기준창 상수를 여기 두지 않는다** — 정본은 `ThresholdConfig.baseline_window_weeks`다.
#:   여기 복제하면 설정을 낮춘 테넌트에서 **판정이 영영 안 선다**(값이 두 곳에 산다 · 03 §1).


@dataclass(frozen=True)
class EventRef:
    """근거로 인용할 학습 기록 1건 — `record_id`와 **그 기록의 날짜**.

    🔴 **날짜를 버리지 않기 위해 생겼다**(99 #60). 종전에는 인덱스가 `list[str]`이라
    주 월요일밖에 알 수 없었고, 그래서 8/13에 푼 문항이 `occurred_on: 2026-08-10`으로
    나갔다 — **BE가 원본을 열면 날짜가 안 맞는다.** `EvidenceItem.observed`의
    *"없는 값을 지어내지 않는다"* 를 바로 옆 필드가 어기고 있었다.

    ⚠ **튜플 `(str, date)`로 두지 않았다** — 소비처가 `[0]`·`[1]`로 읽으면 다음 사람이
    순서를 뒤집는다. 이름이 있어야 한다.
    """

    record_id: str
    occurred_on: date


#: 학습 기록 인덱스 — `(student_ref, 주 월요일) → 그 주의 기록들`.
#:
#: ⚠ **키의 월요일은 「어느 판정 창인가」이고, 값의 날짜는 「그 기록이 언제인가」다.**
#: 둘은 다른 축이다 — 판정은 주 단위로 하고 인용은 기록 단위로 한다.
#: 🔴 별칭을 둔 이유: 이 타입을 **전달만** 하는 시그니처가 `engine.py`에 넷이라,
#: 원시 타입을 적어 두면 형태가 바뀔 때마다 네 곳을 같이 고쳐야 한다(값이 두 곳에 사는 것과 같다).
type EvidenceIndex = Mapping[tuple[str, date], list[EventRef]]


@dataclass(frozen=True)
class StudentEvidence:
    """한 학생의 **정본 근거 묶음** — 판정과 인용이 같은 레코드를 본다.

    ⚠ 판정에 쓴 값과 응답 evidence가 갈리면 BE가 원본을 열었을 때 숫자가 안 맞는다.
    그래서 규칙은 이 묶음에서 **값과 레코드를 함께** 꺼낸다.

    🔴 **`analysis_week`가 여기 있는 이유**(99 #43·#44) — 부재형 규칙의 시간축을
    `learning_events`에서 떼어내기 위해서다. `StudentFeatures.weeks`는 **학습 이벤트가 있는
    주만** 만들므로, **완전 공백 주는 그 목록에 아예 없다** — 즉 *"가장 심한 공백"* 이
    판정 창에서 빠졌다. 부재형 신호가 그 목록을 보면 **자기가 재려는 것을 못 본다.**
    ⚠ **`StudentFeatures`에 빈 주를 억지로 넣지 않는다** — 그러면 R1·R4·R6의 평가 창까지
    바뀌어 이번 작업과 무관한 골든이 흔들린다.
    """

    analysis_week: date | None = None
    """`snapshot_meta.week_start` — 부재형 셋의 **기준 주**. 학습 이벤트와 무관하다."""

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
    analysis_week = date.fromisoformat(request.snapshot_meta.week_start)
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

    #: 🔴 **근거가 없는 학생도 항목을 만든다** — 그래야 규칙이 `analysis_week`을 보고
    #:   *"집계가 없다"* 를 **판정**할 수 있다(항목이 없으면 `EMPTY_EVIDENCE`로 떨어져
    #:   기준 주 자체를 모른다).
    refs = set(windows) | set(activity) | set(transitions) | allowed
    return {
        ref: StudentEvidence(
            analysis_week=analysis_week,
            assignment_windows=dict(windows.get(ref, {})),
            weekly_activity=dict(activity.get(ref, {})),
            returned_transitions=tuple(
                sorted(transitions.get(ref, []), key=lambda t: (t.occurred_at, t.record_id))
            ),
        )
        for ref in refs
    }


# ──────────────────── 주간 활동량 창 — 판정·문면 공용 정본 ────────────────────


@dataclass(frozen=True)
class WeeklyActivityWindow:
    """R3가 보는 **주간 활동량 창** — 판정·응답 근거·브리핑 문면이 같은 레코드를 읽는다.

    🔴 **이 타입이 생긴 이유**(2026-08-13 실측). 종전에는 R3 판정이
    `detection_evidence.weekly_activity`를 읽는데 브리핑 facts는
    `learning_event`에서 복원한 `StudentFeatures.event_count`를 읽었다. 두 축이 갈린 입력에서
    **「학습 공백」 신호 옆에 「평소 대비 100%」** 문장이 나왔고, 그 숫자는 facts 안에 있으므로
    **게이트가 못 잡는다**(근거 밖 숫자 검사는 facts를 기준으로 한다).

    ⚠ **두 입력을 같은 지표로 선언한 것이 아니다.** `learning_events`는 R1·R4·R6의 원시
    피처 정본이고, 주간 활동량은 `weekly_activity`가 정본이다 — capability별 정본을 섞지
    않는다. 여기서 고치는 것은 **R3가 자기 정본을 끝까지 들고 가는가**뿐이다.
    """

    current: WeeklyActivityEvidence
    """분석 주의 집계 — **0건도 실존 레코드**다(부재와 다르다)."""

    prior: tuple[WeeklyActivityEvidence, ...]
    """기준창의 집계 전량 — 하나라도 빠지면 이 창 자체가 만들어지지 않는다."""

    baseline_volume: float
    """`prior`의 평균. 🔴 분자와 **같은 자**로 잰 값이다(learning_event와 섞지 않는다)."""


def resolve_weekly_activity_window(
    evidence: StudentEvidence,
    *,
    baseline_window_weeks: int,
) -> WeeklyActivityWindow | None:
    """분석 주 + 기준창 집계를 **한 번에** 해소한다 — 없으면 `None`(fail-closed).

    🔴 **`None`을 0으로 바꾸지 않는다.** 「집계가 없다」와 「집계가 0이다」는 다른 사실이고
    앞은 증명할 수 없다. `learning_events` 개수로 대신하지도 않는다 — 그게 이 안건의 원인이다.

    ⚠ **기준창 주수를 여기 하드코딩하지 않는다** — 호출부가 `ThresholdConfig`에서 읽어 준다.
    복제하면 설정을 낮춘 테넌트에서 판정이 영영 안 선다(03 §1).
    ⚠ 순수 함수다 — 입력을 바꾸지 않고 DB·네트워크에 닿지 않는다.
    """
    analysis_week = evidence.analysis_week
    if analysis_week is None:
        return None
    current = evidence.weekly_activity.get(analysis_week)
    if current is None:
        return None
    prior: list[WeeklyActivityEvidence] = []
    for back in range(1, baseline_window_weeks + 1):
        row = evidence.weekly_activity.get(analysis_week - timedelta(weeks=back))
        if row is None:
            return None
        prior.append(row)
    if not prior:
        #: ⚠ 기준창이 0주면 평균을 낼 분모가 없다 — 판정하지 않는다(0으로 나누지 않는다).
        return None
    return WeeklyActivityWindow(
        current=current,
        prior=tuple(prior),
        baseline_volume=sum(row.activity_count for row in prior) / len(prior),
    )


# ───────────────────────── 규칙별 resolver ─────────────────────────


def _learning_event_items(
    events: Sequence[EventRef], label: str
) -> tuple[EvidenceItem, ...]:
    """학습 기록 근거 — 🔴 **`observed`를 채우지 않고, `occurred_on`은 그 기록의 날짜다.**

    `learning_event`는 **문항 단위**다(`correct`·`duration_sec`). 주 단위 지표(정답률·
    정규화 시간)에 해당하는 값이 **그 레코드에 없으므로**, 주 값을 레코드마다 반복해 실으면
    *"그 기록 자신의 값"* 이라는 계약이 거짓이 된다. 비교값은 `Signal`이 든다(99 #60 · 안 D).

    🔴 **날짜도 같은 규율이다**(99 #60 보강) — 종전에는 **주 월요일**을 실었다. 8/13에 푼
    문항이 `2026-08-10`으로 나가 **BE가 원본을 열면 날짜가 안 맞았다.** `occurred_at`은
    필수 필드라 **있는 값을 버리고 없는 값을 만들어 넣고 있었다.**

    ⚠ **주간 집계 근거는 그대로 주 시작일이다**(`_activity_item`·`resolve_r2_evidence`) —
    그쪽은 **진짜 주 단위 레코드**라 주 시작일이 정답이다.
    """
    return tuple(
        EvidenceItem(
            source_table=_LEARNING_EVENT_TABLE,
            record_id=event.record_id,
            summary=f"{label} 근거 기록",
            role=EvidenceRole.TRIGGER,
            occurred_on=event.occurred_on,
        )
        for event in events
    )


@dataclass(frozen=True)
class EvidenceRequest:
    """resolver 입력 — 한 규칙이 근거를 만들 때 볼 수 있는 전부."""

    student_ref: str
    label: str
    """`signal_type.value` — 학습 기록 요약 문구 재료."""

    evidence_weeks: tuple[date, ...]
    learning_events: EvidenceIndex
    student_evidence: StudentEvidence

    baseline_weeks: tuple[date, ...] = ()
    """🔴 **기준선이 된 주** — 레코드가 실존하는 규칙만 채워진다(현재 R3뿐).

    비어 있으면 `role="baseline"` 행이 안 나간다. **누락이 아니라 「가리킬 레코드가 없다」**다
    (`docs/part_a/14_evidence_fields.md` 규칙별 표).
    """


type EvidenceResolver = Callable[[EvidenceRequest], tuple[EvidenceItem, ...]]


def _from_learning_events(request: EvidenceRequest) -> tuple[EvidenceItem, ...]:
    """R1·R4·R6 — 판정에 쓴 **그 주의 학습 기록**이 곧 근거다.

    ⚠ **주별로 만든다** — 종전에는 전 주의 record_id를 한 덩어리로 폈는데, 그러면 각 근거가
    **어느 주 것인지**가 사라진다(`occurred_on`을 못 채운다).
    """
    items: list[EvidenceItem] = []
    seen: set[str] = set()
    for week_monday in request.evidence_weeks:
        fresh = [
            event
            for event in request.learning_events.get((request.student_ref, week_monday), [])
            if event.record_id not in seen
        ]
        seen.update(event.record_id for event in fresh)
        items.extend(_learning_event_items(fresh, request.label))
    return tuple(items)


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
                role=EvidenceRole.TRIGGER,
                #: ⚠ 주 단위 백엔드 집계라 **그 레코드 자신의 값**이 실존한다.
                observed=float(window.submitted_count),
                #: 🔴 `summary`의 *"예정 N건"* 이 갈 곳 — 이게 없으면 그 숫자가 응답에서
                #:   사라져 백엔드가 문자열을 계속 파싱해야 한다.
                sample_size=window.expected_count,
                occurred_on=week_monday,
            )
        )
    return tuple(items)


def resolve_r3_evidence(request: EvidenceRequest) -> tuple[EvidenceItem, ...]:
    """R3 — 그 주 학습량 집계를 인용하고 **기준선이 된 주들도 함께** 싣는다.

    0건도 실존 레코드다.

    🔴 **기록 단위 기준선을 가진 유일한 규칙이다**(99 #60). `weekly_activity_summary`가
    **주 단위 백엔드 레코드**라 `record_id`와 `activity_count`가 1:1로 붙는다 — R1·R4의
    `learning_event`(문항 단위)에는 그런 레코드가 없다.
    """
    items = [
        _activity_item(request, week_monday, EvidenceRole.TRIGGER)
        for week_monday in request.evidence_weeks
    ]
    items += [
        _activity_item(request, week_monday, EvidenceRole.BASELINE)
        for week_monday in request.baseline_weeks
    ]
    return tuple(item for item in items if item is not None)


def _activity_item(
    request: EvidenceRequest, week_monday: date, role: EvidenceRole
) -> EvidenceItem | None:
    """주간 학습량 집계 1건 → 근거. 그 주 레코드가 없으면 `None`(지어내지 않는다)."""
    activity = request.student_evidence.weekly_activity.get(week_monday)
    if activity is None:
        return None
    return EvidenceItem(
        source_table=activity.source_table,
        record_id=activity.record_id,
        summary=f"해당 주 학습 활동 {activity.activity_count}건",
        role=role,
        observed=float(activity.activity_count),
        occurred_on=week_monday,
    )


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
                    role=EvidenceRole.TRIGGER,
                    occurred_on=transition.occurred_at.date(),
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
    if resolver is None:
        raise KeyError(f"근거 resolver가 등록되지 않은 규칙이다: {rule_id!r}")
    return resolver(request)
