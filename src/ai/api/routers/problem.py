"""문제 세트 생성 잡의 POST·GET API."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Final

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

from ai.agents.supervisor import Supervisor, system_utc_now
from ai.api.envelope import success_envelope
from ai.api.version_scope import RouterScope
from ai.contracts.agents import TERMINAL_PHASES, JobPhase, WorkerJob
from ai.contracts.diagnosis import DiagnosisResult
from ai.contracts.execution import Capability, ExecutionContext, VersionSet
from ai.contracts.graphrag import GraphContextService
from ai.contracts.llm import CallOutcome, LlmError
from ai.contracts.problem_generation import (
    GeneratedItem,
    ItemFieldChange,
    ItemRevision,
    ItemRevisionRequest,
    ProblemGenerationOutcome,
    ProblemItemStatus,
    ProblemRequest,
    ProblemSetResult,
    RevisionKind,
)
from ai.db.repositories.idempotency import IdempotencyStore
from ai.db.repositories.problem_set_store import PgProblemSetStore
from ai.db.repositories.run_store import RunStore, default_llm_call_collector
from ai.db.session import get_sessionmaker
from ai.db.store_factory import (
    build_agent_job_store,
    build_idempotency_store,
    build_run_store,
)
from ai.llm.determinism import deterministic_params
from ai.llm.prompts.loader import load_prompt_template
from ai.problem_generation.application.drain import ProblemDrainLoop
from ai.problem_generation.application.ports import (
    ProblemItemStore,
    ProblemRevisionStore,
    RevisionConflict,
)
from ai.problem_generation.application.refiner import ProblemItemRefiner
from ai.problem_generation.application.workflow import DiagnosisCallable
from ai.problem_generation.assembly import (
    ProblemGenerationRunner,
    ProblemRuntimeStores,
    build_tenant_scoped_item_store,
    build_tenant_scoped_revision_store,
    open_problem_generation_runner,
    problem_runtime_stores,
    problem_versions,
    reset_problem_memory_runtime,
)
from ai.problem_generation.domain.identity import canonical_json
from ai.problem_generation.enqueue import ProblemGenerationEnqueuer
from ai.problem_generation.infrastructure.config import (
    load_area_specs,
    load_banned_topics,
    load_verify_config,
)
from ai.problem_generation.infrastructure.graph_context import (
    AreaDelegatingGraphContextService,
)
from ai.problem_generation.infrastructure.memory_store import (
    InMemoryProblemRevisionStore,
)
from ai.problem_generation.provider import (
    ProblemProviders,
    build_problem_gateway,
    build_problem_providers,
)
from ai.runtime.errors import (
    DomainException,
    IdempotencyConflict,
    NotFound,
    SnapshotInvalid,
    domain_error_for,
)
from ai.runtime.redaction import redact

logger = logging.getLogger(__name__)

router = APIRouter()


#: 파일이 깨졌으면 실패 응답 때가 아니라 기동 때 죽는 게 맞다 —
#: `require_problem_providers`가 미배선을 기동에서 막는 것과 같은 축이다.
_PROBLEM_FAILURE_VERSIONS: Final = problem_versions(
    taxonomy_version=None,
    verify_config_version=load_verify_config().version,
)


def problem_failure_versions() -> VersionSet:
    """실패 응답용 정적 버전 세트 — 실행 config 확정 전에도 나간다(04 §2.2 A판정 · 99 ㊓).

    🔴 `taxonomy_version`은 요청 **바디**에서 오므로 헤더 누락·JSON 파싱 실패 시점엔 아직
    모른다. 없는 값을 지어내지 않고 `None`으로 둔다 — counsel 실패 응답의 `threshold=None`과
    같은 판단이다("그 실행이 v3를 썼다"는 없는 사실을 만들지 않는다).
    """

    return _PROBLEM_FAILURE_VERSIONS


#: 이 라우터가 응답하는 경로 접두와 그 버전 세트 — `api/app.py`가 **실패 응답**에 쓴다(99 ㊓).
#: 🔴 접두를 여기 두는 이유: **경로를 바꾸는 사람과 접두를 고치는 사람이 같아야 한다.**
#:  `app.py`에 박으면 다른 파일이라 조용히 갈린다.
#: `/v1/problems`·`/{job_id}` 둘을 한 접두가 덮는다.
VERSION_SCOPE: Final = RouterScope("/v1/problems", problem_failure_versions)

_POST_ENDPOINT = "/v1/problems"
_REQUIRED_HEADERS = ("X-Tenant-Id", "X-Request-Id", "Idempotency-Key")
_HEADER_DERIVED_FIELDS = frozenset({"tenant_id", "request_id", "idempotency_key"})
_LEASE_OWNER = "problem-router"


class ProblemRouterSettings(BaseSettings):
    """PG 슈퍼바이저 운영 파라미터."""

    model_config = SettingsConfigDict(
        env_file=".env", env_prefix="PG_", extra="ignore"
    )

    lease_seconds: int = 300
    priority_aging_seconds: int = 600
    poll_retry_after_seconds: int = Field(default=2, ge=1)
    """비종단 조회 응답의 `Retry-After` 값(초).

    🔴 **호출자의 폴링 주기를 코드가 아니라 설정이 정한다.** Kafka-HTTP adapter가 자기
    상수로 돌면 우리가 인라인 실행을 바꿔도(느려지거나 빨라져도) 그쪽 주기는 그대로다 —
    "값이 바뀌면 코드 diff가 생기면 위치가 틀린 것"(03 §1).
    """

    drain_enabled: bool = True
    drain_max_jobs_per_cycle: int = Field(default=20, ge=1)
    drain_cycle_interval_seconds: float = Field(default=0.25, gt=0)
    drain_idle_interval_seconds: float = Field(default=1.0, gt=0)
    drain_failure_backoff_initial_seconds: float = Field(default=1.0, gt=0)
    drain_failure_backoff_max_seconds: float = Field(default=30.0, gt=0)
    drain_shutdown_grace_seconds: float = Field(default=30.0, gt=0)


class ProblemJobView(BaseModel):
    """JobPhase와 도메인 결과를 섞지 않는 조회 투영."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    job_id: str
    status: JobPhase
    result: ProblemGenerationOutcome | None = None


