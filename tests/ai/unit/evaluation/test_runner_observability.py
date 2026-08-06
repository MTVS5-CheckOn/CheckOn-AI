"""평가 러너 계측의 순수 로직 — 혼동행렬(99 ㉠) · 토큰 분포(99 ⓧ 재료).

러너 본체는 실 LLM이 있어야 돌지만 **집계는 순수 함수**라 여기서 고정한다.
`confidence_buckets` 선례와 같은 자리다 — 이 수치가 곧 ⓐ·ⓧ의 판단 근거이므로
집계가 틀리면 값이 틀린다.
"""

from __future__ import annotations

import pytest

from ai.evaluation.classify_eval import (
    AXIS_LABELS,
    ConfusionMatrix,
    confusion_matrix,
    render_confusion,
)
from ai.evaluation.counsel_llm_smoke import (
    compare_reproduction,
    empty_response_rows,
    render_s5_verdict,
    token_spread,
)

# ── ㉠ 혼동행렬 ───────────────────────────────────────────────────


def test_matrix_keeps_labels_that_never_appeared() -> None:
    """🔴 표본에 안 나온 라벨도 **0으로 남는다**.

    빠지면 "그 값이 한 번도 예측되지 않았다"는 사실 자체가 표에서 사라진다 — 그건
    관측 결과이지 노이즈가 아니다.
    """
    matrix = confusion_matrix([("grade", "grade")], ("grade", "schedule", "etc"))
    assert matrix.labels == ("grade", "schedule", "etc")
    assert matrix.counts["schedule"] == {"grade": 0, "schedule": 0, "etc": 0}


def test_rows_are_truth_and_columns_are_prediction() -> None:
    """🔴 **행=정답 · 열=예측.** 뒤집으면 FN과 FP가 통째로 바뀐다."""
    matrix = confusion_matrix([("complaint", "normal")], ("normal", "complaint"))
    assert matrix.counts["complaint"]["normal"] == 1
    assert matrix.counts["normal"]["complaint"] == 0


def test_false_negative_is_the_miss_not_the_over_flag() -> None:
    """🔴 ㉠의 핵심 — **놓침(FN)과 오검(FP)을 가른다.**

    §7이 `complaint`만 재현율 95%로 높게 잡은 이유는 **민원 놓침이 최악**이라서다.
    complaint→normal은 놓침이고, normal→complaint는 재현율 정의상 무관하다.
    """
    pairs = [
        ("complaint", "normal"),  # 놓침
        ("complaint", "normal"),  # 놓침
        ("normal", "complaint"),  # 오검
        ("complaint", "complaint"),
        ("normal", "normal"),
    ]
    matrix = confusion_matrix(pairs, ("normal", "complaint"))

    assert matrix.false_negatives("complaint") == 2
    assert matrix.false_positives("complaint") == 1
    assert matrix.total() == 5
    assert matrix.hits() == 2


def test_recall_alone_cannot_tell_the_direction() -> None:
    """재현율이 같아도 방향이 다를 수 있다 — 그래서 교차표가 필요하다.

    두 표본 모두 complaint 재현율 2/3이지만, 한쪽은 오검이 0이고 다른 쪽은 2다.
    대응(프롬프트 조정 vs 무대응)이 갈리는데 재현율만으로는 구분되지 않는다.
    """
    lean = confusion_matrix(
        [("complaint", "complaint")] * 2 + [("complaint", "normal")], ("normal", "complaint")
    )
    noisy = confusion_matrix(
        [("complaint", "complaint")] * 2
        + [("complaint", "normal")]
        + [("normal", "complaint")] * 2,
        ("normal", "complaint"),
    )
    def recall(m: ConfusionMatrix) -> float:
        row = m.counts["complaint"]
        return row["complaint"] / sum(row.values())

    assert recall(lean) == pytest.approx(recall(noisy))
    assert lean.false_positives("complaint") == 0
    assert noisy.false_positives("complaint") == 2


def test_confusions_are_sorted_by_size_and_exclude_hits() -> None:
    """적중 칸은 빼고 **많은 순**으로 — 어디로 새는지가 대응을 정한다."""
    pairs = [("grade", "etc")] * 3 + [("schedule", "etc")] + [("grade", "grade")] * 5
    matrix = confusion_matrix(pairs, ("grade", "schedule", "etc"))
    assert matrix.confusions() == [("grade", "etc", 3), ("schedule", "etc", 1)]


def test_axis_labels_come_from_the_contract_enums() -> None:
    """라벨 정본은 `contracts/counsel.py`다 — 러너가 어휘를 따로 들지 않는다."""
    from ai.contracts.counsel import InquirySentiment, InquiryTopic, InquiryUrgency

    assert AXIS_LABELS["topic"] == tuple(v.value for v in InquiryTopic)
    assert AXIS_LABELS["sentiment"] == tuple(v.value for v in InquirySentiment)
    assert AXIS_LABELS["urgency"] == tuple(v.value for v in InquiryUrgency)


def test_render_says_so_when_nothing_is_wrong() -> None:
    """오분류 0건이면 빈 표가 아니라 그렇게 적는다(빈 표는 '집계 실패'로 읽힌다)."""
    matrix = confusion_matrix([("normal", "normal")], ("normal", "complaint"))
    body = "\n".join(render_confusion("sentiment", matrix))
    assert "오분류 없음" in body
    assert "[sentiment]" in body


def test_empty_matrix_totals_are_zero_not_error() -> None:
    empty = confusion_matrix([], ("normal", "complaint"))
    assert isinstance(empty, ConfusionMatrix)
    assert (empty.total(), empty.hits()) == (0, 0)


# ── ⓧ 토큰 분포 ───────────────────────────────────────────────────


