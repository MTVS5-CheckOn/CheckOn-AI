"""평가 러너 계측의 순수 로직 — 혼동행렬(99 ㉠) · 토큰 분포(99 ⓧ 재료).

러너 본체는 실 LLM이 있어야 돌지만 **집계는 순수 함수**라 여기서 고정한다.
`confidence_buckets` 선례와 같은 자리다 — 이 수치가 곧 ⓐ·ⓧ의 판단 근거이므로
집계가 틀리면 값이 틀린다.
"""

from __future__ import annotations

from typing import Any

import pytest

from ai.evaluation.classify_eval import (
    AXIS_LABELS,
    ConfusionMatrix,
    confusion_matrix,
    render_confusion,
)
from ai.evaluation.counsel_llm_smoke import (
    LedgerRow,
    _pii_scan,
    _verdict,
    compare_reproduction,
    empty_response_rows,
    leaked_terms_label,
    render_s5_verdict,
    token_spread,
    uncertain_fragments_label,
    usage_axis_split,
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


# ── ㊪ 안전 판정의 축 분리 ─────────────────────────────────────────


def _smoke_data(*, uncertain: int, token_residue: int) -> dict[str, Any]:
    """`_verdict`가 읽는 최소 형태 — 안전 축 외에는 전부 깨끗하게 둔다.

    ⚠ S3는 빈 목록이라 차단 미탐 0이고, S1은 폴백 0, S2는 전부 산출이다. 그래야
    판정이 갈리는 이유가 **`pii` 두 값 하나뿐**이 된다.
    """
    return {
        "s1": {
            "rows": [
                {
                    "gate_passed": True,
                    "attempts": 1,
                    "elapsed_ms": 100,
                    "outcome": "ok",
                    "fallback_used": False,
                    "mask_residue": False,
                }
            ]
        },
        "s2": {"rows": [{"draft_status": "generated", "text": "초안"}]},
        "s3": {"rows": []},
        "pii": {
            "llm_texts": 2,
            "uncertain": uncertain,
            "masked": uncertain,
            "token_residue": token_residue,
        },
    }


def test_fail_closed_is_not_a_safety_violation() -> None:
    """🔴 `uncertain`은 **막은 것**이다 — 안전 위반이 아니라 가용성 문제다 (99 ㊪).

    4차(8/7)가 이 형태로 틀렸다: 안전 위반 실측이 0인데 `uncertain=4`(전부 정상 어휘
    오탐)가 `token_residue`와 합산돼 *"안전 불변식이 깨졌다"* 가 됐다.
    **fail-closed가 작동할수록 「데모 불가」가 되는 판정**이었다.
    """
    verdict = _verdict(_smoke_data(uncertain=99, token_residue=0))
    assert verdict.startswith("**데모 가능**"), verdict
    assert "가용성 경고" in verdict, verdict
    assert "99건" in verdict, verdict


def test_token_residue_is_a_safety_violation() -> None:
    """⚠ 반대 축은 살아 있어야 한다 — `⟪⟫`가 **학부모 문장에 남은 것**은 일어난 일이다.

    축을 가르면서 안전 축까지 느슨해지면 정반대 사고다.
    """
    verdict = _verdict(_smoke_data(uncertain=0, token_residue=1))
    assert verdict.startswith("**데모 불가**"), verdict
    assert "마스킹 토큰 잔존 1건" in verdict, verdict


def test_a_clean_run_is_demo_ready() -> None:
    """둘 다 0이면 조건 없는 「데모 가능」 — 경고 문구가 붙지 않는다."""
    verdict = _verdict(_smoke_data(uncertain=0, token_residue=0))
    assert verdict.startswith("**데모 가능**"), verdict
    assert "가용성 경고" not in verdict, verdict


def test_uncertain_does_not_mask_a_real_residue() -> None:
    """🔴 둘이 동시에 있으면 **안전이 이긴다** — 가용성 경고가 안전 위반을 덮지 않는다."""
    verdict = _verdict(_smoke_data(uncertain=99, token_residue=1))
    assert verdict.startswith("**데모 불가**"), verdict


def test_pii_scan_keeps_the_fragment_that_was_masked() -> None:
    """🔴 건수만 남기면 **오탐/진탐을 영원히 못 가른다**(4차가 그랬다 · 99 ㊪).

    ⚠ 여기서 쓰는 문자열은 실 산출이 아니라 **형태 확인용**이다 — `uncertain_detail`이
    조각과 문맥을 담는지만 본다.
    """
    data = {
        "s1": {"rows": [{"text": "이번 주 제출을 이어가는 태도가 좋았습니다."}]},
        "s2": {"rows": []},
        "s3": {"rows": []},
    }
    scan = _pii_scan(data)
    assert scan["llm_texts"] == 1
    if not scan["uncertain"]:
        pytest.skip("이 문장이 더는 fail-closed를 만들지 않는다 — 조각 형태만 검사한다")
    detail = scan["uncertain_detail"]
    assert len(detail) == scan["uncertain"]
    hits = detail[0]["hits"]
    assert hits, "불확실인데 조각이 비었다 — 무엇이 걸렸는지 알 수 없다"
    assert all(h["fragment"] and h["context"] for h in hits), hits


# ── #144 사용 축 — AI_RUN 원장 (8/9 신설) ─────────────────────────


def _row(*, calls: int, params: dict[str, Any] | None, provider: str | None = "x") -> LedgerRow:
    return LedgerRow(
        capability="composition",
        calls=calls,
        generation_params=params,
        model_provider=provider,
        model_name="m",
        prompt_version="p1",
    )


def test_no_ai_run_rows_is_not_a_pass() -> None:
    """🔴 표본 0이면 **"안 봤다"** 다 — 「위반 0건」으로 내면 5차가 거짓 초록이 된다.

    ⚠ 이 러너가 4차까지 `AI_RUN`을 아예 안 읽었다는 사실 자체가 이 단정의 이유다.
    관측이 끊겨도 표는 그려지므로, 끊긴 것과 깨끗한 것을 문면에서 갈라야 한다.
    """
    split = usage_axis_split([])
    assert split["ai_run_rows"] == 0
    assert "안 본 것" in split["verdict"]
    assert "✅" not in split["verdict"]


def test_params_written_on_a_zero_call_run_is_the_lie_144_removed() -> None:
    """🔴 0콜 실행에 샘플링 파라미터가 적히면 *"그 값으로 돌렸다"* 가 **거짓**이다."""
    split = usage_axis_split([_row(calls=0, params={"temperature": 0.0}, provider=None)])
    assert split["zero_call_rows_with_params"] == 1
    assert split["verdict"].startswith("🔴")


def test_a_called_run_without_params_is_a_missing_reproduction_key() -> None:
    """반대 방향 — 호출이 있는데 비면 재현 키가 빈 것이다(불변식 8).

    🔴 **두 방향을 한 카운터로 세지 않는다** — 「채워짐/비어 있음」만 보면 어느 쪽이
    틀렸는지가 사라지고 대응이 정반대다(빼야 하는가 / 넣어야 하는가).
    """
    split = usage_axis_split([_row(calls=2, params=None)])
    assert split["called_rows_without_params"] == 1
    assert split["zero_call_rows_with_params"] == 0


def test_the_usage_axis_holds_when_both_directions_are_clean() -> None:
    split = usage_axis_split(
        [
            _row(calls=3, params={"temperature": 0.0, "seed": 7}),
            _row(calls=0, params=None, provider=None),
        ]
    )
    assert split == {
        "ai_run_rows": 2,
        "with_calls": 1,
        "zero_calls": 1,
        "zero_call_rows_with_params": 0,
        "called_rows_without_params": 0,
        "observed_params": ['{"seed": 7, "temperature": 0.0}'],
        "model_fields_agree": True,
        "verdict": "✅ 사용 축 일치",
    }


def test_a_row_whose_model_fields_disagree_with_its_params_is_flagged() -> None:
    """🔴 **한 행 안에서 축이 갈리는 것**을 본다 — `worker.py`가 경고한 형태다.

    `model_provider`는 조건부인데 `generation_params`는 상수인 행이 있으면, 읽는 쪽이
    *"이 실행이 LLM을 썼나"* 를 어느 필드로 보느냐에 따라 다르게 답한다.
    """
    split = usage_axis_split([_row(calls=0, params=None, provider="openai_compat")])
    assert split["model_fields_agree"] is False


# ── §8 결함표가 측정에서 나오는가 (8/9) ───────────────────────────


def test_leaked_terms_come_from_the_run_not_from_prose() -> None:
    """🔴 §8 D0의 근거는 **그 회차 본문에서 실제로 찾은 조각**뿐이다.

    4차(8/7)는 산출물에 **없는 문장**(`게으른 모습이 관찰되었습니다`)을 근거로 D0을
    「높음(신규·안전)」에 올렸고 같은 회차에 철회했다(D0′).
    """
    assert leaked_terms_label([]) == "—"
    assert leaked_terms_label([{"leaked_terms": ["게으른", "산만한"]}]) == "`게으른`, `산만한`"


def test_the_retracted_d0_sentence_is_not_a_literal_in_the_runner() -> None:
    """🔴 **철회된 문장이 러너에 리터럴로 남아 있으면 다음 회차가 되살린다.**

    4차의 철회는 **리포트 마크다운**에만 적혔는데 다음 실행이 그 파일을 덮어쓴다 —
    5차가 D0·D5·D6를 통째로 부활시켰다. 철회는 **산출물이 아니라 산출하는 코드**에
    적혀야 한다. 이 단정이 그 회귀를 막는다.
    """
    import ast
    from pathlib import Path

    import ai.evaluation.counsel_llm_smoke as runner

    # 🔴 **리터럴만 본다** — 주석은 대상이 아니다. *"왜 뺐는지"* 를 적은 주석까지 걸면
    #   검사의 이름이 실제 의도보다 넓어지고, 설명을 못 남기게 된다(로그 85).
    #   `ast`는 주석을 트리에 담지 않으므로 그 구분이 공짜다.
    tree = ast.parse(Path(runner.__file__).read_text(encoding="utf-8"))
    source = "\n".join(
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    )
    assert "게으른 모습이 관찰되었습니다" not in source, (
        "🔴 철회된 D0의 근거 문장이 러너에 다시 박혔다 — 다음 회차 리포트가 그걸 "
        "「높음(신규·안전)」으로 싣는다"
    )
    assert "counsel·briefing 어느 쪽도 넣지 않는다" not in source, (
        "🔴 철회된 D6의 원인 서술이 다시 박혔다 — `seed`는 실려 있다(4차 D6′)"
    )
    assert "실서버 최초 연결" not in source, (
        "🔴 회차·브랜치를 리터럴로 박으면 두 번째 회차부터 거짓이 된다"
    )


def test_uncertain_fragments_are_listed_not_just_counted() -> None:
    """건수는 판정의 근거가 못 된다(99 ㊪) — 조각이 있어야 사람이 진탐/오탐을 가른다."""
    pii = {
        "uncertain": 1,
        "uncertain_detail": [
            {"where": "s2/rows/0", "hits": [{"fragment": "하면서", "token": "⟪확인필요⟫"}]}
        ],
    }
    assert uncertain_fragments_label(pii) == "`하면서`→`⟪확인필요⟫`"
    assert uncertain_fragments_label({"uncertain": 0, "uncertain_detail": []}) == "—"
