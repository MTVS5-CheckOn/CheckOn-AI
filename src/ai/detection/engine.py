"""감지 파이프라인 — DetectRequest → DetectResponse (순수 함수, LLM 금지).

사양 원본: docs/part_a/02_design.md(파이프라인)·09 §3(응답)·04(임계).
소유: 박진희 (detection).

단계(02_design·04): ① 제외 ② 베이스라인 ③ 세그먼트 ④ R1~R6 ⑤ 병합
⑥ lifecycle 억제(탈락) ⑦ new·follow_up만 랭킹·상한 ⑧ ongoing·R5 상한 밖 합류 ⑨ 응답.
**억제는 랭킹보다 먼저 · 상한은 new·follow_up에만**(7/22 확정 — ongoing이 슬롯을 소비해
신규 위험을 가리지 않게). DB 저장·증분 축적·HTTP는 범위 밖.

결정론(불변식 8): signal_id는 uuid5(입력 기반)로 재현 가능하게 만든다 — 랜덤 uuid 금지.
현재 시각 미사용 — 모든 시간 기준은 snapshot_meta.week_start에서 유도한다.
meta.versions는 응답 envelope(API 계층)의 몫이며, 이 순수 함수는 data(signals·stats)만
반환한다 — 호출자가 config.threshold_version을 meta에 싣는다.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import replace
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
from ai.detection.features import WeekFeatures, extract_features, merge_weeks
from ai.detection.lifecycle import has_return_care_history, resolve_lifecycle
from ai.detection.ranking import RankedAlert, StudentAlert, merge_student, rank_class
from ai.detection.rules import evaluate_student
from ai.detection.segments import resolve_segment
from ai.detection.thresholds import ThresholdConfig, default_threshold_config

#: signal_id 결정론 생성을 위한 네임스페이스 (고정 — 재현성).
_SIGNAL_NS = uuid.UUID("00000000-0000-5000-8000-0000000d0e70")

#: 근거로 첨부할 최대 이벤트 수.
_MAX_EVIDENCE = 3


def detect(
    request: DetectRequest,
    config: ThresholdConfig | None = None,
    *,
    stored_features: Mapping[str, Sequence[WeekFeatures]] | None = None,
) -> DetectResponse:
    """감지 파이프라인 실행. 같은 입력·같은 config → 같은 출력(결정론).

    stored_features(D-②b baseline read-path): 학생별 축적 주차 피처(FEATURE_WEEK 유래).
    주입 시 요청 피처와 병합(같은 주 요청 승)해 baseline·판정 창을 구성한다 — 1주 증분
    전송을 지원한다. **미주입(기본) 시 현재 동작과 바이트 동일**(판정식 무변경, 입력 조립만
    확장). 판정 대상은 요청 students 전원이며, 조회는 라우터가 fail-closed로 수행한다.
    """
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
        if stored_features:
            stored = stored_features.get(student.student_ref)
            if stored:
                student_features = replace(
                    student_features,
                    weeks=merge_weeks(stored, student_features.weeks),
                )
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

    signals, capped_out = _rank_with_lifecycle(
        request, class_alerts, config, week_start, evidence_index
    )

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


def _rank_with_lifecycle(
    request: DetectRequest,
    class_alerts: dict[str, list[StudentAlert]],
    config: ThresholdConfig,
    week_start: date,
    evidence_index: dict[tuple[str, date], list[str]],
) -> tuple[list[Signal], int]:
    """파이프라인: 병합 → lifecycle 억제(탈락) → new·follow_up만 랭킹·상한 →
    ongoing·R5 상한 밖 합류 → 응답 (04 §3 · 09 §4, 7/22 확정).

    - 억제 후보(lifecycle=None)는 슬롯을 소비하지 않고 랭킹 이전에 탈락한다.
    - **상한 대상은 new·follow_up만**이다. ongoing(기존 카드 갱신)과 R5(정책 신호)는
      "오늘 새로 봐야 할 카드"가 아니므로 상한 밖에서 합류한다 — 만성 미해소가 슬롯을
      점유해 신규 위험을 가리는 것을 막는다. capped_out은 new·follow_up 후보 기준.
    - rank: 상한 통과분(new·follow_up) 다음에 상한 밖(ongoing·R5)을 student_ref순으로
      이어붙인다 — 기존 R5(care) 처리 방식과 일관.
    """
    signals: list[Signal] = []
    capped_out = 0
    capped_kinds = {Lifecycle.NEW, Lifecycle.FOLLOW_UP}
    for _class_ref, alerts in sorted(class_alerts.items()):
        # ① lifecycle 판정 → 억제 탈락 (랭킹 이전)
        kept: list[StudentAlert] = []
        lifecycles: dict[tuple[str, str], Lifecycle] = {}
        for alert in alerts:
            student_context = tuple(
                item
                for item in request.alert_context
                if item.student_ref == alert.student_ref
            )
            lifecycle = resolve_lifecycle(
                alert.primary.signal_type, student_context, week_start
            )
            if lifecycle is None:
                continue  # 억제 — 슬롯 미소비
            kept.append(alert)
            lifecycles[(alert.student_ref, alert.primary.rule_id.value)] = lifecycle

        # ② 상한 대상(new·follow_up의 non-R5)과 상한 밖(ongoing·R5)을 분리
        capped_pool = [
            a
            for a in kept
            if not a.is_auto_flag
            and lifecycles[(a.student_ref, a.primary.rule_id.value)] in capped_kinds
        ]
        free_pool = [
            a
            for a in kept
            if a.is_auto_flag
            or lifecycles[(a.student_ref, a.primary.rule_id.value)] is Lifecycle.ONGOING
        ]

        # ③ new·follow_up만 랭킹·상한 (capped_pool엔 R5가 없어 rank_class care는 빈다)
        result = rank_class(capped_pool, config.cap_max)
        capped_out += result.capped_out
        for ranked in result.ranked:
            key = (ranked.alert.student_ref, ranked.alert.primary.rule_id.value)
            signals.append(_build_signal(ranked, lifecycles[key], week_start, evidence_index))

        # ④ ongoing·R5 상한 밖 합류 — 통과분 뒤에 student_ref순 rank 부여
        next_rank = len(result.ranked) + 1
        for alert in sorted(free_pool, key=lambda a: a.student_ref):
            key = (alert.student_ref, alert.primary.rule_id.value)
            ranked_free = RankedAlert(alert=alert, rank=next_rank)
            signals.append(_build_signal(ranked_free, lifecycles[key], week_start, evidence_index))
            next_rank += 1
    return signals, capped_out


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
        # 부재형 신호(R2·R3·R5): 판정 창에 관련 기록이 없으면 **가장 최근** 실존 기록을
        # 맥락 근거로 인용한다 — 임의 무관(첫 매치) 대체 금지 (09 §3 A판정 7/22).
        record_ids = _latest_records(alert.student_ref, evidence_index)
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


def _build_evidence_index(request: DetectRequest) -> dict[tuple[str, date], list[str]]:
    """(student_ref, week_monday) → record_id 목록. evidence 조립용."""

    index: dict[tuple[str, date], list[str]] = defaultdict(list)
    for event in request.learning_events:
        day = event.occurred_at.date()
        monday = day - timedelta(days=day.weekday())
        index[(event.student_ref, monday)].append(event.record_id)
    return index


def _latest_records(student_ref: str, index: dict[tuple[str, date], list[str]]) -> list[str]:
    """그 학생의 **가장 최근 주**의 record_id — 부재형 신호의 맥락 근거 (09 §3 A판정)."""
    dated = [(monday, ids) for (ref, monday), ids in index.items() if ref == student_ref and ids]
    if not dated:
        return []
    _monday, ids = max(dated, key=lambda pair: pair[0])
    return ids
