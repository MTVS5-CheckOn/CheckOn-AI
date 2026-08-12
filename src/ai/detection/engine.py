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

import logging
import uuid
from collections import Counter, defaultdict
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
    StudentInput,
    StudentStatus,
)
from ai.detection.baseline import Baseline, compute_baseline
from ai.detection.brief import build_brief
from ai.detection.evidence import (
    EMPTY_EVIDENCE,
    EvidenceRequest,
    StudentEvidence,
    build_student_evidence,
    resolve_evidence,
)
from ai.detection.features import (
    StudentFeatures,
    WeekFeatures,
    extract_features,
    merge_weeks,
)
from ai.detection.lifecycle import has_return_care_history, resolve_lifecycle
from ai.detection.quantile import (
    drop_series,
    resolve_drop_threshold,
)
from ai.detection.ranking import RankedAlert, StudentAlert, merge_student, rank_class
from ai.detection.rules import RuleFinding, evaluate_student
from ai.detection.segments import Segment, resolve_segment
from ai.detection.thresholds import ThresholdConfig, default_threshold_config

#: signal_id 결정론 생성을 위한 네임스페이스 (고정 — 재현성).
_SIGNAL_NS = uuid.UUID("00000000-0000-5000-8000-0000000d0e70")

logger = logging.getLogger(__name__)

#: 근거로 첨부할 최대 이벤트 수.
_MAX_EVIDENCE = 3

