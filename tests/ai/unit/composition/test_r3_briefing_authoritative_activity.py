"""R3 — **판정과 브리핑이 같은 정본을 읽는가** (지시서 81).

🔴 **두 입력을 같은 지표라고 판정한 것이 아니다.** `learning_events`는 R1·R4·R6의 원시 피처
정본이고, 주간 활동량은 `detection_evidence.weekly_activity`가 정본이다. 이 파일이 묻는 것은
**서로 다를 수 있는 입력에서 R3가 자기 정본을 끝까지 유지하는가** 하나뿐이다.

⚠ **충돌을 정규화하지 않는다** — 400으로 거부하지도, 한쪽을 다른 쪽으로 덮지도, 픽스처 값을
같게 맞추지도 않는다. **충돌을 그대로 두고 관측한다**(지시서 81 §1).
🔴 **픽스처를 정렬하면 반례가 사라져 이 파일이 아무것도 안 본다** — 절단 가드가 그것을 막는다.

**반례의 형태**(2026-08-13 B단계 실측):

```
learning_events 분석 주 event_count = 20
weekly_activity.activity_count      = 0        ← R3 판정 정본
기준창 집계                          = 20

수정 전 브리핑 facts : 활동 20건 · 평소 대비 100%   ← 「학습 공백」 신호 옆에 「평소와 같다」
수정 후 브리핑 facts : 활동 0건  · 평소 대비 0%
```

⚠ 그 20건·100%는 **facts 안의 숫자**라 근거 밖 숫자 게이트가 **못 잡는다** — 게이트를 고칠
일이 아니라 조립을 고칠 일이다(§15).
"""

from __future__ import annotations

from typing import Final

import pytest
from detect_briefing_scenarios import ScenarioBuilder, builder, week_of

from ai.composition.briefing_context import EvidenceFact, build_contexts
from ai.contracts.detection import DetectResponse, RuleId
from ai.detection.engine import detect
from ai.detection.evidence import (
    EMPTY_EVIDENCE,
    StudentEvidence,
    build_student_evidence,
    resolve_weekly_activity_window,
)
from ai.detection.thresholds import default_threshold_config

_CONFIG: Final = default_threshold_config()

#: 반례가 성립하려면 이 값이 서로 달라야 한다 — 절단 가드가 매 검사 전에 확인한다.
_EVENT_COUNT: Final = 20
_ACTIVITY_COUNT: Final = 0
_BASELINE_ACTIVITY: Final = 20


def _facts(scenario: ScenarioBuilder) -> dict[str, str]:
    """엔진 → 브리핑 컨텍스트를 실제 경로로 통과시키고 대표 신호의 facts를 돌려준다."""
    request = scenario.build()
    response: DetectResponse = detect(request, _CONFIG)
    assert response.signals, "신호가 없으면 이 검사는 아무것도 안 본다"
    signal = response.signals[0]
    context = build_contexts(request, response.signals)[signal.signal_id]
    return {fact.label: fact.value for fact in context.facts}


def _conflicting() -> ScenarioBuilder:
    """🔴 두 축이 **의도적으로** 어긋난 입력 — 분석 주 solve 20건 · 집계 0건."""
    scenario = builder("r3conflict").student("st_a")
    #: `steady_history`가 분석 주까지 채운다 → learning_events 기준 event_count = 20.
    scenario.steady_history("st_a", n=_EVENT_COUNT, correct=17)
    #: 같은 주의 **정본 집계**는 0건이다 — 이것이 R3의 판정값이다.
    scenario.activity_series(
        "st_a", counts={0: _ACTIVITY_COUNT}, baseline=_BASELINE_ACTIVITY
    )
    return scenario


def _conflicting_with_r2() -> ScenarioBuilder:
    """B10 동형 — R2 근거를 더해 병합시킨다(대표는 R3)."""
    scenario = _conflicting()
    for back in range(_CONFIG.r2.consecutive_missing):
        scenario.assignment("st_a", back=back, expected=3, submitted=0)
    return scenario


# ───────────────────────── 절단 가드 ─────────────────────────


