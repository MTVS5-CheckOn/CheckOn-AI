"""문의 분류 라우터 — `POST /v1/classify` (04 §3.5 · `part_a/01` §4-ⓑ).

소유: 박진희 (composition 라우터). 동기 · 200. 선례는 `detect` 라우터(헤더 검증 → 바디
검증 → 실행 → envelope)다.

**Idempotency-Key를 받지 않는다.** 부작용 없는 동기 호출이고 멱등 저장이 없다 — 같은 본문은
결정론 설정(`temperature=0.0`·`seed` 고정)으로 같은 결과가 나온다. 멱등 키를 요구하면
BE가 관리할 상태만 늘고 얻는 게 없다.

**CI 기본은 Fake provider** — 실 LLM 호출 0. 실 경로는 env `LLM_PROVIDER=openai_compat`.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import APIRouter, Request
from pydantic import ValidationError

from ai.api.envelope import success_envelope
from ai.composition.classify.classifier import classify, classify_versions
from ai.composition.classify.provider import build_classify_gateway
from ai.contracts.classify import ClassifyRequest
from ai.contracts.execution import Capability, ExecutionContext
from ai.runtime.errors import SnapshotInvalid

logger = logging.getLogger(__name__)

router = APIRouter()

#: ⚠ `Idempotency-Key`는 **없다**(위 모듈 docstring 참조).
_REQUIRED_HEADERS = ("X-Tenant-Id", "X-Request-Id")


def _format_validation_error(exc: ValidationError) -> list[dict[str, str]]:
    """필드 경로만 — 값은 싣지 않는다(04 §2.3 · 개인정보가 detail로 새지 않게).

    🔴 분류 요청의 `body_text`는 **원문**이라 이 규칙이 특히 중요하다.
    """
    return [
        {"field": ".".join(str(part) for part in error["loc"]), "type": error["type"]}
        for error in exc.errors()
    ]


@router.post("/v1/classify")
async def post_classify(request: Request) -> dict[str, Any]:
    """문의 1건을 3축(topic·sentiment·urgency)으로 분류한다 — 동기 200.

    분류 실패는 **500이 아니다** — `classified: false` + 사유로 200을 낸다
    (`error_codes` :213 "정렬 없이 시간순 표시"). LLM 장애만 503으로 올라간다.
    """
    missing = [name for name in _REQUIRED_HEADERS if not request.headers.get(name)]
    if missing:
        raise SnapshotInvalid("필수 헤더 누락", {"missing_headers": missing})
    tenant_id = request.headers["X-Tenant-Id"]

    try:
        raw_body = await request.json()
    except ValueError as exc:
        raise SnapshotInvalid("요청 바디가 유효한 JSON이 아님", str(exc)) from exc
    try:
        classify_request = ClassifyRequest.model_validate(raw_body)
    except ValidationError as exc:
        raise SnapshotInvalid(
            "요청 바디 스키마 위반", _format_validation_error(exc)
        ) from exc

    execution_id = uuid.uuid4()
    versions = classify_versions()
    context = ExecutionContext(
        execution_id=execution_id,
        tenant_id=tenant_id,
        capability=Capability.COMPOSITION,
        # ⚠ 분류는 스냅숏을 받지 않는다 — 재현 키는 요청 참조다(원문은 싣지 않는다).
        input_snapshot_hash=f"inquiry:{classify_request.inquiry_ref}",
        versions=versions,
    )
    result = await classify(
        classify_request, build_classify_gateway(), context=context
    )
    return success_envelope(
        data=result.model_dump(mode="json"),
        execution_id=str(execution_id),
        versions=versions,
    )


__all__ = ["router"]
