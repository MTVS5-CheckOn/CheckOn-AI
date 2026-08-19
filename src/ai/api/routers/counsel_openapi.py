"""`/v1/counsel/drafts` 3엔드포인트의 **OpenAPI 계약** — 문서 전용 (99 #99).

🔴 **여기 있는 것은 전부 「문서」다.** 런타임은 `counsel.py`가 그대로 하고, 이 모듈의
모델은 **요청·응답을 만들거나 다시 직렬화하지 않는다.**

**선례는 `detect_openapi.py`다** — 그 모듈 docstring이 함정 여섯을 이미 적어 뒀고
**초판 판단을 정정한 이력까지** 남겨 뒀다. 여기서는 그 결론을 그대로 따른다.

**왜 `Request`를 그대로 두는가** — 핸들러 인자를 `body: CounselDraftRequest`로 바꾸면
FastAPI 자동 검증이 붙어 **계약에 없는 422**가 생기고 현행 **400 `INVALID_SCHEMA`** 가
사라진다(04 §2.4 · `error_codes` §2.1). ⇒ 런타임은 손대지 않고 **스키마만** 잇는다.
⚠ `response_model=`도 안 쓴다 — 그건 **직렬화에 관여**해서 런타임이 바뀐다.
`responses={코드: {"model": …}}`는 문서 전용이다.

**요청 스키마는 완전히 인라인한다** — 남은 `$defs`·`$ref`가 **0건**이다.
`openapi_extra`로 넣은 스키마는 FastAPI가 `components`에 자동 등록하지 않으므로
`#/$defs/X`는 **문서 루트 기준**으로 해석되는데 루트에 `$defs`가 없다 ⇒ **끊긴 참조**다.
🔴 루트 `$defs` 주입·`components.schemas` 등록은 `api/app.py`(양자 승인)를 여는 길이라
이 회차에서 고르지 않았다.

⚠ **인라인 크기를 먼저 쟀다**(2026-08-19): `CounselDraftRequest` `$defs` 7개 · 174줄 ·
깊이 7 · **재귀 0**. `RefineRequest` `$defs` 1개 · 26줄. ⇒ 인라인으로 감당된다.

🔴 **예시(examples)를 손으로 적지 않는다.** 8/19에 손으로 적은 apidog 예시가 **21곳
틀렸다**(99 #98). `test_http_fixtures.py`의 첫 문단이 *"손으로 적은 예시를 주지 않는다"*
라고 적어 뒀고, 이 모듈은 **스키마만** 낸다 — 형태의 정본은 그 픽스처 15벌이다.

응답 쪽은 `responses={...: {"model": ...}}`로 등록되므로 `components`를 쓴다.
"""

from __future__ import annotations

from typing import Any, Final

from pydantic import BaseModel, ConfigDict, Field, create_model

#: 🔴 **인라인 도구를 재사용한다** — `detect_openapi.py`는 B 소유라 **고치지 않고 읽는다.**
#: 같은 판정(재귀면 조용히 진행하지 않고 실패)을 두 번 짜면 하나가 낡는다(99 #02).
from ai.api.routers.detect_openapi import _inline_defs
from ai.contracts.counsel import (
    CounselDraftJobView,
    CounselDraftRequest,
    RefineRequest,
    RefineResponse,
)
from ai.contracts.execution import VersionSet

#: 🔴 **Java generator의 메서드명이 된다** — 자동 생성이면
#: (`post_counsel_draft_v1_counsel_drafts_post`) 함수 이름을 바꾸는 순간 BE 클라이언트의
#: 메서드명이 따라 바뀐다. **지금 정하고 안 바꾼다 — 이 값은 계약이다.**
COUNSEL_CREATE_OPERATION_ID: Final = "createCounselDraft"
COUNSEL_GET_OPERATION_ID: Final = "getCounselDraft"
COUNSEL_REFINE_OPERATION_ID: Final = "refineCounselDraft"

COUNSEL_TAG: Final = "상담"

_STRICT: Final = ConfigDict(extra="forbid")


