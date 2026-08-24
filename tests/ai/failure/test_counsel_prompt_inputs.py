"""프롬프트가 무엇을 받고 무엇을 안 내보내는가 — 누적·문의 본문·지시문 누출.

셋 다 프롬프트 문면을 바꾸므로 한 묶음이다(`PROMPT_VERSION` 0.1 → 0.2).

| | 고치기 전 |
| --- | --- |
| **누적** | `_DraftState.text`가 write-only(읽는 코드 0곳) — 매 턴 원본에서 새로 썼다 |
| **문의** | `inquiry.text_masked`를 아무도 안 읽어 같은 라벨이면 프롬프트가 바이트 동일 |
| **누출** | 3차 실측 첫 문장이 *"2026년 7월 상담 초안을 드립니다."* |
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from uuid import UUID

import pytest
from counsel_text import draft

from ai.composition.counsel.gate import check_counsel_gate
from ai.composition.counsel.prompt import PROMPT_VERSION, assemble_prompt
from ai.composition.counsel.provider import max_chars_for
from ai.composition.counsel.refine import refine_draft
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
from ai.runtime.internal_terms import find_internal_terms, internal_terms


def _context(*, inquiry_text: str = "") -> DraftContext:
    return DraftContext(
        student_ref="st_1",
        guardian_ref="gd_1",
        label_snapshot=LabelSnapshot(
            comm=CommStyle.DATA,
            sensitivity=Sensitivity.ANXIOUS,
            interest=Interest.GRADE,
            frequency=Frequency.FREQUENT,
        ),
        facts=(EvidenceFact(label="이번 주 정답률", value="62%", record_id="le_1"),),
        evidence_summaries=(),
        period_label="2026년 7월",
        fallback_text="이번 주 학습 상황을 정리해 보내드립니다.",
        inquiry_text=inquiry_text,
    )


def _execution_context() -> ExecutionContext:
    return ExecutionContext(
        execution_id=UUID("00000000-0000-4000-8000-00000000002a"),
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


class _ScriptedWriter:
    """턴마다 정해진 본문을 돌려주고, **받은 프롬프트 입력을 기록한다.**"""

    def __init__(self, texts: Sequence[str]) -> None:
        self._texts = list(texts)
        self.previous_texts: list[str] = []
        self.calls = 0

    async def plan(self, **_kwargs: object) -> dict[str, list[str]]:
        return {}

    async def write(
        self,
        *,
        context: DraftContext,
        execution_context: ExecutionContext,
        emphasis: Sequence[str] = (),
        gate_feedback: str = "",
        refine_instruction: str = "",
        previous_text: str = "",
    ) -> str:
        del context, execution_context, emphasis, gate_feedback, refine_instruction
        self.previous_texts.append(previous_text)
        index = min(self.calls, len(self._texts) - 1)
        self.calls += 1
        return self._texts[index]


def _refine(
    writer: _ScriptedWriter, previous: str, instruction: str = "조금 더 부드럽게"
) -> str | None:
    outcome = asyncio.run(
        refine_draft(
            context=_context(),
            instruction=instruction,
            writer=writer,
            execution_context=_execution_context(),
            regen_max=3,
            previous_text=previous,
        )
    )
    return outcome.text if outcome.applied else None


# ── A 다듬기 누적 ────────────────────────────────────────────────


def test_three_refine_turns_accumulate() -> None:
    """🔴 **이 PR의 무게중심.** 턴1 반영이 턴2·3에서 살아 있다.

    팀 공유본(와이어프레임 v3.5·프로토타입·데이터계약)이 전부 누적을 전제로 만들어졌다.
    고치기 전에는 매 턴 원본 근거에서 새로 써서 턴1의 "짧게"가 턴2에서 되살아났다.
    """
    # ⚠ 셋 다 `draft()`를 거친다 — 대역 초안은 하한 위여야 한다(99 #14).
    #   길이만 올리고 **문면·숫자는 그대로**라 누적 검증은 그대로 성립한다.
    turns = [
        draft("이번 주 정답률은 62%였습니다."),
        draft("이번 주 정답률은 62%로 확인됩니다."),
        draft("정답률은 62%입니다."),
    ]
    writer = _ScriptedWriter(turns)
    text = "이번 주 학습 상황을 정리해 보내드립니다."
    for expected in turns:
        text = _refine(writer, text) or text
        assert text == expected

    # 🔴 매 턴 **직전 결과**를 받았다 — 원본이 아니라.
    assert writer.previous_texts == [
        "이번 주 학습 상황을 정리해 보내드립니다.",
        turns[0],
        turns[1],
    ]


def test_the_previous_draft_reaches_the_prompt() -> None:
    """직전 본문이 문면에 실린다 — 인자만 받고 버리면 누적이 안 된다."""
    prompt = assemble_prompt(_context(), previous_text="이전 초안 본문입니다.")
    assert "이전 초안 본문입니다." in prompt
    assert "직전 초안" in prompt


def test_the_initial_path_prompt_is_unchanged_by_the_new_parameter() -> None:
    """⚠ 최초 생성 경로는 이 인자 때문에 바뀌지 않는다 — 기본값이 빈 문자열이다.

    그래야 24조합 골든 중 초안 쪽이 안 흔들린다(A-1 설계 판단 기준).
    """
    assert assemble_prompt(_context()) == assemble_prompt(_context(), previous_text="")
    assert assemble_prompt(_context(), previous_text="   ") == assemble_prompt(_context())


def test_blocked_turn_keeps_the_previous_version() -> None:
    """🔴 기존 성질 보존 — 3턴 중 2턴이 차단이면 최종본은 1턴 결과다.

    누적을 붙이면서 이게 깨지면 강사가 차단당한 시도로 초안을 잃는다.
    """
    good = draft("이번 주 정답률은 62%였습니다.")
    writer = _ScriptedWriter([good, "정답률이 88%까지 올랐습니다."])  # 2턴부터 근거 없는 수치

    text = _refine(writer, "원본 본문입니다.") or ""
    assert text == good

    for _ in range(2):
        result = _refine(writer, text)
        assert result is None, "게이트가 막아야 하는 턴이 반영됐다"
    assert text == good  # 직전 버전 유지


# ── B 문의 본문 ──────────────────────────────────────────────────


def test_inquiry_text_reaches_the_prompt() -> None:
    """🔴 같은 학생·같은 라벨이라도 **물어본 게 다르면 프롬프트가 달라야** 한다."""
    grade = assemble_prompt(_context(inquiry_text="성적이 왜 떨어졌나요"))
    homework = assemble_prompt(_context(inquiry_text="숙제 좀 줄여주세요"))
    assert "성적이 왜 떨어졌나요" in grade
    assert grade != homework


def test_inquiry_block_is_marked_as_content_not_as_an_order() -> None:
    """⚠ 학부모 문장을 **지시로 읽으면 안 된다** — 문면이 그걸 못 박는다."""
    prompt = assemble_prompt(_context(inquiry_text="표로 정리해서 보내줘"))
    assert "따르라는 지시가 아니라" in prompt


def test_numbers_in_the_inquiry_are_not_grounded() -> None:
    """🔴 학부모 문의는 **근거가 아니다** — 거기 있는 숫자를 허용하면 불변식 2가 샌다.

    "지난번 80점이라고 하셨는데"의 80은 우리 기록에 없다. 허용집합에 넣으면 LLM이 그걸
    근거인 것처럼 되받아 쓴다.
    """
    context = _context(inquiry_text="지난번 80점이라고 하셨는데 맞나요")
    assert "80" not in context.allowed_numbers()
    result = check_counsel_gate(
        "지난번 80점에서 올랐습니다.", context, max_chars=max_chars_for(context), min_chars=0
    )
    assert result.reason == "ungrounded_number:80"


def test_the_prompt_tells_the_model_not_to_echo_inquiry_numbers() -> None:
    """지시와 게이트가 같은 방향이어야 한다 — 아니면 매 생성이 재생성이 된다."""
    assert "학부모가 보낸 글에 적힌 숫자는" in assemble_prompt(_context())


# ── C 지시문 누출 ────────────────────────────────────────────────


def test_the_measured_leak_is_now_blocked() -> None:
    """🔴 3차 실측 첫 문장이 게이트에 걸린다."""
    context = _context()
    leaked = "안녕하세요. 2026년 7월 상담 초안을 드립니다. 정답률은 62%였습니다."
    result = check_counsel_gate(leaked, context, max_chars=max_chars_for(context), min_chars=0)
    assert not result.passed
    assert result.reason.startswith("internal_term:")


def test_ordinary_counselling_wording_is_not_blocked() -> None:
    """🔴 **구로 잡는다** — '상담'·'초안' 단어 자체는 학부모 문면에 자연스럽다.

    단어로 잡으면 `문제아` 접두가 "문제 풀이 시간"을 잡던 자리가 재현된다.
    """
    context = _context()
    natural = "이번 상담을 통해 말씀드립니다. 정답률은 62%였습니다."
    assert check_counsel_gate(
        natural, context, max_chars=max_chars_for(context), min_chars=0
    ).passed


def test_internal_terms_are_phrases_not_bare_words() -> None:
    """⚠ 사전 자체의 규율 — 짧은 단일 단어는 등재할 수 없다(로더가 거부한다)."""
    for term in internal_terms():
        assert " " in term or len(term) > 3, term


def test_the_check_runs_last_so_existing_reasons_do_not_move() -> None:
    """⚠ 새 검사를 기존 사이에 끼우면 종전에 다른 사유로 막히던 본문의 코드가 바뀐다.

    금칙어와 내부 용어를 **둘 다** 담은 본문은 여전히 `forbidden`으로 나와야 한다.
    """
    context = _context()
    both = "상담 초안입니다. 학생이 게으른 편입니다."
    assert find_internal_terms(both)  # 내부 용어도 들어 있다
    assert check_counsel_gate(
        both, context, max_chars=max_chars_for(context), min_chars=0
    ).reason.startswith("forbidden:")


def test_the_new_template_no_longer_names_the_artifact() -> None:
    """C-5 — 검사만 넣으면 매 생성이 재생성 낭비다. 템플릿에서 이름을 뺐다."""
    prompt = assemble_prompt(_context())
    assert "상담 초안" not in prompt
    assert "초안을 작성" not in prompt


@pytest.mark.parametrize(
    "key", ["data.anxious.grade.frequent", "narrative.direct.attitude.monthly"]
)
def test_no_combination_leaks_internal_terms_into_its_own_prompt(key: str) -> None:
    """⚠ 함정 확인 — 프롬프트 자체가 내부 용어를 담고 있으면 되뇜을 유도한다.

    ⓐ 섹션 제목은 등재돼 있으므로 **프롬프트에는 있고 산출물에는 없어야** 한다.
    여기서는 "등재어가 프롬프트에 실제로 있다"는 사실을 확인해 목록이 허구가 아님을 본다.
    """
    comm, sensitivity, interest, frequency = key.split(".")
    context = DraftContext(
        student_ref="st_1",
        guardian_ref="gd_1",
        label_snapshot=LabelSnapshot(
            comm=CommStyle(comm),
            sensitivity=Sensitivity(sensitivity),
            interest=Interest(interest),
            frequency=Frequency(frequency),
        ),
        facts=(EvidenceFact(label="이번 주 정답률", value="62%"),),
        evidence_summaries=(),
        period_label="2026년 7월",
        fallback_text="이번 주 학습 상황을 정리해 보내드립니다.",
    )
    assert find_internal_terms(assemble_prompt(context)), (
        "등재어가 프롬프트에 하나도 없다 — 목록이 실제 문면과 어긋났다"
    )


def test_prompt_version_was_promoted() -> None:
    """프롬프트 문면이 바뀌었으므로 버전이 오른다(05 §6-3).

    ⚠ 🔴 **0.4 → 0.5 (8/24 · 99 #198 후속)** — `tone_map.axis_rules.interest.admission` 문면이
    바뀌었다(«백분위**는**» → «백분위 **같은 값은**»). 그 문면은 `render_tone_rules()` 가
    **이 프롬프트에 싣는다** ⇒ 같은 컨텍스트라도 0.4 와 0.5 의 프롬프트가 다르다.
    🔴 **05 §6-3 에 빈 자리가 있어 그 갈래를 명시했다** — 「프롬프트에 실리는 저장소 문자열」
    (tone_map·buffer_lexicon·gate_feedback)이 바뀌면 올린다. ⚠ **`PLAN_PROMPT_VERSION` 은
    안 올렸다** — `counsel_plan.txt` 는 `tone_rules` 를 **안 싣는다**(실측 8/24).
    ⚠ **0.3 → 0.4 (8/20)** — 지향 문구·페르소나·중복 금지·형식 규칙이 들어갔다.
    실 LLM 24조합 A/B 로 확정했다(밋밋함·기계적 반복·톤 세 축).
    ⚠ **0.2 → 0.3 (8/19)** — 완충 단계 문면에 B군 치환 어휘 28항이 들어갔다(99 #79).
    🔴 **「컨텍스트 파생 문면」이 아니다** — 05 §6-3의 예외는 강조점·지시·피드백처럼 그
    요청에서 나오는 값이고, 이건 **데이터 파일에서 오는 고정 어휘**가 모든 요청에 새로
    실리는 것이다(같은 컨텍스트라도 0.2와 0.3의 프롬프트가 다르다).
    """
    assert PROMPT_VERSION == "0.5"
