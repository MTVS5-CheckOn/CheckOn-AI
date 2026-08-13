"""매핑 추론 provider — 계약 Protocol + 결정론 Fake (10_import_spec §3.2).

LLM은 **매핑 추론만** 한다(불변식 1) — 헤더·마스킹 통과 통계/샘플로 표준 필드 후보를 낸다.
실 벤더 연결은 후속(llm/providers). 이 브랜치는 FakeMappingProvider로 결정론화하며, 실패
주입(FailingMappingProvider)으로 폴백 분기(§3.2)를 검증한다.
"""

from __future__ import annotations

import re
from typing import Protocol

from ai.contracts.imports import MappingColumn
from ai.import_mapping.profiling import SourceProfile, source_columns


class MappingInferenceError(Exception):
    """매핑 추론 실패(LLM 미가용·파싱 실패 등) — inference가 폴백으로 흡수(§3.2)."""


class MappingProvider(Protocol):
    """매핑 추론 인터페이스 — inference는 이 타입에만 의존한다."""

    async def infer(self, profile: SourceProfile) -> tuple[MappingColumn, ...]:
        """헤더/통계로 컬럼별 (target, confidence) 후보를 낸다. 실패는 MappingInferenceError."""
        ...


#: 헤더 패턴 → (07 표준 필드, 신뢰도). Fake의 결정론 규칙 — 실 LLM 추론의 대역.
_HEADER_RULES: tuple[tuple[re.Pattern[str], str, float], ...] = (
    (re.compile(r"원생명|성명|성함|이름|학생"), "student_name", 0.97),
    (re.compile(r"반|class|학급"), "class_name", 0.9),
    (re.compile(r"등원|입원|가입|enroll"), "enrolled_at", 0.88),
    (re.compile(r"상태|status|재원"), "status", 0.85),
    (re.compile(r"동의|consent"), "consent", 0.9),
    (re.compile(r"학년|grade"), "grade", 0.9),
    (re.compile(r"보호자|학부모|연락처|전화|휴대폰|phone"), "guardian_phone", 0.8),
    (re.compile(r"날짜|일자|occurred|date"), "occurred_at", 0.86),
    (re.compile(r"구분|유형구분|event"), "event_type", 0.7),
    (re.compile(r"과제|시험|assignment|title"), "assignment_title", 0.75),
    (re.compile(r"점수|score|성적"), "score", 0.55),
    (re.compile(r"정답|정오|correct|맞"), "correct", 0.6),
)


def _match_header(name: str) -> tuple[str | None, float | None]:
    for pattern, field, confidence in _HEADER_RULES:
        if pattern.search(name):
            return field, confidence
    return None, None


class FakeMappingProvider:
    """결정론 Fake — 헤더 규칙으로 매핑 후보를 낸다(값 미접근, 헤더만)."""

    async def infer(self, profile: SourceProfile) -> tuple[MappingColumn, ...]:
        columns: list[MappingColumn] = []
        for name in source_columns(profile):
            target, confidence = _match_header(name)
            columns.append(
                MappingColumn(
                    source=name,
                    target=target,
                    confidence=confidence,
                    unmapped_reason=None if target else "표준 필드 후보 없음",
                )
            )
        return tuple(columns)


class FailingMappingProvider:
    """항상 실패하는 provider — LLM 미가용 폴백(§3.2) 분기 검증용."""

    def __init__(self, message: str = "LLM provider 미가용") -> None:
        self._message = message

    async def infer(self, profile: SourceProfile) -> tuple[MappingColumn, ...]:
        raise MappingInferenceError(self._message)
