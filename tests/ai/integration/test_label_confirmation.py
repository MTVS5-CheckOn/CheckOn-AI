"""`kind=label` 확정 — 🔴 **받되 개별 확정을 영속하지 않는다**(2026-08-24 · №92 판정).

남기는 것은 **집계 다섯 칸**(`tenant · axis · 제안값 · 확정값 · action`)뿐이고,
🔴 **`guardian_ref` 는 안 남긴다** — «새로 쌓이는 개인 데이터 0» 정책과 갈리지 않는다.
🔴 그 다섯 칸이 **군집의 착수 근거**다 — 안 남기면 «어느 축의 제안이 얼마나 뒤집히나» 를
영영 못 센다.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from typing import Final

import pytest
from awaiting_label_store import AwaitingLabelConfirmationStore
from fastapi.testclient import TestClient
from httpx import Response

from ai.api.app import create_app
from ai.api.routers.confirmations import (
    label_confirmation_store,
    reset_label_confirmations,
    set_label_confirmation_store,
)


def _counts(client: TestClient) -> dict[tuple[str, str, str, str, str], int]:
    """🔴 집계를 **인터페이스로** 읽는다(2026-08-25 · 99 #239).

    ⚠ 🔴 종전에는 검사가 `Counter` **객체에 직접** 붙어 있었다 — 그래서 저장소를
    인터페이스 뒤로 넣자 **읽는 자리 다섯이 같이 바뀌었다.** 🔴 바뀐 것은 «**읽는 방법**»
    이고 «**무엇을 재는가**» 는 그대로다(키도 기대값도 안 바뀌었다).
    🔴 그래서 이건 «인터페이스가 틀렸다» 가 아니라 «검사가 구현에 붙어 있었다» 다 —
    지금은 **PG 구현으로 갈아끼워도 이 검사들이 안 바뀐다.**
    """
    #: 🔴 **`asyncio.run` 을 쓰지 않는다**(2026-08-25 · 99 #243).
    #: ⚠ 🔴 그건 **새 루프**를 만드는데, `#218` 이 정확히 그것으로 **11 failed** 였다:
    #: «TestClient 가 앱을 **자기 루프(portal)** 에서 돌려 pg 연결이 그 루프에 묶이는데
    #:   `asyncio.run` 이 새 루프에서 그 풀을 다시 쓴다» ⇒ `attached to a different loop`.
    #: 🔴 인메모리라 지금은 안 터지지만 **PG 가 오는 순간 터진다** — 이 회차의 목적이
    #: 「PG 가 왔을 때 안 터지게」였다.
    #: 🔴 **선례를 따른다**(`tests/ai/conftest.py` 의 problem 축 · #218 ⓓ):
    #: `TestClient.portal` 로 **앱과 같은 루프**에서 읽는다. `add` 는 라우터가 `await`
    #: 하므로 이미 앱 루프다.
    if client.portal is None:
        raise RuntimeError("TestClient 앱 이벤트 루프가 열리지 않았다")
    return client.portal.call(label_confirmation_store().snapshot)

_HEADERS = {"X-Tenant-Id": "t_lc", "X-Request-Id": "r_lc"}


@pytest.fixture(autouse=True)
def _reset_counts() -> None:
    """🔴 **검사마다 자동으로 비운다**(99 #237) — 손으로 부르던 다섯 군데를 걷었다.

    ⚠ 🔴 잊으면 앞 검사의 수를 물려받고 **그건 red 로도 skip 으로도 안 나타난다.**
    """
    reset_label_confirmations()


@pytest.fixture
def client() -> Iterator[TestClient]:
    """🔴 **컨텍스트로 연다** — 그래야 `portal`(앱 이벤트 루프)이 산다(99 #243).

    ⚠ 종전에는 `TestClient(...)` 를 그냥 돌려줘서 `portal is None` 이었고, 요청은
    돌지만 **앱 루프로 무언가를 부를 수 없었다.**
    """
    with TestClient(create_app(), raise_server_exceptions=False) as test_client:
        yield test_client


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
    #: 🔴 **줄이 아니라 카운터다**(99 #233).
    assert _counts(client).get(("t_lc", "comm", "data", "data", "confirmed"), 0) == 1
    #: 🔴 **이 단언이 #233 을 닫는다** — 줄이 남아 있으면 순서로 재식별이 된다.
    assert not [m for m in caplog.messages if "confirmations.label" in m], caplog.messages
    #: 🔴 개인 참조가 **카운터 키에도** 없다 — №92 판정의 전부다.
    assert all("gd_11b0" not in "".join(key) for key in _counts(client)), (
        list(_counts(client))
    )


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
    assert _counts(client).get(("t_lc", "comm", "data", "narrative", "corrected"), 0) == 1


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
    #: 🔴 **빈 `guardian_ref` 도 넣었다**(99 #232) — `rsplit` 은 `["", "comm", "data"]` 로
    #: 멀쩡히 쪼개져 **200 이 나갔다.** ⚠ 🔴 «파싱이 깨져서» 가 아니라 «**빈 참조에
    #: 「받았다」를 주면 BE 가 유효한 확정으로 센다**» 가 이유다.
    for bad in ("gd_11b0", "gd_11b0:comm", "gd_11b0:없는축:data", ":comm:data"):
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
    assert _counts(client).get(("t_lc", "interest", "grade", "grade", "confirmed"), 0) == 1
    assert all("gd:11:b0" not in "".join(key) for key in _counts(client))


#: 🔴 네 축 **전수** — 축마다 「남의 축 값」 하나(99 #232).
#: ⚠ 🔴 목록이 비면 이 검사가 **조용히 사라진다**(#202) ⇒ 아래에서 수를 센다.
_CROSS_AXIS: Final = [
    ("comm", "data", "anxious"),
    ("sensitivity", "anxious", "grade"),
    ("interest", "grade", "frequent"),
    ("frequency", "frequent", "narrative"),
]


def test_the_cross_axis_table_is_not_empty() -> None:
    """🔴 **대상이 0이 아니다** — 네 축을 다 덮는다(#202 · «사라지면 알아차린다»)."""
    assert len(_CROSS_AXIS) == 4, _CROSS_AXIS


@pytest.mark.parametrize(("axis", "own", "other"), _CROSS_AXIS, ids=[c[0] for c in _CROSS_AXIS])
def test_a_corrected_value_from_another_axis_is_400(
    client: TestClient, axis: str, own: str, other: str
) -> None:
    """🔴 **정정값이 그 축의 값이 아니면 400**(99 #232).

    ⚠ 🔴 **타입은 이걸 못 막는다** — `LabelCorrection.value` 는 네 축을 다 받고 축을
    모른다. 🔴 종전에는 `corrected` 가 **아무 검사도 안 타고 로그에 찍혔다** — 그 다섯
    칸이 「군집의 착수 근거」라 **오염되면 로그에 남은 뒤엔 못 가른다.**
    """
    bad = _post(
        client,
        {
            "kind": "label",
            "suggestion_id": f"gd_11b0:{axis}:{own}",
            "action": "corrected",
            "corrected_value": {"value": other},
        },
    )
    assert bad.status_code == 400, (axis, other, bad.text)
    assert bad.json()["error"]["detail"]["reason"] == "label_value_axis_mismatch"


def test_the_key_the_suggestion_gives_is_accepted_by_the_confirmation(
    client: TestClient,
) -> None:
    """🔴 **왕복** — 제안이 낸 키를 확정에 **그대로** 넣으면 200 이다(99 #235).

    ⚠ 🔴 **이 검사가 이 회차의 전부다.** №92 가 「받는 쪽」만 만들고 「내는 쪽」을 안 봐서
    우리가 준 UUID 를 돌려주면 **400** 이었다 — 확정 경로가 서 있는데 **아무도 못 밟았다.**
    🔴 「내는 쪽」과 「받는 쪽」을 **한 검사로 묶는다** — 그 사이가 №92 의 구멍이었다.
    """
    history = [
        {
            "record_id": f"cm_{index:02d}",
            "text": text,
            "direction": "inbound",
            "at": "2026-08-20T09:00:00+09:00",
        }
        for index, text in enumerate(
            [
                "지난주 과제를 모두 제출했습니다",
                "상담 일정을 다시 잡고 싶다고 했습니다",
                "교재 진도를 확인했습니다",
                "주말 보충 참여 의사를 밝혔습니다",
                "연락이 사흘째 없습니다",
            ],
            1,
        )
    ]
    #: 🔴 `guardian_ref` 에 `:` 을 넣어 둔다 — 왕복이 그것도 견디나(99 #231).
    suggested = client.post(
        "/v1/labels/suggest",
        json={"guardian_ref": "gd:11:b0", "history": history},
        headers=_HEADERS,
    )
    assert suggested.status_code == 200, suggested.text
    keys = [s["suggestion_id"] for s in suggested.json()["data"]["suggestions"]]
    assert keys, "제안이 0건이라 왕복을 못 잰다"
    for key in keys:
        assert key.startswith("gd:11:b0:"), key
        confirmed = _post(
            client, {"kind": "label", "suggestion_id": key, "action": "confirmed"}
        )
        assert confirmed.status_code == 200, (key, confirmed.text)


def test_four_confirmations_are_counted_and_split_by_key(client: TestClient) -> None:
    """🔴 ① 확정 4건 → 카운터가 4이고 **축·값·action 별로 갈린다**(99 #233)."""
    bodies: list[dict[str, object]] = [
        {"suggestion_id": "gd_1:comm:data", "action": "confirmed"},
        {"suggestion_id": "gd_2:comm:data", "action": "confirmed"},
        {"suggestion_id": "gd_3:comm:data", "action": "rejected"},
        {
            "suggestion_id": "gd_4:interest:grade",
            "action": "corrected",
            "corrected_value": {"value": "attitude"},
        },
    ]
    for body in bodies:
        assert _post(client, {"kind": "label", **body}).status_code == 200
    assert sum(_counts(client).values()) == 4
    assert _counts(client).get(("t_lc", "comm", "data", "data", "confirmed"), 0) == 2
    assert _counts(client).get(("t_lc", "comm", "data", "data", "rejected"), 0) == 1
    assert _counts(client).get(("t_lc", "interest", "grade", "attitude", "corrected"), 0) == 1


def test_a_rejected_400_is_not_counted(client: TestClient) -> None:
    """🔴 ⑤ 400 으로 거절된 확정은 **안 센다** — 「강사가 확정했다」가 부풀려진다."""
    for bad in (
        {"suggestion_id": ":comm:data", "action": "confirmed"},
        {"suggestion_id": "gd_1:없는축:data", "action": "confirmed"},
        {
            "suggestion_id": "gd_1:comm:data",
            "action": "corrected",
            "corrected_value": {"value": "anxious"},
        },
    ):
        assert _post(client, {"kind": "label", **bad}).status_code == 400
    assert sum(_counts(client).values()) == 0, _counts(client)


def test_the_seam_survives_a_store_that_actually_awaits(
    client: TestClient, request: pytest.FixtureRequest
) -> None:
    """🔴 **갈아끼울 대상(PG)과 같은 모양으로 갈아끼워도 안 바뀐다**(99 #241).

    ⚠ 🔴 №98 의 뒤집기는 «빈 **인메모리** 구현» 이었다 — 🔴 **실제로 갈아끼울 것(PG)으로는
    안 재봤다.** PG 는 `asyncpg` 를 타므로 `await` 가 **진짜로 도는** 구현이라야 이음매를
    잰다. 이 검사가 그 자리다: 대역을 꽂고 **위 검사들과 같은 단언**을 그대로 돌린다.
    """
    set_label_confirmation_store(AwaitingLabelConfirmationStore())
    request.addfinalizer(reset_label_confirmations)
    for body in (
        {"suggestion_id": "gd_1:comm:data", "action": "confirmed"},
        {"suggestion_id": "gd_2:interest:grade", "action": "rejected"},
    ):
        assert _post(client, {"kind": "label", **body}).status_code == 200
    #: 🔴 **단언이 위 검사들과 똑같다** — 읽는 방법도 안 바뀌었다.
    assert _counts(client)[("t_lc", "comm", "data", "data", "confirmed")] == 1
    assert _counts(client)[("t_lc", "interest", "grade", "grade", "rejected")] == 1
    assert sum(_counts(client).values()) == 2
