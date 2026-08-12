"""`/v1/detect`의 OpenAPI가 **BE 연동 계약**인가 (지시서 76).

🔴 **`/docs`가 있다고 문서가 있는 것은 아니다.** 실측(2026-08-12) 현행 상태:

| 축 | 값 |
| --- | --- |
| summary | `Post Detect` (함수명에서 자동 생성) |
| tags | 없음 |
| requestBody | **없음** — `DetectRequest`가 어디에도 안 보인다 |
| 필수 헤더 | **없음** — 셋 다 |
| 200 schema | `{"type": "object"}` (generic) |
| 400·409 | **없음** |

⇒ 경로 존재 확인에는 쓸 수 있어도 **Java DTO 생성이나 Try it out에는 못 쓴다.**

🔴 **원인은 handler가 `Request`를 직접 받는다는 것**이다 — FastAPI가 유추할 근거가 없다.
⚠ **그렇다고 `detect_request: DetectRequest`로 바꾸지 않는다**: FastAPI 자동 검증이 붙어
계약에 없는 **422**가 생기고, 현행 **400 `INVALID_SCHEMA`** 가 사라진다. 런타임은 그대로 두고
**OpenAPI 스키마만** `openapi_extra`로 잇는다.

⚠ **브라우저 화면 문자열·소스 grep으로 재지 않는다** — `create_app().openapi()`가 낸
**dict를 값으로** 본다.
"""

from __future__ import annotations

import ast
import json
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any, Final

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from ai.api.app import create_app
from ai.api.routers import detect as detect_router
from ai.api.routers import detect_openapi
from ai.api.routers.detect_openapi import (
    DETECT_OPERATION_ID,
    DetectErrorEnvelope,
    DetectSuccessEnvelope,
    documented_version_keys,
)
from ai.contracts.execution import VersionSet
from ai.db.store_factory import build_run_store
from ai.runtime.errors import LedgerWriteFailed

_PATH: Final = "/v1/detect"
_EXAMPLES: Final = (
    Path(__file__).resolve().parents[3] / "docs" / "part_a" / "examples"
)

#: 🔴 **계약이 요구하는 필수 헤더 셋** — 정확히 이만큼이다(더도 덜도 아니다).
_REQUIRED_HEADERS: Final = ("X-Tenant-Id", "X-Request-Id", "Idempotency-Key")

#: 문서화해야 하는 상태 — 04 계약과 `error_codes` §2.1에서 온다.
_DOCUMENTED_STATUSES: Final = ("200", "400", "409", "500")


@pytest.fixture(scope="module")
def spec() -> dict[str, Any]:
    """🔴 **생성된 OpenAPI** — 소스가 아니라 산출물을 본다."""
    return create_app().openapi()


@pytest.fixture(scope="module")
def operation(spec: dict[str, Any]) -> dict[str, Any]:
    post: dict[str, Any] = spec["paths"][_PATH]["post"]
    return post


# ───────────────────────── endpoint 메타 (§2) ─────────────────────────


def test_the_summary_is_written_not_generated(operation: dict[str, Any]) -> None:
    """🔴 `Post Detect`는 **함수명에서 자동 생성**된 것이다 — 사람이 쓴 문장이어야 한다."""
    summary = operation.get("summary")
    assert summary == "위험신호를 탐지한다", summary


def test_the_description_says_what_the_endpoint_guarantees(
    operation: dict[str, Any],
) -> None:
    """설명은 **불변식**을 말한다 — 근거 없는 신호는 없고, 판정을 LLM이 정하지 않는다."""
    description = operation.get("description") or ""
    for phrase in ("alias", "근거", "결정론", "LLM"):
        assert phrase in description, f"설명에 «{phrase}»가 없다: {description[:120]}"


def test_the_operation_is_tagged_and_has_a_stable_id(
    operation: dict[str, Any],
) -> None:
    """🔴 `operationId`는 **Java generator의 메서드명**이 된다 — 자동 생성이면 리팩터에 흔들린다."""
    assert operation.get("tags") == ["위험신호"], operation.get("tags")
    assert operation.get("operationId") == DETECT_OPERATION_ID
    assert operation["operationId"] == "detectRiskSignals"


# ───────────────────────── 요청 body (§3) ─────────────────────────