def test_spread_reports_nearest_rank_p90_not_interpolated() -> None:
    """🔴 p90은 **실측값**이다 — 보간하면 관측되지 않은 수가 상한 근거가 된다."""
    spread = token_spread("counselor", list(range(1, 11)))  # 1..10
    assert spread.samples == 10
    assert (spread.minimum, spread.maximum) == (1, 10)
    assert spread.p90 == 9  # ceil(10*0.9)=9 → 9번째 = 9. 보간이면 9.1이 된다
    assert spread.p90 in range(1, 11)


def test_spread_of_single_sample_is_that_sample() -> None:
    spread = token_spread("narrator", [42])
    assert (spread.minimum, spread.median, spread.p90, spread.maximum) == (42, 42, 42, 42)


def test_empty_spread_is_zeros_and_marked_by_sample_count() -> None:
    """표본 0이면 전부 0 — 하지만 `samples=0`이 그게 '측정 없음'임을 말한다."""
    spread = token_spread("counselor", [])
    assert spread.samples == 0
    assert (spread.minimum, spread.median, spread.p90, spread.maximum) == (0, 0, 0, 0)


def test_spread_is_computed_per_role_not_pooled() -> None:
    """🔴 role을 섞으면 ⓧ에 못 쓴다 — 브리핑(한 줄)과 초안(문단)은 성격이 다르다."""
    narrator = token_spread("narrator", [30, 35, 40])
    counselor = token_spread("counselor", [300, 350, 400])
    assert narrator.maximum < counselor.minimum
    assert narrator.role != counselor.role


# ── 빈 응답 ───────────────────────────────────────────────────────


@pytest.mark.parametrize("blank", [None, "", "   ", "\n\t"])
def test_blank_text_counts_as_empty(blank: str | None) -> None:
    """🔴 공백만 있는 본문도 빈 응답이다 — 길이 0만 세면 `"   "`가 산출로 잡힌다.

    ⚠ 이 테스트를 처음 쓸 때 구현이 공백을 안 걸렀는데 **테스트 쪽에서 입력을 `""`로
    바꿔치기해 통과시켰다**. 통과하는 방식이 결함을 가린 사례라 구현을 고쳤다.
    """
    rows: list[dict[str, object]] = [{"text": blank}]
    assert len(empty_response_rows(rows)) == 1


def test_empty_rows_are_separated_from_produced_ones() -> None:
    rows: list[dict[str, object]] = [
        {"text": "정답률은 62%였습니다."}, {"text": ""}, {"text": None}
    ]
    assert len(empty_response_rows(rows)) == 2


# ── S5 재현성 판정 (99 ㉣) ────────────────────────────────────────


def test_both_empty_is_not_comparable_and_not_identical() -> None:
    """🔴 ㉣의 본체 — `"" == ""`가 True라 **"둘 다 없음"이 "재현됨"으로** 보고됐다(8/6 3차).

    ⚠ 여기서 False를 기대하면 안 된다. False는 "다른 출력이 나왔다"는 관측이고,
    출력이 아예 없는 것은 다른 사건이다. **"못 잰다"가 맞다.**
    """
    result = compare_reproduction("", "")
    assert result.comparable is False
    assert result.identical is None  # 🔴 True도 False도 거짓 판정이다


def test_one_sided_empty_is_not_comparable_either() -> None:
    """1차(`0 / 298`)도 판정 불가였다 — 빈 산출이 **섞이기만 해도** 재현성 측정이 아니다.

    이 케이스가 False로 찍혔던 탓에 *"서버가 seed를 무시한다"* 로 성급히 확정했다가
    되돌렸다(결정 로그 47). 3차의 True와 **같은 상태인데 정반대 값**이었다.
    """
    assert compare_reproduction("", "초안 본문 298자…").comparable is False
    assert compare_reproduction("초안 본문 298자…", "").identical is None


def test_comparable_pair_still_reports_identical_or_not() -> None:
    """양쪽에 산출이 있으면 원래대로 판정한다 — 가드가 정상 경로를 막지 않는다."""
    same = compare_reproduction("같은 문장입니다.", "같은 문장입니다.")
    differ = compare_reproduction("같은 문장입니다.", "다른 문장입니다.")
    assert (same.comparable, same.identical) == (True, True)
    assert (differ.comparable, differ.identical) == (True, False)


@pytest.mark.parametrize(
    ("first", "second"), [("", ""), ("   ", "\n"), ("", "산출 있음"), ("산출 있음", "  ")]
)
def test_report_never_says_identical_when_it_could_not_measure(
    first: str, second: str
) -> None:
    """🔴 수용 기준 — 빈 산출일 때 리포트에 **"동일"이 찍히지 않는다.**

    다음 회차에 길이 칸을 아무도 안 볼 수 있다. 문장 하나로 상태가 드러나야 한다.
    """
    verdict = render_s5_verdict(compare_reproduction(first, second))
    assert "동일" not in verdict
    assert "비교 불가" in verdict


def test_verdict_names_which_side_was_empty() -> None:
    """한쪽만 비었으면 어느 쪽인지 적는다 — "양쪽 0자"로 뭉치면 1차 형태가 사라진다."""
    assert "양쪽" in render_s5_verdict(compare_reproduction("", ""))
    assert "1회차" in render_s5_verdict(compare_reproduction("", "산출"))
    assert "2회차" in render_s5_verdict(compare_reproduction("산출", ""))


def test_blank_only_output_is_not_a_produced_one() -> None:
    """공백만 있는 산출도 빈 것으로 본다 — `empty_response_rows`와 같은 규칙이다."""
    assert compare_reproduction("   ", "   ").comparable is False
