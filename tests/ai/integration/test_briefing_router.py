"""브리핑 라우터 통합 — 문장화 실패해도 감지 판정 무변 (분기표 #6, HTTP 경로).

detect 라우터가 브리핑(ⓐ)을 붙인 뒤에도 신호·score·lifecycle·rank는 그대로여야 한다
(문장만 폴백). LLM 실패를 mock provider로 주입해 검증한다.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from ai.api.app import create_app
from ai.api.routers.detect import (
    reset_brief_provider,
    reset_detection_store,
    reset_idempotency_store,
    set_brief_provider,
)
from ai.contracts.execution import ExecutionContext
from ai.contracts.llm import LLMRequest, LLMResult, LlmUnavailable
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


def test_default_fake_matches_pure_engine(client: TestClient) -> None:
    """기본 fake: brief까지 순수 엔진과 동일(데모·골든 무변경의 근거)."""
    request = fixture_composite_risk()
    pure = detect(request).model_dump(mode="json")
    resp = client.post("/v1/detect", json=to_payload(request), headers=_HEADERS)
    assert resp.json()["data"] == pure
