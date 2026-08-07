"""🔴 **빈 LLM 응답 하나가 `/v1/detect` 전체를 500으로 죽인다** (99 ㊝).

`briefing.py:147`이 빈 응답을 그대로 `Brief(text="")`에 넣는다. `Brief.text`는
`min_length=1`(`contracts/detection.py`)이라 **`ValidationError`** 가 나고,
`_apply_briefing`에 그걸 받는 `except`가 없다 ⇒ **`POST /v1/detect`가 HTTP 500.**
신호 7건 중 **1건만 비어도 반 전체 감지가 실패**한다.

⚠ **게이트가 못 막는다** — 실측: `check_brief_gate("")` → **passed=True**. 게이트는
*"무엇이 있으면 안 되는가"*(금칙어·미허용 숫자·`⟪⟫`·길이 상한)만 보고 *"무엇이 있어야
하는가"* 는 안 본다. 유일하게 걸린 것이 `Brief.text`의 `min_length=1`이었고, 그 계약이
없었다면 **빈 브리핑이 BE·화면까지 조용히 나갔을 것**이다.

🔴 **실제로 겪은 조건이다** — `max_completion_tokens` 문제로 빈 응답 폴백이 있었다(99 ⓟ).
그리고 실 LLM 4차가 브리핑을 실 LLM으로 돌린다 — **하나만 나와도 S1이 통째로 날아간다.**

⚠ **이건 #120에서 "잠갔다"고 적은 전제다.** `test_briefing_never_raises`가 `LlmError`
5종 **목록**이라 `ValidationError`가 목록 밖이었다(로그 67 · ㉷ 전제 정정).
"""

from __future__ import annotations

import asyncio
import importlib.util
from collections.abc import Coroutine, Iterator
from typing import Any
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from ai.api.app import create_app
from ai.api.routers import detect as detect_router
from ai.api.routers.counsel import reset_counsel_stores, set_counsel_provider
from ai.composition.briefing import make_brief
from ai.composition.briefing_context import BriefingContext, EvidenceFact
from ai.composition.briefing_gate import check_brief_gate
from ai.composition.counsel.provider import FakeCounselProvider
from ai.contracts.detection import Lifecycle, SignalType
from ai.contracts.execution import Capability, ExecutionContext, VersionSet
from ai.contracts.llm import CallOutcome, LLMRequest, LLMResult, TokenUsage
from ai.db.store_factory import reset_shared_agent_runtime
from ai.detection.segments import Segment


def _run[T](coro: Coroutine[object, object, T]) -> T:
    return asyncio.run(coro)


