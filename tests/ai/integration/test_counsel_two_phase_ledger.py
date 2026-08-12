"""실 PG **재개 3구간** — begin → finalize가 최종 사용 축을 지키는가 (99 #46).

🔴 **종전에는 재개가 원장을 잃었다.** `record_run()`이 INSERT 전용이라 두 번째 시도가
`pk_ai_run`으로 죽고, 그 실패가 fail-open으로 삼켜져 **최종 사용 축이 1차 값에 멈췄다.**

이 파일은 **실 PG**로 그 흐름을 끝까지 잰다. FakeProvider만 쓰고 **실 LLM은 안 부른다.**
⚠ **부모 AI_RUN을 손으로 넣지 않는다** — `begin_run`이 만든 것만 센다.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Coroutine, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any, Final

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from ai.contracts.execution import (
    Capability,
    ExecutionContext,
    GenerationParams,
    RunMetadata,
    VersionSet,
)
from ai.contracts.llm import CallOutcome
from ai.db.models import Base
from ai.db.repositories.run_store import (
    CollectedCall,
    PgRunStore,
    RunIdentityConflict,
)
from ai.db.settings import get_db_settings
from ai.llm.gateway import LlmCallRecord

pytestmark = pytest.mark.integration

_TENANT: Final = "t_two_phase"
_T0: Final = datetime(2026, 8, 12, 9, 0, tzinfo=UTC)


def _run[T](coro: Coroutine[Any, Any, T]) -> T:
    try:
        return asyncio.run(coro)
    except Exception as exc:  # noqa: BLE001 — 접속 실패만 skip으로 가른다
        if "connect" in str(exc).lower() or "refused" in str(exc).lower():
            pytest.skip("실 PG 미가용 — docker compose up -d")
        raise


def _meta(
    execution_id: uuid.UUID,
    *,
    provider: str | None = None,
    model: str | None = None,
    created_at: datetime = _T0,
    snapshot: str = "sha256:two-phase",
) -> RunMetadata:
    return ExecutionContext(
        execution_id=execution_id,
        tenant_id=_TENANT,
        capability=Capability.COMPOSITION,
        input_snapshot_hash=snapshot,
        versions=VersionSet(
            pipeline_version="0.1.0",
            engine_version="counsel-pack-0.1",
            schema_version="0.1",
            contract_version="0.1",
        ),
    ).to_run_metadata(
        created_at=created_at,
        model_provider=provider,
        model_name=model,
        generation_params=GenerationParams(temperature=0.2) if provider else None,
    )


def _call(
    *, outcome: CallOutcome = CallOutcome.OK, provider: str = "A", model: str = "A1"
) -> CollectedCall:
    return CollectedCall(
        id=uuid.uuid4(),
        record=LlmCallRecord(
            role="generator",
            provider=provider,
            model=model,
            prompt_id="p",
            prompt_version="0.1",
            usage=None,
            latency_ms=10,
            outcome=outcome,
        ),
    )


async def _prepare(sessions: async_sessionmaker[AsyncSession], engine: Any) -> None:  # noqa: ANN401
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with sessions() as session, session.begin():
        await session.execute(
            text(
                "DELETE FROM llm_payload WHERE call_id IN "
                "(SELECT id FROM llm_call WHERE run_id IN "
                "(SELECT execution_id FROM ai_run WHERE tenant_id = :t))"
            ),
            {"t": _TENANT},
        )
        await session.execute(
            text(
                "DELETE FROM llm_call WHERE run_id IN "
                "(SELECT execution_id FROM ai_run WHERE tenant_id = :t)"
            ),
            {"t": _TENANT},
        )
        await session.execute(
            text("DELETE FROM ai_run WHERE tenant_id = :t"), {"t": _TENANT}
        )


async def _rows(
    sessions: async_sessionmaker[AsyncSession], execution_id: uuid.UUID
) -> tuple[Any, list[Any]]:
    async with sessions() as session:
        run = (
            await session.execute(
                text(
                    "SELECT model_provider, model_name, generation_params, created_at,"
                    " input_snapshot_hash FROM ai_run WHERE execution_id = :x"
                ),
                {"x": execution_id},
            )
        ).one_or_none()
        calls = list(
            (
                await session.execute(
                    text("SELECT id, provider, outcome FROM llm_call WHERE run_id = :x"),
                    {"x": execution_id},
                )
            ).all()
        )
    return run, calls


def test_three_attempts_converge_to_the_last_success() -> None:
    """🔴 **1차 성공 A → 2차 실패만 → 3차 성공 B** — 최종 사용 축은 **B**여야 한다.

    | 구간 | 호출 | 기대 |
    | --- | --- | --- |
    | 1차 | 성공 A | 사용 축 A |
    | 2차 | 실패만 | **A 유지**(값→None 금지) |
    | 3차 | 성공 B | **B로 갱신** |

    ⚠ `created_at`은 **1차 begin 시각**이고, `LLM_CALL`은 세 구간 **전량**이다.
    """

    async def scenario() -> None:
        engine = create_async_engine(get_db_settings().database_url, poolclass=NullPool)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        try:
            await _prepare(sessions, engine)
            store = PgRunStore(sessionmaker=sessions)
            execution_id = uuid.uuid4()

            #: ── 1차: 시작 → 성공 A ──
            await store.begin_run(_meta(execution_id))
            first = _call(provider="A", model="A1")
            await store.finalize_run(
                _meta(execution_id, provider="A", model="A1"), [first]
            )

            #: ── 2차: 재개(같은 execution_id) → 실패 호출만 ──
            await store.begin_run(
                _meta(execution_id, created_at=_T0 + timedelta(minutes=10))
            )
            failed_call = _call(outcome=CallOutcome.TIMEOUT, provider="Z", model="Z9")
            await store.finalize_run(_meta(execution_id), [failed_call])

            #: ── 3차: 재개 → 성공 B ──
            await store.begin_run(
                _meta(execution_id, created_at=_T0 + timedelta(minutes=20))
            )
            third = _call(provider="B", model="B1")
            await store.finalize_run(
                _meta(execution_id, provider="B", model="B1"), [third]
            )

            run, calls = await _rows(sessions, execution_id)
            assert run is not None, "AI_RUN이 없다"
            provider, model, params, created_at, _snapshot = run
            assert (provider, model) == ("B", "B1"), (
                f"최종 사용 축이 {provider}/{model}다 — 마지막 성공이 아니다"
            )
            assert params is not None, "generation_params가 비었다"
            assert created_at == _T0, "재개가 최초 시작 시각을 덮었다"
            assert len(calls) == 3, f"LLM_CALL이 {len(calls)}건이다 — 세 구간 전량이어야 한다"
            assert len({row[0] for row in calls}) == 3, "call id가 중복됐다"
            assert {row[2] for row in calls} == {"ok", "timeout"}
            #: 🔴 정상 종단이면 삼킨 실패는 0이다.
            assert set(store.swallowed_failures.values()) == {0}
        finally:
            await engine.dispose()

    _run(scenario())


def test_only_one_ai_run_row_exists_for_the_execution() -> None:
    """🔴 재개해도 **AI_RUN은 정확히 1행** — PK 충돌이 안 난다."""

    async def scenario() -> None:
        engine = create_async_engine(get_db_settings().database_url, poolclass=NullPool)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        try:
            await _prepare(sessions, engine)
            store = PgRunStore(sessionmaker=sessions)
            execution_id = uuid.uuid4()
            for _ in range(3):
                await store.begin_run(_meta(execution_id))
            async with sessions() as session:
                count = (
                    await session.execute(
                        text("SELECT count(*) FROM ai_run WHERE execution_id = :x"),
                        {"x": execution_id},
                    )
                ).scalar_one()
            assert count == 1, f"AI_RUN이 {count}행이다"
            assert set(store.swallowed_failures.values()) == {0}
        finally:
            await engine.dispose()

    _run(scenario())


def test_a_repeated_call_id_does_not_duplicate_or_drop_the_new_one() -> None:
    """🔴 재개가 이전 호출을 다시 넘겨도 **행이 안 늘고**, 같이 온 새 호출은 **남는다.**"""

    async def scenario() -> None:
        engine = create_async_engine(get_db_settings().database_url, poolclass=NullPool)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        try:
            await _prepare(sessions, engine)
            store = PgRunStore(sessionmaker=sessions)
            execution_id = uuid.uuid4()
            await store.begin_run(_meta(execution_id))
            first = _call(provider="A", model="A1")
            await store.finalize_run(
                _meta(execution_id, provider="A", model="A1"), [first]
            )
            fresh = _call(provider="B", model="B1")
            await store.finalize_run(
                _meta(execution_id, provider="B", model="B1"), [first, fresh]
            )
            _run_row, calls = await _rows(sessions, execution_id)
            assert len(calls) == 2, f"LLM_CALL이 {len(calls)}건이다"
            assert set(store.swallowed_failures.values()) == {0}
        finally:
            await engine.dispose()

    _run(scenario())


def test_a_changed_confirmed_axis_conflicts_in_real_pg() -> None:
    """🔴 확정 축이 달라지면 **예외**다 — 삼킨 실패로 위장하지 않는다."""

    async def scenario() -> None:
        engine = create_async_engine(get_db_settings().database_url, poolclass=NullPool)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        try:
            await _prepare(sessions, engine)
            store = PgRunStore(sessionmaker=sessions)
            execution_id = uuid.uuid4()
            await store.begin_run(_meta(execution_id))
            with pytest.raises(RunIdentityConflict, match="input_snapshot_hash"):
                await store.begin_run(_meta(execution_id, snapshot="sha256:other"))
            assert set(store.swallowed_failures.values()) == {0}
        finally:
            await engine.dispose()

    _run(scenario())


def test_finalize_without_begin_is_counted() -> None:
    """begin 전제가 깨지면 **FK 오류로 흘리지 않고** 센다."""

    async def scenario() -> None:
        engine = create_async_engine(get_db_settings().database_url, poolclass=NullPool)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        try:
            await _prepare(sessions, engine)
            store = PgRunStore(sessionmaker=sessions)
            execution_id = uuid.uuid4()
            await store.finalize_run(
                _meta(execution_id, provider="A", model="A1"), [_call()]
            )
            run, calls = await _rows(sessions, execution_id)
            assert run is None, "없는 실행을 지어냈다"
            assert calls == []
            assert store.swallowed_failures["finalize_run"] == 1
            assert store.swallowed_failures["record_run"] == 0
        finally:
            await engine.dispose()

    _run(scenario())


def test_record_run_is_still_atomic() -> None:
    """🔴 **기존 one-shot 소비자 보호** — 호출 저장이 실패하면 AI_RUN도 롤백된다.

    ⚠ 부모 없는 `llm_call`을 섞어 트랜잭션을 깨뜨린다 — `llm_call.run_id`는 이 실행을
    가리키므로, AI_RUN INSERT가 같은 트랜잭션이 아니면 **AI_RUN만 남는다.**
    """

    async def scenario() -> None:
        engine = create_async_engine(get_db_settings().database_url, poolclass=NullPool)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        try:
            await _prepare(sessions, engine)
            store = PgRunStore(sessionmaker=sessions)
            execution_id = uuid.uuid4()
            duplicate = _call()
            #: 같은 id를 두 번 담으면 `pk_llm_call`이 트랜잭션을 깬다.
            calls: Sequence[CollectedCall] = [duplicate, duplicate]
            await store.record_run(_meta(execution_id, provider="A", model="A1"), calls)

            run, rows = await _rows(sessions, execution_id)
            assert run is None, "호출 저장이 실패했는데 AI_RUN만 남았다 — 원자성이 깨졌다"
            assert rows == []
            assert store.swallowed_failures["record_run"] == 1
        finally:
            await engine.dispose()

    _run(scenario())


def test_the_same_call_id_with_different_content_conflicts_in_real_pg() -> None:
    """🔴 같은 `call id`에 **다른 전문**이면 의미 충돌이다 — PG 경로도 같다.

    ⚠ **이 검사가 없어서 뒤집기가 안 물었다**(실측): PG의 대조를 지워도 인메모리 검사만
    red였다. **두 구현의 의미가 갈리면 백엔드에 따라 원장이 달라진다.**
    """

    async def scenario() -> None:
        engine = create_async_engine(get_db_settings().database_url, poolclass=NullPool)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        try:
            await _prepare(sessions, engine)
            store = PgRunStore(sessionmaker=sessions)
            execution_id = uuid.uuid4()
            await store.begin_run(_meta(execution_id))
            call = _call(provider="A", model="A1")
            await store.finalize_run(
                _meta(execution_id, provider="A", model="A1"), [call]
            )
            twisted = CollectedCall(
                id=call.id, record=call.record.model_copy(update={"provider": "B"})
            )
            with pytest.raises(RunIdentityConflict, match=str(call.id)):
                await store.finalize_run(
                    _meta(execution_id, provider="B", model="B1"), [twisted]
                )
            #: 🔴 충돌은 **삼킨 실패가 아니다.**
            assert set(store.swallowed_failures.values()) == {0}
            _run_row, calls = await _rows(sessions, execution_id)
            assert len(calls) == 1, "충돌인데 행이 늘었다"
        finally:
            await engine.dispose()

    _run(scenario())