@dataclass(frozen=True, slots=True)
class _CachedView:
    view: ProblemJobView
    versions: VersionSet
    execution_id: uuid.UUID


_idempotency_store: IdempotencyStore = build_idempotency_store()
_run_store: RunStore = build_run_store()
_stores: ProblemRuntimeStores = problem_runtime_stores()
_views: dict[tuple[str, str], _CachedView] = {}
_providers: ProblemProviders | None = None
_graph_context: GraphContextService | None = None
_diagnosis: DiagnosisCallable | None = None
_revision_stores: dict[tuple[str, int], ProblemRevisionStore] = {}


@dataclass(slots=True)
class _DrainRegistration:
    drain: ProblemDrainLoop
    users: int


_drains: dict[asyncio.AbstractEventLoop, _DrainRegistration] = {}


class ProblemProviderNotWired(RuntimeError):
    """실 PG provider가 기동 조립에서 주입되지 않음."""


class ProblemServicesNotWired(RuntimeError):
    """GraphContext·진단 서비스가 아직 조립되지 않음."""


class ProblemRevisionConflict(DomainException):
    """문항 수정 낙관적 잠금·진행 중 충돌."""

    code = "REVISION_CONFLICT"
    http_status = 409


def set_problem_providers(providers: ProblemProviders) -> None:
    """실 provider 또는 테스트가 명시한 provider를 주입한다."""

    global _providers
    _providers = providers


def require_problem_providers() -> ProblemProviders:
    if _providers is None:
        raise ProblemProviderNotWired(
            "problem_generation provider가 배선되지 않았다 — Fake 폴백은 허용하지 않는다"
        )
    return _providers


def bootstrap_problem_providers() -> None:
    """기동 시 env 기반 실 OpenAI 호환 provider를 멱등 조립한다."""

    if _providers is not None:
        return
    set_problem_providers(build_problem_providers())


async def _startup() -> None:
    bootstrap_problem_providers()
    bootstrap_problem_services()
    require_problem_providers()
    require_problem_services()
    await _start_problem_drain()


router.add_event_handler("startup", _startup)


async def _shutdown() -> None:
    loop = asyncio.get_running_loop()
    registration = _drains.get(loop)
    if registration is None:
        return
    registration.users -= 1
    if registration.users > 0:
        return
    del _drains[loop]
    await registration.drain.stop()


router.add_event_handler("shutdown", _shutdown)


def set_problem_services(
    *, graph_context: GraphContextService, diagnosis: DiagnosisCallable
) -> None:
    """근거층·진단 구현 주입 seam. 근거층 구현 전에는 명시 주입만 허용한다."""

    global _graph_context, _diagnosis
    _graph_context = graph_context
    _diagnosis = diagnosis


async def _diagnosis_not_wired(_request: ProblemRequest) -> DiagnosisResult:
    raise ProblemServicesNotWired(
        "weakness_auto 진단 서비스가 배선되지 않았다 — 명시 주입이 필요하다"
    )


def bootstrap_problem_services() -> None:
    """기동 시 영역별 GraphContext 위임 경계를 멱등 조립한다."""

    if _graph_context is not None:
        return
    set_problem_services(
        graph_context=AreaDelegatingGraphContextService(),
        diagnosis=_diagnosis or _diagnosis_not_wired,
    )


def require_problem_services() -> tuple[GraphContextService, DiagnosisCallable]:
    """배선된 실 근거층과 주입된 진단 seam을 반환한다."""

    return _require_services()


def set_problem_stores(stores: ProblemRuntimeStores) -> None:
    """테스트와 후속 PG 어댑터가 조립부 저장소 묶음을 교체하는 seam."""

    global _stores
    _stores = stores


