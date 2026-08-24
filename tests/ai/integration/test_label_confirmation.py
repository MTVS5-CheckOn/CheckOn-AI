"""`kind=label` 확정 — 🔴 **받되 개별 확정을 영속하지 않는다**(2026-08-24 · №92 판정).

남기는 것은 **집계 다섯 칸**(`tenant · axis · 제안값 · 확정값 · action`)뿐이고,
🔴 **`guardian_ref` 는 안 남긴다** — «새로 쌓이는 개인 데이터 0» 정책과 갈리지 않는다.
🔴 그 다섯 칸이 **군집의 착수 근거**다 — 안 남기면 «어느 축의 제안이 얼마나 뒤집히나» 를
영영 못 센다.
"""

from __future__ import annotations

import logging

import pytest
from fastapi.testclient import TestClient
from httpx import Response

from ai.api.app import create_app

_HEADERS = {"X-Tenant-Id": "t_lc", "X-Request-Id": "r_lc"}


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app(), raise_server_exceptions=False)


def _post(client: TestClient, body: dict[str, object]) -> Response:
    response: Response = client.post(
        "/v1/confirmations", json=body, headers=_HEADERS
    )
    return response


def test_a_confirmed_label_is_accepted_and_only_the_aggregate_is_logged(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    """🔴 ①·④ — 200 이고 **집계 다섯 칸이 남되 `guardian_ref` 는 안 남는다.**"""
    with caplog.at_level(logging.INFO, logger="ai.api.routers.confirmations"):
        response = _post(
            client,
            {
                "kind": "label",
                "suggestion_id": "gd_11b0:comm:data",
                "action": "confirmed",
            },
        )
    assert response.status_code == 200, response.text
    assert response.json()["data"]["accepted"] is True
    line = next(m for m in caplog.messages if "confirmations.label" in m)
    for cell in ("axis=comm", "suggested=data", "confirmed=data", "action=confirmed"):
        assert cell in line, line
    #: 🔴 **이 단언이 이 판정의 전부다** — 개인 참조가 집계에 섞이면 정책이 무너진다.
    assert "gd_11b0" not in line, line


def test_a_corrected_label_records_the_new_value(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    """제안값과 확정값이 **둘 다** 남아야 «뒤집힘» 을 셀 수 있다."""
    with caplog.at_level(logging.INFO, logger="ai.api.routers.confirmations"):
        response = _post(
            client,
            {
                "kind": "label",
                "suggestion_id": "gd_11b0:comm:data",
                "action": "corrected",
                "corrected_value": {"value": "narrative"},
            },
        )
    assert response.status_code == 200, response.text
    line = next(m for m in caplog.messages if "confirmations.label" in m)
    assert "suggested=data" in line and "confirmed=narrative" in line, line


def test_a_value_outside_the_enum_is_400(client: TestClient) -> None:
    """🔴 ② — 4축 enum 밖의 값은 **거절**한다(자유 텍스트 라벨은 스키마상 불가)."""
    response = _post(
        client,
        {
            "kind": "label",
            "suggestion_id": "gd_11b0:comm:data",
            "action": "corrected",
            "corrected_value": {"value": "무엇이든"},
        },
    )
    assert response.status_code == 400, response.text


def test_a_rejected_label_is_accepted_but_classification_still_is_not(
    client: TestClient,
) -> None:
    """🔴 ③ — 라벨은 «거절» 이 뜻을 갖지만 classification 은 여전히 400 이다.

    ⚠ classification 은 3축이 **값을 반드시 가져야** 해서 «거절» 이 정의되지 않는다.
    🔴 라벨은 «이 축을 안 쓴다» 가 뜻이 된다 — **되돌아가지 않았나** 를 같이 문다.
    """
    label = _post(
        client,
        {"kind": "label", "suggestion_id": "gd_11b0:comm:data", "action": "rejected"},
    )
    assert label.status_code == 200, label.text
    classification = _post(
        client,
        {"kind": "classification", "suggestion_id": "iq_1", "action": "rejected"},
    )
    assert classification.status_code == 400, classification.text


def test_a_malformed_key_is_400(client: TestClient) -> None:
    """🔴 키가 `guardian_ref:axis:value` 가 아니면 거절한다."""
    for bad in ("gd_11b0", "gd_11b0:comm", "gd_11b0:없는축:data"):
        response = _post(
            client, {"kind": "label", "suggestion_id": bad, "action": "confirmed"}
        )
        assert response.status_code == 400, (bad, response.text)


def test_a_guardian_ref_with_colons_still_parses(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    """🔴 **`guardian_ref` 안의 `:` 은 무해하다**(2026-08-24 실측 · §C).

    축·값은 **우리 소유의 닫힌 열거형**이라 `:` 을 안 담으므로 오른쪽에서 둘만 떼면
    언제나 복원된다 ⇒ 🔴 **BE 에 «`:` 금지» 를 걸 필요가 없다.**
    """
    with caplog.at_level(logging.INFO, logger="ai.api.routers.confirmations"):
        response = _post(
            client,
            {
                "kind": "label",
                "suggestion_id": "gd:11:b0:interest:grade",
                "action": "confirmed",
            },
        )
    assert response.status_code == 200, response.text
    line = next(m for m in caplog.messages if "confirmations.label" in m)
    assert "axis=interest" in line and "suggested=grade" in line, line
    assert "gd:11:b0" not in line, line
