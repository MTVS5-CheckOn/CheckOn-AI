"""㉘ period_label 숫자의 게이트 오탐 — 프롬프트가 시킨 표기를 게이트가 막는다.

결함(99 D ㉘ · 점검 3-7): 템플릿 `counsel_pack.txt`는 "{period_label} 상담 초안을
작성하세요"라고 **지시**하는데, `DraftContext.allowed_numbers()`는 `facts`·
`evidence_summaries`의 숫자만 모은다. LLM이 지시대로 "2026년 7월"을 되뇌기만 해도
`ungrounded_number:2026`으로 거부돼 **정상 문장이 게이트에 막힌다**.

규정: `docs/part_a/05_tone_mapping.md` §6-1 — `period_label`은 스냅숏 대상 기간의
결정론 파생이라 `facts`의 수치와 같은 지위다. 어느 출처에도 없는 숫자는 그대로 거부한다
(불변식 1·2는 불변 — 이 파일의 역케이스가 그것을 고정한다).
"""

from __future__ import annotations

from ai.composition.counsel.gate import check_counsel_gate
from ai.composition.counsel.prompt import assemble_prompt
from ai.composition.counsel.provider import max_chars_for
from ai.contracts.composition import (
    CommStyle,
    DraftContext,
    EvidenceFact,
    Frequency,
    Interest,
    LabelSnapshot,
    Sensitivity,
)

#: 근거에 2026·7이 **없는** 컨텍스트 — 기간 숫자의 출처가 period_label 하나뿐이어야
#: 오탐 재현이 성립한다(facts에 우연히 섞이면 이 테스트는 아무것도 증명하지 못한다).
_PERIOD = "2026년 7월"


def _context() -> DraftContext:
    return DraftContext(
        student_ref="st_1",
        guardian_ref="gd_1",
        label_snapshot=LabelSnapshot(
            comm=CommStyle.DATA,
            sensitivity=Sensitivity.ANXIOUS,
            interest=Interest.GRADE,
            frequency=Frequency.FREQUENT,
        ),
        facts=(EvidenceFact(label="이번 주 정답률", value="62%"),),
        evidence_summaries=("지난주 과제 미제출",),
        period_label=_PERIOD,
        fallback_text="이번 주 학습 상황을 정리해 보내드립니다.",
    )


def test_prompt_instructs_the_period_label() -> None:
    """결함의 전제 — 프롬프트가 기간 표기를 실제로 지시한다(지시가 없으면 오탐도 없다)."""
    assert _PERIOD in assemble_prompt(_context())


def test_period_label_numbers_are_not_grounded_in_facts() -> None:
    """전제 ② — 2026·7의 출처는 period_label뿐이다(facts·summaries에 없다)."""
    context = _context()
    haystack = "".join(
        [*(f.value for f in context.facts), *context.evidence_summaries]
    )
    assert "2026" not in haystack
    assert "7" not in haystack


def test_draft_echoing_the_period_label_passes_gate() -> None:
    """🔴 ㉘ 재현 — 지시대로 기간을 되뇐 정상 문장이 거부되면 안 된다."""
    context = _context()
    result = check_counsel_gate(
        f"{_PERIOD} 학습 상황을 전해 드립니다. 이번 주 정답률은 62%였습니다.",
        context,
        max_chars=max_chars_for(context), min_chars=0,
    )
    assert result.passed, result.reason


def test_period_label_numbers_are_in_allowed_set() -> None:
    """허용집합 자체를 고정한다 — facts·summaries·period_label 세 출처."""
    assert _context().allowed_numbers() == frozenset({"62", "2026", "7"})


def test_number_outside_every_source_is_still_rejected() -> None:
    """역케이스 — 창작 수치 차단은 불변이다(불변식 1·2). ㉘ 수정이 게이트를 무르게 하지 않는다."""
    context = _context()
    result = check_counsel_gate(
        "정답률이 83%까지 올랐습니다.", context, max_chars=max_chars_for(context), min_chars=0
    )
    assert not result.passed
    assert result.reason == "ungrounded_number:83"
