"""문제 생성 워커 조립 seam — provider·저장소·체크포인터를 한곳에서 주입한다."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from typing import Protocol
from uuid import UUID

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver

from ai.agents.checkpointer import open_checkpointer
from ai.agents.supervisor import Supervisor, system_utc_now
from ai.contracts.agents import WorkerJob, WorkerKind
from ai.contracts.execution import Capability, ExecutionContext, VersionSet
from ai.contracts.graphrag import GraphContextService
from ai.contracts.llm import LlmError, RedactionBlocked
from ai.contracts.problem_generation import (
    ProblemGenerationOutcome,
    ProblemRequest,
)
from ai.db.repositories.problem_revision_store import PgProblemRevisionStore
from ai.db.repositories.problem_store import PgProblemItemStore
from ai.db.repositories.run_store import (
    LlmCallCollector,
    RunStore,
    default_llm_call_collector,
)
from ai.db.session import get_sessionmaker
from ai.db.settings import DbSettings, get_db_settings
from ai.llm.determinism import deterministic_params
from ai.llm.prompts.loader import load_prompt_template
from ai.problem_generation.application.ports import (
    CandidateStore,
    ProblemItemStore,
    ProblemRevisionStore,
    ProblemSetStore,
)
from ai.problem_generation.application.workflow import DiagnosisCallable
from ai.problem_generation.bootstrap import build_problem_workflow
from ai.problem_generation.enqueue import ProblemRequestStore
from ai.problem_generation.infrastructure.config import load_verify_config
from ai.problem_generation.infrastructure.memory_store import (
    InMemoryCandidateStore,
    InMemoryProblemItemStore,
    InMemoryProblemSetStore,
)
from ai.problem_generation.provider import ProblemProviders, build_problem_gateway
from ai.runtime.errors import (
    DomainException,
    RedactionUncertain,
    domain_error_for,
)
from ai.runtime.tracing import require_tracing_disabled

logger = logging.getLogger(__name__)

_PG = "pg"
_ITEM_PROMPT_ID = "pg.items.v1"
_PIPELINE_VERSION = "0.1.0"
_ENGINE_VERSION = "problem-generation-0.1"
_SCHEMA_VERSION = "0.1"
_CONTRACT_VERSION = "0.1"
_GRAPH_VERSION = "curriculum-five-area-v1"
_ERROR_REQUEST_MISSING = "problem_request_missing"
_ERROR_WORKER_INTERNAL = "problem_worker_internal"


class ProblemResultStore(Protocol):
    """워커의 불투명 result_ref로 최종 결과를 역참조하는 저장 경계."""

    async def put(
        self,
        *,
        tenant_id: str,
        job_id: UUID,
        result: ProblemGenerationOutcome,
    ) -> str: ...

    async def get(
        self, result_ref: str, *, tenant_id: str
    ) -> ProblemGenerationOutcome | None: ...


class _InMemoryProblemRequestStore:
    def __init__(self) -> None:
        self._records: dict[str, ProblemRequest] = {}
        self._lock = asyncio.Lock()

    async def put(self, request: ProblemRequest) -> str:
        request_ref = f"problem-request:{request.tenant_id}:{request.request_id}"
        async with self._lock:
            existing = self._records.get(request_ref)
            if existing is not None and existing != request:
                raise ValueError(f"문제 생성 요청 참조 충돌: {request_ref}")
            self._records[request_ref] = request
        return request_ref

    async def get(
        self, request_ref: str, *, tenant_id: str
    ) -> ProblemRequest | None:
        async with self._lock:
            request = self._records.get(request_ref)
            if request is None or request.tenant_id != tenant_id:
                return None
            return request


class _InMemoryProblemResultStore:
    def __init__(self) -> None:
        self._records: dict[tuple[str, str], ProblemGenerationOutcome] = {}
        self._lock = asyncio.Lock()

    async def put(
        self,
        *,
        tenant_id: str,
        job_id: UUID,
        result: ProblemGenerationOutcome,
    ) -> str:
        result_ref = f"problem-result:{job_id}"
        key = (tenant_id, result_ref)
        async with self._lock:
            existing = self._records.get(key)
            if existing is not None and existing != result:
                raise ValueError(f"문제 생성 결과 참조 충돌: {result_ref}")
            self._records[key] = result
        return result_ref

    async def get(
        self, result_ref: str, *, tenant_id: str
    ) -> ProblemGenerationOutcome | None:
        async with self._lock:
            return self._records.get((tenant_id, result_ref))


@dataclass(frozen=True, slots=True)
class ProblemRuntimeStores:
    """PG 영속 구현 승인 전 사용하는 주입 가능한 저장소 묶음."""

    requests: ProblemRequestStore
    results: ProblemResultStore
    candidates: CandidateStore
    items: ProblemItemStore
    sets: ProblemSetStore


@lru_cache
def default_problem_runtime_stores() -> ProblemRuntimeStores:
    """프로세스 수명 동안 요청·결과·문항을 공유하는 인메모리 저장소."""

    return ProblemRuntimeStores(
        requests=_InMemoryProblemRequestStore(),
        results=_InMemoryProblemResultStore(),
        candidates=InMemoryCandidateStore(),
        items=InMemoryProblemItemStore(),
        sets=InMemoryProblemSetStore(),
    )


def build_tenant_scoped_item_store(
    *,
    tenant_id: str,
    settings: DbSettings | None = None,
) -> ProblemItemStore | None:
    """`store_backend=pg`면 테넌트 스코프 PG 저장소, 아니면 **`None`**.

    🔴 **테넌트를 여기서 받는 이유**: `ProblemItemStore` Protocol의 `save`/`get`에
    `tenant_id`가 없어(계약 고정) **인스턴스를 테넌트 단위로 스코프**한다(09 §2-20.3).
    러너는 `lease_next(tenant_id, worker_kind)`로만 잡을 집으므로 한 러너가 다른 테넌트의
    잡을 실행할 경로가 없다 — 요청 테넌트로 만든 저장소가 그 잡에 항상 맞다.

    🔴 **`None`은 "실패"가 아니라 "교체할 것이 없다"** — 인메모리 저장소에는 테넌트 축이
    아예 없으므로 만들 게 없고, **이미 배선된 저장소를 그대로 써야 한다.** 여기서
    `default_problem_runtime_stores().items`를 돌려주면 `problem_runtime_stores(item_store=…)`로
    주입한 저장소를 조용히 덮어써서 **주입 seam이 죽는다**(그 회귀를
    `test_problem_store_injection_seam_uses_the_supplied_item_store`가 잡는다).
    """
    settings = settings or get_db_settings()
    if settings.store_backend != _PG:
        return None
    return PgProblemItemStore(sessionmaker=get_sessionmaker(), tenant_id=tenant_id)


def build_tenant_scoped_revision_store(
    *, tenant_id: str, settings: DbSettings | None = None
) -> ProblemRevisionStore | None:
    """`store_backend=pg`면 테넌트 스코프 리비전 저장소를 만든다."""

    settings = settings or get_db_settings()
    if settings.store_backend != _PG:
        return None
    return PgProblemRevisionStore(
        sessionmaker=get_sessionmaker(), tenant_id=tenant_id
    )


def problem_runtime_stores(
    *,
    request_store: ProblemRequestStore | None = None,
    result_store: ProblemResultStore | None = None,
    candidate_store: CandidateStore | None = None,
    item_store: ProblemItemStore | None = None,
    set_store: ProblemSetStore | None = None,
) -> ProblemRuntimeStores:
    """저장 포트 교체 seam — PG 저장소 승인 뒤 이 조립 지점만 바꾼다."""

    defaults = default_problem_runtime_stores()
    return ProblemRuntimeStores(
        requests=request_store or defaults.requests,
        results=result_store or defaults.results,
        candidates=candidate_store or defaults.candidates,
        items=item_store or defaults.items,
        sets=set_store or defaults.sets,
    )


@lru_cache
def _default_memory_checkpointer() -> InMemorySaver:
    """잡 수명과 함께 살아야 하는 프로세스 공용 PG 체크포인터."""

    return InMemorySaver()


def reset_problem_memory_runtime() -> None:
    """PG 테스트가 명시적으로 호출하는 인메모리 저장소·체크포인터 리셋."""

    default_problem_runtime_stores.cache_clear()
    _default_memory_checkpointer.cache_clear()


@asynccontextmanager
async def _open_saver(settings: DbSettings) -> AsyncIterator[BaseCheckpointSaver]:  # type: ignore[type-arg]
    if settings.store_backend == _PG:
        async with open_checkpointer(settings) as saver:
            yield saver
    else:
        yield _default_memory_checkpointer()


class ProblemGenerationRunner:
    """problem_generation 잡을 lease해 워크플로·원장·결과를 수렴시킨다."""

    def __init__(
        self,
        *,
        supervisor: Supervisor,
        request_store: ProblemRequestStore,
        result_store: ProblemResultStore,
        workflow: object,
        run_store: RunStore,
        call_log: LlmCallCollector,
        verify_config_version: str,
        prompt_version: str,
        lease_owner: str,
        now: Callable[[], datetime] = system_utc_now,
    ) -> None:
        self._supervisor = supervisor
        self._requests = request_store
        self._results = result_store
        self._workflow = workflow
        self._runs = run_store
        self._call_log = call_log
        self._verify_config_version = verify_config_version
        self._prompt_version = prompt_version
        self._lease_owner = lease_owner
        self._now = now

    async def run_next(self, *, tenant_id: str) -> WorkerJob | None:
        job = await self._supervisor.lease_next(
            tenant_id=tenant_id,
            worker_kind=WorkerKind.PROBLEM_GENERATION,
            lease_owner=self._lease_owner,
        )
        if job is None:
            return None
        return await self._run_guarded(job)

    async def _run_guarded(self, job: WorkerJob) -> WorkerJob:
        try:
            return await self._execute(job)
        except Exception as exc:
            error_code = (
                exc.code if isinstance(exc, DomainException) else _ERROR_WORKER_INTERNAL
            )
            try:
                await self._supervisor.fail(
                    tenant_id=job.tenant_id,
                    job_id=job.job_id,
                    lease_owner=self._lease_owner,
                    lease_generation=job.lease_generation,
                    error_code=error_code,
                )
            except Exception:
                logger.exception(
                    "문제 생성 잡 실패 수렴 중 오류 job=%s", job.job_id
                )
            raise

    async def _execute(self, job: WorkerJob) -> WorkerJob:
        request = await self._requests.get(
            job.payload_ref, tenant_id=job.tenant_id
        )
        if request is None:
            raise DomainException(
                "문제 생성 요청을 찾을 수 없다", {"reason": _ERROR_REQUEST_MISSING}
            )
        await self._supervisor.start(
            tenant_id=job.tenant_id,
            job_id=job.job_id,
            lease_owner=self._lease_owner,
            lease_generation=job.lease_generation,
            checkpoint_ref=str(job.job_id),
        )
        context = self._execution_context(job, request)
        await self._begin_execution(context)
        failed = True
        try:
            try:
                result = await self._workflow.run(request, context)  # type: ignore[attr-defined]
            except RedactionBlocked as exc:
                raise RedactionUncertain("문제 생성 마스킹이 불확실하다") from exc
            except LlmError as exc:
                raise domain_error_for(exc) from exc
            failed = False
        finally:
            await self._finalize_execution(context, swallow_errors=failed)

        result_ref = await self._results.put(
            tenant_id=job.tenant_id,
            job_id=job.job_id,
            result=result,
        )
        return await self._supervisor.succeed(
            tenant_id=job.tenant_id,
            job_id=job.job_id,
            lease_owner=self._lease_owner,
            lease_generation=job.lease_generation,
            result_ref=result_ref,
        )

    def _execution_context(
        self, job: WorkerJob, request: ProblemRequest
    ) -> ExecutionContext:
        return ExecutionContext(
            execution_id=job.execution_id,
            tenant_id=job.tenant_id,
            capability=Capability.PROBLEM_GENERATION,
            input_snapshot_hash=request.snapshot_hash,
            versions=problem_versions(
                taxonomy_version=request.taxonomy_version,
                verify_config_version=self._verify_config_version,
                prompt_version=self._prompt_version,
            ),
        )

    async def _begin_execution(self, context: ExecutionContext) -> None:
        await self._runs.begin_run(
            context.to_run_metadata(
                created_at=self._now(),
                model_provider=None,
                model_name=None,
                generation_params=None,
            )
        )

    async def _finalize_execution(
        self, context: ExecutionContext, *, swallow_errors: bool
    ) -> None:
        calls = self._call_log.take(context.execution_id)
        last = calls[-1].record if calls else None
        try:
            await self._runs.finalize_run(
                context.to_run_metadata(
                    created_at=self._now(),
                    model_provider=last.provider if last is not None else None,
                    model_name=last.model if last is not None else None,
                    # 🔴 **경로 축이 아니라 사용 축이다** — "그 실행이 실제로 쓴 값"이다.
                    #    LLM 0콜로 끝나는 pg 실행이 실재한다(R-1 기준 자료 없음 → 생성 호출
                    #    전에 수렴). 안 쓴 값을 적어 두면 재현 키가 *"그 파라미터로 돌렸다"* 는
                    #    없는 사실을 말한다. counsel·detect·classify와 같은 판단(A 8/9).
                    generation_params=(
                        deterministic_params() if last is not None else None
                    ),
                ),
                calls,
            )
        except Exception:
            if not swallow_errors:
                raise
            logger.exception("실패 경로의 PG 실행 원장 최종화 실패 — 원인 예외를 유지한다")

    async def result_of(
        self, result_ref: str, *, tenant_id: str
    ) -> ProblemGenerationOutcome | None:
        return await self._results.get(result_ref, tenant_id=tenant_id)


def problem_versions(
    *,
    taxonomy_version: str | None,
    verify_config_version: str,
    prompt_version: str | None = None,
) -> VersionSet:
    """응답과 AI_RUN이 공유하는 PG 버전 세트.

    🔴 `taxonomy_version`이 `None`일 수 있는 이유는 **실패 응답** 때문이다 — 헤더 누락·
    스키마 위반은 요청 바디를 읽기 전에 나므로 그 시점엔 taxonomy를 모른다(04 §2.2 A판정).
    **기본값은 두지 않았다** — 성공 경로에서 실수로 빠뜨리면 타입 검사가 잡는다.
    """

    resolved_prompt = prompt_version or load_prompt_template(_ITEM_PROMPT_ID).version
    return VersionSet(
        pipeline_version=_PIPELINE_VERSION,
        engine_version=_ENGINE_VERSION,
        schema_version=_SCHEMA_VERSION,
        contract_version=_CONTRACT_VERSION,
        prompt_version=resolved_prompt,
        graph_version=_GRAPH_VERSION,
        taxonomy_version=taxonomy_version,
        verify_config_version=verify_config_version,
    )


@asynccontextmanager
async def open_problem_generation_runner(
    *,
    supervisor: Supervisor,
    providers: ProblemProviders,
    graph_context: GraphContextService,
    diagnosis: DiagnosisCallable,
    stores: ProblemRuntimeStores,
    lease_owner: str,
    run_store: RunStore,
    db_settings: DbSettings | None = None,
    call_log: LlmCallCollector | None = None,
) -> AsyncIterator[ProblemGenerationRunner]:
    """실 provider와 주입 저장소로 PG 러너를 연다. Fake 폴백은 없다."""

    require_tracing_disabled("problem_generation")
    resolved_db = db_settings or get_db_settings()
    verify_config = load_verify_config()
    collector = call_log or default_llm_call_collector()
    gateway = build_problem_gateway(
        verify_config=verify_config,
        recorder=collector,
        providers=providers,
    )
    prompt_version = load_prompt_template(_ITEM_PROMPT_ID).version
    async with _open_saver(resolved_db) as checkpointer:
        workflow = build_problem_workflow(
            gateway=gateway,
            graph_context=graph_context,
            diagnosis=diagnosis,
            candidate_store=stores.candidates,
            item_store=stores.items,
            set_store=stores.sets,
            checkpointer=checkpointer,
            verify_config=verify_config,
        )
        yield ProblemGenerationRunner(
            supervisor=supervisor,
            request_store=stores.requests,
            result_store=stores.results,
            workflow=workflow,
            run_store=run_store,
            call_log=collector,
            verify_config_version=verify_config.version,
            prompt_version=prompt_version,
            lease_owner=lease_owner,
        )


__all__ = [
    "ProblemGenerationRunner",
    "ProblemResultStore",
    "ProblemRuntimeStores",
    "build_tenant_scoped_item_store",
    "build_tenant_scoped_revision_store",
    "default_problem_runtime_stores",
    "open_problem_generation_runner",
    "problem_runtime_stores",
    "problem_versions",
    "reset_problem_memory_runtime",
]
