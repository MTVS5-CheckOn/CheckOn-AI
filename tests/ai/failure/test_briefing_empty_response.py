"""🔴 **빈 LLM 응답이 `/v1/detect` 전체를 500으로 죽일 수 있었다** — 대역 조건 (99 ㊝).

`briefing.py:147`이 빈 응답을 그대로 `Brief(text="")`에 넣는다. `Brief.text`는
`min_length=1`(`contracts/detection.py`)이라 **`ValidationError`** 가 나고,
`_apply_briefing`에 그걸 받는 `except`가 없다 ⇒ **`POST /v1/detect`가 HTTP 500.**

🔴 **(8/8 정정) 그 500은 대역(`_ShapeProvider`)에서만 난다.** 실 provider 둘 다 이 조건을
만들지 않는다 — `openai_compat.py:224`가 빈·공백 응답을 **`ParseFailed`로 올리고** 그건
`LlmError` 하위라 **수정 전에도** `except LlmError`가 잡아 `llm_failed` 폴백이었다.
⇒ **방어가 아니라 우연이 지켜 줬다.** 브리핑에 검사가 없다는 사실은 그대로이고, 그걸 밟는
provider가 없었을 뿐이다. **구조는 여전히 맞다** — `_apply_briefing`이 `make_brief`를
감싸지 않으므로 **거기서 새는 예외가 하나라도 있으면 반 전체가 죽는다.**

⚠ **게이트가 못 막는다** — 실측: `check_brief_gate("")` → **passed=True**. 게이트는
*"무엇이 있으면 안 되는가"*(금칙어·미허용 숫자·`⟪⟫`·길이 상한)만 보고 *"무엇이 있어야
하는가"* 는 안 본다. 유일하게 걸린 것이 `Brief.text`의 `min_length=1`이었고, 그 계약이
없었다면 **빈 브리핑이 BE·화면까지 조용히 나갔을 것**이다.

⚠ **겪은 사고의 재발 방지가 아니다(8/8 정정).** 이 저장소에 *"브리핑이 빈 응답을 받았다"* 는
실측 기록이 **없다** — 종전에 근거로 든 99 ⓟ는 *env 접두 교체* 항목이고, 토큰 건(ⓤ)의 실제
사건은 **400 거부**였으며, *"0자 = 빈 응답"* 해석은 ㊼가 **게이트 소진**으로 되돌렸다.
⇒ 이 파일이 지키는 것은 **계약을 안 지키는 provider에 대한 방어**다.

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
    """`LLMProvider` 대역 — 계약상 가능한 응답을 그대로 낸다(예외 아님).

    ⚠ **이 대역은 실 provider가 하지 않는 것을 한다** — `openai_compat`은 실패를 전부
    **예외로 올리고 `outcome=OK`만 반환**한다(99 ㉴). 여기서 non-OK·빈 본문을 반환하는 것은
    **방어 검증용이지 재현이 아니다.** 🔴 이 대역으로 얻은 실측을 *"프로덕션에서 이렇게
    된다"* 로 옮겨 적으면 안 된다 — 8/8에 실제로 그렇게 적었다가 되돌렸다(99 ㊝ · 로그 69).
    """

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
    브리핑이 죽었다고 감지가 죽으면 안 된다 — 대역에서는 죽었다(실측 500 · 8/8).

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
def test_the_brief_gate_now_catches_emptiness(text: str) -> None:
    """✅ **㊠ ① 닫힘(8/8)** — 게이트가 빈 문자열을 `empty`로 막는다.

    ⚠ **이 테스트는 뒤집힌 것이다.** #131에서는 `passed is True`를 단정하며
    *"이 단정이 red가 되면 누군가 브리핑 게이트에 빈 검사를 넣은 것이고, 그때는 ㊠를
    닫으면 된다"* 고 적어 뒀다 — 그 red가 났고, 그래서 닫았다.

    🔴 **두 방어선이 겹치는 것이 정상이다.** ㊝이 provider 경계에서 이미 막았고 이건
    두 번째다. 어느 하나를 뺄 이유를 만들지 마라 — 프로덕션 briefing은 첫 번째가,
    대역 측정(㊢ · `briefing_preview`)은 두 번째가 지킨다.
    """
    result = check_brief_gate(text, frozenset())
    assert result.passed is False
    assert result.reason == "empty", (
        f"사유가 {result.reason!r}다 — counsel과 같은 `empty`여야 한다(새 어휘 금지)"
    )


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


# ── ㊠ ② 폴백 경로 — 마지막 미방어 문 ──────────────────────────────


@pytest.mark.parametrize("text", ["", "   "])
def test_an_empty_fallback_text_is_rejected_at_construction(text: str) -> None:
    """🔴 **폴백이 비면 폴백 자체가 예외가 된다** — 생성 시점에 막는다(㊠ ②).

    `_fallback()`이 `Brief(text=ctx.fallback_text)`를 만드는데 `Brief.text`는
    `min_length=1`이다. 비면 `ValidationError`가 나고, 그게 하필 **`except LlmError`
    핸들러 안**이라 ㊝과 **똑같이** `/v1/detect`까지 올라간다.

    ⚠ **지금까지 안전했던 것은 계약이 아니라 우연이다** — `briefing_context.py:225·239`가
    `signal.brief.text`에서 채우고 그 값이 이미 `min_length=1`을 통과했기 때문에
    **전이적으로** 비지 않았다. 이 검사가 그 전이 관계를 **계약으로** 바꾼다.
    """
    with pytest.raises(ValueError, match="fallback_text"):
        BriefingContext(
            signal_type=SignalType.ACC_DROP,
            display_label="정답률 하락",
            lifecycle=Lifecycle.NEW,
            segment=Segment.NORMAL,
            facts=(),
            evidence_summaries=(),
            fallback_text=text,
        )


def test_the_fallback_path_still_works_with_a_normal_text() -> None:
    """✅ **대조군** — 정상 폴백은 그대로 산다(프로덕션 경로 무변경 증명).

    ⚠ 기존 테스트 2174건이 하나도 안 깨진 것이 더 강한 증명이지만, 그건 이 파일에
    안 남으므로 여기 대조군을 둔다.
    """
    brief, reason = _run(
        make_brief(
            _context(),
            _Provider(CallOutcome.OK, ""),
            context=_execution_context(),
            now=lambda: 0.0,
            deadline=45.0,
        )
    )
    assert brief.fallback_used is True
    assert brief.text == _context().fallback_text
    assert reason == "llm_failed"
