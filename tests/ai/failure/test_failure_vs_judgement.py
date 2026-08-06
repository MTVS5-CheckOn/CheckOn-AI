"""🔴 "못 한 것"과 "안 하기로 판단한 것"을 가른다 — `error_codes.md` 첫 줄의 존재 이유.

문서가 *"AI가 못 한 것(에러)과 AI가 안 하기로 판단한 것(정상 상태)을 코드 레벨에서
분리한다"* 를 선언해 놨는데 **상담 다듬기 경로가 정확히 그 반대**로 짜여 있었다.

    refine.py  except LlmError: return RefineOutcome(blocked_reason=TONE_VIOLATION)

⇒ 벤더가 죽었는데 라우터가 **200**을 내고, 화면에는 06 §4 표의 문구가 뜬다 —
*"해당 표현은 안전 기준에 걸려 완곡한 표현으로 제안했어요."*
**벤더 장애를 강사의 말투 문제로 말하고 있었다.**

판별자(준영님 3분할 — 주체 축):

| 누가 바꿀 수 있나 | 결과 |
| --- | --- |
| 강사 | 200 + `blocked_reason` |
| 호출자(BE) | 4xx |
| 아무도 못 바꾼다 · **재시도 예산 소진** | 5xx |

마지막 조건은 구조적으로 충족된다: `gateway.complete`가 `reraise=True`로 재시도를 소진한
뒤 원 예외를 그대로 올린다(`llm/gateway.py:211·214`) — `refine_draft`가 받는 시점에는
예산이 이미 끝나 있다.

⚠ **게이트 소진은 여전히 200이다.** 그건 진짜 판단이고, 5xx로 올리면 불변식 4 위반이다.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import Any
from uuid import UUID

import pytest

from ai.composition.counsel.provider import RedactionBlockedError
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
from ai.contracts.llm import LlmError, LlmTimeout, LlmUnavailable, ParseFailed


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
        facts=(EvidenceFact(label="정답률", value="62%", record_id="le_1"),),
        evidence_summaries=(),
        period_label="2026년 7월",
        fallback_text="이번 기간 학습 상황을 정리해 보내드립니다.",
        inquiry_text=inquiry_text,
    )


def _execution_context() -> ExecutionContext:
    return ExecutionContext(
        execution_id=UUID("00000000-0000-4000-8000-00000000003a"),
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


class _Boom:
    """정해진 예외를 던지는 writer — 장애 주입."""

    def __init__(self, exc: Exception) -> None:
        self._exc = exc
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
        del previous_text
        self.calls += 1
        raise self._exc


def _refine(writer: Any, instruction: str = "조금 더 부드럽게") -> Any:  # noqa: ANN401
    return asyncio.run(
        refine_draft(
            context=_context(),
            instruction=instruction,
            writer=writer,
            execution_context=_execution_context(),
            regen_max=3,
        )
    )


# ── D · LLM 장애가 판단으로 위장되지 않는다 ──────────────────────


@pytest.mark.parametrize(
    "exc",
    [LlmTimeout("t"), LlmUnavailable("u"), LlmError("plain 4xx"), ParseFailed("p")],
    ids=["LlmTimeout", "LlmUnavailable", "LlmError", "ParseFailed"],
)
def test_llm_failure_is_not_swallowed_as_a_judgement(exc: LlmError) -> None:
    """🔴 장애는 **예외로 나간다** — `blocked_reason`으로 위장되지 않는다.

    고치기 전: 넷 전부 `applied=False · blocked_reason=tone_violation` → 라우터 200.
    """
    with pytest.raises(LlmError):
        _refine(_Boom(exc))


def test_the_failure_type_survives_the_boundary() -> None:
    """⚠ 타입이 보존돼야 변환 경계가 504와 503을 가를 수 있다."""
    with pytest.raises(LlmTimeout):
        _refine(_Boom(LlmTimeout("t")))
    with pytest.raises(LlmUnavailable):
        _refine(_Boom(LlmUnavailable("u")))


def test_gate_exhaustion_is_still_a_judgement() -> None:
    """🔴 회귀 방지 — 게이트 소진은 **여전히 200**이다(불변식 4).

    이 PR이 판단까지 5xx로 올리면 리뷰 반려 사유다(`error_codes` §4 마지막 줄).
    """

    class _Ungrounded(_Boom):
        async def write(self, **_kwargs: object) -> str:  # type: ignore[override]
            self.calls += 1
            return "정답률이 88%까지 올랐습니다."  # 근거(62%)에 없는 수치

    outcome = _refine(_Ungrounded(LlmError("unused")))
    assert outcome.applied is False
    assert outcome.blocked_reason is not None


# ── G · 마스킹 불확실의 주체를 가른다 ────────────────────────────


def test_pii_in_the_instruction_stays_a_judgement() -> None:
    """강사가 지시문에 실명을 썼다 → **강사가 고칠 수 있다** → 200 `pii_exposure`.

    ⚠ LLM 호출보다 앞에서 걸러야 원가가 0이다(06 §3·§5 사전 정적 검사와 같은 자리).
    """
    writer = _Boom(LlmError("호출되면 안 된다"))
    outcome = _refine(writer, instruction="박서연 어머니께 이렇게 써줘")
    assert outcome.applied is False
    assert outcome.blocked_reason is not None
    assert outcome.blocked_reason.value == "pii_exposure"
    assert writer.calls == 0, "지시문 차단이 LLM 호출보다 뒤에 있다 — 원가가 샌다"


def test_pii_from_the_context_is_a_failure_not_a_judgement() -> None:
    """🔴 BE가 준 컨텍스트에서 났다 → **강사는 아무것도 못 한다** → 5xx.

    지시를 백 번 고쳐도 같은 화면이 뜬다. 이걸 200으로 내면 강사가 고칠 수 없는 것을
    고치라고 말하는 셈이다.
    """
    from ai.runtime.errors import RedactionUncertain

    class _ContextBlocked(_Boom):
        async def write(self, **_kwargs: object) -> str:  # type: ignore[override]
            self.calls += 1
            raise RedactionBlockedError("컨텍스트 마스킹 불확실")

    with pytest.raises(RedactionUncertain):
        _refine(_ContextBlocked(LlmError("unused")), instruction="조금 더 부드럽게")
