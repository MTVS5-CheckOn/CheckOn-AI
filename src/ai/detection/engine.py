"""감지 파이프라인 — DetectRequest → DetectResponse (순수 함수, LLM 금지).

사양 원본: docs/part_a/02_design.md(파이프라인)·09 §3(응답)·04(임계).
소유: 박진희 (detection).

단계(02_design·04): ① 제외 처리 ② 개인 베이스라인 ③ 세그먼트 계수 ④ R1~R6 판정
⑤ 병합·상한 ⑥ lifecycle ⑦ 응답 조립. DB 저장·증분 축적·HTTP는 이번 범위 밖.

결정론(불변식 8): signal_id는 uuid5(입력 기반)로 재현 가능하게 만든다 — 랜덤 uuid 금지.
현재 시각 미사용 — 모든 시간 기준은 snapshot_meta.week_start에서 유도한다.
meta.versions는 응답 envelope(API 계층)의 몫이며, 이 순수 함수는 data(signals·stats)만
반환한다 — 호출자가 config.threshold_version을 meta에 싣는다.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import date, timedelta

from ai.contracts.detection import (
    CONSENT_GRANTED,
    DISPLAY_LABELS,
    OBSERVED_ONLY_MIN_WEEKS,
    DetectRequest,
    DetectResponse,
    DetectStats,
    EvidenceItem,
    Lifecycle,
    RuleId,
    RuleSkipped,
    Signal,
    StudentStatus,
)
from ai.detection.baseline import compute_baseline
from ai.detection.brief import build_brief
from ai.detection.features import extract_features
from ai.detection.lifecycle import has_return_care_history, resolve_lifecycle
from ai.detection.ranking import RankedAlert, StudentAlert, merge_student, rank_class
from ai.detection.rules import evaluate_student
from ai.detection.segments import resolve_segment
from ai.detection.thresholds import ThresholdConfig, default_threshold_config

#: signal_id 결정론 생성을 위한 네임스페이스 (고정 — 재현성).
_SIGNAL_NS = uuid.UUID("00000000-0000-5000-8000-0000000d0e70")

#: 근거로 첨부할 최대 이벤트 수.
_MAX_EVIDENCE = 3


def detect(request: DetectRequest, config: ThresholdConfig | None = None) -> DetectResponse:
    """감지 파이프라인 실행. 같은 입력·같은 config → 같은 출력(결정론)."""
    config = config or default_threshold_config()
    week_start = date.fromisoformat(request.snapshot_meta.week_start)
    term_context = request.snapshot_meta.term_context
    features = extract_features(request)
    evidence_index = _build_evidence_index(request)

    excluded_under_2w = 0
    evaluated = 0
    skip_counter: dict[tuple[str, str], int] = defaultdict(int)
    class_alerts: dict[str, list[StudentAlert]] = defaultdict(list)

    for student in request.students:
        # ① 제외 — 무동의는 이벤트가 딸려 와도 폐기, paused는 판정 제외
        if student.consent != CONSENT_GRANTED or student.status is StudentStatus.PAUSED:
            continue
        if student.enrolled_weeks < OBSERVED_ONLY_MIN_WEEKS:
            excluded_under_2w += 1
            continue
        evaluated += 1

        student_features = features[student.student_ref]
        baseline = compute_baseline(student_features, config.baseline_window_weeks)
        segment = resolve_segment(
            student.status,
            term_context,
            has_return_care_history(student.student_ref, request.alert_context),
        )
        findings, skips = evaluate_student(student_features, baseline, config, segment)
        for skip in skips:
            skip_counter[(skip.rule_id.value, skip.reason)] += 1
        class_alerts[student.class_ref].extend(
            merge_student(student.student_ref, student.class_ref, findings)
        )

    signals = _assemble_signals(request, class_alerts, config, week_start, evidence_index)
    capped_out = _total_capped(class_alerts, config)

    stats = DetectStats(
        students_evaluated=evaluated,
        signals_raised=len(signals),
        excluded_under_2w=excluded_under_2w,
        capped_out=capped_out,
        rules_skipped=tuple(
            RuleSkipped(rule_id=RuleId(rule_value), reason=reason, students=count)
            for (rule_value, reason), count in sorted(skip_counter.items())
        ),
    )
    return DetectResponse(signals=tuple(signals), stats=stats)


def _assemble_signals(
    request: DetectRequest,
    class_alerts: dict[str, list[StudentAlert]],
    config: ThresholdConfig,
    week_start: date,
    evidence_index: dict[tuple[str, date], list[str]],
) -> list[Signal]:
    """반별 순위·상한 적용 후 lifecycle 판정·응답 조립. 억제(None)는 제외한다."""
    signals: list[Signal] = []
    for _class_ref, alerts in sorted(class_alerts.items()):
        result = rank_class(alerts, config.cap_max)
        for ranked in result.ranked:
            student_context = tuple(
                item
                for item in request.alert_context
                if item.student_ref == ranked.alert.student_ref
            )
            lifecycle = resolve_lifecycle(
                ranked.alert.primary.signal_type, student_context, week_start
            )
            if lifecycle is None:
                continue  # 억제 — 응답에서 제외
            signals.append(
                _build_signal(ranked, lifecycle, week_start, evidence_index)
            )
    return signals


def _build_signal(
    ranked: RankedAlert,
    lifecycle: Lifecycle,
    week_start: date,
    evidence_index: dict[tuple[str, date], list[str]],
) -> Signal:
    alert = ranked.alert
    primary = alert.primary
    signal_type = primary.signal_type

    record_ids: list[str] = []
    for finding in alert.merged:
        for week_monday in finding.evidence_weeks:
            record_ids.extend(evidence_index.get((alert.student_ref, week_monday), []))
    if not record_ids:
        # 발화 주에 record_id가 없으면 그 학생의 아무 이벤트로 근거를 채운다(evidence 필수).
        record_ids = _any_records(alert.student_ref, evidence_index)
    unique_ids = list(dict.fromkeys(record_ids))[:_MAX_EVIDENCE]

    evidence = tuple(
        EvidenceItem(
            source_table="learning_event",
            record_id=record_id,
            summary=f"{signal_type.value} 근거 기록",
        )
        for record_id in unique_ids
    )

    name = f"{alert.student_ref}:{primary.rule_id.value}:{week_start.isoformat()}"
    signal_id = str(uuid.uuid5(_SIGNAL_NS, name))
    return Signal(
        signal_id=signal_id,
        student_ref=alert.student_ref,
        class_ref=alert.class_ref,
        rule_id=primary.rule_id,
        signal_type=signal_type,
        display_label=DISPLAY_LABELS[signal_type],
        score=primary.score,
        rank=ranked.rank,
        lifecycle=lifecycle,
        brief=build_brief(signal_type, primary.detail),
        evidence=evidence,
    )


def _total_capped(class_alerts: dict[str, list[StudentAlert]], config: ThresholdConfig) -> int:
    return sum(rank_class(alerts, config.cap_max).capped_out for alerts in class_alerts.values())


def _build_evidence_index(request: DetectRequest) -> dict[tuple[str, date], list[str]]:
    """(student_ref, week_monday) → record_id 목록. evidence 조립용."""

    index: dict[tuple[str, date], list[str]] = defaultdict(list)
    for event in request.learning_events:
        day = event.occurred_at.date()
        monday = day - timedelta(days=day.weekday())
        index[(event.student_ref, monday)].append(event.record_id)
    return index


def _any_records(student_ref: str, index: dict[tuple[str, date], list[str]]) -> list[str]:
    for (ref, _monday), ids in index.items():
        if ref == student_ref and ids:
            return ids
    return []
