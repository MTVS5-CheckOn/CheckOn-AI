"""워커 입력·산출·스텝 영속 경계 — Protocol + InMemory (langgraph_state §5 · §3.3).

**결정론 골격 1단계:** 인터페이스 + 인메모리 구현만. 실 PG(source_profile·mapping_spec·
agent_step ORM 소비)는 같은 인터페이스로 후속(99_open_items ⑨ spec 캐시 PG와 묶음).

규약(사용자 확정):
① 레코드 필드는 대응 ORM 컬럼(source_profile·mapping_spec·agent_step)과 **1:1**이다 —
   값 대조 테스트로 고정해 후속 PG 연결이 기계적이 되게 한다(id는 PK라 레코드에 포함).
② 참조는 인메모리 키가 아니라 **불투명 URI**(`profile://…`·`spec://…`) — PG로 바꿔도
   ref 형식·§5 계약이 안 바뀐다.
"""

from __future__ import annotations

import dataclasses
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from ai.import_mapping.profiling import ColumnProfile, SheetProfile, SourceProfile

#: ref URI 스킴 — InMemory·PG 구현이 동일 형식을 쓰도록 공개(§5 계약 불변).
PROFILE_SCHEME = "profile"
SPEC_SCHEME = "spec"


def make_ref(scheme: str, key: UUID) -> str:
    """불투명 참조 URI — 후속 PG 구현도 동일 형식을 쓴다."""
    return f"{scheme}://{key}"


def parse_ref(ref: str, scheme: str) -> UUID:
    """URI에서 키 추출. 스킴 불일치는 ValueError(잘못된 참조)."""
    prefix = f"{scheme}://"
    if not ref.startswith(prefix):
        raise ValueError(f"{scheme} 참조가 아님: {ref}")
    return UUID(ref[len(prefix) :])


# ───────────────────────── 레코드(ORM 1:1) ─────────────────────────


class ProfileRecord(BaseModel):
    """source_profile 행과 1:1. sheets = 직렬화된 SourceProfile."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: UUID
    tenant_id: str
    file_hash: str
    filename: str
    sheets: dict[str, Any]
    created_at: datetime


class SpecRecord(BaseModel):
    """mapping_spec 행과 1:1. spec = 직렬화된 MappingSpecDraft."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: UUID
    tenant_id: str
    source_profile_id: UUID
    version: int
    spec: dict[str, Any]
    status: str
    inferred_by_call: UUID | None = None
    probe_agent_run: UUID | None = None
    confirmed_at: datetime | None = None


class AgentStepRecord(BaseModel):
    """agent_step 행과 1:1. tool_args_masked·observation은 마스킹 통과분만."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: UUID
    agent_run_id: UUID
    seq: int
    node_name: str
    tool_called: str | None
    tool_args_masked: dict[str, Any]
    llm_call_id: UUID | None
    outcome: str


# ───────────────────────── 직렬화 어댑터(변환 한 곳) ─────────────────────────


def serialize_profile(profile: SourceProfile) -> dict[str, Any]:
    """SourceProfile → source_profile.sheets(JSONB용 dict). 원본 값 없음(통계·마스킹 샘플만)."""
    return dataclasses.asdict(profile)


def deserialize_profile(sheets: dict[str, Any]) -> SourceProfile:
    """source_profile.sheets(dict) → SourceProfile. 워커 도구 입력 복원(변환 한 곳)."""
    return SourceProfile(
        filename=str(sheets["filename"]),
        sheets=tuple(
            SheetProfile(
                name=str(s["name"]),
                n_rows=int(s["n_rows"]),
                columns=tuple(ColumnProfile(**c) for c in s["columns"]),
                sample_rows=tuple(dict(r) for r in s["sample_rows"]),
            )
            for s in sheets["sheets"]
        ),
    )


# ───────────────────────── 저장소 인터페이스 + InMemory ─────────────────────────


# 저장소는 async — 레포 표준(DetectionStore·JobStore·IdempotencyStore 전부 asyncpg).
# InMemory는 await할 I/O가 없지만 PG 구현과 같은 시그니처를 갖도록 async로 맞춘다.


class ProfileStore(Protocol):
    async def put(self, record: ProfileRecord) -> str: ...

    async def get(self, ref: str) -> ProfileRecord | None: ...


class SpecResultStore(Protocol):
    async def put(self, record: SpecRecord) -> str: ...

    async def get(self, ref: str) -> SpecRecord | None: ...


class AgentStepSink(Protocol):
    async def record(self, step: AgentStepRecord) -> None: ...

    async def steps(self, agent_run_id: UUID) -> tuple[AgentStepRecord, ...]: ...


class InMemoryProfileStore:
    def __init__(self) -> None:
        self._rows: dict[UUID, ProfileRecord] = {}

    async def put(self, record: ProfileRecord) -> str:
        self._rows[record.id] = record
        return make_ref(PROFILE_SCHEME, record.id)

    async def get(self, ref: str) -> ProfileRecord | None:
        return self._rows.get(parse_ref(ref, PROFILE_SCHEME))


class InMemorySpecResultStore:
    def __init__(self) -> None:
        self._rows: dict[UUID, SpecRecord] = {}

    async def put(self, record: SpecRecord) -> str:
        self._rows[record.id] = record
        return make_ref(SPEC_SCHEME, record.id)

    async def get(self, ref: str) -> SpecRecord | None:
        return self._rows.get(parse_ref(ref, SPEC_SCHEME))


class InMemoryAgentStepSink:
    def __init__(self) -> None:
        self._rows: dict[UUID, list[AgentStepRecord]] = {}

    async def record(self, step: AgentStepRecord) -> None:
        self._rows.setdefault(step.agent_run_id, []).append(step)

    async def steps(self, agent_run_id: UUID) -> tuple[AgentStepRecord, ...]:
        return tuple(self._rows.get(agent_run_id, ()))