def test_the_request_body_is_documented(operation: dict[str, Any]) -> None:
    """🔴 `DetectRequest`가 Swagger에 보여야 한다 — 없으면 BE가 DTO를 못 만든다."""
    body = operation.get("requestBody")
    assert body is not None, "requestBody가 없다"
    assert body.get("required") is True
    schema = body["content"]["application/json"]["schema"]
    assert schema, "요청 스키마가 비었다"
    #: 최상위 필드 — 04 §2.4 요청 계약.
    properties = set(schema.get("properties", {}))
    assert {"snapshot_meta", "students", "learning_events"} <= properties, properties


def _refs_in(node: object) -> list[str]:
    """그 서브트리의 `$ref` 전수."""
    found: list[str] = []
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str):
            found.append(ref)
        for value in node.values():
            found.extend(_refs_in(value))
    elif isinstance(node, list):
        for item in node:
            found.extend(_refs_in(item))
    return found


def _resolve(document: dict[str, Any], ref: str) -> object | None:
    """🔴 **JSON Pointer를 문서 루트에서 실제로 따라간다** — 있다고 «믿지» 않는다."""
    if not ref.startswith("#/"):
        return None
    node: object = document
    for raw in ref.removeprefix("#/").split("/"):
        token = raw.replace("~1", "/").replace("~0", "~")
        if not isinstance(node, dict) or token not in node:
            return None
        node = node[token]
    return node


def _broken_refs(document: dict[str, Any]) -> list[str]:
    """🔴 **문서 루트 기준으로** 해석 안 되는 참조 전수.

    ⚠ **아래 두 검사가 이 함수 하나를 공유한다** — 판정을 약하게 고치면 «실제 문서»
    쪽만이 아니라 **합성 문서 검사가 red**가 된다(한쪽만 고쳐서 빠져나갈 수 없다).
    """
    return sorted({ref for ref in _refs_in(document) if _resolve(document, ref) is None})


def test_the_reference_check_itself_reads_from_the_document_root() -> None:
    """🔴 **절단 가드 — 검사 방식을 지킨다.**

    ⚠ 판정을 다시 «중첩 `$defs`만 보기»로 축소하면 실제 문서 쪽은 여전히 green이다
    (지금 코드가 인라인이라서). 그래서 **종전 결함을 그대로 재현한 합성 문서**를 넣어
    *"이것을 끊긴 것으로 보는가"* 를 묻는다 — 축소하면 여기서 red다.
    """
    #: `$defs`가 **요청 스키마 안에만** 있는 문서 — 종전 상태 그대로다.
    document: dict[str, Any] = {
        "openapi": "3.1.0",
        "paths": {
            "/x": {
                "post": {
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "$defs": {"Nested": {"type": "string"}},
                                    "properties": {"a": {"$ref": "#/$defs/Nested"}},
                                }
                            }
                        }
                    }
                }
            }
        },
    }
    assert _broken_refs(document) == ["#/$defs/Nested"], (
        "중첩 $defs 참조를 «해소된다»고 판정한다 — 검사가 문서 루트를 안 보고 있다"
    )


def test_every_ref_in_the_whole_document_resolves_from_the_root(
    spec: dict[str, Any],
) -> None:
    """🔴 **문서 루트 기준으로** 모든 `$ref`가 실제로 해석된다.

    ⚠ **종전 검사는 부족했다**(2026-08-12 실측). `requestBody.schema` 안의 `$defs`만 보고
    *"참조가 있다"* 고 판정했는데, `#/$defs/X`는 **그 스키마가 아니라 OpenAPI 문서 루트**를
    기준으로 해석된다. 문서 루트에 `$defs`가 없어서 **끊긴 참조가 19건**이었다 —
    검사는 green이고 Swagger·generator는 터지는 상태였다.
    """
    broken = _broken_refs(spec)
    assert not broken, f"문서 루트에서 해석 안 되는 참조 {len(broken)}건: {broken[:5]}"


def test_the_request_schema_has_no_local_defs_left(
    operation: dict[str, Any],
) -> None:
    """🔴 **절단 가드** — 요청 스키마에 `$ref`·`$defs`가 **하나도 남으면 안 된다.**

    ⚠ 위 검사만 두면 *"루트에 `$defs`를 심었다"* 로도 통과한다. 그건 `api/app.py`를 여는
    길이고 이 회차의 무접촉이다 — 여기서는 **완전 인라인**임을 못 박는다.
    ⚠ 문자열 치환으로 `$ref`만 지우면 참조가 가리키던 제약을 통째로 잃는다 ⇒ 아래
    「펼쳐졌는가」 검사가 짝이다.
    """
    schema = operation["requestBody"]["content"]["application/json"]["schema"]
    assert "$defs" not in schema, "요청 스키마에 $defs가 남았다"
    assert not _refs_in(schema), f"요청 스키마에 $ref가 남았다: {_refs_in(schema)[:3]}"


