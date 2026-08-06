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
from decimal import Decimal
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from ai.api.app import create_app
from ai.api.routers import classify as classify_router
from ai.api.routers import confirmations as confirmations_router
from ai.api.routers.confirmations import (
    CORRECTED_VALUE_MISSING,
    CORRECTED_VALUE_NOT_ALLOWED,
)
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


# ── B 조용한 통과 2종 ────────────────────────────────────────────


def test_corrected_without_a_value_is_refused(client: TestClient) -> None:
    """🔴 `action=corrected` + 값 없음이 성공으로 나가지 않는다.

    종전엔 200 `accepted:true`로 나가고 `corrections={}`라 `reviewed_at`만 찍혔다.
    그 행은 규약 ①에 의해 **이후 재예측이 영구 차단**된다 — "검토함"으로 굳는다.
    """
    _classify(client, "iq_empty")
    response = _confirm(
        client,
        {"kind": "classification", "suggestion_id": "iq_empty", "action": "corrected"},
    )
    assert response.status_code == 400, response.text
    assert response.json()["error"]["detail"]["reason"] == CORRECTED_VALUE_MISSING

    row = _row("iq_empty")
    assert row is not None and row.reviewed_at is None, (
        "거절했는데 reviewed_at이 찍혀 규약 ①로 재예측이 막혔다"
    )


def test_all_null_correction_counts_as_missing(client: TestClient) -> None:
    """⚠ `corrected_value={}`(3축 전부 null)도 같은 자리다 — 형태만 다르고 결과가 같다."""
    _classify(client, "iq_allnull")
    response = _confirm(
        client,
        {
            "kind": "classification",
            "suggestion_id": "iq_allnull",
            "action": "corrected",
            "corrected_value": {"topic": None, "sentiment": None, "urgency": None},
        },
    )
    assert response.status_code == 400
    assert response.json()["error"]["detail"]["reason"] == CORRECTED_VALUE_MISSING


def test_confirmed_with_a_value_is_refused(client: TestClient) -> None:
    """🔴 반대 조합 — `confirmed`에 값이 실리면 종전엔 값이 통째로 버려졌다.

    버릴 거면 받지 않는다(kind·action 거절과 같은 결).
    """
    _classify(client, "iq_conf")
    response = _confirm(
        client,
        {
            "kind": "classification",
            "suggestion_id": "iq_conf",
            "action": "confirmed",
            "corrected_value": {"topic": "schedule"},
        },
    )
    assert response.status_code == 400
    assert response.json()["error"]["detail"]["reason"] == CORRECTED_VALUE_NOT_ALLOWED


def test_plain_confirmed_still_marks_reviewed(client: TestClient) -> None:
    """⚠ 정상 확정은 그대로 통과한다 — 과차단은 미차단만큼 나쁘다."""
    _classify(client, "iq_ok")
    response = _confirm(
        client,
        {"kind": "classification", "suggestion_id": "iq_ok", "action": "confirmed"},
    )
    assert response.status_code == 200, response.text
    row = _row("iq_ok")
    assert row is not None and row.reviewed_at is not None
    assert row.corrected_topic is None  # 확정은 정정이 아니다


def test_outcome_reports_what_was_actually_applied(client: TestClient) -> None:
    """🔴 로그 수치가 요청받은 축 수가 아니라 **실제 적용분**이다.

    규약 ②로 걸러진 축까지 "적용"으로 남으면 저장 상태와 로그가 갈린다.
    """
    predicted = _classify(client, "iq_same")
    store = confirmations_router.inquiry_class_store()
    outcome = asyncio.run(
        store.apply_confirmation(
            tenant_id="t1",
            inquiry_ref="iq_same",
            corrections={"topic": predicted["topic"]},  # 예측과 같은 값
        )
    )
    assert outcome.found is True
    assert outcome.applied == {}, "같은 값 정정이 적용으로 세어졌다(규약 ②)"
    assert outcome.touched == 0


def test_unknown_axis_is_reported_not_silently_dropped() -> None:
    """🔴 미지 축을 조용히 버리지 않는다.

    ⚠ **HTTP로는 도달하지 않는다** — `ClassificationCorrection`이 `extra="forbid"`라
    축 오타는 400(`extra_forbidden`)에서 걸린다. 이 방어는 저장소를 직접 부르는 호출부
    (평가 러너·향후 배치)를 위한 것이고, 저장소는 계약을 믿지 않는다.
    """
    store = InMemoryInquiryClassStore()
    record = InquiryClassRecord(
        topic="grade",
        sentiment="normal",
        urgency="normal",
        confidence_topic=Decimal("0.9"),
        confidence_sentiment=Decimal("0.9"),
        confidence_urgency=Decimal("0.9"),
    )
    asyncio.run(store.insert_prediction(tenant_id="t1", inquiry_ref="x", record=record))
    outcome = asyncio.run(
        store.apply_confirmation(
            tenant_id="t1",
            inquiry_ref="x",
            corrections={"topik": "schedule", "sentiment": "complaint"},
        )
    )
    assert outcome.unknown == {"topik"}
    assert set(outcome.applied) == {"sentiment"}
    assert outcome.touched == 1, "요청 2축인데 적용은 1축 — 수치가 실제와 같아야 한다"


def test_outcome_is_not_a_truthy_tuple() -> None:
    """🔴 반환 타입을 바꾸면서 남길 뻔한 함정 — `if not outcome:`이 조용히 죽는 형태.

    NamedTuple이면 필드가 있는 한 항상 truthy라 종전 호출부의 404 분기가 사라진다.
    호출부가 `outcome.found`를 명시적으로 보게 강제한다.
    """
    from ai.db.repositories.inquiry_class_store import ConfirmationOutcome

    assert not isinstance(ConfirmationOutcome(found=False), tuple)
