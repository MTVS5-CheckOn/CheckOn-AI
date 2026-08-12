"""근거 없는 finding이 **병합·상한을 오염시키지 않는가** (99 #43).

🔴 종전에는 경보 **전체**를 근거 유무로 판정했다. R1+R2가 병합된 경보에서 R2만 근거가
없으면 ⓐ 경보 전체가 사라지거나 ⓑ 근거 없는 R2가 primary로 남았다 — 어느 쪽이든
**응답이 주장하는 규칙과 실린 근거가 갈린다.**
"""

from __future__ import annotations

from datetime import date
from typing import Final

from ai.contracts.detection import RuleId, SignalType
from ai.detection.engine import _drop_findings_without_evidence
from ai.detection.evidence import EMPTY_EVIDENCE, StudentEvidence
from ai.detection.ranking import StudentAlert
from ai.detection.rules import RuleFinding

_WEEK: Final = date(2026, 8, 10)
_STUDENT: Final = "st_merge"
_CLASS: Final = "cl_a1"


def _finding(rule: RuleId, signal: SignalType, score: float) -> RuleFinding:
    return RuleFinding(
        rule_id=rule, signal_type=signal, score=score, evidence_weeks=(_WEEK,)
    )


def _alert(*findings: RuleFinding) -> StudentAlert:
    ordered = sorted(findings, key=lambda f: f.score, reverse=True)
    return StudentAlert(
        student_ref=_STUDENT,
        class_ref=_CLASS,
        primary=ordered[0],
        merged=tuple(ordered),
        is_auto_flag=False,
        is_advisory=all(f.rule_id is RuleId.R4 for f in ordered),
    )


def _learning_events() -> dict[tuple[str, date], list[str]]:
    return {(_STUDENT, _WEEK): ["le_1", "le_2"]}


def _trim(
    alert: StudentAlert, evidence: StudentEvidence = EMPTY_EVIDENCE
) -> tuple[StudentAlert | None, dict[tuple[str, str], int]]:
    from collections import Counter  # noqa: PLC0415

    skipped: Counter[tuple[str, str]] = Counter()
    trimmed = _drop_findings_without_evidence(
        alert, _learning_events(), {_STUDENT: evidence}, skipped, _WEEK
    )
    return trimmed, dict(skipped)


def test_a_healthy_r1_survives_when_r2_has_no_evidence() -> None:
    """🔴 **R2가 근거 없다고 R1까지 사라지면 안 된다.**"""
    alert = _alert(
        _finding(RuleId.R1, SignalType.ACC_DROP, 0.4),
        _finding(RuleId.R2, SignalType.SUBMIT_DROP, 0.9),
    )
    trimmed, skipped = _trim(alert)
    assert trimmed is not None, "정상 R1까지 사라졌다"
    assert [f.rule_id for f in trimmed.merged] == [RuleId.R1]
    assert skipped == {("R2", "evidence_absent"): 1}


def test_the_primary_is_recomputed_after_the_drop() -> None:
    """🔴 **제거된 finding의 높은 score가 primary로 남으면 안 된다.**"""
    alert = _alert(
        _finding(RuleId.R1, SignalType.ACC_DROP, 0.4),
        _finding(RuleId.R2, SignalType.SUBMIT_DROP, 0.9),
    )
    assert alert.primary.rule_id is RuleId.R2  # 병합 시점의 대표
    trimmed, _ = _trim(alert)
    assert trimmed is not None
    assert trimmed.primary.rule_id is RuleId.R1, "근거 없는 규칙이 대표로 남았다"
    assert trimmed.primary.score == 0.4, "제거된 finding의 score가 남았다"


def test_an_alert_with_no_surviving_finding_is_dropped() -> None:
    """근거가 하나도 없으면 신호를 만들지 않는다 — 상한 슬롯도 안 쓴다."""
    alert = _alert(_finding(RuleId.R2, SignalType.SUBMIT_DROP, 0.9))
    trimmed, skipped = _trim(alert)
    assert trimmed is None
    assert skipped == {("R2", "evidence_absent"): 1}


def test_advisory_is_recomputed_when_only_r4_survives() -> None:
    """R4만 남으면 **참고 표시**가 된다(04 §1 R4 재정의) — 상한 밖으로 빠진다."""
    alert = _alert(
        _finding(RuleId.R4, SignalType.HIDDEN_RISK, 0.3),
        _finding(RuleId.R3, SignalType.VOLUME_GAP, 0.8),
    )
    assert alert.is_advisory is False
    trimmed, _ = _trim(alert)
    assert trimmed is not None
    assert [f.rule_id for f in trimmed.merged] == [RuleId.R4]
    assert trimmed.is_advisory is True


def test_an_untouched_alert_is_returned_as_is() -> None:
    """🔴 전부 근거가 있으면 **같은 객체**다 — 재조립이 순서를 흔들지 않는다."""
    alert = _alert(
        _finding(RuleId.R1, SignalType.ACC_DROP, 0.5),
        _finding(RuleId.R4, SignalType.HIDDEN_RISK, 0.3),
    )
    trimmed, skipped = _trim(alert)
    assert trimmed is alert
    assert skipped == {}
