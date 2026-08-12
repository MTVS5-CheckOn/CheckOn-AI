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
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from ai.agents.supervisor import Supervisor
from ai.composition.counsel.assembly import DEFAULT_REGEN_MAX, build_counsel_gateway
from ai.composition.counsel.enqueue import CounselPackEnqueuer
from ai.composition.counsel.gate import check_counsel_gate
from ai.composition.counsel.provider import (
    PLAN_PROMPT_ID,
    CompositeCounselProvider,
    GatewayDraftWriter,
    GatewayPlanner,
    fake_plan_response,
    max_chars_for,
    min_chars_for,
)
from ai.composition.counsel.stores import (
    InMemoryAgentStepSink,
    InMemoryContextStore,
    InMemoryDraftResultStore,
    InMemoryPackResultStore,
)
from ai.composition.counsel.worker import CounselPackRunner
from ai.contracts.agents import JobPhase
from ai.contracts.composition import (
    CommStyle,
    DraftContext,
    EvidenceFact,
    Frequency,
    Interest,
    LabelSnapshot,
    Sensitivity,
)
from ai.contracts.execution import (
    Capability,
    ExecutionContext,
    GenerationParams,
    RunMetadata,
    VersionSet,
)
from ai.contracts.llm import (
    CallOutcome,
    LlmError,
    LLMRequest,
    LLMResult,
    TokenUsage,
)
from ai.db.models import Base
from ai.db.repositories.agent_job import PgJobStore
from ai.db.repositories.run_store import (
    CollectedCall,
    LlmCallCollector,
    PgRunStore,
    RunIdentityConflict,
)
from ai.db.settings import get_db_settings
from ai.llm.gateway import LlmCallRecord

pytestmark = pytest.mark.integration

_TENANT: Final = "t_two_phase"
#: 🔴 **워커 경로는 테넌트를 분리한다** — 저장소 직접 검사와 행이 섞이면 개수 단정이 흐려진다.
_WORKER_TENANT: Final = "t_two_phase_worker"
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


# ── (지시서 72-R) InMemory ↔ PG **충돌 뒤 상태**가 같은가 ──


def _refused_payload_of(call: CollectedCall) -> CollectedCall:
    """저장 직전 훅이 **거부할** 본문(`response_uncertain`) — 거부 카운터 대역."""
    from ai.db.repositories.run_store import CollectedPayload  # noqa: PLC0415

    return CollectedCall(
        id=call.id,
        record=call.record,
        payload=CollectedPayload(
            call_id=call.id,
            request_masked="정상 요청",
            response_masked="정상 응답",
            response_uncertain=True,
        ),
    )


async def _snapshot(
    sessions: async_sessionmaker[AsyncSession], execution_id: uuid.UUID
) -> tuple[Any, ...]:
    run, calls = await _rows(sessions, execution_id)
    async with sessions() as session:
        payloads = sorted(
            str(row[0])
            for row in (
                await session.execute(
                    text(
                        "SELECT call_id FROM llm_payload WHERE call_id IN "
                        "(SELECT id FROM llm_call WHERE run_id = :x)"
                    ),
                    {"x": execution_id},
                )
            ).all()
        )
    return (
        (run[0], run[1]) if run is not None else None,
        sorted(str(row[0]) for row in calls),
        payloads,
    )


