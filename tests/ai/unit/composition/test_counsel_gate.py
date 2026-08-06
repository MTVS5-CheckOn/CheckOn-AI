"""상담 초안 게이트·프롬프트 조립 — 결정론 검증(LLM 호출 없음).

게이트는 briefing_gate 원칙을 따른다(숫자 EXACT · 금칙어 · 길이 · 기호·토큰).
금칙어는 `buffer_lexicon.yaml` A군을 단일 참조한다(99 #15 ⓒ) — 새 목록을 만들지 않는다.
"""

from __future__ import annotations

import asyncio
from uuid import UUID

import pytest

from ai.composition.buffer_lexicon import forbidden_terms
from ai.composition.counsel.gate import check_counsel_gate
from ai.composition.counsel.prompt import assemble_prompt, tone_rule_for
from ai.composition.counsel.provider import (
    CHARS_PER_SENTENCE,
    CounselPlanner,
    DraftWriter,
    FakeCounselProvider,
    max_chars_for,
)
from ai.contracts.composition import (
    CommStyle,
    DraftContext,
    EvidenceFact,
    Frequency,
    Interest,
    LabelSnapshot,
    Sensitivity,
)
from ai.contracts.execution import Capability, ExecutionContext, VersionSet


def _context(
    *,
    comm: CommStyle = CommStyle.DATA,
    sensitivity: Sensitivity = Sensitivity.ANXIOUS,
    interest: Interest = Interest.GRADE,
    frequency: Frequency = Frequency.FREQUENT,
) -> DraftContext:
    return DraftContext(
        student_ref="st_1",
        guardian_ref="gd_1",
        label_snapshot=LabelSnapshot(
            comm=comm, sensitivity=sensitivity, interest=interest, frequency=frequency
        ),
        facts=(EvidenceFact(label="이번 주 정답률", value="62%"),),
        evidence_summaries=("지난주 과제 2건 미제출",),
        period_label="2026년 7월",
        fallback_text="이번 주 학습 상황을 정리해 보내드립니다.",
    )


def _execution_context() -> ExecutionContext:
    return ExecutionContext(
        execution_id=UUID("00000000-0000-4000-8000-00000000000c"),
        tenant_id="t1",
        capability=Capability.COMPOSITION,
        input_snapshot_hash="h",
        versions=VersionSet(
            pipeline_version="0.1",
            engine_version="0.1",
            schema_version="0.1",
            contract_version="0.1",
        ),
    )


def _max_chars() -> int:
    return max_chars_for(_context())


# ── 게이트: 통과 ─────────────────────────────────────────────────


def test_grounded_draft_passes() -> None:
    result = check_counsel_gate(
        "이번 주 정답률은 62%였습니다. 다음 주에는 오답 정리를 함께 해보겠습니다.",
        _context(),
        max_chars=_max_chars(),
    )
    assert result.passed, result.reason


# ── 게이트: 숫자 EXACT ────────────────────────────────────────────


def test_ungrounded_number_is_rejected() -> None:
    """근거에 없는 수치 — LLM이 수치를 만들지 않는다(불변식 1·2)."""
    result = check_counsel_gate(
        "정답률이 88%까지 올랐습니다.", _context(), max_chars=_max_chars()
    )
    assert not result.passed
    assert result.reason.startswith("ungrounded_number")


def test_grounded_numbers_come_from_context() -> None:
    """facts·evidence_summaries·period_label 세 출처(05 §6-1 · 99 D ㉘)."""
    assert _context().allowed_numbers() == frozenset({"62", "2", "2026", "7"})


# ── 게이트: 금칙어(buffer_lexicon A군 단일 참조) ───────────────────


@pytest.mark.parametrize("stem", ["게으르", "꼴찌", "다른 아이들은", "ADHD"])
def test_forbidden_terms_are_rejected(stem: str) -> None:
    result = check_counsel_gate(
        f"학생이 {stem}다는 인상입니다.", _context(), max_chars=_max_chars()
    )
    assert not result.passed
    assert result.reason == f"forbidden:{stem}"