def test_the_fixture_really_keeps_the_two_axes_apart() -> None:
    """🔴 **절단 가드** — 픽스처가 정렬되면 아래 검사들은 반례를 잃고 조용히 통과한다.

    ⚠ 이 파일의 가치는 **두 값이 다르다는 사실**에 있다. 누군가 `steady_history`를 빼거나
    집계를 20으로 맞추면 「고쳤다」가 아니라 「안 보게 됐다」가 된다.
    """
    request = _conflicting().build()
    evidence = build_student_evidence(request)["st_a"]
    analysis_week = week_of(0)

    activity = evidence.weekly_activity[analysis_week]
    assert activity.activity_count == _ACTIVITY_COUNT

    events = [
        item
        for item in request.learning_events
        if item.student_ref == "st_a" and item.occurred_at.date() >= analysis_week
    ]
    assert len(events) == _EVENT_COUNT, len(events)
    assert activity.activity_count != len(events), (
        "두 축이 같아졌다 — 반례가 사라져 이 파일이 아무것도 안 본다"
    )


def test_the_conflicting_request_is_not_rejected() -> None:
    """⚠ 충돌은 **거부 사유가 아니다**(지시서 81 §1) — 판정은 정상 수행된다."""
    response = detect(_conflicting().build(), _CONFIG)
    assert [s.rule_id for s in response.signals] == [RuleId.R3]


# ───────────────────────── B05 동형 반례 ─────────────────────────


def test_r3_judgment_uses_the_authoritative_weekly_activity() -> None:
    """R3 판정·응답 근거는 집계 0건을 본다 — learning_event 20건이 아니다."""
    response = detect(_conflicting().build(), _CONFIG)
    signal = response.signals[0]
    assert signal.rule_id is RuleId.R3
    assert [item.source_table for item in signal.evidence] == ["student_week_activity"]
    assert any(f"{_ACTIVITY_COUNT}건" in item.summary for item in signal.evidence), [
        item.summary for item in signal.evidence
    ]


def test_b05_briefing_facts_follow_the_judgment_not_the_learning_events() -> None:
    """🔴 **이 안건의 반례** — 수정 전에는 `20건 · 100%`가 나왔다."""
    facts = _facts(_conflicting())
    assert facts["이번 주 학습 활동"] == f"{_ACTIVITY_COUNT}건", facts
    assert facts["평소 대비"] == "0%", facts


def test_b05_the_learning_event_numbers_are_absent_from_the_facts() -> None:
    """🔴 20건·100%가 **facts 어디에도** 없어야 한다 — 프롬프트로 새는 자리다."""
    values = " ".join(_facts(_conflicting()).values())
    assert f"{_EVENT_COUNT}건" not in values, values
    assert "100%" not in values, values


# ───────────────────────── B10 동형 반례 (병합) ─────────────────────────


def test_b10_merged_signal_keeps_r3_as_representative() -> None:
    """R2+R3 병합 결과가 1건이고 대표가 R3여야 이 반례가 성립한다."""
    response = detect(_conflicting_with_r2().build(), _CONFIG)
    assert len(response.signals) == 1, [s.rule_id for s in response.signals]
    assert response.signals[0].rule_id is RuleId.R3


def test_b10_merged_facts_also_use_the_authoritative_activity() -> None:
    """병합돼도 facts는 R3 정본을 쓴다 — 인과관계를 만들지도 않는다."""
    facts = _facts(_conflicting_with_r2())
    assert facts["이번 주 학습 활동"] == f"{_ACTIVITY_COUNT}건", facts
    assert facts["평소 대비"] == "0%", facts
    values = " ".join(facts.values())
    assert f"{_EVENT_COUNT}건" not in values and "100%" not in values, values


def test_the_baseline_also_comes_from_the_activity_aggregate() -> None:
    """🔴 **분모까지 정본이어야 한다** — 분자만 바꾸면 비율이 여전히 틀린다.

    ⚠ 앞의 반례는 두 기준선이 우연히 같아(둘 다 20) **분모가 어디서 오는지 못 본다**.
    여기서는 기준선을 갈라 놓는다.

    ```
    learning_events 기준선   주당 20건
    weekly_activity 기준선   주당 10건        ← R3 정본
    분석 주 집계             3건

    정본 분모 :  3 / 10 = 30%
    잘못된 분모:  3 / 20 = 15%
    ```
    """
    scenario = builder("r3base").student("st_a")
    scenario.steady_history("st_a", n=_EVENT_COUNT, correct=17)
    scenario.activity_series("st_a", counts={0: 3}, baseline=10)

    facts = _facts(scenario)
    assert facts["이번 주 학습 활동"] == "3건", facts
    assert facts["평소 대비"] == "30%", facts
    assert "15%" not in " ".join(facts.values()), facts