def test_the_inlined_schema_kept_the_nested_constraints(
    operation: dict[str, Any],
) -> None:
    """🔴 **참조를 지운 게 아니라 펼친 것**이다 — 중첩 타입의 제약이 살아 있다.

    ⚠ `$ref`만 삭제해도 위 두 검사는 통과한다. 그래서 **펼쳐진 내용**을 본다.
    """
    schema = operation["requestBody"]["content"]["application/json"]["schema"]
    snapshot = schema["properties"]["snapshot_meta"]
    assert "properties" in snapshot, f"중첩 타입이 비었다: {snapshot}"
    assert "week_start" in snapshot["properties"], sorted(snapshot["properties"])
    students = schema["properties"]["students"]["items"]
    assert "student_ref" in students.get("properties", {}), students


def test_a_recursive_model_fails_loudly_instead_of_expanding_forever() -> None:
    """🔴 재귀는 **조용히 진행하지 않는다** — 인라인이 무한히 펼쳐지기 때문이다."""
    from ai.api.routers.detect_openapi import (  # noqa: PLC0415
        RecursiveSchemaError,
        _inline_defs,
    )

    defs = {"Node": {"properties": {"child": {"$ref": "#/$defs/Node"}}}}
    with pytest.raises(RecursiveSchemaError, match="재귀"):
        _inline_defs({"$ref": "#/$defs/Node"}, defs)


def test_the_optional_evidence_and_hash_ownership_are_explained(
    operation: dict[str, Any],
) -> None:
    """⚠ **BE가 오해하기 쉬운 둘**을 문면에 못 박는다.

    ⓐ `snapshot_hash`는 **BE 선언값**이다 — AI가 재계산해 검증하지 않는다.
    ⓑ `detection_evidence`는 **선택**이고, 없으면 부재형 규칙이 발화하지 않는다.
    """
    description = operation.get("description") or ""
    assert "snapshot_hash" in description and "선언" in description, description[:200]
    assert "detection_evidence" in description, description[:200]
    assert "10주" in description, "rolling 10주 계약이 설명에 없다"


# ───────────────────────── 필수 헤더 (§4) ─────────────────────────


def test_exactly_three_required_headers_are_documented(
    operation: dict[str, Any],
) -> None:
    """🔴 **정확히 셋** — 더 적으면 Try it out이 400을 맞고, 더 많으면 없는 계약을 만든다."""
    parameters = operation.get("parameters") or []
    headers = {
        parameter["name"]: parameter
        for parameter in parameters
        if parameter.get("in") == "header"
    }
    assert tuple(headers) == _REQUIRED_HEADERS, tuple(headers)
    for name, parameter in headers.items():
        assert parameter.get("required") is True, f"{name}이 required가 아니다"
        assert parameter.get("description"), f"{name} 설명이 비었다"
        assert parameter["schema"].get("type") == "string", name


def test_the_idempotency_header_is_not_described_as_a_credential(
    operation: dict[str, Any],
) -> None:
    """⚠ **인증 토큰이 아니다** — 그렇게 읽히면 BE가 비밀 관리 경로에 넣는다.

    `Idempotency-Key`는 **같은 Kafka 이벤트면 같은 값**이고, `X-Request-Id`는
    **HTTP 시도마다 새 값**이다(#222 실통신 기록의 BE 체크리스트와 같은 문면).
    """
    parameters = {
        parameter["name"]: parameter["description"]
        for parameter in operation["parameters"]
        if parameter.get("in") == "header"
    }
    for name, description in parameters.items():
        for forbidden in ("인증", "API 키", "토큰"):
            assert forbidden not in description, f"{name}: {description}"
    assert "Kafka" in parameters["Idempotency-Key"], parameters["Idempotency-Key"]
    assert "시도" in parameters["X-Request-Id"], parameters["X-Request-Id"]


def test_no_security_scheme_was_invented(spec: dict[str, Any]) -> None:
    """⚠ 헤더를 문서화하다 **인증 스킴을 만들지 않았다** — 없는 계약이다."""
    assert "securitySchemes" not in spec.get("components", {})


# ───────────────────────── 응답 (§5) ─────────────────────────


