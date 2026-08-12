"""`/v1/detect`의 **OpenAPI 계약** — 문서 전용 (지시서 76).

🔴 **여기 있는 것은 전부 「문서」다.** 런타임은 `detect.py`가 그대로 하고, 이 모듈의 모델은
**요청·응답을 만들거나 다시 직렬화하지 않는다.** 필드를 지우지도, 순서를 바꾸지도 않는다.

**왜 `Request`를 그대로 두는가** — handler 인자를 `detect_request: DetectRequest`로 바꾸면
FastAPI 자동 검증이 붙어 **계약에 없는 422**가 생기고 현행 **400 `INVALID_SCHEMA`** 가
사라진다(04 §2.4 · `error_codes` §2.1). 그래서 런타임은 손대지 않고 **스키마만**
`openapi_extra`로 잇는다.

**요청 스키마는 완전히 인라인한다** — 남은 `$defs`·`$ref`가 **0건**이다.

  ① `openapi_extra`로 넣은 스키마는 FastAPI가 **`components`에 자동 등록하지 않는다.**
  ② 그래서 Pydantic이 낸 `#/$defs/X`는 **문서 루트 기준**으로 해석되는데 루트에 `$defs`가
     없다 ⇒ **끊긴 참조**가 된다.
  ③ 루트 `$defs` 주입이나 `components.schemas` 등록은 `api/app.py`(양자 승인)를 여는
     길이라 이 회차에서 고르지 않았다.
  ④ 재귀 모델이 생기면 인라인이 무한히 펼쳐진다 — 조용히 진행하지 않고
     `RecursiveSchemaError`로 **실패**시킨다(그때가 ③의 승인 축이 필요한 시점이다).

⚠ **초판 판단 정정(2026-08-12)** — 처음에는 *"`$defs`를 동봉하면 OpenAPI 3.1이라 유효하다"*
고 적었다. **문서 루트 실해석으로 반증됐다**: 끊긴 참조 **19건**이었고, 당시 검사는
`requestBody.schema` 안만 봐서 green이었다. 지금 문면이 현재 구현이다.

응답 쪽은 `responses={...: {"model": ...}}`로 등록되므로 `components`를 쓴다.
"""

from __future__ import annotations

from typing import Any, Final

from pydantic import BaseModel, ConfigDict, Field, create_model

from ai.contracts.detection import DetectRequest, DetectResponse
from ai.contracts.execution import VersionSet

#: 🔴 **Java generator의 메서드명이 된다** — 자동 생성(`post_detect_v1_detect_post`)이면
#: 함수 이름을 바꾸는 순간 BE 클라이언트의 메서드명이 따라 바뀐다.
DETECT_OPERATION_ID: Final = "detectRiskSignals"

DETECT_TAG: Final = "위험신호"

DETECT_SUMMARY: Final = "위험신호를 탐지한다"

#: 정본 샘플 — 인라인 예시는 이 파일의 **부분집합**이고 전문을 복제하지 않는다.
_REQUEST_SAMPLE: Final = "docs/part_a/examples/detect_demo_request.json"
_RESPONSE_SAMPLE: Final = "docs/part_a/examples/detect_demo_response.json"

DETECT_DESCRIPTION: Final = f"""\
백엔드가 전달한 **alias 학습 스냅숏**과 정본 집계 근거로 **결정론** 위험신호를 계산하고,
**근거가 있는 신호만** 반환한다. 브리핑 문장화가 실패하면 템플릿으로 폴백하며 **판정값은
LLM이 정하지 않는다.**

**호출 주체** — 백엔드 Kafka Consumer가 이 엔드포인트를 **HTTP로** 호출한다.
AI 서버는 Kafka를 직접 소비하지 않는다.

**입력에서 주의할 둘**

- `snapshot_meta.snapshot_hash`는 **백엔드가 선언한 값**이다. AI는 이 값을 **재계산해
  검증하지 않고** 멱등 판정에만 쓴다(같은 `Idempotency-Key` + 다른 hash → **409**).
  canonical 계산 규약은 `04_api_contract.md` 부록 A이고 AI 쪽 참조 구현은
  `ai/detection/canonical.py`다.
- `detection_evidence`는 **선택**이다. 부재형 규칙(R2 미제출·R3 학습 공백·R5 복귀)은
  *"기록이 없다"* 를 주장하므로 **그 사실을 증명하는 정본 집계 레코드**가 있어야 발화한다.
  안 보내면 해당 규칙은 발화하지 않고 `stats.rules_skipped`에
  `authoritative_evidence_missing`으로 남는다(조용한 미판정 없음).
  R2가 거슬러 올라가는 상한은 **rolling 10주**다.

**출력** — `data`는 `DetectResponse`이고 신호마다 `evidence`가 **1건 이상**이다
(근거 없는 산출물은 생성 단계에서 실패한다).

**전체 정본 샘플** — 10명 스냅숏과 그 응답은 아래 두 파일이다(문서 예시는 축약본):

- `{_REQUEST_SAMPLE}`
- `{_RESPONSE_SAMPLE}`
"""

