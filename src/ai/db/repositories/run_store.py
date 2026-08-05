"""실행 원장 저장소 — `AI_RUN` + `LLM_CALL` (불변식 8 · 99 ㊻·ⓕ·㊼).

사양: `docs/06_erd.md` AI_RUN·LLM_CALL · `docs/02_ownership.md`.
소유: 박진희 (db.repositories). 선례는 `detection_store.py`·`inquiry_class_store.py`다.

🔴 **왜 AI_RUN과 LLM_CALL이 한 저장소인가 — FK 때문이다.**
`LlmCall.run_id`가 `ai_run.execution_id`를 **NOT NULL FK**로 참조한다. AI_RUN 행이 없으면
LLM_CALL 삽입이 FK 위반으로 죽는다. 둘의 삽입 순서가 곧 계약이라 저장소도 하나다.
`ai_run_orm`을 `detection_store`에서 여기로 옮긴 이유도 같다 — 감지·상담·분류가 **같은
매퍼**를 써야 버전 세트 10종이 경로마다 갈리지 않는다.

🔴 **왜 "수집 후 영속"인가 — recorder가 동기 함수다.**
`llm/gateway.py`의 `LlmCallRecorder`는 `Callable[[LlmCallRecord, ExecutionContext], None]`
로 **동기**다(게이트웨이는 B 소유라 시그니처를 바꾸지 않는다). 동기 콜백에서 async DB
쓰기를 할 수 없으므로 두 박자로 나눈다:

    ① 호출 중  — `LlmCallCollector`(동기)가 `execution_id`별로 레코드를 모은다
    ② 호출 후  — 소비자가 `record_run()`/`record_calls()`로 AI_RUN·LLM_CALL을 넣는다

부수 이득 3가지:
  · FK 순서(AI_RUN → LLM_CALL)가 자연히 지켜진다
  · **행 id를 수집 시점에 확정**하므로 실행 중에도 `llm_call_id`를 참조할 수 있다
    (`agent_step`·`inquiry_class`의 간선이 그래서 이어진다 — 99 ⓒ·ⓕ)
  · 영속 실패가 LLM 호출 경로 **밖에서** 일어난다

⚠ 수집기는 **프로세스 공용 싱글턴**이다(`default_llm_call_collector`). 조립부가
`recorder`를 넘기지 않아도 기본값으로 물리게 해서, 게이트웨이를 만드는 자리마다
수집기를 손으로 꿰는 배선 누락(㊻ⓐ가 정확히 그 사고였다)을 구조적으로 없앤다.
버킷은 `execution_id`로 격리되고 영속 시점에 뽑아낸다(`take`).

⚠ **버킷에 상한을 둔다**(불변식 6). 영속 없이 끝난 실행(예외·조립 미비)의 버킷이 남으면
프로세스 수명 동안 누적된다. 초과분은 **조용히 버리지 않고** 카운터+경고로 남긴다 —
게이트웨이 `_record`가 적재 실패를 다루는 방식과 같은 철학이다(관측이지 게이트가 아니다).
"""

from __future__ import annotations

import logging
import uuid
from collections import OrderedDict
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from decimal import Decimal
from functools import lru_cache
from typing import Final, NamedTuple, Protocol, runtime_checkable

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ai.contracts.execution import ExecutionContext, RunMetadata
from ai.contracts.llm import CallOutcome
from ai.db.models import AiRun, LlmCall

# ⚠ `LlmCallRecord`는 `contracts/`가 아니라 게이트웨이가 소유한다(B 파일 — 무접촉).
#   db가 상위 모듈 타입을 import하는 것은 `probe_stores.py`(→ import_mapping.probe.stores)·
#   `agent_job.py`(→ agents.job_store)와 같은 선례다 — 저장소가 소유자의 타입을 담는
#   방향이고, `llm/`은 `db/`를 import하지 않아 순환이 없다(측정 확인).
from ai.llm.gateway import LlmCallRecord

logger = logging.getLogger(__name__)

