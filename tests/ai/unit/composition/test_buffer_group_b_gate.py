"""B군 게이트 — 「이 말 대신 저 말」이 **막히는** 층 (99 #79 · 상담 축 마지막).

두 달 열려 있던 물음은 *"게이트에 넣으면 재생성이 터지나"* 였고, **실 LLM 57건이
답했다**: 적중 **0건** · 95% 상한 **5.3%**(99 #79·#80). ⇒ 넣어도 안 터진다.

🔴 **이 층이 유일한 방어인 항이 둘 있다** — `이해력이 부족`·`다른 학생에 비해` 는
`redact()` 오탐 때문에 **프롬프트에 못 싣는다**(99 #83) ⇒ 종전에는 **어디서도 안 막혔다.**
그게 이 PR 의 실제 값이다.

⚠ 🔴 **(8/21 갱신)** 그 둘은 이제 **프롬프트에 실린다** — `whitelists.name_exclude` 에
`이해력`·`다른` 을 넣어 `redact()` 오탐을 닫았다(99 #123 · №54). ⇒ **방어가 한 층에서
두 층이 됐다**(프롬프트 지시 + 이 게이트).
🔴 **이 검사의 값은 줄지 않았다** — LLM 은 지시를 안 따를 수 있고 **이 층이 마지막
방어**다. 위 문면은 **«유일했던 시절»의 기록**으로 남긴다(로그 173 — 「어느 층에서
참인가」가 바뀌었을 때 옛 사실을 지우면 **왜 이 검사가 생겼는지**가 사라진다).

🔴 **가장 중요한 것은 「치환 산출이 게이트에 안 걸린다」다**(아래 C). #108 이 **관측 축**에서
났고 PR-13 이 닫았는데 **게이트는 새 층**이다 — 결정 로그 133: *«한 층에 있다고 다른 층이
상속하지 않는다»*. 🔴 게이트가 치환 산출을 차단하면 **초안이 영영 안 나온다** — 관측 오염보다
훨씬 나쁘다.
"""

from __future__ import annotations

from typing import Final

import pytest

from ai.composition.buffer_lexicon import (
    load_buffer_lexicon,
    replacement_source_of,
)
from ai.composition.counsel.gate import GateResult, check_counsel_gate
from ai.composition.counsel.refine import _GATE_REASON_TO_BLOCK
from ai.composition.gate_feedback import instruction_for, load_gate_feedback
from ai.contracts.composition import (
    CommStyle,
    DraftContext,
    EvidenceFact,
    Frequency,
    Interest,
    LabelSnapshot,
    Sensitivity,
)
from ai.contracts.gates import BlockedReason
from ai.runtime.redaction import redact

#: 🔴 #83 의 두 항 — **프롬프트에 못 싣는다.** 게이트가 유일한 방어다.
_PROMPT_BLIND: Final = ("이해력이 부족", "다른 학생에 비해")


def _context() -> DraftContext:
    return DraftContext(
        student_ref="st_gate_b",
        guardian_ref="pa_gate_b",
        label_snapshot=LabelSnapshot(
            comm=CommStyle.NARRATIVE,
            sensitivity=Sensitivity.ANXIOUS,
            interest=Interest.GRADE,
            frequency=Frequency.MONTHLY,
        ),
        facts=(EvidenceFact(label="근거", value="제출률 100% (4주)", record_id="le_1"),),
        evidence_summaries=(),
        period_label="2026년 7월",
        fallback_text="이번 기간 학습 상황을 정리해 보내드립니다.",
    )


def _gate(body: str) -> GateResult:
    return check_counsel_gate(body, _context(), max_chars=4000, min_chars=10)


def _conjugations(target: str) -> list[str]:
    """치환문의 흔한 활용형 — ⚠ **전수가 아니다**(PR-13 과 같은 한계·같은 이유)."""
    forms: list[str] = []
    for ending, repl in (
        ("했습니다", ("한", "하는", "해서", "했던")),
        ("습니다", ("은", "는", "어서")),
        ("합니다", ("한", "하는", "해서")),
        ("입니다", ("인", "이라", "이고")),
    ):
        if target.endswith(ending):
            forms.extend(target[: -len(ending)] + r for r in repl)
    return forms


# ── 🔴 C · 치환 산출이 게이트에 안 걸린다 (재발 방지의 본체) ──────


