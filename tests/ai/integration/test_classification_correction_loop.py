"""강사 정정이 실제로 저장되는가 — `/v1/classify` → `/v1/confirmations` 왕복.

🔴 **시임을 쓰지 않는다.** 이 결함이 오래 안 보인 이유가 정확히 그것이다 — 테스트는
`set_inquiry_class_store()`로 같은 인스턴스를 꽂아 왕복을 통과시켰고, 프로덕션 경로는
팩토리가 호출마다 새 인스턴스를 줘서 두 라우터가 **다른 행을 봤다.**

실측(고치기 전):

    ① POST /v1/classify      200 · classified=True · 적재됨
    ② POST /v1/confirmations 404 NOT_FOUND "대상 분류 없음"

⚠ 그 404가 정당한 실패와 **구분되지 않는다** — error_codes §2.7이 정의한 "폴백이라
적재되지 않았다"와 같은 응답이다. 강사 정정이 전부 유실되는데 이상 신호가 없다.
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
from ai.db.repositories.inquiry_class_store import (
    InMemoryInquiryClassStore,
    InquiryClassRecord,
)
from ai.db.store_factory import build_inquiry_class_store

_HEADERS = {"X-Tenant-Id": "t1", "X-Request-Id": "rq-loop-1"}


#: 🔴 앱을 띄우면 counsel 라우터의 기동 가드가 함께 돈다 — provider는 기본값이 없다(#108).
#: 이 테스트는 counsel과 무관하지만 `create_app()`을 쓰므로 조립 루트 몫을 대신 한다.
def _wire_counsel_provider() -> None:
    from ai.api.routers.counsel import set_counsel_provider
    from ai.composition.counsel.provider import FakeCounselProvider

    set_counsel_provider(FakeCounselProvider())


@pytest.fixture(autouse=True)
def _isolate() -> Iterator[None]:
    classify_router.reset_inquiry_class_store()
    confirmations_router.reset_inquiry_class_store()
    yield
    classify_router.reset_inquiry_class_store()
    confirmations_router.reset_inquiry_class_store()


@pytest.fixture
def client() -> Iterator[TestClient]:
    _wire_counsel_provider()
    with TestClient(create_app()) as test_client:
        yield test_client


def _classify(client: TestClient, ref: str) -> dict[str, Any]:
    response = client.post(
        "/v1/classify",
        json={"inquiry_ref": ref, "body_text": "이번 달 성적이 궁금합니다."},
        headers=_HEADERS,
    )
    assert response.status_code == 200, response.text
    data: dict[str, Any] = response.json()["data"]
    return data


def _confirm(client: TestClient, body: dict[str, Any]) -> httpx.Response:
    response: httpx.Response = client.post(
        "/v1/confirmations", json=body, headers=_HEADERS
    )
    return response


def _row(ref: str) -> InquiryClassRecord | None:
    store = confirmations_router.inquiry_class_store()
    return asyncio.run(store.get(tenant_id="t1", inquiry_ref=ref))


# ── A 저장소 공유 ────────────────────────────────────────────────


def test_correction_round_trip_succeeds_without_a_seam(client: TestClient) -> None:
    """🔴 수용 기준 — 시임 없이 왕복이 성공한다. 고치기 전에는 **404**였다."""
    predicted = _classify(client, "iq_1")
    assert predicted["classified"] is True, "예측이 적재되지 않으면 이 테스트가 헛돈다"

    response = _confirm(
        client,
        {
            "kind": "classification",
            "suggestion_id": "iq_1",
            "action": "corrected",
            "corrected_value": {"topic": "schedule"},
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"]["accepted"] is True

    row = _row("iq_1")
    assert row is not None and row.corrected_topic == "schedule"


def test_both_routers_read_the_same_store() -> None:
    """두 라우터가 미주입 상태에서 **같은 객체**를 본다 — 이게 왕복의 전제다."""
    assert (
        classify_router.inquiry_class_store()
        is confirmations_router.inquiry_class_store()
    )


def test_the_factory_no_longer_hands_out_fresh_instances() -> None:
    """🔴 결함의 뿌리 — 호출마다 새 인스턴스면 적재와 조회가 갈린다."""
    assert build_inquiry_class_store() is build_inquiry_class_store()


def test_reset_actually_clears_rows_not_just_rebinds(client: TestClient) -> None:
    """🔴 공유로 바꾸면 생기는 반대편 함정 — 재바인딩만으로는 행이 안 지워진다.

    `_store = build_inquiry_class_store()`는 같은 객체를 다시 가리킬 뿐이라 테스트 간
    행이 샌다. 공유와 격리를 **둘 다** 만족해야 한다.
    """
    _classify(client, "iq_leak")
    assert _row("iq_leak") is not None

    classify_router.reset_inquiry_class_store()
    assert _row("iq_leak") is None, "리셋했는데 이전 테스트의 행이 남아 있다"


def test_injection_still_wins(client: TestClient) -> None:
    """⚠ 공유로 바꾸면서 **주입 seam을 깨지 않았다**(#108의 결과 그대로).

    평가 러너·테스트가 자기 저장소를 꽂으면 그게 이긴다.
    """
    mine = InMemoryInquiryClassStore()
    classify_router.set_inquiry_class_store(mine)
    confirmations_router.set_inquiry_class_store(mine)
    try:
        assert classify_router.inquiry_class_store() is mine
        _classify(client, "iq_injected")
        assert asyncio.run(mine.get(tenant_id="t1", inquiry_ref="iq_injected")) is not None
        # 공용 저장소에는 안 들어갔다 — 주입이 실제로 경로를 바꿨다는 증거
        assert asyncio.run(
            build_inquiry_class_store().get(tenant_id="t1", inquiry_ref="iq_injected")
        ) is None
    finally:
        classify_router.reset_inquiry_class_store()  # 주입을 걷는다
        confirmations_router.reset_inquiry_class_store()


def test_missing_prediction_is_still_a_404(client: TestClient) -> None:
    """⚠ 404 자체를 없앤 게 아니다 — 분류한 적 없는 참조는 여전히 404다.

    이걸 같이 고정하지 않으면 "왕복이 통과한다"가 404를 통째로 없애서 얻어진 것일 수 있다.
    """
    response = _confirm(
        client,
        {
            "kind": "classification",
            "suggestion_id": "iq_never_classified",
            "action": "corrected",
            "corrected_value": {"topic": "schedule"},
        },
    )
    assert response.status_code == 404
