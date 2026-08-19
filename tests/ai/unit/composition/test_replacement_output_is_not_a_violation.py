"""🔴 **우리가 쓰라고 준 문장이 위반으로 세어지면 안 된다** (99 #108).

실측(2026-08-19 · 실 LLM 1차 · n=27): B군 적중 **18/27** 이 전부 `못합니다` 였는데,
실제 문면은 **「정답에 이르지 못한 문항은…」** — 그건 `틀렸습니다` → **`정답에 이르지
못했습니다`** 라는 **우리가 준 치환문**을 모델이 활용한 것이다.
⇒ **규칙을 완벽하게 지킨 초안이 위반으로 계상됐고**, `#79`·`#80` 의 빈도 재료가 오염됐다.

🔴 **원인은 대조군이었다.** PR-08 의 A-1 은 어간 후보를 「오탐 0 ∧ 새로 잡음」으로 판정했는데
대조군 28건이 **전부 손으로 쓴 정상 문장**이라 **「우리가 권장한 대체 표현」이 한 건도
없었다.** 그 종류는 손으로 안 떠오른다 — 실 산출을 보고서야 나왔다.

⚠ **A군에는 이 원칙이 이미 있었다** — `test_non_stigmatizing_context_is_not_blocked` 의
*«치환 산출이 A군에 걸리면 치환 축 자체가 무의미해진다»*. **B군 관측 축에는 아무도
적용 안 했다.** 이 파일이 그것을 **두 축 모두에** 세운다.

🔴 **이 파일의 값은 「이번 건을 고쳤다」가 아니라 「다음에 stem 을 추가할 때 자동으로
막힌다」다.**
"""

from __future__ import annotations

from typing import Final

import pytest

from ai.composition.buffer_lexicon import (
    find_forbidden,
    forbidden_terms,
    load_buffer_lexicon,
)
from ai.runtime.draft_observation import ORIGIN_DRAFT, observe_gated_draft

#: 🔴 **실측에서 실제로 나온 문면** — 지어낸 것이 아니다.
_OBSERVED: Final = "정답에 이르지 못한 문항은 다시 짚어 보겠습니다."


def _conjugations(target: str) -> list[str]:
    """치환문의 흔한 활용형 — 🔴 **전수가 아니다.**

    한국어 활용은 규칙화가 안 된다(PR-08 이 이미 밟았다: `느리` → `느려`, `심각하` →
    `심각한` 처럼 어간이 변형된다). ⇒ 여기서는 **종결형 치환**만 다루고, 그것으로 못 잡는
    형태는 **실측이 발견하는 몫**으로 남긴다.
    ⚠ **그 한계를 적어 두는 것이 이 함수의 절반이다** — 안 적으면 다음 사람이
    *"활용형을 전수로 본다"* 로 읽는다.
    """
    forms: list[str] = []
    for ending, replacements in (
        ("했습니다", ("한", "하는", "해서", "했던")),
        ("습니다", ("은", "는", "어서")),
        ("합니다", ("한", "하는", "해서")),
        ("입니다", ("인", "이라", "이고")),
    ):
        if target.endswith(ending):
            stem = target[: -len(ending)]
            forms.extend(stem + r for r in replacements)
    return forms


def _observation_probes() -> tuple[str, ...]:
    """`observe_gated_draft` 가 실제로 매칭에 쓰는 문자열 전량(`source` ∪ `stem`)."""
    probes: list[str] = []
    for item in load_buffer_lexicon().replacements:
        probes.append(item.source)
        if item.stem:
            probes.append(item.stem)
    return tuple(probes)


# ── 1 · 실측 문면이 적중이 아니다 ─────────────────────────────────


def test_the_observed_compliant_sentence_is_not_a_violation() -> None:
    """🔴 실측에서 18/27 을 만든 그 문면 그대로.

    ⚠ **실 소비처를 지난다** — `observe_gated_draft` 가 `source ∪ stem` 으로 매칭하고
    결과를 `source` 로 정규화한다. `find_forbidden` 만 부르면 그 정규화를 안 지나
    「무엇이 계상되는가」를 재는 것이 아니게 된다.
    """
    hits = observe_gated_draft(
        _OBSERVED, origin=ORIGIN_DRAFT, tenant_id="t_108", execution_id="run-108"
    )
    assert hits == (), (
        f"치환문을 지킨 문장이 B군 적중으로 세어진다: {hits} — "
        "빈도 재료가 「치환이 잘 동작한다」를 「위반이 잦다」로 뒤집는다(99 #108)"
    )


# ── 2 · 🔴 구조 — 어떤 stem 도 치환 산출을 안 잡는다 ──────────────


