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
    assert _context().allowed_numbers() == frozenset({"62", "2"})


# ── 게이트: 금칙어(buffer_lexicon A군 단일 참조) ───────────────────


@pytest.mark.parametrize("stem", ["게으르", "꼴찌", "다른 아이들은", "ADHD"])
def test_forbidden_terms_are_rejected(stem: str) -> None:
    result = check_counsel_gate(
        f"학생이 {stem}다는 인상입니다.", _context(), max_chars=_max_chars()
    )
    assert not result.passed
    assert result.reason == f"forbidden:{stem}"


def test_gate_uses_buffer_lexicon_single_source() -> None:
    """새 금칙어 목록을 만들지 않았다 — A군 20항을 그대로 쓴다."""
    assert len(forbidden_terms()) == 20


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
    assert f"블록당 {rule.sentences_per_block}문장" in prompt
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
