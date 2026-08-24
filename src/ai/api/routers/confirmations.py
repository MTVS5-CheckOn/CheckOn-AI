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
from typing import Any, Final

from fastapi import APIRouter, Request
from pydantic import ValidationError

from ai.api.envelope import success_envelope
from ai.api.version_scope import RouterScope
from ai.composition.classify.classifier import classify_versions
from ai.contracts.composition import (
    CommStyle,
    Frequency,
    Interest,
    Sensitivity,
)
from ai.contracts.confirmations import (
    ClassificationCorrection,
    ConfirmationAction,
    ConfirmationKind,
    ConfirmationRequest,
    ConfirmationResponse,
    LabelCorrection,
)
from ai.db.repositories.inquiry_class_store import InquiryClassStore
from ai.db.store_factory import (
    build_inquiry_class_store,
    reset_default_inquiry_class_store,
)
from ai.runtime.errors import NotFound, SnapshotInvalid

logger = logging.getLogger(__name__)

from ai.api.routers.confirmations_openapi import (  # noqa: E402
    CONFIRMATIONS_OPERATION_ID,
    CONFIRMATIONS_SUMMARY,
    CONFIRMATIONS_TAG,
)

router = APIRouter()

_REQUIRED_HEADERS = ("X-Tenant-Id", "X-Request-Id")

#: 미지원 kind의 사유 코드 — `error_codes.md` 등재.
KIND_NOT_IMPLEMENTED = "kind_not_implemented"
#: 🔴 라벨 확정 키가 `guardian_ref:axis:value` 형식이 아니거나 축·값이 열거형 밖이다.
LABEL_KEY_INVALID: Final = "label_key_invalid"
#: 🔴 **정정값이 그 축의 열거형 밖이다**(2026-08-24 · 99 #232).
#: ⚠ 🔴 `LABEL_KEY_INVALID` 와 **갈랐다** — 그건 «키가 틀렸다» 이고 이건 «**정정값**이
#: 틀렸다» 다. BE 가 고쳐야 할 자리가 **다르다**(키는 우리가 준 값, 정정값은 강사 입력).
LABEL_VALUE_AXIS_MISMATCH: Final = "label_value_axis_mismatch"

#: `classification`이 받지 않는 action의 사유 코드.
ACTION_NOT_SUPPORTED = "action_not_supported"

#: `action=corrected`인데 정정할 축이 하나도 없다 — 조용히 "검토함"으로 굳히지 않는다.
CORRECTED_VALUE_MISSING = "corrected_value_missing"

#: `action=confirmed`인데 정정값이 실려 왔다 — 값을 조용히 버리지 않는다.
CORRECTED_VALUE_NOT_ALLOWED = "corrected_value_not_allowed"

#: 🔴 `/v1/classify`와 **같은 저장소를 봐야 한다** — 여기서 팩토리 결과를 모듈 전역에
#: 굳히면 두 라우터가 각자 인스턴스를 갖고, 정정이 전부 404가 된다(그 404는
#: "폴백이라 적재되지 않았다"와 구분되지 않아 유실이 조용하다). `classify.py`와 같은 규약.
_store: InquiryClassStore | None = None


def inquiry_class_store() -> InquiryClassStore:
    """이 라우터가 쓸 저장소 — 주입분이 있으면 그것, 없으면 공용."""
    return _store if _store is not None else build_inquiry_class_store()


def set_inquiry_class_store(store: InquiryClassStore) -> None:
    """저장소 주입 — 테스트·합성 루트의 seam(`set_counsel_provider` 선례)."""
    global _store
    _store = store


def reset_inquiry_class_store() -> None:
    """테스트 격리용 — 주입을 걷고 공용 저장소를 비운다."""
    global _store
    _store = None
    reset_default_inquiry_class_store()


def _format_validation_error(exc: ValidationError) -> list[dict[str, str]]:
    """필드 경로만 — 값은 싣지 않는다(04 §2.3)."""
    return [
        {"field": ".".join(str(part) for part in error["loc"]), "type": error["type"]}
        for error in exc.errors()
    ]