def test_no_replacement_output_is_blocked_by_the_gate() -> None:
    """🔴 **이 파일에서 제일 중요하다.**

    게이트가 **우리가 쓰라고 준 문장**을 차단하면 재생성이 같은 벽에 부딪혀
    **초안이 영영 안 나온다.** #108 은 관측 축이라 「숫자가 틀렸다」였는데, 여기서 나면
    **산출이 0** 이다.

    ⚠ PR-13 이 같은 검사를 **관측 축**에 세웠다 — 게이트는 **새 층**이라 상속하지 않는다
    (결정 로그 133).
    """
    #: 🔴 **실 게이트를 태운다** — `find_forbidden(text, replacement_probes())` 를 직접
    #: 부르면 **게이트가 무엇을 보는지**가 아니라 **내가 무엇을 보기로 했는지**를 재게 된다.
    #: 실측(고의 파괴 1-②): 게이트 안의 목록을 `to` 전량으로 넓히는 파괴가 **green** 이었다 —
    #: 검사가 게이트를 안 지났기 때문이다(PR-07 이 배운 그 병 · 결정 로그 127).
    offenders: list[tuple[str, str, str]] = []
    examined: list[str] = []
    for item in load_buffer_lexicon().replacements:
        if not item.target:
            continue  # 삭제 항목(§4)
        for text in (item.target, *_conjugations(item.target)):
            examined.append(text)
            #: 길이 하한에 걸리지 않게 채운다 — 재는 축은 **B군**이다.
            result = _gate(f"{text} 함께 살펴보겠습니다. 다음 기간에도 이어 가겠습니다.")
            if result.reason == "buffered":
                offenders.append((item.source, text, result.reason))

    assert not offenders, (
        f"게이트가 치환 산출을 막는다: {offenders} — 재생성이 같은 벽에 부딪혀 "
        "초안이 영영 안 나온다"
    )
    #: 절단 가드 — **훑은 수**를 센다(PR-13 에서 배운 자리: 「몇 건 있나」가 아니다).
    assert len(examined) >= 20, f"치환문을 {len(examined)}건만 훑었다"


# ── 🔴 D · #83 의 두 항이 막힌다 (이 PR 의 존재 이유) ────────────


@pytest.mark.parametrize("term", _PROMPT_BLIND)
def test_the_prompt_blind_terms_are_finally_blocked(term: str) -> None:
    """🔴 **이 PR 의 존재 이유** — 이 둘은 프롬프트에 못 싣는다(99 #83).

    ⇒ 종전에는 **프롬프트에도 게이트에도 없어** 학부모에게 그대로 나갔다.
    """
    result = _gate(f"학습 상황을 보면 {term}한 편입니다. 함께 살펴보겠습니다.")
    assert result.passed is False, f"{term!r} 이 통과했다 — 어디서도 안 막힌다"
    assert result.reason == "buffered", result.reason


def test_a_representative_group_b_term_is_blocked() -> None:
    """B군 일반 항도 막힌다 — 프롬프트가 실패했을 때의 방어선."""
    result = _gate("이번 주 결과는 심각합니다. 함께 살펴보겠습니다.")
    assert result.passed is False
    assert result.reason == "buffered"


# ── 어휘 축을 안 늘린다 ──────────────────────────────────────────


def test_the_reason_maps_to_tone_violation() -> None:
    """🔴 `BlockedReason` 은 닫힌 공용 enum(양자)이라 **값을 안 늘린다** — A군과 합류한다."""
    assert _GATE_REASON_TO_BLOCK["buffered"] is BlockedReason.TONE_VIOLATION
    assert _GATE_REASON_TO_BLOCK["forbidden"] is BlockedReason.TONE_VIOLATION


# ── 🔴 B · 재생성 문구가 전송 가능하다 (#102 재발 방지) ─────────


def test_the_feedback_for_this_reason_can_actually_be_sent() -> None:
    """🔴 **#102 가 정확히 여기서 났다** — `empty` 문구의 `이번에는` 이 트립와이어에 걸려
    재생성 프롬프트가 **통째로 미전송**됐고, 그 문구가 사문이라 아무도 못 봤다.

    ⚠ **그래서 B군 문면에 어휘를 안 실었다** — 실측(8/20 · 28항 전수): 어휘를 detail 로
    붙이면 `이해력이 부족`·`다른 학생에 비해` **2항**이 `redact()` 에 걸린다.
    """
    text = instruction_for("buffered")
    assert text, "buffered 문구가 없다 — 재생성이 빈손이다"
    outcome = redact(text)
    assert not (outcome.uncertain or outcome.findings), (
        f"B군 재생성 문구가 트립와이어에 걸린다: {text!r}"
    )


