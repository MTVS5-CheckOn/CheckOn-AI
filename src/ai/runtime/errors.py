"""도메인 예외 ↔ HTTP 매핑 — error_codes.md §4 트리 구현.

소유: 박진희 (runtime). error_codes.md §1(코드)·§4(매핑 트리)가 정본이다.

불변식 4(CLAUDE.md): GateRejected는 5xx로 올리지 않는다 — 게이트 거부는 200 + status.
이 트리에는 그래서 GateRejected가 예외로 없다(정상 흐름은 반환값).

**detail 노출 정책도 이 파일에 있다**(`DomainException.expose_detail`). canonical adapter가
여기이므로 "응답에 상세를 실어도 되는가"도 예외 정의 옆에 둔다 — 핸들러에 isinstance 목록을
두면 새 예외가 생길 때 조용히 빠진다(04 §2.3 · error_codes §4 민감 detail 강제 제거).
"""

from __future__ import annotations


class DomainException(Exception):
    """도메인 예외 루트 — code·http_status로 응답 매핑된다 (error_codes §4)."""

    code: str = "INTERNAL"
    http_status: int = 500

    expose_detail: bool | None = None
    """detail을 실패 응답에 실을지. None이면 `http_status`에서 **파생**한다.

    4xx = 노출(클라이언트가 고칠 정보 — `INVALID_SCHEMA`의 필드 경로 등) ·
    5xx = 미노출(내부 상세·원문 조각이 담길 수 있다). 파생이 기본값인 이유는 새 5xx 예외를
    만드는 사람이 이 축을 깜빡해도 안전한 쪽(미노출)으로 떨어지게 하기 위함이다.
    파생을 뒤집어야 하면 하위 클래스에서 True/False를 명시한다.
    """

    def __init__(self, message: str, detail: object | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail

    @classmethod
    def detail_is_exposed(cls) -> bool:
        """이 예외의 detail을 응답에 실어도 되는가 — 명시값 우선, 없으면 4xx만 노출."""
        if cls.expose_detail is not None:
            return cls.expose_detail
        return cls.http_status < 500


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
    """마스킹 불확실 — 500 INTERNAL (원문 노출 위험, 상세 사유 응답 미포함).

    error_codes §4 트리: "RedactionUncertain → 500 INTERNAL (원문 노출 위험 —
    **상세 사유 응답에 미포함**)". detail에는 마스킹에 실패한 원문 조각이 담길 수 있으므로
    응답에서 빠진다(5xx 파생 — 별도 명시 없음). 로그에는 남는다(불변식 3은 경계 밖 전송을
    막는 규칙이며, 응답이 그 경계다).
    """

    code = "INTERNAL"
    http_status = 500


class LedgerWriteFailed(DomainException):
    """원장(AI_RUN·SIGNAL·FEATURE_WEEK) 적재 실패 — 500 INTERNAL (fail-closed).

    멱등 캐시(fail-open, IdempotencyConflict와 별개)와 달리 원장은 산출물의 근거·재현
    기록이므로 저장 실패를 삼키지 않는다 — 요청을 실패시켜 백엔드가 재시도하게 한다
    (D-② 확정). 사전에 없는 코드는 만들지 않고 INTERNAL로 매핑한다(error_codes §1)."""

    code = "INTERNAL"
    http_status = 500