def set_problem_run_store(store: RunStore) -> None:
    """AI_RUN·LLM_CALL 적재 관측용 저장소 주입 seam."""

    global _run_store
    _run_store = store


def set_problem_idempotency_store(store: IdempotencyStore) -> None:
    """멱등 재반환 테스트·PG 어댑터용 저장소 주입 seam."""

    global _idempotency_store
    _idempotency_store = store


def reset_problem_router() -> None:
    """PG 테스트가 명시 호출해 라우터 소유 인메모리 상태를 격리한다."""

    global _idempotency_store, _run_store, _stores
    global _providers, _graph_context, _diagnosis
    reset_problem_memory_runtime()
    default_llm_call_collector().reset()
    _idempotency_store = build_idempotency_store()
    _run_store = build_run_store()
    _stores = problem_runtime_stores()
    _views.clear()
    _revision_stores.clear()
    _providers = None
    _graph_context = None
    _diagnosis = None


async def _start_problem_drain() -> None:
    settings = ProblemRouterSettings()
    if not settings.drain_enabled:
        return
    loop = asyncio.get_running_loop()
    registration = _drains.get(loop)
    if registration is not None and registration.drain.is_running:
        registration.users += 1
        return
    drain = ProblemDrainLoop(
        run_next=_run_next_for_tenant,
        max_jobs_per_cycle=settings.drain_max_jobs_per_cycle,
        cycle_interval_seconds=settings.drain_cycle_interval_seconds,
        idle_interval_seconds=settings.drain_idle_interval_seconds,
        failure_backoff_initial_seconds=(
            settings.drain_failure_backoff_initial_seconds
        ),
        failure_backoff_max_seconds=settings.drain_failure_backoff_max_seconds,
        shutdown_grace_seconds=settings.drain_shutdown_grace_seconds,
    )
    await drain.start()
    _drains[loop] = _DrainRegistration(drain=drain, users=1)


def _notify_problem_drain(tenant_id: str) -> None:
    registration = _drains.get(asyncio.get_running_loop())
    if registration is not None:
        registration.drain.notify_tenant(tenant_id)


def problem_drain_running() -> bool:
    """수명주기 테스트가 배경 태스크 잔존 여부를 관측하는 읽기 전용 seam."""

    return any(registration.drain.is_running for registration in _drains.values())


def _require_services() -> tuple[GraphContextService, DiagnosisCallable]:
    if _graph_context is None or _diagnosis is None:
        raise ProblemServicesNotWired(
            "problem_generation GraphContext·진단 서비스가 배선되지 않았다"
        )
    return _graph_context, _diagnosis


def _problem_set_store_for(tenant_id: str) -> PgProblemSetStore | None:
    """문항 저장소의 단일 backend 판정을 공유해 PG 세트 저장소를 조립한다."""

    if build_tenant_scoped_item_store(tenant_id=tenant_id) is None:
        return None
    return PgProblemSetStore(sessionmaker=get_sessionmaker(), tenant_id=tenant_id)


def _build_supervisor() -> Supervisor:
    settings = ProblemRouterSettings()
    return Supervisor(
        store=build_agent_job_store(),
        lease_duration=timedelta(seconds=settings.lease_seconds),
        priority_aging_interval=timedelta(seconds=settings.priority_aging_seconds),
        clock=system_utc_now,
    )


def _canonical_hash(body: dict[str, Any]) -> str:
    canonical = json.dumps(
        body, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    )
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _format_validation_error(exc: ValidationError) -> list[dict[str, str]]:
    return [
        {"field": ".".join(str(part) for part in error["loc"]), "type": error["type"]}
        for error in exc.errors()
    ]


def _problem_request(
    raw_body: dict[str, Any], *, tenant_id: str, request_id: str, idempotency_key: str
) -> ProblemRequest:
    duplicated = sorted(_HEADER_DERIVED_FIELDS.intersection(raw_body))
    if duplicated:
        raise SnapshotInvalid(
            "헤더 파생 필드는 요청 바디에 둘 수 없다", {"fields": duplicated}
        )
    try:
        return ProblemRequest.model_validate(
            {
                **raw_body,
                "tenant_id": tenant_id,
                "request_id": request_id,
                "idempotency_key": idempotency_key,
            }
        )
    except ValidationError as exc:
        raise SnapshotInvalid(
            "요청 바디 스키마 위반", _format_validation_error(exc)
        ) from exc


async def _view_for(
    job: WorkerJob,
    *,
    tenant_id: str,
    runner: ProblemGenerationRunner | None = None,
) -> ProblemJobView:
    result: ProblemGenerationOutcome | None = None
    if job.phase is JobPhase.SUCCEEDED:
        if job.result_ref is None:
            raise DomainException("성공한 문제 생성 잡에 result_ref가 없다")
        result = (
            await runner.result_of(job.result_ref, tenant_id=tenant_id)
            if runner is not None
            else await _stores.results.get(job.result_ref, tenant_id=tenant_id)
        )
        if result is None:
            raise DomainException("성공한 문제 생성 잡의 결과를 찾을 수 없다")
    return ProblemJobView(
        job_id=str(job.job_id),
        status=job.phase,
        result=result,
    )


