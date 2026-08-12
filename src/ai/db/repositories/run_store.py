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

from sqlalchemy import select
from sqlalchemy import update as sa_update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ai.contracts.execution import (
    Capability,
    ExecutionContext,
    GenerationParams,
    RunMetadata,
)
from ai.contracts.llm import CallOutcome
from ai.db.models import AiRun, LlmCall, LlmPayload

# 본문 포착·저장 직전 훅은 `llm_payload`가 소유한다 — 의존은 한 방향뿐이다
# (run_store → llm_payload). 99 ㉝.
from ai.db.repositories.llm_payload import (
    CapturedBody,
    CollectedPayload,
    payload_orm,
    payload_refusal_reason,
    reset_captured,
    take_captured,
)

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

    `payload`는 전송 본문이다(99 ㉝) — provider 래퍼가 포착한 것을 수집기가 **같은 id**로
    짝지어 붙인다. 래퍼가 없는 경로(래핑 전 조립부·직결 mock)에서는 None이다.
    """

    id: uuid.UUID
    record: LlmCallRecord
    payload: CollectedPayload | None = None


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
    ⚠ `cost_usd`는 provider가 준 값을 그대로 옮긴다 — 여기서 단가표를 만들지 않는다
    (하드코딩 금지). 🔴 **현재 그 값은 항상 0.0이고 그건 "공짜"가 아니라 "미측정"이다**
    (`llm/providers/openai_compat.py` · 99 ⓠ). 이 컬럼을 합산해 원가 리포트를 내면 안 된다.
    ⚠ 종전 이 자리에 적혀 있던 근거("로컬 서버라 토큰당 원가가 없다")는 **인용 대상 문장이
    이미 사라진 뒤에도 남아 있었다** — 외부 백엔드 전환(99 ⓟ) 때 함께 갱신되지 않았다.
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


#: 🔴 **확정 축** — 같은 `execution_id`의 기존 행과 하나라도 다르면 **같은 실행이 아니다**.
#: 실행 시작 시점에 이미 다 정해져 있고, 재개해도 바뀌지 않는다.
CONFIRMED_AXES: Final = (
    "execution_id",
    "tenant_id",
    "capability",
    "input_snapshot_hash",
    "pipeline_version",
    "engine_version",
    "threshold_version",
    "prompt_version",
    "schema_version",
    "contract_version",
    "graph_version",
    "taxonomy_version",
    "verify_config_version",
    "difficulty_calib_version",
)
#: 🔴 **사용 축** — 실행이 **끝나야** 아는 값이다. 마지막 **성공** 호출 기준으로 갱신한다.
#: ⚠ **값 → None은 갱신이 아니다** — 실패만 있던 재개 구간이 앞의 성공을 지우면 거짓이 된다.
USAGE_AXES: Final = ("model_provider", "model_name", "generation_params")
#: 🔴 **최초 쓰기 축** — 최초 `begin_run` 값만 유지한다(재개·최종화 시각으로 안 덮는다).
CREATED_AXIS: Final = "created_at"

#: 삼킨 실패를 세는 **닫힌 키**(99 #46) — 임의 문자열 키를 만들지 않는다.
SWALLOWED_OPERATIONS: Final = ("record_run", "finalize_run", "record_calls")


class RunIdentityConflict(RuntimeError):
    """🔴 **같은 `execution_id`인데 확정 축이 다르다** — 의미 충돌이다.

    ⚠ **SQL 장애가 아니다.** fail-open 블록에서 삼키면 *"다른 실행이 같은 원장 행을
    덮어썼다"* 가 조용히 지나간다 — 재현성(불변식 8)이 통째로 무너지는 자리다.
    ⚠ 문면에 **스냅숏 내용·접속정보를 싣지 않는다** — 어긋난 **필드 이름**까지만 말한다.
    """


def _confirmed_mismatch(existing: RunMetadata, incoming: RunMetadata) -> list[str]:
    """확정 축 중 값이 다른 필드 이름 — 🔴 **값 자체는 안 싣는다.**"""
    return [
        axis
        for axis in CONFIRMED_AXES
        if getattr(existing, axis) != getattr(incoming, axis)
    ]


def _require_same_identity(existing: RunMetadata, incoming: RunMetadata) -> None:
    mismatched = _confirmed_mismatch(existing, incoming)
    if mismatched:
        raise RunIdentityConflict(
            f"같은 execution_id인데 확정 축이 다르다: {', '.join(mismatched)}"
        )


def _require_empty_usage(run: RunMetadata) -> None:
    """🔴 **begin 단계에서 사용 결과를 지어내지 않는다** — 아직 아무것도 안 불렀다."""
    filled = [axis for axis in USAGE_AXES if getattr(run, axis) is not None]
    if filled:
        raise ValueError(f"begin_run에 사용 축이 채워져 있다: {', '.join(filled)}")


def _merged_usage(existing: RunMetadata, incoming: RunMetadata) -> RunMetadata:
    """사용 축 **last-write-wins(non-null)** + `created_at` 최초값 유지.

    ⚠ 세 축을 **따로** 본다 — 한 축만 새로 성공해도 그 축만 갱신된다.
    """
    update: dict[str, object] = {CREATED_AXIS: getattr(existing, CREATED_AXIS)}
    for axis in USAGE_AXES:
        fresh = getattr(incoming, axis)
        update[axis] = fresh if fresh is not None else getattr(existing, axis)
    return existing.model_copy(update=update)


def last_success_call(calls: Sequence[CollectedCall]) -> CollectedCall | None:
    """마지막 **성공** 호출 — 🔴 배열의 마지막 행이 아니다.

    실패·timeout이 배열 끝이라고 그 provider를 최종본으로 적으면 **거짓**이다.
    ⚠ `last_success_id()`가 이 함수를 쓴다 — 선택 규칙을 두 벌로 만들지 않는다(99 #02).
    """
    for call in reversed(calls):
        if call.record.outcome is CallOutcome.OK:
            return call
    return None


def last_success_id(calls: Sequence[CollectedCall]) -> uuid.UUID | None:
    """**마지막 성공 호출**의 행 id — `llm_call_id`가 가리킬 대상이다.

    🔴 여러 호출 중 하나를 골라야 하는데 **마지막 성공**을 택한 근거: 산출물을 만든 호출이
    그것이다. 게이트·파싱 재시도로 3번 불렀다면 앞의 둘은 **버려진 시도**이고 최종본을 낸
    것은 마지막 성공 호출이다 — 재현 추적(불변식 8)이 가리켜야 하는 대상이다. 시도 전량은
    LLM_CALL 행으로 남으니 정보가 사라지지도 않는다.

    성공이 하나도 없으면 None이고, 그때는 산출물도 없다(참조할 것이 없는 게 맞다).
    """
    chosen = last_success_call(calls)
    return chosen.id if chosen is not None else None


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
        #: 본문 상한 초과로 **본문만** 버린 수(메타는 남았다 · 99 ㉝ C-3).
        self.dropped_payloads = 0

    def __call__(self, record: LlmCallRecord, context: ExecutionContext) -> None:
        execution_id = context.execution_id
        # 🔴 슬롯은 **먼저** 비운다. 호출 상한으로 이 건을 버려도 포착된 본문이 남아 있으면
        #    다음 호출이 남의 본문을 물려받는다(짝이 틀린 원장이 없는 원장보다 나쁘다).
        captured = take_captured()
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
        call_id = self._new_id()
        bucket.append(
            CollectedCall(call_id, record, self._payload(call_id, captured))
        )
        while len(self._pending) > self._max_pending_runs:
            stale_id, stale_calls = self._pending.popitem(last=False)
            self.evicted_runs += 1
            logger.warning(
                "영속되지 않은 LLM 호출 버킷 폐기 execution_id=%s calls=%d "
                "— 해당 실행의 조립부가 record_run/record_calls를 부르지 않았다.",
                stale_id,
                len(stale_calls),
            )

    def _payload(
        self, call_id: uuid.UUID, captured: CapturedBody | None
    ) -> CollectedPayload | None:
        """포착된 본문을 이 호출의 id로 짝짓는다 — 없거나 상한 초과면 None."""
        if captured is None:  # provider 래퍼가 없는 경로(직결 mock 등) — 정직한 부재
            return None
        if captured.oversized:
            self.dropped_payloads += 1
            logger.warning(
                "LLM 본문 상한 초과 — 본문만 버린다(메타는 남는다) call_id=%s", call_id
            )
            return None
        return CollectedPayload(
            call_id=call_id,
            request_masked=captured.request_masked,
            response_masked=captured.response_masked,
            response_uncertain=captured.response_uncertain,
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
        """테스트 격리용 — 버킷·카운터·인계 슬롯을 비운다."""
        self._pending.clear()
        self.dropped_calls = 0
        self.evicted_runs = 0
        self.dropped_payloads = 0
        reset_captured()


@lru_cache
def default_llm_call_collector() -> LlmCallCollector:
    """프로세스 공용 수집기 — 조립부 `recorder`의 **기본값**이다.

    기본값으로 두는 이유: ㊻ⓐ는 "게이트웨이를 만들 때 recorder를 넘기는 것을 잊었다"는
    사고였다. 기본이 no-op이면 새 조립부가 생길 때마다 같은 사고가 반복된다.
    """
    return LlmCallCollector()


@runtime_checkable
class RunStore(Protocol):
    """실행 원장 저장소 — 라우터·워커는 이 타입에만 의존한다.

    🔴 **표면이 넷이고 실패 정책이 둘로 갈린다**(99 #46):

    | 메서드 | 의미 | 실패 |
    | --- | --- | --- |
    | `begin_run` | 실행 시작 행 보장 | **fail-closed** — 원장을 못 세우면 실행하지 않는다 |
    | `finalize_run` | 사용 축 갱신 + 호출 추가 | fail-open(SQL 장애만) |
    | `record_run` | 기존 one-shot 원자 저장 | fail-open |
    | `record_calls` | 기존 행에 호출 추가 | fail-open |

    ⚠ **`record_run`을 `begin_run`+`finalize_run`으로 구현하면 안 된다** — 기존 소비자
    (classify·refine·problem_generation·mapping_probe)는 **한 트랜잭션**을 전제한다.
    ⚠ **의미 충돌(`RunIdentityConflict`)은 fail-open이 아니다** — SQL 장애가 아니다.
    """

    async def begin_run(self, run: RunMetadata) -> None:
        """실행 **시작** 행을 보장한다 — 사용 축은 전부 `None`이어야 한다.

        멱등이다: 같은 확정 축으로 다시 부르면 성공하고 `created_at`은 최초값을 지킨다.
        """
        ...

    async def finalize_run(
        self, run: RunMetadata, calls: Sequence[CollectedCall]
    ) -> None:
        """실행 **종료** — 사용 축을 갱신하고 새 호출만 덧붙인다."""
        ...

    async def record_run(
        self, run: RunMetadata, calls: Sequence[CollectedCall]
    ) -> None: ...

    async def record_calls(
        self, *, execution_id: uuid.UUID, calls: Sequence[CollectedCall]
    ) -> None: ...


def accepted_payloads(
    calls: Sequence[CollectedCall],
) -> tuple[tuple[CollectedPayload, ...], int]:
    """**저장 직전 훅**(masking_redaction §3) — 통과분과 거부 건수.

    거부는 구조화 로그로 남기고 요청은 성공시킨다(fail-open · ㊻과 같은 철학).
    ⚠ §3 표의 "`AI_RUN` 오류 기록"은 문자 그대로 못 한다 — `AI_RUN`에 오류 컬럼이 없고
    `db/models.py`는 양자 승인 파일이다. 로그 + 카운터로 남기고 컬럼 신설은 99에 등재했다.
    """
    accepted: list[CollectedPayload] = []
    refused = 0
    for call in calls:
        if call.payload is None:
            continue
        reason = payload_refusal_reason(call.payload)
        if reason is not None:
            refused += 1
            logger.warning(
                "LLM_PAYLOAD 저장 거부 call_id=%s reason=%s — 호출은 성공 처리",
                call.id,
                reason,
            )
            continue
        accepted.append(call.payload)
    return tuple(accepted), refused


#: LLM_CALL 멱등 판정에 쓰는 값 컬럼 — `created_at`은 **적재 시각**이라 뺀다
#: (재개 시 같은 호출을 다시 넘겨도 적재 시각은 달라진다).
_CALL_VALUE_COLUMNS: Final = (
    "run_id",
    "role",
    "provider",
    "model",
    "prompt_id",
    "prompt_version",
    "tokens_in",
    "tokens_out",
    "cost_usd",
    "latency_ms",
    "outcome",
)


def _same_call_row(left: LlmCall, right: LlmCall) -> bool:
    return all(
        getattr(left, column) == getattr(right, column)
        for column in _CALL_VALUE_COLUMNS
    )


class InMemoryRunStore:
    """프로세스 인메모리 — CI 기본. 재시작 소실·멀티워커 비공유."""

    def __init__(self, clock: Callable[[], datetime] = system_utc_now) -> None:
        self.runs: dict[uuid.UUID, RunMetadata] = {}
        self.calls: list[LlmCall] = []
        #: call_id → LLM_PAYLOAD 행. PG의 1:1 PK/FK 관계를 dict로 흉내낸다.
        self.payloads: dict[uuid.UUID, LlmPayload] = {}
        #: 저장 직전 훅이 거부한 수 — 조용한 누락 금지.
        self.refused_payloads = 0
        #: 🔴 **삼킨 실패를 센다**(99 #46) — 로그만 남기면 읽는 사람이 0명이다.
        #:   정상 동작에서는 전부 0이다.
        self.swallowed_failures: dict[str, int] = dict.fromkeys(SWALLOWED_OPERATIONS, 0)
        self._clock = clock

    async def begin_run(self, run: RunMetadata) -> None:
        """실행 시작 행 — 🔴 **dict overwrite가 아니다**(확정 축을 대조한다)."""
        _require_empty_usage(run)
        existing = self.runs.get(run.execution_id)
        if existing is None:
            self.runs[run.execution_id] = run
            return
        _require_same_identity(existing, run)  # 멱등 — created_at·사용 축 무변경

    async def finalize_run(
        self, run: RunMetadata, calls: Sequence[CollectedCall]
    ) -> None:
        existing = self.runs.get(run.execution_id)
        if existing is None:
            #: begin 전제가 깨졌다 — 호출자 요청을 새 오류로 뒤집지 않고 **센다**.
            self.swallowed_failures["finalize_run"] += 1
            logger.warning(
                "finalize_run에 시작 행이 없다 — begin_run 전제가 깨졌다 execution_id=%s",
                run.execution_id,
            )
            return
        _require_same_identity(existing, run)
        self.runs[run.execution_id] = _merged_usage(existing, run)
        await self._append_calls(run.execution_id, calls)

    async def record_run(
        self, run: RunMetadata, calls: Sequence[CollectedCall]
    ) -> None:
        #: ⚠ **공개 2단계를 안 부른다** — one-shot 원자성이 이 메서드의 계약이다.
        self.runs[run.execution_id] = run
        await self.record_calls(execution_id=run.execution_id, calls=calls)

    async def record_calls(
        self, *, execution_id: uuid.UUID, calls: Sequence[CollectedCall]
    ) -> None:
        await self._append_calls(execution_id, calls)

    async def _append_calls(
        self, execution_id: uuid.UUID, calls: Sequence[CollectedCall]
    ) -> None:
        """🔴 **call id 기준 멱등** — 재개가 이전 호출을 다시 넘겨도 행이 안 는다.

        ⚠ 같은 id에 **다른 전문**이면 의미 충돌이다(조용히 무시하면 원장이 거짓이 된다).
        ⚠ 중복 하나 때문에 **같은 호출의 새 항목까지** 버리지 않는다 — 새 것만 덧붙인다.
        """
        created_at = self._clock()
        known = {row.id: row for row in self.calls}
        fresh: list[CollectedCall] = []
        for call in calls:
            existing = known.get(call.id)
            candidate = llm_call_orm(call, run_id=execution_id, created_at=created_at)
            if existing is None:
                fresh.append(call)
                continue
            if not _same_call_row(existing, candidate):
                raise RunIdentityConflict(
                    f"같은 llm_call id에 다른 전문이 왔다: {call.id}"
                )
        self.calls.extend(
            llm_call_orm(call, run_id=execution_id, created_at=created_at)
            for call in fresh
        )
        payloads, refused = accepted_payloads(fresh)
        self.refused_payloads += refused
        # LLM_CALL을 먼저 넣은 뒤 본문을 붙인다 — PG의 FK 순서와 같은 순서다.
        self.payloads.update(
            {payload.call_id: payload_orm(payload) for payload in payloads}
        )

    def calls_of(self, execution_id: uuid.UUID) -> list[LlmCall]:
        """`execution_id` → LLM_CALL 역추적 — 불변식 8의 재현 경로다."""
        return [call for call in self.calls if call.run_id == execution_id]

    def payload_of(self, call_id: uuid.UUID) -> LlmPayload | None:
        """`call_id` → 전송 본문 — 재현 축의 마지막 칸이다(99 ㉝)."""
        return self.payloads.get(call_id)

    def clear(self) -> None:
        self.runs.clear()
        self.calls.clear()
        self.payloads.clear()
        self.refused_payloads = 0
        self.swallowed_failures = dict.fromkeys(SWALLOWED_OPERATIONS, 0)


class PgRunStore:
    """PG 영속 — AI_RUN + LLM_CALL을 **한 트랜잭션**에서 순서대로 넣는다(FK).

    ⚠ 적재 실패는 **로그로 남기고 삼킨다** — 관측이지 게이트가 아니다. 감지 원장
    (`detection_store`, fail-closed)과 정반대 판단인데, 근거는 소비자다: 감지 원장은
    baseline 축적분이라 반쪽이면 다음 판정이 왜곡되지만, LLM_CALL은 회계·추적용이고
    이것 때문에 강사의 초안 요청을 500으로 되돌리면 관측이 기능을 이긴 것이 된다
    (`counsel/worker.py`의 실패 경로와 같은 판단).

    🔴 **`begin_run`만 예외다**(99 #46) — 그 행은 `DRAFT.run_id` FK의 **부모**라, 못 세우면
    초안이 저장될 수 없다. 관측이 아니라 **선행 조건**이므로 fail-closed다.
    """

    def __init__(
        self,
        *,
        sessionmaker: async_sessionmaker[AsyncSession],
        clock: Callable[[], datetime] = system_utc_now,
    ) -> None:
        self._sessionmaker = sessionmaker
        self._clock = clock
        self.refused_payloads = 0
        #: 🔴 삼킨 실패를 **operation별로** 센다(99 #46) — 닫힌 키.
        self.swallowed_failures: dict[str, int] = dict.fromkeys(SWALLOWED_OPERATIONS, 0)

    # ── 2단계 ──────────────────────────────────────────────────

    async def begin_run(self, run: RunMetadata) -> None:
        """실행 시작 행 — 🔴 **fail-closed**. 예외가 호출자까지 올라간다.

        ⚠ **없는 행은 `SELECT`로 잠글 수 없다** — 동시 begin이 둘 다 *"행이 없다"* 를 보고
        INSERT하면 `pk_ai_run`이 하나를 죽인다. `ON CONFLICT DO NOTHING` **뒤에 반드시
        기존 행을 정확 대조**한다 — 충돌을 성공으로 간주하면 **다른 실행이 같은 원장 행을
        쓰는 것**을 못 본다(99 #46).
        """
        _require_empty_usage(run)
        async with self._sessionmaker() as session, session.begin():
            await session.execute(
                pg_insert(AiRun)
                .values(_ai_run_values(run))
                .on_conflict_do_nothing(index_elements=[AiRun.execution_id])
            )
            stored = await self._select_run(session, run.execution_id)
            if stored is None:  # pragma: no cover — 방금 넣었으므로 도달 불가
                raise RunIdentityConflict(
                    f"begin 직후 시작 행을 못 읽었다: {run.execution_id}"
                )
            _require_same_identity(stored, run)

    async def finalize_run(
        self, run: RunMetadata, calls: Sequence[CollectedCall]
    ) -> None:
        """사용 축 갱신 + 새 호출만 추가 — SQL 장애는 fail-open, 의미 충돌은 예외."""
        payloads, refused = accepted_payloads(calls)
        try:
            async with self._sessionmaker() as session, session.begin():
                stored = await self._select_run(
                    session, run.execution_id, for_update=True
                )
                if stored is None:
                    #: begin 전제가 깨졌다 — FK 오류로 흘려보내지 않고 **센다**.
                    self.swallowed_failures["finalize_run"] += 1
                    logger.warning(
                        "finalize_run에 시작 행이 없다 — begin_run 전제가 깨졌다 "
                        "execution_id=%s",
                        run.execution_id,
                    )
                    return
                _require_same_identity(stored, run)
                merged = _merged_usage(stored, run)
                await session.execute(
                    sa_update(AiRun)
                    .where(AiRun.execution_id == run.execution_id)
                    .values(
                        model_provider=merged.model_provider,
                        model_name=merged.model_name,
                        generation_params=(
                            merged.generation_params.model_dump(mode="json")
                            if merged.generation_params is not None
                            else None
                        ),
                    )
                )
                fresh = await self._fresh_calls(session, run.execution_id, calls)
                self._add_calls(session, run.execution_id, fresh, payloads)
        except RunIdentityConflict:
            raise  # 🔴 의미 충돌은 fail-open이 아니다
        except SQLAlchemyError:
            self.swallowed_failures["finalize_run"] += 1
            logger.warning(
                "실행 원장 최종화 실패 — 요청은 성공 처리 execution_id=%s calls=%d",
                run.execution_id,
                len(calls),
                exc_info=True,
            )
        else:
            self.refused_payloads += refused

    # ── 기존 one-shot ──────────────────────────────────────────

    async def record_run(
        self, run: RunMetadata, calls: Sequence[CollectedCall]
    ) -> None:
        #: ⚠ **공개 2단계를 안 부른다** — 기존 소비자는 「AI_RUN과 LLM_CALL이 한
        #:   트랜잭션」을 전제한다. 두 호출로 쪼개면 호출 저장이 실패해도 AI_RUN만 남는다.
        await self._write("record_run", run.execution_id, run, calls)

    async def record_calls(
        self, *, execution_id: uuid.UUID, calls: Sequence[CollectedCall]
    ) -> None:
        await self._write("record_calls", execution_id, None, calls)

    # ── 내부 ───────────────────────────────────────────────────

    @staticmethod
    async def _select_run(
        session: AsyncSession, execution_id: uuid.UUID, *, for_update: bool = False
    ) -> RunMetadata | None:
        statement = select(AiRun).where(AiRun.execution_id == execution_id)
        if for_update:
            statement = statement.with_for_update()
        row = (await session.execute(statement)).scalar_one_or_none()
        return _run_metadata_of(row) if row is not None else None

    async def _fresh_calls(
        self,
        session: AsyncSession,
        execution_id: uuid.UUID,
        calls: Sequence[CollectedCall],
    ) -> list[CollectedCall]:
        """🔴 **call id 기준 멱등** — 재개가 이전 호출을 다시 넘겨도 행이 안 는다.

        ⚠ 같은 id에 **다른 전문**이면 의미 충돌이다. ⚠ 중복 하나 때문에 **같은 호출의
        새 항목까지** 버리지 않는다.
        """
        if not calls:
            return []
        created_at = self._clock()
        known = {
            row.id: row
            for row in (
                await session.execute(
                    select(LlmCall).where(LlmCall.id.in_([c.id for c in calls]))
                )
            )
            .scalars()
            .all()
        }
        fresh: list[CollectedCall] = []
        for call in calls:
            existing = known.get(call.id)
            if existing is None:
                fresh.append(call)
                continue
            candidate = llm_call_orm(call, run_id=execution_id, created_at=created_at)
            if not _same_call_row(existing, candidate):
                raise RunIdentityConflict(
                    f"같은 llm_call id에 다른 전문이 왔다: {call.id}"
                )
        return fresh

    def _add_calls(
        self,
        session: AsyncSession,
        execution_id: uuid.UUID,
        calls: Sequence[CollectedCall],
        payloads: Sequence[CollectedPayload],
    ) -> None:
        created_at = self._clock()
        fresh_ids = {call.id for call in calls}
        for call in calls:
            session.add(llm_call_orm(call, run_id=execution_id, created_at=created_at))
        # ② 🔴 본문은 **LLM_CALL 뒤**다 — `llm_payload.call_id`가 PK 겸 FK다.
        #    ⚠ **새로 넣은 호출의 본문만** 붙인다 — 이미 있는 call의 payload를 다시 넣으면
        #      PK 충돌로 그 트랜잭션의 **새 호출까지 전량 롤백**된다.
        for payload in payloads:
            if payload.call_id in fresh_ids:
                session.add(payload_orm(payload))

    async def _write(
        self,
        operation: str,
        execution_id: uuid.UUID,
        run: RunMetadata | None,
        calls: Sequence[CollectedCall],
    ) -> None:
        payloads, refused = accepted_payloads(calls)
        self.refused_payloads += refused
        try:
            async with self._sessionmaker() as session, session.begin():
                if run is not None:
                    session.add(ai_run_orm(run))  # ① FK 대상이 먼저 선다
                self._add_calls(session, execution_id, calls, payloads)
        except SQLAlchemyError:
            self.swallowed_failures[operation] += 1
            logger.warning(
                "실행 원장 적재 실패 — 요청은 성공 처리 execution_id=%s ai_run=%s "
                "calls=%d payloads=%d",
                execution_id,
                run is not None,
                len(calls),
                len(payloads),
                exc_info=True,
            )


def _ai_run_values(run: RunMetadata) -> dict[str, object]:
    """`ai_run_orm`과 **같은 매핑**을 dict로 — INSERT…ON CONFLICT에 쓴다(#02 회피)."""
    row = ai_run_orm(run)
    return {
        column.name: getattr(row, column.name) for column in AiRun.__table__.columns
    }


def _run_metadata_of(row: AiRun) -> RunMetadata:
    """AI_RUN 행 → `RunMetadata` — 확정 축 대조와 사용 축 병합의 입력."""
    gen = row.generation_params
    return RunMetadata(
        execution_id=row.execution_id,
        tenant_id=row.tenant_id,
        capability=Capability(row.capability),
        pipeline_version=row.pipeline_version,
        engine_version=row.engine_version,
        threshold_version=row.threshold_version,
        prompt_version=row.prompt_version,
        schema_version=row.schema_version,
        contract_version=row.contract_version,
        graph_version=row.graph_version,
        taxonomy_version=row.taxonomy_version,
        verify_config_version=row.verify_config_version,
        difficulty_calib_version=row.difficulty_calib_version,
        model_provider=row.model_provider,
        model_name=row.model_name,
        generation_params=GenerationParams.model_validate(gen) if gen else None,
        input_snapshot_hash=row.input_snapshot_hash,
        created_at=row.created_at,
    )


__all__ = [
    "CONFIRMED_AXES",
    "CREATED_AXIS",
    "SWALLOWED_OPERATIONS",
    "USAGE_AXES",
    "RunIdentityConflict",
    "last_success_call",
    "MAX_CALLS_PER_RUN",
    "MAX_PENDING_RUNS",
    "UNKNOWN_MODEL",
    "accepted_payloads",
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
