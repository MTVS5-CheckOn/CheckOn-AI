"""게이트 ② 교차 풀이 결과의 결정론 판정."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from ai.contracts.problem_generation import (
    GeneratedItem,
    MisconceptionCheckResult,
    SolveResult,
)
from ai.problem_generation.domain.policy import MisconceptionTagsConfig, VerifyConfig


class CrossValidationResult(BaseModel):
    """게이트 ② 출력에 대한 코드 판정."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    passed: bool
    low_confidence: bool
    failed_checks: tuple[str, ...] = ()


def validate_misconception_structure(
    item: GeneratedItem,
    config: MisconceptionTagsConfig,
) -> tuple[str, ...]:
    """오개념 필드의 정답 축과 영역별 닫힌 어휘를 결정론적으로 검증한다."""

    allowed = {tag.id for tag in config.tags_for(item.area_tag)}
    failed: list[str] = []
    for choice in sorted(item.choices, key=lambda choice: choice.no):
        if choice.no == item.answer.correct_no:
            if choice.misconception_tag is not None:
                failed.append("C-4:정답_오개념_표기")
            if choice.why_wrong is not None:
                failed.append("C-5:정답_오답_사유_표기")
            continue
        if choice.misconception_tag is None:
            failed.append(f"C-6:오답_오개념_누락:{choice.no}")
        elif choice.misconception_tag not in allowed:
            failed.append(f"C-7:오개념_어휘_이탈:{choice.no}")
        if choice.why_wrong is None:
            failed.append(f"C-8:오답_사유_누락:{choice.no}")
    return tuple(failed)


def validate_cross_solve(
    item: GeneratedItem,
    solve: SolveResult,
    misconception_check: MisconceptionCheckResult,
    config: VerifyConfig,
) -> CrossValidationResult:
    """교차 풀이와 오답 오개념 의미 검증 결과를 코드로 확정한다."""

    failed: list[str] = []
    if solve.chosen != item.answer.correct_no:
        failed.append("C-1:정답_불일치")
    if solve.multiple_answers_possible:
        failed.append("C-2:복수_정답_가능")
    if not solve.aligned:
        failed.append("C-3:목표_의미_불일치")

    expected_wrong = {
        choice.no for choice in item.choices if choice.no != item.answer.correct_no
    }
    checked = {check.choice_no for check in misconception_check.checks}
    if checked != expected_wrong:
        failed.append("C-9:오개념_검증_선지_불일치")
    failed.extend(
        f"C-10:오개념_설명_모순:{check.choice_no}"
        for check in sorted(
            misconception_check.checks,
            key=lambda check: check.choice_no,
        )
        if not check.consistent
    )

    low_confidence = (
        solve.confidence < config.cross_confidence_high
        or solve.alignment_confidence < config.alignment_confidence_min
    )
    return CrossValidationResult(
        passed=not failed,
        low_confidence=low_confidence,
        failed_checks=tuple(failed),
    )
