"""게이트 ② 교차 풀이 결과의 결정론 판정."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from ai.contracts.problem_generation import GeneratedItem, SolveResult
from ai.problem_generation.domain.policy import VerifyConfig


class CrossValidationResult(BaseModel):
    """게이트 ② 출력에 대한 코드 판정."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    passed: bool
    low_confidence: bool
    failed_checks: tuple[str, ...] = ()


def validate_cross_solve(
    item: GeneratedItem,
    solve: SolveResult,
    config: VerifyConfig,
) -> CrossValidationResult:
    """교차 풀이 일치·복수정답·의미 정렬을 코드로 확정한다."""

    failed: list[str] = []
    if solve.chosen != item.answer.correct_no:
        failed.append("C-1:정답_불일치")
    if solve.multiple_answers_possible:
        failed.append("C-2:복수_정답_가능")
    if not solve.aligned:
        failed.append("C-3:목표_의미_불일치")

    low_confidence = (
        solve.confidence < config.cross_confidence_high
        or solve.alignment_confidence < config.alignment_confidence_min
    )
    return CrossValidationResult(
        passed=not failed,
        low_confidence=low_confidence,
        failed_checks=tuple(failed),
    )
