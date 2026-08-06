"""🔴 브리핑이 라우터로 **예외를 올리지 않는다** — `detect.py`의 전제를 잠근다.

`detect.py:399`도 워커·라우터 2곳과 **같은 배치**다(`take()`가 `_apply_briefing` 뒤). 지금
새는 경로가 없는 이유는 **브리핑이 `LlmError` 전부를 삼켜 템플릿 폴백**하기 때문이다
(`briefing._fallback` · 99 22번) — 배치가 안전해서가 아니라 **위가 안 던져서** 안전하다.

⚠ **그래서 이 PR은 `detect.py`를 안 고쳤다.** 이유 둘:
① `persist_ledger`(fail-closed → 500)와 `record_calls`(fail-open)가 **다른 저장소**이고
   순서가 FK 제약이다(`llm_call.run_id` → `ai_run.execution_id` NOT NULL). `finally`로
   옮기면 500 의미론이 바뀐다.
② **동작 변화 0을 증명할 수 없는 변경은 넣지 않는다.**

⇒ 대신 **전제를 여기서 고정한다.** 이 전제가 깨지는 날(브리핑이 예외를 올리기 시작하면)
red가 나고, 그때는 워커와 같은 `finally` 전환이 필요하다. 99 ㉷.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from ai.composition.briefing import make_brief
from ai.composition.briefing_context import BriefingContext, EvidenceFact
from ai.contracts.detection import Lifecycle, SignalType
from ai.contracts.execution import Capability, ExecutionContext, VersionSet
from ai.contracts.llm import (
    LlmError,
    LLMRequest,
    LLMResult,
    LlmTimeout,
    LlmUnavailable,
    ParseFailed,
    RedactionBlocked,
)
from ai.db.repositories.run_store import default_llm_call_collector
from ai.detection.segments import Segment

#: 🔴 브리핑 위쪽이 낼 수 있는 것 전수 — 하나라도 라우터까지 올라오면 `detect.py:399`의
#: `take()`를 건너뛰어 수집기가 샌다.
_FAILURES = [
    LlmTimeout("t"),
    LlmUnavailable("u"),
    ParseFailed("p"),
    LlmError("4xx — 컨텍스트 한도"),
    RedactionBlocked("전송 전 차단"),
]


class _BoomProvider:
    """`LLMProvider` 대역 — 항상 터진다."""

    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    @property
    def name(self) -> str:
        return "boom"

    async def complete(self, request: LLMRequest, context: ExecutionContext) -> LLMResult:
        del request, context
        raise self._exc


@pytest.fixture(autouse=True)
def _isolate() -> Iterator[None]:
    default_llm_call_collector().reset()
    yield
    default_llm_call_collector().reset()


def _context() -> BriefingContext:
    return BriefingContext(
        signal_type=SignalType.ACC_DROP,
        display_label="정답률 하락",
        lifecycle=Lifecycle.NEW,
        segment=Segment.NORMAL,
        facts=(EvidenceFact(label="정답률", value="62%"),),
        evidence_summaries=("근거 기록",),
        fallback_text="이번 주 학습 상황을 정리해 보내드립니다.",
    )


def _execution_context() -> ExecutionContext:
    from uuid import UUID

    return ExecutionContext(
        execution_id=UUID("00000000-0000-4000-8000-00000000004a"),
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


@pytest.mark.parametrize(
    "exc", _FAILURES, ids=[type(e).__name__ for e in _FAILURES]
)
def test_briefing_never_raises_to_the_router(exc: Exception) -> None:
    """🔴 어떤 LLM 장애가 나도 **폴백으로 수렴**하고 예외가 위로 안 올라간다.

    이게 깨지면 `detect.py:399`의 `take()`를 건너뛰어 **수집기가 샌다** — 그때는 워커와
    같은 `finally` 전환이 필요하다(99 ㉷). 지금은 배치가 안전해서가 아니라 **위가 안
    던져서** 안전하다는 것이 요점이다.
    """
    import asyncio

    brief, outcome = asyncio.run(
        make_brief(
            _context(),
            _BoomProvider(exc),
            context=_execution_context(),
            now=lambda: 0.0,
            deadline=45.0,
        )
    )
    assert brief.fallback_used is True, (
        f"{type(exc).__name__}에서 폴백으로 수렴하지 않았다 — 예외가 라우터로 올라가면 "
        "detect.py:399의 take()를 건너뛰어 수집기가 샌다(워커와 같은 finally 전환 필요)"
    )
    assert brief.text == _context().fallback_text
    assert outcome, "outcome 라벨이 비었다 — 폴백 사유를 로그가 못 남긴다"


def test_the_detect_router_still_takes_after_briefing() -> None:
    """⚠ 이 테스트가 지키는 **전제의 위치**를 소스로 고정한다.

    `detect.py`의 `take()`가 `_apply_briefing` **뒤**에 있다는 사실 자체가 위 전제를
    필요하게 만든다. 배치가 바뀌면(예: finally로 옮기면) 이 단정이 red가 되고, 그때는
    위 전제 테스트의 존재 이유를 다시 읽어야 한다.
    """
    import inspect
    from pathlib import Path

    from ai.api.routers import detect

    source = Path(inspect.getfile(detect)).read_text(encoding="utf-8")
    assert "brief_calls = default_llm_call_collector().take(execution_id)" in source
    assert "finally:" not in source, (
        "detect.py에 finally가 생겼다 — 배치가 바뀌었으면 99 ㉷를 다시 판정하라"
    )