@pytest.mark.parametrize("status", _DOCUMENTED_STATUSES)
def test_every_documented_status_has_a_schema(
    operation: dict[str, Any], status: str
) -> None:
    """🔴 200·400·409·500이 **전부** 문서에 있고 각각 스키마를 갖는다."""
    responses = operation["responses"]
    assert status in responses, f"{status}가 문서에 없다: {sorted(responses)}"
    content = responses[status].get("content", {})
    assert "application/json" in content, f"{status}에 JSON 스키마가 없다"
    assert content["application/json"].get("schema"), f"{status} 스키마가 비었다"


def test_the_success_response_carries_the_envelope_and_the_detect_payload(
    operation: dict[str, Any], spec: dict[str, Any]
) -> None:
    """🔴 정상 응답의 `data`가 **`DetectResponse`** 다 — generic object가 아니다."""
    schema = operation["responses"]["200"]["content"]["application/json"]["schema"]
    name = schema["$ref"].removeprefix("#/components/schemas/")
    envelope = spec["components"]["schemas"][name]
    assert set(envelope["properties"]) == {"data", "error", "meta"}, envelope[
        "properties"
    ]
    data = envelope["properties"]["data"]
    assert "DetectResponse" in json.dumps(data), data
    #: 🔴 **`DetectResponse` 본체가 components에 실려 있어야** BE가 DTO를 만든다.
    assert "DetectResponse" in spec["components"]["schemas"]


@pytest.mark.parametrize("status", ("400", "409", "500"))
def test_an_error_response_names_the_code_and_keeps_versions(
    operation: dict[str, Any], spec: dict[str, Any], status: str
) -> None:
    """🔴 오류에도 **`meta.versions`가 실린다**(04 §2.2 A판정) — 어느 버전이 거절했는지 안다."""
    schema = operation["responses"][status]["content"]["application/json"]["schema"]
    envelope = spec["components"]["schemas"][
        schema["$ref"].removeprefix("#/components/schemas/")
    ]
    assert set(envelope["properties"]) == {"data", "error", "meta"}
    error = json.dumps(envelope["properties"]["error"])
    assert "ErrorBody" in error or "code" in error, error


def test_the_error_body_has_code_message_detail(spec: dict[str, Any]) -> None:
    body = spec["components"]["schemas"]["DetectErrorBody"]
    assert set(body["properties"]) == {"code", "message", "detail"}, body["properties"]


def test_fastapi_did_not_add_its_own_validation_error(
    operation: dict[str, Any],
) -> None:
    """🔴 **422가 새로 생기면 안 된다** — 계약에 없는 상태다.

    ⚠ `detect_request: DetectRequest`로 바꾸면 FastAPI가 이걸 붙이고, 현행 400
    `INVALID_SCHEMA`가 사라진다. 그래서 런타임은 `Request`를 그대로 받는다.
    """
    assert "422" not in operation["responses"], sorted(operation["responses"])


# ───────────────────────── 예시 (§6) ─────────────────────────


def test_the_request_example_is_alias_only(operation: dict[str, Any]) -> None:
    """🔴 예시에 **실명·연락처·실 키가 없다**(불변식 3) — 문서도 경계 밖이다."""
    example = operation["requestBody"]["content"]["application/json"]["example"]
    text = json.dumps(example, ensure_ascii=False)
    for forbidden in ("010-", "@", "sk-", "Bearer "):
        assert forbidden not in text, f"예시에 «{forbidden}»가 있다"
    assert example["students"][0]["student_ref"].startswith("st_"), example["students"]


def test_the_example_points_at_the_canonical_sample_files(
    operation: dict[str, Any],
) -> None:
    """⚠ 10명 전체 fixture를 OpenAPI에 **복제하지 않는다** — 정본 파일로 안내한다."""
    description = operation.get("description") or ""
    assert "docs/part_a/examples/detect_demo_request.json" in description
    assert "docs/part_a/examples/detect_demo_response.json" in description


def test_the_inline_example_and_the_canonical_sample_do_not_drift(
    operation: dict[str, Any],
) -> None:
    """🔴 **둘이 갈리면 안 된다** — 인라인 예시는 정본 샘플의 **부분집합**이어야 한다.

    ⚠ 전문을 복제하면 정본이 둘이 되고, 아무 관계도 안 걸면 그날부터 따로 논다.
    ⇒ 형태(키 집합)와 `week_start`가 같은지 본다.
    """
    canonical = json.loads(
        (_EXAMPLES / "detect_demo_request.json").read_text(encoding="utf-8")
    )
    example = operation["requestBody"]["content"]["application/json"]["example"]
    assert set(example) <= set(canonical), set(example) - set(canonical)
    assert (
        example["snapshot_meta"]["week_start"]
        == canonical["snapshot_meta"]["week_start"]
    )
    assert len(example["students"]) < len(canonical["students"]), (
        "인라인 예시가 정본 전문을 복제했다"
    )


