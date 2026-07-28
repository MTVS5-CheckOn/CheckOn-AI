"""감지 라우터 — POST /v1/detect (동기, 04 §2.4 · 09).

소유: 박진희 (detection 라우터). 엔진(순수 함수)을 HTTP로 노출하고, 산출물을 저장 계층에
적재한다 — 엔진은 여전히 요청 구동(시그니처 무변경)이라 요청만으로 이력이 충분하면 DB
없이도 동일하게 동작한다(데모·골든·초기 연동).

**저장 실패 정책이 두 저장소에서 다르다 (D-② 확정) — 헷갈리지 말 것:**
- 멱등 캐시(idempotency, 04 §2.3)는 **fail-open** — 저장·조회 실패를 삼킨다(best-effort).
  같은 Idempotency-Key + 같은 snapshot_hash = 기존 결과 200 재반환 · 다른 hash = 409.
  키 스코프 = (tenant_id, endpoint, idempotency_key). 바디 동일성은 snapshot_hash 판정
  (04 부록 A canonical 해시 — 내부 백엔드 전용 신뢰 전제, 외부 노출 시 재검토).
- 감지 원장(detection_store: AI_RUN·SIGNAL·FEATURE_WEEK)은 **fail-closed** — 적재 실패
  시 LedgerWriteFailed(500)로 요청을 실패시킨다(근거·재현 기록이라 삼키지 않음).

저장소는 인터페이스로 분리해 settings.store_backend로 InMemory↔PG를 고른다(store_factory).
기본 memory라 CI·데모는 DB 없이 통과한다(실 PG는 99 ⑨·⑫).
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Any

from fastapi import APIRouter, Request
from pydantic import ValidationError

from ai.api.envelope import success_envelope
from ai.composition.briefing import make_brief
from ai.composition.briefing_context import build_contexts
from ai.composition.provider import build_brief_gateway, build_brief_provider
from ai.contracts.detection import (
    CONSENT_GRANTED,
    OBSERVED_ONLY_MIN_WEEKS,
    Brief,
    DetectRequest,
    DetectResponse,
    Signal,
    StudentStatus,
)
from ai.contracts.execution import Capability, ExecutionContext, RunMetadata, VersionSet
from ai.contracts.llm import LLMProvider
from ai.db.repositories.detection_store import (
    DetectionStore,
    FeatureWeekRow,
    LedgerWrite,
    dedupe_learning_events,
)
from ai.db.repositories.idempotency import IdempotencyStore, system_utc_now
from ai.db.store_factory import build_detection_store, build_idempotency_store
from ai.detection.engine import detect
from ai.detection.features import (
    WeekFeatures,
    extract_features,
    week_features_from_metrics,
)
from ai.detection.lifecycle import has_return_care_history
from ai.detection.segments import resolve_segment
from ai.detection.thresholds import ThresholdConfig, default_threshold_config
from ai.llm.gateway import LlmGateway
from ai.runtime.errors import IdempotencyConflict, SnapshotInvalid

logger = logging.getLogger(__name__)

router = APIRouter()

#: 앱 버전 메타 — v0 플레이스홀더. Settings 이관은 후속(버전 문자열은 앱 메타).
_PIPELINE_VERSION = "0.1.0"
_ENGINE_VERSION = "detection-rules-0.1"
_SCHEMA_VERSION = "0.1"
_CONTRACT_VERSION = "0.1"
#: FEATURE_WEEK 적재 피처 버전 — 피처 산식이 바뀌면 올린다(재현성 키).
_FEATURE_VERSION = "0.1"

_REQUIRED_HEADERS = ("X-Tenant-Id", "X-Request-Id", "Idempotency-Key")

#: 이 엔드포인트의 멱등 키 스코프(endpoint 성분).
_ENDPOINT = "POST /v1/detect"

#: 시계 주입점 — created_at 등(datetime.now() 직접 호출 금지).
_clock = system_utc_now

#: 저장소 — settings.store_backend로 InMemory↔PG. 캐시=fail-open, 원장=fail-closed.
_idempotency_store: IdempotencyStore = build_idempotency_store()
_detection_store: DetectionStore = build_detection_store()

#: 브리핑 문장화(ⓐ) — provider는 settings로 fake↔openai_compat(기본 fake). 총 예산 45s.
#: LLM 호출은 gateway(role=narrator, 전송 재시도 0) 경유 — 어댑터 직결 종료(03_coding_rules §2).
_brief_provider: LLMProvider = build_brief_provider()
_brief_gateway: LlmGateway = build_brief_gateway(_brief_provider)
_BRIEFING_BUDGET_S = 45.0
#: 신호별 브리핑 LLM 호출 동시 실행 상한 — 팀 로컬 서버 부하를 배려한 세마포어(v3 병렬화).
_BRIEFING_CONCURRENCY = 3


def set_brief_provider(provider: LLMProvider) -> None:
    """브리핑 provider 주입 — 테스트에서 실패·게이트 시나리오 mock을 꽂는다.

    provider는 narrator 게이트웨이로 감싸 주입한다 — make_brief는 gateway 경유로 호출한다
    (직결 제거). mock의 호출 횟수·예외는 gateway가 그대로 통과시켜 기존 검증이 유지된다.
    """
    global _brief_provider, _brief_gateway
    _brief_provider = provider
    _brief_gateway = build_brief_gateway(provider)


def reset_brief_provider() -> None:
    """테스트 격리용 — 브리핑 provider·게이트웨이를 재빌드한다(기본 fake)."""
    set_brief_provider(build_brief_provider())


def set_idempotency_store(store: IdempotencyStore) -> None:
    """저장소 주입 — 실 PG 배선(합성 루트)·테스트에서 특정 인스턴스를 꽂는다."""
    global _idempotency_store
    _idempotency_store = store


def set_detection_store(store: DetectionStore) -> None:
    """저장소 주입 — 실 PG 배선(합성 루트)·테스트에서 특정 인스턴스를 꽂는다."""
    global _detection_store
    _detection_store = store


def reset_detection_store() -> None:
    """테스트 격리용 — 원장 저장소를 재빌드한다(기본 백엔드)."""
    set_detection_store(build_detection_store())


def reset_idempotency_store() -> None:
    """테스트 격리용 — 멱등 저장소를 재빌드한다(기본 백엔드)."""
    set_idempotency_store(build_idempotency_store())


def detection_versions(config: ThresholdConfig | None = None) -> VersionSet:
    """detection 실행/엔드포인트의 버전 세트 — threshold는 config 버전, LLM/B 키는 None.

    config가 없으면(실행 전 오류의 meta.versions 조립) 기본 config로 정적 버전을 낸다
    (04 §2.2 A판정 — 실패 응답도 이 엔드포인트의 버전을 싣는다).
    """
    config = config or default_threshold_config()
    return VersionSet(
        pipeline_version=_PIPELINE_VERSION,
        engine_version=_ENGINE_VERSION,
        schema_version=_SCHEMA_VERSION,
        contract_version=_CONTRACT_VERSION,
        threshold_version=config.threshold_version,
    )


def _format_validation_error(error: ValidationError) -> list[dict[str, str]]:
    """pydantic 오류를 필드 경로 + 메시지로 (사람이 읽을 형태)."""
    return [
        {"field": ".".join(str(part) for part in item["loc"]), "message": item["msg"]}
        for item in error.errors()
    ]


def _week_metrics(week: WeekFeatures) -> dict[str, Any]:
    """WeekFeatures → FEATURE_WEEK.metrics(jsonb). 안정적 키 집합(재현성)."""
    return {
        "accuracy": week.accuracy,
        "norm_time": week.norm_time,
        "submitted": week.submitted,
        "n_solves": week.n_solves,
        "event_count": week.event_count,
        "tagging_rate": week.tagging_rate,
    }


def _build_feature_weeks(request: DetectRequest) -> tuple[FeatureWeekRow, ...]:
    """엔진이 평가하는 학생(동의·미정지·재원 2주+)의 주차 피처를 FEATURE_WEEK 행으로.

    제외 조건과 segment 판정은 엔진(engine.detect)과 같은 공개 상수·함수를 쓴다 —
    임계값·규칙을 재구현하지 않는다(값이 바뀌면 한 곳에서 바뀐다).
    """
    features = extract_features(request)
    term_context = request.snapshot_meta.term_context
    rows: list[FeatureWeekRow] = []
    for student in request.students:
        if student.consent != CONSENT_GRANTED or student.status is StudentStatus.PAUSED:
            continue
        if student.enrolled_weeks < OBSERVED_ONLY_MIN_WEEKS:
            continue
        sf = features.get(student.student_ref)
        if sf is None:
            continue
        segment = resolve_segment(
            student.status,
            term_context,
            has_return_care_history(student.student_ref, request.alert_context),
        )
        rows.extend(
            FeatureWeekRow(
                student_ref=student.student_ref,
                week_start=week.week_monday,
                segment=segment.value,
                metrics=_week_metrics(week),
                feature_version=_FEATURE_VERSION,
            )
            for week in sf.weeks
        )
    return tuple(rows)


def _build_ledger(
    execution_id: uuid.UUID,
    tenant_id: str,
    snapshot_hash: str,
    request: DetectRequest,
    response: DetectResponse,
    config: ThresholdConfig,
) -> LedgerWrite:
    """원장 적재 묶음 조립 — AI_RUN(RunMetadata)·SIGNAL·FEATURE_WEEK."""
    run: RunMetadata = ExecutionContext(
        execution_id=execution_id,
        tenant_id=tenant_id,
        capability=Capability.DETECTION,
        input_snapshot_hash=snapshot_hash,
        versions=detection_versions(config),
    ).to_run_metadata(created_at=_clock())
    return LedgerWrite(
        run=run,
        signals=response.signals,
        feature_weeks=_build_feature_weeks(request),
    )


async def _load_stored_features(
    tenant_id: str, request: DetectRequest
) -> dict[str, list[WeekFeatures]]:
    """축적 FEATURE_WEEK를 요청 students 전원분 조회 → 학생별 WeekFeatures로 되살린다.

    조회 실패는 저장소가 fail-closed(500)로 올린다 — baseline이 반쪽이면 판정이 조용히
    왜곡(미탐)되므로 폴백하지 않는다. memory 백엔드도 같은 경로(축적분이 있으면 병합).
    """
    student_refs = [student.student_ref for student in request.students]
    rows = await _detection_store.load_feature_weeks(tenant_id, student_refs)
    stored: dict[str, list[WeekFeatures]] = {}
    for row in rows:
        stored.setdefault(row.student_ref, []).append(
            week_features_from_metrics(row.week_start, row.metrics)
        )
    return stored


async def _apply_briefing(
    response: DetectResponse,
    request: DetectRequest,
    stored_features: dict[str, list[WeekFeatures]],
    execution_id: uuid.UUID,
    tenant_id: str,
    snapshot_hash: str,
    config: ThresholdConfig,
) -> DetectResponse:
    """신호별 brief를 문장화(ⓐ)로 교체. 총 예산 45s 안에서, 실패는 템플릿 폴백.

    detection 판정(신호·score·lifecycle·evidence)은 건드리지 않고 brief만 바꾼다.
    근거 패키지(v2)는 엔진과 같은 공개 피처 함수로 조립한다(판정식 미접근).
    LLM_CALL.outcome은 로그로만(DB 적재는 후속 — 99 15).
    """
    context = ExecutionContext(
        execution_id=execution_id,
        tenant_id=tenant_id,
        capability=Capability.COMPOSITION,
        input_snapshot_hash=snapshot_hash,
        versions=detection_versions(config),
    )
    contexts = build_contexts(
        request, response.signals, stored_features=stored_features, config=config
    )
    # 병렬화(v3) — 신호별 문장화를 세마포어(동시 N)로 동시에 돌린다. 총 예산 45s는
    # make_brief가 호출 시작 시 deadline을 검사해 지킨다(세마포어 대기 후 시작한 콜이
    # deadline을 넘겼으면 호출 없이 폴백). 판정·순서는 무변(gather가 순서 보존).
    deadline = time.monotonic() + _BRIEFING_BUDGET_S
    semaphore = asyncio.Semaphore(_BRIEFING_CONCURRENCY)

    async def _brief_one(signal: Signal) -> tuple[Brief, str]:
        async with semaphore:
            return await make_brief(
                contexts[signal.signal_id],
                _brief_gateway,
                context=context,
                now=time.monotonic,
                deadline=deadline,
            )

    outcomes = await asyncio.gather(*(_brief_one(s) for s in response.signals))
    briefed = []
    for signal, (brief, outcome) in zip(response.signals, outcomes, strict=True):
        if brief.fallback_used:
            logger.info("brief 폴백 rule=%s outcome=%s", signal.rule_id.value, outcome)
        briefed.append(signal.model_copy(update={"brief": brief}))
    return response.model_copy(update={"signals": tuple(briefed)})


@router.post("/v1/detect")
async def post_detect(request: Request) -> dict[str, Any]:
    """감지 실행. 헤더·바디 검증 → 멱등(fail-open) → dedupe → 엔진 → 원장(fail-closed)."""
    missing = [name for name in _REQUIRED_HEADERS if not request.headers.get(name)]
    if missing:
        raise SnapshotInvalid("필수 헤더 누락", {"missing_headers": missing})

    tenant_id = request.headers["X-Tenant-Id"]
    idempotency_key = request.headers["Idempotency-Key"]

    try:
        raw_body = await request.json()
    except ValueError as exc:
        raise SnapshotInvalid("요청 바디가 유효한 JSON이 아님", str(exc)) from exc

    try:
        detect_request = DetectRequest.model_validate(raw_body)
    except ValidationError as exc:
        raise SnapshotInvalid(
            "요청 바디 스키마 위반", _format_validation_error(exc)
        ) from exc

    snapshot_hash = detect_request.snapshot_meta.snapshot_hash

    # 멱등 조회 (fail-open) — 같은 키+같은 바디 재반환 / 다른 바디 409.
    hit = await _idempotency_store.get(
        tenant_id=tenant_id, endpoint=_ENDPOINT, idempotency_key=idempotency_key
    )
    if hit is not None:
        if hit.snapshot_hash == snapshot_hash:
            return hit.response_body  # 같은 키 + 같은 바디 → 기존 결과 재반환
        raise IdempotencyConflict(
            "같은 Idempotency-Key에 다른 바디", {"idempotency_key": idempotency_key}
        )

    # record_id dedupe (순수) — 재전송 정정은 정상 업무, 최신 승리.
    deduped, corrections = dedupe_learning_events(detect_request.learning_events)
    if corrections:
        logger.info("재전송 정정 %d건 tenant=%s", len(corrections), tenant_id)
    merged = detect_request.model_copy(update={"learning_events": deduped})

    config = default_threshold_config()
    # baseline read-path (D-②b) — 축적 FEATURE_WEEK 조회(fail-closed) → 엔진에 주입.
    stored_features = await _load_stored_features(tenant_id, merged)
    response = detect(merged, config, stored_features=stored_features)
    execution_id = uuid.uuid4()

    # 브리핑 문장화 (ⓐ) — 신호의 brief만 교체(detection 무변경·판정 무변, 분기표 #6).
    response = await _apply_briefing(
        response, merged, stored_features, execution_id, tenant_id, snapshot_hash, config
    )

    envelope = success_envelope(
        data=response.model_dump(mode="json"),
        execution_id=str(execution_id),
        versions=detection_versions(config),
    )

    # 원장 적재 (fail-closed) — 실패 시 LedgerWriteFailed(500), 응답 전에 막는다.
    await _detection_store.persist_ledger(
        _build_ledger(execution_id, tenant_id, snapshot_hash, merged, response, config)
    )

    # 멱등 저장 (fail-open) — 실패해도 요청은 성공.
    await _idempotency_store.put(
        tenant_id=tenant_id,
        endpoint=_ENDPOINT,
        idempotency_key=idempotency_key,
        snapshot_hash=snapshot_hash,
        response_body=envelope,
    )
    return envelope
