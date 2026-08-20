"""첫 요청이 **실행 중**일 때 두 번째가 오면 같은 잡에 도착한다 (99 #76·#85 곱).

🔴 **위험은 두 등재 항목의 곱이다.** 응답은 최악 225초인데(#85) 멱등 행은 실행 **뒤**에
써진다(#76 잔여). BE의 읽기 타임아웃이 먼저 터지면 재시도가 **멱등 행이 생기기 전에**
도착한다 — 그 창에서는 `get`이 비어 있으므로 라우터가 그냥 새 잡을 만든다.

재현(2026-08-19): 같은 `Idempotency-Key`로 동시 2발 → **서로 다른 job_id 2개**, 멱등 행 1개.
⇒ 잡·LLM·초안이 한 번 더 만들어지고 두 요청이 **서로 다른 응답**을 받았다.

**막는 대신 이유를 없앴다** — `job_id`를 멱등 스코프에서 유도하니 두 번째 요청이 첫 번째와
**같은 잡**에 도착한다. 방어가 하나도 안 늘었다.

🔴 **BE는 이 유도를 계산하지 않는다.** 같은 키로 다시 보내기만 하고 `job_id`는 응답에
실려 오는 불투명 값으로 남는다 — 상대가 같은 해시를 내야 하는 설계는 99 #50(Java 해시
갈림)과 같은 계열의 문제를 하나 더 만든다.

🔴 **검사는 전부 라우터를 통해서 잰다** — 내부 함수를 직접 부르면 배선을 지워도 green이다.
"""

from __future__ import annotations

import asyncio
import threading
import time
import uuid
from collections.abc import Iterator
from typing import Any, Final

import httpx
import pytest
from fastapi.testclient import TestClient

from ai.api.app import create_app
from ai.api.routers.counsel import reset_counsel_stores
from ai.contracts.agents import WorkerJob
from ai.db.store_factory import build_agent_job_store, reset_shared_agent_runtime

_TENANT: Final = "t_inflight_idem"
_OTHER_TENANT: Final = "t_inflight_idem_other"
_KEY: Final = "inq_7f3a"


def _headers(*, tenant_id: str = _TENANT, key: str = _KEY) -> dict[str, str]:
    return {
        "X-Tenant-Id": tenant_id,
        "X-Request-Id": f"rq-{tenant_id}",
        "Idempotency-Key": key,
    }


def _request_body() -> dict[str, Any]:
    """🔴 계약 §4-① 예시를 복제하지 않는다 — 기존 통합 검사의 정본을 재사용한다."""
    from test_counsel_router import _REQUEST  # noqa: PLC0415

    return dict(_REQUEST)


@pytest.fixture(autouse=True)
def _isolate() -> Iterator[None]:
    reset_shared_agent_runtime()
    reset_counsel_stores()
    yield
    reset_shared_agent_runtime()
    reset_counsel_stores()


def _jobs_added() -> int:
    """인메모리 원장이 실제로 **몇 개의 잡을 받았는가**.

    ⚠ 현재 잡 수가 아니라 **누적 add 횟수**다 — 중복이 만들어졌다가 사라지는 경우까지 센다.
    """
    store = build_agent_job_store()
    added = getattr(store, "added", None)
    assert isinstance(added, int), "인메모리 원장이 아니다 — 이 검사는 add 횟수를 센다"
    return added


def _post(
    client: TestClient,
    *,
    tenant_id: str = _TENANT,
    key: str = _KEY,
    body: dict[str, Any] | None = None,
) -> httpx.Response:
    response: httpx.Response = client.post(
        "/v1/counsel/drafts",
        json=_request_body() if body is None else body,
        headers=_headers(tenant_id=tenant_id, key=key),
    )
    return response


# ── 검사 7 · 0-1 의 재현이 이제 잡 1개다 ────────────────────────────


