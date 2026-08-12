"""`/v1/detect`의 **OpenAPI 계약** — 문서 전용 (지시서 76).

🔴 **여기 있는 것은 전부 「문서」다.** 런타임은 `detect.py`가 그대로 하고, 이 모듈의 모델은
**요청·응답을 만들거나 다시 직렬화하지 않는다.** 필드를 지우지도, 순서를 바꾸지도 않는다.

**왜 `Request`를 그대로 두는가** — handler 인자를 `detect_request: DetectRequest`로 바꾸면
FastAPI 자동 검증이 붙어 **계약에 없는 422**가 생기고 현행 **400 `INVALID_SCHEMA`** 가
사라진다(04 §2.4 · `error_codes` §2.1). 그래서 런타임은 손대지 않고 **스키마만**
`openapi_extra`로 잇는다.

⚠ **요청 스키마는 자기 완결이다**(`$defs` 동봉) — `openapi_extra`로 넣은 스키마는 FastAPI가
`components`에 등록하지 않기 때문이다. OpenAPI 3.1은 JSON Schema 2020-12라 그게 유효하다.
응답 쪽은 `responses={...: {"model": ...}}`로 등록되므로 `components`를 쓴다.
"""

from __future__ import annotations

from typing import Any, Final

from pydantic import BaseModel, ConfigDict, Field

from ai.contracts.detection import DetectRequest, DetectResponse

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


class DetectEnvelopeVersions(BaseModel):
    """`meta.versions` — `VersionSet` 필드에서 `_version` 접미사를 뗀 이름이다(04 §2.2)."""

    model_config = ConfigDict(extra="forbid")

    pipeline: str | None = None
    engine: str | None = None
    schema_: str | None = Field(default=None, alias="schema")
    contract: str | None = None
    prompt: str | None = None
    threshold_config: str | None = None
    taxonomy: str | None = None
    graph: str | None = None
    lexicon: str | None = None
    tone_map: str | None = None


class DetectEnvelopeMeta(BaseModel):
    model_config = ConfigDict(extra="forbid")

    execution_id: str | None = Field(
        default=None,
        description="AI_RUN 실행 키. 실행 전 오류(헤더 누락 등)면 null이다.",
    )
    versions: DetectEnvelopeVersions


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
    """4xx·5xx — 🔴 **실패에도 `meta.versions`가 실린다**(04 §2.2 A판정 7/22)."""

    model_config = ConfigDict(extra="forbid")

    data: None = None
    error: DetectErrorBody
    meta: DetectEnvelopeMeta | None = None


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


def _request_schema() -> dict[str, Any]:
    """`DetectRequest`의 **자기 완결** JSON Schema.

    ⚠ `openapi_extra`는 FastAPI의 components 수집을 안 거치므로 `$defs`를 동봉한다 —
    OpenAPI 3.1(JSON Schema 2020-12)에서 유효하다.
    """
    return DetectRequest.model_json_schema()


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
    "DetectErrorBody",
    "DetectErrorEnvelope",
    "DetectSuccessEnvelope",
    "detect_openapi_extra",
]