# ───────────────── 런타임은 한 글자도 안 바뀌었다 (§9) ─────────────────
#
# 🔴 **문서를 붙이다 응답을 바꾸는 것이 이 작업의 가장 큰 위험**이다. `openapi_extra`는
#    문서만 건드리는 경로지만, *"그럴 것이다"* 와 *"그렇다"* 는 다른 사실이라 실제로 친다.


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(create_app()) as test_client:
        yield test_client


def _canonical_request() -> dict[str, Any]:
    body: dict[str, Any] = json.loads(
        (_EXAMPLES / "detect_demo_request.json").read_text(encoding="utf-8")
    )
    return body


def _headers(key: str) -> dict[str, str]:
    return {
        "X-Tenant-Id": "t_openapi",
        "X-Request-Id": "rq-openapi-1",
        "Idempotency-Key": key,
    }


def test_the_success_path_returns_the_same_envelope_and_the_replay_matches_by_value(
    client: TestClient,
) -> None:
    """🔴 200 · `{data, error, meta}` · **멱등 재응답이 JSON 의미값으로 동일**하다.

    ⚠ **「바이트 동일」이라고 말하지 않는다**(2026-08-12 정정). 실측상 재응답은 캐시를
    거치며 **키 순서가 달라져** 바이트가 다르다 — 그건 이 PR이 만든 것이 아니고
    **develop에서도 같다.** 검사 이름과 문면이 실제 단정(`==` 의미값 비교)과 어긋나 있어
    바로잡았다: *"검사가 무엇을 보는가"* 를 잘못 적으면 다음 사람이 **없는 보장**을 믿는다.

    ⚠ **`response.content` 비교로 바꾸지 않는다** — 그러면 지금 결함(키 순서)을 이 PR의
    계약으로 끌어들이게 된다. 반대로 **바이트 차이를 고정하는 검사도 만들지 않는다**
    (별건으로만 기록한다).
    """
    body = _canonical_request()
    first = client.post("/v1/detect", json=body, headers=_headers("k-ok"))
    assert first.status_code == 200, first.text
    payload = first.json()
    assert set(payload) == {"data", "error", "meta"}, sorted(payload)
    assert payload["error"] is None
    assert payload["meta"]["versions"], "실패든 성공이든 versions는 실린다"

    again = client.post("/v1/detect", json=body, headers=_headers("k-ok"))
    assert again.status_code == 200
    replay = again.json()
    #: 🔴 세 축을 **각각** 본다 — dict 전체 `==` 하나면 어디가 갈렸는지 안 보인다.
    for axis in ("data", "error", "meta"):
        assert replay[axis] == payload[axis], f"멱등 재응답의 «{axis}»가 달라졌다"


@pytest.mark.parametrize(
    ("label", "headers", "body_key", "expected_status", "expected_code"),
    [
        ("헤더 누락", {"X-Tenant-Id": "t_openapi"}, "canonical", 400, "INVALID_SCHEMA"),
        ("스키마 위반", None, "broken", 400, "INVALID_SCHEMA"),
        ("멱등 충돌", None, "other_hash", 409, "IDEMPOTENCY_CONFLICT"),
    ],
)
def test_the_error_paths_still_return_400_and_409_not_422(
    client: TestClient,
    label: str,
    headers: dict[str, str] | None,
    body_key: str,
    expected_status: int,
    expected_code: str,
) -> None:
    """🔴 **422가 아니다.** 인자를 모델로 바꿨다면 여기가 전부 422로 뒤집힌다."""
    canonical = _canonical_request()
    if body_key == "canonical":
        body = canonical
        sent = headers or _headers("k-miss")
    elif body_key == "broken":
        body = {"nope": 1}
        sent = _headers("k-broken")
    else:
        client.post("/v1/detect", json=canonical, headers=_headers("k-conflict"))
        body = {
            **canonical,
            "snapshot_meta": {
                **canonical["snapshot_meta"],
                "snapshot_hash": "sha256:different",
            },
        }
        sent = _headers("k-conflict")

    response = client.post("/v1/detect", json=body, headers=sent)
    assert response.status_code == expected_status, f"{label}: {response.text[:200]}"
    assert response.json()["error"]["code"] == expected_code, label