async def _generate(request: ProblemRequest) -> tuple[ProblemJobView, WorkerJob]:
    supervisor = _build_supervisor()
    job = await ProblemGenerationEnqueuer(
        supervisor=supervisor,
        request_store=_stores.requests,
    ).enqueue(request)
    try:
        ran = await _run_next_for_tenant(request.tenant_id)
    finally:
        _notify_problem_drain(request.tenant_id)
    if ran is not None and ran.job_id != job.job_id:
        logger.info(
            "PG 러너가 다른 잡을 실행했다 mine=%s ran=%s — 결과는 자기 잡에서 읽는다",
            job.job_id,
            ran.job_id,
        )
    mine = await supervisor.get(
        tenant_id=request.tenant_id, job_id=job.job_id
    ) or job
    return await _view_for(mine, tenant_id=request.tenant_id), job


async def _run_next_for_tenant(tenant_id: str) -> WorkerJob | None:
    supervisor = _build_supervisor()
    graph_context, diagnosis = _require_services()
    providers = require_problem_providers()
    # 🔴 슬롯 최종본 저장소만 **요청 테넌트로 스코프**한다(09 §2-20.3) — Protocol에
    #  `tenant_id`가 없어 인스턴스가 그 축을 든다. 러너는 `lease_next(tenant_id, …)`로만
    #  잡을 집으므로 다른 테넌트의 잡을 이 저장소로 실행할 경로가 없다.
    #  ⚠ `store_backend=memory`(기본)면 `None`이라 **`_stores`가 그대로 간다** — 주입 seam
    #   무변경. 교체할 때도 나머지 셋은 `_stores`에서 그대로 옮긴다(덮어쓰지 않는다).
    tenant_items = build_tenant_scoped_item_store(tenant_id=tenant_id)
    tenant_sets = (
        None
        if tenant_items is None
        else PgProblemSetStore(sessionmaker=get_sessionmaker(), tenant_id=tenant_id)
    )
    stores = (
        _stores
        if tenant_items is None and tenant_sets is None
        else problem_runtime_stores(
            request_store=_stores.requests,
            result_store=_stores.results,
            candidate_store=_stores.candidates,
            item_store=tenant_items or _stores.items,
            set_store=tenant_sets or _stores.sets,
        )
    )
    async with open_problem_generation_runner(
        supervisor=supervisor,
        providers=providers,
        graph_context=graph_context,
        diagnosis=diagnosis,
        stores=stores,
        lease_owner=_LEASE_OWNER,
        run_store=_run_store,
    ) as runner:
        return await runner.run_next(tenant_id=tenant_id)


@router.post("/v1/problems", status_code=202)
async def post_problem(request: Request, response: Response) -> dict[str, Any]:
    """문제 세트 생성 잡을 넣고 최초·재반환 모두 202를 돌려준다."""

    missing = [name for name in _REQUIRED_HEADERS if not request.headers.get(name)]
    if missing:
        raise SnapshotInvalid("필수 헤더 누락", {"missing_headers": missing})
    tenant_id = request.headers["X-Tenant-Id"]
    request_id = request.headers["X-Request-Id"]
    idempotency_key = request.headers["Idempotency-Key"]
    try:
        raw_body = await request.json()
    except ValueError as exc:
        raise SnapshotInvalid("요청 바디가 유효한 JSON이 아님", str(exc)) from exc
    if not isinstance(raw_body, dict):
        raise SnapshotInvalid("요청 바디는 JSON 객체여야 한다")

    body_hash = _canonical_hash(raw_body)
    hit = await _idempotency_store.get(
        tenant_id=tenant_id,
        endpoint=_POST_ENDPOINT,
        idempotency_key=idempotency_key,
    )
    if hit is not None:
        if hit.snapshot_hash == body_hash:
            response.status_code = 202
            return hit.response_body
        raise IdempotencyConflict(
            "같은 Idempotency-Key에 다른 바디",
            {"idempotency_key": idempotency_key},
        )

    problem_request = _problem_request(
        raw_body,
        tenant_id=tenant_id,
        request_id=request_id,
        idempotency_key=idempotency_key,
    )
    view, job = await _generate(problem_request)
    versions = problem_versions(
        taxonomy_version=problem_request.taxonomy_version,
        verify_config_version=load_verify_config().version,
    )
    _views[(tenant_id, view.job_id)] = _CachedView(
        view=view,
        versions=versions,
        execution_id=job.execution_id,
    )
    envelope = success_envelope(
        # 🔴 `status`를 같이 싣는다 — counsel 202와 대칭이고(04 §3.9) **BE가 통지를 기다릴지
        #    바로 GET할지를 이 값 하나로 정한다**(런북 §2 규칙). 종전에는 `job_id`만 실려서
        #    ⓐ 인라인 실행으로 이미 종단인 잡을 두고 BE가 Kafka를 기다리거나
        #    ⓑ 아직 `queued`인 잡을 종단으로 오해하거나 — 어느 쪽인지 응답만으로는 알 수 없었다.
        #    ⚠ `run_next()`가 **자기 잡을 처리한다는 보장이 없다**(우선순위·aging 순) —
        #    202가 `queued`로 나가는 경로가 실재한다.
        data={"job_id": view.job_id, "status": view.status.value},
        execution_id=str(job.execution_id),
        versions=versions,
    )
    await _idempotency_store.put(
        tenant_id=tenant_id,
        endpoint=_POST_ENDPOINT,
        idempotency_key=idempotency_key,
        snapshot_hash=body_hash,
        response_body=envelope,
    )
    return envelope


