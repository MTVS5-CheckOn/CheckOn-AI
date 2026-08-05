"""확정 회신 라우터 — `POST /v1/confirmations` (04 §3.3 · P2-c).

소유: 박진희. 강사 확정·정정을 되돌려 받아 **평가셋 루프를 닫는다**(`part_a/03` §C7).

**Idempotency-Key를 받지 않는다.** 자연키 `(tenant_id, inquiry_ref)` 갱신이라 같은 회신을
두 번 보내도 결과가 같다 — 멱등 키를 요구하면 BE가 관리할 상태만 늘고 얻는 게 없다
(`/v1/classify`와 같은 판단).

**v1이 처리하는 kind는 `classification` 하나다.** 나머지 셋은 제안 생성기가 없어 확정할
대상이 존재하지 않으므로 **400으로 정직하게 거절**한다 — 받아서 조용히 버리면 BE가
"저장됐다"고 오해한다.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import APIRouter, Request
from pydantic import ValidationError

from ai.api.envelope import success_envelope
from ai.composition.classify.classifier import classify_versions
from ai.contracts.confirmations import (
    ConfirmationAction,
    ConfirmationKind,
    ConfirmationRequest,
    ConfirmationResponse,
)
from ai.db.repositories.inquiry_class_store import InquiryClassStore
from ai.db.store_factory import build_inquiry_class_store
from ai.runtime.errors import NotFound, SnapshotInvalid

logger = logging.getLogger(__name__)

router = APIRouter()

_REQUIRED_HEADERS = ("X-Tenant-Id", "X-Request-Id")

#: 미지원 kind의 사유 코드 — `error_codes.md` 등재.
KIND_NOT_IMPLEMENTED = "kind_not_implemented"

#: `classification`이 받지 않는 action의 사유 코드.
ACTION_NOT_SUPPORTED = "action_not_supported"

_store: InquiryClassStore = build_inquiry_class_store()


def set_inquiry_class_store(store: InquiryClassStore) -> None:
    """저장소 주입 — 테스트·합성 루트의 seam(`set_counsel_provider` 선례)."""
    global _store
    _store = store


def reset_inquiry_class_store() -> None:
    """테스트 격리용 — 기본 저장소로 되돌린다."""
    global _store
    _store = build_inquiry_class_store()


def _format_validation_error(exc: ValidationError) -> list[dict[str, str]]:
    """필드 경로만 — 값은 싣지 않는다(04 §2.3)."""
    return [
        {"field": ".".join(str(part) for part in error["loc"]), "type": error["type"]}
        for error in exc.errors()
    ]


@router.post("/v1/confirmations")
async def post_confirmations(request: Request) -> dict[str, Any]:
    """강사 확정·정정 수신 — 동기 200.

    `kind=classification`만 처리한다. `suggestion_id`는 **`inquiry_ref`**다(04 §3.3).
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
        confirmation = ConfirmationRequest.model_validate(raw_body)
    except ValidationError as exc:
        raise SnapshotInvalid(
            "요청 바디 스키마 위반", _format_validation_error(exc)
        ) from exc

    if confirmation.kind is not ConfirmationKind.CLASSIFICATION:
        # 🔴 조용히 버리지 않는다 — 제안 생성기가 없어 확정할 대상이 없다.
        raise SnapshotInvalid(
            "미지원 kind",
            {
                "reason": KIND_NOT_IMPLEMENTED,
                "kind": confirmation.kind.value,
                "detail": "제안 생성기 미구현 — v1은 classification만 확정을 받는다",
            },
        )

    if confirmation.action is ConfirmationAction.REJECTED:
        # 3축은 값이 반드시 있어야 하는 축이라 "거절"이 정의되지 않는다.
        raise SnapshotInvalid(
            "미지원 action",
            {
                "reason": ACTION_NOT_SUPPORTED,
                "action": confirmation.action.value,
                "detail": "classification은 confirmed|corrected만 받는다",
            },
        )

    corrections = (
        confirmation.corrected_value.as_axis_map()
        if confirmation.action is ConfirmationAction.CORRECTED
        and confirmation.corrected_value is not None
        else {}
    )
    applied = await _store.apply_confirmation(
        tenant_id=tenant_id,
        inquiry_ref=confirmation.suggestion_id,
        corrections=corrections,
    )
    if not applied:
        # 대상 분류가 없다 — 폴백이었거나(적재 안 함) 분류를 부른 적이 없다.
        raise NotFound(
            "대상 분류 없음", {"inquiry_ref": confirmation.suggestion_id}
        )

    logger.info(
        "confirmations.applied inquiry_ref=%s action=%s axes=%d",
        confirmation.suggestion_id,
        confirmation.action.value,
        len(corrections),
    )
    return success_envelope(
        data=ConfirmationResponse(accepted=True).model_dump(mode="json"),
        execution_id=str(uuid.uuid4()),
        versions=classify_versions(),
    )


__all__ = [
    "ACTION_NOT_SUPPORTED",
    "KIND_NOT_IMPLEMENTED",
    "reset_inquiry_class_store",
    "router",
    "set_inquiry_class_store",
]