def test_a_conflict_leaves_pg_exactly_as_before() -> None:
    """🔴 **PG도 충돌 뒤 상태가 정확히 이전 값**이어야 한다 — InMemory와 대조한다.

    ⚠ *"예외가 났다"* 만 보면 부분 반영을 못 본다. 사용 축·LLM_CALL·LLM_PAYLOAD·거부
    카운터를 **전부** 본다.
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
            before = await _snapshot(sessions, execution_id)
            refused_before = store.refused_payloads

            twisted = CollectedCall(
                id=call.id, record=call.record.model_copy(update={"provider": "B"})
            )
            fresh = _call(provider="B", model="B1")
            with pytest.raises(RunIdentityConflict):
                await store.finalize_run(
                    _meta(execution_id, provider="B", model="B1"), [twisted, fresh]
                )

            after = await _snapshot(sessions, execution_id)
            assert after == before, f"충돌 뒤 상태가 바뀌었다\\n  전: {before}\\n  후: {after}"
            assert store.refused_payloads == refused_before
            assert set(store.swallowed_failures.values()) == {0}
        finally:
            await engine.dispose()

    _run(scenario())


def test_a_repeated_call_does_not_recount_a_refused_payload_in_pg() -> None:
    """🔴 **중복 재전달이 거부 카운터를 다시 올리면 안 된다** — InMemory와 같은 값이어야 한다.

    ⚠ 종전 PG는 `accepted_payloads(calls)`를 **fresh 판정 앞**에서 돌려, 같은 호출을
    재전달할 때마다 거부 수가 올라갔다(InMemory와 갈렸다).
    """

    async def scenario() -> None:
        engine = create_async_engine(get_db_settings().database_url, poolclass=NullPool)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        try:
            await _prepare(sessions, engine)
            store = PgRunStore(sessionmaker=sessions)
            execution_id = uuid.uuid4()
            await store.begin_run(_meta(execution_id))
            call = _refused_payload_of(_call(provider="A", model="A1"))
            await store.finalize_run(
                _meta(execution_id, provider="A", model="A1"), [call]
            )
            first = store.refused_payloads
            assert first == 1, f"거부가 {first}건이다 — 이 검사가 아무것도 안 재고 있다"

            #: 🔴 중복 옆에 **새 정상 호출**을 같이 보낸다 — 새 것은 저장돼야 한다.
            fresh = _call(provider="B", model="B1")
            await store.finalize_run(
                _meta(execution_id, provider="B", model="B1"), [call, fresh]
            )
            assert store.refused_payloads == first, (
                f"재전달로 거부 수가 {first} → {store.refused_payloads}로 늘었다"
            )
            _run_row, calls = await _rows(sessions, execution_id)
            assert len(calls) == 2, f"중복이 새 호출을 죽였다: {len(calls)}건"
            assert set(store.swallowed_failures.values()) == {0}
        finally:
            await engine.dispose()

    _run(scenario())


# ── (지시서 72-R §4) **실제 상담 워커**가 같은 잡을 2회 재개한다 ──
#
# 🔴 위 검사들은 전부 **저장소를 직접 부른다** — `begin_run`/`finalize_run`을 손으로 세 번
#    호출한 것이라 *"워커가 재개할 때 실제로 그 순서로 부르는가"* 는 **아무도 안 본다.**
#    호출 순서가 워커에서 뒤집혀도(또는 `begin_run`이 빠져도) 위 검사는 전부 green이다.
#    여기서는 **enqueue → run_next → pause → resume → run_next** 로 실제 워커를 돌린다.


class _ScriptedCounselLlmProvider:
    """구간별 성공/실패를 **대본**으로 주는 `LLMProvider` 대역 — 실 벤더 호출 0.

    ⚠ **write 호출 순서에 대본을 붙인다** — 학생 순서는 `sorted(bundle.contexts)`로
    고정돼 있어 결정론이다. 대본이 남거나 모자라면 **그래프가 예상과 다르게 돈 것**이므로
    그대로 실패시킨다(조용한 통과 금지).

    🔴 **plan은 항상 성공**이다 — plan 실패는 서킷 카운터에 안 들어가서(graph.py plan
    docstring) 이 시나리오의 pause를 만들지 못한다.
    """

    #: 대본 원소가 이 값이면 그 write는 벤더 오류다.
    FAIL: Final = "FAIL"
    #: plan 응답의 model 이름 — write의 A1·B1과 **겹치지 않게** 둔다(사용 축 오판 방지).
    PLAN_MODEL: Final = "plan-template"

    def __init__(self, script: Sequence[str], *, draft_text: str) -> None:
        self._script = list(script)
        #: 🔴 **게이트를 통과하는 문면이어야 한다** — 막히면 재생성이 돌아 호출 수가
        #:   대본과 어긋나고, 이 검사가 «재개»가 아니라 «재생성»을 재게 된다.
        self._draft_text = draft_text
        self.writes = 0

    @property
    def name(self) -> str:
        return "scripted-counsel"

    @property
    def exhausted(self) -> bool:
        return self.writes == len(self._script)

    async def complete(self, request: LLMRequest, context: ExecutionContext) -> LLMResult:
        del context
        if request.prompt_id == PLAN_PROMPT_ID:
            return self._ok(fake_plan_response(request.prompt), self.PLAN_MODEL)
        if self.writes >= len(self._script):
            raise AssertionError(
                f"대본이 {len(self._script)}건인데 write가 {self.writes + 1}번째다 —"
                " 그래프가 예상과 다르게 돌았다"
            )
        step = self._script[self.writes]
        self.writes += 1
        if step == self.FAIL:
            raise LlmError("대본상 벤더 오류")
        return self._ok(self._draft_text, step)

    def _ok(self, text: str, model: str) -> LLMResult:
        return LLMResult(
            outcome=CallOutcome.OK,
            text=text,
            provider=self.name,
            model=model,
            usage=TokenUsage(tokens_in=1, tokens_out=2, cost_usd=0.0),
            latency_ms=1,
        )


class _CountingRunStore:
    """`PgRunStore` 위임 + **두 단계 호출 계수** — 워커가 구간마다 정말 둘 다 부르는가.

    ⚠ 행만 세면 *"begin이 빠지고 finalize가 upsert로 때웠다"* 를 **못 본다** — 최종 상태가
    같아서다. 그래서 **부른 횟수**를 본다(99 #46: 값이 같으면 경로는 갈리지 않는다).
    """

    def __init__(self, inner: PgRunStore) -> None:
        self.inner = inner
        self.begins = 0
        self.finalizes = 0
        #: 🔴 **두 단계의 상대 순서**까지 남긴다 — 구간 안에서 뒤집히면 FK 부모가 늦는다.
        self.order: list[str] = []

    async def begin_run(self, run: RunMetadata) -> None:
        self.begins += 1
        self.order.append("begin")
        await self.inner.begin_run(run)

    async def finalize_run(
        self, run: RunMetadata, calls: Sequence[CollectedCall]
    ) -> None:
        self.finalizes += 1
        self.order.append("finalize")
        await self.inner.finalize_run(run, calls)

    async def record_run(
        self, run: RunMetadata, calls: Sequence[CollectedCall]
    ) -> None:
        await self.inner.record_run(run, calls)

    async def record_calls(
        self, *, execution_id: uuid.UUID, calls: Sequence[CollectedCall]
    ) -> None:
        await self.inner.record_calls(execution_id=execution_id, calls=calls)


def _draft_context(student_ref: str) -> DraftContext:
    """근거 1건짜리 최소 컨텍스트 — 문면은 이 파일의 축이 아니다(게이트 통과분)."""
    return DraftContext(
        student_ref=student_ref,
        guardian_ref=f"gd_{student_ref}",
        label_snapshot=LabelSnapshot(
            comm=CommStyle.DATA,
            sensitivity=Sensitivity.ANXIOUS,
            interest=Interest.GRADE,
            frequency=Frequency.FREQUENT,
        ),
        facts=(EvidenceFact(label="이번 주 정답률", value="62%"),),
        evidence_summaries=(),
        period_label="2026년 8월",
        fallback_text="이번 주 학습 상황을 정리해 보내드립니다.",
    )


def _passing_draft_text() -> str:
    """게이트를 통과하는 초안 문면을 **하한에서 파생**한다 — 상수로 베끼지 않는다(03 §1).

    ⚠ tone_map이 바뀌어 하한이 올라가면 리터럴은 **조용히** 재생성 루프로 떨어진다.
    수치는 컨텍스트의 근거(`62%`)뿐이다 — 다른 숫자를 쓰면 `ungrounded_number`로 막힌다.
    """
    context = _draft_context("st_a")
    sentence = (
        "이번 주 학습 상황을 정리해 말씀드립니다. 정답률은 62%였습니다."
        " 꾸준히 이어 가시도록 함께 살피겠습니다. "
    )
    lower, upper = min_chars_for(context), max_chars_for(context)
    body = sentence
    while len(body.strip()) < lower:
        body += sentence
    text = body.strip()
    gate = check_counsel_gate(text, context, max_chars=upper, min_chars=lower)
    assert gate.passed, f"대역 문면이 게이트에 막힌다: {gate.reason}"
    return text


async def _clean_worker_tenant(sessions: async_sessionmaker[AsyncSession]) -> None:
    """🔴 **자기 테넌트 행만** 지운다 — 잡 원장(`agent_run`)까지 포함한다."""
    async with sessions() as session, session.begin():
        await session.execute(
            text(
                "DELETE FROM llm_payload WHERE call_id IN "
                "(SELECT id FROM llm_call WHERE run_id IN "
                "(SELECT execution_id FROM ai_run WHERE tenant_id = :t))"
            ),
            {"t": _WORKER_TENANT},
        )
        await session.execute(
            text(
                "DELETE FROM agent_step WHERE agent_run_id IN "
                "(SELECT id FROM agent_run WHERE tenant_id = :t)"
            ),
            {"t": _WORKER_TENANT},
        )
        await session.execute(
            text(
                "DELETE FROM llm_call WHERE run_id IN "
                "(SELECT execution_id FROM ai_run WHERE tenant_id = :t)"
            ),
            {"t": _WORKER_TENANT},
        )
        for table in ("agent_run", "ai_run"):
            await session.execute(
                text(f"DELETE FROM {table} WHERE tenant_id = :t"), {"t": _WORKER_TENANT}
            )


def test_the_real_counsel_worker_resumes_twice_and_keeps_one_ledger_row() -> None:
    """🔴 **실 워커 재개 3구간** — 같은 잡이 두 번 재개돼도 원장은 1행이고 축은 B다.

    | 구간 | 워커에서 일어나는 일 | 원장 |
    | --- | --- | --- |
    | 1차 | plan 성공 · `st_a` 성공(A1) · `st_b` 벤더 오류 → **서킷 → paused** | 축 A1 |
    | 2차 | resume → `st_b` 또 실패 → paused. **성공 호출이 0건** | **A1 유지** |
    | 3차 | resume → `st_b` 성공(B1) → **succeeded** | **B1로 갱신** |

    🔴 **2차가 이 검사의 핵심이다.** `take()`가 버킷을 비우므로 2차 구간의 수집분은
    **실패 호출뿐**이고 `last_success_call`이 `None`이라 워커가 넘기는 메타의 사용 축은
    **전부 `None`** 이다 — 병합이 없으면 여기서 A1이 **지워진다.**

    ⚠ 부모 `AI_RUN`을 손으로 넣지 않는다. 저장소 메서드도 직접 부르지 않는다 —
    **워커가 부른 것만** 센다(`_CountingRunStore`).
    """

    async def scenario() -> None:
        engine = create_async_engine(get_db_settings().database_url, poolclass=NullPool)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            await _clean_worker_tenant(sessions)

            #: 대본: 1차 `st_a` 성공 → `st_b` 실패 / 2차 `st_b` 실패 / 3차 `st_b` 성공.
            fail = _ScriptedCounselLlmProvider.FAIL
            provider = _ScriptedCounselLlmProvider(
                ["A1", fail, fail, "B1"], draft_text=_passing_draft_text()
            )
            collector = LlmCallCollector()
            gateway = build_counsel_gateway(provider, recorder=collector)
            counsel = CompositeCounselProvider(
                GatewayPlanner(gateway), GatewayDraftWriter(gateway)
            )
            supervisor = Supervisor(
                store=PgJobStore(sessionmaker=sessions),
                lease_duration=timedelta(minutes=30),
                priority_aging_interval=timedelta(minutes=1),
                clock=lambda: _T0,
            )
            contexts = InMemoryContextStore()
            runs = _CountingRunStore(PgRunStore(sessionmaker=sessions))
            runner = CounselPackRunner(
                supervisor=supervisor,
                context_store=contexts,
                draft_store=InMemoryDraftResultStore(),
                pack_store=InMemoryPackResultStore(),
                step_sink=InMemoryAgentStepSink(),
                run_store=runs,
                call_log=collector,
                planner=counsel,
                writer=counsel,
                #: 🔴 **구간마다 새로 만들지 않는다** — 새 saver면 재개할 state가 없어
                #:   매 구간이 처음부터 돌고 «재개»가 아니게 된다.
                checkpointer=InMemorySaver(),
                regen_max=DEFAULT_REGEN_MAX,
                #: 🔴 **1학생 실패로 즉시 서킷** — 결정론적으로 pause를 만든다(불변식 6).
                llm_failure_circuit=1,
                lease_owner="worker-two-phase",
                now=lambda: _T0,
            )
            job = await CounselPackEnqueuer(
                supervisor=supervisor, context_store=contexts, now=lambda: _T0
            ).enqueue(
                tenant_id=_WORKER_TENANT,
                class_ref="cl_two_phase",
                contexts={ref: _draft_context(ref) for ref in ("st_a", "st_b")},
            )
            execution_id = job.execution_id

            #: ── 1차 ──
            first = await runner.run_next(tenant_id=_WORKER_TENANT)
            assert first is not None, "워커가 잡을 집지 않았다"
            assert first.phase is JobPhase.PAUSED, (
                f"1차 종단이 {first.phase}다 — 서킷 개방이 안 일어났다"
            )
            after_first = await _rows(sessions, execution_id)
            assert after_first[0] is not None, "1차 뒤 AI_RUN이 없다"
            assert (after_first[0][0], after_first[0][1]) == (provider.name, "A1"), (
                f"1차 사용 축이 {after_first[0][:2]}다 — 마지막 «성공» 호출이 아니다"
            )

            #: ── 2차: 실패만 있는 구간 ──
            resumed = await supervisor.resume(
                tenant_id=_WORKER_TENANT, job_id=job.job_id
            )
            assert resumed.phase is JobPhase.QUEUED
            second = await runner.run_next(tenant_id=_WORKER_TENANT)
            assert second is not None and second.job_id == job.job_id, (
                "2차가 같은 잡을 집지 않았다 — 재개가 아니다"
            )
            assert second.phase is JobPhase.PAUSED, f"2차 종단이 {second.phase}다"
            run_row, mid_calls = await _rows(sessions, execution_id)
            assert (run_row[0], run_row[1]) == (provider.name, "A1"), (
                f"실패만 있는 구간이 사용 축을 {run_row[:2]}로 덮었다"
            )
            assert run_row[2] is not None, "실패 구간이 generation_params를 지웠다"

            #: ── 3차: 성공 B ──
            await supervisor.resume(tenant_id=_WORKER_TENANT, job_id=job.job_id)
            third = await runner.run_next(tenant_id=_WORKER_TENANT)
            assert third is not None and third.job_id == job.job_id
            assert third.phase is JobPhase.SUCCEEDED, (
                f"3차 종단이 {third.phase}다 — 대본상 성공해야 한다"
            )

            #: ── 원장 ──
            run_row, calls = await _rows(sessions, execution_id)
            assert (run_row[0], run_row[1]) == (provider.name, "B1"), (
                f"최종 사용 축이 {run_row[:2]}다 — 마지막 성공(B1)이 아니다"
            )
            assert run_row[3] == _T0, "재개가 최초 begin 시각을 덮었다"
            async with sessions() as session:
                run_count = (
                    await session.execute(
                        text("SELECT count(*) FROM ai_run WHERE execution_id = :x"),
                        {"x": execution_id},
                    )
                ).scalar_one()
            assert run_count == 1, f"AI_RUN이 {run_count}행이다 — PK 충돌 또는 중복 적재"

            #: plan 1 + write 4(A1·실패·실패·B1) = 5건이 **누적**돼야 한다.
            assert len(calls) == 5, f"LLM_CALL이 {len(calls)}건이다: {calls}"
            assert len({row[0] for row in calls}) == 5, "call id가 중복됐다"
            outcomes = sorted(row[2] for row in calls)
            assert outcomes.count("ok") == 3, f"성공 호출이 {outcomes}다"
            assert outcomes.count(CallOutcome.PROVIDER_ERROR.value) == 2, (
                f"실패 호출이 {outcomes}다 — 실패 구간이 원장에 안 남았다"
            )
            #: 🔴 **적재 실패를 삼킨 적이 없다** — 삼켰다면 위 숫자가 우연히 맞은 것이다.
            assert set(runs.inner.swallowed_failures.values()) == {0}, (
                f"삼킨 적재 실패가 있다: {runs.inner.swallowed_failures}"
            )

            #: ── 워커가 정말 두 단계를 구간마다 불렀는가 ──
            assert (runs.begins, runs.finalizes) == (3, 3), (
                f"begin={runs.begins} finalize={runs.finalizes} — 구간마다 둘 다여야 한다"
            )
            assert runs.order == ["begin", "finalize"] * 3, (
                f"두 단계의 순서가 {runs.order}다 — begin이 먼저여야 부모가 먼저 선다"
            )
            assert provider.exhausted, (
                f"대본 {provider.writes}건만 소비됐다 — 재개가 학생을 다시 돌렸거나 건너뛰었다"
            )
        finally:
            await engine.dispose()

    _run(scenario())
