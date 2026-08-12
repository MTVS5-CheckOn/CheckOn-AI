"""**전량 스냅숏과 증분 요청이 같은 판정을 내는가** (99 #43 §12).

🔴 **저장하지 않으면 다음 요청에서 결과가 달라지는 구조라면 완료가 아니다.**
`FEATURE_WEEK.metrics`에는 R2·R3의 새 파생값이 없다 — 그래서 그 축을 **저장이 아니라
요청 계약**으로 닫는다: **원시 학습 기록은 증분, 작은 주간 집계는 rolling 10주 동봉.**

⚠ `db/models.py`·마이그레이션 **무접촉**이다. 집계 전문을 AI PG에 복제 저장하지 않는다.
"""

from __future__ import annotations

from typing import Any, Final

import pytest

from ai.contracts.detection import DetectRequest, DetectResponse
from ai.detection.engine import detect
from ai.detection.features import extract_features
from ai.evaluation.fake_snapshot import (
    StudentPlan,
    build_detect_request,
    take_single_week,
    take_week_range,
)

_PRIOR: Final = "2026-08-03"
_ANALYSIS: Final = "2026-08-10"
_WEEKS: Final = 10


def _plan() -> StudentPlan:
    """R2(연속 미제출)와 R3(학습량 급감)를 동시에 만드는 학생."""
    return StudentPlan(
        student_ref="st_inc",
        class_ref="cl_a1",
        weeks=_WEEKS,
        solves_per_week=(10,) * 9 + (1,),
        submit_ok=(True,) * 7 + (False, False, False),
        assignment_expected=3,
    )


def _full() -> DetectRequest:
    return build_detect_request(week_start=_ANALYSIS, seed=11, students=[_plan()])


def _snapshot(response: DetectResponse) -> dict[str, Any]:
    """비교 대상 — 발화 여부·score·evidence·signal_id·skip을 **값으로** 본다."""
    return {
        "signals": [
            (
                signal.signal_id,
                signal.rule_id.value,
                signal.score,
                signal.rank,
                tuple(
                    (item.source_table, item.record_id, item.summary)
                    for item in signal.evidence
                ),
            )
            for signal in response.signals
        ],
        "skipped": [
            (row.rule_id.value, row.reason, row.students)
            for row in response.stats.rules_skipped
        ],
    }


def _rolling_evidence(full: DetectRequest) -> tuple[Any, ...]:
    """최근 10주 집계 **전량** — 증분 요청에도 이걸 그대로 동봉한다."""
    return full.detection_evidence


def test_the_incremental_request_matches_the_full_snapshot() -> None:
    """🔴 **전량 vs 증분이 같은 판정**이어야 §12의 증분 축이 닫힌다.

    증분 = 이번 주 `learning_events` + 저장된 `FEATURE_WEEK` + **rolling 10주 집계**.
    """
    full = _full()
    full_request = take_week_range(full, week_start=_ANALYSIS, weeks_back=_WEEKS)
    expected = _snapshot(detect(full_request))

    #: 저장된 축적분 — 라우터가 `FEATURE_WEEK`에서 되살려 주입하는 것과 같은 모양이다.
    prior = take_week_range(full, week_start=_PRIOR, weeks_back=_WEEKS - 1)
    stored = {ref: feats.weeks for ref, feats in extract_features(prior).items()}

    #: 🔴 **집계는 잘라 보내지 않는다** — 그게 이 계약의 전부다.
    incremental = take_single_week(full, week_start=_ANALYSIS).model_copy(
        update={"detection_evidence": _rolling_evidence(full)}
    )
    assert len(incremental.learning_events) < len(full_request.learning_events), (
        "증분이 전량과 같은 이벤트를 들고 있다 — 증분을 재고 있지 않다"
    )
    assert len(incremental.detection_evidence) == len(full_request.detection_evidence)

    actual = _snapshot(detect(incremental, stored_features=stored))
    assert actual == expected, (
        "전량과 증분의 판정이 다르다 — rolling 집계 계약이 안 닫혔다"
    )
    assert expected["signals"], "아무 신호도 안 났다 — 이 검사가 아무것도 안 보고 있다"


def test_dropping_the_rolling_aggregates_changes_the_result() -> None:
    """🔴 **왜 rolling이 필요한지**를 값으로 남긴다 — 자르면 판정이 달라진다.

    ⚠ 달라지는 방향은 **fail-closed skip**이다(틀린 판정이 아니다). 그래도 **결과가
    달라지므로** 계약으로 못 박는다.
    """
    full = _full()
    expected = _snapshot(detect(take_week_range(full, week_start=_ANALYSIS, weeks_back=_WEEKS)))

    prior = take_week_range(full, week_start=_PRIOR, weeks_back=_WEEKS - 1)
    stored = {ref: feats.weeks for ref, feats in extract_features(prior).items()}
    #: 이번 주 집계만 보낸다(= 자른 경우).
    trimmed = take_single_week(full, week_start=_ANALYSIS)
    actual = _snapshot(detect(trimmed, stored_features=stored))

    assert actual != expected, "집계를 잘랐는데 결과가 같다 — 이 검사가 헛돈다"
    reasons = {reason for _rule, reason, _n in actual["skipped"]}
    assert "authoritative_evidence_missing" in reasons, actual["skipped"]


@pytest.mark.parametrize("weeks_back", [1, 5, _WEEKS])
def test_the_rolling_window_is_sliced_consistently(weeks_back: int) -> None:
    """⚠ 창을 자르는 도구가 **집계도 같은 창으로** 자른다 — 판정 입력이기 때문이다."""
    sliced = take_week_range(_full(), week_start=_ANALYSIS, weeks_back=weeks_back)
    weeks = {
        item.week_start
        for item in sliced.detection_evidence
        if hasattr(item, "week_start")
    }
    assert len(weeks) == weeks_back, f"{weeks_back}주를 잘랐는데 집계는 {len(weeks)}주다"