class _Provider:
    """`LLMProvider` 대역 — 계약상 가능한 응답을 그대로 낸다(예외 아님)."""

    name = "fake-empty"

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
    return ExecutionContext(
        execution_id=UUID("00000000-0000-4000-8000-0000000000b1"),
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


def _golden(path: str) -> Any:  # noqa: ANN401 — 통합 테스트 픽스처 재사용
    spec = importlib.util.spec_from_file_location(path.replace("/", "."), path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(autouse=True)
def _isolate() -> Iterator[None]:
    detect_router.reset_detection_store()
    detect_router.reset_idempotency_store()
    detect_router.reset_brief_provider()
    reset_shared_agent_runtime()  # A·B 공용 잡 원장(99 ㊒)
    reset_counsel_stores()
    yield
    detect_router.reset_brief_provider()
    reset_shared_agent_runtime()
    reset_counsel_stores()


# ── 🔴 종단: 빈 응답 하나가 detect를 죽인다 ────────────────────────


def test_an_empty_briefing_does_not_kill_the_whole_detect_request() -> None:
    """🔴 **이 PR의 근거** — 빈 LLM 응답에서 `POST /v1/detect`가 **200**이어야 한다.

    분기표 ⑥이 *"어떤 실패든 감지 판정 무변"* 이라고 정했다. 브리핑은 **부가**이고,
    브리핑이 죽었다고 감지가 죽으면 안 된다 — 지금은 죽는다(실측 500).

    ⚠ 신호 수까지 본다 — 200만 보면 *"신호가 0건이라 브리핑을 안 탔다"* 로도 통과한다.
    """
    detect_test = _golden("tests/ai/integration/test_idempotency_restart.py")
    set_counsel_provider(FakeCounselProvider())  # counsel 기동 가드
    detect_router.set_brief_provider(_Provider(CallOutcome.OK, ""))

    with TestClient(create_app(), raise_server_exceptions=False) as client:
        response = client.post(
            "/v1/detect", json=detect_test._payload(), headers=detect_test._HEADERS
        )

    assert response.status_code == 200, (
        f"빈 LLM 응답에서 detect가 {response.status_code}다 — 브리핑 한 건의 실패가 "
        "반 전체 감지를 죽인다(분기표 ⑥ '어떤 실패든 감지 판정 무변' 위반 · 99 ㊝)"
    )
    signals = response.json()["data"]["signals"]
    assert signals, "신호가 0건이라 브리핑 경로를 안 탔다 — 이 테스트의 전제가 깨졌다"
    for signal in signals:
        assert signal["brief"]["fallback_used"] is True, (
            "빈 응답인데 폴백이 아니다 — 빈 문장이 그대로 나갔다"
        )


# ── 단위: 두 축 ────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("label", "outcome", "text"),
    [
        ("② 빈 응답", CallOutcome.OK, ""),
        ("②′ 공백만", CallOutcome.OK, "   "),
        ("②″ None", CallOutcome.OK, None),
    ],
)
def test_an_empty_response_falls_back_instead_of_raising(
    label: str, outcome: CallOutcome, text: str | None
) -> None:
    """🔴 빈 응답은 **템플릿 폴백**으로 수렴한다 — 예외로 나가지 않는다.

    ⚠ classify와 처방이 다르다. classify는 `raise`가 맞았지만(라우터가 5xx로 변환),
    briefing은 **밖으로 내보내면 안 된다** — 분기표 ⑥이 감지 판정 무변을 정했고,
    브리핑 실패가 detect를 죽이는 것이 지금 결함 자체다.
    """
    brief, reason = _run(
        make_brief(
            _context(),
            _Provider(outcome, text),
            context=_execution_context(),
            now=lambda: 0.0,
            deadline=45.0,
        )
    )
    assert brief.fallback_used is True, f"{label}: 폴백으로 수렴하지 않았다"
    assert brief.text == _context().fallback_text
    assert reason == "llm_failed", (
        f"{label}: 사유가 {reason!r}다 — 장애는 `llm_failed`다. `gate_exhausted:*`로 나가면 "
        "벤더가 죽은 것이 문장 품질 문제로 기록된다(counsel/provider.py가 경고한 오분류)"
    )


def test_a_failed_outcome_never_looks_like_a_normal_brief() -> None:
    """🔴 `outcome≠OK`인데 본문이 멀쩡하면 종전에는 **정상 브리핑으로 나갔다**.

    실측(8/8 · 고치기 전): `fallback_used=False` · 라벨 `'timeout'`.
    장애가 은폐된다 — classify ①a와 같은 형태다(99 G).
    """
    brief, reason = _run(
        make_brief(
            _context(),
            _Provider(CallOutcome.TIMEOUT, "정답률이 낮아졌어요."),
            context=_execution_context(),
            now=lambda: 0.0,
            deadline=45.0,
        )
    )
    assert brief.fallback_used is True, (
        "outcome=TIMEOUT인데 정상 브리핑으로 나갔다 — 장애가 은폐된다"
    )
    assert reason == "llm_failed"


# ── 게이트는 못 막는다(전/후 무변경 · 판정 근거) ───────────────────


@pytest.mark.parametrize("text", ["", "   "])
def test_the_brief_gate_does_not_catch_emptiness(text: str) -> None:
    """⚠ **게이트는 빈 문자열을 통과시킨다** — 그게 이 결함이 여기까지 온 이유다.

    게이트는 *"무엇이 있으면 안 되는가"* 만 본다. 🔴 **이 PR은 게이트를 고치지 않았다** —
    `check_brief_gate`는 왜곡 검사이고 *"비었는가"* 가 그 책임인지 애매하며, 고치면
    counsel·문항 게이트까지 함께 봐야 한다(99 ㊠에 선택지로 올렸다).

    ⚠ 대신 **`check_counsel_gate`는 빈 문자열을 `empty`로 막는다**(실측) — 게이트 계열이
    이미 갈려 있다는 사실을 여기 남긴다. 이 단정이 red가 되면 누군가 브리핑 게이트에
    빈 검사를 넣은 것이고, 그때는 ㊠를 닫으면 된다.
    """
    assert check_brief_gate(text, frozenset()).passed is True


def test_the_counsel_gate_does_catch_emptiness() -> None:
    """⚠ 위 테스트의 짝 — **같은 계열인데 판정이 다르다**(비대칭의 증거).

    ⚠ 이 파일이 counsel 게이트를 보는 이유는 *"고쳐야 하는가"* 의 근거이기 때문이다 —
    선례가 이미 `empty`를 사유로 갖고 있다.
    """
    from ai.composition.counsel.gate import check_counsel_gate
    from ai.contracts.composition import (
        CommStyle,
        DraftContext,
        Frequency,
        Interest,
        LabelSnapshot,
        Sensitivity,
    )
    from ai.contracts.composition import (
        EvidenceFact as CFact,
    )

    context = DraftContext(
        student_ref="st_1",
        guardian_ref="gd_1",
        label_snapshot=LabelSnapshot(
            comm=CommStyle.DATA,
            sensitivity=Sensitivity.ANXIOUS,
            interest=Interest.GRADE,
            frequency=Frequency.FREQUENT,
        ),
        facts=(CFact(label="정답률", value="62%", record_id="le_1"),),
        evidence_summaries=(),
        period_label="2026년 8월",
        fallback_text="f",
    )
    result = check_counsel_gate("", context=context, max_chars=360)
    assert result.passed is False
    assert result.reason == "empty"