#: `model`이 None일 때 넣는 값 — `LlmCall.model`은 NOT NULL이다.
#: ⚠ 빈 문자열이 아니라 명시 토큰을 쓴다. 빈 문자열은 "모델명을 못 받았다"와 "빈 값이 왔다"를
#: 구분하지 못해 나중에 원가 회계에서 조용히 뭉개진다.
UNKNOWN_MODEL: Final = "unknown"

#: 영속 대기 실행 수 상한 — 초과 시 가장 오래된 버킷을 버린다(불변식 6).
MAX_PENDING_RUNS: Final = 256

#: 실행 1건당 수집 상한. 상담팩 22명 × 재생성 3 + plan 1 = 67을 여유로 덮는 값이다
#: (`docs/policies/langgraph_state.md` §1.2 기준 규모).
MAX_CALLS_PER_RUN: Final = 128


def system_utc_now() -> datetime:
    """주입용 기본 시계 — 이 한 곳만 벽시계를 읽는다(03 §3)."""
    return datetime.now(UTC)


class CollectedCall(NamedTuple):
    """수집된 호출 1건 — **행 id가 이미 정해져 있다**.

    id를 삽입 시점이 아니라 수집 시점에 발급하는 것이 이 모듈의 핵심 결정이다. 실행
    도중에 `llm_call_id`를 참조해야 하는 소비자(`agent_step`·`inquiry_class`)가 있고,
    나중에 발급하면 그 간선을 이을 방법이 없다.
    """

    id: uuid.UUID
    record: LlmCallRecord


def ai_run_orm(run: RunMetadata) -> AiRun:
    """RunMetadata → AI_RUN 행. 버전 세트 10종을 1:1로 옮긴다(대조 테스트가 강제)."""
    gen = run.generation_params
    return AiRun(
        execution_id=run.execution_id,
        tenant_id=run.tenant_id,
        capability=run.capability.value,
        pipeline_version=run.pipeline_version,
        engine_version=run.engine_version,
        threshold_version=run.threshold_version,
        prompt_version=run.prompt_version,
        schema_version=run.schema_version,
        contract_version=run.contract_version,
        graph_version=run.graph_version,
        taxonomy_version=run.taxonomy_version,
        verify_config_version=run.verify_config_version,
        difficulty_calib_version=run.difficulty_calib_version,
        model_provider=run.model_provider,
        model_name=run.model_name,
        generation_params=gen.model_dump(mode="json") if gen is not None else None,
        input_snapshot_hash=run.input_snapshot_hash,
        created_at=run.created_at,
    )


def llm_call_orm(
    call: CollectedCall, *, run_id: uuid.UUID, created_at: datetime
) -> LlmCall:
    """CollectedCall → LLM_CALL 행.

    ⚠ `usage`가 None일 수 있다(전송 실패 기록은 토큰이 없다) — **0으로 채운다.** 컬럼이
    NOT NULL이고 회계는 합산이라 0이 중립값이며, "모름"과 "0"의 구분은 `outcome`이 한다.
    ⚠ `cost_usd`는 provider가 준 값을 그대로 옮긴다 — 로컬 서버라 **0.0이 이미 규칙**이고
    (`llm/providers/openai_compat.py` "로컬 서버 — 토큰당 API 원가 없음") 여기서 단가표를
    새로 만들지 않는다(하드코딩 금지).
    """
    record = call.record
    usage = record.usage
    return LlmCall(
        id=call.id,
        run_id=run_id,
        role=record.role.value,
        provider=record.provider,
        model=record.model or UNKNOWN_MODEL,
        prompt_id=record.prompt_id,
        prompt_version=record.prompt_version,
        tokens_in=usage.tokens_in if usage is not None else 0,
        tokens_out=usage.tokens_out if usage is not None else 0,
        cost_usd=Decimal(str(usage.cost_usd)) if usage is not None else Decimal("0"),
        latency_ms=record.latency_ms,
        outcome=record.outcome.value,
        created_at=created_at,
    )