def test_gate_uses_buffer_lexicon_single_source() -> None:
    """새 금칙어 목록을 만들지 않았다 — `buffer_lexicon.yaml` A군을 그대로 쓴다.

    23항 = 금칙 20 + **불규칙 활용 3**(8/5 · 99 D ⑰ 해소). 활용형은 코드가 종성 결합으로
    처리하고, 코드로 만들 수 없는 르·여·으탈락 불규칙만 사전에 어간으로 등재했다.
    """
    assert len(forbidden_terms()) == 23


# ── 게이트: 길이·기호·토큰 ────────────────────────────────────────


def test_too_long_is_rejected() -> None:
    result = check_counsel_gate("가" * (_max_chars() + 1), _context(), max_chars=_max_chars())
    assert not result.passed
    assert result.reason.startswith("too_long")


def test_mask_token_leak_is_rejected() -> None:
    result = check_counsel_gate("⟪이름1⟫ 학생은", _context(), max_chars=_max_chars())
    assert not result.passed
    assert result.reason == "token_leak"


def test_symbol_is_rejected() -> None:
    result = check_counsel_gate("정답률 $62$", _context(), max_chars=_max_chars())
    assert not result.passed
    assert result.reason == "symbol"


def test_empty_is_rejected() -> None:
    assert check_counsel_gate("   ", _context(), max_chars=_max_chars()).reason == "empty"


def test_gate_is_deterministic() -> None:
    args = ("정답률이 88%까지 올랐습니다.", _context())
    first = check_counsel_gate(*args, max_chars=_max_chars())
    second = check_counsel_gate(*args, max_chars=_max_chars())
    assert first == second


# ── 프롬프트 조립: tone_map 반영 ──────────────────────────────────


def test_prompt_reflects_tone_rule() -> None:
    """블록 순서·문장 수·조합 키가 프롬프트 문면에 들어간다."""
    context = _context()
    rule = tone_rule_for(context)
    prompt = assemble_prompt(context)
    assert "data.anxious.grade.frequent" in prompt
    assert f"문단마다 {rule.sentences_per_block}문장" in prompt  # 0.2 어휘
    for block in rule.blocks:
        assert block in prompt


def test_prompt_includes_only_context_evidence() -> None:
    prompt = assemble_prompt(_context())
    assert "이번 주 정답률: 62%" in prompt
    assert "지난주 과제 2건 미제출" in prompt


def test_prompt_without_facts_says_no_numbers() -> None:
    """근거가 없으면 그 사실을 명시한다 — 빈 블록으로 두면 LLM이 지어낸다."""
    context = _context().model_copy(update={"facts": (), "evidence_summaries": ()})
    assert "제공된 수치 없음" in assemble_prompt(context)


def test_prompt_is_deterministic() -> None:
    assert assemble_prompt(_context()) == assemble_prompt(_context())


# ── Fake provider ────────────────────────────────────────────────


def test_fake_satisfies_protocols() -> None:
    fake = FakeCounselProvider()
    assert isinstance(fake, CounselPlanner)
    assert isinstance(fake, DraftWriter)


def test_fake_plan_emits_record_id_backed_points() -> None:
    """plan 산출은 근거 record_id를 동반한다(불변식 ①)."""
    fake = FakeCounselProvider()
    planned = asyncio.run(
        fake.plan(
            contexts={"st_1": _context()},
            student_refs=["st_1"],
            execution_context=_execution_context(),
        )
    )
    assert planned["st_1"]
    assert "record_id=" in planned["st_1"][0]


def test_fake_write_is_scenario_driven_and_deterministic() -> None:
    def run() -> list[str]:
        fake = FakeCounselProvider(drafts=["초안 A", "초안 B"])
        return [
            asyncio.run(
                fake.write(context=_context(), execution_context=_execution_context())
            )
            for _ in range(3)
        ]

    assert run() == ["초안 A", "초안 B", "초안 B"]  # 마지막 시나리오 반복
    assert run() == run()


def test_fake_write_falls_back_to_context_template() -> None:
    fake = FakeCounselProvider()
    text = asyncio.run(
        fake.write(context=_context(), execution_context=_execution_context())
    )
    assert text == _context().fallback_text


# ── 길이 상한: 전체 본문 기준 (99 ㉤) ────────────────────────────

