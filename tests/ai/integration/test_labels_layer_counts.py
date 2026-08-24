"""라벨 층별 통과 수 — 🔴 **관측이 아니라 배선 가드다**(99 #208 · 2026-08-24).

№66·№67·№71 이 네 회차 연속 「뒤집기 green」을 냈고 셋이 **같은 모양**이었다:
단위 검사가 층 함수를 **직접** 부르니, 라우터가 그 층을 아예 안 불러도 단위 검사도
종단 검사도 둘 다 통과했다. 층을 껐는데 **아무 데도 안 빨개졌다.**

⇒ 이 파일은 층을 직접 부르지 않는다. **HTTP 로 한 요청을 넣고, 층별 로그 한 줄의
수를 읽는다.** 층이 안 불리면 그 칸이 `-`(미측정) 또는 수가 어긋나 red 다.

🔴 **대역 provider 로는 이걸 못 잰다** — `FakeLabelSuggestProvider` 는 마스킹 층을 안
탄다(전송이 없으니 필요가 없다). 그래서 여기서는 **실 `GatewayLabelSuggestProvider`**
에 대역 **LLM** 만 꽂는다 — 우리 층은 전부 진짜로 돈다.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterator

import pytest
from fake_provider import FakeProvider
from fastapi.testclient import TestClient

from ai.api.app import create_app
from ai.api.routers import labels as labels_router
from ai.composition.labels.provider import (
    GatewayLabelSuggestProvider,
    build_label_gateway,
)


#: 한 요청의 이력 넷 — 🔴 **코퍼스에 이미 있는 가명만**(새 실명 창작 금지).
#: `r-3` 만 전화번호를 들어 마스킹 문지기에 걸린다(**오탐이 아니라 진짜 탐지**).
def _item(record_id: str, text: str) -> dict[str, str]:
    return {
        "record_id": record_id,
        "text": text,
        "direction": "inbound",
        "at": "2026-08-20T09:00:00+09:00",
    }


_HISTORY = [
    _item("r-1", "지난주 과제를 모두 제출했습니다"),
    _item("r-2", "상담 일정을 다시 잡고 싶다고 했습니다"),
    _item("r-3", "회신은 010-1234-5678 로 달라고 했습니다"),
    _item("r-4", "주말 보충 참여 의사를 밝혔습니다"),
    _item("r-5", "연락이 사흘째 없습니다"),
]

#: 🔴 걸릴 이력이 **하나도 없는** 같은 크기의 이력 — 앵커 폭 측정용.
#: ⚠ 건수를 줄이면 `MIN_HISTORY`(요청 하한 5)에 걸려 **400** 이 난다 — 그건 층이 아니라
#: 계약이 낸 red 라 앵커를 못 잰다. ⇒ **한 건을 갈아끼운다**(수는 그대로 5).
_CLEAN_HISTORY = [
    item if item["record_id"] != "r-3" else _item("r-3", "교재 진도를 확인했습니다")
    for item in _HISTORY
]

#: LLM 이 낼 다섯 줄 — 층마다 **정확히 하나씩** 걸리게 짰다.
#:   ① 형식 위반 한 줄       → 파서가 버린다        (parsed=4 · parse_dropped=1)
#:   ② 지어낸 인용 한 줄     → 게이트가 버린다      (gate_passed=3 · gate_dropped=1)
#:   ③ 같은 축이 둘          → 병합이 합친다        (merged_from=3 · merged_to=2)
_LLM_TEXT = "\n".join(
    [
        "이건 형식이 틀린 줄입니다",
        "comm | data | 0.5 | r-1 | 지난주 과제를 모두 제출했습니다",
        "comm | data | 0.7 | r-2 | 상담 일정을 다시 잡고 싶다고 했습니다",
        "interest | attitude | 0.6 | r-4 | 주말 보충 참여 의사를 밝혔습니다",
        "comm | data | 0.9 | r-5 | 지어낸 인용문입니다",
    ]
)


@pytest.fixture
def client() -> Iterator[TestClient]:
    provider = GatewayLabelSuggestProvider(
        build_label_gateway(FakeProvider([_LLM_TEXT]))
    )
    labels_router.set_label_suggest_provider(provider)
    try:
        with TestClient(create_app()) as test_client:
            yield test_client
    finally:
        labels_router.reset_label_suggest_provider()


def _layer_line(caplog: pytest.LogCaptureFixture) -> str:
    lines = [m for m in caplog.messages if "층별" in m]
    assert len(lines) == 1, f"층별 줄이 {len(lines)}개다: {lines}"
    return lines[0]


def _counts(line: str) -> dict[str, str]:
    return dict(pair.split("=", 1) for pair in line.split() if "=" in pair)


def test_every_layer_reports_its_own_count(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    """🔴 **한 요청이 네 층을 지나고, 네 층이 전부 자기 수를 낸다.**

    칸 하나가 `-` 면 그 층이 **안 불렸다**는 뜻이다 — 그게 이 검사가 잡는 것이다.
    """
    with caplog.at_level(logging.INFO, logger="ai.api.routers.labels"):
        response = client.post(
            "/v1/labels/suggest",
            json={"guardian_ref": "g-1", "history": _HISTORY},
            headers={"X-Tenant-Id": "t-1", "X-Request-Id": "req-1"},
        )
    assert response.status_code == 200, response.text
    counts = _counts(_layer_line(caplog))

    unmeasured = sorted(key for key, value in counts.items() if value == "-")
    assert not unmeasured, f"안 불린 층이 있다: {unmeasured}"

    #: 마스킹 층 — 전화번호가 든 `r-3` 한 건만 빠진다.
    assert counts["history_kept"] == "4"
    assert counts["history_dropped"] == "1"
    #: 파서 층 — 형식 위반 한 줄을 버린다.
    assert counts["parsed"] == "4"
    assert counts["parse_dropped"] == "1"
    #: 게이트 층 — 지어낸 인용 하나를 버린다. 🔴 **사유까지 갈라 센다.**
    assert counts["gate_passed"] == "3"
    assert counts["gate_dropped"] == "1"
    assert counts["gate_reasons"] == "quote_not_in_record:1"
    #: 병합 층 — 같은 `(comm, data)` 둘을 하나로.
    assert counts["merged_from"] == "3"
    assert counts["merged_to"] == "2"

    #: 응답도 같은 말을 한다 — 로그만 맞고 본문이 틀리면 그건 관측이 거짓말한 것이다.
    assert len(response.json()["data"]["suggestions"]) == 2


def test_the_layer_line_carries_no_history_text(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    """🔴 **본문·인용문 미기재**(불변식 3 · 99 #80) — 수와 사유 코드까지다."""
    with caplog.at_level(logging.INFO, logger="ai.api.routers.labels"):
        client.post(
            "/v1/labels/suggest",
            json={"guardian_ref": "g-1", "history": _HISTORY},
            headers={"X-Tenant-Id": "t-1", "X-Request-Id": "req-1"},
        )
    line = _layer_line(caplog)
    for item in _HISTORY:
        assert item["text"] not in line
    #: 🔴 전화번호 조각도 안 새야 한다 — 걸린 이력의 `record_id` 조차 이 줄엔 없다.
    assert not re.search(r"\d{3}-\d{4}", line), line


def test_the_counts_are_not_a_constant(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    """🔴 **앵커 폭** — 입력을 바꾸면 수도 바뀌어야 한다(상수를 박으면 여기서 red).

    №68 이 「검사가 무엇을 쟀는지」를 물어야 한다고 정한 그 자리다. 위 검사만 있으면
    카운터를 그 숫자로 **박아도** 통과한다.
    """
    clean = _CLEAN_HISTORY
    with caplog.at_level(logging.INFO, logger="ai.api.routers.labels"):
        response = client.post(
            "/v1/labels/suggest",
            json={"guardian_ref": "g-2", "history": clean},
            headers={"X-Tenant-Id": "t-1", "X-Request-Id": "req-2"},
        )
    assert response.status_code == 200, response.text
    counts = _counts(_layer_line(caplog))
    #: 걸릴 이력이 없으니 마스킹은 **0건 드롭** — 🔴 `-`(미측정)이 아니라 `0`(측정했고 없다).
    assert counts["history_dropped"] == "0"
    assert counts["history_kept"] == "5"
