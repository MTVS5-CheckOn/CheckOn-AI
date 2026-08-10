"""캐시가 비어도 **라우터가 PG에서 살려내는가** — ㉿ 네 증상의 실측 축.

🔴 **저장소를 만든 것으로는 아무것도 안 고쳐진다.** `PgCounselDraftViewStore`가 있어도
`_view_cache`·`_drafts`가 여전히 **유일한 정본**이면 ㉿는 그대로다(99 #22가 등재한 형태 —
정의·마이그레이션까지 있는데 **프로덕션 소비가 0**). 이 파일이 그 **호출자 0**을 red로 만든다.

**캐시를 비우는 것이 재시작의 대역이다** — 프로세스 공용 LRU라 비우면 남는 것은 PG뿐이다.
축출(㉿ ⓐ)과 재시작(ⓒ)은 캐시 관점에서 **같은 상태**이므로 한 번에 본다.

🔴 **읽기 모델 저장소만 PG로 주입한다 — 앱 전체를 `STORE_BACKEND=pg`로 돌리지 않는다.**
전면 플립을 시도했더니 **이 축과 무관한 FK 순서**에서 죽었다(실측):
`agent_run.run_id → ai_run` 위반 — 잡 행이 실행 원장 행보다 먼저 들어간다.
그건 **플립 점검표의 안건**이고 이 PR의 축이 아니다. 섞으면 read-model이 살아났는지
아닌지를 **그 실패가 가린다.** ⇒ 다른 저장소는 memory로 두고 **한 축만 바꾼다.**
⚠ 팩토리가 `store_backend`로 고르는지는 별도 단위 검사가 본다.

PG가 없으면 skip이고, 그때 이 파일은 **아무것도 안 본 것**이다.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from typing import Any, Final

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from ai.api.app import create_app
from ai.api.routers import counsel as counsel_router
from ai.api.routers.counsel import reset_counsel_stores, set_counsel_draft_view_store
from ai.db.counsel_read_model import PgCounselDraftViewStore
from ai.db.settings import get_db_settings
from ai.db.store_factory import reset_shared_agent_runtime

pytestmark = pytest.mark.integration

_TENANT: Final = "t_readmodel_router"
_HEADERS: Final = {
    "X-Tenant-Id": _TENANT,
    "X-Request-Id": "rq-readmodel-1",
    "Idempotency-Key": f"{_TENANT}:counsel:1",
}


async def _query(statement: str, **params: object) -> object:
    """🔴 **async 드라이버로만 붙는다** — 동기 드라이버는 이 저장소에 설치돼 있지 않고,
    그걸 쓰면 이 파일 전체가 **조용히 skip**된다(실측: 6건 skip).
    """
    engine = create_async_engine(get_db_settings().database_url, poolclass=NullPool)
    try:
        async with engine.begin() as conn:
            return (await conn.execute(text(statement), params)).scalar_one()
    finally:
        await engine.dispose()


def _pg_available() -> bool:
    try:
        asyncio.run(_query("SELECT 1"))
    except Exception:  # noqa: BLE001 — 접속 실패 종류를 가리지 않는다
        return False
    return True


def _truncate() -> None:
    asyncio.run(
        _query(
            "WITH x AS (DELETE FROM counsel_draft_view WHERE tenant_id = :t "
            "RETURNING 1) SELECT count(*) FROM x",
            t=_TENANT,
        )
    )


@pytest.fixture
def pg_client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    if not _pg_available():
        pytest.skip("실 PG 미가용 — docker compose up -d")
    del monkeypatch
    reset_shared_agent_runtime()
    reset_counsel_stores()
    #: 🔴 **테스트 전용 엔진이다** — 공용 `get_sessionmaker()`는 처음 쓴 이벤트 루프에 묶이고
    #: `TestClient`는 테스트마다 새 루프를 연다. 공용을 쓰면 두 번째 테스트부터
    #: `attached to a different loop`로 죽는다(실측). `NullPool`로 접속도 안 물고 있는다.
    engine = create_async_engine(get_db_settings().database_url, poolclass=NullPool)
    set_counsel_draft_view_store(
        PgCounselDraftViewStore(async_sessionmaker(engine, expire_on_commit=False))
    )
    _truncate()
    try:
        with TestClient(create_app()) as client:
            yield client
    finally:
        _truncate()
        asyncio.run(engine.dispose())
        reset_shared_agent_runtime()
        reset_counsel_stores()


def _request_body() -> dict[str, Any]:
    """🔴 **계약 §4-① 예시를 복제하지 않는다** — 기존 통합 테스트가 든 정본을 재사용한다.

    ⚠ 평면 import다(`tests/ai/integration`은 패키지가 아니다 · `pythonpath` 규약).
    """
    from test_counsel_router import _REQUEST  # noqa: PLC0415

    return dict(_REQUEST)


def _post(client: TestClient) -> str:
    response: httpx.Response = client.post(
        "/v1/counsel/drafts", json=_request_body(), headers=_HEADERS
    )
    assert response.status_code == 202, response.text
    job_id: str = response.json()["data"]["job_id"]
    return job_id


def _forget_caches() -> None:
    """재시작·축출의 대역 — 프로세스 캐시만 비운다. **PG는 그대로 둔다.**"""
    counsel_router._view_cache.clear()
    counsel_router._drafts.clear()


def test_the_read_model_store_under_test_is_the_pg_one(pg_client: TestClient) -> None:
    """🔴 절단 가드 — Null 구현이 꽂혀 있으면 이 파일 전체가 아무것도 안 본다."""
    assert isinstance(
        counsel_router._draft_view_store, PgCounselDraftViewStore
    ), "읽기 모델 저장소가 PG가 아니다 — 이 파일은 캐시만 재고 있다"
    del pg_client


def test_the_row_is_written_on_post(pg_client: TestClient) -> None:
    """🔴 호출자 0 가드 — POST가 실제 `counsel_draft_view` 행을 남겨야 한다.

    ⚠ **「행이 있다」로는 부족하다** — 같은 행을 **초안 저장이 대신 만든다.** 뷰 쓰기를
    끊고 돌려 봤더니 초안 쪽이 행을 만들어 `count == 1`이 **그대로 통과**했다(실측).
    ⇒ **`view_snapshot`이 실제로 찼는지**를 센다. 검사의 이름이 보는 것보다 넓었다(로그 85).
    """
    job_id = _post(pg_client)
    count = asyncio.run(
        _query(
            "SELECT count(*) FROM counsel_draft_view "
            "WHERE tenant_id = :t AND job_id = :j AND view_snapshot IS NOT NULL",
            t=_TENANT,
            j=job_id,
        )
    )
    assert count == 1, "POST가 뷰 스냅숏을 안 남겼다 — 저장소 호출자가 0이다"


def test_get_survives_an_emptied_cache(pg_client: TestClient) -> None:
    """㉿ ⓐ·ⓒ — 축출/재시작 뒤에도 GET이 404가 아니어야 한다."""
    job_id = _post(pg_client)
    assert pg_client.get(
        f"/v1/counsel/drafts/{job_id}", headers=_HEADERS
    ).status_code == 200

    _forget_caches()
    revived = pg_client.get(f"/v1/counsel/drafts/{job_id}", headers=_HEADERS)
    assert revived.status_code == 200, (
        f"캐시를 비우니 GET이 죽는다({revived.status_code}) — 캐시가 아직 정본이다"
    )
    assert revived.json()["data"]["job_id"] == job_id


def test_another_tenant_still_gets_404(pg_client: TestClient) -> None:
    """🔴 복원 경로가 **테넌트 격리를 뚫으면 안 된다** — 존재 은닉 그대로."""
    job_id = _post(pg_client)
    _forget_caches()
    headers = {**_HEADERS, "X-Tenant-Id": "t_intruder"}
    assert pg_client.get(
        f"/v1/counsel/drafts/{job_id}", headers=headers
    ).status_code == 404


def test_refine_survives_an_emptied_cache(pg_client: TestClient) -> None:
    """㉿ ⓑ — GET과 refine이 **같은 축**으로 살아나야 한다(두 캐시가 독립 축출이었다)."""
    job_id = _post(pg_client)
    _forget_caches()
    response = pg_client.post(
        f"/v1/counsel/drafts/{job_id}/refine",
        json={"instruction": "조금 더 부드럽게 써줘", "turn_no": 1},
        headers={**_HEADERS, "Idempotency-Key": f"{_TENANT}:refine:1"},
    )
    assert response.status_code != 404, (
        "캐시를 비우니 refine이 404다 — 초안 상태가 PG에서 안 살아난다"
    )


def test_an_unknown_job_is_still_404(pg_client: TestClient) -> None:
    """복원 경로가 **없는 잡을 만들어 내면 안 된다**."""
    assert pg_client.get(
        "/v1/counsel/drafts/job-does-not-exist", headers=_HEADERS
    ).status_code == 404