#: 8/6 3차 실측 산출 4건의 **최대** 문장 길이(47.0자)를 올림한 값. 아래 회귀 테스트가
#: "지시대로 쓴 초안"을 이 길이로 합성한다 — 임의로 고른 숫자가 아니다.
_MEASURED_CHARS_PER_SENTENCE = 47


def _sentence(length: int) -> str:
    """숫자·금칙어·기호가 없는 지정 길이 문장 — 길이 검사만 남긴다."""
    return "가" * (length - 1) + "."


def test_limit_covers_the_whole_body_not_one_block() -> None:
    """🔴 ㉤의 본체 — 게이트가 본문 전체를 받으므로 상한도 전체 기준이어야 한다.

    종전엔 `sentences_per_block × 120`이라 **블록 하나분**이었다. 3블록 조합에서
    프롬프트는 9문장을 지시하는데 상한은 3문장분이었다.
    """
    context = _context()
    rule = tone_rule_for(context)
    assert max_chars_for(context) == (
        len(rule.blocks) * rule.sentences_per_block * CHARS_PER_SENTENCE
    )


@pytest.mark.parametrize(
    ("comm", "sensitivity", "interest", "frequency"),
    [
        (CommStyle.DATA, Sensitivity.ANXIOUS, Interest.GRADE, Frequency.FREQUENT),
        (CommStyle.NARRATIVE, Sensitivity.ANXIOUS, Interest.ATTITUDE, Frequency.FREQUENT),
        (CommStyle.DATA, Sensitivity.DIRECT, Interest.ADMISSION, Frequency.MONTHLY),
        (CommStyle.NARRATIVE, Sensitivity.DIRECT, Interest.GRADE, Frequency.MONTHLY),
    ],
)
def test_a_draft_written_as_instructed_fits(
    comm: CommStyle,
    sensitivity: Sensitivity,
    interest: Interest,
    frequency: Frequency,
) -> None:
    """🔴 **프롬프트가 지시한 만큼 쓰면 통과해야 한다** — 이게 안 되면 게이트가 프롬프트와 싸운다.

    실측 재현(8/6): `narrative.anxious.attitude.frequent`에서 9문장 421자가
    `too_long:421>360`으로 막혔고, 통과한 유일한 초안은 LLM이 **덜 따라 7문장만** 쓴 것이었다.
    """
    context = _context(
        comm=comm, sensitivity=sensitivity, interest=interest, frequency=frequency
    )
    rule = tone_rule_for(context)
    as_instructed = " ".join(
        _sentence(_MEASURED_CHARS_PER_SENTENCE)
        for _ in range(len(rule.blocks) * rule.sentences_per_block)
    )
    result = check_counsel_gate(as_instructed, context, max_chars=max_chars_for(context))
    assert result.passed, (
        f"{len(rule.blocks)}블록×{rule.sentences_per_block}문장 지시인데 "
        f"{len(as_instructed)}자가 막혔다: {result.reason}"
    )


def test_the_limit_is_not_toothless() -> None:
    """⚠ 상한을 넓히면서 무력해지지 않았는지 — 폭주는 여전히 막힌다.

    실측 문장 길이의 **2배**로 쓰면(문장 수는 지시대로) 걸려야 한다. 안 걸리면
    `too_long`이 영영 안 뜨는 죽은 검사가 된다.
    """
    context = _context()
    rule = tone_rule_for(context)
    bloated = " ".join(
        _sentence(_MEASURED_CHARS_PER_SENTENCE * 2)
        for _ in range(len(rule.blocks) * rule.sentences_per_block)
    )
    result = check_counsel_gate(bloated, context, max_chars=max_chars_for(context))
    assert not result.passed
    assert result.reason.startswith("too_long:")


def test_no_combination_got_a_narrower_limit() -> None:
    """⚠ 24조합 어디도 좁아지지 않았다 — 넓히는 변경이 어딘가를 조이면 회귀다."""
    for comm in CommStyle:
        for sensitivity in Sensitivity:
            for interest in Interest:
                for frequency in Frequency:
                    context = _context(
                        comm=comm,
                        sensitivity=sensitivity,
                        interest=interest,
                        frequency=frequency,
                    )
                    rule = tone_rule_for(context)
                    previous = rule.sentences_per_block * 120  # 구 공식
                    assert max_chars_for(context) >= previous