def _versions_model() -> type[BaseModel]:
    """`meta.versions` 모델을 **`VersionSet`에서 파생**한다(04 §2.2).

    🔴 **손으로 적으면 틀린다** — detect가 2026-08-12에 없는 키 셋을 적고 있는 키 둘을
    빠뜨렸다. 같은 실수를 반복하지 않으려고 정본에서 유도한다.
    ⚠ 그리고 8/19에 apidog 문서가 `pipeline_version` 처럼 **접미사를 붙여** 21곳이
    틀렸다(99 #98) — wire 키는 접미사를 **뗀** 이름이다(`envelope.py`의
    `name.removesuffix("_version")`).
    """
    fields: dict[str, Any] = {}
    for name, info in VersionSet.model_fields.items():
        key = name.removesuffix("_version")
        #: ⚠ `schema`는 `BaseModel`의 deprecated 메서드와 겹친다 — 필드명은 피하고
        #:   **alias로 wire 키**를 낸다.
        field_name = f"{key}_" if hasattr(BaseModel, key) else key
        annotation = str if info.is_required() else str | None
        fields[field_name] = (
            annotation,
            Field(alias=key, description=f"`VersionSet.{name}`"),
        )
    return create_model("CounselEnvelopeVersions", __config__=_STRICT, **fields)


CounselEnvelopeVersions = _versions_model()


class CounselEnvelopeMeta(BaseModel):
    model_config = _STRICT

    execution_id: str | None = Field(
        default=None,
        description=(
            "AI_RUN 실행 키. 🔴 **잡을 만들지 않는 성공 경로**"
            "(`template_only`·근거 0건)는 원장에 행이 없어 **상관 ID**가 실린다 — "
            "그 값으로는 원장을 못 찾는다(04 §2.2)."
        ),
    )
    versions: CounselEnvelopeVersions  # type: ignore[valid-type]  # 동적 생성 모델


class CounselErrorBody(BaseModel):
    """`error` — 🔴 **내부 상세는 싣지 않는다.**"""

    model_config = _STRICT

    code: str = Field(description="`INVALID_SCHEMA` · `IDEMPOTENCY_CONFLICT` · `NOT_FOUND`")
    message: str
    detail: Any | None = None


class CounselAcceptedData(BaseModel):
    """🔴 **202의 `data`는 정확히 2키다** — 초안 실물은 절대 안 실린다.

    apidog 문서 §0-A가 BE에 명시적으로 경고한 계약이다 —
    *"묶으면 202에 없는 `result`를 BE가 기다립니다."*
    """

    model_config = _STRICT

    job_id: str
    status: str = Field(
        description="잡 phase 7종(`error_codes` §2.5). 종단이면 바로 GET한다."
    )


class CounselAcceptedEnvelope(BaseModel):
    """202 — 기동 접수 **또는 멱등 재응답**(같은 키 + 같은 바디)."""

    model_config = _STRICT

    data: CounselAcceptedData
    error: None = None
    meta: CounselEnvelopeMeta


class CounselDraftEnvelope(BaseModel):
    """200 — 🔴 **초안 실물이 나오는 자리는 여기 하나다.**"""

    model_config = _STRICT

    data: CounselDraftJobView
    error: None = None
    meta: CounselEnvelopeMeta


class CounselRefineEnvelope(BaseModel):
    """200 — 다듬기 1턴. ⚠ **게이트 거부는 에러가 아니다**(`applied=false` + 200)."""

    model_config = _STRICT

    data: RefineResponse
    error: None = None
    meta: CounselEnvelopeMeta


class CounselErrorEnvelope(BaseModel):
    """4xx — 🔴 **실패에도 `meta.versions`가 실린다**(04 §2.2)."""

    model_config = _STRICT

    data: None = None
    error: CounselErrorBody
    meta: CounselEnvelopeMeta


#: 🔴 헤더 셋 — ⚠ **인증 수단이 아니다.** 런타임 인자로 선언하면(`Header(...)`) FastAPI가
#: 누락 시 **422**를 내고 현행 400 `INVALID_SCHEMA`가 사라진다 ⇒ 여기서 문서로만 낸다.
_TENANT_HEADER: Final[dict[str, Any]] = {
    "name": "X-Tenant-Id",
    "in": "header",
    "required": True,
    "schema": {"type": "string"},
    "description": "강사·기관 tenant alias. 전 테이블 격리 술어이며 실명이 아니다.",
}
_REQUEST_ID_HEADER: Final[dict[str, Any]] = {
    "name": "X-Request-Id",
    "in": "header",
    "required": True,
    "schema": {"type": "string"},
    "description": "HTTP **시도마다 새 값**. 재전송하면 달라지는 추적 ID다.",
}
_IDEMPOTENCY_HEADER: Final[dict[str, Any]] = {
    "name": "Idempotency-Key",
    "in": "header",
    "required": True,
    "schema": {"type": "string"},
    "description": (
        "문의 1건 = 초안 1개이므로 `inquiry_ref` 가 자연스러운 키다(04 §3.4). "
        "🔴 **응답이 오기 전에 타임아웃이 나면 같은 값으로 다시 보내라** — "
        "같은 `job_id`를 돌려받는다(잡이 중복 생성되지 않는다 · 99 #76·#85). "
        "⚠ `job_id`를 직접 계산하지 마라 — 응답에 실린 값을 그대로 쓴다."
    ),
}


