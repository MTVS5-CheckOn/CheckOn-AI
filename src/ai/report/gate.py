"""리포트 블록 결정론 게이트 어댑터 — LLM 호출 없음."""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel, ConfigDict

from ai.contracts.composition import extract_numbers
from ai.contracts.report import ReportBlock


class TextGateResult(Protocol):
    """기존 결정론 텍스트 게이트 결과의 최소 경계."""

    @property
    def passed(self) -> bool: ...

    @property
    def reason(self) -> str: ...


class DeterministicTextGate(Protocol):
    """브리핑 게이트를 직접 참조하지 않고 주입받는 호출 계약."""

    def __call__(
        self,
        text: str,
        allowed_numbers: frozenset[str],
        *,
        max_length: int,
    ) -> TextGateResult: ...


class ReportGateResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    passed: bool
    reason: str = ""


def check_report_block(
    block: ReportBlock,
    allowed_numbers: frozenset[str],
    *,
    max_length: int,
    text_gate: DeterministicTextGate,
) -> ReportGateResult:
    """현재 리비전을 기존 다섯 검사에 위임하고 numbers_used도 전수 대조한다."""

    revision = block.active_revision
    text = revision.display_content
    delegated = text_gate(text, allowed_numbers, max_length=max_length)
    if not delegated.passed:
        return ReportGateResult(passed=False, reason=delegated.reason)

    declared = frozenset(str(number) for number in revision.numbers_used)
    found = frozenset(extract_numbers(text))
    if found != declared:
        return ReportGateResult(passed=False, reason="numbers_used_mismatch")
    return ReportGateResult(passed=True)


__all__ = [
    "DeterministicTextGate",
    "ReportGateResult",
    "TextGateResult",
    "check_report_block",
]
