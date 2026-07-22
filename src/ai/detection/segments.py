"""세그먼트 판정과 임계 계수 — 04 §2.

소유: 박진희 (detection). 순수 함수.

세그먼트별로 규칙 임계를 완화(계수 곱)하거나 미적용한다. 계수는 "발화를 어렵게" 만드는
방향이다(완화 = 임계 상향).
"""

from __future__ import annotations

from enum import StrEnum

from ai.contracts.detection import RuleId, StudentStatus, TermContext
from ai.detection.thresholds import SegmentCoefficients


class Segment(StrEnum):
    """베이스라인 세그먼트 — 04 §2."""

    NORMAL = "normal"
    READAPT = "readapt"
    """복귀 후 4주 — R1·R2·R3 완화. 단일 스냅숏에서는 status=returned로 근사한다."""

    VACATION = "vacation"
    """방학·휴강 — R2·R3 미적용(과제 자체가 없음)."""

    NEW_TERM = "new_term"
    """신학기 첫 2주 — 전 규칙 완화."""


def resolve_segment(
    status: StudentStatus,
    term_context: TermContext,
    has_return_care_history: bool = False,
) -> Segment:
    """학생 상태·학사 상황·복귀 케어 이력으로 세그먼트를 정한다 (04 §2).

    term_context(반 전체 학사)가 개인 상태보다 우선한다 — 방학·신학기는 전원 적용.

    readapt 판정 = status==returned(복귀 첫 주) OR alert_context에 return_care 이력.
    09 §2상 status=returned는 복귀 첫 주에만 찍히고, 그 주 R5 경보가 이후 30일간
    alert_context에 return_care 이력으로 남는다 — 이 이력 창(≈4주)이 "복귀 후 4주"와
    일치한다. 덕분에 복귀 첫 주(R5 발화)와 이후 재적응(완화)이 구분된다.
    """
    if term_context is TermContext.VACATION:
        return Segment.VACATION
    if term_context is TermContext.NEW_TERM:
        return Segment.NEW_TERM
    if status is StudentStatus.RETURNED or has_return_care_history:
        return Segment.READAPT
    return Segment.NORMAL


def is_rule_active(rule: RuleId, segment: Segment) -> bool:
    """이 세그먼트에서 규칙이 적용되는지 (04 §2 미적용 규칙)."""
    if segment is Segment.VACATION and rule in (RuleId.R2, RuleId.R3):
        return False
    return True


def threshold_multiplier(
    rule: RuleId, segment: Segment, coefficients: SegmentCoefficients
) -> float:
    """이 세그먼트·규칙의 임계 계수 (04 §2). 임계값에 곱해 완화한다.

    - readapt: R1·R2·R3 ×1.3
    - new_term: 전 규칙 ×1.2
    - 그 외: ×1.0
    """
    if segment is Segment.READAPT and rule in (RuleId.R1, RuleId.R2, RuleId.R3):
        return coefficients.readapt_relax
    if segment is Segment.NEW_TERM:
        return coefficients.new_term_relax
    return 1.0
