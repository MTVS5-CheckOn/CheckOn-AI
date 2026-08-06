"""🔴 강조점 0건의 **이유**가 구분되지 않는다 (㉪·㉲).

초안에 강조점이 조용히 0건으로 나가는데, 그 이유가 넷이고 산출물만 보면 **넷이 같다**:

    ① plan LLM 호출이 실패했다              → logger.info 한 줄  (㉲)
    ② plan이 응답했는데 파싱 0건이다         → 아무것도          (㉪)
    ③ 파싱은 됐는데 근거 검증에서 전량 드롭  → logger.info 한 줄
    ④ 진짜로 강조할 게 없었다                → (정상)

🔴 **㉪은 ㉦과 같은 유형이다 — CI에서 한 번도 안 도는 경로가 있다.**
`FakeCounselLlmProvider`가 `request`를 **완전히 무시**하고 고정 문자열을 내므로 plan 호출도
write 호출도 같은 값이다. 파서는 `학생참조 | 강조점` 형식을 기대하고 `"|"` 없는 줄을
`continue`하므로 **항상 `{}`** 다. ⇒ `plan → parse_plan_response → ground_emphasis →
프롬프트 주입` 체인이 **조립 루트에서 통째로 안 돈다**(실측: `parse_plan_response`를
호출하는 테스트가 CI 전체에 **0개**).

⚠ **가르는 축은 role이 아니라 `prompt_id`다** — plan도 write도 `ModelRole.COUNSELOR`다.

🔴 **이 파일이 고치는 것은 대역의 충실도와 관측이다.** 실 provider
(`LLM_PROVIDER=openai_compat`)에서는 plan 프롬프트가 형식을 지시하므로 정상 동작할 수
있다 — **미실측이다.** *"실 LLM이 강조점을 못 낸다"* 를 주장하지 않는다(99 ㉲).
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine, Mapping, Sequence
from uuid import UUID

import pytest

from ai.composition.counsel.assembly import (
    build_counsel_llm_provider,
    build_counsel_provider,
)
from ai.composition.counsel.grounding import ground_emphasis
from ai.composition.counsel.provider import (
    COUNSEL_GEN_PARAMS,
    PLAN_PROMPT_ID,
    PLAN_PROMPT_VERSION,
    PROMPT_ID,
    assemble_plan_prompt,
    parse_plan_response,
)
from ai.contracts.composition import (
    CommStyle,
    DraftContext,
    EvidenceFact,
    Frequency,
    Interest,
    LabelSnapshot,
    PlanOutcome,
    Sensitivity,
)
from ai.contracts.execution import Capability, ExecutionContext, VersionSet
from ai.contracts.llm import LLMRequest, ModelRole

_MAX_POINTS = 3


def _run[T](coro: Coroutine[object, object, T]) -> T:
    return asyncio.run(coro)


def _context(ref: str, record_id: str | None = "le_2041") -> DraftContext:
    return DraftContext(
        student_ref=ref,
        guardian_ref=f"gd_{ref}",
        label_snapshot=LabelSnapshot(
            comm=CommStyle.DATA,
            sensitivity=Sensitivity.ANXIOUS,
            interest=Interest.GRADE,
            frequency=Frequency.FREQUENT,
        ),
        facts=(
            EvidenceFact(label="이번 주 정답률", value="62%", record_id=record_id),
        ),
        evidence_summaries=(),
        period_label="2026년 8월",
        fallback_text="이번 주 학습 상황을 정리해 보내드립니다.",
    )


def _execution_context() -> ExecutionContext:
    return ExecutionContext(
        execution_id=UUID("00000000-0000-4000-8000-0000000000e1"),
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


def _plan_request(contexts: Mapping[str, DraftContext], refs: Sequence[str]) -> LLMRequest:
    return LLMRequest(
        role=ModelRole.COUNSELOR,
        prompt=assemble_plan_prompt(contexts, refs, max_points=_MAX_POINTS),
        prompt_id=PLAN_PROMPT_ID,
        prompt_version=PLAN_PROMPT_VERSION,
        generation_params=COUNSEL_GEN_PARAMS,
    )


# ── ㉪ CI 대역이 plan 형식 계약을 지킨다 ───────────────────────────


def test_the_ci_fake_answers_plan_in_the_parseable_format() -> None:
    """🔴 무인자 Fake가 **plan 호출에는 파서가 먹는 형식**을 낸다.

    ⚠ 판정 축은 `prompt_id`다 — role은 plan·write 둘 다 `COUNSELOR`라 못 가른다.
    """
    contexts = {"st_1": _context("st_1"), "st_2": _context("st_2", "le_2077")}
    refs = sorted(contexts)
    provider = build_counsel_llm_provider()

    result = _run(provider.complete(_plan_request(contexts, refs), _execution_context()))
    parsed = parse_plan_response(result.text or "", refs)

    assert parsed, (
        f"Fake가 plan 호출에 파싱 불가한 응답을 냈다: {result.text!r} — "
        "plan→파싱→grounding→주입 체인이 CI에서 한 번도 안 돈다(㉪)"
    )
    assert set(parsed) == set(refs)


def test_the_ci_fake_still_answers_write_with_the_draft_text() -> None:
    """⚠ write 경로는 **바뀌지 않는다** — 남의 테스트 12곳이 그 동작에 기대고 있다."""
    contexts = {"st_1": _context("st_1")}
    write_request = _plan_request(contexts, ["st_1"]).model_copy(
        update={"prompt_id": PROMPT_ID}
    )
    result = _run(build_counsel_llm_provider().complete(write_request, _execution_context()))
    assert "|" not in (result.text or ""), (
        "write 응답까지 plan 형식으로 바뀌었다 — 초안 본문에 파이프가 섞인다"
    )


def test_an_explicit_text_argument_always_wins() -> None:
    """🔴 명시 `text` 인자가 있으면 **plan 호출이어도** 그대로 낸다.

    소비처 12곳 중 대부분이 write 경로 검증용으로 특정 문자열을 주입한다
    (`FakeCounselLlmProvider("정답률은 62%였습니다.")`). 그 동작이 바뀌면 남의 테스트가
    **이유 없이** 깨진다 — 분기는 **무인자일 때만**이다.
    """
    from ai.composition.counsel.provider import FakeCounselLlmProvider

    pinned = "정답률은 62%였습니다."
    contexts = {"st_1": _context("st_1")}
    result = _run(
        FakeCounselLlmProvider(pinned).complete(
            _plan_request(contexts, ["st_1"]), _execution_context()
        )
    )
    assert result.text == pinned


def test_the_fake_gives_no_emphasis_to_students_without_evidence() -> None:
    """⚠ 근거 0건 학생에게는 **줄을 내지 않는다**.

    그 학생은 강조점 0건이 정답이고, `ground_emphasis`가 드롭할 재료를 억지로 만들면
    `all_dropped`가 거짓으로 뜬다(관측을 고치려다 관측을 오염시키는 꼴).
    """
    contexts = {"st_1": _context("st_1"), "st_2": _context("st_2", record_id=None)}
    refs = sorted(contexts)
    result = _run(
        build_counsel_llm_provider().complete(
            _plan_request(contexts, refs), _execution_context()
        )
    )
    parsed = parse_plan_response(result.text or "", refs)
    assert "st_1" in parsed
    assert "st_2" not in parsed, "근거 0건 학생에게 강조점을 지어냈다"


def test_the_whole_chain_runs_through_the_assembly_root() -> None:
    """🔴 **㉪이 실제로 무엇을 살리는지** — 체인이 조립 루트에서 끝까지 돈다.

    plan → 파싱 → `ground_emphasis`(record_id 실존 대조) → 프롬프트 주입.
    실측(8/8): 이 PR 전에는 `parse_plan_response`를 부르는 테스트가 CI 전체에 **0개**였다.
    """
    from ai.composition.counsel.prompt import assemble_prompt

    contexts = {"st_1": _context("st_1"), "st_2": _context("st_2", "le_2077")}
    refs = sorted(contexts)
    provider = build_counsel_provider(build_counsel_llm_provider())

    planned = _run(
        provider.plan(
            contexts=contexts, student_refs=refs, execution_context=_execution_context()
        )
    )
    assert planned, "조립 루트의 plan이 강조점을 0건으로 냈다"

    grounded = ground_emphasis(planned, contexts=contexts)
    assert grounded.emphasis_points, (
        f"근거 실존 대조에서 전량 드롭됐다: {grounded.drops}"
    )
    for ref, points in grounded.emphasis_points.items():
        allowed = contexts[ref].cited_record_ids()
        for point in points:
            assert any(f"record_id={rid}" in point for rid in allowed), (
                f"{ref}의 강조점이 실존하지 않는 record_id를 인용한다: {point!r}"
            )

    prompt = assemble_prompt(contexts["st_1"], tuple(grounded.emphasis_points["st_1"]))
    for point in grounded.emphasis_points["st_1"]:
        assert point in prompt, "강조점이 최종 프롬프트에 실리지 않았다"
    assert prompt != assemble_prompt(contexts["st_1"], ()), (
        "강조점이 있는데 빈 emphasis 경로와 프롬프트가 같다 — 주입이 안 됐다"
    )


# ── ㉲ 강조점 0건의 이유를 남긴다 ──────────────────────────────────


def test_the_four_reasons_are_distinguishable() -> None:
    """🔴 네 이유가 **값으로** 구분된다 — 산출물만 보고 넷을 가를 수 있어야 한다."""
    assert {outcome.value for outcome in PlanOutcome} == {
        "ok",
        "llm_failed",
        "unparsed",
        "all_dropped",
    }


@pytest.mark.parametrize(
    ("scenario", "expected"),
    [
        ("ok", PlanOutcome.OK),
        ("llm_failed", PlanOutcome.LLM_FAILED),
        ("unparsed", PlanOutcome.UNPARSED),
        ("all_dropped", PlanOutcome.ALL_DROPPED),
        ("nothing_to_say", PlanOutcome.OK),
    ],
)
def test_the_graph_records_why_emphasis_is_empty(
    scenario: str, expected: PlanOutcome
) -> None:
    """🔴 그래프가 네 경우를 **state에** 남긴다 — 진짜 0건(`nothing_to_say`)은 `ok`다.

    ⚠ 분모는 **잡**이다. plan은 잡당 1회 사건이라 `StudentResult`·`AGENT_STEP`이 아니라
    잡 단위 state 필드에 남긴다(99 ㊻ⓑ 규율 · 그래프가 이미 같은 근거로 plan 실패를
    서킷 카운터에서 뺐다).
    """
    from _plan_outcome_harness import run_plan_scenario

    state = run_plan_scenario(scenario)
    assert state.plan_outcome is expected, (
        f"{scenario}에서 plan_outcome이 {state.plan_outcome}로 나왔다 — 네 이유가 "
        "구분되지 않으면 '강조점이 왜 없지'에 답할 수 없다(㉲)"
    )


def test_partial_drops_are_counted_even_when_the_outcome_is_ok() -> None:
    """⚠ 일부만 드롭되면 `ok` + `plan_dropped > 0`이다 — 그래서 둘 다 필요하다."""
    from _plan_outcome_harness import run_plan_scenario

    state = run_plan_scenario("partial_drop")
    assert state.plan_outcome is PlanOutcome.OK
    assert state.plan_dropped == 1
    assert state.emphasis_points, "살아남은 강조점까지 버렸다"


def test_the_state_carries_no_response_body() -> None:
    """🔴 **본문을 담지 않는다** — state는 체크포인트·트레이스 두 경로로 나간다(7/30).

    사유 코드와 개수까지다. 응답 원문이나 드롭된 강조점 문구를 담으면 그 두 경로로
    그대로 샌다.
    """
    from ai.composition.counsel.state import CounselPackState

    fields = set(CounselPackState.model_fields)
    assert not {
        "plan_response",
        "plan_raw",
        "dropped_points",
        "plan_drop_reasons",
    } & fields, "plan 사유 필드에 본문·문구를 담았다(7/30 규율 위반)"
    assert {"plan_outcome", "plan_dropped"} <= fields


def test_the_pack_record_carries_the_plan_outcome() -> None:
    """⚠ 사유가 **밖으로 나간다** — `pack://` 결과 계약에 실려 워커가 저장한다.

    state에만 있으면 체크포인트를 뒤져야 읽을 수 있다. 잡이 끝나면 체크포인트는 재개
    대상이 아니므로, 사후에 *"강조점이 왜 없었지"* 를 묻는 사람이 볼 곳이 없다.
    """
    from ai.composition.counsel.stores import CounselPackResultRecord

    fields = set(CounselPackResultRecord.model_fields)
    assert {"plan_outcome", "plan_dropped"} <= fields
