"""Import 라우터 — /v1/imports 3엔드포인트 (202 비동기, 10_import_spec §1·§2).

소유: 박진희 (import_mapping 라우터). 계약(contracts/imports)과 결정론 코어(import_mapping)를
HTTP로 노출한다. envelope·에러코드는 공통 계약 정본(§2.2·error_codes §1)과 정합.

**범위(2026-07-30):** AI는 매핑 제안까지다 — 전체 행 변환·산출물은 백엔드 소유(§4).
프로파일링·매핑 추론(Fake)·상태 저장까지 동기로 수행하고 202를 낸다. 실 비동기 워커·Kafka
완료 통지는 후속. 스토리지 fetch·조사 에이전트 실행은 명시적 보류(스텁 주입).

멱등: 감지 라우터 선례 재사용 — (tenant_id, endpoint, idempotency_key) 스코프,
바디 동일성은 canonical 해시. 캐시 fail-open.
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from typing import Any, Final

from fastapi import APIRouter, Request
from pydantic import ValidationError

from ai.api.envelope import success_envelope
from ai.api.version_scope import RouterScope
from ai.contracts.execution import VersionSet
from ai.contracts.imports import (
    ConfirmRequest,
    ImportCreateRequest,
    ImportJobView,
    ImportStatus,
)
from ai.db.repositories.idempotency import IdempotencyStore
from ai.db.store_factory import build_idempotency_store
from ai.import_mapping.inference import (
    STANDARD_FIELDS,
    confirmed_cache_entry,
    infer_mapping,
    mapped_targets,
    unmapped_target_fields,
)
from ai.import_mapping.job_store import (
    ImportJob,
    ImportJobStore,
    InMemoryImportJobStore,
    SourceLoader,
    StubSourceLoader,
)
from ai.import_mapping.probe.enqueue import ProbeEnqueuer
from ai.import_mapping.profiling import ProfilingError, Redactor, profile_source
from ai.import_mapping.provider import FakeMappingProvider, MappingProvider
from ai.import_mapping.settings import get_import_settings
from ai.import_mapping.signature import (
    InMemorySpecCache,
    SpecCache,
    columns_from_overrides,
    form_signature,
)
from ai.import_mapping.state import assert_transition
from ai.runtime.errors import IdempotencyConflict, NotFound, SnapshotInvalid
from ai.runtime.redaction import redact

logger = logging.getLogger(__name__)

router = APIRouter()

_PIPELINE_VERSION = "0.1.0"
_ENGINE_VERSION = "import-mapping-0.1"
_SCHEMA_VERSION = "0.1"
_CONTRACT_VERSION = "0.1"
_PROMPT_VERSION = "mapping-0.1"
_TAXONOMY_VERSION = "taxonomy-0.1"

_POST_ENDPOINT = "POST /v1/imports"
_CONFIRM_ENDPOINT = "POST /v1/imports/confirm"
_WRITE_HEADERS = ("X-Tenant-Id", "X-Request-Id", "Idempotency-Key")
_READ_HEADERS = ("X-Tenant-Id", "X-Request-Id")

#: 주입 가능한 협력자 — 테스트·실배선에서 특정 인스턴스를 꽂는다(감지 라우터 선례).
_idempotency_store: IdempotencyStore = build_idempotency_store()
_job_store: ImportJobStore = InMemoryImportJobStore()
_spec_cache: SpecCache = InMemorySpecCache()
_mapping_provider: MappingProvider = FakeMappingProvider()
_source_loader: SourceLoader = StubSourceLoader()
#: 프로덕션 조립부 기본 = 실 redaction 엔진(runtime/redaction.redact) 주입 — 샘플 보관 활성.
#: profiling 모듈은 redaction을 import하지 않는다(벤더/보안 층은 조립부에서 주입, §3.1·§5.2).
_redactor: Redactor | None = redact
#: probing 기동 시 WorkerJob enqueue(§6). 미배선(None)이면 preview_ready 유지(기존 동작 무변).
_probe_enqueuer: ProbeEnqueuer | None = None


def set_import_stores(
    *,
    job_store: ImportJobStore | None = None,
    idempotency_store: IdempotencyStore | None = None,
    spec_cache: SpecCache | None = None,
    provider: MappingProvider | None = None,
    source_loader: SourceLoader | None = None,
    redactor: Redactor | None = None,
    probe_enqueuer: ProbeEnqueuer | None = None,
) -> None:
    """협력자 주입(합성 루트·테스트). 미지정(None) 인자는 무시 — None 강제는 reset이 한다."""
    global _idempotency_store, _job_store, _spec_cache, _mapping_provider, _source_loader
    global _redactor, _probe_enqueuer
    if job_store is not None:
        _job_store = job_store
    if idempotency_store is not None:
        _idempotency_store = idempotency_store
    if spec_cache is not None:
        _spec_cache = spec_cache
    if provider is not None:
        _mapping_provider = provider
    if source_loader is not None:
        _source_loader = source_loader
    if redactor is not None:
        _redactor = redactor
    if probe_enqueuer is not None:
        _probe_enqueuer = probe_enqueuer


def reset_import_stores() -> None:
    """테스트 격리 — 기본 구현 재빌드. 테스트 기본은 redactor·probe_enqueuer **미배선**(무변)."""
    global _redactor, _probe_enqueuer
    set_import_stores(
        job_store=InMemoryImportJobStore(),
        idempotency_store=build_idempotency_store(),
        spec_cache=InMemorySpecCache(),
        provider=FakeMappingProvider(),
        source_loader=StubSourceLoader(),
    )
    _redactor = None
    _probe_enqueuer = None


def import_versions() -> VersionSet:
    """Import 엔드포인트 버전 세트 — engine/threshold/graph 등 미해당 키는 None(§2.2)."""
    return VersionSet(
        pipeline_version=_PIPELINE_VERSION,
        engine_version=_ENGINE_VERSION,
        schema_version=_SCHEMA_VERSION,
        contract_version=_CONTRACT_VERSION,
        prompt_version=_PROMPT_VERSION,
        taxonomy_version=_TAXONOMY_VERSION,
    )


def _require_headers(request: Request, names: tuple[str, ...]) -> None:
    missing = [name for name in names if not request.headers.get(name)]
    if missing:
        raise SnapshotInvalid("필수 헤더 누락", {"missing_headers": missing})


def _body_hash(payload: dict[str, Any]) -> str:
    """바디 canonical 해시 — 멱등 동일성 판정(정렬 키·NFC). 내부 백엔드 전용 신뢰."""
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _view_body(job: ImportJob) -> dict[str, Any]:
    view = ImportJobView(
        job_id=job.job_id,
        status=job.status,
        status_reason=job.status_reason,
        mapping_preview=job.preview,
    )
    return success_envelope(
        view.model_dump(mode="json"), execution_id=job.job_id, versions=import_versions()
    )


async def _parse_body(request: Request) -> dict[str, Any]:
    try:
        body = await request.json()
    except ValueError as exc:
        raise SnapshotInvalid("요청 바디가 유효한 JSON이 아님", str(exc)) from exc
    if not isinstance(body, dict):
        raise SnapshotInvalid("요청 바디는 객체여야 함")
    return body


@router.post("/v1/imports", status_code=202)
async def post_import(request: Request) -> dict[str, Any]:
    """업로드 접수 — 프로파일링·매핑 추론 후 202 + job_id·status(§1.1)."""
    _require_headers(request, _WRITE_HEADERS)
    tenant_id = request.headers["X-Tenant-Id"]
    idempotency_key = request.headers["Idempotency-Key"]
    raw_body = await _parse_body(request)

    try:
        req = ImportCreateRequest.model_validate(raw_body)
    except ValidationError as exc:
        raise SnapshotInvalid("요청 바디 스키마 위반", exc.errors(include_url=False)) from exc

    body_hash = _body_hash(req.model_dump(mode="json"))
    hit = await _idempotency_store.get(
        tenant_id=tenant_id, endpoint=_POST_ENDPOINT, idempotency_key=idempotency_key
    )
    if hit is not None:
        if hit.snapshot_hash == body_hash:
            return hit.response_body  # 데코레이터 status_code=202로 반환
        raise IdempotencyConflict("같은 Idempotency-Key에 다른 바디", {"key": idempotency_key})

    settings = get_import_settings()
    job = ImportJob(job_id=str(uuid.uuid4()), tenant_id=tenant_id, status=ImportStatus.PROFILING)
    _job_store.create(job)

    try:
        data = _source_loader.load(req.source_url)
        profile = profile_source(
            data, req.filename, max_rows=settings.import_sample_max_rows, redactor=_redactor
        )
    except (ProfilingError, NotImplementedError) as exc:
        assert_transition(ImportStatus.PROFILING, ImportStatus.FAILED)
        job.status = ImportStatus.FAILED
        job.status_reason = "file_unreadable"
        _job_store.update(job)
        logger.info("import 프로파일 실패 job=%s reason=%s", job.job_id, type(exc).__name__)
    else:
        job.profile = profile
        outcome = await infer_mapping(
            profile,
            tenant_id,
            provider=_mapping_provider,
            cache=_spec_cache,
            confidence_review=settings.import_confidence_review,
        )
        job.preview = outcome.preview
        job.status = outcome.status  # 항상 PREVIEW_READY — 차단 축 없음(2026-07-30 확정)
        if outcome.needs_probing and _probe_enqueuer is not None:
            # **저신뢰 컬럼이 있으면 조사한다**(§3.3 기동 조건). 필수 충족 여부는 보지 않는다
            # — 필수 판단이 백엔드로 갔으므로 조사 기동에서 그 축이 빠졌다(2026-07-30 확정).
            assert_transition(ImportStatus.INFERRING, ImportStatus.PROBING)  # state.py 규칙
            probe_job = await _probe_enqueuer.enqueue(
                tenant_id=tenant_id, profile=profile, file_hash=body_hash
            )
            job.status = ImportStatus.PROBING
            logger.info("import probing enqueue job=%s probe=%s", job.job_id, probe_job.job_id)
        elif outcome.needs_probing:
            logger.info("import probing 대상(enqueuer 미배선 — preview 유지) job=%s", job.job_id)
        _job_store.update(job)

    body_out = _view_body(job)
    await _idempotency_store.put(
        tenant_id=tenant_id,
        endpoint=_POST_ENDPOINT,
        idempotency_key=idempotency_key,
        snapshot_hash=body_hash,
        response_body=body_out,
    )
    return body_out


def _load_job(request: Request, job_id: str, headers: tuple[str, ...]) -> ImportJob:
    _require_headers(request, headers)
    tenant_id = request.headers["X-Tenant-Id"]
    job = _job_store.get(job_id)
    if job is None or job.tenant_id != tenant_id:
        raise NotFound("job_id 부재", {"job_id": job_id})  # 존재 은닉(테넌트 불일치 포함)
    return job


@router.get("/v1/imports/{job_id}")
async def get_import(job_id: str, request: Request) -> dict[str, Any]:
    """상태·미리보기 보조 조회(§1.2)."""
    job = _load_job(request, job_id, _READ_HEADERS)
    return _view_body(job)


@router.post("/v1/imports/{job_id}/confirm")
async def confirm_import(job_id: str, request: Request) -> dict[str, Any]:
    """강사 확정 → override 재검증 → 확정 spec 캐시(§1.3). 전체 행 변환은 백엔드 소유(§4)."""
    job = _load_job(request, job_id, _WRITE_HEADERS)
    tenant_id = request.headers["X-Tenant-Id"]
    idempotency_key = request.headers["Idempotency-Key"]
    raw_body = await _parse_body(request)

    try:
        confirm = ConfirmRequest.model_validate(raw_body)
    except ValidationError as exc:
        raise SnapshotInvalid("confirm 바디 스키마 위반", exc.errors(include_url=False)) from exc

    if job.status is not ImportStatus.PREVIEW_READY or job.preview is None:
        raise SnapshotInvalid("확정 불가 상태", {"status": job.status.value})

    for override in confirm.spec_overrides:
        if override.target_field is not None and override.target_field not in STANDARD_FIELDS:
            raise SnapshotInvalid(
                "target_field가 표준 필드명이 아님", {"field": override.target_field}
            )

    body_hash = _body_hash(confirm.model_dump(mode="json"))
    hit = await _idempotency_store.get(
        tenant_id=tenant_id, endpoint=_CONFIRM_ENDPOINT, idempotency_key=idempotency_key
    )
    if hit is not None:
        if hit.snapshot_hash == body_hash:
            return hit.response_body
        raise IdempotencyConflict("같은 Idempotency-Key에 다른 바디", {"key": idempotency_key})

    overrides = {o.source_column: o.target_field for o in confirm.spec_overrides}
    new_columns = columns_from_overrides(job.preview.columns, overrides)

    # **필수 재검증 게이트 없음** — 필수 여부 판단은 백엔드가 Import 유형별 규칙으로 한다
    # (2026-07-30 확정). override를 적용해 미매핑 표준 필드 목록만 갱신하고 확정 spec을
    # 캐시한다. 어느 표준 필드가 비었는지는 응답의 unmapped_target_fields가 계속 알려준다.
    job.preview = job.preview.model_copy(
        update={
            "columns": new_columns,
            "unmapped_target_fields": unmapped_target_fields(mapped_targets(new_columns)),
        }
    )
    if job.profile is not None:  # 확정 spec 캐시 → 다음 재수입 reused(§3.4)
        _spec_cache.put(
            form_signature(job.profile, tenant_id), confirmed_cache_entry(job.preview)
        )
    # TODO(99 D-㉑ⓐ): 확정 매핑 전달 API 계약 구체화 대기 — 경로·요청 형식은 백엔드와 이후
    # 확정한다(델타가 아니라 **전체 spec** 방향 합의). 현행 confirm은 **과도기**다 —
    # 확정 매핑의 기준 데이터는 백엔드가 보유하고 AI는 양식 재사용을 위해 전달받아 활용한다
    # (2026-07-30 확정). 여기서는 확정 spec 캐시만 하고 판정·차단은 하지 않는다.
    logger.info("import 확정 spec 캐시 job=%s status=%s", job.job_id, job.status.value)

    _job_store.update(job)
    body_out = _view_body(job)
    await _idempotency_store.put(
        tenant_id=tenant_id,
        endpoint=_CONFIRM_ENDPOINT,
        idempotency_key=idempotency_key,
        snapshot_hash=body_hash,
        response_body=body_out,
    )
    return body_out

#: 이 라우터가 응답하는 경로 접두와 그 버전 세트 — `api/app.py`가 **실패 응답**에 쓴다(99 ㊓).
#: 🔴 접두를 여기 두는 이유: **경로를 바꾸는 사람과 접두를 고치는 사람이 같아야 한다.**
#:  `app.py`에 박으면 다른 파일이라 조용히 갈린다.
#: `/v1/imports`·`/{job_id}`·`/confirm` 셋을 한 접두가 덮는다.
VERSION_SCOPE: Final = RouterScope("/v1/imports", import_versions)