@router.get("/v1/problems/{job_id}")
async def get_problem(
    job_id: str, request: Request, response: Response
) -> dict[str, Any]:
    """테넌트 범위에서 잡 현재 phase와 확정 결과를 회수한다."""

    tenant_id = request.headers.get("X-Tenant-Id")
    if not tenant_id:
        raise SnapshotInvalid("필수 헤더 누락", {"missing_headers": ["X-Tenant-Id"]})
    try:
        parsed_job_id = uuid.UUID(job_id)
    except ValueError as exc:
        raise NotFound("job_id 부재", {"job_id": job_id}) from exc
    job = await _build_supervisor().get(
        tenant_id=tenant_id, job_id=parsed_job_id
    )
    if job is None:
        raise NotFound("job_id 부재", {"job_id": job_id})
    cached = _views.get((tenant_id, job_id))
    if cached is None:
        set_store = _problem_set_store_for(tenant_id)
        restored = (
            await set_store.get_by_execution_id(job.execution_id)
            if set_store is not None and job.phase is JobPhase.SUCCEEDED
            else None
        )
        if restored is None:
            raise NotFound("job_id 부재", {"job_id": job_id})
        cached = _CachedView(
            view=ProblemJobView(job_id=job_id, status=job.phase, result=restored.result),
            versions=restored.versions,
            execution_id=restored.execution_id,
        )
    view = (
        cached.view
        if cached.view.status is job.phase and cached.view.result is not None
        else await _view_for(job, tenant_id=tenant_id)
    )
    _views[(tenant_id, job_id)] = _CachedView(
        view=view,
        versions=cached.versions,
        execution_id=cached.execution_id,
    )
    if job.phase not in TERMINAL_PHASES:
        # 🔴 **폴링 종료 조건은 `data.status`가 종단인지 하나다.** 이 헤더는 *"언제 다시
        #    오라"* 만 말한다 — 종단 응답에는 붙지 않으므로 헤더 유무로도 갈린다.
        response.headers["Retry-After"] = str(
            ProblemRouterSettings().poll_retry_after_seconds
        )
    return success_envelope(
        data=view.model_dump(mode="json"),
        execution_id=str(cached.execution_id),
        versions=cached.versions,
    )


def _tenant_id(request: Request) -> str:
    tenant_id = request.headers.get("X-Tenant-Id")
    if not tenant_id:
        raise SnapshotInvalid("필수 헤더 누락", {"missing_headers": ["X-Tenant-Id"]})
    return tenant_id


async def _set_result(
    *, tenant_id: str, set_id: uuid.UUID
) -> tuple[ProblemSetResult, VersionSet, uuid.UUID]:
    for (cached_tenant, _job_id), cached in _views.items():
        result = cached.view.result
        if (
            cached_tenant == tenant_id
            and isinstance(result, ProblemSetResult)
            and result.set_id == set_id
        ):
            return result, cached.versions, cached.execution_id
    set_store = _problem_set_store_for(tenant_id)
    restored = await set_store.get_by_set_id(set_id) if set_store is not None else None
    if restored is not None:
        return restored.result, restored.versions, restored.execution_id
    raise NotFound("set_id 부재", {"set_id": str(set_id)})


def _set_job_id(*, tenant_id: str, set_id: uuid.UUID) -> uuid.UUID:
    for (cached_tenant, job_id), cached in _views.items():
        result = cached.view.result
        if (
            cached_tenant == tenant_id
            and isinstance(result, ProblemSetResult)
            and result.set_id == set_id
        ):
            return uuid.UUID(job_id)
    raise NotFound("set_id 부재", {"set_id": str(set_id)})


def _set_id(raw_set_id: str) -> uuid.UUID:
    try:
        return uuid.UUID(raw_set_id)
    except ValueError as exc:
        raise NotFound("set_id 부재", {"set_id": raw_set_id}) from exc


def _item_store_for(tenant_id: str) -> ProblemItemStore:
    return build_tenant_scoped_item_store(tenant_id=tenant_id) or _stores.items


