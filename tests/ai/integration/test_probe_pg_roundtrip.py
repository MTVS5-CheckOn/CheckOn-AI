"""mapping_probe 저장소 PG 왕복 + PostgresSaver 재개 — 실 PG 통합 (99 ⑮ · §5).

**테스트 DB 전략(D-② 확정):**
- 단위·계약: fake(InMemory) 저장소 — test_probe_stores·test_probe_worker가 결정론 검증.
- 통합(이 파일): 실 PG 왕복. store_factory PG 3종 + AsyncPostgresSaver 체크포인트 재개.

`integration` 마커로 기본 실행 제외. PG 미가용이면 skip. 각 시나리오는 전용 NullPool 엔진 +
단일 이벤트 루프로 돈다(test_pg_store_roundtrip 관례). 스키마는 create_all로 자체 준비.

FK 부모는 호출측 책임이라 여기서 실제 부모를 만든다 — spec은 source_profile을, agent_step은
ai_run→agent_run(PgJobStore.add로 유효 행 생성)을 먼저 넣는다. agent_step.agent_run_id가
AGENT_RUN.id(=job_id)를 참조함을 실 FK로 증명한다(§5 · fix 커밋과 짝).
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

pytestmark = pytest.mark.integration

_NOW = datetime(2026, 7, 27, tzinfo=UTC)
_HASH = "sha256:" + "0" * 64
_Scenario = Callable[[async_sessionmaker[AsyncSession]], Awaitable[None]]


async def _with_pg(scenario: _Scenario) -> str:
    from ai.db.models import Base
    from ai.db.settings import get_db_settings

    engine = create_async_engine(get_db_settings().database_url, poolclass=NullPool)
    try:
        try:
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
        except Exception:  # noqa: BLE001 — 접속 불가 → skip
            return "skip"
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)  # checkfirst=True
        await scenario(async_sessionmaker(engine, expire_on_commit=False))
    finally:
        await engine.dispose()
    return "ok"


def _run(scenario: _Scenario) -> None:
    if asyncio.run(_with_pg(scenario)) == "skip":
        pytest.skip("실 PG 미가용 — docker compose -f compose.dev.yml up (99 ⑫)")


# ───────────────────────── 저장소 3종 왕복 ─────────────────────────


def _profile_record(pid: UUID, tenant: str) -> object:
    from ai.import_mapping.probe.stores import ProfileRecord, serialize_profile
    from ai.import_mapping.profiling import ColumnProfile, SheetProfile, SourceProfile

    cols = (
        ColumnProfile(
            name="반", n_total=1, n_null=0, n_unique=1, dtype_guess="string", suspect_pii=False
        ),
    )
    profile = SourceProfile(filename="r.xlsx", sheets=(SheetProfile("s", 1, cols, ()),))
    return ProfileRecord(
        id=pid, tenant_id=tenant, file_hash="h",
        filename="r.xlsx", sheets=serialize_profile(profile), created_at=_NOW,
    )


def test_profile_store_roundtrip() -> None:
    """PgProfileStore.put→get 왕복이 저장분을 재반환(불투명 ref 형식 불변)."""
    pid, tenant = uuid.uuid4(), f"t_{uuid.uuid4().hex[:8]}"

    async def scenario(sm: async_sessionmaker[AsyncSession]) -> None:
        from ai.db.repositories.probe_stores import PgProfileStore

        store = PgProfileStore(sessionmaker=sm)
        rec = _profile_record(pid, tenant)
        ref = await store.put(rec)  # type: ignore[arg-type]
        assert ref == f"profile://{pid}"
        assert await store.get(ref, tenant_id=tenant) == rec
        assert await store.get(f"profile://{uuid.uuid4()}", tenant_id=tenant) is None
        #: 🔴 **술어가 SQL에 걸린다**(99 #40) — 남의 테넌트로는 같은 ref가 안 풀린다.
        assert await store.get(ref, tenant_id=f"{tenant}-stranger") is None

    _run(scenario)


def test_spec_store_roundtrip() -> None:
    """PgSpecResultStore.put→get 왕복 — source_profile 부모(FK) 선적재."""
    pid, sid, tenant = uuid.uuid4(), uuid.uuid4(), f"t_{uuid.uuid4().hex[:8]}"

    async def scenario(sm: async_sessionmaker[AsyncSession]) -> None:
        from ai.db.repositories.probe_stores import PgProfileStore, PgSpecResultStore
        from ai.import_mapping.probe.stores import SpecRecord

        await PgProfileStore(sessionmaker=sm).put(_profile_record(pid, tenant))  # type: ignore[arg-type]
        store = PgSpecResultStore(sessionmaker=sm)
        rec = SpecRecord(
            id=sid, tenant_id=tenant, source_profile_id=pid, version=1,
            spec={"resolved": [], "unresolved": [], "overall_confidence": 0.0},
            status="succeeded", probe_agent_run=uuid.uuid4(),
        )
        ref = await store.put(rec)
        assert ref == f"spec://{sid}"
        assert await store.get(ref, tenant_id=rec.tenant_id) == rec
        assert await store.get(ref, tenant_id=f"{rec.tenant_id}-stranger") is None

    _run(scenario)


async def _make_agent_run(sm: async_sessionmaker[AsyncSession], *, tenant: str) -> UUID:
    """ai_run→agent_run(mapping_probe, queued) 부모를 만들고 job_id(=agent_run.id) 반환."""
    from ai.contracts.agents import (
        OperationKind,
        PriorityClass,
        WorkerJob,
        WorkerKind,
    )
    from ai.db.models import AiRun
    from ai.db.repositories.agent_job import PgJobStore

    job_id, execution_id = uuid.uuid4(), uuid.uuid4()
    async with sm() as session, session.begin():
        session.add(
            AiRun(
                execution_id=execution_id, tenant_id=tenant, capability="import",
                pipeline_version="0.1", engine_version="0.1", schema_version="0.1",
                contract_version="0.1", input_snapshot_hash="h", created_at=_NOW,
            )
        )
    job = WorkerJob(
        job_id=job_id, execution_id=execution_id, tenant_id=tenant,
        worker_kind=WorkerKind.MAPPING_PROBE, operation=OperationKind.MAPPING_PROBE_RESOLVE,
        payload_ref=f"profile://{uuid.uuid4()}", payload_hash=_HASH,
        priority_class=PriorityClass.STANDARD, queued_at=_NOW,
    )
    await PgJobStore(sessionmaker=sm).add(job)
    return job_id


def test_agent_step_sink_roundtrip_fk_to_job_id() -> None:
    """PgAgentStepSink.record→steps 왕복 — agent_run_id=job_id가 실 FK를 만족(fix 증명)."""
    tenant = f"t_{uuid.uuid4().hex[:8]}"

    async def scenario(sm: async_sessionmaker[AsyncSession]) -> None:
        from ai.db.repositories.probe_stores import PgAgentStepSink
        from ai.import_mapping.probe.stores import AgentStepRecord

        job_id = await _make_agent_run(sm, tenant=tenant)  # AGENT_RUN.id = job_id
        sink = PgAgentStepSink(sessionmaker=sm)
        for seq in range(3):
            await sink.record(
                AgentStepRecord(
                    id=uuid.uuid4(), agent_run_id=job_id, seq=seq, node_name="tool_call",
                    tool_called="get_unique_values", tool_args_masked={"column": "반"},
                    llm_call_id=None, outcome="ok",
                )
            )
        steps = await sink.steps(job_id)
        assert [s.seq for s in steps] == [0, 1, 2]  # seq 오름차순
        assert all(s.agent_run_id == job_id for s in steps)
        assert await sink.steps(uuid.uuid4()) == ()

    _run(scenario)


# ───────────────────────── PostgresSaver 체크포인트 재개 ─────────────────────────


def test_postgres_saver_checkpoint_survives_reopen() -> None:
    """propose_spec 앞 중단→새 saver(같은 PG)로 재개해도 완결 — 체크포인트 PG 생존(§4)."""
    outcome = asyncio.run(_checkpoint_scenario())
    if outcome == "skip":
        pytest.skip("실 PG 미가용 — docker compose -f compose.dev.yml up (99 ⑫)")


async def _checkpoint_scenario() -> str:
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

    from ai.agents.checkpointer import checkpoint_connection_string
    from ai.db.settings import get_db_settings
    from ai.import_mapping.probe.graph import build_probe_graph
    from ai.import_mapping.probe.planner import FakeProbePlanner
    from ai.import_mapping.probe.state import MappingProbeState
    from ai.import_mapping.probe.tools import FakeProbeTools
    from ai.import_mapping.profiling import ColumnProfile, SheetProfile, SourceProfile

    settings = get_db_settings()
    # 사전 연결 프로브 — from_conn_string 진입 자체가 접속이라 미가용이면 여기서 skip.
    probe = create_async_engine(settings.database_url, poolclass=NullPool)
    try:
        async with probe.connect() as pconn:
            await pconn.execute(text("SELECT 1"))
    except Exception:  # noqa: BLE001 — 접속 불가 → skip
        return "skip"
    finally:
        await probe.dispose()

    conn = checkpoint_connection_string(settings)
    headers = ["원생명", "반", "등원일", "상태", "동의"]
    cols = tuple(
        ColumnProfile(
            name=h, n_total=1, n_null=0, n_unique=1, dtype_guess="string", suspect_pii=False
        )
        for h in headers
    )
    profile = SourceProfile(filename="r.xlsx", sheets=(SheetProfile("s", 1, cols, ()),))
    thread = f"probe-{uuid.uuid4().hex[:8]}"
    config = {"configurable": {"thread_id": thread}}
    state = MappingProbeState(
        tenant_id="t1", source_profile_id=uuid.uuid4(), sheets_meta={"columns": headers}
    )

    def _graph(saver: AsyncPostgresSaver) -> object:
        return build_probe_graph(
            tools=FakeProbeTools(profile), planner=FakeProbePlanner(),
            loop_max=6, checkpointer=saver, interrupt_before=("propose_spec",),
        )

    try:
        async with AsyncPostgresSaver.from_conn_string(conn) as saver:
            try:
                await saver.setup()  # 체크포인트 테이블 준비(접속 확인 겸)
            except Exception:  # noqa: BLE001 — 접속 불가 → skip
                return "skip"
            first = await _graph(saver).ainvoke(state, config=config)  # type: ignore[attr-defined]
            assert first.get("spec_draft") is None  # propose_spec 앞에서 중단

        # 새 saver 인스턴스(같은 PG) — 재개가 PG 체크포인트에서 이어진다.
        async with AsyncPostgresSaver.from_conn_string(conn) as saver2:
            resumed = await _graph(saver2).ainvoke(None, config=config)  # type: ignore[attr-defined]
            assert resumed["spec_draft"] is not None
            assert len(resumed["spec_draft"].resolved) == 5
    finally:
        pass
    return "ok"
