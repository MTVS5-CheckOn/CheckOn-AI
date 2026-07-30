"""완충 사전 데이터 파일·검출 — A군 금칙 20 · B군 치환 30 = 50항 (05 §4·§5).

정본은 `docs/part_a/05_tone_mapping.md` §4이고 yaml은 파생물이다. 기대값은 문서에서 손으로
옮겼다.

**활용형 회귀는 두 묶음이다:**
- 어간이 유지되는 활용형 → 현재 잡힌다(통상 assert)
- 어간이 변형되는 활용형(르·하 불규칙) → **현재 미탐이라 xfail**. 99 D ⑰로 형태소 접두
  매칭을 도입하면 xpass로 뒤집혀 자동으로 드러난다.
"""

from __future__ import annotations

import pytest

from ai.composition.buffer_lexicon import (
    EXPECTED_TERM_COUNT,
    BufferLexiconError,
    find_forbidden,
    forbidden_terms,
    load_buffer_lexicon,
    parse_buffer_lexicon,
)

#: 05 §4 A군 20항 중 경계 사례 — 문서에서 손으로 옮겼다.
_DOC_FORBIDDEN_SAMPLE = ("게으르", "산만하", "ADHD", "다른 아이들은", "손을 놓")


def test_term_counts_match_doc() -> None:
    """05 §4 표제 '금칙·치환 50항' — A군 20 + B군 30."""
    lexicon = load_buffer_lexicon()
    assert len(lexicon.forbidden) == 20
    assert len(lexicon.replacements) == 30
    assert len(lexicon.forbidden) + len(lexicon.replacements) == EXPECTED_TERM_COUNT == 50


@pytest.mark.parametrize("stem", _DOC_FORBIDDEN_SAMPLE)
def test_doc_forbidden_terms_present(stem: str) -> None:
    assert stem in forbidden_terms()


def test_replacement_pairs_match_doc() -> None:
    """B군 치환 방향 — 단정 → 관찰·상태 서술. 문서에서 손으로 옮긴 3건."""
    by_source = {r.source: r.target for r in load_buffer_lexicon().replacements}
    assert by_source["못합니다"] == "아직 익숙하지 않습니다"
    assert by_source["최악"] == "가장 어려웠던"
    assert by_source["혼자만"] == ""  # 비교 함의 삭제 — 빈 문자열이 정상


# ── A군 검출: 현재 잡히는 활용형 ──────────────────────────────────


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("게으르다는 인상을 받았습니다", "게으르"),
        ("게으르고 산만합니다", "게으르"),
        ("수업 중 산만하다는 이야기", "산만하"),
        ("ADHD가 의심됩니다", "ADHD"),
        ("다른 아이들은 다 했는데", "다른 아이들은"),
    ],
)
def test_forbidden_detected_when_stem_preserved(text: str, expected: str) -> None:
    """어간이 그대로 남는 활용형은 현재 방식으로 잡힌다."""
    assert expected in find_forbidden(text, forbidden_terms())


def test_clean_text_passes() -> None:
    """빈 결과가 통과 — 오탐 없음."""
    assert find_forbidden("이번 주 제출이 어려운 날이 있었습니다", forbidden_terms()) == ()


def test_find_forbidden_preserves_registration_order() -> None:
    """결정론 — 등록 순서대로 돌려준다."""
    terms = forbidden_terms()
    hits = find_forbidden("꼴찌이고 게으르다", terms)
    assert list(hits) == [t for t in terms if t in ("게으르", "꼴찌")]


# ── A군 검출: 현재 미탐인 활용형 (99 D ⑰) ────────────────────────


@pytest.mark.xfail(
    reason="99 D ⑰ — 현 구현은 부분 문자열 포함이라 어간 변형 활용형은 미탐. "
    "형태소 접두 매칭 도입 시 통과로 전환된다.",
    strict=True,
)
@pytest.mark.parametrize(
    ("text", "stem"),
    [
        ("게을러서 숙제를 미룹니다", "게으르"),  # 르 불규칙: 게으르 + 어 → 게을러
        ("수업 중 산만해서 걱정입니다", "산만하"),  # 하 활용: 산만하 + 여 → 산만해
        ("산만한 모습이 보입니다", "산만하"),  # 관형형: 산만하 + ㄴ → 산만한
    ],
)
def test_conjugated_forms_not_yet_detected(text: str, stem: str) -> None:
    """어간이 변형되는 활용형 — 05 §5는 잡아야 한다고 규정하나 현재는 미탐."""
    assert stem in find_forbidden(text, forbidden_terms())


# ── fail-closed ──────────────────────────────────────────────────


def _minimal_raw() -> dict[str, object]:
    return {
        "version": "0.1",
        "forbidden": [f"금칙{i}" for i in range(20)],
        "replace": [{"from": f"원{i}", "to": f"대{i}"} for i in range(30)],
    }


def test_term_count_drift_fails_closed() -> None:
    raw = _minimal_raw()
    forbidden = raw["forbidden"]
    assert isinstance(forbidden, list)
    forbidden.pop()
    with pytest.raises(BufferLexiconError, match="50항"):
        parse_buffer_lexicon(raw)


def test_duplicate_stem_fails_closed() -> None:
    raw = _minimal_raw()
    forbidden = raw["forbidden"]
    assert isinstance(forbidden, list)
    forbidden[1] = forbidden[0]
    with pytest.raises(BufferLexiconError, match="중복"):
        parse_buffer_lexicon(raw)


def test_missing_section_fails_closed() -> None:
    raw = _minimal_raw()
    del raw["replace"]
    with pytest.raises(BufferLexiconError, match="replace"):
        parse_buffer_lexicon(raw)
