"""counsel 3엔드포인트의 **OpenAPI 계약** — BE 코드 생성이 가능해야 한다 (99 #99).

핸들러 반환이 `dict[str, Any]` 라 `/openapi.json` 의 counsel 스키마가 **비어 있었다**
(실측 8/19: `{"type":"object","additionalProperties":true}`) ⇒ 승우님이 클라이언트를
**코드 생성으로 못 만든다.** detect 축에는 있고 counsel 에만 없던 마지막 BE 표면 결손이다.

🔴 **끊긴 참조는 「문서 루트」에서 잰다** — detect 가 초판에서 `requestBody.schema` 안만
보다가 **끊긴 참조 19건을 놓쳤다**(2026-08-12 정정 이력). 그 판정 도구를
`test_detect_openapi_contract.py` 에서 **재사용**한다 — 같은 판정을 두 번 짜면 하나가 낡는다.

⚠ **런타임은 한 글자도 안 바뀌었다** — 데코레이터 인자만 늘었다. 그 사실을 아래 검사 3이
지킨다(핸들러 인자를 모델로 바꾸면 FastAPI 자동 검증이 **422**를 만들고 현행 400
`INVALID_SCHEMA` 가 사라진다).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any, Final

import pytest

from ai.api.app import create_app
from ai.api.routers.counsel_openapi import (
    COUNSEL_CREATE_OPERATION_ID,
    COUNSEL_GET_OPERATION_ID,
    COUNSEL_REFINE_OPERATION_ID,
)

_CREATE: Final = "/v1/counsel/drafts"
_GET: Final = "/v1/counsel/drafts/{job_id}"
_REFINE: Final = "/v1/counsel/drafts/{job_id}/refine"
_FIXTURE_DIR: Final = Path(__file__).parent / "fixtures" / "http"


def _document() -> dict[str, Any]:
    doc: dict[str, Any] = create_app().openapi()
    return doc


def _broken_refs_tool() -> ModuleType:
    """🔴 **detect 검사의 판정 도구를 그대로 쓴다** — 새로 짜지 않는다.

    ⚠ 그 파일은 같은 디렉터리라 평면 import 가 되지만, **경로로 읽어** pytest 수집 순서에
    안 기대게 한다.
    """
    target = Path(__file__).parent / "test_detect_openapi_contract.py"
    spec = importlib.util.spec_from_file_location("_detect_openapi_contract", target)
    assert spec is not None and spec.loader is not None, target
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ── 1 · 🔴 문서 루트에서 $ref 가 전부 해석된다 ────────────────────


def test_every_counsel_reference_resolves_from_the_document_root() -> None:
    """🔴 **detect 가 초판에서 19건을 놓친 그 검사다.**

    `requestBody.schema` **안**만 보면 `#/$defs/X` 가 거기 있으니 green 이다. 그런데
    그 참조는 **문서 루트** 기준으로 풀리고 루트에는 `$defs` 가 없다 ⇒ **끊긴다.**
    Swagger·generator 양쪽에서 터진다.
    """
    tools = _broken_refs_tool()
    document = _document()

    broken = tools._broken_refs(document)  # noqa: SLF001
    assert not broken, f"문서 루트에서 안 풀리는 참조: {broken}"

    #: 절단 가드 — counsel 경로를 실제로 훑었나. 참조가 0건이면 위 단언이 공허하다.
    counsel_refs = tools._refs_in(  # noqa: SLF001
        {path: document["paths"][path] for path in (_CREATE, _GET, _REFINE)}
    )
    assert counsel_refs, "counsel 경로에 참조가 하나도 없다 — 스키마가 안 붙었다"


def test_the_request_schemas_carry_no_defs_at_all() -> None:
    """요청 스키마는 **완전 인라인**이다 — `$defs`·`$ref` 0건.

    ⚠ 루트 `$defs` 주입·`components.schemas` 등록은 `api/app.py`(양자 승인)를 여는
    길이라 이 회차에서 고르지 않았다.
    """
    document = _document()
    for path, method in ((_CREATE, "post"), (_REFINE, "post")):
        body = document["paths"][path][method]["requestBody"]
        schema = body["content"]["application/json"]["schema"]
        assert "$defs" not in schema, path
        assert "$ref" not in str(schema), path
        assert schema.get("properties"), f"{path} 요청 스키마가 비었다"


# ── 2 · operation_id 는 계약이다 ─────────────────────────────────


def test_the_operation_ids_are_pinned() -> None:
    """🔴 **Java generator 의 메서드명이 된다** — 바뀌면 BE 클라이언트가 깨진다.

    자동 생성이면 `post_counsel_draft_v1_counsel_drafts_post` 가 되고, **함수 이름을
    바꾸는 순간** 그 메서드명이 따라 바뀐다.
    """
    document = _document()
    assert document["paths"][_CREATE]["post"]["operationId"] == "createCounselDraft"
    assert document["paths"][_GET]["get"]["operationId"] == "getCounselDraft"
    assert document["paths"][_REFINE]["post"]["operationId"] == "refineCounselDraft"
    #: 상수와 문서가 갈리지 않는다.
    assert COUNSEL_CREATE_OPERATION_ID == "createCounselDraft"
    assert COUNSEL_GET_OPERATION_ID == "getCounselDraft"
    assert COUNSEL_REFINE_OPERATION_ID == "refineCounselDraft"


# ── 3 · 🔴 런타임이 안 바뀌었다 ──────────────────────────────────


def test_a_missing_header_is_still_four_hundred_not_a_validation_error() -> None:
    """🔴 **함정 ①** — 핸들러 인자를 모델로 바꾸면 FastAPI 자동 검증이 **422**를 내고
    현행 400 `INVALID_SCHEMA` 가 **사라진다**(04 §2.4 · `error_codes` §2.1).

    ⚠ 이 검사가 없으면 이 PR 이 400을 죽였는지 아무도 모른다.
    """
    from fastapi.testclient import TestClient  # noqa: PLC0415

    with TestClient(create_app()) as client:
        response = client.post("/v1/counsel/drafts", json={}, headers={})

    assert response.status_code == 400, response.text
    assert response.json()["error"]["code"] == "INVALID_SCHEMA", response.json()


# ── 4 · 응답 코드 집합이 픽스처에서 파생된다 ─────────────────────


def _fixture_codes(prefix: str) -> set[str]:
    """픽스처 **파일 이름**에서 응답 코드를 뽑는다 — 손으로 나열하면 갈린다."""
    codes: set[str] = set()
    for path in _FIXTURE_DIR.glob(f"{prefix}.*.json"):
        part = path.name.removeprefix(f"{prefix}.").split(".")[0]
        if part.isdigit():
            codes.add(part)
        elif part != "request":
            codes.add("200")  # 이름이 판정인 GET 픽스처(generated·template_only 등)
    return codes


def test_the_documented_codes_cover_every_fixture() -> None:
    """🔴 픽스처가 낸 코드는 **전부** 문서에 있다 — 손으로 나열하면 갈린다.

    ⚠ **`==` 가 아니라 `⊇` 다.** 실측(8/19): refine 은 **404·409 픽스처가 없는데
    라우터는 둘 다 낸다**(`NotFound`·`IdempotencyConflict`). 픽스처 공백이지 문서 오류가
    아니라서 `==` 로 묶으면 **문서를 줄이는** 잘못된 방향으로 압력이 간다.
    ⇒ 대신 **문서에만 있는 코드를 명시**해 그 공백이 조용히 늘지 않게 한다.
    """
    document = _document()
    documented = {
        _CREATE: set(document["paths"][_CREATE]["post"]["responses"]),
        _GET: set(document["paths"][_GET]["get"]["responses"]),
        _REFINE: set(document["paths"][_REFINE]["post"]["responses"]),
    }
    from_fixtures = {
        _CREATE: _fixture_codes("post_counsel_drafts"),
        _GET: _fixture_codes("get_counsel_draft"),
        _REFINE: _fixture_codes("post_counsel_refine"),
    }

    for path, codes in from_fixtures.items():
        assert codes, f"{path} 픽스처에서 코드를 하나도 못 읽었다"
        assert codes <= documented[path], (
            f"{path}: 픽스처에 있는데 문서에 없는 코드 {sorted(codes - documented[path])}"
        )

    #: 🔴 **문서에만 있는 코드 — 늘면 red 다.** 각각 왜 픽스처가 없는지 아래에 적었다.
    extra = {path: sorted(documented[path] - from_fixtures[path]) for path in documented}
    #: 🔴 **예외 항마다 「왜 예외인가」와 「언제 없어지나」를 적는다**(로그 178).
    #:   둘 중 하나라도 못 적으면 그건 예외가 아니라 **미해소 안건**이었다.
    #: ✅ **(8/22 · №56) 셋이 다 비었다** — `404`·`409` 는 픽스처가 덮었고(№55),
    #:   `422` 는 `api/app.py` 후처리가 뗐다(99 #105 완전 해소).
    #: 🔴 **빈 목록을 남긴다 — 상수를 없애지 않는다.** 이 단언이 재는 것은 «지금 예외가
    #:   없다» 가 아니라 **«문서에만 있는 코드가 늘면 red»** 이고, 그 성질은 예외가 0개여도
    #:   그대로다. 없애면 다음에 새 코드가 문서에만 생겨도 **아무도 안 운다**(로그 149).
    #: ⚠ 그리고 `422` 가 되살아나면 여기가 red 다 — 후처리가 죽었다는 신호가 된다
    #:   (`test_unreachable_422_is_not_documented.py` 와 **두 층에서** 잡는다).
    assert extra == {
        _CREATE: [],
        _GET: [],
        _REFINE: [],
    }, extra


# ── 5 · 🔴 data 키 집합이 세 곳에서 다르다 ───────────────────────


def _resolved_data_props(document: dict[str, Any], path: str, method: str, code: str) -> set[str]:
    tools = _broken_refs_tool()
    schema = document["paths"][path][method]["responses"][code]["content"][
        "application/json"
    ]["schema"]
    envelope = tools._resolve(document, schema["$ref"])  # noqa: SLF001
    assert isinstance(envelope, dict), (path, code)
    data = envelope["properties"]["data"]
    if "$ref" in data:
        data = tools._resolve(document, data["$ref"])  # noqa: SLF001
    assert isinstance(data, dict), (path, code)
    return set(data.get("properties", {}))


def test_the_data_shape_differs_across_the_three_endpoints() -> None:
    """🔴 apidog 문서 §0-A 가 승우님께 경고한 계약 — **스키마에서 다시 잰다.**

    *"묶으면 202 에 없는 `result` 를 BE 가 기다립니다."*
    ⚠ PR-02 가 **픽스처**에서 같은 것을 쟀다 — 여기는 **문서**에서 잰다. 둘이 갈리면
    BE 가 보는 것(문서)과 실제(픽스처)가 다른 것이라 **두 층 다 필요하다.**
    """
    document = _document()

    assert _resolved_data_props(document, _CREATE, "post", "202") == {"job_id", "status"}
    assert _resolved_data_props(document, _GET, "get", "200") == {
        "job_id",
        "status",
        "result",
    }
    assert _resolved_data_props(document, _REFINE, "post", "200") == {
        "applied",
        "text",
        "citations",
        "blocked_reason",
    }


# ── 6 · 기존 픽스처가 그대로다 ───────────────────────────────────


def test_the_counsel_fixture_count_is_raised_by_hand() -> None:
    """counsel 픽스처 **개수 가드** — *"픽스처가 사라졌는데 문서만 늘었다"* 를 막는다.

    ⚠ 픽스처 **대조**는 `test_http_fixtures.py` 가 든다. 여기서는 **개수**만 본다.

    ⚠ 🔴 **(8/21) 이름을 고쳤다** — 종전 이름은 `test_this_change_did_not_touch_any_response`
    였고, 그건 **이 함수가 하는 일이 아니었다**(«어떤 PR 이 응답을 안 건드렸다» 는 그 PR
    한 번의 사실이고, 이 검사는 **개수를 손으로 올리게 하는 장치**다). 로그 154 가 8/10 에
    등재했고 그동안 안 고쳐졌다 — **숫자를 건드리는 이번 회차가 그 자리다.**
    🔴 옛 이름을 여기 남긴다 — 지우면 로그 154 를 찾는 사람이 이 함수에 못 닿는다.
    """
    counsel_fixtures = sorted(_FIXTURE_DIR.glob("*counsel*.json"))
    #: 🔴 **15 → 17 (8/21 · 99 #163)** — 바디 검증 400 의 **배열 detail** 을 덮으면서
    #: `post_counsel_drafts.400.body_schema` · `post_counsel_refine.400.body_schema` 둘이 늘었다.
    #: 🔴 **17 → 19 (8/21 · 99 #105)** — refine 의 **404·409** 를 덮었다.
    #: ⚠ `len(...)` 으로 빼지 않는다 — **손으로 올리는 것이 이 검사의 목적**이다(로그 145).
    assert len(counsel_fixtures) == 19, [p.name for p in counsel_fixtures]


@pytest.mark.parametrize(
    ("path", "method"), [(_CREATE, "post"), (_GET, "get"), (_REFINE, "post")]
)
def test_every_counsel_operation_is_tagged(path: str, method: str) -> None:
    """태그가 붙어 있다 — generator 가 클라이언트 클래스를 그걸로 가른다."""
    assert _document()["paths"][path][method]["tags"] == ["상담"]
