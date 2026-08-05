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
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Request
from pydantic import ValidationError

from ai.api.envelope import success_envelope
from ai.composition.classify.classifier import classify, classify_versions
from ai.composition.classify.provider import build_classify_gateway
from ai.contracts.classify import AxisConfidence, ClassifyRequest, ClassifyResult
from ai.contracts.counsel import InquirySentiment, InquiryTopic, InquiryUrgency
from ai.contracts.execution import Capability, ExecutionContext
from ai.db.repositories.inquiry_class_store import (
    InquiryClassRecord,
    InquiryClassStore,
)
from ai.db.store_factory import build_inquiry_class_store
from ai.runtime.errors import SnapshotInvalid

logger = logging.getLogger(__name__)

router = APIRouter()

#: ⚠ `Idempotency-Key`는 **없다**(위 모듈 docstring 참조).
_REQUIRED_HEADERS = ("X-Tenant-Id", "X-Request-Id")

_store: InquiryClassStore = build_inquiry_class_store()


def set_inquiry_class_store(store: InquiryClassStore) -> None:
    """저장소 주입 — 테스트·합성 루트의 seam."""
    global _store
    _store = store


def reset_inquiry_class_store() -> None:
    global _store
    _store = build_inquiry_class_store()


def _to_result(inquiry_ref: str, record: InquiryClassRecord) -> ClassifyResult:
    """저장된 예측 → 계약 응답. **캐시 히트도 정상 응답이다**(`classified=True`)."""
    return ClassifyResult(
        inquiry_ref=inquiry_ref,
        topic=InquiryTopic(record.topic),
        sentiment=InquirySentiment(record.sentiment),
        urgency=InquiryUrgency(record.urgency),
        confidence=AxisConfidence(
            topic=float(record.confidence_topic),
            sentiment=float(record.confidence_sentiment),
            urgency=float(record.confidence_urgency),
        ),
    )


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
    # ① 캐시 — 같은 `(tenant_id, inquiry_ref)`는 저장분을 돌려주고 **LLM을 안 부른다**
    #    (04:132 "분류·태깅(캐시)" · 04:418 태깅 선례). 재시도가 멱등키 없이 안전해지고,
    #    서버가 seed를 존중하는지 미확인인 상태(99 ㊼)에서도 같은 문의엔 같은 답이 나간다.
    cached = await _store.get(
        tenant_id=tenant_id, inquiry_ref=classify_request.inquiry_ref
    )
    if cached is not None:
        return success_envelope(
            data=_to_result(classify_request.inquiry_ref, cached).model_dump(mode="json"),
            execution_id=str(execution_id),
            versions=versions,
        )

    result = await classify(
        classify_request, build_classify_gateway(), context=context
    )
    # ② 적재 — **판정이 선 건만**(불변식 2 · P2-b 조건 4). 폴백 건은 행을 만들지 않으므로
    #    캐시도 되지 않고, 재호출하면 다시 시도한다(redaction·파싱 실패는 일시적일 수 있다).
    #    ⚠ 적재 실패는 **삼키지 않는다** — 저장이 이 경로의 목적이고, 실패를 숨기면
    #    "평가셋이 쌓이는 줄 알았는데 비어 있다"가 된다. 캐시 덕에 재시도 비용이 0이다.
    if result.classified:
        await _store.insert_prediction(
            tenant_id=tenant_id,
            inquiry_ref=classify_request.inquiry_ref,
            record=InquiryClassRecord(
                topic=result.topic.value,
                sentiment=result.sentiment.value,
                urgency=result.urgency.value,
                confidence_topic=Decimal(str(result.confidence.topic)),
                confidence_sentiment=Decimal(str(result.confidence.sentiment)),
                confidence_urgency=Decimal(str(result.confidence.urgency)),
            ),
        )
    return success_envelope(
        data=result.model_dump(mode="json"),
        execution_id=str(execution_id),
        versions=versions,
    )


__all__ = ["router"]
