"""LLM provider 인터페이스 — 벤더 독립성의 경계.

사양 원본: docs/06_erd.md LLM_CALL 테이블 · docs/policies/error_codes.md §3·§4
소유: [A+B] 양자 승인 — 변경 시 두 명 승인 필수 (docs/02_ownership.md §4)

**벤더 SDK를 import하지 않는다.** 벤더 선정은 미확정(B-5)이며 확정 전 설치 금지
(CLAUDE.md §3). 이 파일은 인터페이스만 정의하고, 구현체(어댑터·FakeProvider)는
llm/providers/ 아래 B가 소유한다. 개발·테스트는 FakeProvider로 한다.

불변식 1(CLAUDE.md): LLM은 수치·판정을 확정하지 않는다. 이 인터페이스의 반환값은
언제나 게이트(contracts/gates.py)를 거쳐야 저장된다.
"""

from enum import StrEnum
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from ai.contracts.execution import ExecutionContext, GenerationParams


class ModelRole(StrEnum):
    """LLM_CALL.role — 호출의 역할. ERD 값 집합 그대로.

    역할별로 다른 모델·파라미터로 라우팅하기 위한 축이다(라우팅은 llm/gateway.py).
    """

    GENERATOR = "generator"
    """지문·문항 등 생성 (문제생성 중심)."""

    VERIFIER = "verifier"
    """생성물 검증·교차 풀이."""

    MAPPER = "mapper"
    """엑셀 컬럼 매핑 추론."""

    CLASSIFIER = "classifier"
    """문의 분류·태깅 제안 (v2 신설)."""

    NARRATOR = "narrator"
    """위험신호 브리핑 문장화 전용 (v2.1 신설).

    초안·리포트·refine은 도입 시 별도 role을 양자 결정으로 신설한다 — narrator를
    재사용하지 않는다(role별 전송 재시도·원가 회계가 소비자마다 갈리지 않게).
    """


class CallOutcome(StrEnum):
    """LLM_CALL.outcome — 내부 관측용(API 미노출). error_codes.md §3.

    재시도 정책(§3): PARSE_FAIL·FIELD_MISSING은 블록 단위 ≤3회 ·
    BAD_REF는 즉시 해당 문장 폐기(재시도 무의미 — 환각) ·
    REDACTION_BLOCKED는 재시도 금지 + 알럿.
    """

    OK = "ok"
    PARSE_FAIL = "parse_fail"
    FIELD_MISSING = "field_missing"
    BAD_REF = "bad_ref"
    """근거 ID 실존 실패 — 환각."""

    TIMEOUT = "timeout"
    PROVIDER_ERROR = "provider_error"
    REDACTION_BLOCKED = "redaction_blocked"
    """전송 전 차단 — 마스킹 정의서 §3 fail-closed."""


class TokenUsage(BaseModel):
    """LLM_CALL의 토큰·비용 집계분 — usage_daily 미터링 훅의 입력."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    tokens_in: int = Field(ge=0)
    tokens_out: int = Field(ge=0)
    cost_usd: float = Field(ge=0.0)


class LLMRequest(BaseModel):
    """provider에 넘기는 요청.

    prompt는 이미 조립·마스킹을 마친 문자열이다. 조립은 템플릿
    (llm/prompts/templates/)이 하고, 마스킹은 전송 직전 runtime/redaction이
    fail-closed로 통과시킨 분만 여기 담긴다 (불변식 3).
    """

    model_config = ConfigDict(frozen=True, extra="forbid", protected_namespaces=())

    role: ModelRole
    prompt: str = Field(min_length=1)
    """redaction 통과분만 — 원문 금지 (불변식 3)."""

    prompt_id: str
    """registry.yaml의 프롬프트 식별자 — 코드에 f-string 조립 금지."""

    prompt_version: str
    generation_params: GenerationParams | None = None
    response_schema_name: str | None = None
    """구조화 출력의 스키마 이름 — 파싱은 llm/structured.py가 담당."""


class LLMResult(BaseModel):
    """provider의 반환값 — LLM_CALL 기록의 원천.

    text는 아직 신뢰할 수 없는 원문이다. 구조화 출력은 llm/structured.py 파서를
    거친 모델만 신뢰하며, 원문을 직접 파싱하는 코드는 반려 (03_coding_rules.md §4).
    """

    model_config = ConfigDict(frozen=True, extra="forbid", protected_namespaces=())

    outcome: CallOutcome
    text: str | None = None
    """OK가 아니면 None일 수 있다."""

    provider: str
    model: str
    usage: TokenUsage
    latency_ms: int = Field(ge=0)


@runtime_checkable
class LLMProvider(Protocol):
    """provider 어댑터가 구현하는 인터페이스.

    구현체는 llm/providers/ 소유(B). FakeProvider도 이 Protocol을 구현해
    테스트를 결정론화한다 (03_coding_rules.md §3 — 인터페이스 + 주입).

    구현 규약:
    - 예외를 삼키지 않는다. 실패는 LlmError 계열로 올리거나, 관측 가능한
      실패(파싱·근거)는 LLMResult.outcome으로 표현한다 (03_coding_rules.md §5).
    - 재시도·백오프를 어댑터 안에서 자체 구현하지 않는다 — 게이트웨이가
      tenacity로 처리한다 (03_coding_rules.md §1b).
    """

    @property
    def name(self) -> str:
        """provider 식별자 — LLM_CALL.provider에 기록된다."""
        ...

    async def complete(
        self,
        request: LLMRequest,
        context: ExecutionContext,
    ) -> LLMResult:
        """프롬프트 1회 호출. 실패는 LlmError 계열 또는 outcome으로 표현한다."""
        ...


class LlmError(Exception):
    """이 계약의 LLM 예외 루트.

    **공통 예외는 contracts가 소유한다**(7/15 판단). contracts는 내부 모듈을
    import하지 않으므로(의존 방향 규칙) runtime/errors.py의 DomainException을
    상속하지 않는 독립 계층이며, HTTP 매핑은 runtime/errors.py가 이 예외들을
    받아 수행한다 (error_codes.md §4).
    """


class LlmUnavailable(LlmError):
    """벤더 장애 — error_codes.md §4: 503 LLM_UPSTREAM_DOWN.

    폴백 규약(§1): 감지=전일 브리핑 유지+배지 · 초안="잠시 후 다시".
    """


class LlmTimeout(LlmError):
    """호출 시간 초과 — error_codes.md §1: 504 TIMEOUT (동기 10s)."""


class ParseFailed(LlmError):
    """구조화 출력 파싱 실패 — outcome=parse_fail. 블록 단위 ≤3회 재시도 대상."""


class FieldMissing(ParseFailed):
    """파싱은 됐으나 필수 필드 누락 — outcome=field_missing. 재시도 대상."""


class RedactionBlocked(LlmError):
    """전송 전 마스킹 차단 — fail-closed. **재시도 금지 + 알럿**.

    사유 상세를 응답에 싣지 않는다 — 원문 노출 위험 (error_codes.md §4
    RedactionUncertain → 500 INTERNAL).
    """