# ───────────────── versions는 **실제 wire 값**과 같다 (76-R 작업 2) ─────────────────
#
# 🔴 **손으로 적었더니 틀렸다**(2026-08-12 실측). 문서 모델이 없는 키 셋
#    (`threshold_config`·`lexicon`·`tone_map`)을 적고 있는 키 둘(`verify_config`·
#    `difficulty_calib`)을 빠뜨렸다 — **문서가 wire와 다른 계약을 말하고 있었다.**
#    ⇒ 손으로 만든 샘플만 보면 같은 오류를 반복한다. **실제 응답으로** 검증한다.


def test_the_documented_version_keys_are_derived_from_the_contract() -> None:
    """🔴 문서 키 집합 == `VersionSet`에서 `_version`을 뗀 이름."""
    expected = {
        name.removesuffix("_version") for name in VersionSet.model_fields
    }
    assert documented_version_keys() == expected, sorted(
        documented_version_keys() ^ expected
    )


def test_the_live_success_response_validates_against_the_documented_model(
    client: TestClient,
) -> None:
    """🔴 **실제 200 응답**을 문서용 성공 모델로 검증한다.

    ⚠ 이게 이번 결함을 잡는 검사다 — 손으로 만든 샘플은 내 오해를 그대로 통과시켰다.
    `extra="forbid"`라 **응답에만 있는 키**도, **문서에만 있는 키**도 여기서 걸린다.
    """
    response = client.post(
        "/v1/detect", json=_canonical_request(), headers=_headers("k-model")
    )
    assert response.status_code == 200, response.text
    DetectSuccessEnvelope.model_validate(response.json())


@pytest.mark.parametrize(
    ("label", "headers", "body_kind", "status"),
    [
        ("헤더 누락", {"X-Tenant-Id": "t_openapi"}, "canonical", 400),
        ("스키마 위반", None, "broken", 400),
        ("멱등 충돌", None, "other_hash", 409),
    ],
)
def test_the_live_error_responses_validate_and_always_carry_versions(
    client: TestClient,
    label: str,
    headers: dict[str, str] | None,
    body_kind: str,
    status: int,
) -> None:
    """🔴 **오류 응답에 `meta`가 항상 있다** — 문서에서 `None` 허용을 지운 근거다.

    ⚠ `error_envelope()`는 호출자가 `versions`를 안 넘기면 `meta=None`을 낼 수 있지만
    **`/v1/detect`는 셋 다 넘긴다.** 문서가 `None`을 열어 두면 BE가 «없어도 되는 값»으로
    읽는다 — 그래서 실제 응답으로 확인한다.
    """
    canonical = _canonical_request()
    if body_kind == "canonical":
        body, sent = canonical, headers or _headers("k-err-miss")
    elif body_kind == "broken":
        body, sent = {"nope": 1}, _headers("k-err-broken")
    else:
        client.post("/v1/detect", json=canonical, headers=_headers("k-err-conflict"))
        body = {
            **canonical,
            "snapshot_meta": {
                **canonical["snapshot_meta"],
                "snapshot_hash": "sha256:different-2",
            },
        }
        sent = _headers("k-err-conflict")

    response = client.post("/v1/detect", json=body, headers=sent)
    assert response.status_code == status, f"{label}: {response.text[:200]}"
    payload = response.json()
    assert payload["meta"] is not None, f"{label}: 오류에 meta가 없다"
    assert set(payload["meta"]["versions"]) == documented_version_keys(), label
    #: 🔴 문서 모델로 **실제 오류 본문**을 검증한다.
    DetectErrorEnvelope.model_validate(payload)


def test_the_documented_error_envelope_requires_meta() -> None:
    """🔴 `meta=None`을 **거절**한다 — 허용을 되돌리면 여기서 red."""
    with pytest.raises(ValidationError):
        DetectErrorEnvelope.model_validate(
            {"data": None, "error": {"code": "X", "message": "y", "detail": None},
             "meta": None}
        )


# ───────────────── 실제 500 응답 (76-R2 작업 3) ─────────────────
#
# 🔴 **문서는 「500도 `meta.versions`를 항상 싣는다」고 확정한다.** 그런데 실제 wire 검사는
#    200·400·409만 탔다 — 그 확정을 **아무도 안 보고 있었다.**
# ⚠ 손으로 만든 dict로 대신하지 않는다. 원장 적재를 실제로 터뜨려 **HTTP 응답**을 본다.


