"""위험신호 대표·경계·병합·생애주기 시나리오 팩토리 (지시서 79 §3).

🔴 **`Signal`을 손으로 만들지 않는다.** 전부 `DetectRequest` 입력에서 시작해 **실제 감지
엔진**을 통과한다 — 손으로 만든 신호는 *"엔진이 그렇게 판정한다"* 를 증명하지 않는다.

⚠ **임계값을 여기 복제하지 않는다.** 경계 시나리오는 `default_threshold_config()`에서
읽어 파생한다(03 §1 — 값이 바뀌면 코드 diff가 생기면 위치가 틀린 것). 설정을 낮춘
테넌트에서 픽스처만 낡는 것을 막는다.

**입력 파생 규약**(`detection/features.py`·`baseline.py` 실측):

| 지표 | 어디서 오는가 |
| --- | --- |
| `accuracy` | 그 주 `solve` 중 `correct`가 있는 것들의 정답 비율 |
| `norm_time` | `duration_sec / passage_word_count` 평균 |
| `submitted` | `submit` 이벤트 유무 |
| 기준선 | **최근 2주를 뺀**(`ASSESSMENT_WEEKS`) 직전 `baseline_window_weeks`주 평균 |
| R2·R3·R5 | 🔴 `detection_evidence` 정본 집계 — 없으면 **판정 자체를 안 한다** |

⚠ **R1 임계는 풀이 작으면 고정 폴백**이다(`quantile_min_pool=100`) — 이 픽스처는 학생 수가
적어 항상 `config.r1.drop_pp`가 쓰인다. 그래서 경계 계산이 결정론이다.

⚠ **모든 참조는 alias**다. 실명·연락처·학교명 0건(불변식 3 — 픽스처도 경계 밖이다).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any, Final

from ai.contracts.detection import DetectRequest
from ai.detection.baseline import ASSESSMENT_WEEKS
from ai.detection.thresholds import default_threshold_config

#: 고정 분석 주(월요일) — 시계·난수를 쓰지 않는다(03 §3).
ANALYSIS_WEEK: Final = date(2026, 7, 20)

#: 기준선이 성립하는 최소 이력 = 판정 창 + 기준 창.
_CONFIG: Final = default_threshold_config()
BASELINE_WEEKS: Final = _CONFIG.baseline_window_weeks
HISTORY_WEEKS: Final = BASELINE_WEEKS + ASSESSMENT_WEEKS

#: ⚠ 태깅률 100%를 만들기 위한 기본 태그 — R6이 아닌 시나리오에서도 무해하다.
_AREA: Final = "reading"
_TYPE: Final = "infer"


def week_of(back: int) -> date:
    """분석 주에서 `back`주 전 월요일."""
    return ANALYSIS_WEEK - timedelta(weeks=back)


def _at(day: date, hour: int = 19) -> str:
    return datetime(day.year, day.month, day.day, hour, tzinfo=UTC).isoformat()


@dataclass
class _Ids:
    """레코드 id 발급기 — 시나리오 안에서 유일하고 결정론이다."""

    prefix: str
    seq: int = 0

    def next(self) -> str:
        self.seq += 1
        return f"{self.prefix}_{self.seq}"


@dataclass
class ScenarioBuilder:
    """한 시나리오의 `DetectRequest`를 조립한다.

    🔴 **엔진을 우회하지 않는다** — 이벤트·집계·이력만 넣고 판정은 엔진에 맡긴다.
    """

    scenario: str
    class_ref: str = "cl_s1"
    students: list[dict[str, Any]] = field(default_factory=list)
    learning_events: list[dict[str, Any]] = field(default_factory=list)
    alert_context: list[dict[str, Any]] = field(default_factory=list)
    detection_evidence: list[dict[str, Any]] = field(default_factory=list)
    _ids: _Ids = field(init=False)

    def __post_init__(self) -> None:
        self._ids = _Ids(f"le_{self.scenario}")

    # ── 학생 ──

    def student(
        self,
        ref: str,
        *,
        status: str = "enrolled",
        enrolled_weeks: int = 20,
        class_ref: str | None = None,
    ) -> ScenarioBuilder:
        self.students.append(
            {
                "student_ref": ref,
                "class_ref": class_ref or self.class_ref,
                "enrolled_weeks": enrolled_weeks,
                "status": status,
                "consent": "granted",
            }
        )
        return self

    # ── 학습 이벤트 ──

    def solves(
        self,
        ref: str,
        *,
        back: int,
        n: int,
        correct: int,
        seconds: int = 100,
        words: int = 500,
        area: str = _AREA,
        type_tag: str = _TYPE,
    ) -> ScenarioBuilder:
        """그 주에 `solve` `n`건 중 `correct`건 정답.

        🔴 **비율이 아니라 정수 카운트를 받는다**(2026-08-12 실측). `accuracy=0.88-0.15`처럼
        실수로 주면 `0.73`이 이진수로 정확히 표현되지 않아 **경계가 흐려진다** —
        엔진은 `correct/n`을 그대로 쓰므로 정수를 주는 쪽이 계산과 같은 축이다.

        ⚠ `norm_time = seconds / words`도 같은 이유로 **정수 두 개**로 준다 —
        `0.2 * 1.5`는 `0.30000000000000004`이고 그 비율은 `1.4999999999999998`이라
        「정확히 1.5배」가 **발화하지 않는다**(실측). 이분수(`200/800`·`300/800`)를 쓰면
        비율이 정확히 `1.5`다.
        """
        assert 0 <= correct <= n, f"correct={correct} n={n}"
        day = week_of(back)
        for index in range(n):
            self.learning_events.append(
                {
                    "record_id": self._ids.next(),
                    "student_ref": ref,
                    "type": "solve",
                    "occurred_at": _at(day + timedelta(days=index % 5)),
                    "correct": index < correct,
                    "duration_sec": seconds,
                    "passage_word_count": words,
                    "area_tag": area,
                    "type_tag": type_tag,
                    "item_format": "mcq",
                    "source": "trackB",
                }
            )
        return self

    def submit(self, ref: str, *, back: int) -> ScenarioBuilder:
        self.learning_events.append(
            {
                "record_id": self._ids.next(),
                "student_ref": ref,
                "type": "submit",
                "occurred_at": _at(week_of(back)),
                "source": "trackA",
            }
        )
        return self

    def steady_history(
        self,
        ref: str,
        *,
        n: int = 20,
        correct: int = 17,
        seconds: int = 100,
        words: int = 500,
        weeks: int | None = None,
        skip_recent: int = 0,
    ) -> ScenarioBuilder:
        """기준선을 만드는 과거 주들 — 판정 창(최근 `skip_recent`주)은 비운다."""
        span = weeks if weeks is not None else HISTORY_WEEKS
        for back in range(skip_recent, span):
            self.solves(
                ref, back=back, n=n, correct=correct, seconds=seconds, words=words
            )
        return self

    # ── 정본 집계 근거 ──

    def assignment(
        self, ref: str, *, back: int, expected: int, submitted: int
    ) -> ScenarioBuilder:
        week = week_of(back)
        self.detection_evidence.append(
            {
                "kind": "assignment_window",
                "student_ref": ref,
                "week_start": week.isoformat(),
                "source_table": "assignment_week_summary",
                "record_id": f"aw_{ref}_{week.isoformat()}",
                "expected_count": expected,
                "submitted_count": submitted,
            }
        )
        return self

    def activity(self, ref: str, *, back: int, count: int) -> ScenarioBuilder:
        week = week_of(back)
        self.detection_evidence.append(
            {
                "kind": "weekly_activity",
                "student_ref": ref,
                "week_start": week.isoformat(),
                "source_table": "student_week_activity",
                "record_id": f"wa_{ref}_{week.isoformat()}",
                "activity_count": count,
            }
        )
        return self

    def activity_series(
        self, ref: str, *, counts: dict[int, int], baseline: int, weeks: int | None = None
    ) -> ScenarioBuilder:
        """기준창 전 주 + 분석 주 집계 — `counts`에 준 주만 값을 바꾼다.

        🔴 **한 주라도 빠지면 R3는 판정하지 않는다**(`authoritative_evidence_missing`) —
        그래서 기본은 전 주를 채우고, 부재 시나리오만 명시로 뺀다.
        """
        span = weeks if weeks is not None else BASELINE_WEEKS
        for back in range(span + 1):
            if back in counts and counts[back] < 0:
                continue  # 명시 부재 — 그 주 집계를 넣지 않는다
            self.activity(ref, back=back, count=counts.get(back, baseline))
        return self

    def returned(self, ref: str, *, back: int = 0) -> ScenarioBuilder:
        week = week_of(back)
        self.detection_evidence.append(
            {
                "kind": "enrollment_transition",
                "student_ref": ref,
                "occurred_at": _at(week + timedelta(days=1), hour=10),
                "source_table": "student_status_history",
                "record_id": f"st_{ref}_{week.isoformat()}",
                "from_status": "paused",
                "to_status": "returned",
            }
        )
        return self

    # ── 생애주기 이력 ──

    def history(
        self,
        ref: str,
        *,
        signal_type: str,
        status: str,
        resolved_days_ago: int | None = None,
        followed_up: bool = False,
    ) -> ScenarioBuilder:
        item: dict[str, Any] = {
            "student_ref": ref,
            "signal_type": signal_type,
            "status": status,
            "followed_up": followed_up,
        }
        if resolved_days_ago is not None:
            item["resolved_at"] = _at(ANALYSIS_WEEK - timedelta(days=resolved_days_ago))
        self.alert_context.append(item)
        return self

    # ── 조립 ──

    def build(self, *, term_context: str = "normal") -> DetectRequest:
        classes = sorted({s["class_ref"] for s in self.students})
        return DetectRequest.model_validate(
            {
                "snapshot_meta": {
                    "week_start": ANALYSIS_WEEK.isoformat(),
                    #: ⚠ 통신 fixture 선언값이다 — canonical 검증값으로 쓰지 않는다.
                    "snapshot_hash": f"sha256:scenario-{self.scenario}",
                    "term_context": term_context,
                    "classes": [{"class_ref": ref} for ref in classes],
                },
                "students": self.students,
                "learning_events": self.learning_events,
                "alert_context": self.alert_context,
                "detection_evidence": self.detection_evidence,
            }
        )


def builder(scenario: str, *, class_ref: str = "cl_s1") -> ScenarioBuilder:
    return ScenarioBuilder(scenario=scenario, class_ref=class_ref)


__all__ = [
    "ANALYSIS_WEEK",
    "BASELINE_WEEKS",
    "HISTORY_WEEKS",
    "ScenarioBuilder",
    "builder",
    "week_of",
]