def test_no_group_b_term_leaks_into_the_feedback() -> None:
    """🔴 **어휘가 문면에 실리면 안 된다** — 위 실측의 2항이 그때 죽는다.

    ⚠ 대가는 «어느 표현이었나» 를 모델이 못 듣는 것이다. **읽는 자리가 다르다** —
    그건 관측(`observe_gated_draft`)이 로그에 남긴다.

    ━━ ⚠ 🔴 **(8/21) 단언은 그대로이고 「근거」가 바뀌었다** ━━

    종전 근거는 **«못 싣는다»**(기술적 제약 — `redact()` 오탐이 막았다)였다.
    №54 가 그 오탐을 닫아 **기술적으로는 실을 수 있게 됐다.** 그런데도 **안 싣는다** —
    이건 이제 **판정**이다(8/21 · 진희). 이유 둘:

        ① 🔴 게이트 사유는 **응답·로그를 타고 나간다.** `이해력이 부족` 은 학부모에 대한
           **낙인 표현**이고, 그것을 사유에 담으면 **새로운 노출 축**이 열린다 —
           막으려던 말을 **막았다는 이유로 실어 보내는** 셈이다.
        ② №54 는 **오탐 처방 회차**다. «이제 가능하니 하자» 는 별개 설계 결정이고,
           그 물음(«게이트 사유에 무엇을 담나»)은 **아직 아무도 안 물었다.**

    🔴 **근거를 안 적으면 다음 사람이 «제약이 풀렸는데 왜 안 하지» 로 읽고 되돌린다.**
    """
    text = instruction_for("buffered")
    leaked = [i.source for i in load_buffer_lexicon().replacements if i.source in text]
    assert not leaked, f"B군 어휘가 재생성 문구에 실렸다: {leaked}"
    #: 🔴 **막는 자리는 「게이트가 사유에 콜론 뒤를 안 싣는 것」이다.**
    #:   `instruction_for` 자체는 detail 을 받으면 붙인다(다른 사유는 그게 맞다) —
    #:   그러니 **게이트가 낸 사유에 콜론이 없어야** 한다. 그것을 잰다.
    assert ":" not in _gate("이번 주 결과는 심각합니다. 함께 살펴보겠습니다.").reason, (
        "게이트가 B군 사유에 detail 을 실었다 — 그 어휘가 재생성 프롬프트에 붙어 "
        "2항(이해력이 부족·다른 학생에 비해)에서 미전송이 난다(99 #83·#102)"
    )
    #: ⚠ **detail 이 붙었을 때 트립와이어에 걸리는 항을 같이 센다.**
    #: 🔴 **(8/21) 이 수가 2 → 0 이 됐다** — 종전 둘(`이해력이 부족`·`다른 학생에 비해`)이
    #:   `redact()` 오탐에 걸리던 것이고, №54 가 `name_exclude` 로 닫았다(99 #123).
    #: ⚠ **0 이 됐다고 위 단언이 느슨해진 것이 아니다** — 위는 «안 싣는다»(판정)이고
    #:   여기는 «실었으면 어떻게 됐나»(사실)다. **두 줄이 재는 것이 다르다.**
    #: 🔴 이 수가 **다시 오르면** 새 오탐이 생긴 것이다 — 그 신호를 여기 남긴다.
    risky = [
        i.source
        for i in load_buffer_lexicon().replacements
        if (lambda o: o.uncertain or o.findings)(
            redact(instruction_for(f"forbidden:{i.source}"))
        )
    ]
    assert risky == [], (
        f"B군 어휘가 재생성 문구에서 트립와이어에 걸린다 — 새 오탐이다(99 #123 · #178): {risky}"
    )


# ── 정상 초안이 안 걸린다 ────────────────────────────────────────


@pytest.mark.parametrize(
    "body",
    [
        "제출률은 4주 내내 100%로 이어졌습니다. 지금 흐름을 유지하면 좋겠습니다.",
        "이번 기간 학습 상황을 정리해 보내드립니다. 꾸준히 참여하고 있습니다.",
        "정답에 이르지 못한 문항은 다시 짚어 보겠습니다.",
    ],
    ids=["수치", "일반", "치환문_활용형"],
)
def test_a_clean_draft_still_passes(body: str) -> None:
    """🔴 **없으면 「전부 막는 게이트」와 구분이 안 된다.**

    ⚠ 세 번째는 **치환 산출의 활용형**이다 — 실 LLM 1차에서 18/27 을 만든 그 문면이고,
    게이트 축에서 같은 사고가 나면 **초안이 0** 이다.
    """
    assert _gate(body).passed is True, _gate(body).reason


# ── 순서가 계약이다 ──────────────────────────────────────────────


def test_the_check_order_puts_numbers_before_nothing_important() -> None:
    """🔴 B군은 **A군 뒤 · `ungrounded_number` 앞**이다.

    ⚠ 숫자 검사가 **더 치명적**이다(불변식 1·2 — LLM 이 수치를 만들지 않는다).
    B군이 숫자보다 뒤면 **숫자 문제가 B군에 가려진다.**
    """
    import inspect  # noqa: PLC0415

    source = inspect.getsource(check_counsel_gate)
    order = [
        source.index('reason=f"forbidden:'),
        source.index('reason="buffered"'),
        source.index('reason=f"internal_term:'),
        source.index('reason=f"ungrounded_number:'),
    ]
    assert order == sorted(order), (
        "검사 순서가 A군 → B군 → internal_term → 숫자가 아니다"
    )


def test_the_a_group_check_is_unchanged() -> None:
    """이 PR 은 B군만 건드린다 — A군은 그대로 `forbidden:` 을 낸다."""
    result = _gate("학생이 게으르다는 인상을 받았습니다. 함께 살펴보겠습니다.")
    assert result.passed is False
    assert result.reason.startswith("forbidden:"), result.reason


def test_the_feedback_map_covers_the_new_reason() -> None:
    """`gate_feedback.yaml` 에 등재됐다 — 미등재는 조용히 default 로 퇴화한다."""
    assert "buffered" in load_gate_feedback().instructions
    assert replacement_source_of("심각한") == "심각합니다"
