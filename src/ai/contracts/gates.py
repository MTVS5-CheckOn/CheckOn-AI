"""게이트 인터페이스와 결과 타입.

사양 원본: docs/06_erd.md GATE_RESULT 테이블 · docs/policies/error_codes.md §2·§4
소유: [A+B] 양자 승인 — 변경 시 두 명 승인 필수 (docs/02_ownership.md §4)

불변식 4(CLAUDE.md): **게이트 거부는 에러가 아니다.** 거부는 예외가 아니라
반환값(GateResult.passed=False)이며, API에서는 200 + status로 내려간다.
GateRejected를 5xx로 올리는 코드는 리뷰 반려 (error_codes.md §4).

불변식 5: 강사 지시가 게이트를 이기지 못한다 — refine의 매 턴도 체인 전체를
재통과한다 (docs/part_a/06_refine_policy.md).
"""

from enum import StrEnum
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field


class GateName(StrEnum):
    """GATE_RESULT.gate_name — A 체인과 B 출제 게이트의 공용 값 집합."""

    CONSENT = "Consent"
    """동의 확인 — 미동의 시 생성 자체가 불가 (error_codes.md §1: 422)."""

    DATA_SUFFICIENCY = "DataSufficiency"
    """데이터 충분성 — 재원 2주 미만 등. 거부 시 status=rejected_insufficient."""

    EVIDENCE = "Evidence"
    """근거 실존 — evidence 없는 산출물 금지 (불변식 2)."""

    SOURCE_GROUNDING = "SourceGrounding"
    """원천 대조 — 수치·예측 단정이 근거에 실재하는지."""

    TONE_SAFETY = "ToneSafety"
    """톤·금칙어 — direct 라벨이어도 금칙은 유지."""

    REQUIRED_FIELD = "RequiredField"
    """필수 필드 충족."""

    MAPPING_CONFIDENCE = "MappingConfidence"
    """매핑 신뢰도 — 낮으면 숨기지 않고 needs_review로 노출."""

    TEACHER_CONFIRM = "TeacherConfirm"
    """강사 확정 — 자동 확정 금지 지점."""

    RULE_VALIDATION = "RuleValidation"
    """B 게이트 ① — 구조·근거·금칙·중복의 결정론 검사."""

    BLIND_CROSS_SOLVE = "BlindCrossSolve"
    """B 게이트 ② — blind 교차 풀이와 약점 의미 정렬 결과 기록."""

    RELEASE_DECISION = "ReleaseDecision"
    """B 게이트 ③ — pass·needs_review·reject의 결정론 판정."""


class OwnerKind(StrEnum):
    """GATE_RESULT.owner_kind — 게이트 이력이 달리는 산출물 종류."""

    DRAFT = "draft"
    IMPORT_JOB = "import_job"
    PROBLEM_SET = "problem_set"


class BlockedReason(StrEnum):
    """refine 리비전 차단 사유 — error_codes.md §2.2.

    문구의 원본은 핑퐁 정책서 §4 표다 — 여기서 문구를 중복 정의하지 않는다.
    """

    EVIDENCE_MISSING = "evidence_missing"
    COMPARISON_EXPOSURE = "comparison_exposure"
    """반 평균·석차 등 teacher_only 데이터 노출 시도 (불변식 7)."""

    TONE_VIOLATION = "tone_violation"
    PII_EXPOSURE = "pii_exposure"
    OUT_OF_SCOPE = "out_of_scope"
    ANSWER_INTEGRITY = "answer_integrity"
    BANNED_TOPIC = "banned_topic"
    PROMPT_INJECTION = "prompt_injection"


class GateResult(BaseModel):
    """게이트 1개의 판정 — GATE_RESULT 1행에 대응.

    passed=False는 정상 흐름이다. 예외로 승격하지 않는다 (불변식 4).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    gate_name: GateName
    passed: bool
    seq: int = Field(ge=0)
    """체인 내 실행 순서 — 어디서 멈췄는지가 감사 대상."""

    reason: str | None = None
    """거부 사유 코드. passed=True면 보통 None.

    refine 경로의 사유는 BlockedReason 값을 쓴다 (error_codes.md §2.2).
    강사에게 보일 문구가 아니라 코드다 — 문구는 백엔드가 사전에서 찾는다.
    """


class GateChainResult(BaseModel):
    """체인 전체의 판정 — 실행된 게이트 이력의 묶음.

    체인은 첫 거부에서 멈출 수 있으므로 results가 전체 게이트를 담지 않을 수 있다.

    **이 타입은 사실만 담는다.** 여기서 error_codes.md §2.1의 draft status
    (`template_only` · `rejected_insufficient`)로 가는 매핑은 composition의
    정책이므로 composition/gates_impl.py 소유다 (7/15 판단) — 계약에 두지 않는다.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    results: tuple[GateResult, ...] = ()

    @property
    def passed(self) -> bool:
        """전 게이트 통과 여부. 빈 체인은 통과로 보지 않는다(게이트 미실행 = 미검증)."""
        return len(self.results) > 0 and all(result.passed for result in self.results)

    @property
    def first_rejection(self) -> GateResult | None:
        """최초 거부 게이트 — 산출물 status·사유의 근거가 된다."""
        return next((result for result in self.results if not result.passed), None)


@runtime_checkable
class Gate(Protocol):
    """게이트 1개가 구현하는 인터페이스.

    체인 실행기는 gates/chain.py(A 소유)이며, capability별 구현은 각
    gates_impl.py에 둔다.

    구현 규약:
    - 거부를 예외로 던지지 않는다 — GateResult를 반환한다 (불변식 4).
    - 판정은 결정론이다. 게이트 안에서 LLM에 판정을 위임하지 않는다 (불변식 1).
    - 스킵 플래그·우회 옵션을 만들지 않는다 (03_coding_rules.md §9).
    """

    @property
    def name(self) -> GateName:
        """GATE_RESULT.gate_name에 기록될 이름."""
        ...

    def check(self, payload: object, seq: int) -> GateResult:
        """판정 후 결과를 반환한다. payload 타입은 구현체가 좁힌다."""
        ...
