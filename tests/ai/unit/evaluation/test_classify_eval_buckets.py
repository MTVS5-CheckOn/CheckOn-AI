"""분류 평가 러너의 순수 로직 — confidence 구간 집계 · 축 순서 (99 ⓝ①).

러너 본체는 실 LLM이 있어야 돌지만 **집계는 순수 함수**라 여기서 고정한다.
99 ⓐ(임계값)가 이 표를 근거로 삼으므로 표가 틀리면 계약 값이 틀린다.
"""

from __future__ import annotations

import pytest

from ai.evaluation.classify_eval import (
    AXES,
    BUCKET_WIDTH,
    MIN_BUCKET_SAMPLES,
    Bucket,
    CaseRow,
    confidence_buckets,
    render_buckets,
)


def test_axes_order_is_the_single_source() -> None:
    """🔴 세 배열이 같은 순서를 쓴다 — 순서가 어긋나면 **조용히 틀린 표**가 나온다.

    정확도는 그럴듯한 숫자로 나오고 아무도 눈치채지 못한다.
    """
    assert AXES == ("topic", "sentiment", "urgency")
    row = CaseRow(
        expected=("grade", "normal", "immediate"),
        actual=("grade", "complaint", "immediate"),
        confidence=(0.9, 0.4, 0.8),
        hit=(True, False, True),
        classified=True,
    )
    payload = row.as_json()
    assert payload["axes"] == list(AXES)
    for key in ("expected", "actual", "confidence", "hit"):
        assert len(payload[key]) == len(AXES)  # type: ignore[arg-type]
    # sentiment(=index 1)만 틀린 케이스가 세 배열에서 같은 자리에 있다.
    assert payload["actual"][1] != payload["expected"][1]  # type: ignore[index]
    assert payload["hit"][1] is False  # type: ignore[index]


# ── 구간 집계 ─────────────────────────────────────────────────────


def test_buckets_cover_zero_to_one_at_width() -> None:
    buckets = confidence_buckets([])
    assert len(buckets) == int(round(1.0 / BUCKET_WIDTH))
    assert buckets[0].low == pytest.approx(0.0)
    assert buckets[-1].high == pytest.approx(1.0)


def test_confidence_one_falls_in_the_top_bucket() -> None:
    """🔴 상단 경계 1.0을 마지막 구간에 넣는다 — 안 그러면 혼자 빈 구간을 만든다."""
    buckets = confidence_buckets([(1.0, True)])
    assert buckets[-1].samples == 1
    assert sum(b.samples for b in buckets) == 1


def test_accuracy_and_samples_travel_together() -> None:
    """🔴 **표본 수가 정확도와 항상 붙어 다닌다**(A-3).

    구간 5건짜리 정확도는 근거가 못 되는데 정확도만 적으면 표에서 다른 구간과 똑같이
    생겼다 — ⓐ가 그 숫자를 근거로 삼는다.
    """
    scored = [(0.95, True)] * 8 + [(0.95, False)] * 2 + [(0.15, False)] * 3
    buckets = confidence_buckets(scored)

    top = buckets[-1]
    assert (top.samples, top.hits) == (10, 8)
    assert top.accuracy == pytest.approx(0.8)
    assert not top.thin  # 10건 = MIN_BUCKET_SAMPLES 이상

    low = buckets[1]  # 0.1~0.2
    assert low.samples == 3
    assert low.thin, "표본 3건인데 '충분'으로 표시되면 잘못된 근거가 된다"


def test_empty_bucket_accuracy_is_none_not_zero() -> None:
    """표본 0을 0%로 표시하면 "정확도가 낮다"로 오독된다 — None이 정직하다."""
    empty = Bucket(low=0.0, high=0.1, samples=0, hits=0)
    assert empty.accuracy is None
    assert not empty.thin  # "얇다"가 아니라 "없다"


def test_min_bucket_samples_is_documented_threshold() -> None:
    assert MIN_BUCKET_SAMPLES == 10


def test_every_sample_lands_in_exactly_one_bucket() -> None:
    """경계값(0.1·0.2…)에서 이중 계수·누락이 없어야 한다."""
    scored = [(round(i * 0.05, 2), True) for i in range(21)]  # 0.00~1.00
    buckets = confidence_buckets(scored)
    assert sum(b.samples for b in buckets) == len(scored)


# ── 렌더 ──────────────────────────────────────────────────────────


def test_render_omits_empty_buckets_and_marks_thin_ones() -> None:
    lines = render_buckets("topic", confidence_buckets([(0.95, True)] * 2))
    body = "\n".join(lines)
    assert "[topic]" in body
    assert "n=2" in body
    assert "표본 부족" in body, "얇은 구간에 경고가 없으면 근거로 오용된다"
    assert body.count("\n") == 1, "표본 0 구간이 표에 남았다(노이즈)"
