"""평가자 인터페이스와 지표 타입.

사양 원본: docs/part_a/08_evaluation_plan.md (golden/ 구성과 합격 기준)
소유: [A+B] 양자 승인 — 변경 시 두 명 승인 필수 (docs/02_ownership.md §4)

소비자는 evaluation/golden/ 코퍼스다. LLM이 낀 기능은 정답 비교가 아니라
**불변식 검증**(게이트 통과·구조 준수·근거 실존)이 기본이며, 문장 품질은
스냅숏 + 사람 리뷰로 본다 (평가 계획서 머리말).

합격 임계값(태깅 area 90% · classify 85% · complaint 재현율 95% 등)을 이 파일에
상수로 두지 않는다 — 값이 바뀔 때 코드 diff가 생기면 위치가 틀린 것이다
(03_coding_rules.md §1). **임계값의 거처는 골든셋 옆 설정 파일
(`evaluation/golden/criteria.yaml`)이며**(7/15 판단), 여기서는 Metric.threshold로
주입받기만 한다.
"""

from enum import StrEnum
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field


class GoldenSuite(StrEnum):
    """골든셋 코퍼스 — 평가 계획서 §1 디렉터리 구성 그대로."""

    DETECTION = "detection"
    """합성 학생 시나리오 12종(G1~G12) — 기대 발화·미발화."""

    TONE = "tone"
    """라벨 24조합 프롬프트 스냅숏 — LLM 호출 없이 결정론 검증."""

    TAGGING = "tagging"
    """수능 영역 태깅 — 정답 라벨 확정이 [A+B] 공용."""

    REFINE_ATTACK = "refine_attack"
    """게이트 공격 케이스(A1~A8) — 차단 미탐 0건이 CI 게이트."""

    REDACTION = "redaction"
    """마스킹 failure 코퍼스 30건 — 미탐 0건이 CI 게이트."""

    IMPORT_CORPUS = "import_corpus"
    """타사 양식 10종."""

    CLASSIFY = "classify"
    """문의 분류 100건."""

    PROBLEMS = "problems"
    """문항 검증 — B 소유."""

    DIAGNOSIS = "diagnosis"
    """약점 진단 그래프·결정론 회귀 — B 소유."""


class MetricKind(StrEnum):
    """지표 종류 — 계획서 §2~§7의 합격 기준에서 실제로 쓰이는 축만."""

    ACCURACY = "accuracy"
    """정확도 — 예: 태깅 area 정확도, 분류 정확도."""

    RECALL = "recall"
    """재현율 — 예: complaint 재현율(민원 놓침이 최악)."""

    PRECISION = "precision"

    FALSE_NEGATIVES = "false_negatives"
    """미탐 건수 — redaction·refine_attack은 0건이 CI 게이트."""

    FALSE_POSITIVES = "false_positives"
    """오탐 건수 — 예: G1 정상 시나리오의 오탐 0."""


class Metric(BaseModel):
    """지표 1개의 측정값과 합격 판정.

    threshold는 호출자가 주입한다 — 코드 상수로 굳히지 않는다.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: MetricKind
    value: float
    threshold: float | None = None
    """합격 기준값 — `evaluation/golden/criteria.yaml`에서 주입한다.

    None이면 관측만 하고 판정하지 않는다(기준이 아직 `[제안]`인 지표).
    """

    passed: bool | None = None
    """threshold가 있을 때의 판정. None이면 미판정."""


class CaseResult(BaseModel):
    """골든 케이스 1건의 결과.

    case_id는 계획서의 케이스 번호를 그대로 쓴다(G5·A1·G12 등) — 문서의 단어와
    코드 식별자가 1:1이어야 리뷰가 빨라진다 (03_coding_rules.md §6).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    case_id: str = Field(min_length=1)
    passed: bool
    reason: str | None = None
    """실패 사유 코드. 기대값과 실제값의 차이를 사람이 읽을 수 있게."""


class EvaluationResult(BaseModel):
    """스위트 1개의 평가 결과 — CI 판정의 단위.

    골든셋 기대값을 코드가 임의로 고쳐 통과시키는 것을 막기 위해, 결과는
    기대값이 아니라 측정값만 담는다. 기대값 변경은 PR로만 (계획서 §8).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    suite: GoldenSuite
    cases: tuple[CaseResult, ...] = ()
    metrics: tuple[Metric, ...] = ()

    @property
    def failed_cases(self) -> tuple[CaseResult, ...]:
        return tuple(case for case in self.cases if not case.passed)

    @property
    def passed(self) -> bool:
        """전 케이스 통과 + 판정된 지표 전부 통과.

        빈 스위트는 통과로 보지 않는다 — 케이스 미수집을 성공으로 읽으면 안 된다.
        """
        if not self.cases:
            return False
        if self.failed_cases:
            return False
        return all(metric.passed for metric in self.metrics if metric.passed is not None)


@runtime_checkable
class Evaluator(Protocol):
    """스위트 1개를 평가하는 인터페이스.

    구현: evaluation/detection_eval.py·draft_eval.py·import_eval.py(A) ·
    problem_eval.py(B).

    구현 규약:
    - 기대값을 코드에서 수정하지 않는다 — 골든 코퍼스가 원본 (계획서 §8).
    - 합격 임계값은 `golden/criteria.yaml`에서 읽어 Metric.threshold로 넣는다 —
      코드 상수 금지 (03_coding_rules.md §1).
    - 실 LLM·실 DB를 쓰는 평가는 integration 마커로 분리한다
      (03_coding_rules.md §7 — 기본 pytest는 오프라인으로 통과해야 한다).
    """

    @property
    def suite(self) -> GoldenSuite:
        """평가 대상 스위트."""
        ...

    def evaluate(self) -> EvaluationResult:
        """골든 코퍼스를 읽어 평가하고 결과를 반환한다."""
        ...
