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

🔴 **(8/8 정정) 이 파일의 원래 형태가 틀렸다 — 로그 67.**

> *"예외를 안 올린다"를 특정 예외 목록으로 증명하면, 목록 밖 예외는 증명이 아니라 사각이다.*

아래 `_FAILURES`는 `LlmError` **5종 목록**이었고, 실제로 브리핑을 죽인 것은
**`ValidationError`** 였다 — 빈 응답이 `Brief(text="")`에 들어가 `min_length=1`에 걸렸다.
목록 밖이라 이 파일이 **8주간 못 잡았고**, `POST /v1/detect`가 500으로 죽는 상태가
*"잠갔다"* 고 적힌 채 남아 있었다(99 ㊝).

⇒ **목록을 늘리지 않고 형태를 바꿨다.** 아래 `test_no_response_shape_escapes_briefing`이
`outcome × text`의 **곱집합**(계약상 가능한 응답 공간)을 훑고, 단정이
`pytest.raises(...)`가 아니라 **"아무 예외도 안 나온다"** 다. `_FAILURES` 목록은
**대조군으로 남긴다** — 유효하되 **그 목록이 전부가 아니다.**

⚠ **「전수를 세라」가 항상 목록을 만들라는 뜻은 아니다** — 목록이 유한할 때만 그렇고,
아닐 때는 **전칭을 검사하는 형태**를 찾아야 한다. 다음 사람이 여기 예외를 하나 더
추가하고 싶어지면, 그건 목록이 부족하다는 신호이지 목록을 늘릴 이유가 아니다.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from typing import Final

import pytest

from ai.composition.briefing import make_brief
from ai.composition.briefing_context import BriefingContext, EvidenceFact
from ai.contracts.detection import Brief, Lifecycle, SignalType
from ai.contracts.execution import Capability, ExecutionContext, VersionSet
from ai.contracts.llm import (
    CallOutcome,
    LlmError,
    LLMRequest,
    LLMResult,
    LlmTimeout,
    LlmUnavailable,
    ParseFailed,
    RedactionBlocked,
    TokenUsage,
)
from ai.db.repositories.run_store import default_llm_call_collector
from ai.detection.segments import Segment

#: 브리핑 위쪽이 낼 수 있는 **예외** 목록 — 대조군이다.
#: ⚠ **이 목록이 전부가 아니다**(로그 67). 실제로 detect를 죽인 것은 여기 없는
#: `ValidationError`였다. 전칭 검사는 아래 곱집합 테스트가 한다.
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


# ── 🔴 목록이 아니라 전칭 (로그 67) ────────────────────────────────

#: 계약상 가능한 응답 공간 — `LLMResult`가 실을 수 있는 조합의 곱집합.
#: ⚠ 표본이 아니라 **공간을 훑는다**(#116의 redaction 커버리지 매트릭스와 같은 발상).
_TEXTS: Final = ("정답률이 낮아졌어요.", "", "   ", None)


class _ShapeProvider:
    """`LLMProvider` 대역 — 주어진 `(outcome, text)`를 **예외 없이** 그대로 낸다."""

    name = "shape"

    def __init__(self, outcome: CallOutcome, text: str | None) -> None:
        self._outcome = outcome
        self._text = text

    async def complete(self, request: LLMRequest, context: ExecutionContext) -> LLMResult:
        del request, context
        return LLMResult(
            outcome=self._outcome,
            text=self._text,
            provider=self.name,
            model="m",
            usage=TokenUsage(tokens_in=0, tokens_out=0, cost_usd=0.0),
            latency_ms=1,
        )


@pytest.mark.parametrize("outcome", list(CallOutcome), ids=lambda o: o.value)
@pytest.mark.parametrize("text", _TEXTS, ids=lambda t: repr(t))
def test_no_response_shape_escapes_briefing(
    outcome: CallOutcome, text: str | None
) -> None:
    """🔴 **어떤 응답 모양에서도 예외가 안 나온다** — 목록이 아니라 곱집합이다(로그 67).

    ⚠ 단정이 `pytest.raises`가 **아니다.** *"이 예외들은 안 나온다"* 가 아니라
    *"아무 예외도 안 나온다"* 를 본다 — 그 차이가 `ValidationError`를 8주간 놓친 이유다.

    ⚠ 브리핑은 **항상 `Brief`를 돌려준다**(폴백이든 아니든). 그 계약이 지켜지는지까지
    본다 — 예외만 안 나오고 `None`이 나오면 호출부가 다음 줄에서 죽는다.
    """
    brief, reason = asyncio.run(
        make_brief(
            _context(),
            _ShapeProvider(outcome, text),
            context=_execution_context(),
            now=lambda: 0.0,
            deadline=45.0,
        )
    )
    assert isinstance(brief, Brief), "브리핑이 Brief를 안 돌려줬다"
    assert brief.text, "빈 문장이 나왔다 — Brief.text의 min_length=1이 유일한 방어선이다"
    assert reason, "사유 라벨이 비었다 — 로그가 무엇이 일어났는지 못 남긴다"


def test_the_detect_route_survives_every_response_shape() -> None:
    """🔴 **종단** — 어떤 응답 모양에서도 `POST /v1/detect`가 200이다(분기표 ⑥).

    단위 테스트만으로는 부족하다 — `_apply_briefing`이 `make_brief`를 감싸지 않으므로
    거기서 새는 것이 있으면 **라우트가 죽는다.** 실제로 그랬다(99 ㊝ · 빈 응답 → 500).

    ⚠ 곱집합 전부를 HTTP로 돌리면 느리다 — **가장 위험한 셋**만 태운다(빈 문자열·공백·
    `None`). `outcome` 축은 위 단위 테스트가 전수로 본다.
    """
    import importlib.util

    from fastapi.testclient import TestClient

    from ai.api.app import create_app
    from ai.api.routers import detect as detect_router
    from ai.api.routers.counsel import set_counsel_provider
    from ai.composition.counsel.provider import FakeCounselProvider

    spec = importlib.util.spec_from_file_location(
        "t", "tests/ai/integration/test_idempotency_restart.py"
    )
    assert spec is not None and spec.loader is not None
    detect_test = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(detect_test)

    for text in ("", "   ", None):
        detect_router.reset_detection_store()
        detect_router.reset_idempotency_store()
        detect_router.set_brief_provider(_ShapeProvider(CallOutcome.OK, text))
        set_counsel_provider(FakeCounselProvider())
        with TestClient(create_app(), raise_server_exceptions=False) as client:
            response = client.post(
                "/v1/detect", json=detect_test._payload(), headers=detect_test._HEADERS
            )
        detect_router.reset_brief_provider()

        assert response.status_code == 200, (
            f"text={text!r}에서 detect가 {response.status_code}다 — 브리핑 한 건의 실패가 "
            "반 전체 감지를 죽인다(분기표 ⑥ · 99 ㊝)"
        )
        assert response.json()["data"]["signals"], "신호가 0건이라 브리핑 경로를 안 탔다"
