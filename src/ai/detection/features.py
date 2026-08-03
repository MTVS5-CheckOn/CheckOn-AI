"""주간 피처 추출 — DetectRequest의 learning_events를 학생별·주차별로 집계.

사양 원본: docs/part_a/02_design.md(파이프라인)·04 §1(피처 의미)·09 §2(이벤트 필드).
소유: 박진희 (detection). LLM 금지 — 순수 집계 (불변식 1).

계산과 I/O를 분리한다 (03_coding_rules.md §2): 입력 스냅숏을 받아 피처를 반환할 뿐
DB·시계를 만지지 않는다 — 골든셋 테스트가 가능한 이유다.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta

from ai.contracts.detection import DetectRequest, EventType, LearningEvent, StudentStatus
from ai.contracts.taxonomy import AreaTag, TypeTag


@dataclass(frozen=True)
class CellStat:
    """area×type 한 셀의 정오 집계 — R6 편중 판정용."""

    area: AreaTag
    type: TypeTag
    n: int
    n_wrong: int


@dataclass(frozen=True)
class WeekFeatures:
    """한 학생의 한 주 피처."""

    week_monday: date
    n_solves: int
    accuracy: float | None
    """정답률 — solve가 없으면 None."""

    submitted: bool
    """그 주 submit 이벤트가 하나라도 있었는지 (R2 연속 미제출)."""

    norm_time: float | None
    """어절 정규화 시간 평균(duration_sec/passage_word_count) — 재료 없으면 None (R4)."""

    event_count: int
    """그 주 전체 이벤트 수 (R3 학습량)."""

    tagging_rate: float
    """solve 중 area·type가 모두 태깅된 비율 (R6 태깅률 게이트)."""

    cells: tuple[CellStat, ...]
    """태깅된 solve의 area×type 셀별 집계 (R6)."""

    expected_accuracy: float | None = None
    """그 주 푼 문항들의 **기대 정답률**(지문×유형 실측 · 폴백 포함 가중 평균).

    04 §1 "기대치 입력 층". None이면 기대치 층 미적용(콜드 스타트·전량 미태깅)이며
    R1은 원 정답률 하락폭으로 판정한다 — **보정 없음과 동치**라 회귀 위험이 0이다.
    """

    @property
    def residual(self) -> float | None:
        """잔차 = 실제 − 기대. 둘 중 하나라도 없으면 None(원 정답률 경로로 떨어진다)."""
        if self.accuracy is None or self.expected_accuracy is None:
            return None
        return self.accuracy - self.expected_accuracy


@dataclass(frozen=True)
class StudentFeatures:
    """한 학생의 전 주차 피처 (과거→최근 순)."""

    student_ref: str
    class_ref: str
    status: StudentStatus
    enrolled_weeks: int
    weeks: tuple[WeekFeatures, ...]


def _monday_of(day: date) -> date:
    return day - timedelta(days=day.weekday())


def _week_features(week_monday: date, events: list[LearningEvent]) -> WeekFeatures:
    solves = [e for e in events if e.type is EventType.SOLVE]
    submits = [e for e in events if e.type is EventType.SUBMIT]

    graded = [e for e in solves if e.correct is not None]
    accuracy = (sum(1 for e in graded if e.correct) / len(graded)) if graded else None

    # duration_sec 0은 "풀이 시간 없음"으로 보고 제외한다 — 정규화 시간이 무의미.
    ratios = [
        e.duration_sec / e.passage_word_count
        for e in solves
        if e.duration_sec and e.passage_word_count
    ]
    norm_time = sum(ratios) / len(ratios) if ratios else None

    tagged = [e for e in solves if e.area_tag is not None and e.type_tag is not None]
    tagging_rate = (len(tagged) / len(solves)) if solves else 0.0

    counts: dict[tuple[AreaTag, TypeTag], list[int]] = defaultdict(lambda: [0, 0])  # [n, n_wrong]
    for event in tagged:
        assert event.area_tag is not None and event.type_tag is not None
        bucket = counts[(event.area_tag, event.type_tag)]
        bucket[0] += 1
        if event.correct is False:
            bucket[1] += 1
    ordered = sorted(counts.items(), key=lambda kv: (kv[0][0].value, kv[0][1].value))
    cells = tuple(
        CellStat(area=area, type=type_tag, n=n, n_wrong=n_wrong)
        for (area, type_tag), (n, n_wrong) in ordered
    )

    return WeekFeatures(
        week_monday=week_monday,
        n_solves=len(solves),
        accuracy=accuracy,
        submitted=len(submits) > 0,
        norm_time=norm_time,
        event_count=len(events),
        tagging_rate=tagging_rate,
        cells=cells,
    )


def extract_features(request: DetectRequest) -> dict[str, StudentFeatures]:
    """학생별 주차 피처를 집계한다. 주차는 이벤트 occurred_at의 월요일로 버킷한다.

    무동의·paused 학생의 이벤트가 딸려 와도 여기서는 집계만 하고, 폐기·제외 판정은
    파이프라인의 제외 단계가 한다(features는 사실만 만든다).
    """
    by_student_week: dict[str, dict[date, list[LearningEvent]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for event in request.learning_events:
        monday = _monday_of(event.occurred_at.date())
        by_student_week[event.student_ref][monday].append(event)

    result: dict[str, StudentFeatures] = {}
    for student in request.students:
        weeks_map = by_student_week.get(student.student_ref, {})
        weeks = tuple(
            _week_features(monday, weeks_map[monday]) for monday in sorted(weeks_map)
        )
        result[student.student_ref] = StudentFeatures(
            student_ref=student.student_ref,
            class_ref=student.class_ref,
            status=student.status,
            enrolled_weeks=student.enrolled_weeks,
            weeks=weeks,
        )
    return result


# ─────────────── baseline read-path (D-②b) — 축적분 재사용 ───────────────
# FEATURE_WEEK(파생 메트릭)에서 WeekFeatures를 되살려 요청 피처와 병합한다.
# **판정 로직·baseline 계산식은 무변경** — 이건 입력 조립의 확장이다(엔진 detect가 주입받음).


def _opt_float(value: object) -> float | None:
    return float(value) if isinstance(value, int | float) else None


def _as_int(value: object) -> int:
    if not isinstance(value, int):
        raise TypeError(f"정수 메트릭 기대, {type(value).__name__} 수신")
    return value


def _as_float(value: object) -> float:
    if not isinstance(value, int | float):
        raise TypeError(f"실수 메트릭 기대, {type(value).__name__} 수신")
    return float(value)


def week_features_from_metrics(week_monday: date, metrics: Mapping[str, object]) -> WeekFeatures:
    """FEATURE_WEEK.metrics(jsonb) → WeekFeatures. cells는 미저장이라 빈 튜플.

    baseline은 accuracy·norm_time·event_count만 쓰고 cells를 안 쓰므로 무방하다
    (R6 편중은 최근 주 판정이라 그 주 이벤트가 요청에 실려 온다).
    """
    return WeekFeatures(
        week_monday=week_monday,
        n_solves=_as_int(metrics["n_solves"]),
        accuracy=_opt_float(metrics.get("accuracy")),
        submitted=bool(metrics["submitted"]),
        norm_time=_opt_float(metrics.get("norm_time")),
        event_count=_as_int(metrics["event_count"]),
        tagging_rate=_as_float(metrics["tagging_rate"]),
        cells=(),
    )


def merge_weeks(
    stored: Sequence[WeekFeatures], current: Sequence[WeekFeatures]
) -> tuple[WeekFeatures, ...]:
    """축적 주차 ∪ 요청 주차 — 같은 (주)는 요청(오늘 수신)이 최신 승리(09 §2, dedupe와 동일 원칙).

    과거→최근으로 정렬해 반환한다. 판정 창(최근 2주)·baseline 창(직전 8주) 모두 이 병합
    타임라인을 쓴다 — 그래야 1주 증분 전송에서도 판정 창의 옆 주를 축적분이 채운다.
    """
    by_week: dict[date, WeekFeatures] = {week.week_monday: week for week in stored}
    for week in current:
        by_week[week.week_monday] = week
    return tuple(sorted(by_week.values(), key=lambda week: week.week_monday))