#: 🔴 **헤더 셋 — 정확히 이만큼**이다. ⚠ 인증 수단이 아니다(그렇게 읽히면 BE가 비밀 관리
#: 경로에 넣는다). 문면은 #222 실통신 기록의 BE 체크리스트와 같은 축이다.
DETECT_HEADERS: Final[tuple[dict[str, Any], ...]] = (
    {
        "name": "X-Tenant-Id",
        "in": "header",
        "required": True,
        "schema": {"type": "string"},
        "description": "강사·기관 tenant alias. 전 테이블 격리 술어이며 실명이 아니다.",
        "example": "t_demo",
    },
    {
        "name": "X-Request-Id",
        "in": "header",
        "required": True,
        "schema": {"type": "string"},
        "description": "HTTP **시도마다 새 값**. 재전송하면 달라지는 추적 ID다.",
        "example": "rq-detect-1",
    },
    {
        "name": "Idempotency-Key",
        "in": "header",
        "required": True,
        "schema": {"type": "string"},
        "description": (
            "같은 **Kafka 이벤트**를 재전달하면 **같은 값**을 보낸다. "
            "같은 키 + 같은 `snapshot_hash` → 기존 결과 200 재반환, "
            "다른 `snapshot_hash` → 409."
        ),
        "example": "t_demo:detect:2026-07-20",
    },
)


# ───────────────────────── 응답 envelope (문서 전용) ─────────────────────────


def _versions_model() -> type[BaseModel]:
    """`meta.versions` 모델을 **`VersionSet`에서 파생**한다(04 §2.2).

    🔴 **손으로 적었더니 틀렸다**(2026-08-12 실측): 없는 키 셋(`threshold_config`·
    `lexicon`·`tone_map`)을 적고 있는 키 둘(`verify_config`·`difficulty_calib`)을 빠뜨렸다.
    문서가 **wire와 다른 계약**을 말하고 있었던 셈이다.

    ⇒ 이름·필수 여부를 정본에서 유도한다. ⚠ **응답은 nullable이어도 키를 생략하지 않는다**
    (`envelope.versions_dict()`가 전 필드를 채운다) — 그래서 전부 `required`이고 값만
    nullable이다.
    """
    fields: dict[str, Any] = {}
    for name, info in VersionSet.model_fields.items():
        key = name.removesuffix("_version")
        #: ⚠ `schema`는 `BaseModel`의 deprecated 메서드와 이름이 겹친다 — 필드명은
        #:   피하고 **alias로 wire 키를 낸다**(응답 키는 `schema`가 정본이다).
        field_name = f"{key}_" if hasattr(BaseModel, key) else key
        annotation = str if info.is_required() else str | None
        fields[field_name] = (
            annotation,
            Field(alias=key, description=f"`VersionSet.{name}`"),
        )
    return create_model("DetectEnvelopeVersions", __config__=_STRICT, **fields)


def documented_version_keys() -> frozenset[str]:
    """문서 모델이 말하는 **wire 키 집합** — 내부 필드명이 아니라 alias다."""
    return frozenset(
        info.alias or name
        for name, info in DetectEnvelopeVersions.model_fields.items()
    )


#: 🔴 `extra="forbid"` — 문서에 없는 키가 응답에 있으면 **문서가 낡은 것**이다.
#: ⚠ `populate_by_name=False` — 응답은 **alias(wire 키)로만** 검증한다.
_STRICT: Final = ConfigDict(extra="forbid")