# ── 허용 수치 커버리지 (C) ───────────────────────────────────────


def _numeric_context(
    *, label: str = "정답률", value: str = "62%", period: str = "2026년 7월"
) -> DraftContext:
    return DraftContext(
        student_ref="st_1",
        guardian_ref="gd_1",
        label_snapshot=LabelSnapshot(
            comm=CommStyle.DATA,
            sensitivity=Sensitivity.ANXIOUS,
            interest=Interest.GRADE,
            frequency=Frequency.FREQUENT,
        ),
        facts=(EvidenceFact(label=label, value=value),),
        evidence_summaries=(),
        period_label=period,
        fallback_text="이번 주 학습 상황을 정리해 보내드립니다.",
    )


def test_numbers_inside_the_fact_label_are_allowed() -> None:
    """🔴 label도 프롬프트에 실린다 — `- {label}: {value}`(render_evidence_block).

    `period_label`을 허용집합에 넣은 것과 **같은 근거**다: 프롬프트가 쓰라고 지시한 문면을
    게이트가 막으면 게이트가 프롬프트와 싸운다(99 ㉘).
    """
    context = _numeric_context(label="7월 3주차 정답률", value="62%")
    result = check_counsel_gate(
        "7월 3주차 정답률은 62%였습니다.", context, max_chars=max_chars_for(context)
    )
    assert result.passed, result.reason


@pytest.mark.parametrize(
    ("evidence", "body"),
    [
        ("출석 1,240회", "출석은 1240회였습니다."),  # 근거에 쉼표 · 본문에 없음
        ("출석 1240회", "출석은 1,240회였습니다."),  # 반대 방향
        ("출석 1,240회", "출석은 1,240회였습니다."),  # 양쪽 다 쉼표
    ],
)
def test_digit_separators_match_in_both_directions(evidence: str, body: str) -> None:
    """🔴 같은 값인데 **표기만 달라도** 막히던 것 — 양방향을 고정한다.

    `\\d+`가 쉼표에서 끊겨 `1,240`이 `{1, 240}`이 됐다. 한쪽만 정규화하면 반대 방향이
    남으므로 **추출 함수 자체**를 게이트와 허용집합이 공유한다.
    """
    context = _numeric_context(label="출석", value=evidence)
    result = check_counsel_gate(body, context, max_chars=max_chars_for(context))
    assert result.passed, result.reason


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ("정답률이 88%까지 올랐습니다.", "88"),  # 근거에 없는 수치
        ("출석은 1250회였습니다.", "1250"),  # 근사값 — 1,240과 다르다
        ("정답률은 62%이고 백분위는 31입니다.", "31"),  # 일부만 근거
    ],
)
def test_fabricated_numbers_are_still_blocked(body: str, expected: str) -> None:
    """🔴 **C의 유일한 안전 리스크** — 허용집합을 넓히면서 불변식 2가 느슨해지지 않았는지.

    근거에 없는 숫자는 여전히 막힌다. 이 역케이스가 통과하지 못하면 위 완화는 되돌려야 한다.
    """
    context = _numeric_context(label="출석", value="1,240회 · 정답률 62%")
    result = check_counsel_gate(body, context, max_chars=max_chars_for(context))
    assert not result.passed
    assert result.reason == f"ungrounded_number:{expected}"


def test_separator_normalisation_does_not_merge_separate_numbers() -> None:
    """⚠ 쉼표를 전부 지우면 `"62%, 71%"`가 `6271`로 붙어 **없던 수치가 생긴다.**

    숫자 사이에 낀 쉼표만 지운다 — 뒤에 공백이 있으면 자릿수 구분이 아니다.
    """
    context = _numeric_context(label="정답률", value="62%, 71%")
    allowed = context.allowed_numbers()
    assert {"62", "71"} <= allowed
    assert "6271" not in allowed


def test_allowed_numbers_still_rejects_what_no_source_provided() -> None:
    """출처 셋(label·value·period) 밖의 숫자는 허용집합에 없다."""
    context = _numeric_context(label="정답률", value="62%", period="2026년 7월")
    assert context.allowed_numbers() == {"62", "2026", "7"}