class _LedgerFailsStore:
    """`persist_ledger`만 터지는 감지 원장 대역 — 🔴 **fail-closed**인지 재려는 것이다.

    ⚠ `load_feature_weeks`는 정상이어야 한다 — baseline 조회에서 먼저 죽으면 재려는
    자리(원장 적재)에 **도달하지 않는다**.
    """

    def __init__(self) -> None:
        self.persist_calls = 0

    async def persist_ledger(self, ledger: object) -> None:
        del ledger
        self.persist_calls += 1
        raise LedgerWriteFailed(
            "감지 원장 적재 실패(대역)", {"table": "signal"}
        )

    async def load_feature_weeks(
        self, tenant_id: str, student_refs: Sequence[str], *, feature_version: str
    ) -> tuple[Any, ...]:
        del tenant_id, student_refs, feature_version
        return ()


@pytest.fixture
def ledger_failure() -> Iterator[_LedgerFailsStore]:
    """원장만 터지게 바꿔 끼우고 **끝나면 되돌린다** — 다음 검사로 새면 안 된다."""
    broken = _LedgerFailsStore()
    #: ⚠ 대역이 `DetectionStore` Protocol을 구조적으로 만족한다 — cast가 필요 없다.
    detect_router.set_detection_store(broken)
    try:
        yield broken
    finally:
        detect_router.reset_detection_store()
        detect_router.reset_idempotency_store()
        detect_router.set_detect_run_store(build_run_store())


#: 🔴 **두 모드 모두 잰다** — 아래 docstring의 정정 사유를 값으로 고정한다.
_CLIENT_MODES: Final = ((True, "기본"), (False, "예외 미전파"))


@pytest.mark.parametrize(("reraise", "label"), _CLIENT_MODES)
def test_a_real_500_carries_the_documented_error_envelope(
    ledger_failure: _LedgerFailsStore, reraise: bool, label: str
) -> None:
    """🔴 **실제 500**이 문서 계약과 같은가 — `meta.versions`까지.

    ⚠ **정정(2026-08-12)** — 처음엔 *"`raise_server_exceptions=False`가 필수다. 기본
    `TestClient`는 예외를 재전파한다"* 고 적었는데 **이 앱에서는 틀렸다**:
    `LedgerWriteFailed`는 `DomainException`이고 `api/app.py`에 **핸들러가 등록**돼 있어
    **두 모드 모두 500 응답**이 된다(재전파는 *처리되지 않은* 예외에만 해당한다).
    ⇒ 그래서 **두 모드를 함께 잰다** — 없는 이유를 근거로 쓰지 않는다.

    🔴 **핸들러가 사라지면 여기서 걸린다** — Starlette 기본 500은 envelope가 아니라 평문
    `Internal Server Error`라 아래 모델 검증이 red다.
    """
    with TestClient(create_app(), raise_server_exceptions=reraise) as client:
        response = client.post(
            #: ⚠ 멱등 키는 **HTTP 헤더**라 ASCII만 된다 — 한국어 라벨을 쓰면
            #:   `UnicodeEncodeError`다(실측). 라벨은 실패 문면에만 쓴다.
            "/v1/detect",
            json=_canonical_request(),
            headers=_headers(f"k-500-{int(reraise)}"),
        )

    assert response.status_code == 500, f"{label}: {response.text[:200]}"
    assert ledger_failure.persist_calls == 1, "원장 적재에 도달하지 않았다"

    payload = response.json()
    #: 🔴 문서 모델로 **실제 본문**을 검증한다.
    DetectErrorEnvelope.model_validate(payload)
    assert payload["error"]["code"] == "INTERNAL", payload["error"]["code"]
    assert payload["meta"] is not None, "500에 meta가 없다"
    assert set(payload["meta"]["versions"]) == documented_version_keys()


def test_the_500_body_does_not_leak_internals(
    ledger_failure: _LedgerFailsStore,
) -> None:
    """🔴 스택·SQL·접속정보가 응답에 실리면 안 된다(불변식 3의 같은 방향)."""
    del ledger_failure
    with TestClient(create_app(), raise_server_exceptions=False) as client:
        response = client.post(
            "/v1/detect", json=_canonical_request(), headers=_headers("k-500-leak")
        )
    text = response.text
    for forbidden in (
        "Traceback",
        "postgresql+asyncpg",
        "asyncpg",
        "INSERT INTO",
        "site-packages",
        "/Users/",
    ):
        assert forbidden not in text, f"500 본문에 «{forbidden}»가 있다"