#: 근거 전무로 신호를 만들지 못한 경우의 스킵 사유(09 §3 ②) — stats.rules_skipped.reason.
#: ⚠ **정본 근거 부재(`authoritative_evidence_missing`)와 다른 값이다** — 이쪽은
#: "판정은 됐는데 인용할 기록이 없다"이고, 그쪽은 "판정 자체를 못 했다"(99 #43).
_SKIP_EVIDENCE_ABSENT = "evidence_absent"


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
    #: 🔴 R2·R3·R5의 **정본 근거**(99 #43) — 없으면 그 셋은 판정하지 않는다.
    student_evidence = build_student_evidence(request)

    excluded_under_2w = 0
    evaluated = 0
    skip_counter: dict[tuple[str, str], int] = defaultdict(int)
    class_alerts: dict[str, list[StudentAlert]] = defaultdict(list)

    # ①' 판정 대상을 먼저 확정한다 — R1 분위 임계가 **테넌트 풀 전체**를 입력으로 쓰므로
    #     학생별 판정보다 앞서야 한다(04 §1 발동률 목표 방식). 피처·baseline은 여기서
    #     한 번만 만들어 판정 단계에서 재사용한다(중복 계산 없음).
    prepared: list[tuple[StudentInput, StudentFeatures, Baseline, Segment]] = []
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
        prepared.append((student, student_features, baseline, segment))

    # ①'' R1 임계 산출 — 풀 = 베이스라인 창 × 전 학생의 주간 하락폭(13 §4-1과 같은 모양).
    #      표본 부족이면 고정 폴백. 순수 함수라 같은 스냅숏 = 같은 임계다(불변식 8).
    r1_pool: list[float] = []
    for _student, student_features, baseline, _segment in prepared:
        r1_pool.extend(
            drop_series(student_features.weeks[-config.baseline_window_weeks :], baseline)
        )
    r1_threshold_pp, r1_threshold_source = resolve_drop_threshold(
        r1_pool,
        target_rate=config.r1.target_alert_rate,
        min_pool=config.r1.quantile_min_pool,
        fallback_pp=config.r1.drop_pp,
    )

    for student, student_features, baseline, segment in prepared:
        findings, skips = evaluate_student(
            student_features,
            baseline,
            config,
            segment,
            r1_threshold_pp,
            student_evidence.get(student.student_ref, EMPTY_EVIDENCE),
        )
        for skip in skips:
            skip_counter[(skip.rule_id.value, skip.reason)] += 1
        class_alerts[student.class_ref].extend(
            merge_student(student.student_ref, student.class_ref, findings)
        )

    signals, capped_out, evidence_skips = _rank_with_lifecycle(
        request, class_alerts, config, week_start, evidence_index, student_evidence
    )
    skip_counter.update(evidence_skips)

    stats = DetectStats(
        students_evaluated=evaluated,
        signals_raised=len(signals),
        excluded_under_2w=excluded_under_2w,
        capped_out=capped_out,
        r1_threshold_pp=round(r1_threshold_pp, 4),
        r1_threshold_source=r1_threshold_source.value,
        r1_pool_n=len(r1_pool),
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
    student_evidence: dict[str, StudentEvidence],
) -> tuple[list[Signal], int, Counter[tuple[str, str]]]:
    """파이프라인: 병합 → lifecycle 억제(탈락) → **evidence 전무 탈락** → new·follow_up만
    랭킹·상한 → ongoing·R5 상한 밖 합류 → 응답 (04 §3 · 09 §4, 7/22 확정).

    - 억제 후보(lifecycle=None)는 슬롯을 소비하지 않고 랭킹 이전에 탈락한다.
    - **evidence 전무 후보도 같은 자리에서 탈락한다**(09 §3 A판정 ② — "관련 기록이 전무하면
      신호를 생성하지 않는다"). 억제보다 강한 케이스다: 억제는 "지금 안 보여줄 신호"지만
      evidence 전무는 `Signal.evidence min_length=1`(불변식 2)로 **존재 자체가 불가능**하다.
      존재할 수 없는 신호가 상한 슬롯을 잡으면 진짜 위험이 밀려나므로 슬롯을 소비하지 않고,
      그 결과 rank가 1..N으로 연속을 유지한다(구멍 없음). 탈락 수는 반환해
      `stats.rules_skipped`에 남긴다 — 조용한 드롭 금지.
    - **상한 대상은 new·follow_up만**이다. ongoing(기존 카드 갱신)·R5(정책 신호)·
      advisory(R4 단독 — 04 §1 재정의)는
      "오늘 새로 봐야 할 카드"가 아니므로 상한 밖에서 합류한다 — 만성 미해소가 슬롯을
      점유해 신규 위험을 가리는 것을 막는다. capped_out은 new·follow_up 후보 기준.
    - rank: 상한 통과분(new·follow_up) 다음에 상한 밖(ongoing·R5)을 student_ref순으로
      이어붙인다 — 기존 R5(care) 처리 방식과 일관.
    """
    signals: list[Signal] = []
    capped_out = 0
    skipped: Counter[tuple[str, str]] = Counter()
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
            #: 🔴 **근거 없는 finding을 병합 전에 떼어 낸다**(99 #43) — 종전에는 경보
            #:   **전체**를 근거 유무로 판정해서, R1+R2 병합에서 R2만 근거가 없어도
            #:   **정상 R1까지 사라지거나** 반대로 근거 없는 R2가 primary로 남았다.
            trimmed = _drop_findings_without_evidence(
                alert, evidence_index, student_evidence, skipped, week_start
            )
            if trimmed is None:
                continue  # 남은 finding이 0 — 신호를 만들지 않는다(슬롯 미소비)
            kept.append(trimmed)
            lifecycles[(trimmed.student_ref, trimmed.primary.rule_id.value)] = lifecycle

        # ② 상한 대상(new·follow_up의 non-R5·non-advisory)과 상한 밖(ongoing·R5·advisory) 분리
        # advisory(R4 단독)도 상한 밖이다 — ongoing·R5와 같은 축이며 슬롯을 안 쓴다.
        # 04 §1 R4 재정의: 판정은 그대로고 랭킹·상한 참여만 빠진다.
        capped_pool = [
            a
            for a in kept
            if not a.is_auto_flag
            and not a.is_advisory
            and lifecycles[(a.student_ref, a.primary.rule_id.value)] in capped_kinds
        ]
        free_pool = [
            a
            for a in kept
            if a.is_auto_flag
            or a.is_advisory
            or lifecycles[(a.student_ref, a.primary.rule_id.value)] is Lifecycle.ONGOING
        ]

        # ③ new·follow_up만 랭킹·상한 (capped_pool엔 R5가 없어 rank_class care는 빈다)
        result = rank_class(capped_pool, config.cap_max)
        capped_out += result.capped_out
        for ranked in result.ranked:
            key = (ranked.alert.student_ref, ranked.alert.primary.rule_id.value)
            signals.append(
                _build_signal(
                    ranked, lifecycles[key], week_start, evidence_index, student_evidence
                )
            )

        # ④ ongoing·R5·advisory 상한 밖 합류 — 통과분 뒤에 student_ref순 rank 부여
        next_rank = len(result.ranked) + 1
        for alert in sorted(free_pool, key=lambda a: a.student_ref):
            key = (alert.student_ref, alert.primary.rule_id.value)
            ranked_free = RankedAlert(alert=alert, rank=next_rank)
            signals.append(
                _build_signal(
                    ranked_free,
                    lifecycles[key],
                    week_start,
                    evidence_index,
                    student_evidence,
                )
            )
            next_rank += 1
    return signals, capped_out, skipped


