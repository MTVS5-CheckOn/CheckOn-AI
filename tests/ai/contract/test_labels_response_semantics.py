"""`/v1/labels/suggest` 응답 의미론 — 🔴 **04 §3.7 ①~⑤ 를 무는 가드**(2026-08-24 · №76).

🔴 «문서에 적었다» 로 끝내지 않는다. №61 §G ② 가 «04 문면을 무는 가드가 **표·목록은
아무도 안 문다**» 였고, 그 뒤로 **문면 옆에 검사 경로를 적는 형식**을 쓴다(준영님이 ops
표기에 요구한 그 형식). 이 파일의 함수 이름이 04 §3.7 에 **그대로 적혀 있다.**

🔴 **①과 ②를 가르는 것이 이 파일의 목적**이다:
  ① 게이트가 전량 드롭 → **200 · `suggestions: []`** («없다»)
  ② 이력이 전부 마스킹에 걸림 → **500 INTERNAL · 상세 미노출** («못 한다»)
⚠ 둘 다 «제안 0건» 으로 보이지만 **다른 사건**이다. 같은 모양으로 내보내면 BE 는 화면에
빈 칩 영역을 띄우고 강사는 «이 학부모는 라벨이 없구나» 로 읽는다.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from typing import Final

import pytest
from fake_provider import FakeProvider
from fastapi.testclient import TestClient
from httpx import Response

from ai.api.app import create_app
from ai.api.routers import labels as labels_router
from ai.composition.labels.prompt import PROMPT_ID
from ai.composition.labels.provider import (
    GatewayLabelSuggestProvider,
    build_label_gateway,
)
from ai.contracts.execution import ExecutionContext
from ai.contracts.llm import (
    CallOutcome,
    LlmError,
    LLMRequest,
    LLMResult,
    LlmTimeout,
    LlmUnavailable,
    ModelRole,
    TokenUsage,
)
from ai.db.repositories.run_store import default_llm_call_collector
from ai.llm.call_timeouts import CallTimeoutTable
from ai.llm.gateway import LlmGateway
from ai.runtime.trace_masking import RedactionTripwireTraceHook

_HEADERS: Final = {"X-Tenant-Id": "t-sem", "X-Request-Id": "r-sem"}


class _SlowProvider:
    """🔴 상한보다 **느린** 대역 — 실 LLM 없이 「상한 초과」를 만든다."""

    name = "slow-fake"

    async def complete(
        self, request: LLMRequest, context: ExecutionContext
    ) -> LLMResult:
        del request, context
        await asyncio.sleep(0.5)
        return LLMResult(
            outcome=CallOutcome.OK,
            text="",
            provider=self.name,
            model="slow",
            usage=TokenUsage(tokens_in=0, tokens_out=0, cost_usd=0.0),
            latency_ms=0,
        )


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


def _client(llm_text: str | LlmError) -> Iterator[TestClient]:
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


def test_the_current_suggestion_shape_is_pinned_pending_be_agreement() -> None:
    """현재 제안 객체 모양을 BE 합의 전까지 고정한다 — `label` 중첩(99 #215).

    ⚠ PR #403에는 누가·언제·어디서 합의했는지 기록이 없다. 이 검사는 합의 전에 현행이
    조용히 바뀌지 않게 할 뿐, v1 확정을 대신하지 않는다.

    🔴 **왜 중첩인가**: `LabelSuggestion(axis, value)` 은 **재사용 모델**이고
    `SuggestedLabel` 이 그것을 **품는다** — 4축 값의 정의를 **한 곳**에 두려는 것이다
    (`contracts/counsel.py` 의 `LabelSuggestion` docstring). 평평하게 펴면 그 재사용이
    깨지고 4축 정의가 **두 곳으로 갈린다.**
    ⚠ 🔴 `operationId` 와 다르다 — 그건 **BE 의 메서드명**이 되지만(남의 코드) 응답
    스키마도 BE가 파싱하는 접점이므로 실제 합의 기록 뒤에 확정한다.
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
    #: 🔴 **평탄화 금지** — 최상위에 `axis` 가 나타나면 4축 정의가 두 곳으로 갈린 것이다.
    assert "axis" not in suggestion, "제안 객체를 평평하게 폈다 — 04 §3.7 예시가 계약이다"


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


@pytest.mark.parametrize(
    ("error", "status_code", "code"),
    [
        (LlmTimeout("대역 타임아웃"), 504, "TIMEOUT"),
        (LlmUnavailable("대역 장애"), 503, "LLM_UPSTREAM_DOWN"),
    ],
)
def test_gateway_failures_use_the_canonical_http_mapping_without_retry(
    error: LlmError, status_code: int, code: str
) -> None:
    """실 게이트웨이 경계의 실패 매핑과 라벨 전송 재시도 0회를 함께 고정한다."""
    fake = FakeProvider([error])
    labels_router.set_label_suggest_provider(
        GatewayLabelSuggestProvider(build_label_gateway(fake))
    )
    try:
        with TestClient(create_app(), raise_server_exceptions=False) as client:
            response = _post(client, _CLEAN_HISTORY)
    finally:
        labels_router.reset_label_suggest_provider()

    assert response.status_code == status_code, response.text
    assert response.json()["error"]["code"] == code
    assert response.json()["error"].get("detail") is None
    assert len(fake.requests) == 1, "라벨 전송 실패를 재시도했다"


def test_label_gateway_resolves_the_prompt_to_twenty_seconds_and_one_attempt() -> None:
    """조립된 게이트웨이가 라벨 prompt_id를 정본 표의 20초·무재시도로 해석한다."""
    gateway = build_label_gateway(FakeProvider(["쓰이지 않음"]))

    assert gateway._call_timeouts.get(PROMPT_ID) == 20.0
    assert gateway._attempts_for(ModelRole.COUNSELOR) == 1


def test_exceeding_the_call_cap_is_a_504_not_a_500() -> None:
    """🔴 **04 §3.7 ⑥** — 콜당 상한을 넘기면 **504 `TIMEOUT`** 이다. 500 이 아니다.

    🔴 **문서에 적기 전에 이 경로가 실제로 도는지 먼저 쟀다**(2026-08-24) — #191 이
    세운 규율(«생성기 부재를 빈 배열로 위장하지 않는다»)의 같은 결이다: **안 도는
    상태코드를 계약에 적으면 문서가 거짓말을 한다.**

    ⚠ 실 LLM 을 안 쓴다 — **느린 대역 provider** 와 **아주 작은 상한**으로 같은 경로를
    태운다(`LlmGateway` 가 상한 초과를 `LlmTimeout` 으로 바꿔 올리고, 라우터의
    `domain_error_for` 가 그것을 `LlmUpstreamTimeout`(504 `TIMEOUT`)으로 옮긴다).
    """
    gateway = LlmGateway(
        {ModelRole.COUNSELOR: _SlowProvider()},
        recorder=default_llm_call_collector(),
        transport_retry={ModelRole.COUNSELOR: 0},
        trace_masking_hook=RedactionTripwireTraceHook(),
        call_timeouts=CallTimeoutTable(call_timeouts={PROMPT_ID: 0.05}),
    )
    labels_router.set_label_suggest_provider(GatewayLabelSuggestProvider(gateway))
    try:
        with TestClient(create_app(), raise_server_exceptions=False) as client:
            response = _post(client, _CLEAN_HISTORY)
    finally:
        labels_router.reset_label_suggest_provider()

    assert response.status_code == 504, response.text
    assert response.json()["error"]["code"] == "TIMEOUT"
    #: 🔴 ②(500)와 **다른 코드**여야 한다 — 같으면 BE 가 두 사건을 못 가른다.
    assert response.status_code != 500
    #: ⚠ 🔴 이력 본문이 새면 안 된다(불변식 3 · 99 #80).
    for item in _CLEAN_HISTORY:
        assert item["text"] not in response.text
