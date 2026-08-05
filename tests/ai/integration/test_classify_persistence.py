"""분류 평가셋 루프 — 적재 · 캐시 · 확정 회신 (P2-c · 99 ⓑ).

🔴 **이 파일의 핵심은 `test_evaluation_loop_closes_from_contract_only`다.**
계약만으로 `classify → confirmations → 평가셋`이 닫히는지 본다. **private 심볼을 일절
import하지 않는다** — P0에서 골든 테스트가 내부 `_drafts` dict를 뒤져 키를 얻는 바람에
"BE는 refine을 호출할 수 없다"가 안 보였던 것이 교훈이다.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from ai.api.app import create_app
from ai.api.routers import classify as classify_router
from ai.api.routers import confirmations as confirmations_router
from ai.composition.classify.provider import build_classify_gateway
from ai.contracts.execution import ExecutionContext
from ai.contracts.llm import CallOutcome, LLMRequest, LLMResult, TokenUsage
from ai.db.repositories.inquiry_class_store import InMemoryInquiryClassStore

_HEADERS = {"X-Tenant-Id": "t1", "X-Request-Id": "rq-1"}
_BODY: dict[str, Any] = {
    "inquiry_ref": "iq_204",
    "body_text": "여름방학 특강 시간표가 궁금합니다",
}
_OK = (
    '{"topic": "schedule", "sentiment": "normal", "urgency": "normal",'
    ' "confidence": {"topic": 0.9, "sentiment": 0.8, "urgency": 0.7}}'
)


class _CountingProvider:
    """LLM 호출 수를 센다 — 캐시가 실제로 호출을 막는지 보려면 이게 필요하다."""

    def __init__(self, text: str = _OK) -> None:
        self.calls = 0
        self._text = text

    @property
    def name(self) -> str:
        return "counting"

    async def complete(
        self, request: LLMRequest, context: ExecutionContext
    ) -> LLMResult:
        del request, context
        self.calls += 1
        return LLMResult(
            outcome=CallOutcome.OK,
            text=self._text,
            provider=self.name,
            model="counting",
            usage=TokenUsage(tokens_in=0, tokens_out=0, cost_usd=0.0),
            latency_ms=0,
        )


@pytest.fixture
def store() -> Iterator[InMemoryInquiryClassStore]:
    """두 라우터가 **같은 저장소**를 봐야 루프가 닫힌다."""
    shared = InMemoryInquiryClassStore()
    classify_router.set_inquiry_class_store(shared)
    confirmations_router.set_inquiry_class_store(shared)
    yield shared
    classify_router.reset_inquiry_class_store()
    confirmations_router.reset_inquiry_class_store()


def _patch_gateway(
    monkeypatch: pytest.MonkeyPatch, counting: _CountingProvider
) -> None:
    """라우터가 쓰는 게이트웨이 빌더를 카운팅 provider로 바꾼다."""
    monkeypatch.setattr(
        classify_router,
        "build_classify_gateway",
        lambda: build_classify_gateway(counting),
    )


@pytest.fixture
def provider(monkeypatch: pytest.MonkeyPatch) -> _CountingProvider:
    counting = _CountingProvider()
    _patch_gateway(monkeypatch, counting)
    return counting


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(create_app()) as test_client:
        yield test_client


def _classify(client: TestClient, **overrides: object) -> dict[str, Any]:
    body = {**_BODY, **overrides}
    response = client.post("/v1/classify", json=body, headers=_HEADERS)
    assert response.status_code == 200, response.text
    data: dict[str, Any] = response.json()["data"]
    return data


def _confirm(client: TestClient, payload: dict[str, Any]) -> httpx.Response:
    response: httpx.Response = client.post(
        "/v1/confirmations", json=payload, headers=_HEADERS
    )
    return response


# ── 🔴 D-1 평가셋 루프 종단 (계약만으로) ────────────────────────


def test_evaluation_loop_closes_from_contract_only(
    client: TestClient, store: InMemoryInquiryClassStore, provider: _CountingProvider
) -> None:
    """🔴 **계약만으로 `classify → confirmations`가 닫힌다** — 이 PR의 존재 이유다.

    응답의 `inquiry_ref`만 들고 확정 회신을 보낼 수 있어야 한다. private 심볼을
    import하지 않는다 — BE가 할 수 있는 것만 한다(P0의 교훈).

    확인하는 것: **예측이 살아남고**(평가셋 3요소) 정정은 별도 축에 남으며, 바꾸지 않은
    축은 NULL로 남는다.
    """
    result = _classify(client)
    reference = result["inquiry_ref"]  # ← 응답이 준 값만으로 다음 호출을 만든다

    response = _confirm(
        client,
        {
            "kind": "classification",
            "suggestion_id": reference,
            "action": "corrected",
            "corrected_value": {"topic": "grade"},
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"]["accepted"] is True

    row = asyncio.run(store.get(tenant_id="t1", inquiry_ref=reference))
    assert row is not None
    assert row.topic == "schedule"  # 🔴 예측 보존 — 덮이지 않았다
    assert row.corrected_topic == "grade"  # 정답은 별도 축
    assert row.corrected_sentiment is None  # 안 바꾼 축은 NULL
    assert row.corrected_urgency is None
    assert row.reviewed_at is not None


# ── 🔴 D-2 기록 규약 ① — 같은 값 정정은 정정이 아니다 ──────────


def test_same_value_correction_stays_null(
    client: TestClient, store: InMemoryInquiryClassStore, provider: _CountingProvider
) -> None:
    """🔴 예측과 **같은 값**으로 정정하면 `corrected_*`는 NULL을 유지한다.

    이게 깨지면 강사가 확인만 하고 지나간 건이 오답으로 집계돼 **재분류율이 부풀려지고**,
    P2-b에서 `reviewed_at`으로 "검토함"과 "정정함"을 분리한 의미가 통째로 무너진다.
    """
    result = _classify(client)

    response = _confirm(
        client,
        {
            "kind": "classification",
            "suggestion_id": result["inquiry_ref"],
            "action": "corrected",
            "corrected_value": {"topic": result["topic"]},  # 같은 값
        },
    )
    assert response.status_code == 200

    row = asyncio.run(store.get(tenant_id="t1", inquiry_ref=result["inquiry_ref"]))
    assert row is not None
    assert row.corrected_topic is None, "같은 값을 정정으로 셌다 — 재분류율이 부풀려진다"
    assert row.reviewed_at is not None, "검토 시각은 남아야 한다"


def test_confirmed_records_review_without_correction(
    client: TestClient, store: InMemoryInquiryClassStore, provider: _CountingProvider
) -> None:
    """`confirmed` — 검토 시각만 남고 정정 축은 전부 NULL(= AI 정답)."""
    result = _classify(client)
    assert (
        _confirm(
            client,
            {
                "kind": "classification",
                "suggestion_id": result["inquiry_ref"],
                "action": "confirmed",
            },
        ).status_code
        == 200
    )

    row = asyncio.run(store.get(tenant_id="t1", inquiry_ref=result["inquiry_ref"]))
    assert row is not None
    assert (row.corrected_topic, row.corrected_sentiment, row.corrected_urgency) == (
        None,
        None,
        None,
    )
    assert row.reviewed_at is not None


# ── D-3 캐시 — 2회 호출에 LLM 1회 ───────────────────────────────


def test_second_call_hits_cache_without_llm(
    client: TestClient, store: InMemoryInquiryClassStore, provider: _CountingProvider
) -> None:
    """같은 `(tenant_id, inquiry_ref)` 재호출은 **LLM 0회**다(04:132 캐시 규정)."""
    first = _classify(client)
    second = _classify(client)

    assert provider.calls == 1, "캐시가 LLM을 막지 못했다"
    for axis in ("topic", "sentiment", "urgency"):
        assert first[axis] == second[axis]
    assert second["classified"] is True


# ── D-5 폴백 미적재 ─────────────────────────────────────────────


def test_fallback_is_not_persisted_and_confirmation_404(
    client: TestClient,
    store: InMemoryInquiryClassStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`classified=False`는 행을 만들지 않고, 그 `inquiry_ref`로 온 확정은 404다.

    판정이 없는 건에 enum 값을 채우면 불변식 2 위반이고 평가셋이 오염된다(P2-b 조건 4).
    """
    _patch_gateway(monkeypatch, _CountingProvider("깨진 출력"))
    result = _classify(client, inquiry_ref="iq_fallback")

    assert result["classified"] is False

    assert asyncio.run(store.get(tenant_id="t1", inquiry_ref="iq_fallback")) is None

    response = _confirm(
        client,
        {
            "kind": "classification",
            "suggestion_id": "iq_fallback",
            "action": "confirmed",
        },
    )
    assert response.status_code == 404


# ── D-6 거절 경로 ───────────────────────────────────────────────


def test_rejected_action_is_400(client: TestClient, store: InMemoryInquiryClassStore) -> None:
    """3축은 값이 반드시 있어야 하는 축이라 "거절"이 정의되지 않는다."""
    response = _confirm(
        client,
        {"kind": "classification", "suggestion_id": "iq_1", "action": "rejected"},
    )
    assert response.status_code == 400
    assert "action_not_supported" in response.text


@pytest.mark.parametrize("kind", ["tag", "label", "draft_edit"])
def test_unimplemented_kinds_are_400(
    client: TestClient, store: InMemoryInquiryClassStore, kind: str
) -> None:
    """🔴 제안 생성기가 없어 **확정할 대상이 없다** — 조용히 버리면 BE가 오해한다."""
    response = _confirm(
        client,
        {"kind": kind, "suggestion_id": "x", "action": "confirmed"},
    )
    assert response.status_code == 400
    assert "kind_not_implemented" in response.text
