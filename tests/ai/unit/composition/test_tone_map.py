"""톤 매핑 데이터 파일·로더 — 24조합 완전성·fail-closed (05 §1·§2·§3·§5).

정본은 `docs/part_a/05_tone_mapping.md`이고 yaml은 파생물이다. 이 테스트는 yaml이 문서와
어긋나면 깨지도록 **문서의 값을 손으로 적어** 대조한다(엔진·yaml 산출을 붙여넣지 않는다).
"""

from __future__ import annotations

import pytest

from ai.composition.tone import (
    EXPECTED_COMBINATION_COUNT,
    ToneMapError,
    combination_key,
    expected_keys,
    load_tone_map,
    parse_tone_map,
)

#: 05 §1 축별 1차 규칙의 값 집합 — 문서에서 손으로 옮겼다.
_DOC_AXIS_VOCAB = {
    "comm": {"data", "narrative"},
    "sensitivity": {"anxious", "direct"},
    "interest": {"grade", "attitude", "admission"},
    "frequency": {"frequent", "monthly"},
}


def test_all_24_combinations_present() -> None:
    """05 §5 — 24键 전부 존재. 데카르트곱과 정확히 일치해야 한다."""
    tone_map = load_tone_map()
    assert len(tone_map.combinations) == EXPECTED_COMBINATION_COUNT == 24
    assert set(tone_map.combinations) == set(expected_keys(tone_map.axis_rules))


def test_axis_vocabulary_matches_doc() -> None:
    """축 어휘가 05 §1 표와 일치 — 조합 키 공간의 정본."""
    axis_rules = load_tone_map().axis_rules
    assert {axis: set(values) for axis, values in axis_rules.items()} == _DOC_AXIS_VOCAB


def test_combination_key_format() -> None:
    """05 §5 규정 — comm.sens.interest.freq."""
    assert combination_key("data", "anxious", "grade", "frequent") == (
        "data.anxious.grade.frequent"
    )


@pytest.mark.parametrize(
    ("key", "blocks", "sentences", "buffer"),
    [
        # 05 §2 표에서 손으로 옮긴 경계 행 — 1·7·14·24
        ("data.anxious.grade.frequent", ("인사", "수치 요약", "안심 포인트", "제안"), 2, 2),
        ("data.direct.grade.frequent", ("결론(수치)", "근거", "제안"), 2, 1),
        ("narrative.anxious.grade.monthly", ("인사", "한 달 서사", "성장 해설", "제안"), 5, 2),
        ("narrative.direct.admission.monthly", ("핵심", "로드맵 서사", "제안"), 4, 1),
    ],
)
def test_rule_values_match_doc_table(
    key: str, blocks: tuple[str, ...], sentences: int, buffer: int
) -> None:
    rule = load_tone_map().combinations[key]
    assert rule.blocks == blocks
    assert rule.sentences_per_block == sentences
    assert rule.buffer_level == buffer


def test_buffer_level_is_1_or_2_across_table() -> None:
    """05 §2 완충 열은 anxious=2 · direct=1 뿐이다(0은 표에 없음)."""
    for key, rule in load_tone_map().combinations.items():
        expected = 2 if ".anxious." in key else 1
        assert rule.buffer_level == expected, key


def test_note_is_none_when_absent() -> None:
    """비고 없는 행은 키를 생략하고 로더가 None을 준다(일관 처리)."""
    tone_map = load_tone_map()
    assert tone_map.combinations["data.anxious.grade.monthly"].note is None
    assert (
        tone_map.combinations["data.anxious.grade.frequent"].note
        == "수치 뒤 즉시 맥락 문장 의무"
    )


def test_rule_lookup_rejects_unknown_combination() -> None:
    """없는 조합은 조용한 폴백이 아니라 실패."""
    with pytest.raises(ToneMapError, match="등록되지 않은"):
        load_tone_map().rule("data", "anxious", "grade", "weekly")


# ── fail-closed (05 §5 "로드 시 검증") ────────────────────────────


def _minimal_raw() -> dict[str, object]:
    axis_rules = {axis: dict.fromkeys(vals, "설명") for axis, vals in _DOC_AXIS_VOCAB.items()}
    combos = {
        key: {"blocks": ["인사", "제안"], "sentences_per_block": 2, "buffer_level": 1}
        for key in expected_keys(axis_rules)
    }
    return {
        "version": "0.1",
        "axis_rules": axis_rules,
        "combinations": combos,
        "percentile_softening": {},
    }


def test_missing_combination_fails_closed() -> None:
    raw = _minimal_raw()
    combos = raw["combinations"]
    assert isinstance(combos, dict)
    combos.pop("data.anxious.grade.frequent")
    with pytest.raises(ToneMapError, match="누락"):
        parse_tone_map(raw)


def test_unknown_combination_fails_closed() -> None:
    raw = _minimal_raw()
    combos = raw["combinations"]
    assert isinstance(combos, dict)
    combos["data.anxious.grade.weekly"] = {
        "blocks": ["인사"],
        "sentences_per_block": 1,
        "buffer_level": 1,
    }
    with pytest.raises(ToneMapError, match="미등록"):
        parse_tone_map(raw)


def test_axis_vocabulary_change_fails_closed() -> None:
    """축 어휘가 늘면 조합 수가 24가 아니게 되어 기동 실패."""
    raw = _minimal_raw()
    axis_rules = raw["axis_rules"]
    assert isinstance(axis_rules, dict)
    axis_rules["frequency"]["weekly"] = "설명"
    with pytest.raises(ToneMapError, match="24조합"):
        parse_tone_map(raw)


def test_missing_section_fails_closed() -> None:
    raw = _minimal_raw()
    del raw["percentile_softening"]
    with pytest.raises(ToneMapError, match="percentile_softening"):
        parse_tone_map(raw)


# ── §3 하위 백분위 완곡 규칙 ──────────────────────────────────────


def test_percentile_bands_match_doc() -> None:
    """05 §3 — ≥50 제한 없음 / 30~49 병기 / <30 병기+단독 금지."""
    bands = {b["id"]: b for b in load_tone_map().percentile_softening["bands"]}
    assert bands["high"]["min_percentile"] == 50
    assert bands["high"]["requires_pairing"] is False
    assert (bands["mid"]["min_percentile"], bands["mid"]["max_percentile"]) == (30, 49)
    assert bands["mid"]["requires_pairing"] is True
    assert bands["low"]["max_percentile"] == 29
    assert bands["low"]["standalone_sentence_allowed"] is False
    assert bands["low"]["next_plan_adjacent"] is True


def test_percentile_anxious_overrides_match_doc() -> None:
    """anxious는 한 단계 엄격 + 50~69 병기 권장 + 해설 블록 후치."""
    overrides = load_tone_map().percentile_softening["anxious_overrides"]
    assert overrides["stricter_by_one_band"] is True
    assert overrides["pairing_recommended_from_percentile"] == 50
    assert overrides["defer_percentile_block_after_suggestion"] is True