def test_the_500_really_came_from_the_http_layer(
    ledger_failure: _LedgerFailsStore,
) -> None:
    """🔴 **절단 가드** — 위 검사가 «함수 호출»이 아니라 **HTTP 응답**을 본다.

    ⚠ 원장 대역이 안 불렸으면 500은 **다른 이유**로 난 것이고(예: 입력이 애초에 400),
    그러면 위 검사는 재려던 자리를 본 적이 없다. 도달 횟수와 상태·헤더를 함께 본다.
    """
    with TestClient(create_app(), raise_server_exceptions=False) as client:
        response = client.post(
            "/v1/detect", json=_canonical_request(), headers=_headers("k-500-layer")
        )
    assert ledger_failure.persist_calls == 1, "원장 적재에 도달하지 않았다"
    assert response.status_code == 500
    #: 🔴 HTTP를 지났다는 증거 — 미들웨어가 상관 ID를 되울린다(`app.py::_echo_request_id`).
    assert response.headers.get("X-Request-Id") == "rq-openapi-1", dict(response.headers)
    assert response.headers["content-type"].startswith("application/json")


# ───────────────── 현재 문면 가드 (76-R2 작업 4) ─────────────────
#
# 🔴 **이 PR에서 문면이 두 번 낡았다.** ⓐ 모듈 상단이 «`$defs` 동봉이면 유효하다»로 남았고
#    ⓑ 멱등 검사 이름이 «바이트 동일»이라 실제 단정(의미값 비교)과 어긋났다.
#    **문면이 코드보다 넓게 약속하면 다음 사람이 없는 보장을 믿는다.**
#
# ⚠ **파일 전체 grep을 하지 않는다** — 과거 오판은 «정정» 문맥으로 **보존**해야 한다.
#    현재 표면(모듈 docstring · 테스트 이름)만 좁게 본다.


def _module_docstring() -> str:
    """`detect_openapi.py`의 **모듈 docstring만** — 함수 주석·과거 기록은 안 본다."""
    tree = ast.parse(
        Path(str(detect_openapi.__file__)).read_text(encoding="utf-8")
    )
    return ast.get_docstring(tree) or ""


def test_the_module_docstring_describes_full_inlining_not_bundled_defs() -> None:
    """🔴 모듈 상단이 **현재 구현**(완전 인라인)을 말한다.

    ⚠ 초판 판단(`$defs` 동봉)을 현재형으로 되돌리면 red다. **과거형 정정 문장은 허용**한다 —
    지우면 다음 사람이 *"왜 인라인인가"* 를 다시 판단한다(99 ㊩).
    """
    text = _module_docstring()
    assert "완전히 인라인" in text, text[:200]
    assert "끊긴 참조" in text, "왜 인라인인지(루트 참조 결손)가 없다"
    #: 🔴 현재형 주장으로 남아 있으면 red — 과거형 정정은 「초판」 표시가 있어야 한다.
    stale = "요청 스키마는 자기 완결이다"
    assert stale not in text, f"초판 문면이 현재형으로 남았다: «{stale}»"
    assert "초판 판단 정정" in text, "과거 오판이 정정 문맥 없이 사라졌다"


def test_no_test_name_claims_byte_equality() -> None:
    """🔴 **검사 이름이 실제로 단정하는 것보다 넓으면 안 된다.**

    ⚠ 멱등 재응답은 JSON 의미값으로 같고 **바이트로는 다르다**(키 순서 · develop도 동일).
    이름에 «byte»를 넣으면 없는 보장을 약속하는 것이다.
    """
    tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
    offenders = [
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
        and "byte" in node.name.lower()
        #: ⚠ **가드 자신을 제외한다** — 첫 판이 자기 이름을 잡아 red였다(로그 85 계열:
        #:   「검사의 이름이 보는 것보다 넓다」). 이름으로만 빼고 규칙은 안 넓힌다.
        and node.name != "test_no_test_name_claims_byte_equality"
    ]
    assert not offenders, f"«바이트 동일»을 주장하는 검사 이름: {offenders}"


def test_the_idempotent_replay_check_compares_by_value() -> None:
    """🔴 **짝 가드** — 이름만 고치고 단정을 `content` 비교로 바꾸면 red.

    ⚠ `response.content` 비교로 바꾸면 지금 결함(키 순서)을 이 PR의 계약으로 끌어들인다.
    ⚠ 반대로 **바이트 차이를 고정하는 검사도 만들지 않는다** — 별건으로만 기록한다.
    """
    tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
    target = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
        and node.name.endswith("the_replay_matches_by_value")
    )
    body = ast.dump(target)
    assert "'content'" not in body, "멱등 검사가 바이트를 비교한다"
    assert "'json'" in body, "멱등 검사가 JSON 값을 안 읽는다"
