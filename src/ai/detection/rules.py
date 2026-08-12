"""R1~R6 판정 — 결정론 규칙 (LLM 금지, 불변식 1).

사양 원본: docs/part_a/04_threshold_config.md §1(파라미터)·§2(세그먼트)·§3.1(score 정규화).
소유: 박진희 (detection). 순수 함수 — 피처·기준선·설정을 받아 발화 결과를 반환한다.

score는 §3.1대로 [임계값, 포화값] 선형 정규화한다 — **세그먼트 완화는 발화 판정에만
적용하고 score 정규화의 기준은 원임계값**이다(경계 G11에서 score=0이 되도록).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from ai.contracts.detection import (
    RULE_SIGNAL_MAP,
    RuleId,
    SignalType,
    StudentStatus,
)
from ai.detection.baseline import Baseline
from ai.detection.evidence import (
    EMPTY_EVIDENCE,
    SKIP_AUTHORITATIVE_EVIDENCE_MISSING,
    StudentEvidence,
)
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
    evidence: StudentEvidence | None = None,
) -> tuple[list[RuleFinding], list[RuleSkip]]:
    """한 학생의 R1~R6 판정. 발화 목록과 skip 사유를 반환한다.

    `r1_drop_threshold_pp`는 테넌트 풀에서 산출한 R1 임계다(04 §1 발동률 목표 방식).
    미지정이면 `config.r1.drop_pp` 폴백 — 단위 테스트·단건 호출의 편의값이며, 엔진은
    항상 명시 주입한다.

    🔴 **`evidence`는 R2·R3·R5의 정본 입력이다**(99 #43). 없으면 그 셋은 **발화하지 않고**
    `authoritative_evidence_missing`으로 skip된다 — 다른 기록을 근거로 삼지 않는다.
    ⚠ R1·R4·R6는 이 인자를 안 본다(학습 기록 자체가 근거다).
    """
    findings: list[RuleFinding] = []
    skips: list[RuleSkip] = []
    student_evidence = evidence if evidence is not None else EMPTY_EVIDENCE
    r1_threshold = (
        r1_drop_threshold_pp if r1_drop_threshold_pp is not None else config.r1.drop_pp
    )

    finding, skip = _r1(features, baseline, config, segment, r1_threshold)
    if finding is not None:
        findings.append(finding)
    if skip is not None:
        skips.append(skip)

    for rule_fn in (_r4, _r6):
        finding, skip = rule_fn(features, baseline, config, segment)
        if finding is not None:
            findings.append(finding)
        if skip is not None:
            skips.append(skip)

    #: 🔴 **부재형 셋은 정본 근거를 받는다**(99 #43) — 시그니처가 달라 위 루프와 안 섞는다.
    for absence_fn in (_r2, _r3, _r5):
        finding, skip = absence_fn(features, baseline, config, segment, student_evidence)
        if finding is not None:
            findings.append(finding)
        if skip is not None:
            skips.append(skip)

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
    # 판정 시리즈 — 잔차가 서면 잔차, 아니면 원 정답률(04 §1 기대치 입력 층).
    # 판정 창 **전체**에 잔차가 있어야 잔차 경로다(주마다 섞이면 의미가 흔들린다).
    use_residual = baseline.residual is not None and all(
        week.residual is not None for week in assess
    )
    drops: list[float] = []
    for week in assess:
        if use_residual:
            assert baseline.residual is not None and week.residual is not None
            drop_pp = (baseline.residual - week.residual) * 100
        else:
            if week.accuracy is None:
                return None, None
            drop_pp = (baseline.accuracy - week.accuracy) * 100
        if drop_pp < drop_threshold:
            return None, None
        drops.append(drop_pp)
    score = _normalize(max(drops), p.drop_pp, p.saturation_drop_pp)
    return _finding(RuleId.R1, score, assess), None


def _r2(
    features: StudentFeatures,
    baseline: Baseline,
    config: ThresholdConfig,
    segment: Segment,
    evidence: StudentEvidence,
) -> tuple[RuleFinding | None, RuleSkip | None]:
    """R2 연속 미제출 — 🔴 **과제 주차 집계에서 파생한다**(99 #43).

    종전에는 `WeekFeatures.submitted: bool` 하나만 봤다 — 그 값으로는
    *"과제가 있었는데 안 냈다"* 와 *"과제가 없었다"* 가 **구분되지 않는다.**

    연속 미제출 한 주 = `expected_count > 0 AND submitted_count == 0`.
    ⚠ `expected_count == 0`인 주는 **연속에서 제외**한다(방학·휴강 — 미제출이 아니다).
    ⚠ 일부 제출(`submitted_count > 0`)은 **연속을 끊는다** — 제출률 하락 경로는 분모 계약이
    서기 전까지 열지 않는다(04 §1 R2 · BE-10).

    v0는 consecutive_missing 경로만.
    """
    p = config.r2
    if not is_rule_active(RuleId.R2, segment):
        return None, None
    if not features.weeks:
        return None, None
    if not evidence.assignment_windows:
        #: 🔴 집계가 하나도 없으면 **판정 자체를 안 한다** — 0으로 간주하지 않는다.
        return None, RuleSkip(
            rule_id=RuleId.R2, reason=SKIP_AUTHORITATIVE_EVIDENCE_MISSING
        )
    mult = threshold_multiplier(RuleId.R2, segment, config.segments)
    missing_threshold = round(p.consecutive_missing * mult)
    streak: list[date] = []
    for week in reversed(features.weeks):
        window = evidence.assignment_windows.get(week.week_monday)
        if window is None:
            break  # 그 주 집계가 없다 — 연속을 이어 붙일 근거가 없다
        if window.expected_count == 0:
            continue  # 과제가 없던 주 — 미제출이 아니라 **연속에서 제외**
        if window.submitted_count > 0:
            break  # 냈다 — 연속 종료
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
    features: StudentFeatures,
    baseline: Baseline,
    config: ThresholdConfig,
    segment: Segment,
    evidence: StudentEvidence,
) -> tuple[RuleFinding | None, RuleSkip | None]:
    """R3 학습 공백 — 🔴 **주간 학습량 집계**가 판정값이다(99 #43).

    ⚠ **집계가 없으면 0으로 간주하지 않는다** — 「기록이 없다」와 「집계가 0이다」는 다른
    사실이고 앞은 증명할 수 없다. `learning_events` 개수와 조용히 섞지도 않는다.
    ⚠ 0건도 실존 레코드이므로 **evidence가 존재한다**.
    """
    p = config.r3
    if not is_rule_active(RuleId.R3, segment) or baseline.volume is None:
        return None, None
    if not features.weeks:
        return None, None
    recent = features.weeks[-1]
    activity = evidence.weekly_activity.get(recent.week_monday)
    if activity is None:
        return None, RuleSkip(
            rule_id=RuleId.R3, reason=SKIP_AUTHORITATIVE_EVIDENCE_MISSING
        )
    #: 🔴 **분자와 분모를 같은 자로 잰다**(99 #43) — 현재 주는 집계, baseline은 학습 이벤트
    #:   개수로 재면 **두 다른 측정이 비교**된다. 증분 전송에서는 이벤트 수가 집계보다
    #:   작을 수 있어 비율이 조용히 부풀거나 꺼진다.
    #: ⚠ baseline 창의 집계가 하나라도 없으면 **판정하지 않는다** — 섞느니 안 한다.
    prior = features.weeks[-1 - config.baseline_window_weeks : -1]
    prior_counts = [
        evidence.weekly_activity[week.week_monday].activity_count
        for week in prior
        if week.week_monday in evidence.weekly_activity
    ]
    if not prior or len(prior_counts) != len(prior):
        return None, RuleSkip(
            rule_id=RuleId.R3, reason=SKIP_AUTHORITATIVE_EVIDENCE_MISSING
        )
    baseline_volume = sum(prior_counts) / len(prior_counts)
    if baseline_volume < p.min_baseline_events:
        return None, None
    mult = threshold_multiplier(RuleId.R3, segment, config.segments)
    ratio_threshold = p.volume_ratio * mult
    actual_ratio = activity.activity_count / baseline_volume
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
    features: StudentFeatures,
    baseline: Baseline,
    config: ThresholdConfig,
    segment: Segment,
    evidence: StudentEvidence,
) -> tuple[RuleFinding | None, RuleSkip | None]:
    """R5 복귀 케어 — 🔴 **상태 전환 이력**이 있어야 발화한다(99 #43).

    종전에는 `status == returned` 하나로 발화했다. 상태 필드는 **현재 값**이라
    *"언제 바뀌었는가"* 를 말하지 않는다 — 복귀 케어는 **전환 사실**로 발화하는 신호다.

    조건 전부: ⓐ `status == returned` ⓑ 같은 학생의 `to_status == returned` 이력
    ⓒ 전환 주 == 분석 주 ⓓ 레코드 1건 이상. ⚠ 다른 주의 복귀는 이번 주 신호가 아니다.
    """
    del baseline, config, segment
    if features.status is not StudentStatus.RETURNED:
        return None, None
    if not features.weeks:
        return None, None
    week_monday = features.weeks[-1].week_monday
    if not evidence.returns_in_week(week_monday):
        #: 상태만 returned이고 이력이 없다 — 발화하지 않고 사유를 남긴다.
        return None, RuleSkip(
            rule_id=RuleId.R5, reason=SKIP_AUTHORITATIVE_EVIDENCE_MISSING
        )
    return (
        RuleFinding(
            rule_id=RuleId.R5,
            signal_type=RULE_SIGNAL_MAP[RuleId.R5],
            score=1.0,
            evidence_weeks=(week_monday,),
            detail="복귀 첫 주 — 점수 무관 케어 신호",
        ),
        None,
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