def last_success_id(calls: Sequence[CollectedCall]) -> uuid.UUID | None:
    """**마지막 성공 호출**의 행 id — `llm_call_id`가 가리킬 대상이다.

    🔴 여러 호출 중 하나를 골라야 하는데 **마지막 성공**을 택한 근거: 산출물을 만든 호출이
    그것이다. 게이트·파싱 재시도로 3번 불렀다면 앞의 둘은 **버려진 시도**이고 최종본을 낸
    것은 마지막 성공 호출이다 — 재현 추적(불변식 8)이 가리켜야 하는 대상이다. 시도 전량은
    LLM_CALL 행으로 남으니 정보가 사라지지도 않는다.

    성공이 하나도 없으면 None이고, 그때는 산출물도 없다(참조할 것이 없는 게 맞다).
    """
    for call in reversed(calls):
        if call.record.outcome is CallOutcome.OK:
            return call.id
    return None


class LlmCallCollector:
    """게이트웨이에 꽂는 **동기 recorder** — 호출 중에는 모으기만 한다.

    실패할 여지를 최소로 둔다: dict 조회와 append뿐이다. 게이트웨이가 recorder 예외를
    감싸 주기는 하지만, 관측이 조용히 누락되는 것 자체가 09 §1-10 ②가 막으려는 일이다.
    """

    def __init__(
        self,
        *,
        new_id: Callable[[], uuid.UUID] = uuid.uuid4,
        max_pending_runs: int = MAX_PENDING_RUNS,
        max_calls_per_run: int = MAX_CALLS_PER_RUN,
    ) -> None:
        self._pending: OrderedDict[uuid.UUID, list[CollectedCall]] = OrderedDict()
        self._new_id = new_id
        self._max_pending_runs = max_pending_runs
        self._max_calls_per_run = max_calls_per_run
        #: 상한 초과로 버린 수 — 조용한 누락 금지. 스모크·테스트가 이 값을 읽는다.
        self.dropped_calls = 0
        self.evicted_runs = 0

    def __call__(self, record: LlmCallRecord, context: ExecutionContext) -> None:
        execution_id = context.execution_id
        bucket = self._pending.setdefault(execution_id, [])
        self._pending.move_to_end(execution_id)  # LRU — 활성 실행을 뒤로 보낸다
        if len(bucket) >= self._max_calls_per_run:
            self.dropped_calls += 1
            logger.warning(
                "LLM 호출 수집 상한 초과 — 이 건은 버린다 execution_id=%s limit=%d",
                execution_id,
                self._max_calls_per_run,
            )
            return
        bucket.append(CollectedCall(self._new_id(), record))
        while len(self._pending) > self._max_pending_runs:
            stale_id, stale_calls = self._pending.popitem(last=False)
            self.evicted_runs += 1
            logger.warning(
                "영속되지 않은 LLM 호출 버킷 폐기 execution_id=%s calls=%d "
                "— 해당 실행의 조립부가 record_run/record_calls를 부르지 않았다.",
                stale_id,
                len(stale_calls),
            )

    def take(self, execution_id: uuid.UUID) -> tuple[CollectedCall, ...]:
        """이 실행의 수집분을 **꺼낸다**(버킷 제거) — 영속 직전에 한 번 부른다."""
        return tuple(self._pending.pop(execution_id, ()))

    def peek(self, execution_id: uuid.UUID) -> tuple[CollectedCall, ...]:
        """꺼내지 않고 본다 — 실행 도중 `llm_call_id`를 참조할 때 쓴다."""
        return tuple(self._pending.get(execution_id, ()))

    def last_success_id(self, execution_id: uuid.UUID) -> uuid.UUID | None:
        """실행 도중 참조용 — 방금 성공한 호출의 행 id(`last_success_id` 규칙 동일)."""
        return last_success_id(self.peek(execution_id))

    def reset(self) -> None:
        """테스트 격리용 — 버킷·카운터를 비운다."""
        self._pending.clear()
        self.dropped_calls = 0
        self.evicted_runs = 0


@lru_cache
def default_llm_call_collector() -> LlmCallCollector:
    """프로세스 공용 수집기 — 조립부 `recorder`의 **기본값**이다.

    기본값으로 두는 이유: ㊻ⓐ는 "게이트웨이를 만들 때 recorder를 넘기는 것을 잊었다"는
    사고였다. 기본이 no-op이면 새 조립부가 생길 때마다 같은 사고가 반복된다.
    """
    return LlmCallCollector()