#: 🔴 라벨 확정 키 — `guardian_ref:axis:value`(2026-08-24 · №92).
#: ⚠ 🔴 **`guardian_ref` 안의 `:` 을 금지하지 않는다** — 축·값은 **우리 소유의 닫힌
#: 열거형**이라 `:` 을 안 담으므로 **오른쪽에서 둘만 떼면**(`rsplit(":", 2)`) 언제나
#: 복원된다(실측 8/24: `gd:11b0`·`a:b:c:d`·`::` 등 8종 **전부 복원 · 깨지는 입력 0**).
_LABEL_KEY_PARTS: Final = 3
_LABEL_AXIS_VALUES: Final = {
    "comm": CommStyle,
    "sensitivity": Sensitivity,
    "interest": Interest,
    "frequency": Frequency,
}


def _accept_label(
    confirmation: ConfirmationRequest, *, tenant_id: str
) -> dict[str, Any]:
    """🔴 `kind=label` 확정을 **받는다** — 🔴 **개별 확정을 영속하지 않는다**(№92 판정).

    남기는 것은 **집계 다섯 칸**뿐이다: `tenant · axis · 제안값 · 확정값 · action`.
    🔴 **`guardian_ref` 를 안 남긴다** — «새로 쌓이는 개인 데이터 0» 정책과 갈리지 않는다.
    🔴 **새 테이블·마이그레이션을 안 만든다** — `label_suggestion` 테이블은 `created_at` 이
    없어 **축출 근거가 없다**(`db/models.py`) ⇒ 그 두 이유를 되살리지 않는다.
    ⇒ 자리는 **구조화 로그**다. 🔴 그 다섯 칸이 **군집의 착수 근거**다(99 #190 계열).

    ⚠ 🔴 **`accepted: true` 는 «받았다» 이지 «영속했다» 가 아니다** — 04 §3.3 에 적었다.
    🔴 `action=rejected` 는 **라벨에서 성립한다**(classification 은 3축이 값을 반드시
    가져야 해서 «거절» 이 정의되지 않지만, 라벨은 «이 축을 안 쓴다» 가 뜻이 된다).
    """
    parts = confirmation.suggestion_id.rsplit(":", _LABEL_KEY_PARTS - 1)
    #: 🔴 **빈 `guardian_ref` 도 거절한다**(2026-08-24 · 99 #232).
    #: ⚠ 🔴 «파싱이 깨지기 때문» 이 **아니다** — 우리는 `guardian_ref` 를 안 싣고 안 읽는다.
    #: 🔴 이유는 «**빈 참조에 「받았다」를 주면 BE 가 그걸 유효한 확정으로 센다**» 다.
    if len(parts) != _LABEL_KEY_PARTS or not parts[0]:
        raise SnapshotInvalid(
            "라벨 확정 키 형식 위반",
            {
                "reason": LABEL_KEY_INVALID,
                "detail": "suggestion_id 는 guardian_ref:axis:value 다",
            },
        )
    _, axis, suggested = parts
    values = _LABEL_AXIS_VALUES.get(axis)
    if values is None or suggested not in {member.value for member in values}:
        raise SnapshotInvalid(
            "라벨 축·값이 열거형 밖",
            {"reason": LABEL_KEY_INVALID, "axis": axis, "detail": "4축 enum 만 받는다"},
        )
    #: 🔴 **kind 와 정정값의 짝이 안 맞으면 400** — label 인데 3축 정정값이 오는 경우다.
    if confirmation.corrected_value is not None and not isinstance(
        confirmation.corrected_value, LabelCorrection
    ):
        raise SnapshotInvalid(
            "정정값이 kind 와 안 맞는다",
            {
                "reason": CORRECTED_VALUE_NOT_ALLOWED,
                "detail": "kind=label 의 corrected_value 는 {value:…} 하나다",
            },
        )
    corrected = (
        confirmation.corrected_value.value
        if isinstance(confirmation.corrected_value, LabelCorrection)
        else None
    )
    #: 🔴 **정정값이 「그 축의」 값인지 본다**(2026-08-24 · 99 #232).
    #: ⚠ 🔴 **타입은 이걸 못 막는다** — `CommStyle | Sensitivity | Interest | Frequency` 는
    #: **네 축 아무거나**를 받고 축을 모른다. 막을 수 있는 자리는 **라우터뿐**이고
    #: `LabelCorrection.value` 의 문면도 «축과 짝이 맞는지는 라우터가 본다» 라 적었다 —
    #: 🔴 종전에는 **그 문면이 코드에 대해 거짓**이었다(`corrected` 를 꺼내 로그에 찍기만 했다).
    #: 🔴 그 다섯 칸이 **군집의 착수 근거**라, 축과 안 맞는 값이 섞이면 **로그에 남은 뒤엔
    #: 못 가른다.**
    if corrected is not None and corrected not in {member.value for member in values}:
        raise SnapshotInvalid(
            "정정값이 축과 안 맞는다",
            {
                "reason": LABEL_VALUE_AXIS_MISMATCH,
                "axis": axis,
                "detail": f"{axis} 축의 값이 아니다",
            },
        )
    if confirmation.action is ConfirmationAction.CORRECTED and corrected is None:
        raise SnapshotInvalid(
            "정정값 없음",
            {"reason": CORRECTED_VALUE_MISSING, "detail": "action=corrected 는 value 를 담는다"},
        )
    if confirmation.action is not ConfirmationAction.CORRECTED and corrected is not None:
        raise SnapshotInvalid(
            "확정·거절에 정정값이 실림",
            {"reason": CORRECTED_VALUE_NOT_ALLOWED, "detail": "값을 버리지 않는다"},
        )
    #: 🔴 **집계 다섯 칸** — 🔴 `guardian_ref` 는 **일부러 안 싣는다**(이 함수의 전부다).
    logger.info(
        "confirmations.label tenant=%s axis=%s suggested=%s confirmed=%s action=%s",
        tenant_id,
        axis,
        suggested,
        corrected if corrected is not None else suggested,
        confirmation.action.value,
    )
    return success_envelope(
        data=ConfirmationResponse(accepted=True).model_dump(mode="json"),
        execution_id=str(uuid.uuid4()),
        versions=classify_versions(),
    )



