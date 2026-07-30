"""counsel_pack 입력·산출·스텝 영속 경계 — Protocol + InMemory.

`probe/stores.py`와 대칭이다. ref 형식도 같은 선례를 따른다(`profile://`·`spec://` →
`context://`·`draft://`) — 형식이 갈리면 나중에 플랫폼으로 승격할 때 마이그레이션이 생긴다.

**capability 간 직접 import 금지**(03 §2)라 `probe/stores.py`를 재사용하지 않고 자체 정의한다.
`AgentStepRecord`·`AgentStepSink`가 두 capability에 중복되는 문제는 99 D 후속 안건으로
등록했다(세 번째 워커가 붙을 때 플랫폼 승격 판단). 두 정의가 갈리지 않도록 **`agent_step`
ORM 컬럼과의 값 대조 테스트**를 양쪽에 건다.

레코드 필드는 대응 ORM(`draft`·`agent_step`) 컬럼과 1:1이다 — 후속 PG 연결이 기계적이 되게.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Final, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from ai.contracts.composition import DraftContext

#: ref URI 스킴 — probe의 profile://·spec:// 선례를 따른다.
CONTEXT_SCHEME: Final = "context"
DRAFT_SCHEME: Final = "draft"


def make_ref(scheme: str, key: UUID) -> str:
    """불투명 참조 URI — PG 구현도 같은 형식을 쓴다."""
    return f"{scheme}://{key}"


def parse_ref(ref: str, scheme: str) -> UUID:
    """URI에서 키 추출. 스킴 불일치는 ValueError(잘못된 참조)."""
    prefix = f"{scheme}://"
    if not ref.startswith(prefix):
        raise ValueError(f"{scheme} 참조가 아님: {ref}")
    return UUID(ref[len(prefix) :])


# ───────────────────────── 레코드 ─────────────────────────


class ContextBundleRecord(BaseModel):
    """학생 컨텍스트 묶음 1건 — `context_ref`가 가리키는 대상.

    state에 본문을 넣지 않기 위한 저장소다(§1.2 ⑨). `content_hash`는 state의
    `context_hash`와 대조된다(불변식 ④).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: UUID
    tenant_id: str
    class_ref: str
    contexts: dict[str, DraftContext]
    content_hash: str
    created_at: datetime


class DraftRecord(BaseModel):
    """`draft` 행과 1:1 — ERD DRAFT 컬럼 그대로."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: UUID
    run_id: UUID
    agent_run_id: UUID | None
    tenant_id: str
    kind: str
    student_ref: str
    guardian_ref: str
    label_snapshot: dict[str, Any]
    status: str
    fail_reason: str | None
    created_at: datetime


class AgentStepRecord(BaseModel):
    """`agent_step` 행과 1:1. `tool_args_masked`는 마스킹 통과분만.

    ⚠ `probe/stores.py`에도 같은 타입이 있다(capability 간 import 금지 — 99 D 후속 안건).
    두 정의가 갈리지 않도록 양쪽에 ORM 값 대조 테스트를 건다.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: UUID
    agent_run_id: UUID
    seq: int
    node_name: str
    tool_called: str | None
    tool_args_masked: dict[str, Any]
    llm_call_id: UUID | None
    outcome: str


# ───────────────────────── 인터페이스 + InMemory ─────────────────────────


class ContextStore(Protocol):
    async def put(self, record: ContextBundleRecord) -> str: ...

    async def get(self, ref: str) -> ContextBundleRecord | None: ...


class DraftResultStore(Protocol):
    async def put(self, record: DraftRecord) -> str: ...

    async def get(self, ref: str) -> DraftRecord | None: ...


class AgentStepSink(Protocol):
    async def record(self, step: AgentStepRecord) -> None: ...

    async def steps(self, agent_run_id: UUID) -> tuple[AgentStepRecord, ...]: ...


class InMemoryContextStore:
    def __init__(self) -> None:
        self._rows: dict[UUID, ContextBundleRecord] = {}

    async def put(self, record: ContextBundleRecord) -> str:
        self._rows[record.id] = record
        return make_ref(CONTEXT_SCHEME, record.id)

    async def get(self, ref: str) -> ContextBundleRecord | None:
        return self._rows.get(parse_ref(ref, CONTEXT_SCHEME))


class InMemoryDraftResultStore:
    def __init__(self) -> None:
        self._rows: dict[UUID, DraftRecord] = {}

    async def put(self, record: DraftRecord) -> str:
        self._rows[record.id] = record
        return make_ref(DRAFT_SCHEME, record.id)

    async def get(self, ref: str) -> DraftRecord | None:
        return self._rows.get(parse_ref(ref, DRAFT_SCHEME))


class InMemoryAgentStepSink:
    def __init__(self) -> None:
        self._rows: dict[UUID, list[AgentStepRecord]] = {}

    async def record(self, step: AgentStepRecord) -> None:
        self._rows.setdefault(step.agent_run_id, []).append(step)

    async def steps(self, agent_run_id: UUID) -> tuple[AgentStepRecord, ...]:
        return tuple(self._rows.get(agent_run_id, ()))


__all__ = [
    "CONTEXT_SCHEME",
    "DRAFT_SCHEME",
    "AgentStepRecord",
    "AgentStepSink",
    "ContextBundleRecord",
    "ContextStore",
    "DraftRecord",
    "DraftResultStore",
    "InMemoryAgentStepSink",
    "InMemoryContextStore",
    "InMemoryDraftResultStore",
    "make_ref",
    "parse_ref",
]
