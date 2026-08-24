"""`/v1/labels/suggest` 응답 의미론 — 🔴 **04 §3.7 ①~⑤ 를 무는 가드**(2026-08-24 · №76).

🔴 «문서에 적었다» 로 끝내지 않는다. №61 §G ② 가 «04 문면을 무는 가드가 **표·목록은
아무도 안 문다**» 였고, 그 뒤로 **문면 옆에 검사 경로를 적는 형식**을 쓴다(준영님이 ops
표기에 요구한 그 형식). 이 파일의 함수 이름이 04 §3.7 에 **그대로 적혀 있다.**

🔴 **①과 ②를 가르는 것이 이 파일의 목적**이다:
  ① 게이트가 전량 드롭 → **200 · `suggestions: []`** («없다»)
  ② 이력이 전부 마스킹에 걸림 → **500 · `reason`** («못 한다»)
⚠ 둘 다 «제안 0건» 으로 보이지만 **다른 사건**이다. 같은 모양으로 내보내면 BE 는 화면에
빈 칩 영역을 띄우고 강사는 «이 학부모는 라벨이 없구나» 로 읽는다.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Final

from fake_provider import FakeProvider
from fastapi.testclient import TestClient
from httpx import Response

from ai.api.app import create_app
from ai.api.routers import labels as labels_router
from ai.composition.labels.provider import (
    GatewayLabelSuggestProvider,
    build_label_gateway,
)

_HEADERS: Final = {"X-Tenant-Id": "t-sem", "X-Request-Id": "r-sem"}


def _item(record_id: str, text: str) -> dict[str, str]:
    return {
        "record_id": record_id,
        "text": text,
        "direction": "inbound",
        "at": "2026-08-20T09:00:00+09:00",
    }


#: 🔴 다섯 건 — `MIN_HISTORY`(요청 하한)를 만족한다. 전부 마스킹을 통과하는 문면.
_CLEAN_HISTORY: Final = [
    _item("r-1", "지난주 과제를 모두 제출했습니다"),
    _item("r-2", "상담 일정을 다시 잡고 싶다고 했습니다"),
    _item("r-3", "교재 진도를 확인했습니다"),
    _item("r-4", "주말 보충 참여 의사를 밝혔습니다"),
    _item("r-5", "연락이 사흘째 없습니다"),
]

#: 🔴 다섯 건 **전부** 전화번호를 들어 마스킹 문지기에 걸린다(**오탐이 아니라 진짜 탐지**).
#: ⚠ 코퍼스에 이미 있는 가명만 쓴다 — 새 실명을 만들지 않는다.
_ALL_BLOCKED_HISTORY: Final = [
    _item("r-1", "회신은 010-1234-5678 로 달라고 했습니다"),
    _item("r-2", "회신은 010-1111-2222 로 달라고 했습니다"),
    _item("r-3", "회신은 010-2222-3333 로 달라고 했습니다"),
    _item("r-4", "회신은 010-3333-4444 로 달라고 했습니다"),
    _item("r-5", "회신은 010-5555-6666 로 달라고 했습니다"),
]


def _client(llm_text: str) -> Iterator[TestClient]:
    """🔴 실 `GatewayLabelSuggestProvider` + 대역 **LLM** — 우리 층은 전부 진짜로 돈다.

    ⚠ `FakeLabelSuggestProvider` 로는 못 잰다 — 그 대역은 **마스킹 층을 안 탄다**(99 #208).
    """
    labels_router.set_label_suggest_provider(
        GatewayLabelSuggestProvider(build_label_gateway(FakeProvider([llm_text])))
    )
    try:
        with TestClient(create_app(), raise_server_exceptions=False) as client:
            yield client
    finally:
        labels_router.reset_label_suggest_provider()


def _post(client: TestClient, history: list[dict[str, str]]) -> Response:
    response: Response = client.post(
        "/v1/labels/suggest",
        json={"guardian_ref": "gd-sem", "history": history},
        headers=_HEADERS,
    )
    return response


def test_a_fully_dropped_request_is_a_normal_200() -> None:
    """🔴 **04 §3.7 ①** — 게이트가 전량 폐기해도 **200 · 빈 배열**이다. 오류가 아니다.

    LLM 이 낸 두 줄 모두 **이력에 없는 인용**이라 근거 실존 게이트가 전부 버린다.
    """
    forged = "\n".join(
        [
            "comm | data | 0.7 | r-1 | 이력에 없는 지어낸 인용입니다",
            "interest | grade | 0.6 | r-2 | 이것도 지어낸 인용입니다",
        ]
    )
    for client in _client(forged):
        response = _post(client, _CLEAN_HISTORY)
    assert response.status_code == 200, response.text
    assert response.json()["data"]["suggestions"] == []
    #: 🔴 성공 봉투다 — `error` 가 비어 있어야 «없다» 로 읽힌다.
    assert response.json()["error"] is None


def test_all_history_blocked_is_a_500_not_an_empty_200() -> None:
    """🔴 **04 §3.7 ②** — 이력이 전부 걸리면 «못 한다» 다. ①과 **같은 모양이면 안 된다.**

    🔴 이 검사가 red 가 되는 가장 흔한 방식은 «빈 배열 200 으로 바꿨다» 이고, 그게 바로
    04 가 금지한 것이다(BE 가 성공으로 처리 ⇒ ①과 구분 소멸).
    """
    for client in _client("comm | data | 0.7 | r-1 | 안 쓰인다"):
        response = _post(client, _ALL_BLOCKED_HISTORY)
    assert response.status_code == 500, response.text
    body = response.json()
    assert body["data"] is None
    assert body["error"]["code"], body["error"]
    #: ⚠ 🔴 본문·인용문이 새면 안 된다(불변식 3 · 99 #80) — 이력 문면이 응답에 없어야 한다.
    rendered = response.text
    for item in _ALL_BLOCKED_HISTORY:
        assert item["text"] not in rendered


def test_the_two_zero_cases_do_not_look_alike() -> None:
    """🔴 **①과 ②를 가르는 자리** — 둘 다 「제안 0건」인데 **응답이 달라야 한다.**

    ⚠ 이 단언이 없으면 ②를 200+`[]` 로 바꿔도 위 두 검사 중 하나만 red 가 된다.
    """
    forged = "comm | data | 0.7 | r-1 | 이력에 없는 지어낸 인용입니다"
    for client in _client(forged):
        empty = _post(client, _CLEAN_HISTORY)
    for client in _client("comm | data | 0.7 | r-1 | 안 쓰인다"):
        blocked = _post(client, _ALL_BLOCKED_HISTORY)
    assert empty.status_code != blocked.status_code, (
        f"「없다」({empty.status_code})와 「못 한다」({blocked.status_code})가 "
        "같은 모양이다 — 04 §3.7 ②가 금지한 자리다."
    )


def test_the_suggestion_shape_is_pinned_and_differs_from_the_04_example() -> None:
    """🔴 **04 예시와 실제 응답이 갈린 자리를 고정한다**(99 #215 · 2026-08-24 실측).

    04 §3.7 예시는 `axis`·`value` 를 **최상위**에 두는데 실제는 `label` **중첩**이다.
    🔴 어느 쪽이 맞는지는 **BE 합의 사항**이라 이 회차에서 안 골랐다 — 대신 **현행을
    못 박아** 합의 전에 조용히 바뀌지 않게 한다. 합의가 나면 이 검사부터 고친다.
    """
    line = "comm | data | 0.7 | r-1 | 지난주 과제를 모두 제출했습니다"
    for client in _client(line):
        response = _post(client, _CLEAN_HISTORY)
    suggestion = response.json()["data"]["suggestions"][0]
    assert sorted(suggestion) == [
        "confidence",
        "evidence_quotes",
        "guardian_ref",
        "label",
        "suggestion_id",
    ], sorted(suggestion)
    assert sorted(suggestion["label"]) == ["axis", "value"]
    #: 🔴 **04 예시가 말하는 모양은 아직 아니다** — 이 단언이 red 가 되면 합의가 반영된 것이다.
    assert "axis" not in suggestion, "04 예시대로 평탄해졌다면 99 #215 를 닫아라"


def test_an_out_of_range_confidence_drops_only_that_line() -> None:
    """🔴 **04 §3.7 ⑤** — 범위 밖 `confidence` 는 **500 이 아니라 그 줄만** 버려진다.

    실측(8/24): `parse_suggestions` 가 `ValidationError` 를 받아 드롭한다 ⇒ 나머지는 산다.
    ⚠ 🔴 **그 처리가 맞는지는 판정 대기**(99 #207) — 이 검사는 «현행이 이렇다» 를 고정한다.
    """
    mixed = "\n".join(
        [
            "comm | data | 1.5 | r-1 | 지난주 과제를 모두 제출했습니다",
            "interest | attitude | 0.6 | r-4 | 주말 보충 참여 의사를 밝혔습니다",
        ]
    )
    for client in _client(mixed):
        response = _post(client, _CLEAN_HISTORY)
    assert response.status_code == 200, response.text
    suggestions = response.json()["data"]["suggestions"]
    #: 🔴 **한 건만 산다** — 범위 밖 한 줄이 나머지를 죽이지 않는다.
    assert len(suggestions) == 1, suggestions
    #: 🔴 실제 직렬화는 `label: {axis, value}` **중첩**이다 — 04 예시(최상위)와 다르다.
    assert suggestions[0]["label"]["axis"] == "interest"