DetectEnvelopeVersions = _versions_model()


class DetectEnvelopeMeta(BaseModel):
    model_config = _STRICT

    execution_id: str | None = Field(
        default=None,
        description="AI_RUN 실행 키. 실행 전 오류(헤더 누락 등)면 null이다.",
    )
    versions: DetectEnvelopeVersions  # type: ignore[valid-type]  # 동적 생성 모델


class DetectErrorBody(BaseModel):
    """`error` — 🔴 **내부 상세는 싣지 않는다**(필드 경로 수준까지)."""

    model_config = ConfigDict(extra="forbid")

    code: str = Field(
        description="`INVALID_SCHEMA` · `IDEMPOTENCY_CONFLICT` · `LEDGER_WRITE_FAILED` 등"
    )
    message: str
    detail: Any | None = None


class DetectSuccessEnvelope(BaseModel):
    """200 — 정상 결과 **또는 멱등 재응답**(같은 키 + 같은 hash)."""

    model_config = ConfigDict(extra="forbid")

    data: DetectResponse
    error: None = None
    meta: DetectEnvelopeMeta


class DetectErrorEnvelope(BaseModel):
    """4xx·5xx — 🔴 **실패에도 `meta.versions`가 실린다**(04 §2.2 A판정 7/22).

    ⚠ **`meta`에 `None`을 허용하지 않는다.** `error_envelope()`는 호출자가 `versions`를
    안 넘기면 `meta=None`을 내지만, **`/v1/detect`는 400·409·500 전부 버전을 넘긴다**
    (실측). 문서가 `None`을 열어 두면 BE가 **없어도 되는 값**으로 읽는다.
    """

    model_config = _STRICT

    data: None = None
    error: DetectErrorBody
    meta: DetectEnvelopeMeta


#: 상태별 문면 — `error_codes` §2.1 매핑에서 온다.
DETECT_RESPONSES: Final[dict[int | str, dict[str, Any]]] = {
    200: {
        "model": DetectSuccessEnvelope,
        "description": "위험신호 정상 결과 또는 멱등 재응답(같은 키 + 같은 snapshot_hash)",
    },
    400: {
        "model": DetectErrorEnvelope,
        "description": (
            "필수 헤더 누락 · JSON 파싱 실패 · 요청 스키마 위반 · evidence 계약 위반 "
            "(`INVALID_SCHEMA`)"
        ),
    },
    409: {
        "model": DetectErrorEnvelope,
        "description": (
            "같은 `Idempotency-Key`에 **다른 `snapshot_hash`** (`IDEMPOTENCY_CONFLICT`)"
        ),
    },
    500: {
        "model": DetectErrorEnvelope,
        "description": (
            "감지 원장(AI_RUN·SIGNAL·FEATURE_WEEK) 적재 실패 또는 미분류 내부 오류. "
            "⚠ 원장은 **fail-closed**다 — 근거·재현 기록이라 삼키지 않는다."
        ),
    },
}


# ───────────────────────── 요청 body (문서 전용) ─────────────────────────


class RecursiveSchemaError(RuntimeError):
    """인라인할 수 없는 **재귀 모델**을 만났다 — 조용히 진행하지 않는다.

    🔴 재귀가 생기면 인라인은 무한히 펼쳐진다. 그때는 `components.schemas` 등록이
    필요하고 그건 `api/app.py`(양자 승인) 축이라 **여기서 판정하지 않고 실패시킨다.**
    """


def _inline_defs(node: Any, defs: dict[str, Any], seen: tuple[str, ...] = ()) -> Any:  # noqa: ANN401
    """`#/$defs/X` 참조를 **정의 본문으로 치환**한다(순수 · 재귀는 예외).

    🔴 **문자열 치환으로 `$ref`를 지우지 않는다** — 그건 참조가 가리키던 제약을 통째로
    잃는 것이다. 여기서는 정의를 **그 자리에 펼친다.**
    """
    if isinstance(node, list):
        return [_inline_defs(item, defs, seen) for item in node]
    if not isinstance(node, dict):
        return node

    ref = node.get("$ref")
    if isinstance(ref, str):
        if not ref.startswith("#/$defs/"):
            raise RecursiveSchemaError(f"예상 못 한 참조 형태다: {ref}")
        name = ref.removeprefix("#/$defs/")
        if name in seen:
            raise RecursiveSchemaError(
                f"재귀 모델이라 인라인할 수 없다: {' → '.join([*seen, name])}"
            )
        if name not in defs:
            raise RecursiveSchemaError(f"정의가 없는 참조다: {ref}")
        expanded = _inline_defs(defs[name], defs, (*seen, name))
        #: ⚠ `$ref` 옆에 있던 형제 키(`description` 등)를 잃지 않는다.
        siblings = {key: value for key, value in node.items() if key != "$ref"}
        return {**expanded, **_inline_defs(siblings, defs, seen)} if siblings else expanded

    return {key: _inline_defs(value, defs, seen) for key, value in node.items()}


