"""관측 전용 어간(`stem`) — 계수를 하한에서 실수(實數)로 (99 #84).

🔴 **문제:** `observe_gated_draft` 가 `replace[].from` 으로 초안을 세는데 B군 28항 중
**15항이 종결형**(`~니다`)이라 다른 활용형은 표면이 달라 **안 잡힌다** ⇒ 계수가 **하한**이고,
그 숫자가 게이트 판정(99 #79)의 **유일한 재료**라 «빈도가 낮으니 안전하다» 로 기운다.

🔴 **처방:** `from` 을 낮추지 않는다 — 그건 프롬프트가 쓰는 **자연문**이다
(*"「심각하」 대신"* 은 문장이 아니다). **관측 전용 `stem` 을 따로 준다.**
⇒ 항 수 28 불변 · `from`·`to` 문면 불변 ⇒ 05 §4 개정도, 항 수 fail-closed(53)도,
99 #83 의 `redact()` 오탐 축도 **따라오지 않는다.**

⚠ **`stem` 이 없는 항이 있는 것이 정상이다** — 어간·구 13항은 이미 맞고, 종결형 3항은
**낮추면 정상 문장을 오탐**해서 일부러 안 줬다. **그 3항이 남은 하한이다.**
"""

from __future__ import annotations

from typing import Final

import pytest

from ai.composition.buffer_lexicon import (
    EXPECTED_TERM_COUNT,
    Replacement,
    find_forbidden,
    load_buffer_lexicon,
)
from ai.composition.counsel.prompt import _buffer_replacements
from ai.runtime.draft_observation import ORIGIN_DRAFT, observe_gated_draft

_TENANT: Final = "t_stem"
_RUN: Final = "run-stem"

#: 🔴 **대조군 — PR-07 의 D 목록(99 #104)을 재사용한다.** 새로 지어내면 그 목록과 갈린다.
#: ⚠ 뒤쪽은 *"어간을 낮추면 걸릴 법한"* 칭찬·개선 문면이다 — 오탐 축의 실제 위험이 거기 있다.
_CLEAN_BODIES: Final = (
    "6월 지문 42개·312문항",
    "제출률 100% (4주)",
    "최근 4주 정답률 평균 81%",
    "이번 주 정답률 62%",
    "지난주 대비 상승",
    "지난달 대비 하락",
    "이해력 향상 추세",
    "수업 태도 양호",
    "숙제 미제출 2회",
    "출결 100%",
    "이번 달 오답 노트 작성 3회",
    "지난 학기 성적 유지",
    "문학 영역 정답률 78%",
    "비문학 영역 정답률 55%",
    "이번 주 지문 8개 완료",
    "지난 2주간 결석 0회",
    "약점을 보완했습니다",
    "걱정을 덜어 드리려 말씀드립니다",
    "문제가 잘 풀리고 있습니다",
    "떨어졌던 부분이 회복됐습니다",
    "실패했던 유형을 다시 짚었습니다",
    "심각하게 받아들이실 필요는 없습니다",
    "거부감 없이 잘 따라옵니다",
    "안정적으로 유지되고 있습니다",
    "성적이 오르고 있습니다",
    "제출을 잘 하고 있습니다",
    "못하던 유형을 해냈습니다",
    "항상 성실합니다",
)

#: 🔴 **보류 3항** — 낮추면 위 대조군을 오탐한다(실측 8/19). 이것이 **남은 하한**이다.
_WITHHELD: Final = ("틀렸습니다", "느립니다", "안 했습니다")


def _entry(source: str) -> Replacement:
    for item in load_buffer_lexicon().replacements:
        if item.source == source:
            return item
    raise AssertionError(f"등재에 없다: {source}")


# ── 1 · 🔴 활용형이 잡힌다 (본체) ─────────────────────────────────


@pytest.mark.parametrize(
    ("body", "entry"),
    [
        ("이번 주는 심각한 상황입니다.", "심각합니다"),
        ("문제를 느려서 오래 붙잡고 있습니다.", None),  # 보류 항 — 안 잡히는 게 맞다
        ("추론이 약점이 되고 있습니다.", "약점입니다"),
        ("제출을 안 하는 날이 있습니다.", "제출을 안 합니다"),
        ("걱정되는 부분이 있습니다.", "걱정입니다"),
    ],
    ids=["심각한", "느려서(보류)", "약점이", "제출을 안 하는", "걱정되는"],
)
def test_a_conjugated_form_is_counted_when_a_stem_was_given(
    body: str, entry: str | None
) -> None:
    """🔴 **이 PR 의 본체** — #84 가 실측한 미검출이 채택 항에서 사라진다.

    ⚠ 보류 항(`느립니다`)은 **안 잡히는 것이 맞다** — 같은 표에 넣어 둔 이유는
    「전부 잡히게 됐다」와 「채택분만 잡히게 됐다」를 **한 자리에서** 가르기 위해서다.
    """
    hits = observe_gated_draft(
        body, origin=ORIGIN_DRAFT, tenant_id=_TENANT, execution_id=_RUN
    )
    if entry is None:
        assert not hits, f"보류 항인데 잡혔다: {hits}"
    else:
        assert entry in hits, f"{entry!r} 의 활용형이 안 잡힌다 — stem 이 관측에 안 물렸다"


