"""근거의 역할·비교값 — 기준선이 실리는가, 어디에 실리는가 (99 #59·#60).

🔴 **동어반복을 피한다.** `len(evidence) == 5` 같은 개수 단언은 아무것도 지키지 못한다.
여기서 지키는 불변식은 넷이다::

    ① 기준선 비교를 하는 규칙에는 **비교 대상이 실제로 있다**
    ② `trigger`와 `baseline`이 **role로 갈린다**
    ③ 기준선 행을 **못 만드는 규칙이 문서에 명시**돼 있다
    ④ 🔴 비교값은 **신호에** 있고 evidence 행에는 없다 (안 D)

③이 중요하다 — 못 싣는 규칙이 조용히 섞이면 「없는 건지 못 만드는 건지」가 다시 안 보인다.

⚠ 실 LLM 0회 · 백엔드 호출 0회 — 골든 시나리오를 결정론으로 돌린다.
"""

from __future__ import annotations

import inspect
from datetime import date, timedelta
from pathlib import Path
from typing import Final

import pytest

from ai.contracts.detection import EvidenceRole, RuleId, Signal
from ai.detection import rules as rules_module
from ai.detection.engine import detect
from ai.evaluation.golden.detection.scenarios import all_scenarios

#: 🔴 **기록 단위 기준선을 가진 규칙** — 현재 R3뿐이다.
#: `weekly_activity_summary`가 **주 단위 백엔드 레코드**라 `record_id`와 `activity_count`가
#: 1:1로 붙는다. R1·R4의 `learning_event`는 **문항 단위**라(`correct`·`duration_sec`)
#: 주 단위 지표(정답률·정규화 시간)에 해당하는 레코드가 **존재하지 않는다**.
_RULES_WITH_RECORD_BASELINE: Final[frozenset[RuleId]] = frozenset({RuleId.R3})

#: 🔴 **비교 대상 자체가 없는 규칙** — 비워 두는 것이 정답이다.
#: R5는 *"휴원했다가 돌아왔다"* 는 **사건**이지 값 비교가 아니다.
_RULES_WITHOUT_COMPARISON: Final[frozenset[RuleId]] = frozenset({RuleId.R5})

_DESIGN_DOC: Final = Path("docs/part_a/14_evidence_fields.md")


def _signals_by_rule() -> dict[RuleId, list[Signal]]:
    found: dict[RuleId, list[Signal]] = {}
    for scenario in all_scenarios():
        for signal in detect(scenario.request).signals:
            found.setdefault(signal.rule_id, []).append(signal)
    return found


def test_every_finding_carries_at_least_one_baseline_row() -> None:
    """🔴 기록 단위 기준선을 가진 규칙은 **비교 대상 행을 실제로 싣는다**.

    ⚠ 고의 파괴: `resolve_r3_evidence`에서 baseline 행 추가를 되돌리면 red다.
    """
    by_rule = _signals_by_rule()
    for rule_id in _RULES_WITH_RECORD_BASELINE:
        signals = by_rule.get(rule_id)
        assert signals, f"{rule_id.value} 신호가 골든에 하나도 없다 — 이 검사가 눈이 멀었다"
        for signal in signals:
            roles = {item.role for item in signal.evidence}
            assert EvidenceRole.BASELINE in roles, (
                f"{rule_id.value}: 기준선 행이 없다 — 비교 대상 없이 "
                f"'평소 대비'를 주장하고 있다"
            )