def test_a_retry_while_the_first_is_still_running_lands_on_the_same_job(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """🔴 **재현 그대로.** 첫 요청이 실행 중일 때 같은 키로 두 번째 → **job_id 가 같다.**

    ⚠ 종전에는 서로 다른 job_id 2개였다. 느린 writer 로 첫 요청을 실행 구간에 붙잡아
    두 번째가 **멱등 행이 써지기 전에** 도착하게 만든다 — 그게 #76·#85 의 창이다.
    """
    from ai.composition.counsel import provider as provider_module  # noqa: PLC0415

    original = provider_module.FakeCounselProvider.write

    async def slow_write(self: provider_module.FakeCounselProvider, **kwargs: object) -> str:
        #: 🔴 실행 구간을 **넓히기만** 한다 — 산출물은 원본 그대로다. 여기서 본문을
        #: 바꾸면 재는 축(잡이 몇 개 생기는가)이 아니라 다른 걸 재게 된다.
        await asyncio.sleep(0.6)
        return await original(self, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(provider_module.FakeCounselProvider, "write", slow_write)

    with TestClient(create_app()) as client:
        before = _jobs_added()
        results: dict[str, httpx.Response] = {}

        def fire(name: str, delay: float) -> None:
            time.sleep(delay)
            results[name] = _post(client)

        first = threading.Thread(target=fire, args=("first", 0.0))
        second = threading.Thread(target=fire, args=("second", 0.15))
        first.start()
        second.start()
        first.join()
        second.join()
        added = _jobs_added() - before

    assert set(results) == {"first", "second"}
    for name, response in results.items():
        assert response.status_code == 202, f"{name}: {response.text}"
    ids = {name: r.json()["data"]["job_id"] for name, r in results.items()}
    assert ids["first"] == ids["second"], (
        f"실행 중 재시도가 **다른 잡**을 만들었다 {ids} — "
        "잡·LLM·초안이 한 번 더 생긴다 (99 #76·#85 곱)"
    )
    assert added == 1, f"잡이 {added}개 만들어졌다 — 재시도가 새 잡을 만들면 안 된다"


# ── 검사 8 · 이미 종단인 잡에 같은 키가 오면 결과가 온다 ──────────


def test_a_retry_after_the_job_finished_returns_the_result_without_a_new_job(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """잡이 **이미 종단**인데 멱등 행이 없다 → **결과가 온다**(새 잡 0).

    🔴 이 상황은 가정이 아니다 — `put` 실패는 삼켜지고 202 로 나간다(실측 0-5·0-6:
    `PgIdempotencyStore.put` 이 `except SQLAlchemyError` 로 유니크 충돌과 DB 장애를
    구분 없이 삼킨다). 그러면 **행 없이 종단인 잡**이 남는다.
    ⚠ 기존 `get`→hit 경로와 **겹치지 않는다** — 저쪽은 행이 있을 때만 돈다.
    """
    from ai.api.routers import counsel as counsel_router  # noqa: PLC0415

    with TestClient(create_app()) as client:
        # 첫 요청은 정상으로 끝내되 **멱등 행만 안 써지게** 한다.
        monkeypatch.setattr(
            counsel_router._idempotency_store,
            "put",
            lambda **_kwargs: asyncio.sleep(0),
        )
        first = _post(client)
        assert first.status_code == 202, first.text
        #: 🔴 K=0 — POST 는 적재만 한다. 이 검사의 축은 **재시도가 새 잡을 안 만드는가**이지
        #:   POST 가 언제 끝나는가가 아니다(2026-08-20).
        assert first.json()["data"]["status"] == "queued", first.text
        monkeypatch.undo()

        before = _jobs_added()
        second = _post(client)
        added = _jobs_added() - before
        # ⚠ POST 응답은 `{job_id, status}`뿐이다 — **본문은 GET 이 준다**(계약 §4).
        #   "결과가 온다"는 재시도가 받은 job_id 로 **초안까지 닿는다**는 뜻이다.
        fetched = client.get(
            f"/v1/counsel/drafts/{second.json()['data']['job_id']}", headers=_headers()
        )

    assert second.status_code == 202, second.text
    body = second.json()["data"]
    assert body["job_id"] == first.json()["data"]["job_id"], (
        f"재시도가 다른 잡을 가리킨다 {body['job_id']}"
    )
    assert added == 0, f"종단 잡에 재시도가 왔는데 잡이 {added}개 더 생겼다"
    assert body["status"] == "succeeded", body
    assert fetched.status_code == 200, fetched.text
    result = fetched.json()["data"]["result"]
    assert result is not None, "종단인데 결과가 비었다 — 재시도가 빈손으로 돌아간다"
    assert result["draft_status"] == "generated", result


# ── 검사 9 · 같은 키 + 다른 바디 → 409 (회귀) ──────────────────────


def test_the_same_key_with_a_different_body_is_still_a_conflict() -> None:
    """🔴 **회귀.** 멱등 행이 있는 정상 경로에서 같은 키 + 다른 바디는 그대로 409 (#277).

    ⚠ 유도된 `job_id` 가 이 검사를 무력화하면 안 된다 — 바디 검사가 **먼저**다.
    """
    other = _request_body()
    other["class_ref"] = f"{other['class_ref']}_다름"

    with TestClient(create_app()) as client:
        assert _post(client).status_code == 202
        clashed = _post(client, body=other)

    assert clashed.status_code == 409, clashed.text


def test_the_same_key_with_a_different_body_conflicts_while_still_in_flight(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """멱등 행이 **없는** 구간에서도 다른 바디는 409다.

    🔴 유도 `job_id` 가 새로 연 구멍이다 — 행이 없으면 대조할 `body_hash` 도 없는데
    같은 키는 **같은 job_id** 로 풀리므로, 막지 않으면 다른 요청이 남의 초안을 받는다.
    ⚠ 대조할 수 있는 건 잡의 `payload_hash`(입력 묶음)뿐이라 **부분 방어**다(99 #94).
    """
    from ai.api.routers import counsel as counsel_router  # noqa: PLC0415

    other = _request_body()
    other["student_ref"] = f"{other['student_ref']}_다름"

    with TestClient(create_app()) as client:
        monkeypatch.setattr(
            counsel_router._idempotency_store,
            "put",
            lambda **_kwargs: asyncio.sleep(0),
        )
        assert _post(client).status_code == 202
        clashed = _post(client, body=other)

    assert clashed.status_code == 409, clashed.text


# ── 검사 10 · 테넌트가 다르면 안 섞인다 ────────────────────────────


def test_the_same_key_from_another_tenant_is_a_different_job() -> None:
    """스코프에 `tenant_id` 가 있다 — 같은 키를 써도 **안 섞인다.**

    🔴 `job_id` 가 유도값이면 남이 값을 추측할 여지가 생긴다. 격리를 지키는 건 조회의
    tenant 술어다(실측: `InMemoryJobStore.get`·`PgJobStore.get` 둘 다
    `tenant_id` 비교가 있다) — **유도가 아니라 그 술어가 방어다.**
    """
    with TestClient(create_app()) as client:
        mine = _post(client)
        theirs = _post(client, tenant_id=_OTHER_TENANT)
        assert mine.status_code == 202 and theirs.status_code == 202
        mine_id = mine.json()["data"]["job_id"]
        theirs_id = theirs.json()["data"]["job_id"]
        assert mine_id != theirs_id, "다른 테넌트가 같은 키로 같은 잡에 닿았다"

        # 남의 job_id 를 알아도 내 테넌트로는 못 읽는다.
        peeked = client.get(f"/v1/counsel/drafts/{theirs_id}", headers=_headers(tenant_id=_TENANT))
    assert peeked.status_code == 404, peeked.text


# ── 실 PG · 중복 job_id 를 원장이 정말 막는가 ──────────────────────


def _pg_reachable() -> bool:
    """접속 가능 여부만 가른다 — **접속 실패와 검사 실패를 섞지 않는다.**"""
    from sqlalchemy import text  # noqa: PLC0415
    from sqlalchemy.ext.asyncio import create_async_engine  # noqa: PLC0415
    from sqlalchemy.pool import NullPool  # noqa: PLC0415

    from ai.db.settings import get_db_settings  # noqa: PLC0415

    async def probe() -> None:
        engine = create_async_engine(get_db_settings().database_url, poolclass=NullPool)
        try:
            async with engine.begin() as conn:
                await conn.execute(text("SELECT 1"))
        finally:
            await engine.dispose()

    try:
        asyncio.run(probe())
    except Exception:  # noqa: BLE001 — 접속 실패 종류를 가리지 않는다
        return False
    return True


@pytest.mark.integration
def test_the_pg_ledger_rejects_a_duplicate_job_id() -> None:
    """🔴 **인메모리와 PG 의 규약이 갈리면 안 된다**(0-9).

    둘 다 `JobAlreadyExistsError` 여야 라우터의 경합 처리가 **양쪽 배포에서 같은 뜻**이다.
    PG 는 `AgentRun` PK 의 SQLSTATE 23505 를 그 예외로 번역한다 — 그 번역이 살아 있는지를
    여기서 잰다. ⚠ 인메모리는 위 검사들이 이미 라우터를 통해 재고 있다.
    """
    import uuid as _uuid  # noqa: PLC0415

    from pg_hint import PG_UNAVAILABLE  # noqa: PLC0415
    from sqlalchemy import text  # noqa: PLC0415
    from sqlalchemy.ext.asyncio import (  # noqa: PLC0415
        async_sessionmaker,
        create_async_engine,
    )
    from sqlalchemy.pool import NullPool  # noqa: PLC0415

    from ai.agents.job_store import JobAlreadyExistsError  # noqa: PLC0415
    from ai.api.routers.counsel import _clock  # noqa: PLC0415
    from ai.contracts.agents import (  # noqa: PLC0415
        OperationKind,
        WorkerJob,
        WorkerKind,
        default_priority_for_operation,
    )
    from ai.db.repositories.agent_job import PgJobStore  # noqa: PLC0415
    from ai.db.settings import get_db_settings  # noqa: PLC0415

    operation = OperationKind.COUNSEL_PACK_GENERATE
    job_id = _uuid.uuid4()

    async def run() -> str:
        engine = create_async_engine(get_db_settings().database_url, poolclass=NullPool)
        try:
            store = PgJobStore(sessionmaker=async_sessionmaker(engine, expire_on_commit=False))
            job = WorkerJob(
                job_id=job_id,
                execution_id=_uuid.uuid4(),
                tenant_id=_TENANT,
                worker_kind=WorkerKind.COUNSEL_PACK,
                operation=operation,
                payload_ref=f"context://{_uuid.uuid4()}",
                payload_hash="sha256:" + "0" * 64,
                priority_class=default_priority_for_operation(operation),
                queued_at=_clock(),
            )
            await store.add(job)
            try:
                # 같은 job_id · 다른 execution_id — 재시도가 만드는 모양 그대로다.
                await store.add(job.model_copy(update={"execution_id": _uuid.uuid4()}))
            except JobAlreadyExistsError:
                return "rejected"
            return "accepted"
        finally:
            async with engine.begin() as conn:
                await conn.execute(
                    text("DELETE FROM agent_run WHERE tenant_id = :t"), {"t": _TENANT}
                )
            await engine.dispose()

    if not _pg_reachable():
        pytest.skip(PG_UNAVAILABLE)
    # 🔴 여기서 예외를 삼키지 않는다 — 접속 여부는 위에서 이미 갈랐다. 종전에 통째로
    #   `except Exception: skip` 이었더니 `ImportError` 가 **거짓 skip** 으로 나왔다.
    verdict = asyncio.run(run())

    assert verdict == "rejected", (
        "PG 원장이 중복 job_id 를 받아들였다 — 인메모리는 거절한다. "
        "규약이 갈리면 배포에 따라 재시도가 잡을 두 개 만든다"
    )


# ── 선조회가 실제로 무엇을 사는가 ──────────────────────────────────


def _bundle_rows() -> int:
    """저장된 입력 묶음 행 수 — 인메모리 구현의 내부를 본다(계약에 개수 조회가 없다)."""
    from ai.api.routers import counsel as counsel_router  # noqa: PLC0415

    rows = getattr(counsel_router._context_store, "_rows", None)
    assert isinstance(rows, dict), "인메모리 묶음 저장소가 아니다"
    return len(rows)


def test_a_retry_does_not_leave_an_orphan_context_bundle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """재시도가 **입력 묶음을 한 벌 더 쓰지 않는다.**

    🔴 정확성은 `JobAlreadyExistsError` 포착이 이미 지킨다 — 선조회를 지워도 잡은 하나다
    (실측: 지우면 위 검사 5건 전부 green). **선조회가 사는 건 이거다**: `ContextStore.put`
    이 `enqueue` 보다 **먼저** 돌기 때문에, 선조회 없이 재시도가 들어오면 잡은 안 늘어도
    **묶음 행은 늘어난다.** BE 타임아웃이 연달아 나면 재시도마다 한 행씩 쌓인다.
    ⚠ 묶음에는 학생 컨텍스트(alias)가 들어 있다 — 유출은 아니지만 쌓일 이유도 없다.
    """
    from ai.api.routers import counsel as counsel_router  # noqa: PLC0415

    with TestClient(create_app()) as client:
        monkeypatch.setattr(
            counsel_router._idempotency_store,
            "put",
            lambda **_kwargs: asyncio.sleep(0),
        )
        assert _post(client).status_code == 202
        before = _bundle_rows()
        assert _post(client).status_code == 202
        after = _bundle_rows()

    assert after == before, (
        f"재시도가 입력 묶음을 {after - before}행 더 남겼다 — "
        "`ContextStore.put` 이 `enqueue` 보다 먼저 돌기 때문이다"
    )


# ── 경합 · 선조회와 add 사이에 남이 끼어든다 ──────────────────────


def test_a_race_between_the_precheck_and_the_insert_lands_on_the_same_job(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """선조회가 **비었는데** `add` 가 중복을 만나는 창 — 그때도 잡은 하나다.

    🔴 **타이밍으로는 못 잰다.** 두 요청의 선조회가 동시에 비어야 하는데 그 창은
    마이크로초다(실측: 느린 writer 로 0.15초 뒤에 쏘면 두 번째의 **선조회가** 먼저 잡는다).
    ⇒ 「선조회가 딱 한 번 빈손으로 돌아온다」를 **결정론으로 주입**한다 — 다른 프로세스가
    그 사이에 넣은 것과 같은 모양이다.

    ⚠ 이 가지가 없으면 경합한 두 번째 요청은 `JobAlreadyExistsError` 가 그대로 올라가
    **500**이 된다(미분류 예외 → `INTERNAL`). 재시도가 500을 받으면 BE 는 또 재시도한다.
    """
    from ai.agents.supervisor import Supervisor  # noqa: PLC0415
    from ai.api.routers import counsel as counsel_router  # noqa: PLC0415

    original_get = Supervisor.get
    blinded: list[int] = []

    async def get_blind_once(
        self: Supervisor, *, tenant_id: str, job_id: uuid.UUID
    ) -> WorkerJob | None:
        #: 첫 조회(=선조회)만 빈손으로 돌려보낸다. 이후는 그대로 — 포착 가지가 잡을
        #: **정말로 찾아오는지**를 재야 하므로 두 번째부터는 진짜 값이어야 한다.
        if not blinded:
            blinded.append(1)
            return None
        return await original_get(self, tenant_id=tenant_id, job_id=job_id)

    with TestClient(create_app()) as client:
        monkeypatch.setattr(
            counsel_router._idempotency_store,
            "put",
            lambda **_kwargs: asyncio.sleep(0),
        )
        first = _post(client)
        assert first.status_code == 202, first.text
        monkeypatch.setattr(Supervisor, "get", get_blind_once)
        before = _jobs_added()
        second = _post(client)
        added = _jobs_added() - before

    assert blinded, "선조회를 가리지 못했다 — 경합 가지를 안 지났다"
    assert second.status_code == 202, (
        f"경합한 재시도가 {second.status_code} 를 받았다 — "
        "포착이 없으면 JobAlreadyExistsError 가 그대로 올라가 500 이 된다"
    )
    assert second.json()["data"]["job_id"] == first.json()["data"]["job_id"]
    assert added == 0, f"경합 가지에서 잡이 {added}개 더 생겼다"