def test_the_original_termination_form_is_still_counted() -> None:
    """🔴 **`stem or from` 이 아니라 둘 다 본다** — 좁힌 어간이 원본을 안 품는다.

    실측: `못한` ⊄ `못합니다`. `or` 로 갈면 **종결형 자체를 놓쳐** 계수가 **줄어든다** —
    「하한을 고치러 와서 다른 하한을 만드는」 꼴이다.
    """
    assert _entry("못합니다").stem == "못한"
    assert "못합니다" not in _entry("못합니다").stem

    hits = observe_gated_draft(
        "이 유형은 아직 못합니다.", origin=ORIGIN_DRAFT, tenant_id=_TENANT, execution_id=_RUN
    )
    assert "못합니다" in hits, "stem 을 주면서 원본 종결형을 놓쳤다"


# ── 2 · 🔴 프롬프트가 안 바뀐다 ───────────────────────────────────


def test_the_prompt_never_sees_the_stem() -> None:
    """🔴 **1-판정의 전제** — 프롬프트는 `from`·`to` 만 쓴다.

    깨지면 05 §4 개정과 99 #83 의 `redact()` 오탐 축까지 번진다.
    ⚠ **문면으로 잰다** — 조립 결과에 어떤 `stem` 문자열도 **단독으로** 나타나면 안 된다.
    """
    lexicon = load_buffer_lexicon()
    stems = [item.stem for item in lexicon.replacements if item.stem]
    assert stems, "stem 이 하나도 없다 — 이 검사가 눈이 멀었다"

    for level in (1, 2):
        rendered = _buffer_replacements(level)
        for item in lexicon.replacements:
            if not item.stem:
                continue
            #: `stem` 은 `from` 의 부분열일 수 있으므로(`문제가 있` ⊂ `문제가 있습니다`)
            #: **`from`·`to` 를 지운 뒤** 남은 자리에 나타나는지를 본다.
            residue = rendered.replace(item.source, "").replace(item.target, "")
            assert item.stem not in residue, (
                f"프롬프트에 관측 전용 어간이 샜다: {item.stem!r}(level={level})"
            )


# ── 3 · 항 수 fail-closed 유지 ────────────────────────────────────


def test_the_term_counts_are_unchanged() -> None:
    """항 수·조합이 그대로다 — `stem` 은 **항을 늘리지 않는다.**"""
    lexicon = load_buffer_lexicon()
    assert len(lexicon.forbidden) == 25
    assert len(lexicon.replacements) == 28
    assert len(lexicon.forbidden) + len(lexicon.replacements) == EXPECTED_TERM_COUNT == 53


# ── 4 · 🔴 보류 항은 종전과 같다 ──────────────────────────────────


def test_a_withheld_entry_behaves_exactly_as_before() -> None:
    """🔴 「전부 낮췄다」와 구분한다 — 보류 3항은 `stem` 이 **없다.**

    ⚠ 그 사유(정상 문장 오탐)는 yaml 주석에 적혀 있다 — 안 적으면 다음 사람이
    *"빠뜨렸구나"* 하고 넣는다.
    """
    for source in _WITHHELD:
        assert _entry(source).stem == "", (
            f"{source!r} 에 stem 이 붙었다 — 그 항은 오탐 때문에 보류한 것이다"
        )


# ── 5 · 🔴 정상 문장이 오탐 안 된다 ───────────────────────────────


def test_no_clean_body_is_falsely_counted() -> None:
    """🔴 **없으면 「계수를 늘린 것」과 「정확해진 것」이 구분 안 된다.**

    ⚠ 대조군은 PR-07 의 D 목록(99 #104) + 칭찬·개선 문면이다 — 지어내지 않았다.
    """
    offenders: dict[str, tuple[str, ...]] = {}
    for body in _CLEAN_BODIES:
        hits = observe_gated_draft(
            body, origin=ORIGIN_DRAFT, tenant_id=_TENANT, execution_id=_RUN
        )
        if hits:
            offenders[body] = hits

    assert not offenders, (
        f"정상 문장이 B군으로 계상된다: {offenders} — "
        "어간이 과도하게 짧으면 계수가 늘기만 하고 정확해지지 않는다"
    )


# ── 6 · stem 없는 항목도 로드된다 ─────────────────────────────────


def test_entries_without_a_stem_still_load() -> None:
    """`stem` 은 **선택 필드**다 — 어간·구 13항은 원래 맞아서 안 준다."""
    lexicon = load_buffer_lexicon()
    without = [item.source for item in lexicon.replacements if not item.stem]
    assert len(without) >= 13, f"stem 없는 항이 {len(without)}개뿐이다"
    #: 어간으로 등재된 항은 종전대로 활용형을 잡는다(부분 문자열).
    assert find_forbidden("과제를 게을리했습니다.", ("게을리",)) == ("게을리",)
