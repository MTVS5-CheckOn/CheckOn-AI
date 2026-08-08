"""plan 산출(`emphasis_points`)이 **포착 시점에** 마스킹을 거치는가 (99 #25).

🔴 **입력측이 아니라 출력측이다.** `GatewayPlanner`는 프롬프트를 `redact()`로 걸러
보내지만(fail-closed), **돌아온 텍스트**는 걸러지지 않고 팩 스냅숏에 영속된다.

**선례가 같은 위험에 이미 판정을 냈다** — `db/repositories/llm_payload.py`가 응답을
*"어떤 게이트도 통과하지 않았다 ⇒ 저장 전 변형"* 으로 다루고 *"마스킹은 포착 시점에
한다(저장 시점이 아니다)"* 를 못 박는다. `emphasis_points`는 **같은 성질의 값인데 다른
저장소로 간다** — 같은 값에 두 규율이 서 있었다.

⚠ **`FakeCounselProvider`로는 못 본다** — 그건 게이트웨이를 우회한다(99 ㊱에서 겪었다).
실 경로(`GatewayPlanner` + 게이트웨이)로만 묻는다.
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import Final
from uuid import uuid4

from ai.composition.counsel.assembly import build_counsel_gateway
from ai.composition.counsel.prompt import assemble_prompt
from ai.composition.counsel.provider import (
    FakeCounselLlmProvider,
    GatewayPlanner,
)
from ai.composition.counsel.versions import counsel_versions
from ai.contracts.composition import (
    CommStyle,
    DraftContext,
    EvidenceFact,
    Frequency,
    Interest,
    LabelSnapshot,
    Sensitivity,
)
from ai.contracts.execution import Capability, ExecutionContext
from ai.runtime.redaction import redact

#: plan 응답 형식 — `학생참조 | 강조점1; 강조점2`.
#: 🔴 **실명을 문맥 신호와 함께** 넣는다(`김민준 학생의`) — `redact()`는 호칭·조사가
#: 있어야 인명을 확정한다. 맨 이름은 안 잡히는데 그건 **처방의 한계**이지 이 테스트의
#: 축이 아니다(99 #28).
_NAME: Final = "김민준"
_PLAN_TEXT: Final = f"st_1 | {_NAME} 학생의 어휘 정확도 (record_id=le_1)"
_STUDENT_REFS: Final = ("st_1",)


def _run[T](coro: Coroutine[object, object, T]) -> T:
    return asyncio.run(coro)


def _context() -> ExecutionContext:
    return ExecutionContext(
        execution_id=uuid4(),
        tenant_id="t1",
        capability=Capability.COMPOSITION,
        input_snapshot_hash="sha256:" + "c" * 64,
        versions=counsel_versions(),
    )


def _draft_context() -> DraftContext:
    return DraftContext(
        student_ref="st_1",
        guardian_ref="gd_1",
        label_snapshot=LabelSnapshot(
            comm=CommStyle.NARRATIVE,
            sensitivity=Sensitivity.ANXIOUS,
            interest=Interest.ATTITUDE,
            frequency=Frequency.FREQUENT,
        ),
        facts=(EvidenceFact(label="이번 주 정답률", value="62%"),),
        period_label="2026년 7월",
        fallback_text="이번 주 학습 상황을 정리해 보내드립니다.",
    )


def _plan() -> dict[str, list[str]]:
    planner = GatewayPlanner(build_counsel_gateway(FakeCounselLlmProvider(_PLAN_TEXT)))
    return _run(
        planner.plan(
            contexts={},
            student_refs=_STUDENT_REFS,
            execution_context=_context(),
        )
    )


def test_the_fixture_actually_reaches_the_parser() -> None:
    """🔴 검사 경로가 끊기면 통과가 아니라 실패다.

    파싱이 0건이면 *"실명이 안 샌다"* 가 아니라 **아무것도 안 봤다**이다 — plan 응답
    형식이 바뀌면 위 픽스처가 조용히 빈 dict를 낸다.
    """
    assert _plan().get("st_1"), "plan 픽스처가 파싱되지 않았다 — 이 파일은 아무것도 안 본다"


def test_plan_output_is_masked_at_capture() -> None:
    """🔴 **포착 시점에 마스킹한다** — 저장 시점이 아니다(`llm_payload` 규약)."""
    points = _plan()["st_1"]
    joined = " ".join(points)
    assert _NAME not in joined, (
        f"plan 산출에 실명이 그대로 남았다: {joined!r} — 이 값은 팩 스냅숏에 영속되고 "
        "게이트를 안 탄다(초안 본문과 달리). 마스킹은 포착 시점에 한다"
    )
    assert "⟪이름" in joined, f"마스킹 토큰이 없다 — 값이 통째로 사라졌나: {joined!r}"


def test_the_masked_emphasis_survives_re_entry_without_a_second_pass() -> None:
    """🔴 **재입력 축** — 마스킹본이 다음 프롬프트에서 **2차 마스킹을 받으면 red**.

    `emphasis_points`는 저장만 되는 값이 아니다 — `graph.py:256`이 writer에 `emphasis=`로
    넘기고 그 프롬프트는 `GatewayDraftWriter.write`에서 **다시 `redact()`를 탄다.**
    비멱등이면 *"보낸 적 없는 문면"* 이 남거나 fail-closed로 **초안이 죽는다.**

    ⚠ 이 경로는 **이미 지원 대상**이다 — 트립와이어와 `LLM_PAYLOAD` 저장 훅이 마스킹
    통과본을 다시 검사하므로 `test_redaction_idempotence.py`가 코퍼스 전건 멱등성을
    세워 뒀다. 여기서는 **그 성질이 이 경로에도 물리는지**를 본다.
    """
    points = _plan()["st_1"]
    prompt = assemble_prompt(_draft_context(), points)
    outcome = redact(prompt)
    assert not outcome.uncertain, (
        "마스킹본을 실은 프롬프트가 fail-closed로 막혔다 — 재입력에서 초안이 죽는다"
    )
    for point in points:
        assert point in outcome.masked_text, (
            f"강조점이 재입력에서 2차 마스킹으로 변형됐다: {point!r} — "
            "「보낸 적 없는 문면」이 남는다"
        )