def _revision_store_for(tenant_id: str) -> ProblemRevisionStore:
    pg_store = build_tenant_scoped_revision_store(tenant_id=tenant_id)
    if pg_store is not None:
        return pg_store
    item_store = _item_store_for(tenant_id)
    key = (tenant_id, id(item_store))
    store = _revision_stores.get(key)
    if store is None:
        store = InMemoryProblemRevisionStore(item_store)
        _revision_stores[key] = store
    return store


class _AiRefineBody(BaseModel):
    """헤더 파생 필드를 제외한 Step3 AI 수정 HTTP body."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    base_revision_no: int = Field(ge=0)
    revision_kind: RevisionKind
    instruction: str = Field(min_length=1)


def _item_diff(
    before: GeneratedItem, after: GeneratedItem
) -> tuple[ItemFieldChange, ...]:
    before_body = before.model_dump(mode="json")
    after_body = after.model_dump(mode="json")
    return tuple(
        ItemFieldChange(
            path=f"$.{field}",
            before_json=canonical_json(before_body[field]),
            after_json=canonical_json(after_body[field]),
        )
        for field in sorted(before_body)
        if before_body[field] != after_body[field]
    )


async def _problem_request_for_set(
    *, tenant_id: str, set_id: uuid.UUID
) -> ProblemRequest:
    job_id = _set_job_id(tenant_id=tenant_id, set_id=set_id)
    job = await _build_supervisor().get(tenant_id=tenant_id, job_id=job_id)
    if job is None:
        raise NotFound("set_id 부재", {"set_id": str(set_id)})
    request = await _stores.requests.get(job.payload_ref, tenant_id=tenant_id)
    if request is None:
        raise NotFound("set_id 부재", {"set_id": str(set_id)})
    return request


async def _record_revision_run(
    context: ExecutionContext,
    *,
    swallow_errors: bool = False,
) -> uuid.UUID | None:
    calls = default_llm_call_collector().take(context.execution_id)
    generated_call_id = next(
        (
            call.id
            for call in reversed(calls)
            if call.record.prompt_id == "pg.refine.v1"
            and call.record.outcome is CallOutcome.OK
        ),
        None,
    )
    last = calls[-1].record if calls else None
    try:
        await _run_store.record_run(
            context.to_run_metadata(
                created_at=system_utc_now(),
                model_provider=last.provider if last is not None else None,
                model_name=last.model if last is not None else None,
                generation_params=(
                    deterministic_params() if last is not None else None
                ),
            ),
            calls,
        )
    except Exception:
        if not swallow_errors:
            raise
        logger.exception("문항 수정 장애 턴의 원장 적재 실패 — 원인 예외를 유지한다")
    return generated_call_id


async def _revision_no(
    *, tenant_id: str, set_id: uuid.UUID, slot_index: int
) -> int:
    return await _item_store_for(tenant_id).current_revision_no(set_id, slot_index)


@router.get("/v1/problems/{set_id}/items")
async def get_problem_items(set_id: str, request: Request) -> dict[str, Any]:
    """Step3 검토 목록을 상태 카운터와 함께 반환한다."""

    tenant_id = _tenant_id(request)
    parsed_set_id = _set_id(set_id)
    result, versions, execution_id = await _set_result(
        tenant_id=tenant_id,
        set_id=parsed_set_id,
    )
    counts = {status.value: 0 for status in ProblemItemStatus}
    items: list[dict[str, Any]] = []
    for slot_index, item_result in enumerate(result.items):
        counts[item_result.status.value] += 1
        revision_no = (
            await _revision_no(
                tenant_id=tenant_id,
                set_id=parsed_set_id,
                slot_index=slot_index,
            )
            if item_result.item_id is not None
            else 0
        )
        items.append(
            {
                "slot_index": slot_index,
                "item_id": (
                    str(item_result.item_id) if item_result.item_id is not None else None
                ),
                "status": item_result.status.value,
                "current_revision_no": revision_no,
                "review_reason": (
                    item_result.review_reason.value
                    if item_result.review_reason is not None
                    else None
                ),
                "failure_reason": (
                    item_result.failure_reason.value
                    if item_result.failure_reason is not None
                    else None
                ),
            }
        )
    return success_envelope(
        data={"set_id": str(parsed_set_id), "status_counts": counts, "items": items},
        execution_id=str(execution_id),
        versions=versions,
    )


@router.get("/v1/problems/{set_id}/items/{slot_index}")
async def get_problem_item(
    set_id: str, slot_index: int, request: Request
) -> dict[str, Any]:
    """Step3 문항 본문·교차 풀이·검증 상태를 한 번에 반환한다."""

    tenant_id = _tenant_id(request)
    parsed_set_id = _set_id(set_id)
    result, versions, execution_id = await _set_result(
        tenant_id=tenant_id,
        set_id=parsed_set_id,
    )
    if slot_index < 0 or slot_index >= len(result.items):
        raise NotFound(
            "문항 슬롯 부재", {"set_id": str(parsed_set_id), "slot_index": slot_index}
        )
    item_result = result.items[slot_index]
    stored = None
    candidate = None
    current_item = None
    latest_revision = None
    revision_no = 0
    if item_result.item_id is not None:
        store = _item_store_for(tenant_id)
        try:
            stored = await store.get(parsed_set_id, slot_index)
            revision_no = await store.current_revision_no(parsed_set_id, slot_index)
            current_item = stored.item
            if revision_no > 0:
                history = await _revision_store_for(tenant_id).list_revisions(
                    parsed_set_id, slot_index
                )
                latest_revision = history[-1] if history else None
                current_item = next(
                    (
                        revision.result_snapshot
                        for revision in reversed(history)
                        if revision.verifications_passed
                        and revision.result_snapshot is not None
                    ),
                    current_item,
                )
            if revision_no == 0 and stored.candidate_ref is not None:
                candidate = await _stores.candidates.get(stored.candidate_ref)
        except LookupError as exc:
            raise NotFound(
                "문항 슬롯 부재",
                {"set_id": str(parsed_set_id), "slot_index": slot_index},
            ) from exc
    verified = item_result.status in {
        ProblemItemStatus.VERIFIED,
        ProblemItemStatus.NEEDS_REVIEW,
    }
    return success_envelope(
        data={
            "set_id": str(parsed_set_id),
            "slot_index": slot_index,
            "item_id": (
                str(item_result.item_id) if item_result.item_id is not None else None
            ),
            "status": item_result.status.value,
            "current_revision_no": revision_no,
            "available_actions": (
                ["refine"]
                if current_item is not None
                and current_item.area_tag.value == "language"
                else []
            ),
            "item": (
                current_item.model_dump(mode="json")
                if current_item is not None
                else None
            ),
            "cross_solve": (
                candidate.solve_result.model_dump(mode="json")
                if candidate is not None
                else None
            ),
            "verification": {
                "rule_validation": (
                    "passed"
                    if latest_revision is not None
                    and latest_revision.verifications_passed
                    else "blocked"
                    if latest_revision is not None
                    else "passed"
                    if verified
                    else "unavailable"
                ),
                "blind_cross_solve": (
                    "passed"
                    if latest_revision is not None
                    and latest_revision.verifications_passed
                    else "blocked"
                    if latest_revision is not None
                    else "passed"
                    if candidate is not None
                    else "unavailable"
                ),
                "release_decision": (
                    "passed"
                    if latest_revision is not None
                    and latest_revision.verifications_passed
                    else "blocked"
                    if latest_revision is not None
                    else item_result.status.value
                ),
            },
            "revisions": [
                revision.model_dump(mode="json")
                for revision in (
                    await _revision_store_for(tenant_id).list_revisions(
                        parsed_set_id, slot_index
                    )
                    if revision_no > 0
                    else ()
                )
            ],
            "review_reason": (
                item_result.review_reason.value
                if item_result.review_reason is not None
                else None
            ),
            "failure_reason": (
                item_result.failure_reason.value
                if item_result.failure_reason is not None
                else None
            ),
        },
        execution_id=str(execution_id),
        versions=versions,
    )


@router.post("/v1/problems/{set_id}/items/{slot_index}/revisions")
async def post_problem_item_revision(
    set_id: str, slot_index: int, request: Request
) -> dict[str, Any]:
    """language 문항의 AI 수정 1턴을 전체 재검증하고 리비전으로 남긴다."""

    missing = [name for name in _REQUIRED_HEADERS if not request.headers.get(name)]
    if missing:
        raise SnapshotInvalid("필수 헤더 누락", {"missing_headers": missing})
    tenant_id = request.headers["X-Tenant-Id"]
    request_id = request.headers["X-Request-Id"]
    idempotency_key = request.headers["Idempotency-Key"]
    parsed_set_id = _set_id(set_id)
    await _set_result(tenant_id=tenant_id, set_id=parsed_set_id)
    try:
        raw_body = await request.json()
    except ValueError as exc:
        raise SnapshotInvalid("요청 바디가 유효한 JSON이 아님") from exc
    if not isinstance(raw_body, dict):
        raise SnapshotInvalid("요청 바디는 JSON 객체여야 한다")
    body_hash = _canonical_hash(raw_body)
    endpoint = f"/v1/problems/{parsed_set_id}/items/{slot_index}/revisions"
    hit = await _idempotency_store.get(
        tenant_id=tenant_id,
        endpoint=endpoint,
        idempotency_key=idempotency_key,
    )
    if hit is not None:
        if hit.snapshot_hash == body_hash:
            return hit.response_body
        raise IdempotencyConflict(
            "같은 Idempotency-Key에 다른 바디",
            {"idempotency_key": idempotency_key},
        )
    try:
        body = _AiRefineBody.model_validate(raw_body)
    except ValidationError as exc:
        raise SnapshotInvalid(
            "요청 바디 스키마 위반", _format_validation_error(exc)
        ) from exc
    if body.revision_kind is not RevisionKind.AI_REFINE:
        raise SnapshotInvalid("MVP 수정 API는 ai_refine만 지원한다")

    item_store = _item_store_for(tenant_id)
    try:
        stored = await item_store.get(parsed_set_id, slot_index)
    except LookupError as exc:
        raise NotFound(
            "문항 슬롯 부재",
            {"set_id": str(parsed_set_id), "slot_index": slot_index},
        ) from exc
    if stored.item is None:
        raise NotFound(
            "문항 슬롯 부재",
            {"set_id": str(parsed_set_id), "slot_index": slot_index},
        )
    if stored.item.area_tag.value != "language":
        raise SnapshotInvalid("MVP ai_refine은 language 문항만 지원한다")
    command = ItemRevisionRequest(
        request_id=request_id,
        idempotency_key=idempotency_key,
        item_id=stored.item_id,
        base_revision_no=body.base_revision_no,
        revision_kind=body.revision_kind,
        instruction=body.instruction,
    )
    problem_request = await _problem_request_for_set(
        tenant_id=tenant_id, set_id=parsed_set_id
    )
    versions = problem_versions(
        taxonomy_version=problem_request.taxonomy_version,
        verify_config_version=load_verify_config().version,
        prompt_version=load_prompt_template("pg.refine.v1").version,
    )
    execution_context = ExecutionContext(
        execution_id=uuid.uuid4(),
        tenant_id=tenant_id,
        capability=Capability.PROBLEM_GENERATION,
        input_snapshot_hash=problem_request.snapshot_hash,
        versions=versions,
    )
    revision_store = _revision_store_for(tenant_id)
    try:
        async with revision_store.reserve(
            set_id=parsed_set_id,
            slot_index=slot_index,
            base_revision_no=command.base_revision_no,
        ) as session:
            verify_config = load_verify_config()
            refiner = ProblemItemRefiner(
                gateway=build_problem_gateway(
                    verify_config=verify_config,
                    recorder=default_llm_call_collector(),
                    providers=require_problem_providers(),
                ),
                graph_context=require_problem_services()[0],
                verify_config=verify_config,
                banned_topics=load_banned_topics(),
                area_specs=load_area_specs(),
            )
            try:
                outcome = await refiner.refine(
                    original=session.current_item,
                    request=problem_request,
                    instruction=body.instruction,
                    execution_context=execution_context,
                )
            except LlmError as exc:
                await _record_revision_run(execution_context, swallow_errors=True)
                raise domain_error_for(exc) from exc
            llm_call_id = await _record_revision_run(execution_context)
            revision = ItemRevision(
                revision_no=session.current_revision_no + 1,
                revision_kind=RevisionKind.AI_REFINE,
                instruction=redact(body.instruction).masked_text,
                result_snapshot=outcome.item if outcome.applied else None,
                diff=(
                    _item_diff(session.current_item, outcome.item)
                    if outcome.item is not None
                    else ()
                ),
                verifications_passed=outcome.applied,
                blocked_reason=outcome.blocked_reason,
                llm_call_id=llm_call_id,
            )
            await session.append(revision)
    except RevisionConflict as exc:
        raise ProblemRevisionConflict(
            "문항 리비전 충돌",
            {
                "reason": exc.reason,
                "base_revision_no": body.base_revision_no,
                "current_revision_no": exc.current_revision_no,
            },
        ) from exc

    envelope = success_envelope(
        data={
            "set_id": str(parsed_set_id),
            "slot_index": slot_index,
            "revision": revision.model_dump(mode="json"),
            "current_revision_no": revision.revision_no,
            "verification": {
                "rule_validation": "passed" if outcome.applied else "blocked",
                "blind_cross_solve": "passed" if outcome.applied else "blocked",
                "release_decision": (
                    outcome.release_status.value
                    if outcome.release_status is not None
                    else "blocked"
                ),
                "review_reason": (
                    outcome.review_reason.value
                    if outcome.review_reason is not None
                    else None
                ),
                "difficulty_est": outcome.difficulty_est,
                "difficulty_band": (
                    outcome.difficulty_band.value
                    if outcome.difficulty_band is not None
                    else None
                ),
            },
        },
        execution_id=str(execution_context.execution_id),
        versions=versions,
    )
    await _idempotency_store.put(
        tenant_id=tenant_id,
        endpoint=endpoint,
        idempotency_key=idempotency_key,
        snapshot_hash=body_hash,
        response_body=envelope,
    )
    return envelope


__all__ = [
    "VERSION_SCOPE",
    "ProblemJobView",
    "ProblemProviderNotWired",
    "ProblemServicesNotWired",
    "bootstrap_problem_services",
    "bootstrap_problem_providers",
    "get_problem",
    "get_problem_item",
    "get_problem_items",
    "post_problem",
    "post_problem_item_revision",
    "problem_drain_running",
    "problem_failure_versions",
    "require_problem_providers",
    "require_problem_services",
    "reset_problem_router",
    "router",
    "set_problem_idempotency_store",
    "set_problem_providers",
    "set_problem_run_store",
    "set_problem_services",
    "set_problem_stores",
]
