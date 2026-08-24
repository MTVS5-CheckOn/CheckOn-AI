"""A 소유 라우트의 `/docs` 표기 — 🔴 **`operationId` 는 BE 메서드명이다**(2026-08-24 · №74).

`detect_openapi.py` 가 적어 둔 함정 그대로다: 자동 생성 id 를 두면 **핸들러 함수 이름을
바꾸는 순간 BE 클라이언트의 메서드명이 따라 바뀐다.** 통신테스트 전에 고정한다.

🔴 **리터럴로 한 번 더 문다**(`test_detect_openapi_contract.py` 선례 · 로그 173):
상수와만 대조하면 «상수를 고쳐서 통과» 가 된다. 리터럴 줄이 그걸 막는다.
⚠ 🔴 그리고 이건 **등식 대조**라 드리프트를 문다(99 #211 닫는 조건 · 로그 188).
"""

from __future__ import annotations

from typing import Any, Final

import pytest

from ai.api.app import create_app
from ai.api.routers.classify_openapi import (
    CLASSIFY_OPERATION_ID,
    CLASSIFY_SUMMARY,
    CLASSIFY_TAG,
)
from ai.api.routers.confirmations_openapi import (
    CONFIRMATIONS_OPERATION_ID,
    CONFIRMATIONS_SUMMARY,
    CONFIRMATIONS_TAG,
)
from ai.api.routers.labels_openapi import (
    LABELS_OPERATION_ID,
    LABELS_SUMMARY,
    LABELS_TAG,
)

#: (경로, 메서드, 태그 상수, id 상수, summary 상수, 🔴 **리터럴 id**)
_A_OWNED_ROUTES: Final = [
    (
        "/v1/classify",
        "post",
        CLASSIFY_TAG,
        CLASSIFY_OPERATION_ID,
        CLASSIFY_SUMMARY,
        "classifyInquiry",
    ),
    (
        "/v1/confirmations",
        "post",
        CONFIRMATIONS_TAG,
        CONFIRMATIONS_OPERATION_ID,
        CONFIRMATIONS_SUMMARY,
        "confirmSuggestion",
    ),
    (
        "/v1/labels/suggest",
        "post",
        LABELS_TAG,
        LABELS_OPERATION_ID,
        LABELS_SUMMARY,
        "suggestGuardianLabels",
    ),
]

#: 🔴 **태그가 없어도 되는 자리** — «왜» 와 «언제 없어지나» 를 반드시 적는다.
#: 실측(8/24 · `/openapi.json`): `default` 그룹에 **13개**가 있었고 A 소유는 셋뿐이었다
#: ⇒ 이 회차 뒤 **10개**가 남는다(아래 목록과 같은 수여야 한다 — 그걸 다음 검사가 문다).
_NOT_A_OWNED: Final = {
    # 왜: `/v1/diagnosis`·`/v1/problems*` 는 B(염준영) 소유 축이다 — 남의 라우터에
    #     `operationId` 를 붙이는 것은 그쪽 BE 메서드명을 우리가 정하는 것이다.
    # 언제: B 가 같은 처방을 하면 없어진다(전달함 — №74 §C).
    "/v1/diagnosis",
    "/v1/problems",
    "/v1/problems/{job_id}",
    "/v1/problems/{set_id}/items",
    "/v1/problems/{set_id}/items/{slot_index}",
    "/v1/problems/{set_id}/items/{slot_index}/revisions",
    # 왜: `/v1/reports*` 도 B 소유(`report-0.1` · 99 #210 표기 판정 대기 8/24 회신).
    # 언제: 그 회신이 오면 정해진다.
    "/v1/reports",
    "/v1/reports/{report_id}",
    "/v1/reports/{report_id}/blocks/{block_id}",
    "/v1/reports/{report_id}/blocks/{block_id}/restore",
}


def _spec() -> dict[str, Any]:
    spec: dict[str, Any] = create_app().openapi()
    return spec


def test_the_route_list_is_not_empty() -> None:
    """🔴 **앵커 폭** — `parametrize` 가 빈 목록이면 검사가 **조용히 사라진다**(99 #202).

    red 도 skip 도 아니고 collect 조차 안 된다 ⇒ 「무엇을 쟀나」를 따로 문다.
    """
    assert len(_A_OWNED_ROUTES) == 3, _A_OWNED_ROUTES


@pytest.mark.parametrize(
    ("path", "method", "tag", "operation_id", "summary", "literal_id"),
    _A_OWNED_ROUTES,
    ids=[route[0] for route in _A_OWNED_ROUTES],
)
def test_a_owned_route_is_tagged_and_has_a_frozen_operation_id(
    path: str,
    method: str,
    tag: str,
    operation_id: str,
    summary: str,
    literal_id: str,
) -> None:
    operation = _spec()["paths"][path][method]
    #: ① 도메인 그룹 하나 — 여러 개면 `/docs` 에 중복으로 뜬다.
    assert operation.get("tags") == [tag], operation.get("tags")
    #: ② 상수와 대조 · 🔴 **리터럴로도 대조**(상수를 고쳐서 통과하는 길을 막는다).
    assert operation.get("operationId") == operation_id
    assert operation.get("operationId") == literal_id
    #: ③ 요약이 있고 비어 있지 않다 — FastAPI 자동 요약(`Post Classify`)이면 여기서 갈린다.
    assert operation.get("summary") == summary
    assert summary.strip(), summary


def test_no_a_owned_route_falls_into_the_default_group() -> None:
    """🔴 **이 회차의 진짜 가드** — 새 A 라우터가 태그 없이 생기면 red.

    ⚠ 🔴 **`default` 그룹 자체는 안 비어 있다** — B 소유 **열**이 남는다(실측 8/24).
    그건 결함이 아니라 **소유 경계**다: 남의 축에 `operationId` 를 붙이는 것은 그쪽
    BE 메서드명을 우리가 정하는 것이다(§C 로 전달했다).
    """
    untagged = {
        f"{method.upper()} {path}"
        for path, operations in _spec()["paths"].items()
        for method, operation in operations.items()
        if not operation.get("tags") and path not in _NOT_A_OWNED
    }
    assert not untagged, (
        f"태그 없는 A 소유 라우트: {sorted(untagged)}. "
        "도메인 그룹을 붙이든지, B 소유면 `_NOT_A_OWNED` 에 왜·언제와 함께 적어라."
    )


def test_the_exception_list_only_holds_routes_that_exist() -> None:
    """면제 목록이 **죽은 문자열**로 남지 않게 — 경로가 사라지면 여기서 red."""
    stale = _NOT_A_OWNED - set(_spec()["paths"])
    assert not stale, f"없어진 경로가 면제 목록에 남았다: {sorted(stale)}"


def test_the_default_group_holds_exactly_the_excused_routes() -> None:
    """🔴 **「몇 개가 남았나」를 수로 문다** — 보고에 적은 수가 실제와 갈리지 않게.

    ⚠ 실측 8/24: 이 회차 **전 13 · 후 10**. 종전 보고 초안이 «12 → 9» 로 적었다가
    갈렸다 — 🔴 **수를 손으로 세면 틀린다. 검사가 세게 한다**(로그 190).
    """
    untagged = {
        path
        for path, operations in _spec()["paths"].items()
        for operation in operations.values()
        if not operation.get("tags")
    }
    assert untagged == _NOT_A_OWNED, sorted(untagged ^ _NOT_A_OWNED)
    assert len(untagged) == 10, sorted(untagged)
