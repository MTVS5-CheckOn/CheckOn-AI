"""멱등 저장소 단위 검사 — 스코프·hash 판정·TTL·fail-open (D-② 커밋④).

인메모리 구현은 전 규약을 결정론적으로 검증하고, PG 구현은 DB 없이 검증 가능한
fail-open 경로(저장·조회 실패가 요청을 막지 않음)를 가짜 sessionmaker로 검증한다.
실 PG 왕복은 docker PG(99 ⑫) 확정 후 integration 마커로 붙인다.

이 저장소만 async라서(코드베이스에 async 테스트 인프라 없음) asyncio.run으로 구동한다 —
새 의존성(pytest-asyncio) 없이 결정론적으로 돈다(저장소는 실제 I/O await가 없다).
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.exc import OperationalError

from ai.db.repositories.idempotency import (
    DEFAULT_TTL,
    IdempotencyStore,
    InMemoryIdempotencyStore,
    PgIdempotencyStore,
)

_T0 = datetime(2026, 7, 22, 9, 0, tzinfo=UTC)
_SCOPE = {"tenant_id": "teacher_alias_001", "endpoint": "POST /v1/detect"}
_BODY: dict[str, Any] = {"data": {"signals": []}, "error": None, "meta": {"x": 1}}


def _run[T](coro: Awaitable[T]) -> T:
    return asyncio.run(coro)  # type: ignore[arg-type]


class _FixedClock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


def _store(clock: _FixedClock | None = None) -> InMemoryIdempotencyStore:
    return InMemoryIdempotencyStore(clock=clock or _FixedClock(_T0))


def test_inmemory_satisfies_protocol() -> None:
    assert isinstance(InMemoryIdempotencyStore(), IdempotencyStore)


def test_miss_returns_none() -> None:
    store = _store()
    assert _run(store.get(**_SCOPE, idempotency_key="k1")) is None


def test_put_then_get_same_hash_returns_body() -> None:
    store = _store()

    async def scenario() -> None:
        await store.put(
            **_SCOPE, idempotency_key="k1", snapshot_hash="h1", response_body=_BODY
        )
        hit = await store.get(**_SCOPE, idempotency_key="k1")
        assert hit is not None
        assert hit.snapshot_hash == "h1"
        assert hit.response_body == _BODY

    _run(scenario())


def test_conflict_hash_surfaced_to_caller() -> None:
    """다른 hash면 저장분(다른 hash)을 그대로 돌려주고, 409 판정은 라우터가 한다."""
    store = _store()

    async def scenario() -> None:
        await store.put(
            **_SCOPE, idempotency_key="k1", snapshot_hash="h1", response_body=_BODY
        )
        hit = await store.get(**_SCOPE, idempotency_key="k1")
        assert hit is not None
        assert hit.snapshot_hash != "h2"  # 라우터가 요청 hash h2와 비교 → conflict

    _run(scenario())


def test_scope_isolation_by_tenant_and_endpoint() -> None:
    """같은 key라도 tenant·endpoint가 다르면 다른 레코드다."""
    store = _store()

    async def scenario() -> None:
        await store.put(
            **_SCOPE, idempotency_key="k1", snapshot_hash="h1", response_body=_BODY
        )
        assert (
            await store.get(
                tenant_id="other_tenant",
                endpoint="POST /v1/detect",
                idempotency_key="k1",
            )
            is None
        )
        assert (
            await store.get(
                tenant_id="teacher_alias_001",
                endpoint="POST /v1/other",
                idempotency_key="k1",
            )
            is None
        )

    _run(scenario())


def test_first_write_wins_on_same_scope() -> None:
    """같은 스코프 재저장은 최초분 유지(PG 유니크 제약과 정합)."""
    store = _store()

    async def scenario() -> None:
        await store.put(
            **_SCOPE, idempotency_key="k1", snapshot_hash="h1", response_body=_BODY
        )
        await store.put(
            **_SCOPE,
            idempotency_key="k1",
            snapshot_hash="h2",
            response_body={"changed": True},
        )
        hit = await store.get(**_SCOPE, idempotency_key="k1")
        assert hit is not None
        assert hit.snapshot_hash == "h1"

    _run(scenario())


def test_ttl_expiry_is_a_miss() -> None:
    """TTL(30일) 밖은 미스 — 주입 시계로 결정론 검증."""
    clock = _FixedClock(_T0)
    store = _store(clock)

    async def scenario() -> None:
        await store.put(
            **_SCOPE, idempotency_key="k1", snapshot_hash="h1", response_body=_BODY
        )
        clock.now = _T0 + DEFAULT_TTL + timedelta(seconds=1)  # 30일 + 1초 경과
        assert await store.get(**_SCOPE, idempotency_key="k1") is None

    _run(scenario())


def test_ttl_boundary_within_window_hits() -> None:
    clock = _FixedClock(_T0)
    store = _store(clock)

    async def scenario() -> None:
        await store.put(
            **_SCOPE, idempotency_key="k1", snapshot_hash="h1", response_body=_BODY
        )
        clock.now = _T0 + DEFAULT_TTL - timedelta(seconds=1)  # 창 안
        assert await store.get(**_SCOPE, idempotency_key="k1") is not None

    _run(scenario())


def test_clear_isolates() -> None:
    store = _store()
    store.clear()
    assert not store._rows


# ─────────── PG 구현 fail-open (DB 없이 가짜 sessionmaker) ───────────


class _RaisingSessionmaker:
    """with 진입 즉시 SQLAlchemyError를 내는 가짜 — DB 장애를 흉내낸다."""

    def __call__(self) -> _RaisingSessionmaker:
        return self

    async def __aenter__(self) -> _RaisingSessionmaker:
        raise OperationalError("boom", None, Exception("db down"))

    async def __aexit__(self, *exc: object) -> None:
        return None


def _pg_store() -> PgIdempotencyStore:
    return PgIdempotencyStore(
        sessionmaker=_RaisingSessionmaker(),  # type: ignore[arg-type]
        clock=_FixedClock(_T0),
    )


def test_pg_satisfies_protocol() -> None:
    assert isinstance(_pg_store(), IdempotencyStore)


def test_pg_get_failure_is_miss_not_raise() -> None:
    """조회 중 DB 장애 → 예외 전파 없이 미스(fail-open)."""
    assert _run(_pg_store().get(**_SCOPE, idempotency_key="k1")) is None


def test_pg_put_failure_swallowed() -> None:
    """저장 중 DB 장애(유니크 경합 포함) → 예외 전파 없이 삼킴(fail-open)."""
    _run(
        _pg_store().put(
            **_SCOPE, idempotency_key="k1", snapshot_hash="h1", response_body=_BODY
        )
    )  # 예외가 나면 이 테스트가 실패한다