@runtime_checkable
class RunStore(Protocol):
    """실행 원장 저장소 — 라우터·워커는 이 타입에만 의존한다."""

    async def record_run(
        self, run: RunMetadata, calls: Sequence[CollectedCall]
    ) -> None: ...

    async def record_calls(
        self, *, execution_id: uuid.UUID, calls: Sequence[CollectedCall]
    ) -> None: ...


class InMemoryRunStore:
    """프로세스 인메모리 — CI 기본. 재시작 소실·멀티워커 비공유."""

    def __init__(self, clock: Callable[[], datetime] = system_utc_now) -> None:
        self.runs: dict[uuid.UUID, RunMetadata] = {}
        self.calls: list[LlmCall] = []
        self._clock = clock

    async def record_run(
        self, run: RunMetadata, calls: Sequence[CollectedCall]
    ) -> None:
        self.runs[run.execution_id] = run
        await self.record_calls(execution_id=run.execution_id, calls=calls)

    async def record_calls(
        self, *, execution_id: uuid.UUID, calls: Sequence[CollectedCall]
    ) -> None:
        created_at = self._clock()
        self.calls.extend(
            llm_call_orm(call, run_id=execution_id, created_at=created_at)
            for call in calls
        )

    def calls_of(self, execution_id: uuid.UUID) -> list[LlmCall]:
        """`execution_id` → LLM_CALL 역추적 — 불변식 8의 재현 경로다."""
        return [call for call in self.calls if call.run_id == execution_id]

    def clear(self) -> None:
        self.runs.clear()
        self.calls.clear()


class PgRunStore:
    """PG 영속 — AI_RUN + LLM_CALL을 **한 트랜잭션**에서 순서대로 넣는다(FK).

    ⚠ 적재 실패는 **로그로 남기고 삼킨다** — 관측이지 게이트가 아니다. 감지 원장
    (`detection_store`, fail-closed)과 정반대 판단인데, 근거는 소비자다: 감지 원장은
    baseline 축적분이라 반쪽이면 다음 판정이 왜곡되지만, LLM_CALL은 회계·추적용이고
    이것 때문에 강사의 초안 요청을 500으로 되돌리면 관측이 기능을 이긴 것이 된다
    (게이트웨이 `_record`가 이미 같은 철학으로 서 있다).
    """

    def __init__(
        self,
        *,
        sessionmaker: async_sessionmaker[AsyncSession],
        clock: Callable[[], datetime] = system_utc_now,
    ) -> None:
        self._sessionmaker = sessionmaker
        self._clock = clock

    async def record_run(
        self, run: RunMetadata, calls: Sequence[CollectedCall]
    ) -> None:
        await self._write(run.execution_id, run, calls)

    async def record_calls(
        self, *, execution_id: uuid.UUID, calls: Sequence[CollectedCall]
    ) -> None:
        if not calls:
            return
        await self._write(execution_id, None, calls)

    async def _write(
        self,
        execution_id: uuid.UUID,
        run: RunMetadata | None,
        calls: Sequence[CollectedCall],
    ) -> None:
        created_at = self._clock()
        try:
            async with self._sessionmaker() as session, session.begin():
                if run is not None:
                    session.add(ai_run_orm(run))  # ① FK 대상이 먼저 선다
                for call in calls:
                    session.add(
                        llm_call_orm(call, run_id=execution_id, created_at=created_at)
                    )
        except SQLAlchemyError:
            logger.warning(
                "실행 원장 적재 실패 — 요청은 성공 처리 execution_id=%s ai_run=%s calls=%d",
                execution_id,
                run is not None,
                len(calls),
                exc_info=True,
            )


__all__ = [
    "MAX_CALLS_PER_RUN",
    "MAX_PENDING_RUNS",
    "UNKNOWN_MODEL",
    "CollectedCall",
    "InMemoryRunStore",
    "LlmCallCollector",
    "PgRunStore",
    "RunStore",
    "ai_run_orm",
    "default_llm_call_collector",
    "last_success_id",
    "llm_call_orm",
    "system_utc_now",
]