def _request_schema() -> dict[str, Any]:
    """`DetectRequest`의 **완전 인라인** JSON Schema — 남은 `$ref` 0건.

    🔴 **`$defs`를 동봉하는 것으로는 부족하다**(2026-08-12 실측). `#/$defs/X`는 그 스키마가
    아니라 **OpenAPI 문서 루트**를 기준으로 해석되는데, 문서 루트에는 `$defs`가 없다
    ⇒ **끊긴 참조 19건**이었다. Swagger·generator 양쪽에서 터진다.

    ⚠ 루트에 `$defs`를 심거나 `components.schemas`에 등록하려면 `api/app.py`를 열어야
    하고 그건 **양자 승인** 축이다 — 이 회차는 **인라인**으로 닫는다.
    """
    schema = DetectRequest.model_json_schema()
    defs = schema.pop("$defs", {})
    inlined: dict[str, Any] = _inline_defs(schema, defs)
    return inlined


#: 🔴 **축약 예시** — 10명 전체 fixture를 여기 복제하지 않는다(정본은 위 두 파일).
#: ⚠ alias만 쓴다. 실명·연락처·실 키가 없다(불변식 3 — 문서도 경계 밖이다).
DETECT_REQUEST_EXAMPLE: Final[dict[str, Any]] = {
    "snapshot_meta": {
        "week_start": "2026-07-20",
        "snapshot_hash": "sha256:example-1-2026-07-20",
        "term_context": "normal",
        "classes": [{"class_ref": "cl_a1"}],
    },
    "students": [
        {
            "student_ref": "st_01",
            "class_ref": "cl_a1",
            "enrolled_weeks": 14,
            "status": "enrolled",
            "consent": "granted",
        }
    ],
    "learning_events": [
        {
            "record_id": "le_1",
            "student_ref": "st_01",
            "type": "solve",
            "occurred_at": "2026-07-21T19:20:00+09:00",
            "correct": True,
            "duration_sec": 180,
            "passage_word_count": 800,
            "area_tag": "reading",
            "type_tag": "infer",
            "item_format": "mcq",
            "source": "trackB",
        }
    ],
    "alert_context": [],
}


def detect_openapi_extra() -> dict[str, Any]:
    """`@router.post(..., openapi_extra=...)`에 그대로 넣는 조각."""
    return {
        #: ⚠ **헤더도 여기서 낸다** — 런타임 인자로 선언하면(`Header(...)`) FastAPI가
        #:   누락 시 **422**를 내고, 현행 400 `INVALID_SCHEMA`가 사라진다.
        "parameters": [dict(header) for header in DETECT_HEADERS],
        "requestBody": {
            "required": True,
            "description": (
                "alias 학습 스냅숏. `snapshot_hash`는 백엔드 선언값이고 "
                "`detection_evidence`는 선택이다."
            ),
            "content": {
                "application/json": {
                    "schema": _request_schema(),
                    "example": DETECT_REQUEST_EXAMPLE,
                }
            },
        }
    }


__all__ = [
    "DETECT_DESCRIPTION",
    "DETECT_HEADERS",
    "DETECT_OPERATION_ID",
    "DETECT_REQUEST_EXAMPLE",
    "DETECT_RESPONSES",
    "DETECT_SUMMARY",
    "DETECT_TAG",
    "DetectEnvelopeVersions",
    "documented_version_keys",
    "DetectErrorBody",
    "DetectErrorEnvelope",
    "DetectSuccessEnvelope",
    "detect_openapi_extra",
]
