"""브리핑 라우터 통합 — 문장화 실패해도 감지 판정 무변 (분기표 #6, HTTP 경로).

detect 라우터가 브리핑(ⓐ)을 붙인 뒤에도 신호·score·lifecycle·rank는 그대로여야 한다
(문장만 폴백). LLM 실패를 mock provider로 주입해 검증한다.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from ai.api.app import create_app
from ai.api.routers.detect import (
    _BRIEFING_CONCURRENCY,
    reset_brief_provider,
    reset_detection_store,
    reset_idempotency_store,
    set_brief_provider,
)
from ai.contracts.execution import ExecutionContext
from ai.contracts.llm import (
    CallOutcome,
    LLMRequest,
    LLMResult,
    LlmUnavailable,
    TokenUsage,
)
from ai.detection.engine import detect
from ai.evaluation.fake_snapshot import fixture_composite_risk, to_payload

_HEADERS = {
    "X-Tenant-Id": "teacher_alias_001",
    "X-Request-Id": "req-1",
    "Idempotency-Key": "teacher_alias_001:2026-07-13",
}


class _FailingProvider:
    @property
    def name(self) -> str:
        return "failing"

    async def complete(self, request: LLMRequest, context: ExecutionContext) -> LLMResult:
        raise LlmUnavailable("down")


@pytest.fixture
def client() -> Iterator[TestClient]:
    reset_idempotency_store()
    reset_detection_store()
    reset_brief_provider()
    yield TestClient(create_app())
    reset_idempotency_store()
    reset_detection_store()
    reset_brief_provider()


def _signals(body: dict[str, Any]) -> list[dict[str, Any]]:
    result = body["data"]["signals"]
    assert isinstance(result, list)
    return result


def test_llm_failure_keeps_judgment_only_brief_falls_back(client: TestClient) -> None:
    """LLM 실패 시: 200 + 문장은 폴백(fallback_used=True), 판정(score·lifecycle·rank)은 무변."""
    set_brief_provider(_FailingProvider())
    request = fixture_composite_risk()
    pure = detect(request)  # 순수 엔진 판정(브리핑 전)

    resp = client.post("/v1/detect", json=to_payload(request), headers=_HEADERS)
    assert resp.status_code == 200
    signals = _signals(resp.json())

    assert len(signals) == len(pure.signals)
    for api_signal, pure_signal in zip(signals, pure.signals, strict=True):
        # 판정 무변
        assert api_signal["score"] == pure_signal.score
        assert api_signal["lifecycle"] == pure_signal.lifecycle.value
        assert api_signal["rank"] == pure_signal.rank
        assert api_signal["signal_type"] == pure_signal.signal_type.value
        # 문장만 폴백(엔진 초안 재사용)
        assert api_signal["brief"]["fallback_used"] is True
        assert api_signal["brief"]["text"] == pure_signal.brief.text


class _ConcurrencyTracker:
    """동시 진입 수를 추적하는 provider — 세마포어 상한(동시 N) 검증용."""

    def __init__(self) -> None:
        self.active = 0
        self.peak = 0

    @property
    def name(self) -> str:
        return "concurrency"

    async def complete(self, request: LLMRequest, context: ExecutionContext) -> LLMResult:
        self.active += 1
        self.peak = max(self.peak, self.active)
        try:
            await asyncio.sleep(0.02)  # 겹치도록 잠깐 대기
            return LLMResult(
                outcome=CallOutcome.OK,
                text="정답률이 조금 흔들리고 있어요",  # 숫자·기호 없음 → 게이트 통과
                provider=self.name,
                model="m",
                usage=TokenUsage(tokens_in=0, tokens_out=0, cost_usd=0.0),
                latency_ms=0,
            )
        finally:
            self.active -= 1


def test_briefing_is_parallel_but_bounded(client: TestClient) -> None:
    """신호별 문장화는 병렬이되 동시 실행이 세마포어 상한을 넘지 않는다(팀 서버 배려)."""
    tracker = _ConcurrencyTracker()
    set_brief_provider(tracker)
    request = fixture_composite_risk()
    pure = detect(request)
    resp = client.post("/v1/detect", json=to_payload(request), headers=_HEADERS)
    signals = _signals(resp.json())

    assert len(signals) == len(pure.signals)
    assert tracker.peak <= _BRIEFING_CONCURRENCY, "동시 호출이 세마포어 상한 초과"
    if len(pure.signals) >= _BRIEFING_CONCURRENCY:
        assert tracker.peak == _BRIEFING_CONCURRENCY, "병렬화가 안 됨(순차 실행)"
    for api_sig in signals:
        assert api_sig["brief"]["fallback_used"] is False  # 게이트 통과


def test_default_fake_judgment_matches_pure_engine(client: TestClient) -> None:
    """기본 fake: 판정 필드는 순수 엔진과 정확 일치, brief는 결정론(기본 템플릿·비공백)."""
    request = fixture_composite_risk()
    pure = detect(request).model_dump(mode="json")
    resp = client.post("/v1/detect", json=to_payload(request), headers=_HEADERS)
    api = resp.json()["data"]
    exclude = {"signal_id", "brief"}
    for api_sig, pure_sig in zip(api["signals"], pure["signals"], strict=True):
        assert {k: v for k, v in api_sig.items() if k not in exclude} == {
            k: v for k, v in pure_sig.items() if k not in exclude
        }
        assert api_sig["brief"]["text"].strip()  # 결정론 fake — 비공백
    assert api["stats"] == pure["stats"]
