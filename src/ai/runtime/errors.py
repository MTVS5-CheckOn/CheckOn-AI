"""도메인 예외 ↔ HTTP 매핑 — error_codes.md §4 트리 구현.

소유: 박진희 (runtime). error_codes.md §1(코드)·§4(매핑 트리)가 정본이다.

불변식 4(CLAUDE.md): GateRejected는 5xx로 올리지 않는다 — 게이트 거부는 200 + status.
이 트리에는 그래서 GateRejected가 예외로 없다(정상 흐름은 반환값).
"""

from __future__ import annotations


class DomainException(Exception):
    """도메인 예외 루트 — code·http_status로 응답 매핑된다 (error_codes §4)."""

    code: str = "INTERNAL"
    http_status: int = 500

    def __init__(self, message: str, detail: object | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail


class SnapshotInvalid(DomainException):
    """요청 스키마 위반 — 400 INVALID_SCHEMA (error_codes §1)."""

    code = "INVALID_SCHEMA"
    http_status = 400


class IdempotencyConflict(DomainException):
    """같은 Idempotency-Key + 다른 바디 — 409 IDEMPOTENCY_CONFLICT (04 §2.3)."""

    code = "IDEMPOTENCY_CONFLICT"
    http_status = 409


class NotFound(DomainException):
    """job_id 등 부재·불투명 참조(존재 은닉) — 404 NOT_FOUND (error_codes §1)."""

    code = "NOT_FOUND"
    http_status = 404


class ConsentAbsent(DomainException):
    """대상 학생 동의 없음 — 422 CONSENT_ABSENT."""

    code = "CONSENT_ABSENT"
    http_status = 422


class LlmUnavailable(DomainException):
    """LLM 벤더 장애 — 503 LLM_UPSTREAM_DOWN.

    감지 경로에는 해당 없음(결정론) — 트리 구조를 §4대로 갖추기 위해 둔다.
    """

    code = "LLM_UPSTREAM_DOWN"
    http_status = 503


class RedactionUncertain(DomainException):
    """마스킹 불확실 — 500 INTERNAL (원문 노출 위험, 상세 사유 응답 미포함)."""

    code = "INTERNAL"
    http_status = 500


class LedgerWriteFailed(DomainException):
    """원장(AI_RUN·SIGNAL·FEATURE_WEEK) 적재 실패 — 500 INTERNAL (fail-closed).

    멱등 캐시(fail-open, IdempotencyConflict와 별개)와 달리 원장은 산출물의 근거·재현
    기록이므로 저장 실패를 삼키지 않는다 — 요청을 실패시켜 백엔드가 재시도하게 한다
    (D-② 확정). 사전에 없는 코드는 만들지 않고 INTERNAL로 매핑한다(error_codes §1)."""

    code = "INTERNAL"
    http_status = 500