def test_trigger_and_baseline_are_distinguishable_by_role() -> None:
    """`trigger`와 `baseline`이 **갈린다** — 둘 다 있는 신호에서 확인한다.

    ⚠ 고의 파괴: `role`을 전부 `TRIGGER`로 고정하면 red다.
    """
    by_rule = _signals_by_rule()
    mixed = [
        signal
        for rule_id in _RULES_WITH_RECORD_BASELINE
        for signal in by_rule.get(rule_id, [])
    ]
    assert mixed, "기준선을 가진 신호가 없다"
    for signal in mixed:
        triggers = [i for i in signal.evidence if i.role is EvidenceRole.TRIGGER]
        baselines = [i for i in signal.evidence if i.role is EvidenceRole.BASELINE]
        assert triggers and baselines, f"{signal.rule_id.value}: 한쪽만 있다"
        # 🔴 같은 레코드가 양쪽에 있으면 갈린 것이 아니다
        assert not ({i.record_id for i in triggers} & {i.record_id for i in baselines})


def test_rules_without_record_level_baseline_are_documented() -> None:
    """🔴 **기준선 행을 못 만드는 규칙이 문서에 명시**돼 있다.

    조용히 빠지면 백엔드가 *"baseline 행은 항상 온다"* 로 가정해 표시 로직을 짜고,
    그 규칙에서 **화면이 빈다.** 코드가 안 만드는 것과 문서가 말하는 것이 같아야 한다.
    """
    doc = _DESIGN_DOC.read_text(encoding="utf-8")
    by_rule = _signals_by_rule()
    for rule_id, signals in by_rule.items():
        emits_baseline = any(
            item.role is EvidenceRole.BASELINE
            for signal in signals
            for item in signal.evidence
        )
        if emits_baseline:
            continue
        assert rule_id.value in doc, (
            f"{rule_id.value}는 기준선 행을 안 내는데 {_DESIGN_DOC}에 사유가 없다"
        )


def test_the_comparison_rides_on_the_signal_not_the_evidence_row() -> None:
    """🔴 **안 D** — 비교값(`baseline`)은 신호에 있고 evidence 행에는 **필드조차 없다**.

    R1의 기준선은 직전 주들의 **평균 정답률**이고 `learning_event`는 **문항 단위**라
    그 값에 해당하는 레코드가 없다. 레코드마다 주 단위 값을 붙이면 *"그 기록 자신의 값"*
    이라는 계약이 거짓이 된다 — 그래서 evidence에는 `baseline` 필드를 두지 않았다.
    """
    from ai.contracts.detection import EvidenceItem

    assert "baseline" not in EvidenceItem.model_fields
    assert "baseline" in Signal.model_fields

    by_rule = _signals_by_rule()
    r1 = by_rule.get(RuleId.R1)
    assert r1, "R1 신호가 없다"
    for signal in r1:
        assert signal.baseline is not None, "R1은 기준선 비교 규칙인데 값이 비었다"
        # 문항 단위 레코드에 주 단위 값을 붙이지 않았다
        assert all(item.observed is None for item in signal.evidence)


@pytest.mark.parametrize(
    "rule_id", [r for r in RuleId if r not in _RULES_WITHOUT_COMPARISON], ids=lambda r: r.value
)
def test_every_comparing_rule_fills_metric(rule_id: RuleId) -> None:
    """🔴 R5를 뺀 모든 규칙이 `metric`을 채운다 — **값이 아니라 「채워졌는가」를 본다.**

    값을 단언하면 임계가 바뀔 때마다 테스트를 고치게 되고, 그러면 검사가 규칙을 따라다닌다.
    """
    signals = _signals_by_rule().get(rule_id)
    if not signals:
        pytest.skip(f"{rule_id.value} 신호가 골든에 없다")
    for signal in signals:
        assert signal.metric, f"{rule_id.value}: metric이 비었다"
        assert signal.observed is not None, f"{rule_id.value}: observed가 비었다"


def test_r5_is_empty_on_purpose_and_says_so() -> None:
    """🔴 R5가 비어 있는 것은 **의도**다 — 사유가 코드에 적혀 있어야 한다.

    안 적으면 다음 사람이 *"R5만 빠졌네"* 하고 억지로 채운다. 그때 임계값이 `baseline`으로
    들어가면 *"평소 대비"* 로 읽혀 거짓이 된다.
    """
    source = inspect.getsource(rules_module)
    marker = source[: source.index("def _r5(")]
    assert "R5는 비교값을 비워 둔다" in marker, "R5를 비워 둔 사유가 코드에 없다"


