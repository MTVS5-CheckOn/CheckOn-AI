"""도메인 예외 ↔ HTTP 매핑 — error_codes.md §4 트리 구현.

소유: 박진희 (runtime). error_codes.md §1(코드)·§4(매핑 트리)가 정본이다.

불변식 4(CLAUDE.md): GateRejected는 5xx로 올리지 않는다 — 게이트 거부는 200 + status.
이 트리에는 그래서 GateRejected가 예외로 없다(정상 흐름은 반환값).

**detail 노출 정책도 이 파일에 있다**(`DomainException.expose_detail`). canonical adapter가
여기이므로 "응답에 상세를 실어도 되는가"도 예외 정의 옆에 둔다 — 핸들러에 isinstance 목록을
두면 새 예외가 생길 때 조용히 빠진다(04 §2.3 · error_codes §4 민감 detail 강제 제거).
"""

from __future__ import annotations

import logging

from ai.contracts.llm import LlmError, LlmTimeout, LlmUnavailable, RedactionBlocked

logger = logging.getLogger(__name__)


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

    #: ⚠ **도달 경로 0** — 동의 확인은 백엔드가 1차로 선차단한다(§1 [백엔드 확인 대기]).
    #: 지우지 않는 이유: §1 사전에 있는 코드라 지우면 사전과 코드가 갈린다.
    #: 404/422 경계는 회신 대기 중이다(99 등재).


class LlmUpstreamDown(DomainException):
    """LLM 벤더 장애 — 503 LLM_UPSTREAM_DOWN. **재시도 예산은 이미 소진됐다.**

    🔴 **이름을 갈랐다(8/6).** 종전 이름은 `LlmUnavailable`이었는데 `contracts/llm.py`에
    같은 이름이 있었다. §4 트리의 A 판정(7/22)이 *"contracts.llm 예외(LlmUnavailable·
    LlmTimeout)는 runtime adapter가 받아 HTTP로 변환하는 **단일 경계**"* 라고 못박았는데,
    **받는 쪽과 받히는 쪽이 같은 이름이면 그게 경계가 아니다.** `LlmUpstream*` 접두가
    "여기는 HTTP 매핑 계층"을 이름으로 말한다.
    ⚠ 종전 이름은 raise 0곳·import 0곳이라 삭제 파급이 0이었다(8/6 전수 실측).
    """

    code = "LLM_UPSTREAM_DOWN"
    http_status = 503


class LlmUpstreamTimeout(DomainException):
    """LLM 응답 시간 초과 — 504 TIMEOUT. **재시도 예산은 이미 소진됐다.**

    게이트웨이가 `reraise=True`로 재시도를 소진한 뒤 원 예외를 그대로 올리므로
    (`llm/gateway.py:211·214`), 이 경계가 받는 시점에는 예산이 끝나 있다 —
    "아무도 못 바꾸고 기다릴 뿐"의 조건이 구조적으로 충족된다.
    """

    code = "TIMEOUT"
    http_status = 504


class RedactionUncertain(DomainException):
    """마스킹 불확실 — 500 INTERNAL (원문 노출 위험, 상세 사유 응답 미포함).

    error_codes §4 트리: "RedactionUncertain → 500 INTERNAL (원문 노출 위험 —
    **상세 사유 응답에 미포함**)". detail에는 마스킹에 실패한 원문 조각이 담길 수 있으므로
    응답에서 빠진다(5xx 파생 — 별도 명시 없음). 로그에는 남는다(불변식 3은 경계 밖 전송을
    막는 규칙이며, 응답이 그 경계다).
    """

    code = "INTERNAL"
    http_status = 500

    #: 🔴 (8/6) **도달 경로가 생겼다** — refine이 컨텍스트 쪽 마스킹 불확실을 이걸로 올린다
    #: (강사 지시문 쪽은 200 `pii_exposure`로 남는다 · 주체가 다르다).
    #: ⚠ **정직하게 적어 둘 것**: §1 표상 BE 대응이 "재시도 1회 후 폴백"인데 redaction은
    #: 결정론이라 **재시도가 무의미하다.** 사전을 따르되 이 사실을 99에 남겼다
    #: (재시도 정책 개정은 별건).


class LedgerWriteFailed(DomainException):
    """원장(AI_RUN·SIGNAL·FEATURE_WEEK) 적재 실패 — 500 INTERNAL (fail-closed).

    멱등 캐시(fail-open, IdempotencyConflict와 별개)와 달리 원장은 산출물의 근거·재현
    기록이므로 저장 실패를 삼키지 않는다 — 요청을 실패시켜 백엔드가 재시도하게 한다
    (D-② 확정). 사전에 없는 코드는 만들지 않고 INTERNAL로 매핑한다(error_codes §1)."""

    code = "INTERNAL"
    http_status = 500


# ── contracts.llm → HTTP 매핑 경계 (7/22 A 판정 실행) ────────────


def domain_error_for(exc: LlmError) -> DomainException:
    """`contracts.llm` 예외 → HTTP 매핑 예외. **재시도 예산은 이미 소진됐다**는 전제.

    이 표가 이 경계의 계약이다:

    | 받는 예외 | 낼 것 | HTTP | 주체 판별 |
    | --- | --- | --- | --- |
    | `LlmTimeout` | `LlmUpstreamTimeout` | 504 TIMEOUT | 아무도 못 바꾼다 · 예산 소진 |
    | `LlmUnavailable` | `LlmUpstreamDown` | 503 LLM_UPSTREAM_DOWN | 〃 (429 승격분 포함) |
    | `ParseFailed`/`FieldMissing` | `DomainException` | 500 INTERNAL | **우리 스키마** 문제 |
    | 그 밖의 `LlmError`(4xx 등) | `DomainException` | 500 INTERNAL | **우리 프롬프트** 문제 |

    🔴 **plain `LlmError`를 503으로 뭉개지 마라.** 4xx는 벤더가 살아 있는데 **우리 요청이
    틀린** 것이다. 503으로 내면 BE 폴백 문구가 "잠시 후 다시"인데 잠시 후에도 똑같이
    실패한다 — 또 하나의 거짓말이 된다. `openai_compat`이 **429만** `LlmUnavailable`로
    승격하고 나머지 4xx를 plain `LlmError`로 두는 것이 바로 이 구분이다.

    🔴 **`RedactionBlocked`(contracts)·`RedactionBlockedError`(counsel provider)는 여기
    오지 않는다.** 둘 다 `LlmError` 하위라 인자 타입에는 걸리지만 **호출부가 먼저 잡아야**
    한다 — `except` 절 순서가 곧 계약이다. 그래도 들어오면 조용히 넘기지 않고 경고를
    남기고 INTERNAL로 떨어뜨린다(조용한 오분류 금지).
    """
    if isinstance(exc, LlmTimeout):
        return LlmUpstreamTimeout("LLM 응답 시간 초과")
    if isinstance(exc, LlmUnavailable):
        return LlmUpstreamDown("LLM 벤더 장애")
    if isinstance(exc, RedactionBlocked):
        logger.warning(
            "redaction 차단이 HTTP 매핑 경계까지 왔다 — 호출부가 먼저 잡아야 한다: %s",
            type(exc).__name__,
        )
    return DomainException("내부 처리 실패")


__all__ = [
    "ConsentAbsent",
    "DomainException",
    "IdempotencyConflict",
    "LedgerWriteFailed",
    "LlmUpstreamDown",
    "LlmUpstreamTimeout",
    "NotFound",
    "RedactionUncertain",
    "SnapshotInvalid",
    "domain_error_for",
]
