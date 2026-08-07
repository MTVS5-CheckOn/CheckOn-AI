"""🔴 classify에서 **장애가 판단으로 둔갑하지 않는다** (99 G).

`classifier.py`가 `gateway.complete()`의 결과에서 **`outcome`을 안 보고** 곧장
`parse(result.text or "")`를 부른다. 그래서:

    ①a outcome≠OK · 본문 있음   → 🔴 **classified=True** (장애가 **성공**으로 둔갑)
    ①b outcome≠OK · 본문 없음   → parse_exhausted · **호출 3회**
    ②  OK · 빈 응답             → parse_exhausted · **호출 3회**

🔴 **선례가 같은 레포에 있다.** `counsel/provider.py`가 정확히 이 자리를 이미 막아 뒀다:

> *"outcome≠OK를 빈 문자열로 삼키면 장애가 게이트 실패(`gate_exhausted:empty`)로
> **오분류**된다 — 그러면 서킷 카운터도 안 오르고 알럿이 뜨지 않는다."*

classify만 빠졌다. 여덟 번째다(#108·#111·#114·#116·#117·#119·#120·여기).

⚠ **`parse_exhausted`가 왜 나쁜 라벨인가.** `error_codes` §6이 그 사유를 *"LLM 출력이 enum
강제 스키마를 못 채웠다"* 로 등재했고 BE는 **정렬 미적용**으로 넘어간다 — **벤더가 죽은
것을 모델 품질 문제로 기록한다.** 그리고 그 위에 파싱 재시도가 2회 더 돈다(비용 3배).

🔴 **이게 실 LLM 4차의 선행이다** — 4차에서 장애가 나면 `parse_exhausted`로 기록되어
㉡ 재측정이 **거짓 데이터**를 낸다.

⚠ **폴백 2종은 판단이라 그대로 둔다** — `tripwire_blocked`·`parse_exhausted`는 200으로
나가는 게 맞다(`error_codes` §6 · 서두 원칙 *"AI가 안 하기로 판단한 것은 정상 상태"*).
이 파일이 가르는 것은 **장애가 그 둘로 둔갑하는 것**뿐이고, 아래 대조군이 그 증명이다.
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import Final
from uuid import UUID

import pytest

from ai.composition.classify.classifier import MAX_PARSE_RETRY, classify
from ai.contracts.classify import ClassifyRequest
from ai.contracts.execution import Capability, ExecutionContext, VersionSet
from ai.contracts.llm import (
    CallOutcome,
    LlmError,
    LLMRequest,
    LLMResult,
    RedactionBlocked,
    TokenUsage,
)

#: 파서가 먹는 정상 출력 — 대역이 "본문은 멀쩡한데 outcome만 실패"를 낼 수 있어야 한다.
_OK_JSON: Final = (
    '{"topic":"grade","sentiment":"normal","urgency":"normal",'
    '"confidence":{"topic":0.9,"sentiment":0.9,"urgency":0.9}}'
)


def _run[T](coro: Coroutine[object, object, T]) -> T:
    return asyncio.run(coro)


class _Gateway:
    """`complete(request, context)` 동형 대역 — 호출 수를 센다."""

    def __init__(self, outcome: CallOutcome, text: str | None) -> None:
        self._outcome = outcome
        self._text = text
        self.calls = 0

    async def complete(self, request: LLMRequest, context: ExecutionContext) -> LLMResult:
        del request, context
        self.calls += 1
        return LLMResult(
            outcome=self._outcome,
            text=self._text,
            provider="fake",
            model="m",
            usage=TokenUsage(tokens_in=0, tokens_out=0, cost_usd=0.0),
            latency_ms=1,
        )


class _BlockedGateway:
    """전송 직전 트립와이어 차단 — 폴백 유지가 맞는 쪽(대조군)."""

    async def complete(self, request: LLMRequest, context: ExecutionContext) -> LLMResult:
        del request, context
        raise RedactionBlocked("가려지지 않은 게 남았다")


def _context() -> ExecutionContext:
    return ExecutionContext(
        execution_id=UUID("00000000-0000-4000-8000-0000000000c1"),
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


def _request() -> ClassifyRequest:
    return ClassifyRequest(inquiry_ref="iq_1", body_text="아이가 요즘 힘들어하는 것 같아요")


# ── 장애는 올라간다 ────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("label", "outcome", "text"),
    [
        ("outcome=TIMEOUT · 본문 있음", CallOutcome.TIMEOUT, _OK_JSON),
        ("outcome=PROVIDER_ERROR", CallOutcome.PROVIDER_ERROR, ""),
        ("OK · 빈 응답", CallOutcome.OK, ""),
        ("OK · None", CallOutcome.OK, None),
        ("OK · 공백만", CallOutcome.OK, "   "),
    ],
)
def test_llm_failure_is_raised_not_folded_into_a_judgement(
    label: str, outcome: CallOutcome, text: str | None
) -> None:
    """🔴 장애는 `LlmError`로 **올라간다** — `classified=false`로 수렴하지 않는다.

    `classifier.py`의 모듈 주석이 이미 판정해 뒀다: *"`LlmUnavailable`·`LlmTimeout`은
    여기서 삼키지 않는다 — 장애는 폴백이 아니라 503이다"*. `outcome≠OK`와 빈 응답도
    **같은 장애**이므로 같은 처리다.

    ⚠ `/v1/classify`는 #119에서 `domain_error_for` 변환 경계를 이미 붙였다 — 올리면
    503/504/500으로 정확히 나간다. 여기서 삼키면 그 경계가 **받을 것이 없어진다**.
    """
    gateway = _Gateway(outcome, text)
    with pytest.raises(LlmError):
        _run(classify(_request(), gateway, context=_context()))
    assert gateway.calls == 1, (
        f"{label}: 장애인데 {gateway.calls}회 불렀다 — 장애는 같은 프롬프트를 다시 보내도 "
        "안 풀린다. 재시도는 파싱 실패 전용이다"
    )


def test_a_failed_call_never_produces_a_confident_classification() -> None:
    """🔴 **가장 나쁜 경우** — `outcome≠OK`인데 본문이 멀쩡하면 종전에는 `classified=True`였다.

    실측(8/8 · 고치기 전): `outcome=TIMEOUT` + 정상 JSON → `classified=True` · 호출 1회.
    *"장애가 `parse_exhausted`로 둔갑한다"* 보다 한 단계 나쁘다 — **성공으로** 둔갑했다.
    부분 응답·캐시된 본문이 실려 오면 **확신 있는 분류가 원장에 남는다.**
    """
    gateway = _Gateway(CallOutcome.TIMEOUT, _OK_JSON)
    with pytest.raises(LlmError):
        _run(classify(_request(), gateway, context=_context()))


# ── 대조군: 폴백 2종은 그대로다 ────────────────────────────────────


def test_a_real_parse_failure_still_falls_back_after_retries() -> None:
    """✅ **바뀌면 안 되는 것** — 진짜 파싱 실패는 재시도 후 `parse_exhausted`로 수렴한다.

    이 PR이 폴백 2종을 안 건드렸다는 증명이 여기 있다. `error_codes` §6이 이 사유를
    *"LLM 출력이 enum 강제 스키마를 못 채웠다"* 로 등재했고 200으로 나가는 게 맞다.
    """
    gateway = _Gateway(CallOutcome.OK, "이건 JSON이 아니다")
    result = _run(classify(_request(), gateway, context=_context()))

    assert result.classified is False
    assert result.fallback_reason == "parse_exhausted"
    assert gateway.calls == MAX_PARSE_RETRY + 1, (
        f"파싱 실패의 재시도 예산이 바뀌었다: {gateway.calls}회 "
        f"(기대 {MAX_PARSE_RETRY + 1} = 최초 1 + 재시도 {MAX_PARSE_RETRY})"
    )


def test_tripwire_block_still_falls_back() -> None:
    """✅ **바뀌면 안 되는 것** — 트립와이어 차단은 `tripwire_blocked` 폴백 그대로.

    ⚠ `RedactionBlocked`는 `LlmError`의 **하위형**이다. 새로 던지는 plain `LlmError`가
    그 `except` 절에 잡히면 차단이 장애로 둔갑한다 — 반대 방향의 오분류다.
    하위형이 더 좁으므로 안 잡히지만, 그 사실을 여기서 못 박는다.
    """
    result = _run(classify(_request(), _BlockedGateway(), context=_context()))

    assert result.classified is False
    assert result.fallback_reason == "tripwire_blocked"


def test_redaction_blocked_is_a_subclass_of_llm_error() -> None:
    """⚠ 위 테스트가 지키는 **계층 관계**를 명시한다 — `except` 절 순서가 곧 계약이다.

    `RedactionBlocked ⊂ LlmError`이므로 `except RedactionBlocked`가 **먼저** 와야 한다.
    순서가 뒤집히면 차단이 `LlmError`로 잡혀 503이 된다(`briefing.py`가 같은 이유로
    같은 경고를 달아 뒀다).
    """
    assert issubclass(RedactionBlocked, LlmError)
    assert not issubclass(LlmError, RedactionBlocked)


# ── HTTP 층: 변환 경계가 실제로 이 경로에서 도는가 ─────────────────


def test_http_layer_turns_the_failure_into_a_gateway_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """🔴 올린 장애가 `/v1/classify`에서 **5xx로 나가고 AI_RUN이 남는다**.

    #119가 이 라우터에 `domain_error_for` 변환 경계와 `finally` 원장을 붙였다 — 이 PR이
    던지기 시작한 예외가 **그 경계에 실제로 닿는지**를 본다. 닿지 않으면 이 PR은
    *"예외를 만들었는데 아무도 안 받는"* 상태다.

    ⚠ **주입 seam이 없다** — 라우터가 `build_classify_gateway()`를 직접 부른다. 그래서
    그 팩토리를 monkeypatch한다(라우터에 seam을 새로 뚫는 것은 이 PR의 범위 밖이다).

    ⚠ **`outcome≠OK`는 plain `LlmError`라 500이다**(`error_codes` §4 매핑표) — counsel과
    대칭을 맞춘 결과다. 여기서는 `LlmTimeout`(504)으로 경계 자체가 도는지를 본다.
    `outcome=TIMEOUT`이 504가 아니라 500이 되는 것은 **실무상 도달하지 않지만**(99 ㉴ —
    게이트웨이가 타입 예외로 re-raise한다) 도달하게 되면 다시 판정해야 한다.
    """
    import importlib.util

    from fastapi.testclient import TestClient

    from ai.api.app import create_app
    from ai.api.routers import classify as classify_router
    from ai.api.routers.counsel import set_counsel_provider
    from ai.composition.counsel.provider import FakeCounselProvider
    from ai.contracts.llm import LlmTimeout
    from ai.db.repositories.run_store import InMemoryRunStore

    spec = importlib.util.spec_from_file_location(
        "tr", "tests/ai/integration/test_classify_router.py"
    )
    assert spec is not None and spec.loader is not None
    router_test = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(router_test)

    class _TimeoutGateway:
        async def complete(
            self, request: LLMRequest, context: ExecutionContext
        ) -> LLMResult:
            del request, context
            raise LlmTimeout("업스트림 무응답")

    store = InMemoryRunStore()
    classify_router.reset_inquiry_class_store()
    classify_router.set_classify_run_store(store)
    monkeypatch.setattr(classify_router, "build_classify_gateway", _TimeoutGateway)
    set_counsel_provider(FakeCounselProvider())  # counsel 기동 가드

    with TestClient(create_app(), raise_server_exceptions=False) as client:
        response = client.post(
            "/v1/classify", json=router_test._REQUEST, headers=router_test._HEADERS
        )

    assert response.status_code == 504, (
        f"LlmTimeout이 {response.status_code}로 나갔다 — #119의 변환 경계가 이 경로에서 "
        "안 도는 것이다(error_codes §4: LlmTimeout → 504 TIMEOUT)"
    )
    assert response.json()["error"]["code"] == "TIMEOUT"
    #: #119의 `finally` 원장 — 장애 턴도 AI_RUN이 남는다(불변식 8).
    assert store.runs, "장애 응답에서 AI_RUN이 안 남았다 — #119의 finally가 이 경로에 없다"