@router.post(
    "/v1/confirmations",
    operation_id=CONFIRMATIONS_OPERATION_ID,
    tags=[CONFIRMATIONS_TAG],
    summary=CONFIRMATIONS_SUMMARY,
)
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

    if confirmation.kind is ConfirmationKind.LABEL:
        return _accept_label(confirmation, tenant_id=tenant_id)

    if confirmation.kind is not ConfirmationKind.CLASSIFICATION:
        # 🔴 조용히 버리지 않는다 — 제안 생성기가 없어 확정할 대상이 없다.
        raise SnapshotInvalid(
            "미지원 kind",
            {
                "reason": KIND_NOT_IMPLEMENTED,
                "kind": confirmation.kind.value,
                #: ⚠ 🔴 **(8/24 정정 · 99 #221)** 종전 문면은 «**제안 생성기 미구현**» 이었는데
                #: 🔴 **거짓이 됐다** — 라벨 생성기는 №64 이후 있고 `POST /v1/labels/suggest`
                #: 는 **동작한다**(실측: BE 인 척 200 · 제안 1건). 미구현인 것은 **확정 경로**다.
                #: 🔴 BE 가 종전 문면을 읽으면 «AI 가 아직 제안을 못 만든다» 로 **오독한다.**
                "detail": (
                    "이 kind 의 확정은 v1 미구현 — 제안 API 자체는 동작한다"
                    "(예: POST /v1/labels/suggest). 확정을 받는 것은 v1 에서 "
                    "classification 뿐이다"
                ),
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

    #: 🔴 여기 오면 kind 는 classification 이다 — 라벨은 위에서 이미 돌려줬다.
    #: ⚠ 🔴 그래도 타입을 좁힌다: `corrected_value` 가 `LabelCorrection` 이면 **짝이 안 맞는다**.
    if confirmation.corrected_value is not None and not isinstance(
        confirmation.corrected_value, ClassificationCorrection
    ):
        raise SnapshotInvalid(
            "정정값이 kind 와 안 맞는다",
            {
                "reason": CORRECTED_VALUE_NOT_ALLOWED,
                "detail": "kind=classification 의 corrected_value 는 3축이다",
            },
        )
    corrections = (
        confirmation.corrected_value.as_axis_map()
        if confirmation.corrected_value is not None
        else {}
    )
    # 🔴 **action과 값의 조합이 어긋나면 거절한다.** 종전에는 둘 다 200 `accepted:true`였다.
    #   ⓐ `corrected` + 값 없음 → `corrections={}`로 떨어져 `reviewed_at`만 찍혔다.
    #      그 행은 규약 ①에 의해 **이후 재예측이 영구 차단**된다 — "검토함"으로 굳는다.
    #   ⓑ `confirmed` + 값 실림 → 값이 통째로 버려졌다.
    #   둘 다 BE는 "저장됐다"고 믿는다. 안 한 일을 한 척하지 않는다(kind·action 거절과 같은 결).
    if confirmation.action is ConfirmationAction.CORRECTED and not corrections:
        raise SnapshotInvalid(
            "정정값 없음",
            {
                "reason": CORRECTED_VALUE_MISSING,
                "action": confirmation.action.value,
                "detail": "action=corrected는 corrected_value에 축을 하나 이상 담아야 한다",
            },
        )
    if confirmation.action is ConfirmationAction.CONFIRMED and corrections:
        raise SnapshotInvalid(
            "확정에 정정값이 실림",
            {
                "reason": CORRECTED_VALUE_NOT_ALLOWED,
                "action": confirmation.action.value,
                "detail": "action=confirmed는 corrected_value를 비운다 — 값을 버리지 않는다",
            },
        )

    outcome = await inquiry_class_store().apply_confirmation(
        tenant_id=tenant_id,
        inquiry_ref=confirmation.suggestion_id,
        corrections=corrections,
    )
    if not outcome.found:
        # 대상 분류가 없다 — 폴백이었거나(적재 안 함) 분류를 부른 적이 없다.
        raise NotFound(
            "대상 분류 없음", {"inquiry_ref": confirmation.suggestion_id}
        )

    # ⚠ **실제 적용분을 찍는다.** 종전엔 필터 이전 값(`len(corrections)`)이라 규약 ②로
    # 걸러진 축까지 "적용"으로 남았다 — 로그가 저장 상태와 달랐다.
    logger.info(
        "confirmations.applied inquiry_ref=%s action=%s applied=%d cleared=%d unknown=%d",
        confirmation.suggestion_id,
        confirmation.action.value,
        len(outcome.applied),
        len(outcome.cleared),
        len(outcome.unknown),
    )
    return success_envelope(
        data=ConfirmationResponse(accepted=True).model_dump(mode="json"),
        execution_id=str(uuid.uuid4()),
        versions=classify_versions(),
    )



#: 이 라우터가 응답하는 경로 접두와 그 버전 세트 — `api/app.py`가 **실패 응답**에 쓴다(99 ㊓).
#: 🔴 접두를 여기 두는 이유: **경로를 바꾸는 사람과 접두를 고치는 사람이 같아야 한다.**
#:  `app.py`에 박으면 다른 파일이라 조용히 갈린다.
#: 🔴 **`classify_versions`를 빌려 쓴다 — 확인했고 의도다.**
#:  확정 회신은 **분류의 정정 경로**라 같은 capability이고(모듈 docstring: *"평가셋 루프를
#:  닫는다"*), 성공 응답(`:185`)도 이미 같은 것을 쓴다. `engine=classify-0.1`이 맞다.
#:  ⚠ `VersionSet`에는 엔드포인트를 가르는 필드가 없다 — 두 경로를 구분해야 할 일이
#:  생기면 그건 계약 변경이다(양자 · 99 ㊚).
VERSION_SCOPE: Final = RouterScope("/v1/confirmations", classify_versions)

__all__ = [
    "ACTION_NOT_SUPPORTED",
    "CORRECTED_VALUE_MISSING",
    "CORRECTED_VALUE_NOT_ALLOWED",
    "KIND_NOT_IMPLEMENTED",
    "inquiry_class_store",
    "reset_inquiry_class_store",
    "router",
    "set_inquiry_class_store",
]