def test_the_baseline_rows_never_reach_the_briefing_prompt() -> None:
    """🔴 **기준선 행은 브리핑 프롬프트에 안 들어간다** — 실측 회귀에서 나온 검사.

    처음 구현에서 `evidence` 전량의 `summary`를 프롬프트 재료로 실었더니 기준선 숫자가
    새어 나갔다. 두 가지가 깨진다::

        ① LLM 이 비교 기준을 「이번 주 값」으로 오독한다
        ② 🔴 EXACT 게이트의 `allowed_numbers` 가 넓어져 그 숫자를 아무 자리에나 써도 통과한다

    ⚠ 실제로 `test_r3_prompt_carries_the_authoritative_activity_not_the_learning_events`가
    잡았는데, **기준선 값(20건)이 그 검사가 막으려던 값과 우연히 같아서** 잡혔다.
    값이 달랐으면 조용히 지나갔다 ⇒ **역할로 직접 잠근다.**
    """
    from ai.composition.briefing_context import _trigger_summaries

    by_rule = _signals_by_rule()
    signals = by_rule.get(RuleId.R3)
    assert signals, "R3 신호가 없다 — 이 검사가 눈이 멀었다"
    for signal in signals:
        baselines = [i for i in signal.evidence if i.role is EvidenceRole.BASELINE]
        assert baselines, "기준선 행이 없다 — 재려는 것이 없다"
        summaries = _trigger_summaries(signal)
        for item in baselines:
            assert item.summary not in summaries, (
                f"기준선 문면이 프롬프트 재료에 실렸다: {item.summary!r}"
            )


def test_signal_metric_units_match_the_documented_table() -> None:
    """규칙별 `metric` 문자열과 `baseline is None` 집합이 문서 표와 일치하는지.

    🔴 **이 표는 백엔드가 화면 단위를 고르는 근거다.** `observed`를 그냥 `%`로 찍으면
    `submit_drop`이 **300%**로 나간다 — `consecutive_missing_weeks`는 **주 수**다.
    표가 코드와 갈리면 그 사고가 **조용히** 난다.

    🔴 **이 검사가 못 잠그는 것 — 단위 그 자체.**
    «0~1 비율»·«주 수» 같은 말은 문자열로 단언할 수 없다. 여기서 잠그는 것은
    **이름(`metric`)과 «비교 규칙인가»(`baseline is None`)** 뿐이고, **단위는 문서가 든다**
    (`docs/part_a/14_evidence_fields.md` §3-2′).
    ⚠ **이 한계를 안 적으면 다음 사람이 «테스트가 다 잡아준다»고 믿는다.**
    """
    expected_metric = {
        RuleId.R1: "accuracy",
        RuleId.R2: "consecutive_missing_weeks",
        RuleId.R3: "activity_count",
        RuleId.R4: "norm_time",
        RuleId.R6: "error_share",
        RuleId.R5: None,
    }
    #: 🔴 «평소와 비교하지 않는» 규칙 — 임계값과 비교한다(임계를 baseline으로 적으면 거짓).
    no_baseline = {RuleId.R2, RuleId.R5, RuleId.R6}

    by_rule = _signals_by_rule()
    assert by_rule, "골든에 신호가 하나도 없다 — 이 검사가 눈이 멀었다"
    for rule_id, signals in by_rule.items():
        for signal in signals:
            assert signal.metric == expected_metric[rule_id], rule_id.value
            if rule_id in no_baseline:
                assert signal.baseline is None, (
                    f"{rule_id.value}가 baseline을 실었다 — 임계는 «평소»가 아니다"
                )
            else:
                assert signal.baseline is not None, f"{rule_id.value}에 기준선이 비었다"


