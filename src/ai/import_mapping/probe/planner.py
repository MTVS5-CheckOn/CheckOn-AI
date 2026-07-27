"""ReAct 플래너 — 계약 Protocol + 결정론 Fake (langgraph_state §2.1 reason 노드).

reason 노드는 본래 LLM이지만, 이 골격의 LLM 접점은 **Protocol+Fake**다(실 LLM·게이트웨이는
후속). FakeProbePlanner는 저신뢰 컬럼을 하나씩 도구로 조사하고, 관찰 후 헤더 규칙으로
해소/미해소를 판정한다 — 억지 매핑 금지(규칙 없으면 '모름'). 판정은 결정론이며 값 미접근.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol

from ai.import_mapping.probe.state import (
    ColumnMapping,
    MappingProbeState,
    ProbeStep,
    ProbeToolName,
    UnresolvedColumn,
)


@dataclass(frozen=True)
class ToolAction:
    """도구 호출 지시 — reason 노드 산출."""

    thought: str
    tool: ProbeToolName
    tool_args: dict[str, str]
    column: str
    """이 조사가 겨냥한 컬럼(관찰 후 판정 대상)."""


@dataclass(frozen=True)
class ProposeAction:
    """조사 종료·spec 제안 지시."""

    thought: str


Action = ToolAction | ProposeAction


class ProbePlanner(Protocol):
    """조사 정책 인터페이스 — state를 보고 다음 행동/판정을 낸다."""

    def next_action(self, state: MappingProbeState) -> Action: ...

    def classify(self, column: str, step: ProbeStep) -> ColumnMapping | UnresolvedColumn: ...


#: 헤더 규칙 → (07 표준 필드, 신뢰도). Fake 조사자의 판정 정책(결정론 · 값 미접근).
_HEADER_RULES: tuple[tuple[re.Pattern[str], str, float], ...] = (
    (re.compile(r"원생명|성명|성함|이름|학생"), "student_name", 0.95),
    (re.compile(r"반|class|학급"), "class_name", 0.9),
    (re.compile(r"등원|입원|가입|enroll"), "enrolled_at", 0.9),
    (re.compile(r"상태|status|재원"), "status", 0.88),
    (re.compile(r"동의|consent"), "consent", 0.92),
    (re.compile(r"학년|grade"), "grade", 0.9),
    (re.compile(r"날짜|일자|제출|occurred|date"), "occurred_at", 0.9),
    (re.compile(r"구분|유형구분|event"), "event_type", 0.85),
    (re.compile(r"점수|score|성적"), "score", 0.9),
)


def candidate_columns(state: MappingProbeState) -> list[str]:
    """아직 해소/미해소로 확정되지 않은 컬럼(등장 순서) — sheets_meta의 columns 기준."""
    columns = state.sheets_meta.get("columns", [])
    names = [str(c) for c in columns] if isinstance(columns, list) else []
    settled = set(state.resolved_columns) | {u.source for u in state.unresolved_columns}
    return [name for name in names if name not in settled]


class FakeProbePlanner:
    """결정론 Fake ReAct — 남은 후보를 get_unique_values로 하나씩 조사하고 헤더 규칙으로 판정."""

    def next_action(self, state: MappingProbeState) -> Action:
        remaining = candidate_columns(state)
        if not remaining:
            return ProposeAction(thought="모든 컬럼 판정 완료 — spec 제안")
        target = remaining[0]
        return ToolAction(
            thought=f"'{target}' 저신뢰 — 유니크 값으로 성격 확인",
            tool="get_unique_values",
            tool_args={"column": target},
            column=target,
        )

    def classify(self, column: str, step: ProbeStep) -> ColumnMapping | UnresolvedColumn:
        for pattern, field, confidence in _HEADER_RULES:
            if pattern.search(column):
                return ColumnMapping(source=column, target=field, confidence=confidence)
        return UnresolvedColumn(source=column, reason="표준 필드 후보 없음 — 강사 확인 필요")