def _evidence_items(
    alert: StudentAlert,
    evidence_index: dict[tuple[str, date], list[str]],
    student_evidence: dict[str, StudentEvidence],
) -> tuple[EvidenceItem, ...]:
    """이 경보가 인용할 근거 — 🔴 **규칙별 resolver가 만든다**(99 #43).

    ⚠ **탈락 판정과 조립이 같은 함수를 쓴다** — 두 곳에서 따로 계산하면 "탈락 안 시켰는데
    조립에서 비는" 드리프트가 생긴다.
    🔴 **「최근 아무 기록」 폴백을 없앴다** — 그 폴백이 R2에 과거 `solve`를, R5에 복귀 전
    `solve`를 붙였다. 근거가 없으면 **신호를 만들지 않는다**(불변식 2).
    ⚠ 중복 제거 뒤 **결정론 순서**로 자른다 — `_MAX_EVIDENCE` 적용 **전에** 순서가 정해진다.
    """
    bundle = student_evidence.get(alert.student_ref, EMPTY_EVIDENCE)
    items: list[EvidenceItem] = []
    for finding in alert.merged:
        items.extend(
            resolve_evidence(
                finding.rule_id,
                EvidenceRequest(
                    student_ref=alert.student_ref,
                    label=finding.signal_type.value,
                    evidence_weeks=finding.evidence_weeks,
                    learning_events=evidence_index,
                    student_evidence=bundle,
                ),
            )
        )
    unique: dict[tuple[str, str], EvidenceItem] = {}
    for item in items:
        unique.setdefault((item.source_table, item.record_id), item)
    return tuple(unique.values())[:_MAX_EVIDENCE]


def _drop_findings_without_evidence(
    alert: StudentAlert,
    evidence_index: dict[tuple[str, date], list[str]],
    student_evidence: dict[str, StudentEvidence],
    skipped: Counter[tuple[str, str]],
    week_start: date,
) -> StudentAlert | None:
    """근거 없는 finding을 떼고 **대표 규칙·score를 다시 정한다**(99 #43).

    🔴 **제거된 finding의 높은 score가 primary로 남으면 안 된다** — 응답이 주장하는 규칙과
    실린 근거가 갈린다. 남은 것이 없으면 `None`(신호 미생성 · 슬롯 미소비).
    ⚠ `is_advisory`도 다시 계산한다 — R4만 남으면 참고 표시다(04 §1 R4 재정의).
    """
    bundle = student_evidence.get(alert.student_ref, EMPTY_EVIDENCE)
    kept: list[RuleFinding] = []
    for finding in alert.merged:
        has_evidence = bool(
            resolve_evidence(
                finding.rule_id,
                EvidenceRequest(
                    student_ref=alert.student_ref,
                    label=finding.signal_type.value,
                    evidence_weeks=finding.evidence_weeks,
                    learning_events=evidence_index,
                    student_evidence=bundle,
                ),
            )
        )
        if has_evidence:
            kept.append(finding)
            continue
        skipped[(finding.rule_id.value, _SKIP_EVIDENCE_ABSENT)] += 1
        logger.info(
            "신호 미생성(근거 전무) student=%s rule=%s week=%s",
            alert.student_ref,
            finding.rule_id.value,
            week_start.isoformat(),
        )
    if not kept:
        return None
    if len(kept) == len(alert.merged):
        return alert
    primary = max(kept, key=lambda f: f.score)
    return replace(
        alert,
        primary=primary,
        merged=tuple(sorted(kept, key=lambda f: f.score, reverse=True)),
        is_advisory=(
            not alert.is_auto_flag
            and all(f.rule_id is RuleId.R4 for f in kept)
        ),
    )


def _build_signal(
    ranked: RankedAlert,
    lifecycle: Lifecycle,
    week_start: date,
    evidence_index: dict[tuple[str, date], list[str]],
    student_evidence: dict[str, StudentEvidence],
) -> Signal:
    """경보 → 응답 Signal. 호출 전에 evidence 실존이 보장돼 있다(`_rank_with_lifecycle` ①')."""
    alert = ranked.alert
    primary = alert.primary
    signal_type = primary.signal_type

    evidence = _evidence_items(alert, evidence_index, student_evidence)

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
        advisory=alert.is_advisory,
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
