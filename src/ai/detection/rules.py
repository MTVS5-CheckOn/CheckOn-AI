"""R1~R6 판정 — 결정론 규칙 (LLM 금지, 불변식 1).

사양 원본: docs/part_a/04_threshold_config.md §1(파라미터)·§2(세그먼트)·§3.1(score 정규화).
소유: 박진희 (detection). 순수 함수 — 피처·기준선·설정을 받아 발화 결과를 반환한다.

score는 §3.1대로 [임계값, 포화값] 선형 정규화한다 — **세그먼트 완화는 발화 판정에만
적용하고 score 정규화의 기준은 원임계값**이다(경계 G11에서 score=0이 되도록).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from ai.contracts.detection import RULE_SIGNAL_MAP, RuleId, SignalType
from ai.detection.baseline import Baseline
from ai.detection.features import StudentFeatures, WeekFeatures
from ai.detection.segments import Segment, is_rule_active, threshold_multiplier
from ai.detection.thresholds import ThresholdConfig


@dataclass(frozen=True)
class RuleFinding:
    """한 규칙의 발화 결과."""

    rule_id: RuleId
    signal_type: SignalType
    score: float
    evidence_weeks: tuple[date, ...]
    """근거가 되는 주(월요일) — 엔진이 그 주의 record_id를 evidence로 변환한다."""

    detail: str = ""
    """brief·evidence 보조 문구 재료 (R6 셀 정보 등)."""


@dataclass(frozen=True)
class RuleSkip:
    """규칙을 통째로 판정하지 못한 사유 — stats.rules_skipped 재료."""

    rule_id: RuleId
    reason: str


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def _normalize(value: float, threshold: float, saturation: float) -> float:
    """value를 [threshold, saturation] 구간에서 0~1로 (§3.1)."""
    if saturation == threshold:
        return 1.0 if value >= threshold else 0.0
    return _clamp01((value - threshold) / (saturation - threshold))


def evaluate_student(
    features: StudentFeatures,
    baseline: Baseline,
    config: ThresholdConfig,
    segment: Segment,
    r1_drop_threshold_pp: float | None = None,
) -> tuple[list[RuleFinding], list[RuleSkip]]:
    """한 학생의 R1~R6 판정. 발화 목록과 skip 사유를 반환한다.

    `r1_drop_threshold_pp`는 테넌트 풀에서 산출한 R1 임계다(04 §1 발동률 목표 방식).
    미지정이면 `config.r1.drop_pp` 폴백 — 단위 테스트·단건 호출의 편의값이며, 엔진은
    항상 명시 주입한다.
    """
    findings: list[RuleFinding] = []
    skips: list[RuleSkip] = []
    r1_threshold = (
        r1_drop_threshold_pp if r1_drop_threshold_pp is not None else config.r1.drop_pp
    )

    finding, skip = _r1(features, baseline, config, segment, r1_threshold)
    if finding is not None:
        findings.append(finding)
    if skip is not None:
        skips.append(skip)

    for rule_fn in (_r2, _r3, _r4, _r6):
        finding, skip = rule_fn(features, baseline, config, segment)
        if finding is not None:
            findings.append(finding)
        if skip is not None:
            skips.append(skip)

    r5 = _r5(features, config, segment)
    if r5 is not None:
        findings.append(r5)

    return findings, skips


def _r1(
    features: StudentFeatures,
    baseline: Baseline,
    config: ThresholdConfig,
    segment: Segment,
    drop_threshold_pp: float,
) -> tuple[RuleFinding | None, RuleSkip | None]:
    """R1 정답률 하락 — 최근 consecutive_weeks 각 주가 baseline 대비 임계 이상 하락.

    임계는 **주입받는다**(04 §1 발동률 목표 방식) — 테넌트 풀 분위이거나 폴백 15%p다.
    세그먼트 완화 계수는 그대로 곱해진다(readapt ×1.3 등 — 나머지 조건 불변).
    """
    p = config.r1
    if not is_rule_active(RuleId.R1, segment) or baseline.accuracy is None:
        return None, None
    assess = features.weeks[-p.consecutive_weeks :]
    if len(assess) < p.consecutive_weeks:
        return None, None
    mult = threshold_multiplier(RuleId.R1, segment, config.segments)
    drop_threshold = drop_threshold_pp * mult
    drops: list[float] = []
    for week in assess:
        if week.accuracy is None:
            return None, None
        drop_pp = (baseline.accuracy - week.accuracy) * 100
        if drop_pp < drop_threshold:
            return None, None
        drops.append(drop_pp)
    score = _normalize(max(drops), p.drop_pp, p.saturation_drop_pp)
    return _finding(RuleId.R1, score, assess), None


def _r2(
    features: StudentFeatures, baseline: Baseline, config: ThresholdConfig, segment: Segment
) -> tuple[RuleFinding | None, RuleSkip | None]:
    """R2 연속 미제출 — 최근 주부터 submit 없는 주 연속.

    v0는 consecutive_missing 경로만 (submit_drop_pp는 제출률 분모 부재 — 04 §1 R2 · BE-10).
    """
    p = config.r2
    if not is_rule_active(RuleId.R2, segment):
        return None, None
    mult = threshold_multiplier(RuleId.R2, segment, config.segments)
    missing_threshold = round(p.consecutive_missing * mult)
    streak: list[date] = []
    for week in reversed(features.weeks):
        if week.submitted:
            break
        streak.append(week.week_monday)
    if len(streak) < missing_threshold:
        return None, None
    score = _normalize(len(streak), p.consecutive_missing, p.saturation_missing)
    return (
        RuleFinding(
            rule_id=RuleId.R2,
            signal_type=RULE_SIGNAL_MAP[RuleId.R2],
            score=score,
            evidence_weeks=tuple(reversed(streak)),
        ),
        None,
    )


def _r3(
    features: StudentFeatures, baseline: Baseline, config: ThresholdConfig, segment: Segment
) -> tuple[RuleFinding | None, RuleSkip | None]:
    """R3 학습 공백 — 최근 주 이벤트 수가 baseline 학습량의 volume_ratio 미만."""
    p = config.r3
    if not is_rule_active(RuleId.R3, segment) or baseline.volume is None:
        return None, None
    if baseline.volume < p.min_baseline_events:
        return None, None
    if not features.weeks:
        return None, None
    mult = threshold_multiplier(RuleId.R3, segment, config.segments)
    ratio_threshold = p.volume_ratio * mult
    recent = features.weeks[-1]
    actual_ratio = recent.event_count / baseline.volume
    if actual_ratio >= ratio_threshold:
        return None, None
    # deficit 방향: threshold에서 0, 완전 공백(0)에서 1
    score = _normalize(p.volume_ratio - actual_ratio, 0.0, p.volume_ratio)
    return _finding(RuleId.R3, score, (recent,)), None


def _r4(
    features: StudentFeatures, baseline: Baseline, config: ThresholdConfig, segment: Segment
) -> tuple[RuleFinding | None, RuleSkip | None]:
    """R4 숨은 위기 — 정답률 유지 + 어절 정규화 시간 급증. duration 없으면 미적용+skip."""
    p = config.r4
    if not is_rule_active(RuleId.R4, segment):
        return None, None
    assess = features.weeks[-p.consecutive_weeks :]
    if len(assess) < p.consecutive_weeks:
        return None, None
    # duration 재료 부재 판정 — 최근 주 또는 베이스라인에 정규화 시간이 없으면 R4 불가
    if any(week.norm_time is None for week in assess) or not baseline.norm_time:
        return None, RuleSkip(rule_id=RuleId.R4, reason="duration_missing")
    if baseline.accuracy is None:
        return None, None
    mult = threshold_multiplier(RuleId.R4, segment, config.segments)
    time_threshold = p.time_ratio * mult
    ratios: list[float] = []
    for week in assess:
        assert week.accuracy is not None and week.norm_time is not None
        if abs(week.accuracy - baseline.accuracy) * 100 > p.acc_stable_band_pp:
            return None, None  # 정답률 유지 조건 위반
        time_ratio = week.norm_time / baseline.norm_time
        if time_ratio < time_threshold:
            return None, None
        ratios.append(time_ratio)
    score = _normalize(max(ratios), p.time_ratio, p.saturation_time_ratio)
    return _finding(RuleId.R4, score, assess), None


def _r5(
    features: StudentFeatures, config: ThresholdConfig, segment: Segment
) -> RuleFinding | None:
    """R5 복귀 케어 — status=returned 복귀 첫 주. auto_flag(점수 무관, score=1.0)."""
    if features.status.value != "returned":
        return None
    weeks = (features.weeks[-1].week_monday,) if features.weeks else ()
    return RuleFinding(
        rule_id=RuleId.R5,
        signal_type=RULE_SIGNAL_MAP[RuleId.R5],
        score=1.0,
        evidence_weeks=weeks,
        detail="복귀 첫 주 — 점수 무관 케어 신호",
    )


def _r6(
    features: StudentFeatures, baseline: Baseline, config: ThresholdConfig, segment: Segment
) -> tuple[RuleFinding | None, RuleSkip | None]:
    """R6 오답 유형 편중 — 최근 주 area×type 한 셀에 오답 집중.

    태깅률 60% 미만이면 미적용+skip. 셀 최소 문항·절대 정답률 조건 병행.
    """
    p = config.r6
    if not is_rule_active(RuleId.R6, segment) or not features.weeks:
        return None, None
    recent = features.weeks[-1]
    if recent.n_solves == 0:
        return None, None
    if recent.tagging_rate < p.tagging_rate_min:
        return None, RuleSkip(rule_id=RuleId.R6, reason="tagging_below_60pct")
    total_wrong = sum(cell.n_wrong for cell in recent.cells)
    if total_wrong == 0:
        return None, None
    top = max(recent.cells, key=lambda c: c.n_wrong)
    share = top.n_wrong / total_wrong
    cell_acc = (top.n - top.n_wrong) / top.n if top.n else 1.0
    if share < p.cell_error_share or top.n < p.cell_min_items or cell_acc >= p.cell_acc_below:
        return None, None
    score = _normalize(share, p.cell_error_share, p.saturation_error_share)
    detail = f"{top.area.value}×{top.type.value} 오답 {top.n_wrong}/{top.n}"
    finding = RuleFinding(
        rule_id=RuleId.R6,
        signal_type=RULE_SIGNAL_MAP[RuleId.R6],
        score=score,
        evidence_weeks=(recent.week_monday,),
        detail=detail,
    )
    return finding, None


def _finding(rule_id: RuleId, score: float, weeks: tuple[WeekFeatures, ...]) -> RuleFinding:
    return RuleFinding(
        rule_id=rule_id,
        signal_type=RULE_SIGNAL_MAP[rule_id],
        score=score,
        evidence_weeks=tuple(w.week_monday for w in weeks),
    )
