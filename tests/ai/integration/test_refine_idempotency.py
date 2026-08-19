"""refine 의 멱등 축 — **헤더는 BE 클라이언트가 굳으면 못 늘린다** (99 #76).

🔴 **POST에는 있는 방어가 refine에만 없었다.** refine은 LLM 호출이라 중복이 **원가와
품질을 동시에** 친다 — 네트워크 재시도 한 번이 *강사가 시키지 않은 다듬기 한 턴*이 되고,
그 턴이 누적에 남는다(`turn_no`는 로그로만 쓰여서 아무것도 막지 않는다).

⚠ **지금이 유일하게 싼 시점이다** — 백엔드에 counsel 구현이 **0건**이라(2026-08-19 실측)
필수로 받아도 깨지는 호출자가 없다. 선택 필드로 두고 나중에 조이면 **그 순간이 BE 재작업**이다.

━━ 🔴 스코프 키에 `job_id`가 들어간다 ━━

멱등 스코프는 `(tenant_id, endpoint, idempotency_key)` **셋**이고, refine 바디는
`instruction`·`turn_no` 둘뿐이라 **다른 잡에 같은 지시를 같은 턴으로 보내면 바디 해시가
같아진다.** `endpoint`를 리터럴 하나로 두면 **다른 잡의 응답이 그대로 나간다** —
`test_a_reused_key_does_not_leak_another_job` 이 그 자리를 직접 겨냥한다.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from typing import Any, Final

import httpx
import pytest
from counsel_text import DEFAULT_DRAFT
from fastapi.testclient import TestClient
from pg_hint import PG_UNAVAILABLE
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from ai.api.app import create_app
from ai.api.routers import counsel as counsel_router
from ai.api.routers.counsel import reset_counsel_stores, set_counsel_provider
from ai.composition.counsel.provider import FakeCounselProvider
from ai.db.repositories.idempotency import PgIdempotencyStore
from ai.db.settings import get_db_settings
from ai.db.store_factory import reset_shared_agent_runtime

_TENANT: Final = "t_refine_idem"
_HEADERS: Final = {
    "X-Tenant-Id": _TENANT,
    "X-Request-Id": "rq-refine-idem",
    "Idempotency-Key": f"{_TENANT}:counsel:1",
}
_INSTRUCTION: Final = {"instruction": "조금 더 부드럽게", "turn_no": 1}


def _request_body() -> dict[str, Any]:
    """🔴 계약 §4-① 예시를 복제하지 않는다 — 기존 통합 검사의 정본을 재사용한다."""
    from test_counsel_router import _REQUEST  # noqa: PLC0415

    return dict(_REQUEST)


@pytest.fixture(autouse=True)
def _isolate() -> Iterator[None]:
    reset_shared_agent_runtime()
    reset_counsel_stores()
    #: 다듬기 턴에 **다른 문면**을 준다 — 재반환이 「같은 값」인지 보려면 구분이 필요하다.
    set_counsel_provider(
        FakeCounselProvider(drafts=[DEFAULT_DRAFT, _REFINED, _REFINED, _REFINED])
    )
    yield
    reset_shared_agent_runtime()
    reset_counsel_stores()


_REFINED: Final = DEFAULT_DRAFT.replace("정리해", "차분히 정리해")


def _client() -> TestClient:
    return TestClient(create_app())


def _post(client: TestClient, *, key: str = _HEADERS["Idempotency-Key"]) -> str:
    response: httpx.Response = client.post(
        "/v1/counsel/drafts",
        json=_request_body(),
        headers={**_HEADERS, "Idempotency-Key": key},
    )
    assert response.status_code == 202, response.text
    job_id: str = response.json()["data"]["job_id"]
    return job_id


def _refine(
    client: TestClient, job_id: str, *, key: str, body: dict[str, Any] | None = None
) -> httpx.Response:
    response: httpx.Response = client.post(
        f"/v1/counsel/drafts/{job_id}/refine",
        json=body if body is not None else _INSTRUCTION,
        headers={**_HEADERS, "Idempotency-Key": key},
    )
    return response


def _ledger_size() -> int:
    """AI_RUN 행 수 — 🔴 **「LLM이 안 불렸다」를 원장으로 잰다.**

    ⚠ 호출 카운터를 세면 **구현 단언**이 된다(내부 객체의 속성을 본다). 원장 행 수는
    계약에 가까운 관측이고, 멱등 hit이 실행을 안 만든다는 것이 그대로 드러난다.
    """
    store = counsel_router._run_store
    runs: dict[Any, Any] = store.runs  # type: ignore[attr-defined]
    return len(runs)


def test_a_refine_without_the_key_is_a_schema_error() -> None:
    """🔴 헤더 누락은 **요청 계약 위반**이다 — 400 `INVALID_SCHEMA`(7/22 결정 ⑥)."""
    with _client() as client:
        job_id = _post(client)
        response = client.post(
            f"/v1/counsel/drafts/{job_id}/refine",
            json=_INSTRUCTION,
            headers={k: v for k, v in _HEADERS.items() if k != "Idempotency-Key"},
        )
    assert response.status_code == 400, response.text
    body = response.json()
    assert body["error"]["code"] == "INVALID_SCHEMA", body
    assert "Idempotency-Key" in str(body["error"]), body


def test_the_same_key_and_body_replays_without_a_new_run() -> None:
    """같은 키 + 같은 바디 → **같은 본문 재반환 · 원장이 안 는다.**

    ⚠ 멱등 hit이면 LLM을 안 부르고 AI_RUN·LLM_CALL도 안 남는다 — POST와 같은 성질이다.
    """
    with _client() as client:
        job_id = _post(client)
        first = _refine(client, job_id, key="idem-replay")
        assert first.status_code == 200, first.text
        after_first = _ledger_size()

        second = _refine(client, job_id, key="idem-replay")

    assert second.status_code == 200, second.text
    assert second.json()["data"] == first.json()["data"], "재반환이 저장분과 다르다"
    assert _ledger_size() == after_first, (
        "멱등 hit인데 원장이 늘었다 — 다듬기가 한 턴 더 돌았다(강사가 시키지 않은 턴)"
    )


def test_a_reused_key_does_not_leak_another_job() -> None:
    """🔴 **같은 키를 다른 잡에 써도 남의 응답이 나오지 않는다.**

    refine 바디는 `instruction`·`turn_no` 둘뿐이라 **바디 해시가 같아진다.** 스코프의
    `endpoint`에 `job_id`가 없으면 두 번째 잡이 **첫 잡의 응답**을 그대로 받는다.
    ⚠ 이 검사가 없으면 그 판단이 코드 주석에만 있고 검사에 없다.
    """
    with _client() as client:
        first_job = _post(client, key=f"{_TENANT}:counsel:a")
        second_job = _post(client, key=f"{_TENANT}:counsel:b")
        assert first_job != second_job

        first = _refine(client, first_job, key="idem-shared")
        second = _refine(client, second_job, key="idem-shared")

    assert first.status_code == 200 and second.status_code == 200, (
        first.text,
        second.text,
    )
    #: 🔴 실행 원장 키가 다르다 = **각자 돌았다.** 같으면 남의 저장분이 나온 것이다.
    assert first.json()["meta"]["execution_id"] != second.json()["meta"]["execution_id"], (
        "다른 잡에 같은 키를 썼는데 같은 실행이 나왔다 — 멱등 스코프에 job_id가 없다"
    )


def test_the_same_key_with_a_different_body_conflicts() -> None:
    """같은 키 + 다른 바디 → **409 `IDEMPOTENCY_CONFLICT`.**"""
    with _client() as client:
        job_id = _post(client)
        assert _refine(client, job_id, key="idem-conflict").status_code == 200
        clash = _refine(
            client,
            job_id,
            key="idem-conflict",
            body={"instruction": "완전히 다른 지시", "turn_no": 9},
        )
    assert clash.status_code == 409, clash.text
    assert clash.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT", clash.text


# ── 🔴 실 PG — 인메모리와 유니크 처리가 다르다 ──────────────────────


def _pg_available() -> bool:
    async def probe() -> None:
        engine = create_async_engine(get_db_settings().database_url, poolclass=NullPool)
        try:
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
        finally:
            await engine.dispose()

    try:
        asyncio.run(probe())
    except Exception:  # noqa: BLE001 — 접속 실패 종류를 가리지 않는다
        return False
    return True


@pytest.mark.integration
def test_the_replay_holds_on_real_postgres() -> None:
    """🔴 멱등 축을 **실 PG에서도** 한 번 돈다.

    ⚠ `InMemoryIdempotencyStore`와 `PgIdempotencyStore`는 유니크 처리가 다르다 —
    인메모리는 dict 덮어쓰기고 PG는 제약이다. 인메모리에서만 보면 그 차이가 안 보인다.
    """
    if not _pg_available():
        pytest.skip(PG_UNAVAILABLE)
    engine = create_async_engine(get_db_settings().database_url, poolclass=NullPool)
    #: 🔴 전역에 직접 대입하지 않는다 — 합성 루트가 쓰는 공식 주입 문을 탄다.
    counsel_router.set_counsel_stores(
        idempotency_store=PgIdempotencyStore(
            sessionmaker=async_sessionmaker(engine, expire_on_commit=False)
        )
    )
    try:
        #: 🔴 **키를 한 번만 뽑는다** — 호출마다 새로 뽑으면 서로 다른 키가 되어
        #: 「재반환」이 아니라 「두 번 실행」을 재게 된다(2026-08-19에 그 실수를 했다).
        run = job_key()
        with _client() as client:
            job_id = _post(client, key=f"{_TENANT}:pg:{run}")
            first = _refine(client, job_id, key=f"idem-pg-{run}")
            assert first.status_code == 200, first.text
            after_first = _ledger_size()
            second = _refine(client, job_id, key=f"idem-pg-{run}")

        assert second.status_code == 200, second.text
        assert second.json()["data"] == first.json()["data"]
        assert _ledger_size() == after_first, "실 PG에서 멱등 hit인데 원장이 늘었다"
    finally:
        asyncio.run(engine.dispose())
        reset_counsel_stores()


def job_key() -> str:
    """🔴 실행마다 다른 키 — 실 PG는 행이 **남는다**(인메모리와 다른 점이다)."""
    from uuid import uuid4  # noqa: PLC0415

    return uuid4().hex[:8]
