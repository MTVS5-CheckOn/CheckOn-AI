"""완충 사전 데이터 파일·검출 — A군 금칙 20 · B군 치환 30 = 50항 (05 §4·§5).

정본은 `docs/part_a/05_tone_mapping.md` §4이고 yaml은 파생물이다. 기대값은 문서에서 손으로
옮겼다.

**활용형 회귀는 세 묶음이다:**
- 어간이 그대로 남는 활용형(`게으르다`·`산만하다고`) → 현재 잡힌다(통상 assert)
- **어간이 유지되지 않는 활용형** → **현재 미탐이라 xfail(strict)**. 두 경로가 있다:
  ⓐ 어간 끝 음절에 **종성이 결합**(`게으르`+ㄴ→`게으른` · +ㅁ→`게으름`) ⓑ **어간 자체가
  변형**(르·하 불규칙 — `게을러`·`산만해`). ⓐ가 ⑰이 좁게 적고 있던 부분이다.
- **음성 대조군**(낙인이 아닌 문맥) → 통상 assert. 지금도, 해소 후에도 통과해야 한다.

**이 파일의 xfail + 음성 대조군이 곧 ⑰ 해소의 수용 기준이다**(99 D ⑰). 양성만 늘리면
"다 잡으면 통과"가 되어 과차단을 못 잡으므로 두 묶음을 함께 본다.
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
    reason="99 D ⑰ — 현 구현은 부분 문자열 포함이라 어간 끝 음절에 종성이 붙거나 "
    "어간 자체가 변형되면 미탐. 형태소 접두 매칭 도입 시 통과로 전환된다.",
    strict=True,
)
@pytest.mark.parametrize(
    ("text", "stem"),
    [
        # ── 어간 변형(르·하 불규칙) — 종전 3건 ──
        ("게을러서 숙제를 미룹니다", "게으르"),  # 르 불규칙: 게으르 + 어 → 게을러
        ("수업 중 산만해서 걱정입니다", "산만하"),  # 하 활용: 산만하 + 여 → 산만해
        ("산만한 모습이 보입니다", "산만하"),  # 관형형: 산만하 + ㄴ → 산만한
        # ── 🔴 종성 결합(-ㄴ·-ㅁ) — 8/4 실서버 스모크로 승격(99 D ⑰) ──
        # 위 `산만한`과 **같은 원리**인데 문서 3곳이 "게으른은 잡힌다"고 반대로 적고 있었다.
        # 어간 끝 음절에 종성이 없으면(`르`·`하`·`쁘`) 어떤 종성이 붙어도 부분 문자열이 깨진다.
        ("학생이 게으른 모습입니다", "게으르"),  # 관형형 — 실서버가 실제로 낸 문면
        ("게으름이 눈에 띕니다", "게으르"),  # 명사형: 게으르 + ㅁ → 게으름
        ("산만함이 계속됩니다", "산만하"),  # 명사형: 산만하 + ㅁ → 산만함
        ("머리가 나쁜 편입니다", "머리가 나쁘"),  # 관형형: 나쁘 + ㄴ → 나쁜
        # ── 🔴 부정문도 **양성**이다(8/5 A 확정 · 99 D ⑰) ──
        # A군은 치환 불가(문장 재생성)라 표현 자체를 금지한다. 부정문을 허용하면
        # "머리가 나쁘다고 볼 수는 없겠지만…" 우회가 열리고, 부정 판정은 문맥 처리라
        # 결정론 게이트의 결이 아니다(불변식 1).
        ("머리가 나쁜 편은 아닙니다", "머리가 나쁘"),
    ],
)
def test_conjugated_forms_not_yet_detected(text: str, stem: str) -> None:
    """어간이 유지되지 않는 활용형 — 05 §5는 잡아야 한다고 규정하나 현재는 미탐.

    **이 목록이 곧 ⑰ 해소의 수용 기준이다** — `strict=True`라 해소하면 xpass로 뒤집혀
    자동으로 드러난다. 아래 `test_non_stigmatizing_context_is_not_blocked`(음성)와
    **함께** 봐야 한다: 양성만 늘리면 "다 잡으면 통과"가 되어 과차단을 아무도 못 잡는다.

    🔴 **부정문("…편은 아닙니다")도 여기(양성)에 있다** — A군은 치환 불가(문장 재생성)라
    **표현 자체를 금지**한다(05 §4). 부정문을 허용하면 "머리가 나쁘다고 볼 수는 없겠지만…"
    우회가 열리고, 부정 판정은 문맥 처리라 **결정론 게이트의 결이 아니다**(불변식 1).
    """
    assert stem in find_forbidden(text, forbidden_terms())


# ── 🔴 음성 대조군 — 해소안이 과차단으로 가는 것을 막는다 (99 D ⑰ 이유 ①) ──


@pytest.mark.parametrize(
    ("text", "why"),
    [
        (
            "산만한 분위기였습니다",
            "A군은 **학생에 대한** 낙인·평가·진단이다(05 §4). 환경 서술은 대상이 다르다",
        ),
        (
            "오늘 집중이 어려웠습니다",
            "`집중력이 없` 항목 주석이 명시적으로 허용한다 — 기질 단정만 금지(05 §4)",
        ),
        (
            "과제를 미루는 모습이 관찰됩니다",
            "B군 치환의 결과 문면이다(`게을리` → `우선순위가 밀린 듯` 계열). "
            "치환 산출이 A군에 걸리면 치환 축 자체가 무의미해진다(05 §4 B군)",
        ),
        (
            "문제 풀이 시간이 늘었습니다",
            "🔴 실측 근거 — 8/4 스모크 S1·S2 출력 5건에 등장한 정상 학습 용어다. "
            "`문제아`의 접두 `문제`로 매칭하면 이 문장이 걸린다(순진한 접두 매칭 대조군에서 "
            "코퍼스 26건 중 7건 오탐)",
        ),
    ],
)
def test_non_stigmatizing_context_is_not_blocked(text: str, why: str) -> None:
    """낙인이 아닌 문맥은 지금도, 해소 후에도 통과해야 한다 — **통상 assert**다.

    ⑰이 해소를 미룬 이유 ①이 오탐 위험("'산만한 분위기'처럼 비난이 아닌 문맥까지 걸린다")
    인데, 음성 대조군이 없으면 어떤 해소안이든 과차단으로 가는 것을 아무도 못 잡는다.
    """
    assert find_forbidden(text, forbidden_terms()) == (), why


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
