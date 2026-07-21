"""감지 골든셋 G1~G12 — 08_evaluation_plan.md §2 시나리오.

각 시나리오 = 8~12주 FakeSnapshot + 기대 발화/미발화. threshold 시트 v0(default-v1)이
기대값의 기준이다 — 시트(04)가 개정되면 이 골든셋도 함께 개정한다(08 §8 버전 연동).

기대는 최종 응답 signals에 나타난 signal_type 집합으로 검증한다. G12(복합)는 학생당
1경보 병합이라 대표 signal_type 1건만 남는다(발화 규칙 검증은 test_engine이 담당).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from ai.contracts.detection import (
    DetectRequest,
    SignalType,
    StudentStatus,
    TermContext,
)
from ai.contracts.taxonomy import AreaTag, TypeTag
from ai.evaluation.fake_snapshot import (
    StudentPlan,
    alert_resolved,
    build_detect_request,
)

WEEK_START = "2026-07-13"
_KST = ZoneInfo("Asia/Seoul")


@dataclass(frozen=True)
class GoldenScenario:
    """골든 시나리오 1건 — 08 §2 표의 한 행."""

    case_id: str
    note: str
    request: DetectRequest
    expected_fired: frozenset[str] = field(default_factory=frozenset)
    """응답 signals에 나타나야 할 signal_type 값 (빈 집합 = 무신호)."""

    expect_single_merged: bool = False
    """G12 — 복수 규칙이 학생당 1경보로 병합됐는지 추가 검증."""


def _monday() -> datetime:
    from datetime import date

    day = date.fromisoformat(WEEK_START)
    return datetime(day.year, day.month, day.day, tzinfo=_KST)


def _req(students: list[StudentPlan], **kwargs: object) -> DetectRequest:
    return build_detect_request(week_start=WEEK_START, seed=1, students=students, **kwargs)  # type: ignore[arg-type]


def g1_normal() -> GoldenScenario:
    """G1 정상(안정 학습) — 발화 0 (오탐 0)."""
    return GoldenScenario(
        case_id="G1",
        note="정상 — 안정 학습",
        request=_req(
            [StudentPlan(student_ref="st_g1", class_ref="cl_a1", weeks=10, accuracy=0.80)]
        ),
    )


def g2_acc_drop() -> GoldenScenario:
    """G2 명시적 하락 — R1."""
    acc = (0.8,) * 8 + (0.60, 0.60)
    return GoldenScenario(
        case_id="G2",
        note="정답률 2주 급락 → R1",
        request=_req(
            [
                StudentPlan(
                    student_ref="st_g2",
                    class_ref="cl_a1",
                    weeks=10,
                    solves_per_week=20,
                    accuracy=acc,
                )
            ]
        ),
        expected_fired=frozenset({SignalType.ACC_DROP.value}),
    )


def g3_submit_drop() -> GoldenScenario:
    """G3 제출 포기(3연속 미제출) — R2."""
    return GoldenScenario(
        case_id="G3",
        note="3주 연속 미제출 → R2",
        request=_req(
            [
                StudentPlan(
                    student_ref="st_g3",
                    class_ref="cl_a1",
                    weeks=10,
                    submit_ok=(True,) * 7 + (False, False, False),
                )
            ]
        ),
        expected_fired=frozenset({SignalType.SUBMIT_DROP.value}),
    )


def g4_volume_gap() -> GoldenScenario:
    """G4 잠수(학습량 급감) — R3."""
    volume = (12,) * 9 + (3,)
    return GoldenScenario(
        case_id="G4",
        note="학습량 급감 → R3",
        request=_req(
            [
                StudentPlan(
                    student_ref="st_g4",
                    class_ref="cl_a1",
                    weeks=10,
                    solves_per_week=volume,
                    accuracy=0.80,
                )
            ]
        ),
        expected_fired=frozenset({SignalType.VOLUME_GAP.value}),
    )


def g5_hidden_risk() -> GoldenScenario:
    """G5 숨은 위기(정답률 유지 + 시간 급증) — R4 (제품 핵심)."""
    dur = (180,) * 8 + (300, 320)
    return GoldenScenario(
        case_id="G5",
        note="정답률 유지 + 정규화 시간 급증 → R4",
        request=_req(
            [
                StudentPlan(
                    student_ref="st_g5",
                    class_ref="cl_a1",
                    weeks=10,
                    solves_per_week=12,
                    accuracy=0.80,
                    duration_sec=dur,
                    passage_word_count=800,
                )
            ]
        ),
        expected_fired=frozenset({SignalType.HIDDEN_RISK.value}),
    )


def g6_readapt_silent() -> GoldenScenario:
    """G6 재적응 오탐 방지 — return_care 이력으로 readapt, R1 미발화(완화)."""
    acc = (0.8,) * 8 + (0.63, 0.63)  # 17%p 하락(원임계 15 초과, 완화 19.5 미만)
    resolved_at = _monday() - timedelta(days=14)
    return GoldenScenario(
        case_id="G6",
        note="복귀 재적응 완만 하락 → R1 미발화(readapt ×1.3)",
        request=_req(
            [
                StudentPlan(
                    student_ref="st_g6",
                    class_ref="cl_a1",
                    weeks=10,
                    solves_per_week=100,
                    accuracy=acc,
                )
            ],
            alert_context=[
                alert_resolved(
                    "st_g6", SignalType.RETURN_CARE, resolved_at=resolved_at, followed_up=True
                )
            ],
        ),
    )


def g7_vacation_silent() -> GoldenScenario:
    """G7 방학 오탐 방지 — vacation에서 R2·R3 미적용."""
    return GoldenScenario(
        case_id="G7",
        note="방학 무제출·학습량 급감 → R2·R3 미발화",
        request=_req(
            [
                StudentPlan(
                    student_ref="st_g7",
                    class_ref="cl_a1",
                    weeks=10,
                    solves_per_week=(12,) * 9 + (3,),
                    submit_ok=(True,) * 7 + (False, False, False),
                    accuracy=0.80,
                )
            ],
            term_context=TermContext.VACATION,
        ),
    )


def g8_return_care() -> GoldenScenario:
    """G8 복귀 첫 주 — R5 (점수 무관)."""
    return GoldenScenario(
        case_id="G8",
        note="복귀 첫 주 → R5",
        request=_req(
            [
                StudentPlan(
                    student_ref="st_g8",
                    class_ref="cl_a1",
                    weeks=10,
                    status=StudentStatus.RETURNED,
                )
            ]
        ),
        expected_fired=frozenset({SignalType.RETURN_CARE.value}),
    )


def g9_type_bias() -> GoldenScenario:
    """G9 유형 편중(문학×추론) — R6."""
    return GoldenScenario(
        case_id="G9",
        note="문학×추론 오답 집중(12문항) → R6",
        request=_req(
            [
                StudentPlan(
                    student_ref="st_g9",
                    class_ref="cl_a1",
                    weeks=10,
                    solves_per_week=24,
                    accuracy=0.80,
                    bias=(AreaTag.LITERATURE, TypeTag.INFER),
                    bias_accuracy=0.30,
                )
            ]
        ),
        expected_fired=frozenset({SignalType.TYPE_BIAS.value}),
    )


def g10_bias_low_sample() -> GoldenScenario:
    """G10 표본 부족 편중 — R6 미발화(cell_min_items 미달)."""
    return GoldenScenario(
        case_id="G10",
        note="같은 편중이나 6문항 → R6 미발화",
        request=_req(
            [
                StudentPlan(
                    student_ref="st_g10",
                    class_ref="cl_a1",
                    weeks=10,
                    solves_per_week=12,
                    accuracy=0.80,
                    bias=(AreaTag.LITERATURE, TypeTag.INFER),
                    bias_accuracy=0.30,
                )
            ]
        ),
    )


def g11_boundary() -> GoldenScenario:
    """G11 경계값 — R1 drop 정확히 15.0%p 발화(≥)."""
    acc = (0.8,) * 8 + (0.65, 0.65)
    return GoldenScenario(
        case_id="G11",
        note="−15.0%p 정확 → R1 발화(≥)",
        request=_req(
            [
                StudentPlan(
                    student_ref="st_g11",
                    class_ref="cl_a1",
                    weeks=10,
                    solves_per_week=20,
                    accuracy=acc,
                )
            ]
        ),
        expected_fired=frozenset({SignalType.ACC_DROP.value}),
    )


def g12_composite() -> GoldenScenario:
    """G12 복합(R1+R2+R4) — 1경보로 병합, score=max."""
    acc = (0.8,) * 8 + (0.50, 0.50)
    dur = (180,) * 8 + (300, 320)
    return GoldenScenario(
        case_id="G12",
        note="R1+R2+R4 동시 → 1경보 병합",
        request=_req(
            [
                StudentPlan(
                    student_ref="st_g12",
                    class_ref="cl_a1",
                    weeks=10,
                    solves_per_week=20,
                    accuracy=acc,
                    submit_ok=(True,) * 7 + (False, False, False),
                    duration_sec=dur,
                    passage_word_count=800,
                )
            ]
        ),
        expect_single_merged=True,
    )


def all_scenarios() -> list[GoldenScenario]:
    """G1~G12 전체."""
    return [
        g1_normal(),
        g2_acc_drop(),
        g3_submit_drop(),
        g4_volume_gap(),
        g5_hidden_risk(),
        g6_readapt_silent(),
        g7_vacation_silent(),
        g8_return_care(),
        g9_type_bias(),
        g10_bias_low_sample(),
        g11_boundary(),
        g12_composite(),
    ]