def _inlined(model: type[BaseModel]) -> dict[str, Any]:
    """모델의 **완전 인라인** JSON Schema — 남은 `$ref` 0건.

    🔴 `$defs` 동봉으로는 부족하다 — `#/$defs/X`는 **문서 루트** 기준이라 끊긴다.
    재귀가 나오면 `_inline_defs`가 `RecursiveSchemaError`로 **실패**시킨다.
    """
    schema = model.model_json_schema()
    defs = schema.pop("$defs", {})
    inlined: dict[str, Any] = _inline_defs(schema, defs)
    return inlined


COUNSEL_CREATE_RESPONSES: Final[dict[int | str, dict[str, Any]]] = {
    202: {
        "model": CounselAcceptedEnvelope,
        "description": (
            "접수. 🔴 **`status`가 이미 종단이면 폴링하지 말고 바로 GET 1회.** "
            "v1은 같은 요청 안에서 워커를 동기로 돌리므로 대부분 `succeeded`다."
        ),
    },
    400: {
        "model": CounselErrorEnvelope,
        "description": "필수 헤더 누락 · JSON 파싱 실패 · 스키마 위반 · 라벨 미지값",
    },
    409: {
        "model": CounselErrorEnvelope,
        "description": "같은 `Idempotency-Key`에 **다른 바디** (`IDEMPOTENCY_CONFLICT`)",
    },
}

COUNSEL_GET_RESPONSES: Final[dict[int | str, dict[str, Any]]] = {
    200: {
        "model": CounselDraftEnvelope,
        "description": (
            "초안 실물. ⚠ **잡 성공 ≠ 초안 존재** — `status=\"succeeded\"` + "
            "`result.draft_status=\"rejected_insufficient\"`는 **정상 조합**이고 "
            "에러 UI로 그리면 안 된다."
        ),
    },
    404: {"model": CounselErrorEnvelope, "description": "`job_id` 부재 (`NOT_FOUND`)"},
}

COUNSEL_REFINE_RESPONSES: Final[dict[int | str, dict[str, Any]]] = {
    200: {
        "model": CounselRefineEnvelope,
        "description": (
            "다듬기 1턴. ⚠ **게이트 거부도 200이다** — `applied=false` + "
            "`blocked_reason`. 강사 지시가 게이트를 이기지 못한다."
        ),
    },
    400: {"model": CounselErrorEnvelope, "description": "필수 헤더 누락 · 스키마 위반"},
    404: {"model": CounselErrorEnvelope, "description": "`job_id` 부재"},
    409: {
        "model": CounselErrorEnvelope,
        "description": "같은 `Idempotency-Key`에 다른 바디 — 🔴 스코프는 **job_id별**이다",
    },
}


def counsel_create_openapi_extra() -> dict[str, Any]:
    """`@router.post("/v1/counsel/drafts", openapi_extra=...)`에 넣는 조각."""
    return {
        "parameters": [
            dict(_TENANT_HEADER),
            dict(_REQUEST_ID_HEADER),
            dict(_IDEMPOTENCY_HEADER),
        ],
        "requestBody": {
            "required": True,
            "content": {"application/json": {"schema": _inlined(CounselDraftRequest)}},
        },
    }


def counsel_get_openapi_extra() -> dict[str, Any]:
    """GET — 바디가 없고 **헤더 하나**다(`Idempotency-Key` 없음 · 조회는 멱등)."""
    return {"parameters": [dict(_TENANT_HEADER)]}


def counsel_refine_openapi_extra() -> dict[str, Any]:
    """refine — 🔴 `Idempotency-Key`가 **필수**다(PR #277에서 새로 필수가 됐다)."""
    return {
        "parameters": [dict(_TENANT_HEADER), dict(_IDEMPOTENCY_HEADER)],
        "requestBody": {
            "required": True,
            "content": {"application/json": {"schema": _inlined(RefineRequest)}},
        },
    }


__all__ = [
    "COUNSEL_CREATE_OPERATION_ID",
    "COUNSEL_CREATE_RESPONSES",
    "COUNSEL_GET_OPERATION_ID",
    "COUNSEL_GET_RESPONSES",
    "COUNSEL_REFINE_OPERATION_ID",
    "COUNSEL_REFINE_RESPONSES",
    "COUNSEL_TAG",
    "counsel_create_openapi_extra",
    "counsel_get_openapi_extra",
    "counsel_refine_openapi_extra",
]