def test_the_unit_table_in_the_design_doc_lists_every_metric() -> None:
    """🔴 문서 표가 **코드의 metric 전량**을 담고 있는지.

    규칙이 늘거나 `metric` 이름이 바뀌었는데 표가 안 따라오면, 백엔드가 **모르는 단위**를
    받는다. 위 검사는 이름을 잠그지만 «표에 그 줄이 있는가»는 안 본다.
    """
    doc = _DESIGN_DOC.read_text(encoding="utf-8")
    metrics = {
        signal.metric
        for signals in _signals_by_rule().values()
        for signal in signals
        if signal.metric
    }
    assert metrics, "metric을 가진 신호가 없다"
    missing = sorted(m for m in metrics if f"`{m}`" not in doc)
    assert not missing, f"단위 표에 없는 metric: {missing}"


def test_the_cited_records_come_from_the_week_that_observed_reports() -> None:
    """🔴 **근거가 `observed`와 같은 주에서 나온다** — 판정 창이 여러 주인 규칙에서.

    `_finding`이 판정 창을 **오래된 주 먼저** 담았고 `engine._evidence_items`가
    `triggers[:3]`으로 자르므로, **이번 주 근거가 통째로 잘려 나갔다.** 실측(2026-08-13):

        분석 주 2026-07-20 · signal.observed = 이번 주 값
        근거    2026-07-13 · 07-14 · 07-15   🔴 전부 지난 주

    화면에는 *"평소 72% → 이번 주 51%"* 옆에 **지난 주 문항 3개**가 붙는다.
    🔴 **날짜가 안 실릴 때는 안 보였다** — `occurred_on`을 실제 날짜로 바꾸면서 드러났다.

    ⚠ **R2는 이미 `reversed(streak)`로 최신 우선이었다** — 최신 우선이 원래 의도였고
    `_finding`을 쓰는 R1·R4만 빠져 있었다.
    """
    by_rule = _signals_by_rule()
    multi_week = {RuleId.R1, RuleId.R4}
    checked = 0
    for rule_id in multi_week:
        for signal in by_rule.get(rule_id, []):
            dates = [i.occurred_on for i in signal.evidence if i.occurred_on is not None]
            assert dates, f"{rule_id.value}: 근거에 날짜가 없다"
            #: 판정 창의 **마지막 주**가 `observed`의 주다 — 근거의 가장 이른 날짜가
            #: 그 주에 들어와야 «이번 주를 인용한다»가 성립한다.
            newest_week = max(dates) - timedelta(days=max(dates).weekday())
            oldest_week = min(dates) - timedelta(days=min(dates).weekday())
            assert newest_week == oldest_week, (
                f"{rule_id.value}: 근거가 여러 주에 걸쳐 있다 {sorted(dates)}"
            )
            checked += 1
    assert checked, "R1·R4 신호가 없다 — 이 검사가 눈이 멀었다"


def test_the_newest_week_survives_the_trigger_cap() -> None:
    """🔴 상한에 걸려 잘리는 것은 **오래된 주**여야 한다.

    판정 창 2주 × 주당 10~20문항이면 상한 3건은 **한 주로도 다 찬다.** 오래된 주가 앞에
    오면 이번 주가 **한 건도 안 남는다** — 그게 이 회귀의 형태였다.
    """
    from ai.detection.features import WeekFeatures
    from ai.detection.rules import RuleFinding, _finding

    weeks = tuple(
        WeekFeatures(
            week_monday=date(2026, 7, 13) + timedelta(weeks=i),
            n_solves=10, accuracy=0.8, submitted=True, norm_time=0.2,
            event_count=10, tagging_rate=1.0, cells=(), graded_count=10, timed_count=10,
        )
        for i in range(2)
    )
    finding: RuleFinding = _finding(RuleId.R1, 0.5, weeks)

    assert finding.evidence_weeks[0] == date(2026, 7, 20), (
        f"판정 창이 오래된 주부터 실린다 {finding.evidence_weeks}"
    )
