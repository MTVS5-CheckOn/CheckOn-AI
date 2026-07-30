"""실패 응답의 민감 detail 노출 차단 — 실제 HTTP 응답 JSON 검사 (TestClient).

사양: `docs/policies/error_codes.md` §4(`RedactionUncertain → 500 INTERNAL — 상세 사유
응답에 미포함`) · `docs/04_api_contract.md` §2.2·§2.3 · `docs/part_b/09_integration_proposals.md`
§2-11(민감 detail 강제 제거 `[P0]` — "통합 테스트 확인"까지가 조건이라 단위 mock으로
끝내지 않는다).

**두 방향을 함께 고정한다.**
① 5xx의 `error.detail`이 응답에 없다(원문 조각 유출 차단 — 핵심).
② 4xx의 `error.detail`은 **살아 있다**(필드 경로 등 — 과잉 차단 회귀 방지).
그리고 모든 실패에 `meta.versions`가 실리고(04 §2.2 A판정 7/22) `X-Request-Id`가 echo된다.

5xx 예외를 올리는 프로덕션 라우트가 아직 없어(감지는 결정론·원장 실패는 DB 필요) 공통
핸들러를 타는 프로브 라우트를 앱에 붙여 검사한다 — 검사 대상은 `create_app()`이 등록한
**실제 핸들러·실제 envelope**이며, 프로브는 예외 발생 지점 역할만 한다.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import Response

from ai.api.app import create_app
from ai.api.routers.detect import reset_detection_store, reset_idempotency_store
from ai.contracts.execution import VersionSet
from ai.runtime.errors import (
    LedgerWriteFailed,
    NotFound,
    RedactionUncertain,
)

#: 응답 어디에도 나타나면 안 되는 값 — 마스킹 실패 원문 조각을 모사한 합성 문자열.
#: (실 데이터 아님. 불변식 3의 검사 대상이 되도록 실명·연락처 형태를 흉내낸다.)
_SECRET = "홍길동 010-0000-0000"

_HEADERS = {"X-Request-Id": "req-detail-1"}


def _probe_app() -> FastAPI:
    """공통 핸들러를 그대로 쓰는 앱 + 예외 발생용 프로브 라우트."""
    app = create_app()

    @app.get("/_probe/redaction-uncertain")
    async def _redaction_uncertain() -> None:
        raise RedactionUncertain("마스킹 검증 실패", {"unmasked_fragment": _SECRET})

    @app.get("/_probe/ledger-write-failed")
    async def _ledger_write_failed() -> None:
        raise LedgerWriteFailed("원장 적재 실패", {"sql": f"INSERT ... '{_SECRET}'"})

    @app.get("/_probe/not-found")
    async def _not_found() -> None:
        raise NotFound("job_id 부재", {"job_id": "job-1"})

    return app


@pytest.fixture
def client() -> Iterator[TestClient]:
    reset_idempotency_store()
    reset_detection_store()
    yield TestClient(_probe_app())
    reset_idempotency_store()
    reset_detection_store()


def _versions_keys() -> set[str]:
    return {name.removesuffix("_version") for name in VersionSet.model_fields}


def _assert_common_failure_shape(response: Response) -> dict[str, Any]:
    """실패 응답의 공통 규약 — data null · meta.versions 전 키 · X-Request-Id echo."""
    body: dict[str, Any] = response.json()
    assert body["data"] is None
    assert set(body["meta"]["versions"]) == _versions_keys()  # 04 §2.2 (실패도 항상)
    assert response.headers["X-Request-Id"] == _HEADERS["X-Request-Id"]
    return body


# ── ① 5xx — detail 미노출 (핵심) ──────────────────────────────────


def test_redaction_uncertain_omits_detail(client: TestClient) -> None:
    """error_codes §4: RedactionUncertain의 상세 사유는 응답에 실리지 않는다."""
    response = client.get("/_probe/redaction-uncertain", headers=_HEADERS)

    assert response.status_code == 500
    body = _assert_common_failure_shape(response)
    assert body["error"]["code"] == "INTERNAL"
    assert body["error"]["detail"] is None
    # message는 남는다 — 없어지는 건 detail 한 축이다.
    assert body["error"]["message"] == "마스킹 검증 실패"
    # 응답 전문에 원문 조각이 없다 — detail 밖 경로(message·meta)로도 새지 않는다.
    assert _SECRET not in response.text


def test_ledger_write_failed_omits_detail(client: TestClient) -> None:
    """5xx 계열 전반 — RedactionUncertain 하나만 특별 취급하지 않는다."""
    response = client.get("/_probe/ledger-write-failed", headers=_HEADERS)

    assert response.status_code == 500
    body = _assert_common_failure_shape(response)
    assert body["error"]["code"] == "INTERNAL"
    assert body["error"]["detail"] is None
    assert _SECRET not in response.text


# ── ② 4xx — detail 유지 (과잉 차단 회귀 방지) ──────────────────────


def test_invalid_schema_keeps_detail(client: TestClient) -> None:
    """400 INVALID_SCHEMA의 필드 경로는 그대로 실린다 — 실제 프로덕션 라우트로 검사."""
    response = client.post("/v1/detect", headers=_HEADERS, json={})

    assert response.status_code == 400
    body = _assert_common_failure_shape(response)
    assert body["error"]["code"] == "INVALID_SCHEMA"
    # detect.py의 `SnapshotInvalid("필수 헤더 누락", {"missing_headers": [...]})`가 살아 있다.
    assert body["error"]["detail"] == {"missing_headers": ["X-Tenant-Id", "Idempotency-Key"]}


def test_not_found_keeps_detail(client: TestClient) -> None:
    """404도 4xx — 클라이언트가 고칠 정보(job_id)는 유지된다."""
    response = client.get("/_probe/not-found", headers=_HEADERS)

    assert response.status_code == 404
    body = _assert_common_failure_shape(response)
    assert body["error"]["code"] == "NOT_FOUND"
    assert body["error"]["detail"] == {"job_id": "job-1"}


# ── ③ 미분류 예외 — 기존 동작 유지 (건드리지 않았다는 회귀) ────────


def test_unhandled_exception_still_omits_detail() -> None:
    """`_unhandled`는 이미 detail을 싣지 않았다 — 이 PR로 바뀌지 않는다."""
    app = create_app()

    @app.get("/_probe/boom")
    async def _boom() -> None:
        raise RuntimeError(_SECRET)

    client = TestClient(app, raise_server_exceptions=False)
    response = client.get("/_probe/boom", headers=_HEADERS)

    assert response.status_code == 500
    body = response.json()
    assert body["error"] == {"code": "INTERNAL", "message": "내부 서버 오류", "detail": None}
    assert set(body["meta"]["versions"]) == _versions_keys()
    assert _SECRET not in response.text
