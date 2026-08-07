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

from collections.abc import Mapping
from datetime import datetime
from typing import Any, Final, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from ai.contracts.composition import DraftContext, PlanOutcome, StudentResult

#: ref URI 스킴 — probe의 profile://·spec:// 선례를 따른다.
CONTEXT_SCHEME: Final = "context"
DRAFT_SCHEME: Final = "draft"
#: 팩 결과(요약 + 학생별 결과 포인터) — probe의 profile://(입력)→spec://(산출) 대칭.
#: `draft://`는 **학생별 본문 전용**이라 결이 다른 산출물에 스킴을 나눈다.
PACK_SCHEME: Final = "pack"


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
    """`draft` 행과 1:1 — ERD DRAFT 컬럼 + 본문.

    ⚠ **게이트 통과분만 저장된다** — 저장 호출이 게이트 통과 직후에 있다(graph.py). 순서가
    곧 불변식 1(LLM 산출물은 게이트를 거쳐야 저장된다)이다.
    """

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

    content: str
    """게이트를 통과한 초안 본문. state에는 싣지 않는다(§1.2 ⑨ — 본문 미복제·ref만)."""


class CounselPackResultRecord(BaseModel):
    """팩 1건의 결과 계약 — `result_ref`(`pack://…`)가 가리키는 대상.

    `succeed(result_ref=…)`에 이 ref를 넣는다. 요약과 학생별 결과 **포인터**만 담고 본문은
    담지 않는다(본문은 `draft://`). 부분 미해결도 유효한 결과 계약이므로 Job은 succeeded다
    (error_codes §2.5).

    ⚠ **대응 ERD 테이블이 없다**(AGENT_RUN은 `result_ref` 포인터만·DRAFT는 학생별) —
    PG 영속 시 테이블 신설이 필요하고 `db/models.py`는 양자 승인 파일이라 B와 함께 정한다
    (99 D 후속 등록).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: UUID
    tenant_id: str
    class_ref: str
    summary: str
    results: tuple[StudentResult, ...]
    created_at: datetime

    #: plan 결과 사유 — **강조점 0건의 이유**(99 ㉲). state에만 두면 체크포인트를 뒤져야
    #: 읽을 수 있는데, 잡이 끝나면 그건 재개 대상이 아니라 사후에 볼 사람이 없다.
    #: ⚠ 기본값을 둔 이유는 **기존 레코드 호환**이다 — 이 필드 이전에 저장된 pack은 사유를
    #: 모르며, 그걸 `ok`로 읽는 것은 거짓이 아니라 "그때는 안 쟀다"에 가장 가깝다.
    plan_outcome: PlanOutcome = PlanOutcome.OK
    plan_dropped: int = 0

    #: 학생별 **검증 통과 강조점** — `state.emphasis_points`가 밖으로 나오는 경로다(99 ㉮).
    #: 🔴 refine이 이 값을 못 받아서 **다듬기 턴마다 강조점이 사라졌다.** 최초 생성은
    #: `graph.py:201`에서 `state`를 직접 읽지만 refine은 라우터가 부르고, 라우터가 볼 수
    #: 있는 것은 결과 계약뿐이라 **여기 없으면 도달 경로가 없다.**
    #: ⚠ `plan_outcome`과 **같은 이유·같은 형태**다 — 잡이 끝나면 체크포인트는 재개 대상이
    #: 아니라 사후에 읽을 사람이 없다(㉲). 기본값도 같은 이유로 둔다(기존 레코드 호환).
    #: 🔴 **빈 값이 「없음」인지 「안 쟀음」인지는 `plan_outcome`이 가른다** — `OK`+빈 값은
    #: 고를 게 없었던 것, `ALL_DROPPED`는 전량 드롭, `LLM_FAILED`·`UNPARSED`는 plan 실패다.
    #: 이 필드 하나만 보고 판단하지 마라.
    #: ⚠ 타입은 `graph.py:201`이 넘기는 것과 같다(`tuple[str, ...]`) — 새 타입을 만들지 않았다.
    emphasis_points: Mapping[str, tuple[str, ...]] = {}


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

    async def get(self, ref: str, *, tenant_id: str) -> ContextBundleRecord | None:
        """참조 해소 — **테넌트 스코프 필수**(CLAUDE.md §4 "테넌트 격리 없는 쿼리는 반려").

        다른 테넌트의 ref를 들고 와도 저장소 수준에서 해소되지 않는다. 워커의 추가 검증은
        이중 방어다.
        """
        ...


class DraftResultStore(Protocol):
    async def put(self, record: DraftRecord) -> str: ...

    async def get(self, ref: str, *, tenant_id: str) -> DraftRecord | None:
        """참조 해소 — **테넌트 스코프 필수**(`ContextStore.get`과 동형).

        ⚠ 종전에는 이 인자가 없어 `ContextStore`와 **비대칭**이었다. 라우터가 잘못된 잡의
        `draft://`를 들고 오면 저장소가 그대로 내줬고, **초안 본문에는 다른 학생의 문장이
        들어 있다** — 입력 묶음보다 유출 피해가 큰 쪽에 방어가 없었던 셈이다.
        상위(라우터·워커)의 귀속 검증은 이중 방어이고 여기가 마지막 층이다.
        """
        ...


class PackResultStore(Protocol):
    """팩 결과(요약+포인터) 저장소 — `result_ref`의 대상."""

    async def put(self, record: CounselPackResultRecord) -> str: ...

    async def get(self, ref: str) -> CounselPackResultRecord | None: ...


class AgentStepSink(Protocol):
    async def record(self, step: AgentStepRecord) -> None: ...

    async def steps(self, agent_run_id: UUID) -> tuple[AgentStepRecord, ...]: ...


class InMemoryContextStore:
    def __init__(self) -> None:
        self._rows: dict[UUID, ContextBundleRecord] = {}

    async def put(self, record: ContextBundleRecord) -> str:
        self._rows[record.id] = record
        return make_ref(CONTEXT_SCHEME, record.id)

    async def get(self, ref: str, *, tenant_id: str) -> ContextBundleRecord | None:
        row = self._rows.get(parse_ref(ref, CONTEXT_SCHEME))
        if row is None or row.tenant_id != tenant_id:  # 저장소 수준 격리
            return None
        return row


class InMemoryDraftResultStore:
    def __init__(self) -> None:
        self._rows: dict[UUID, DraftRecord] = {}

    async def put(self, record: DraftRecord) -> str:
        self._rows[record.id] = record
        return make_ref(DRAFT_SCHEME, record.id)

    async def get(self, ref: str, *, tenant_id: str) -> DraftRecord | None:
        row = self._rows.get(parse_ref(ref, DRAFT_SCHEME))
        if row is None or row.tenant_id != tenant_id:  # 저장소 수준 격리
            return None
        return row

    def ids(self) -> tuple[UUID, ...]:
        """저장된 draft_id 목록 — 재개가 이중 발급하지 않는지 확인하는 테스트용."""
        return tuple(self._rows)


class InMemoryPackResultStore:
    def __init__(self) -> None:
        self._rows: dict[UUID, CounselPackResultRecord] = {}

    async def put(self, record: CounselPackResultRecord) -> str:
        self._rows[record.id] = record
        return make_ref(PACK_SCHEME, record.id)

    async def get(self, ref: str) -> CounselPackResultRecord | None:
        return self._rows.get(parse_ref(ref, PACK_SCHEME))


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
    "PACK_SCHEME",
    "AgentStepRecord",
    "AgentStepSink",
    "ContextBundleRecord",
    "ContextStore",
    "CounselPackResultRecord",
    "DraftRecord",
    "DraftResultStore",
    "InMemoryAgentStepSink",
    "InMemoryContextStore",
    "InMemoryDraftResultStore",
    "InMemoryPackResultStore",
    "PackResultStore",
    "make_ref",
    "parse_ref",
]