def test_no_observation_probe_flags_any_replacement_output() -> None:
    """🔴 **재발 방지의 본체** — `to` 전량과 그 활용형을 **기계로** 훑는다.

    ⚠ 손으로 고른 케이스로는 못 막는다 — 이번 결함이 정확히 그래서 났다.
    다음 사람이 `stem` 을 추가하면 **여기서 막힌다.**

    ⚠ 활용형은 **전수가 아니다**(`_conjugations` docstring) — 종결형 치환만 본다.
    그것으로 못 잡는 형태는 실측이 발견하는 몫이고, **그 한계를 아는 채로 쓰는 것**이
    「전수인 척하는 것」보다 낫다.
    """
    probes = _observation_probes()
    offenders: list[tuple[str, str, tuple[str, ...]]] = []
    #: 🔴 **훑은 것을 센다** — 절단 가드가 그 수를 본다.
    examined: list[str] = []
    direct: set[str] = set()
    for item in load_buffer_lexicon().replacements:
        if not item.target:
            continue  # 삭제 항목(§4 비교 함의 제거)
        direct.add(item.target)
        for text in (item.target, *_conjugations(item.target)):
            examined.append(text)
            hits = find_forbidden(text, probes)
            if hits:
                offenders.append((item.source, text, hits))

    assert not offenders, (
        f"치환 산출이 B군 관측에 걸린다: {offenders} — "
        "치환 축 자체가 무의미해진다(A군의 같은 원칙 · 05 §4 B군)"
    )
    #: 🔴 **절단 가드 — 「몇 건 있나」가 아니라 「몇 건을 실제로 훑었나」를 센다.**
    #:   실측(고의 파괴 1-③): 순회 대상을 비우는 파괴가 **green** 이었다 — 사전에 `to` 가
    #:   28건 있다는 사실은 **이 검사가 그걸 봤다는 뜻이 아니다.**
    assert len(examined) >= 20, f"치환문을 {len(examined)}건만 훑었다(순회가 비었나)"
    assert sum(1 for t in examined if t not in direct) >= 10, (
        f"활용형을 {sum(1 for t in examined if t not in direct)}건만 훑었다"
    )


# ── 6 · 같은 원칙을 A군에도 (작업 0-3: 기존 검사는 손으로 고른 3건뿐) ──


def test_no_forbidden_term_flags_any_replacement_output() -> None:
    """A군도 치환 산출을 안 잡는다 — **`to` 전량을 기계로** 훑는다.

    ⚠ 기존 `test_non_stigmatizing_context_is_not_blocked` 는 **손으로 고른 3건**이고
    (그중 하나가 이 원칙을 말한다) `to` 전량을 순회하지 않는다(실측 8/20).
    ⇒ 같은 원칙이라 **같은 폭으로** 세운다. 지금은 0건이지만 A군에 항이 늘 때 막힌다.
    """
    terms = forbidden_terms()
    offenders: list[tuple[str, str, tuple[str, ...]]] = []
    for item in load_buffer_lexicon().replacements:
        if not item.target:
            continue
        for text in (item.target, *_conjugations(item.target)):
            hits = find_forbidden(text, terms)
            if hits:
                offenders.append((item.source, text, hits))

    assert not offenders, (
        f"치환 산출이 A군 게이트에 걸린다: {offenders} — "
        "🔴 그건 더 큰 결함이다(게이트가 우리 권장문을 차단한다)"
    )


# ── 3·4 · 보류와 채택이 둘 다 살아 있다 ──────────────────────────


def test_the_withheld_stems_are_empty() -> None:
    """보류 항은 `stem == ""` — PR-08 의 형식을 유지한다.

    ⚠ `못합니다` 가 이번에 추가됐다(99 #108). 나머지 셋은 PR-08 의 오탐 보류다.
    """
    withheld = {"못합니다", "틀렸습니다", "느립니다", "안 했습니다"}
    by_source = {i.source: i for i in load_buffer_lexicon().replacements}
    for source in withheld:
        assert by_source[source].stem == "", (
            f"{source!r} 에 stem 이 붙었다 — 오탐 때문에 보류한 항이다"
        )


@pytest.mark.parametrize(
    ("body", "entry"),
    [
        ("이번 주는 심각한 상황입니다.", "심각합니다"),
        ("추론이 약점이 되고 있습니다.", "약점입니다"),
        ("제출을 안 하는 날이 있습니다.", "제출을 안 합니다"),
    ],
    ids=["심각한", "약점이", "제출을 안 하는"],
)
def test_the_accepted_stems_still_catch_conjugations(body: str, entry: str) -> None:
    """🔴 **없으면 「전부 걷었다」와 구분이 안 된다** — 남은 채택분은 여전히 잡는다."""
    hits = observe_gated_draft(
        body, origin=ORIGIN_DRAFT, tenant_id="t_108", execution_id="run-108"
    )
    assert entry in hits, (
        f"{entry!r} 의 활용형을 놓쳤다({hits}) — 보류를 늘리다 계수를 통째로 죽였다"
    )


def test_the_counts_are_unchanged() -> None:
    """항 수·`from`·`to` 는 안 바꿨다 — 05 §4·프롬프트·#83 축이 안 따라온다."""
    lexicon = load_buffer_lexicon()
    assert len(lexicon.forbidden) == 25
    assert len(lexicon.replacements) == 28
