"""mapping_probe 워커 저장소 PG 구현 — source_profile·mapping_spec·agent_step 소비 (99 ⑮).

워커 골격의 `ProfileStore`·`SpecResultStore`·`AgentStepSink`(Protocol, import_mapping/probe/
stores.py)를 기존 ORM으로 영속한다. 레코드 필드가 ORM 컬럼과 1:1이라(값 대조 테스트가 강제)
매핑은 기계적이다 — 스키마·마이그레이션 신설 없음. ref 형식(`profile://`·`spec://`)과 §5
계약은 InMemory와 동일하게 유지한다(스킴 상수를 공유).

소유: 박진희 (db.repositories). 실 PG 왕복은 integration 마커(test_probe_pg_roundtrip.py)로
CI `integration-pg` 잡이 검증한다. FK 부모(agent_run·source_profile) 존재는 호출측(워커·
enqueue) 책임 — 이 저장소는 주어진 레코드를 1:1로 투영할 뿐이다.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ai.db.models import AgentStep as AgentStepRow
from ai.db.models import MappingSpec as MappingSpecRow
from ai.db.models import SourceProfile as SourceProfileRow
from ai.import_mapping.probe.stores import (
    PROFILE_SCHEME,
    SPEC_SCHEME,
    AgentStepRecord,
    ProfileRecord,
    SpecRecord,
    make_ref,
    parse_ref,
)


class PgProfileStore:
    """source_profile 영속. put=insert→ref, get=id 조회→레코드(없으면 None)."""

    def __init__(self, *, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sessionmaker = sessionmaker

    async def put(self, record: ProfileRecord) -> str:
        async with self._sessionmaker() as session, session.begin():
            session.add(
                SourceProfileRow(
                    id=record.id,
                    tenant_id=record.tenant_id,
                    file_hash=record.file_hash,
                    filename=record.filename,
                    sheets=record.sheets,
                    created_at=record.created_at,
                )
            )
        return make_ref(PROFILE_SCHEME, record.id)

    async def get(self, ref: str) -> ProfileRecord | None:
        row_id = parse_ref(ref, PROFILE_SCHEME)
        async with self._sessionmaker() as session:
            row = await session.get(SourceProfileRow, row_id)
            if row is None:
                return None
            return ProfileRecord(
                id=row.id,
                tenant_id=row.tenant_id,
                file_hash=row.file_hash,
                filename=row.filename,
                sheets=row.sheets,
                created_at=row.created_at,
            )


class PgSpecResultStore:
    """mapping_spec 영속. 산출 spec(MappingSpecDraft 직렬화)을 result_ref 뒤에 둔다."""

    def __init__(self, *, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sessionmaker = sessionmaker

    async def put(self, record: SpecRecord) -> str:
        async with self._sessionmaker() as session, session.begin():
            session.add(
                MappingSpecRow(
                    id=record.id,
                    tenant_id=record.tenant_id,
                    source_profile_id=record.source_profile_id,
                    version=record.version,
                    spec=record.spec,
                    status=record.status,
                    inferred_by_call=record.inferred_by_call,
                    probe_agent_run=record.probe_agent_run,
                    confirmed_at=record.confirmed_at,
                )
            )
        return make_ref(SPEC_SCHEME, record.id)

    async def get(self, ref: str) -> SpecRecord | None:
        row_id = parse_ref(ref, SPEC_SCHEME)
        async with self._sessionmaker() as session:
            row = await session.get(MappingSpecRow, row_id)
            if row is None:
                return None
            return SpecRecord(
                id=row.id,
                tenant_id=row.tenant_id,
                source_profile_id=row.source_profile_id,
                version=row.version,
                spec=row.spec,
                status=row.status,
                inferred_by_call=row.inferred_by_call,
                probe_agent_run=row.probe_agent_run,
                confirmed_at=row.confirmed_at,
            )


class PgAgentStepSink:
    """agent_step 영속. record=insert, steps=agent_run_id별 seq 오름차순 조회.

    tool_args_masked는 마스킹 통과분만(§5.2) — 이 저장소는 받은 레코드를 그대로 쓸 뿐
    마스킹을 다시 하지 않는다(도구·워커에서 이미 통과). agent_run_id는 AGENT_RUN.id(=job_id).
    """

    def __init__(self, *, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sessionmaker = sessionmaker

    async def record(self, step: AgentStepRecord) -> None:
        async with self._sessionmaker() as session, session.begin():
            session.add(
                AgentStepRow(
                    id=step.id,
                    agent_run_id=step.agent_run_id,
                    seq=step.seq,
                    node_name=step.node_name,
                    tool_called=step.tool_called,
                    tool_args_masked=step.tool_args_masked,
                    llm_call_id=step.llm_call_id,
                    outcome=step.outcome,
                )
            )

    async def steps(self, agent_run_id: UUID) -> tuple[AgentStepRecord, ...]:
        stmt = (
            select(AgentStepRow)
            .where(AgentStepRow.agent_run_id == agent_run_id)
            .order_by(AgentStepRow.seq)
        )
        async with self._sessionmaker() as session:
            rows = (await session.execute(stmt)).scalars().all()
        return tuple(
            AgentStepRecord(
                id=row.id,
                agent_run_id=row.agent_run_id,
                seq=row.seq,
                node_name=row.node_name,
                tool_called=row.tool_called,
                tool_args_masked=row.tool_args_masked,
                llm_call_id=row.llm_call_id,
                outcome=row.outcome,
            )
            for row in rows
        )