# ───────────────────────── 정상 입력 대조 ─────────────────────────


def test_an_aligned_request_keeps_the_previous_facts() -> None:
    """⚠ 두 축이 우연히 같은 정상 입력에서 **종전과 같은 facts**가 나온다.

    새 resolver가 정상 경로를 깨지 않는지 본다 — 반례만 보면 그것을 모른다.
    """
    aligned = builder("r3aligned").student("st_a")
    aligned.steady_history("st_a", n=_BASELINE_ACTIVITY, correct=17, skip_recent=1)
    aligned.solves("st_a", back=0, n=7, correct=6)
    aligned.activity_series("st_a", counts={0: 7}, baseline=_BASELINE_ACTIVITY)

    facts = _facts(aligned)
    assert facts["이번 주 학습 활동"] == "7건", facts
    assert facts["평소 대비"] == "35%", facts


# ───────────────────────── fail-closed ─────────────────────────


def test_the_resolver_returns_none_when_a_baseline_week_is_missing() -> None:
    """🔴 기준창 중 한 주라도 빠지면 창이 서지 않는다 — 0으로 간주하지 않는다."""
    scenario = builder("r3gap").student("st_a").steady_history("st_a")
    #: 기준창 한 주를 **명시 부재**로 뺀다(음수 = 그 주 집계를 넣지 않는다).
    scenario.activity_series(
        "st_a", counts={0: 0, 2: -1}, baseline=_BASELINE_ACTIVITY
    )
    evidence = build_student_evidence(scenario.build())["st_a"]
    assert (
        resolve_weekly_activity_window(
            evidence, baseline_window_weeks=_CONFIG.baseline_window_weeks
        )
        is None
    )


def test_empty_evidence_yields_no_numbers_instead_of_learning_event_fallback() -> None:
    """🔴 창이 안 서면 **수치를 비운다** — learning_event로 대신하지 않는다(§13)."""
    from ai.composition.briefing_context import _r3_facts  # noqa: PLC0415

    assert _r3_facts(EMPTY_EVIDENCE, _CONFIG) == ()


@pytest.mark.parametrize("weeks", [2, 4])
def test_the_baseline_window_comes_from_the_config_not_a_constant(weeks: int) -> None:
    """🔴 기준창 주수를 하드코딩하면 설정을 낮춘 테넌트에서 판정이 영영 안 선다.

    ⚠ 집계를 **설정 주수만큼만** 주고 그 설정으로 해소되는지 본다 — 8주 상수가 살아 있으면
    창이 `None`이 된다.
    """
    scenario = builder(f"r3win{weeks}").student("st_a").steady_history("st_a")
    scenario.activity_series(
        "st_a", counts={0: 0}, baseline=_BASELINE_ACTIVITY, weeks=weeks
    )
    evidence = build_student_evidence(scenario.build())["st_a"]

    window = resolve_weekly_activity_window(evidence, baseline_window_weeks=weeks)
    assert window is not None
    assert len(window.prior) == weeks
    assert window.current.activity_count == _ACTIVITY_COUNT
    assert window.baseline_volume == _BASELINE_ACTIVITY

    #: 🔴 브리핑도 **설정에서 읽는다** — 상수를 들면 여기서 facts가 비어 버린다.
    facts = {f.label: f.value for f in _r3_facts_for(evidence, weeks)}
    assert facts["이번 주 학습 활동"] == f"{_ACTIVITY_COUNT}건", facts
    assert facts["평소 대비"] == "0%", facts


def _r3_facts_for(evidence: StudentEvidence, weeks: int) -> tuple[EvidenceFact, ...]:
    """설정 주수를 바꿔 R3 facts를 만든다 — 기준창이 설정에서 오는지 보기 위한 보조."""
    from ai.composition.briefing_context import _r3_facts  # noqa: PLC0415

    return _r3_facts(evidence, _CONFIG.model_copy(update={"baseline_window_weeks": weeks}))
