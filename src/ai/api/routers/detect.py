"""감지 라우터 — POST /v1/detect (동기, 04 §2.4 · 09).

소유: 박진희 (detection 라우터). 엔진(순수 함수)을 HTTP로 노출한다 — 상태 없는 v0.
DB 저장(SIGNAL·AI_RUN)은 이번 범위 밖(D-② Alembic 이후).

멱등(04 §2.3): 같은 Idempotency-Key + 같은 snapshot_hash = 기존 결과 200 재반환 ·
같은 키 + 다른 hash = 409 IDEMPOTENCY_CONFLICT. 바디 동일성은 snapshot_hash로 판정한다
(04 부록 A: 요청 본문 전체의 canonical 해시) — **내부 백엔드 전용 신뢰 전제이며,
외부 노출 시 자체 검증을 재검토한다.** 저장소는 IdempotencyStore 인터페이스로 분리했다
(db.repositories.idempotency) — 기본값은 인메모리(재시작 소실), 실 PG 주입은 DB 연동 시
(D-②/99 안건 ⑨·⑫). 키 스코프 = (tenant_id, endpoint, idempotency_key).
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Request
from pydantic import ValidationError

from ai.api.envelope import success_envelope
from ai.contracts.detection import DetectRequest
from ai.contracts.execution import VersionSet
from ai.db.repositories.idempotency import (
    IdempotencyStore,
    InMemoryIdempotencyStore,
)
from ai.detection.engine import detect
from ai.detection.thresholds import ThresholdConfig, default_threshold_config
from ai.runtime.errors import IdempotencyConflict, SnapshotInvalid

router = APIRouter()

#: 앱 버전 메타 — v0 플레이스홀더. Settings 이관은 후속(버전 문자열은 앱 메타).
_PIPELINE_VERSION = "0.1.0"
_ENGINE_VERSION = "detection-rules-0.1"
_SCHEMA_VERSION = "0.1"
_CONTRACT_VERSION = "0.1"

_REQUIRED_HEADERS = ("X-Tenant-Id", "X-Request-Id", "Idempotency-Key")

#: 이 엔드포인트의 멱등 키 스코프(endpoint 성분).
_ENDPOINT = "POST /v1/detect"

#: 멱등 저장소 — 기본은 인메모리(테스트·개발). 실 PG는 DB 연동 시 이 인스턴스를 교체 주입.
_idempotency_store: IdempotencyStore = InMemoryIdempotencyStore()


def reset_idempotency_store() -> None:
    """테스트 격리용 — 멱등 저장소를 빈 인메모리로 되돌린다."""
    global _idempotency_store
    _idempotency_store = InMemoryIdempotencyStore()


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


@router.post("/v1/detect")
async def post_detect(request: Request) -> dict[str, Any]:
    """감지 실행. 헤더·바디 검증 → 멱등 → 엔진 → envelope."""
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

    hit = await _idempotency_store.get(
        tenant_id=tenant_id, endpoint=_ENDPOINT, idempotency_key=idempotency_key
    )
    if hit is not None:
        if hit.snapshot_hash == snapshot_hash:
            return hit.response_body  # 같은 키 + 같은 바디 → 기존 결과 재반환
        raise IdempotencyConflict(
            "같은 Idempotency-Key에 다른 바디", {"idempotency_key": idempotency_key}
        )

    config = default_threshold_config()
    response = detect(detect_request, config)
    envelope = success_envelope(
        data=response.model_dump(mode="json"),
        execution_id=str(uuid.uuid4()),
        versions=detection_versions(config),
    )
    await _idempotency_store.put(
        tenant_id=tenant_id,
        endpoint=_ENDPOINT,
        idempotency_key=idempotency_key,
        snapshot_hash=snapshot_hash,
        response_body=envelope,
    )
    return envelope
