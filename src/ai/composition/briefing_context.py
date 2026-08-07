"""브리핑 근거 패키지(grounding v2) — 신호별 구조화 수치를 조립한다.

소유: 박진희 (A · composition). v1은 엔진 초안(brief.text)을 LLM에 넣어 "다듬기"에
그쳤다. v2는 신호의 **근거 수치를 구조화해** LLM이 직접 문장을 구성하게 한다.

불변(02_design·CLAUDE.md §1):
- 판정·발화는 결정론 엔진 소유 — 여기서 rule 임계·판정식을 재구현하지 않는다.
  detection 공개 순수 함수(extract_features·merge_weeks·compute_baseline·resolve_segment)를
  재사용해 요청+축적피처에서 수치를 **읽어** 파생(하락폭·배율·비율=단순 산술)할 뿐이다.
- rule의 임계 판정 결과물(연속 주수·연속 미제출 수)은 공개 함수로 정확히 재현할 수 없어
  담지 않는다(단순 카운트는 판정과 어긋날 수 있으므로) — 지속성은 lifecycle이 담는다.
- 실명·student_ref·자유 텍스트 불포함(수치·enum 태그·라벨만). 조립 후 redaction은
  briefing.make_brief가 전송 직전 재확인한다(fail-closed).
- 허용 숫자 집합 = 이 컨텍스트가 실제로 제공한 수치뿐(EXACT — 제공 안 한 환산값 불허).
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace

from ai.contracts.detection import (
    DetectRequest,
    Lifecycle,
    RuleId,
    Signal,
    SignalType,
    StudentInput,
    TermContext,
)
from ai.contracts.taxonomy import AreaTag, TypeTag
from ai.detection.baseline import Baseline, compute_baseline
from ai.detection.features import (
    StudentFeatures,
    WeekFeatures,
    extract_features,
    merge_weeks,
)
from ai.detection.lifecycle import has_return_care_history
from ai.detection.segments import Segment, resolve_segment
from ai.detection.thresholds import ThresholdConfig, default_threshold_config

_NUMBER_RE = re.compile(r"\d+")

#: area·type enum → 한글 라벨(확정 어휘 그대로 — 09 §2 영역/유형). 표시용 매핑을 여기 두는 건
#: contracts/taxonomy.py가 양자 승인 파일이라 임의 수정 대상이 아니기 때문(CLAUDE.md §2).
_AREA_KO: dict[AreaTag, str] = {
    AreaTag.READING: "독서",
    AreaTag.LITERATURE: "문학",
    AreaTag.SPEECH: "화법",
    AreaTag.WRITING: "작문",
    AreaTag.LANGUAGE: "언어",
    AreaTag.MEDIA: "매체",
}
_TYPE_KO: dict[TypeTag, str] = {
    TypeTag.FACT: "사실",
    TypeTag.INFER: "추론",
    TypeTag.CRITIC: "비판",
    TypeTag.CONCEPT: "개념",
}

#: 세그먼트 → 프롬프트용 한글 맥락(강사에게 배경으로만 제공, 문장 강제 아님).
_SEGMENT_KO: dict[Segment, str] = {
    Segment.NORMAL: "평상시",
    Segment.READAPT: "복귀 후 적응기",
    Segment.VACATION: "방학·휴강 기간",
    Segment.NEW_TERM: "신학기 초",
}

#: lifecycle → 지속성 서술(연속 주수 대신 — rule 판정과 어긋나지 않는 표현).
#: **Lifecycle 전 값을 덮어야 한다** — `render_evidence_block`이 직접 인덱싱하므로 누락은
#: 런타임 KeyError이고, 그 호출은 `make_brief` 폴백 경계 밖이라 응답 전체가 500이 된다.
#: `test_briefing_500_paths.test_lifecycle_ko_covers_every_lifecycle`가 값대조로 고정한다.
#: ⚠ 문면에 **숫자를 넣지 않는다** — 이 블록이 곧 프롬프트라, 근거가 제공하지 않은 수치를
#: 심으면 LLM이 그것을 되뇌고 왜곡 게이트(수치 EXACT 대조)에서 걸려 폴백이 잦아진다.
_LIFECYCLE_KO: dict[Lifecycle, str] = {
    Lifecycle.NEW: "이번 주 새로 나타난 상태",
    Lifecycle.ONGOING: "지난주부터 이어지고 있는 상태",
    Lifecycle.FOLLOW_UP: "해소된 뒤 다시 나타난 상태",
}


@dataclass(frozen=True)
class EvidenceFact:
    """근거 한 줄 — 라벨과 표기값. 표기값 안의 숫자만 게이트 허용집합에 들어간다."""

    label: str
    value: str


@dataclass(frozen=True)
class BriefingContext:
    """신호 하나의 브리핑 근거 패키지 — LLM 입력·게이트 허용집합·폴백의 단일 출처."""

    signal_type: SignalType
    display_label: str
    lifecycle: Lifecycle
    segment: Segment
    facts: tuple[EvidenceFact, ...]
    evidence_summaries: tuple[str, ...]
    fallback_text: str
    """게이트 소진·LLM 실패 시 되돌아갈 엔진 결정론 템플릿(`signal.brief.text`).

    🔴 **비면 폴백 자체가 예외가 된다** — `_fallback()`이 `Brief(text=fallback_text)`를
    만드는데 `Brief.text`는 `min_length=1`이라 `ValidationError`가 나고, 그게 하필
    `except LlmError` **핸들러 안**이라 ㊝과 똑같이 `/v1/detect`까지 올라간다.
    **폴백 경로가 마지막 미방어 문이었다**(99 ㊠ ②).

    ⚠ **지금까지 안전했던 것은 계약이 아니라 우연이다** — `:225`·`:239`가
    `signal.brief.text`에서 채우고 그 값이 `Brief.text`(`min_length=1`)를 이미 통과한
    것이라 **전이적으로** 비지 않았다. 그 전이 관계가 끊기면(다른 출처가 생기면) 조용히
    깨진다. 아래 `__post_init__`이 그걸 계약으로 바꾼다.
    """

    def __post_init__(self) -> None:
        """`fallback_text` 비어 있음 금지 — `counsel`의 `NonEmptyStr`과 같은 제약.

        ⚠ **`@dataclass(frozen=True)`를 유지한다** — Pydantic 전환은 이 타입이 신호마다
        만들어지는 핫 경로(`:225`·`:239`)라 **동작 변화 0을 증명할 수 없다**(#124 판단).
        검사만 하고 값을 고치지 않는다(frozen이라 대입도 안 된다).
        """
        if not self.fallback_text.strip():
            raise ValueError(
                "fallback_text가 비면 _fallback()이 Brief.text의 min_length=1에 걸려 "
                "폴백 자체가 예외가 된다 — 그 예외는 except LlmError 핸들러 안에서 나므로 "
                "/v1/detect까지 올라간다(99 ㊝·㊠ ②)"
            )

    def allowed_numbers(self) -> frozenset[str]:
        """이 컨텍스트가 실제로 제공한 수치 집합(EXACT). 파생 표기의 숫자만 허용된다."""
        nums: set[str] = set()
        for fact in self.facts:
            nums.update(_NUMBER_RE.findall(fact.value))
        for summary in self.evidence_summaries:
            nums.update(_NUMBER_RE.findall(summary))
        return frozenset(nums)


def _pct(value: float) -> str:
    return f"{round(value * 100)}%"


def _r1_facts(sf: StudentFeatures, baseline: Baseline) -> tuple[EvidenceFact, ...]:
    """R1 정답률 하락 — 이번 주·평소 정답률·하락폭(단순 차 — 임계 판정 아님)."""
    week = sf.weeks[-1]
    facts: list[EvidenceFact] = []
    if week.accuracy is not None:
        facts.append(EvidenceFact("이번 주 정답률", _pct(week.accuracy)))
    if baseline.accuracy is not None:
        facts.append(EvidenceFact("평소 정답률(개인 기준선)", _pct(baseline.accuracy)))
    if week.accuracy is not None and baseline.accuracy is not None:
        drop = round((baseline.accuracy - week.accuracy) * 100)
        facts.append(EvidenceFact("하락폭", f"{drop}%p"))
    return tuple(facts)


def _r2_facts(sf: StudentFeatures, baseline: Baseline) -> tuple[EvidenceFact, ...]:
    """R2 제출 저조 — 최근 주 미제출 사실만(연속 주수는 rule 판정이라 담지 않음)."""
    facts: list[EvidenceFact] = [EvidenceFact("이번 주 과제 제출", "없음")]
    if len(sf.weeks) >= 2 and not sf.weeks[-2].submitted:
        facts.append(EvidenceFact("직전 주 과제 제출", "없음"))
    return tuple(facts)


def _r3_facts(sf: StudentFeatures, baseline: Baseline) -> tuple[EvidenceFact, ...]:
    """R3 학습 공백 — 이번 주 학습량·평소 대비 비율."""
    week = sf.weeks[-1]
    facts: list[EvidenceFact] = [
        EvidenceFact("이번 주 학습 활동", f"{week.event_count}건")
    ]
    if baseline.volume:
        facts.append(
            EvidenceFact("평소 대비", _pct(week.event_count / baseline.volume))
        )
    return tuple(facts)


def _r4_facts(sf: StudentFeatures, baseline: Baseline) -> tuple[EvidenceFact, ...]:
    """R4 숨은 위기 — 정답률 유지 폭·풀이시간 배율('배' 단위)."""
    week = sf.weeks[-1]
    facts: list[EvidenceFact] = []
    if week.accuracy is not None and baseline.accuracy is not None:
        keep = round(abs(week.accuracy - baseline.accuracy) * 100)
        facts.append(EvidenceFact("정답률 변동(평소 대비)", f"{keep}%p 이내로 유지"))
    if week.norm_time is not None and baseline.norm_time:
        # 배율은 '배' 단위(반올림 1자리) — % 표기보다 강사에게 직관적(v2 프리뷰 평가 반영).
        ratio = week.norm_time / baseline.norm_time
        facts.append(EvidenceFact("문제 풀이 시간(평소 대비)", f"{ratio:.1f}배"))
    return tuple(facts)


def _r5_facts(sf: StudentFeatures, baseline: Baseline) -> tuple[EvidenceFact, ...]:
    """R5 복귀 케어 — 복귀 첫 주 사실만(점수 무관)."""
    return (EvidenceFact("상태", "복귀 첫 주"),)


def _r6_facts(sf: StudentFeatures, baseline: Baseline) -> tuple[EvidenceFact, ...]:
    """R6 유형 편중 — 오답이 몰린 셀(영역·유형)·오답 문항 수·해당 유형 정답률."""
    week = sf.weeks[-1]
    if not week.cells:
        return ()
    top = max(week.cells, key=lambda c: c.n_wrong)
    area = _AREA_KO.get(top.area, top.area.value)
    type_ = _TYPE_KO.get(top.type, top.type.value)
    facts: list[EvidenceFact] = [
        EvidenceFact("오답이 몰린 영역·유형", f"{area}·{type_}"),
        EvidenceFact("해당 유형 오답", f"{top.n}문항 중 {top.n_wrong}문항"),
    ]
    if top.n:
        facts.append(
            EvidenceFact("해당 유형 정답률", _pct((top.n - top.n_wrong) / top.n))
        )
    return tuple(facts)


_FACT_BUILDERS: dict[
    RuleId, Callable[[StudentFeatures, Baseline], tuple[EvidenceFact, ...]]
] = {
    RuleId.R1: _r1_facts,
    RuleId.R2: _r2_facts,
    RuleId.R3: _r3_facts,
    RuleId.R4: _r4_facts,
    RuleId.R5: _r5_facts,
    RuleId.R6: _r6_facts,
}


def _build_facts(
    signal: Signal, sf: StudentFeatures, baseline: Baseline
) -> tuple[EvidenceFact, ...]:
    if not sf.weeks:
        return ()
    builder = _FACT_BUILDERS.get(signal.rule_id)
    return builder(sf, baseline) if builder is not None else ()


def build_briefing_context(
    signal: Signal, sf: StudentFeatures, baseline: Baseline, segment: Segment
) -> BriefingContext:
    """신호 + 그 학생의 피처·기준선·세그먼트 → 근거 패키지. rule 판정식 미접근."""
    summaries = tuple(dict.fromkeys(e.summary for e in signal.evidence))
    return BriefingContext(
        signal_type=signal.signal_type,
        display_label=signal.display_label,
        lifecycle=signal.lifecycle,
        segment=segment,
        facts=_build_facts(signal, sf, baseline),
        evidence_summaries=summaries,
        fallback_text=signal.brief.text,
    )


def _minimal_context(signal: Signal) -> BriefingContext:
    """학생 피처를 못 찾은 방어 경로 — 수치 없이 lifecycle·폴백만(정직하게 비움)."""
    summaries = tuple(dict.fromkeys(e.summary for e in signal.evidence))
    return BriefingContext(
        signal_type=signal.signal_type,
        display_label=signal.display_label,
        lifecycle=signal.lifecycle,
        segment=Segment.NORMAL,
        facts=(),
        evidence_summaries=summaries,
        fallback_text=signal.brief.text,
    )


def build_contexts(
    request: DetectRequest,
    signals: Sequence[Signal],
    *,
    stored_features: Mapping[str, Sequence[WeekFeatures]] | None = None,
    config: ThresholdConfig | None = None,
) -> dict[str, BriefingContext]:
    """신호별 BriefingContext를 signal_id로 매핑. 엔진과 같은 공개 함수로 피처를 되살린다.

    엔진 detect()가 쓰는 것과 동일한 병합·기준선·세그먼트 경로를 재사용한다(값이 바뀌면
    한 곳에서 바뀐다). 신호가 있는 학생만 계산한다.
    """
    config = config or default_threshold_config()
    features = extract_features(request)
    students = {s.student_ref: s for s in request.students}
    term = request.snapshot_meta.term_context

    cache: dict[str, tuple[StudentFeatures, Baseline, Segment] | None] = {}
    contexts: dict[str, BriefingContext] = {}
    for signal in signals:
        ref = signal.student_ref
        if ref not in cache:
            cache[ref] = _resolve_student(
                ref, students.get(ref), features.get(ref), request, stored_features, config, term
            )

        entry = cache[ref]
        if entry is None:
            contexts[signal.signal_id] = _minimal_context(signal)
        else:
            sf, baseline, segment = entry
            contexts[signal.signal_id] = build_briefing_context(signal, sf, baseline, segment)
    return contexts


def _resolve_student(
    ref: str,
    student: StudentInput | None,
    sf: StudentFeatures | None,
    request: DetectRequest,
    stored_features: Mapping[str, Sequence[WeekFeatures]] | None,
    config: ThresholdConfig,
    term: TermContext,
) -> tuple[StudentFeatures, Baseline, Segment] | None:
    if sf is None or student is None:
        return None
    if stored_features:
        stored = stored_features.get(ref)
        if stored:
            sf = replace(sf, weeks=merge_weeks(stored, sf.weeks))
    baseline = compute_baseline(sf, config.baseline_window_weeks)
    segment = resolve_segment(
        student.status,
        term,
        has_return_care_history(ref, request.alert_context),
    )
    return sf, baseline, segment


def render_evidence_block(ctx: BriefingContext) -> str:
    """근거 패키지를 프롬프트에 넣을 한글 블록으로. 이 모듈이 컨텍스트 표현을 소유한다."""
    lines = [
        f"신호 유형: {ctx.display_label}",
        f"지속성: {_LIFECYCLE_KO[ctx.lifecycle]}",
        f"학사 맥락: {_SEGMENT_KO.get(ctx.segment, ctx.segment.value)}",
    ]
    if ctx.facts:
        lines.append("관찰된 근거:")
        lines.extend(f"- {fact.label}: {fact.value}" for fact in ctx.facts)
    else:
        lines.append("관찰된 수치 근거: (제공된 수치 없음 — 성격만 전달)")
    if ctx.evidence_summaries:
        lines.append("참고 근거: " + " / ".join(ctx.evidence_summaries))
    return "\n".join(lines)
