"""FakeSnapshot — /detect 요청의 결정론적 가짜 데이터 빌더 (평가 전용).

**평가·테스트 전용.** detection 등 프로덕션 capability는 이 모듈을 import하지 않는다
(02_ownership.md §5 "평가 (프로덕션 격리)"). 소비자는 evaluation/ 내부와 tests/뿐이다.

사양 원본:
- docs/05_request_json.md §1 (요청 JSON 형태)
- docs/part_a/09_detect_spec.md §2 (필드 필수/선택·의미)
- docs/part_a/08_evaluation_plan.md §2 (골든셋 = 8~12주 FakeSnapshot + 기대 결과)
- src/ai/contracts/detection.py DetectRequest (모든 산출물이 이 계약을 통과한다)

설계 원칙:
- **계약이 곧 스키마 검증이다.** 빌더는 순수 dict를 조립한 뒤 DetectRequest.model_validate로
  통과시켜 반환한다 — 계약을 어기는 픽스처는 생성 단계에서 실패한다.
- **결정론.** seed와 week_start만으로 산출물이 고정된다. datetime.now()·전역 random 금지
  (03_coding_rules.md §3). 시각은 week_start에서 유도하고 난수는 Random(seed)로 격리한다.
- **불변식 3(alias만).** 실명·연락처 필드는 만들지 않는다. 식별자는 st_*·cl_*·le_* 형태.

이 모듈은 빌더와 궤적 헬퍼·대표 픽스처까지만 제공한다. 골든셋 G1~G12의 기대값 검증은
detection 구현 작업의 범위다 (08_evaluation_plan.md §2).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from itertools import count
from random import Random
from typing import Any
from zoneinfo import ZoneInfo

from ai.contracts.detection import (
    AlertContextItem,
    AlertStatus,
    DetectionEvidence,
    DetectRequest,
    EnrollmentTransitionEvidence,
    EventSource,
    SignalType,
    StudentStatus,
    TermContext,
)
from ai.contracts.taxonomy import AreaTag, ItemFormat, SubjectTrack, TypeTag

#: 학습 기록 시각의 표준 시간대 — KST (03_coding_rules.md §1b: zoneinfo 표준 사용).
KST = ZoneInfo("Asia/Seoul")

#: 이벤트가 찍히는 고정 시각(주중 저녁). 결정론을 위해 현재 시각을 쓰지 않는다.
_EVENT_TIME = time(hour=19, minute=20)


# ───────────────────────── 궤적 헬퍼 ─────────────────────────
# 스칼라 대신 주차별 tuple을 만들어 "안정/하락/급증/연속 미제출" 패턴을 명시적으로 표현한다.


def stable(weeks: int, value: float) -> tuple[float, ...]:
    """전 주 동일 값 — 안정 궤적 (예: 정답률 유지)."""
    return (value,) * weeks


def declining(weeks: int, start: float, end: float) -> tuple[float, ...]:
    """start→end 선형 하락 — R1(정답률 하락) 재료."""
    if weeks < 2:
        return (start,) * weeks
    step = (end - start) / (weeks - 1)
    return tuple(round(start + step * w, 4) for w in range(weeks))


def spike(weeks: int, base: int, peak: int, from_week: int) -> tuple[int, ...]:
    """from_week부터 base→peak로 급증 — R4(숨은 위기: 시간 급증) 재료.

    정답률(stable)과 함께 쓰면 "점수는 유지되는데 풀이 시간만 급증"을 만든다.
    """
    if from_week >= weeks - 1 or weeks < 2:
        return (base,) * weeks
    ramp = weeks - from_week
    step = (peak - base) / (ramp - 1) if ramp > 1 else 0
    return tuple(
        base if w < from_week else round(base + step * (w - from_week)) for w in range(weeks)
    )


def intermittent(weeks: int, fail_from: int) -> tuple[bool, ...]:
    """fail_from 주차부터 제출 실패 — R2(연속 미제출) 재료."""
    return tuple(w < fail_from for w in range(weeks))


# ───────────────────────── 학생 계획 ─────────────────────────


@dataclass(frozen=True)
class StudentPlan:
    """한 학생의 주차별 학습 패턴 스펙 — 빌더가 이걸 이벤트로 전개한다.

    스칼라를 주면 전 주 동일, 길이 weeks인 tuple을 주면 주차별 궤적으로 해석한다
    (궤적 헬퍼 stable/declining/spike/intermittent 참고).
    """

    student_ref: str
    class_ref: str
    weeks: int = 10
    """스냅숏이 담는 주차 수 — 08 §2 기준 8~12."""

    enrolled_weeks: int = 14
    """등원 주차 — 2 미만이면 신규생(판정 제외 대상)."""

    status: StudentStatus = StudentStatus.ENROLLED
    consent: str = "granted"
    solves_per_week: int | tuple[int, ...] = 8
    """주간 풀이 이벤트 수 — 볼륨 증감(궤적)·표본 부족(작은 값)에 사용."""

    accuracy: float | tuple[float, ...] = 0.8
    """주간 정답률(0~1)."""

    duration_sec: int | tuple[int, ...] = 180
    """풀이 시간(초) 궤적 — passage_word_count 대비 정규화 시간(R4)."""

    passage_word_count: int = 800
    """지문 어절 수 — 고정하면 duration 급증이 곧 정규화 시간 급증이 된다."""

    submit_ok: bool | tuple[bool, ...] = True
    """주간 과제 제출 여부 — False가 연속되면 R2."""

    default_area: AreaTag = AreaTag.READING
    default_type: TypeTag = TypeTag.INFER
    bias: tuple[AreaTag, TypeTag] | None = None
    """지정 시 매 주 풀이의 절반을 이 (area×type) 셀에 몰고 그 셀 정답률을 낮춘다 — R6 유형 편중."""

    bias_accuracy: float = 0.35
    """편중 셀의 낮은 정답률 (bias가 있을 때만)."""

    assignment_expected: int | tuple[int, ...] = 1
    """주간 **예정 과제 수**(99 #43) — R2의 분모다.

    🔴 `0`이면 그 주는 **과제가 없던 주**라 미제출 연속에서 제외된다(방학·휴강).
    ⚠ 제출 수는 `submit_ok`에서 파생한다 — 두 값을 따로 주면 픽스처가 모순될 수 있다.
    """

    emit_detection_evidence: bool = True
    """🔴 **정본 근거 배열을 함께 만들 것인가**(99 #43).

    `False`면 근거 없는 **기존 형태의 요청**이 나온다 — R2·R3·R5가
    `authoritative_evidence_missing`으로 skip되는 경로를 조립할 때 쓴다.
    """

    events_despite_exclusion: bool = False
    """True면 무동의·paused 학생에게도 learning_events를 생성한다.

    09 §2는 "consent≠granted면 이벤트가 **와도** 버린다"는 AI 방어 규칙을 둔다 —
    즉 백엔드가 못 거른 경우를 조립해 엔진의 제외 처리(폐기)를 테스트할 수 있어야 한다.
    기본값 False는 현실 스냅숏(백엔드가 걸러 보냄)을 따른다.
    """


def _per_week_float(value: float | tuple[float, ...], weeks: int) -> tuple[float, ...]:
    if isinstance(value, tuple):
        if len(value) != weeks:
            raise ValueError(f"궤적 길이 {len(value)}가 weeks {weeks}와 다르다")
        return value
    return (float(value),) * weeks


def _per_week_int(value: int | tuple[int, ...], weeks: int) -> tuple[int, ...]:
    if isinstance(value, tuple):
        if len(value) != weeks:
            raise ValueError(f"궤적 길이 {len(value)}가 weeks {weeks}와 다르다")
        return value
    return (int(value),) * weeks


def _per_week_bool(value: bool | tuple[bool, ...], weeks: int) -> tuple[bool, ...]:
    if isinstance(value, tuple):
        if len(value) != weeks:
            raise ValueError(f"궤적 길이 {len(value)}가 weeks {weeks}와 다르다")
        return value
    return (value,) * weeks


# ───────────────────────── 이벤트 전개 ─────────────────────────


def _monday_of(week_start: str) -> date:
    """week_start(ISO 날짜)를 그 주 월요일로 정규화한다."""
    base = date.fromisoformat(week_start)
    return base - timedelta(days=base.weekday())


def _event_dt(week_monday: date, weeks: int, w: int, ordinal: int) -> datetime:
    """주차 w(0=가장 과거, weeks-1=최근=week_start 주)·순번 ordinal의 이벤트 시각.

    최근 주가 week_start에 놓이도록 과거로 거슬러 배치한다. 분(minute)만 순번으로
    벌려 같은 주 이벤트가 구분되게 한다(현재 시각 미사용 — 결정론).
    """
    monday = week_monday - timedelta(weeks=(weeks - 1 - w))
    day = monday + timedelta(days=ordinal % 5)
    stamp = datetime.combine(day, _EVENT_TIME, tzinfo=KST)
    return stamp + timedelta(minutes=ordinal)


def _solve_events(
    plan: StudentPlan,
    week_monday: date,
    rng: Random,
    next_id: count[int],
) -> list[dict[str, Any]]:
    """한 학생의 전 주차 solve/submit 이벤트를 계약 dict로 전개한다."""
    weeks = plan.weeks
    solves = _per_week_int(plan.solves_per_week, weeks)
    accuracy = _per_week_float(plan.accuracy, weeks)
    durations = _per_week_int(plan.duration_sec, weeks)
    submits = _per_week_bool(plan.submit_ok, weeks)

    events: list[dict[str, Any]] = []
    for w in range(weeks):
        n = solves[w]
        # 정답 여부를 결정적으로 만든 뒤 seed로만 순서를 섞는다(같은 seed → 같은 배열).
        correct_count = round(n * accuracy[w])
        flags = [i < correct_count for i in range(n)]
        rng.shuffle(flags)
        for ordinal in range(n):
            area, type_tag, is_correct = _cell_for(plan, ordinal, n, flags[ordinal], rng)
            events.append(
                {
                    "record_id": f"le_{next(next_id)}",
                    "student_ref": plan.student_ref,
                    "type": "solve",
                    "occurred_at": _event_dt(week_monday, weeks, w, ordinal),
                    "correct": is_correct,
                    "duration_sec": durations[w],
                    "passage_word_count": plan.passage_word_count,
                    "area_tag": area.value,
                    "subject_track": _track_for(area).value,
                    "type_tag": type_tag.value,
                    "item_format": ItemFormat.MCQ.value,
                    "source": EventSource.TRACK_B.value,
                }
            )
        if submits[w]:
            events.append(
                {
                    "record_id": f"le_{next(next_id)}",
                    "student_ref": plan.student_ref,
                    "type": "submit",
                    "occurred_at": _event_dt(week_monday, weeks, w, n),
                    "source": EventSource.STUDENT_HOME.value,
                }
            )
    return events


def _cell_for(
    plan: StudentPlan,
    ordinal: int,
    n: int,
    default_correct: bool,
    rng: Random,
) -> tuple[AreaTag, TypeTag, bool]:
    """문항의 (area, type, 정답 여부)를 정한다.

    bias가 있으면 앞 절반을 편중 셀로 몰고 그 셀 정답률을 낮춰 R6(유형 편중)을 만든다.
    """
    if plan.bias is not None and ordinal < n // 2:
        area, type_tag = plan.bias
        is_correct = rng.random() < plan.bias_accuracy
        return area, type_tag, is_correct
    return plan.default_area, plan.default_type, default_correct


def _track_for(area: AreaTag) -> SubjectTrack:
    from ai.contracts.taxonomy import derive_subject_track

    return derive_subject_track(area)


# ───────────────────────── alert_context 헬퍼 ─────────────────────────
# lifecycle 3분기(new·ongoing·follow_up)를 만들 수 있는 이력 항목을 구성한다 (09 §4).


def alert_open(student_ref: str, signal_type: SignalType) -> AlertContextItem:
    """미해결(open) 경보 — 같은 유형이 재발하면 lifecycle=ongoing."""
    return AlertContextItem(
        student_ref=student_ref,
        signal_type=signal_type,
        status=AlertStatus.OPEN,
        resolved_at=None,
        followed_up=False,
    )


def alert_resolved(
    student_ref: str,
    signal_type: SignalType,
    resolved_at: datetime,
    followed_up: bool = False,
) -> AlertContextItem:
    """해소(resolved) 경보 — 2주 이내 재발 + 미팔로업이면 lifecycle=follow_up.

    resolved_at은 결정론을 위해 호출자가 명시한다(현재 시각 금지). week_start 기준으로
    2주 이내/이후를 직접 계산해 넘긴다.
    """
    return AlertContextItem(
        student_ref=student_ref,
        signal_type=signal_type,
        status=AlertStatus.RESOLVED,
        resolved_at=resolved_at,
        followed_up=followed_up,
    )


# ───────────────────────── 빌더 ─────────────────────────


def _test_snapshot_hash(week_start: str, seed: int) -> str:
    """테스트용 snapshot_hash 플레이스홀더 — **실제 해시가 아니다.**

    실제 값은 백엔드가 요청 본문 전체를 04 부록 A의 canonical json 규칙으로 해시해
    계산한다(09 §2). 그 규칙을 여기서 재현·지어내지 않는다 — 픽스처는 week_start와
    seed에서 결정적으로 유도한 재현 가능한 표식만 둔다.
    """
    return f"sha256:test-{seed}-{week_start}"


def build_detect_request(
    *,
    week_start: str,
    seed: int,
    students: Sequence[StudentPlan],
    alert_context: Sequence[AlertContextItem] = (),
    term_context: TermContext = TermContext.NORMAL,
) -> DetectRequest:
    """StudentPlan들을 결정론적 DetectRequest로 전개한다.

    순수 dict를 조립한 뒤 DetectRequest.model_validate로 통과시켜 반환한다 —
    계약(detection.py)을 어기는 픽스처는 여기서 실패한다. classes는 학생들의
    class_ref에서 유도한다.
    """
    rng = Random(seed)
    week_monday = _monday_of(week_start)
    next_id = count(1)

    class_refs = sorted({plan.class_ref for plan in students})
    learning_events: list[dict[str, Any]] = []
    detection_evidence: list[dict[str, Any]] = []
    student_rows: list[dict[str, Any]] = []
    for plan in students:
        student_rows.append(
            {
                "student_ref": plan.student_ref,
                "class_ref": plan.class_ref,
                "enrolled_weeks": plan.enrolled_weeks,
                "status": plan.status.value,
                "consent": plan.consent,
            }
        )
        # 무동의·휴원 학생은 기본적으로 이벤트를 만들지 않는다(백엔드가 걸러 보냄).
        # events_despite_exclusion=True면 "백엔드가 못 거른 경우"를 조립한다 — 엔진의
        # 제외 방어를 테스트하기 위한 명시적 경로 (09 §2).
        excluded = plan.status is StudentStatus.PAUSED or plan.consent != "granted"
        if excluded and not plan.events_despite_exclusion:
            continue
        plan_events = _solve_events(plan, week_monday, rng, next_id)
        learning_events.extend(plan_events)
        if plan.emit_detection_evidence:
            detection_evidence.extend(
                _detection_evidence(plan, week_monday, plan_events)
            )

    payload: dict[str, Any] = {
        "snapshot_meta": {
            "week_start": week_start,
            "snapshot_hash": _test_snapshot_hash(week_start, seed),
            "term_context": term_context.value,
            "classes": [{"class_ref": ref} for ref in class_refs],
        },
        "students": student_rows,
        "learning_events": learning_events,
        "alert_context": [item.model_dump(mode="python") for item in alert_context],
        "detection_evidence": detection_evidence,
    }
    return DetectRequest.model_validate(payload)


def _detection_evidence(
    plan: StudentPlan, week_monday: date, events: Sequence[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    """R2·R3·R5의 **정본 근거**를 계획에서 파생한다 (99 #43).

    🔴 **백엔드가 보낼 것과 같은 모양**이다 — 픽스처가 근거를 지어내는 것이 아니라,
    같은 계획에서 학습 이벤트와 집계를 **함께** 전개한다(둘이 갈리면 픽스처가 거짓이 된다).
    ⚠ 제출 수는 `submit_ok`에서 파생한다 — 예정이 0이면 제출도 0이다.
    """
    weeks = plan.weeks
    submits = _per_week_bool(plan.submit_ok, weeks)
    expected_counts = _per_week_int(plan.assignment_expected, weeks)
    per_week_events: dict[date, int] = {}
    for event in events:
        occurred = datetime.fromisoformat(str(event["occurred_at"])).date()
        monday = occurred - timedelta(days=occurred.weekday())
        per_week_events[monday] = per_week_events.get(monday, 0) + 1

    rows: list[dict[str, Any]] = []
    for index in range(weeks):
        monday = week_monday - timedelta(weeks=weeks - 1 - index)
        stamp = monday.isoformat()
        expected = expected_counts[index]
        rows.append(
            {
                "kind": "assignment_window",
                "source_table": "assignment_week_summary",
                "record_id": f"aws_{stamp}_{plan.student_ref}",
                "student_ref": plan.student_ref,
                "week_start": stamp,
                "expected_count": expected,
                "submitted_count": 1 if (expected > 0 and submits[index]) else 0,
            }
        )
        rows.append(
            {
                "kind": "weekly_activity",
                "source_table": "student_week_activity",
                "record_id": f"swa_{stamp}_{plan.student_ref}",
                "student_ref": plan.student_ref,
                "week_start": stamp,
                "activity_count": per_week_events.get(monday, 0),
                "enrolled_seconds": 7 * 24 * 60 * 60,
            }
        )
    if plan.status is StudentStatus.RETURNED:
        #: 복귀 학생은 **이번 주 전환 이력**이 있어야 R5가 선다(99 #43).
        rows.append(
            {
                "kind": "enrollment_transition",
                "source_table": "student_status_history",
                "record_id": f"ssh_{plan.student_ref}_{week_monday.isoformat()}",
                "student_ref": plan.student_ref,
                "occurred_at": datetime.combine(
                    week_monday, time(9, 0), tzinfo=KST
                ).isoformat(),
                "from_status": StudentStatus.PAUSED.value,
                "to_status": StudentStatus.RETURNED.value,
            }
        )
    return rows


def to_payload(request: DetectRequest) -> dict[str, Any]:
    """05 §1 형태(snake_case · ISO-8601 +09:00)의 JSON dict로 dump한다.

    백엔드·Apidog 예시 페이로드로도 재사용할 수 있다.
    """
    return request.model_dump(mode="json")


# ───────────────────────── 대표 픽스처 3종 ─────────────────────────


def fixture_stable(week_start: str = "2026-07-13", seed: int = 1) -> DetectRequest:
    """ⓐ 정상(안정 학습) — 무신호 기대. G1 성격 (08 §2)."""
    return build_detect_request(
        week_start=week_start,
        seed=seed,
        students=[
            StudentPlan(student_ref="st_stable_1", class_ref="cl_a1", weeks=10, accuracy=0.82),
            StudentPlan(student_ref="st_stable_2", class_ref="cl_a1", weeks=10, accuracy=0.78),
        ],
    )


def fixture_composite_risk(week_start: str = "2026-07-13", seed: int = 2) -> DetectRequest:
    """ⓑ 복합 위험 — 정답률 하락 + 연속 미제출 + 정답 유지·시간 급증 재료 포함.

    R1·R2·R4를 조립할 수 있는 한 학생. 기대값 판정은 detection 구현 범위다.
    """
    weeks = 10
    return build_detect_request(
        week_start=week_start,
        seed=seed,
        students=[
            StudentPlan(
                student_ref="st_risk_1",
                class_ref="cl_a1",
                weeks=weeks,
                accuracy=declining(weeks, 0.83, 0.61),
                submit_ok=intermittent(weeks, fail_from=weeks - 3),
                duration_sec=spike(weeks, base=180, peak=340, from_week=weeks - 3),
            ),
            StudentPlan(
                student_ref="st_hidden_1",
                class_ref="cl_a1",
                weeks=weeks,
                accuracy=stable(weeks, 0.81),
                duration_sec=spike(weeks, base=180, peak=350, from_week=weeks - 3),
            ),
        ],
    )


def fixture_with_history(week_start: str = "2026-07-13", seed: int = 3) -> DetectRequest:
    """ⓒ 이력 있는 학생 — alert_context로 lifecycle 3분기 재료를 담는다 (09 §4).

    - st_ongoing: 같은 유형 open 이력 → ongoing 재료
    - st_followup: 해소 후 1주(2주 이내) + 미팔로업 → follow_up 재료
    - st_new: 이력 없음 → new 재료
    """
    monday = datetime.combine(_monday_of(week_start), _EVENT_TIME, tzinfo=KST)
    resolved_recent = monday - timedelta(days=7)  # 2주 이내 — follow_up 조건
    return build_detect_request(
        week_start=week_start,
        seed=seed,
        students=[
            StudentPlan(
                student_ref="st_ongoing", class_ref="cl_a1", accuracy=declining(10, 0.8, 0.6)
            ),
            StudentPlan(
                student_ref="st_followup", class_ref="cl_a1", accuracy=declining(10, 0.8, 0.62)
            ),
            StudentPlan(student_ref="st_new", class_ref="cl_a1", accuracy=declining(10, 0.8, 0.6)),
        ],
        alert_context=[
            alert_open("st_ongoing", SignalType.ACC_DROP),
            alert_resolved("st_followup", SignalType.ACC_DROP, resolved_at=resolved_recent),
        ],
    )


# ───────────── 멀티데이 증분 추출 (D-②b read-path 테스트용) ─────────────
# 백엔드의 "day1=풀 스냅숏, day2부터=1주 증분" 전송을 재현한다 — 전체 플랜에서 특정
# 주차만 페이로드로 뽑는다. 새 이벤트를 지어내지 않고 기존 요청을 자른다(결정론 유지).


def _week_monday_of(day: date) -> date:
    return day - timedelta(days=day.weekday())


def take_week_range(
    request: DetectRequest, *, week_start: str, weeks_back: int
) -> DetectRequest:
    """week_start 주부터 과거 weeks_back주까지만 남긴 스냅숏.

    🔴 **`detection_evidence`도 같은 창으로 자른다**(99 #43) — 그것도 **판정 입력**이다.
    안 자르면 분석 주차보다 **미래인 집계**가 남아 요청이 거부된다(실측: 400).
    ⚠ 창 밖 집계를 남겨 두는 것은 *"과거 스냅숏을 재현했다"* 가 아니다.
    """
    end = _monday_of(week_start)
    start = end - timedelta(weeks=weeks_back - 1)
    events = tuple(
        event
        for event in request.learning_events
        if start <= _week_monday_of(event.occurred_at.date()) <= end
    )
    evidence = tuple(
        item
        for item in request.detection_evidence
        if start <= _evidence_week_of(item) <= end
    )
    meta = request.snapshot_meta.model_copy(update={"week_start": week_start})
    return request.model_copy(
        update={
            "snapshot_meta": meta,
            "learning_events": events,
            "detection_evidence": evidence,
        }
    )


def _evidence_week_of(item: DetectionEvidence) -> date:
    """정본 근거가 속한 주(월요일) — 집계는 `week_start`, 전환은 발생일에서 유도."""
    if isinstance(item, EnrollmentTransitionEvidence):
        return _week_monday_of(item.occurred_at.date())
    return item.week_start


def take_single_week(request: DetectRequest, *, week_start: str) -> DetectRequest:
    """week_start 주 1주분 이벤트만 남긴 증분 요청(day2+ 재현)."""
    return take_week_range(request, week_start=week_start, weeks_back=1)
