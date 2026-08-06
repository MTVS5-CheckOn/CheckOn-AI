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
from ai.composition.classify.classifier import (
    CLASSIFY_GEN_PARAMS,
    classify,
    classify_versions,
)
from ai.composition.classify.provider import build_classify_gateway
from ai.contracts.classify import AxisConfidence, ClassifyRequest, ClassifyResult
from ai.contracts.counsel import InquirySentiment, InquiryTopic, InquiryUrgency
from ai.contracts.execution import Capability, ExecutionContext
from ai.contracts.llm import LlmError
from ai.db.repositories.inquiry_class_store import (
    InquiryClassRecord,
    InquiryClassStore,
)
from ai.db.repositories.run_store import (
    RunStore,
    default_llm_call_collector,
    last_success_id,
    system_utc_now,
)
from ai.db.store_factory import (
    build_inquiry_class_store,
    build_run_store,
    reset_default_inquiry_class_store,
)
from ai.runtime.errors import SnapshotInvalid, domain_error_for

logger = logging.getLogger(__name__)

router = APIRouter()

#: ⚠ `Idempotency-Key`는 **없다**(위 모듈 docstring 참조).
_REQUIRED_HEADERS = ("X-Tenant-Id", "X-Request-Id")

#: 시계 주입점 — `datetime.now()` 직접 호출 금지(03 §3).
_clock = system_utc_now

#: 🔴 **모듈 전역에 팩토리 결과를 굳히지 않는다.** 굳히면 `/v1/confirmations`가 자기
#: 인스턴스를 따로 갖게 되고(팩토리가 호출마다 새 객체였다) 정정이 전부 404가 됐다.
#: 미주입이면 매번 팩토리를 부르고, 팩토리가 프로세스 공용 1개를 돌려준다.
#: **명시 주입은 여전히 이긴다** — `bootstrap_counsel_provider`가 `_provider`를 덮지 않은
#: 것과 같은 결이다(#108).
_store: InquiryClassStore | None = None
_run_store: RunStore = build_run_store()


def inquiry_class_store() -> InquiryClassStore:
    """이 라우터가 쓸 저장소 — 주입분이 있으면 그것, 없으면 공용."""
    return _store if _store is not None else build_inquiry_class_store()


def set_inquiry_class_store(store: InquiryClassStore) -> None:
    """저장소 주입 — 테스트·합성 루트의 seam."""
    global _store
    _store = store


def set_classify_run_store(store: RunStore) -> None:
    """실행 원장 주입 — 테스트가 AI_RUN·LLM_CALL 적재를 관측하는 seam."""
    global _run_store
    _run_store = store


def reset_inquiry_class_store() -> None:
    """테스트 격리용. ⚠ 재바인딩만으로는 **아무것도 리셋되지 않는다** — 공용 저장소라
    같은 객체를 다시 가리킬 뿐이다. 캐시를 버려야 행이 실제로 사라진다."""
    global _store, _run_store
    _store = None
    reset_default_inquiry_class_store()
    _run_store = build_run_store()
    default_llm_call_collector().reset()


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
    cached = await inquiry_class_store().get(
        tenant_id=tenant_id, inquiry_ref=classify_request.inquiry_ref
    )
    if cached is not None:
        return success_envelope(
            data=_to_result(classify_request.inquiry_ref, cached).model_dump(mode="json"),
            execution_id=str(execution_id),
            versions=versions,
        )

    #: 🔴 refine과 **같은 형태**다(counsel.py) — 부수효과를 `except` 절 안에 두지 않는다.
    failed = True
    try:
        result = await classify(
            classify_request, build_classify_gateway(), context=context
        )
        failed = False
    except LlmError as exc:
        # 🔴 **변환 경계를 여기서 받는다.** `classifier.py`가 *"LlmUnavailable·LlmTimeout은
        #   여기서 삼키지 않는다"* 고 선언하고 올려보내는데 **받는 쪽이 없어서** 전부
        #   `app.py` 일반 핸들러 → 500이었다(8/7 실측). 맞는 값은 504·503이다
        #   (error_codes §4·§6 "LLM 장애는 폴백이 아니다").
        #   ⚠ 판정을 여기서 하지 않는다 — 매핑표는 `domain_error_for` 한 곳이 든다.
        raise domain_error_for(exc) from exc
    finally:
        # ② 실행 원장 — AI_RUN + LLM_CALL. 🔴 **INQUIRY_CLASS보다 먼저**다:
        #    `inquiry_class.llm_call_id`가 `llm_call.id`를 참조하는 **실 FK**라 순서가 뒤집히면
        #    FK 위반으로 죽는다. AI_RUN은 폴백 건에도 남긴다 — 불변식 8은 판정 성공 여부와
        #    무관하게 "모든 실행"을 기록하고, 폴백 원인 추적에 호출 기록이 정확히 필요하다.
        # 🔴 **장애 건도 여기를 지난다.** 위 주석이 "모든 실행"이라고 선언해 놓고 종전에는
        #    호출 뒤에만 있어 장애 건이 빠졌다 — 선언과 코드가 갈린 자리였다(8/7 실측:
        #    AI_RUN 0 · 수집기 잔존 1). 분류는 파싱 재시도로 호출을 여러 번 하고 실패하므로
        #    **수집기에 레코드가 실제로 쌓인 채** 방치됐다.
        # ⚠ 실패 경로에서만 적재 오류를 삼킨다 — 원인 예외를 덮으면 진단이 뒤집힌다.
        calls = default_llm_call_collector().take(execution_id)
        last = calls[-1].record if calls else None
        try:
            await _run_store.record_run(
                context.to_run_metadata(
                    created_at=_clock(),
                    model_provider=last.provider if last is not None else None,
                    model_name=last.model if last is not None else None,
                    generation_params=CLASSIFY_GEN_PARAMS,
                ),
                calls,
            )
        except Exception:  # noqa: BLE001 — 원인 예외를 덮지 않는다(위 주석)
            if not failed:
                raise
            logger.exception("분류 장애 건의 원장 적재 실패 — 원인 예외를 유지한다")
    # ③ 적재 — **판정이 선 건만**(불변식 2 · P2-b 조건 4). 폴백 건은 행을 만들지 않으므로
    #    캐시도 되지 않고, 재호출하면 다시 시도한다(redaction·파싱 실패는 일시적일 수 있다).
    #    ⚠ 적재 실패는 **삼키지 않는다** — 저장이 이 경로의 목적이고, 실패를 숨기면
    #    "평가셋이 쌓이는 줄 알았는데 비어 있다"가 된다. 캐시 덕에 재시도 비용이 0이다.
    if result.classified:
        await inquiry_class_store().insert_prediction(
            tenant_id=tenant_id,
            inquiry_ref=classify_request.inquiry_ref,
            record=InquiryClassRecord(
                topic=result.topic.value,
                sentiment=result.sentiment.value,
                urgency=result.urgency.value,
                confidence_topic=Decimal(str(result.confidence.topic)),
                confidence_sentiment=Decimal(str(result.confidence.sentiment)),
                confidence_urgency=Decimal(str(result.confidence.urgency)),
                # 판정을 만든 마지막 성공 호출 — 파싱 재시도의 앞 시도는 버려진 것이다
                # (99 ㊻ⓕ 해소). 수집이 비면 None이고 그건 정직한 부재다.
                llm_call_id=last_success_id(calls),
            ),
        )
    return success_envelope(
        data=result.model_dump(mode="json"),
        execution_id=str(execution_id),
        versions=versions,
    )


__all__ = ["router", "set_classify_run_store"]
