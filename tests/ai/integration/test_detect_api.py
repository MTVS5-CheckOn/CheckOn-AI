"""POST /v1/detect HTTP 통합 — envelope·검증·멱등·결정론 (TestClient).

사양: docs/04_api_contract.md §2.1·§2.2·§2.3 · docs/part_a/09_detect_spec.md §3.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from ai.api.app import create_app
from ai.api.routers.detect import reset_detection_store, reset_idempotency_store
from ai.composition.briefing import PROMPT_VERSION as BRIEF_PROMPT_VERSION
from ai.contracts.execution import VersionSet
from ai.evaluation.fake_snapshot import fixture_composite_risk, to_payload

_HEADERS = {
    "X-Tenant-Id": "teacher_alias_001",
    "X-Request-Id": "req-1",
    "Idempotency-Key": "teacher_alias_001:2026-07-13",
}


@pytest.fixture
def client() -> Iterator[TestClient]:
    reset_idempotency_store()  # 테스트 간 저장소 격리
    reset_detection_store()
    yield TestClient(create_app())
    reset_idempotency_store()
    reset_detection_store()


def _payload() -> dict[str, Any]:
    return to_payload(fixture_composite_risk())


def test_success_envelope_shape(client: TestClient) -> None:
    """정상 200 — envelope 형태·versions 키 집합·signals."""
    resp = client.post("/v1/detect", json=_payload(), headers=_HEADERS)
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {"data", "error", "meta"}
    assert body["error"] is None
    assert set(body["data"]) == {"signals", "stats"}
    assert "execution_id" in body["meta"]
    # meta.versions 키 = VersionSet 필드에서 _version 뗀 집합
    expected_keys = {name.removesuffix("_version") for name in VersionSet.model_fields}
    assert set(body["meta"]["versions"]) == expected_keys
    # detection 실행 — threshold는 config 버전, B 전용 키는 null
    # v2 — R1 발동률 목표 전환(04 §1 재정의 · 2026-08-03). 거동 변경이라 config_version 인상.
    assert body["meta"]["versions"]["threshold"] == "default-v3"
    # 🔴 **기대값이 8/8에 바뀌었다 — 종전 `is None`은 낡은 계약을 굳히고 있었다.**
    #    04 §2.2가 `prompt`를 "LLM 미사용 실행(감지·진단)에서 null"이라고 적었는데 **감지는
    #    LLM을 쓴다** — 브리핑 문장화(ⓐ)가 선형 LLM 1콜이고 같은 문서 §1·`brief` 설명이
    #    그렇게 적고 있다. 그 괄호는 브리핑이 붙기 전에 쓰였다. 결과로 응답도 `AI_RUN`도
    #    `prompt=null`인 채 프롬프트 0.2를 쓰고 있었다(불변식 8 — 원장에서 못 읽는다).
    #    ⚠ 값은 `briefing.PROMPT_VERSION`이 정본이다. 여기 리터럴을 적지 않는다(#118).
    assert body["meta"]["versions"]["prompt"] == BRIEF_PROMPT_VERSION
    assert body["meta"]["versions"]["graph"] is None


def test_missing_header_400(client: TestClient) -> None:
    headers = {k: v for k, v in _HEADERS.items() if k != "X-Tenant-Id"}
    resp = client.post("/v1/detect", json=_payload(), headers=headers)
    assert resp.status_code == 400
    body = resp.json()
    assert body["error"]["code"] == "INVALID_SCHEMA"
    assert "X-Tenant-Id" in body["error"]["detail"]["missing_headers"]


def test_error_envelope_carries_versions(client: TestClient) -> None:
    """실패 응답에도 meta.versions가 10키 집합으로 실린다 (04 §2.2 A판정 7/22)."""
    headers = {k: v for k, v in _HEADERS.items() if k != "X-Tenant-Id"}
    resp = client.post("/v1/detect", json=_payload(), headers=headers)
    body = resp.json()
    assert body["meta"] is not None, "실패에도 meta는 null이 아니어야 한다"
    expected_keys = {name.removesuffix("_version") for name in VersionSet.model_fields}
    assert set(body["meta"]["versions"]) == expected_keys
    assert body["meta"]["execution_id"] is None  # 실행 전 오류 — execution_id 없음


def test_x_request_id_echoed(client: TestClient) -> None:
    """X-Request-Id를 응답 헤더로 echo (성공·실패 모두)."""
    ok = client.post("/v1/detect", json=_payload(), headers=_HEADERS)
    assert ok.headers.get("X-Request-Id") == _HEADERS["X-Request-Id"]
    bad_headers = {k: v for k, v in _HEADERS.items() if k != "X-Tenant-Id"}
    bad = client.post("/v1/detect", json=_payload(), headers=bad_headers)
    assert bad.headers.get("X-Request-Id") == bad_headers["X-Request-Id"]


def test_extra_field_400_with_path(client: TestClient) -> None:
    payload = _payload()
    payload["snapshot_meta"]["injected"] = "x"
    resp = client.post("/v1/detect", json=payload, headers=_HEADERS)
    assert resp.status_code == 400
    body = resp.json()
    assert body["error"]["code"] == "INVALID_SCHEMA"
    fields = {item["field"] for item in body["error"]["detail"]}
    assert any("injected" in f for f in fields)


def test_missing_required_body_field_400(client: TestClient) -> None:
    payload = _payload()
    del payload["snapshot_meta"]["week_start"]
    resp = client.post("/v1/detect", json=payload, headers=_HEADERS)
    assert resp.status_code == 400
    fields = {item["field"] for item in resp.json()["error"]["detail"]}
    assert any("week_start" in f for f in fields)


def test_idempotent_same_body_returns_cached(client: TestClient) -> None:
    """같은 키 + 같은 바디 = 기존 결과 재반환(execution_id까지 동일)."""
    first = client.post("/v1/detect", json=_payload(), headers=_HEADERS).json()
    second = client.post("/v1/detect", json=_payload(), headers=_HEADERS).json()
    assert first == second


def test_idempotent_conflict_409(client: TestClient) -> None:
    """같은 키 + 다른 바디 = 409 IDEMPOTENCY_CONFLICT."""
    client.post("/v1/detect", json=_payload(), headers=_HEADERS)
    other = to_payload(fixture_composite_risk(seed=99))  # 다른 snapshot_hash
    resp = client.post("/v1/detect", json=other, headers=_HEADERS)
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"


def test_determinism_same_signals_across_keys(client: TestClient) -> None:
    """같은 바디, 다른 Idempotency-Key = 같은 signals, execution_id만 다름."""
    payload = _payload()
    r1 = client.post(
        "/v1/detect", json=payload, headers={**_HEADERS, "Idempotency-Key": "k1"}
    ).json()
    r2 = client.post(
        "/v1/detect", json=payload, headers={**_HEADERS, "Idempotency-Key": "k2"}
    ).json()
    assert r1["data"] == r2["data"]  # signals·stats 동일
    assert r1["meta"]["execution_id"] != r2["meta"]["execution_id"]


# ── (지시서 70-R) `detection_evidence` 위반이 실제로 400인가 (99 #43·#45) ──

#: 🔴 **모델에서 `ValidationError`가 난다는 것은 HTTP 400을 증명하지 않는다.**
#:   라우터가 그것을 어떤 코드로 번역하는지는 **응답으로만** 알 수 있다.
_EVIDENCE_VIOLATIONS: dict[str, dict[str, Any]] = {
    "제출이 예정 초과": {
        "kind": "assignment_window",
        "source_table": "assignment_week_summary",
        "record_id": "aws_bad",
        "week_start": "2026-07-13",
        "expected_count": 1,
        "submitted_count": 2,
    },
    "알 수 없는 kind": {
        "kind": "mystery",
        "source_table": "assignment_week_summary",
        "record_id": "aws_bad",
        "week_start": "2026-07-13",
        "expected_count": 1,
        "submitted_count": 0,
    },
    "kind/source_table 불일치": {
        "kind": "assignment_window",
        "source_table": "student_status_history",
        "record_id": "aws_bad",
        "week_start": "2026-07-13",
        "expected_count": 1,
        "submitted_count": 0,
    },
    "미래 집계": {
        "kind": "weekly_activity",
        "source_table": "student_week_activity",
        "record_id": "swa_bad",
        "week_start": "2026-12-28",
        "activity_count": 0,
        "enrolled_seconds": 604800,
    },
    "timezone 없는 전환": {
        "kind": "enrollment_transition",
        "source_table": "student_status_history",
        "record_id": "ssh_bad",
        "occurred_at": "2026-07-13T09:00:00",
        "from_status": "paused",
        "to_status": "returned",
    },
    "상태와 복귀 전환 불일치": {
        "kind": "enrollment_transition",
        "source_table": "student_status_history",
        "record_id": "ssh_bad",
        "occurred_at": "2026-07-13T09:00:00+09:00",
        "from_status": "paused",
        "to_status": "returned",
    },
}


@pytest.mark.parametrize("case", sorted(_EVIDENCE_VIOLATIONS))
def test_detection_evidence_violations_are_http_400(
    client: TestClient, case: str
) -> None:
    """🔴 여섯 위반이 **실제 `/v1/detect`에서 400 `INVALID_SCHEMA`** 로 수렴한다 (99 #45).

    ⚠ 지시서 70 §9는 `submitted > expected`를 **422**로 적었지만, `error_codes` §1의
    **7/22 A판정**이 *"바디 스키마 위반은 헤더 누락·JSON 파싱과 함께 하나의 400"* 으로 이미
    확정했다. 🔴 **같은 배열의 위반이 상태 코드 둘로 갈리면** BE가 *"어떤 위반이 422인가"* 를
    따로 알아야 한다 ⇒ **기존 상위 계약을 그대로 적용**한다(#45 해소 · 새 판정 없음).
    """
    payload = _payload()
    row = dict(_EVIDENCE_VIOLATIONS[case])
    row["student_ref"] = payload["students"][0]["student_ref"]
    payload["detection_evidence"] = [row]

    resp = client.post("/v1/detect", json=payload, headers=_HEADERS)
    assert resp.status_code == 400, f"{case}: {resp.status_code} — {resp.text[:300]}"
    body = resp.json()
    assert body["error"]["code"] == "INVALID_SCHEMA", body["error"]
    assert body["data"] is None


def test_a_valid_evidence_request_is_still_202(client: TestClient) -> None:
    """🔴 **절단 가드** — 위 여섯이 「무엇을 보내도 400」이라서 통과한 것이 아니다."""
    payload = _payload()
    assert payload["detection_evidence"], "픽스처가 근거를 안 만든다 — 축이 비었다"
    resp = client.post("/v1/detect", json=payload, headers=_HEADERS)
    assert resp.status_code == 200, resp.text
