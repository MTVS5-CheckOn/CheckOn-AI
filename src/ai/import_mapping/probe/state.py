"""mapping_probe 워커 state·도메인 타입 — langgraph_state §2.2 스키마 그대로.

state는 §2.2를 1:1로 옮긴다(값 대조 테스트로 고정). 타입(ColumnMapping·UnresolvedColumn·
MappingSpecDraft)은 워커 내부 전용이라 import_mapping이 소유한다(계약 오염 방지 — 02_ownership).
응답 DTO(contracts/imports.MappingColumn)와의 변환은 어댑터 한 곳에서만 한다.

불변식(§2.2): ① loop_count > 5 도달 시 남은 컬럼은 전부 unresolved(억지 매핑 금지) ②
observation_masked에 마스킹 실패 흔적(⟪확인필요⟫)이 있으면 관찰 폐기 + 루프 1회 소모 ③
최종 spec 컬럼 수 = resolved + unresolved(누락 없음).
"""

from __future__ import annotations

from typing import Final, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

STATE_SCHEMA_VERSION: Final = "mapping_probe.v1"

#: 조사 도구 3종(§2.1·01_pipeline §3) — 전부 결정론·마스킹 뒤.
ProbeToolName = Literal["get_unique_values", "get_more_sample", "check_join_key"]

#: 마스킹 실패 흔적 토큰(masking_redaction §1) — 관찰에 있으면 폐기(불변식 ②).
UNCERTAIN_TOKEN = "⟪확인필요⟫"


class ColumnMapping(BaseModel):
    """해소된(target 있는) 컬럼 — resolved_columns의 값."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source: str = Field(min_length=1)
    target: str = Field(min_length=1)
    """07 표준 필드명."""

    confidence: float = Field(ge=0.0, le=1.0)


class UnresolvedColumn(BaseModel):
    """미해소('모름') 컬럼 — 사유 필수(억지 매핑 금지)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source: str = Field(min_length=1)
    reason: str = Field(min_length=1)


class MappingSpecDraft(BaseModel):
    """조사 산출 spec 초안 — 컬럼 수 = resolved + unresolved(불변식 ③)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    resolved: tuple[ColumnMapping, ...] = ()
    unresolved: tuple[UnresolvedColumn, ...] = ()
    overall_confidence: float = Field(ge=0.0, le=1.0)


class ProbeStep(BaseModel):
    """조사 스텝 1개 — 도구 호출·중간 가설. thought·observation은 마스킹 통과분만."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    seq: int = Field(ge=0)
    thought: str
    """마스킹 후 저장(§3.1). 자유 텍스트라 redaction 통과분만."""

    tool: ProbeToolName | None
    tool_args: dict[str, str] = {}
    observation_masked: str = ""
    """도구 반환 = 마스킹 통과분만(구조적 차단, §5.2)."""


class MappingProbeState(BaseModel):
    """mapping_probe ReAct 체크포인트 state — langgraph_state §2.2 그대로."""

    model_config = ConfigDict(extra="forbid")

    state_schema_version: Literal["mapping_probe.v1"] = STATE_SCHEMA_VERSION
    tenant_id: str = Field(min_length=1)
    source_profile_id: UUID
    sheets_meta: dict[str, object]
    """헤더·타입·샘플 통계(원본 행 아님 — 마스킹 통과 통계만)."""

    steps: list[ProbeStep] = []
    loop_count: int = 0
    """≤ 5 — 초과 시 강제 propose(불변식 ①)."""

    resolved_columns: dict[str, ColumnMapping] = {}
    unresolved_columns: list[UnresolvedColumn] = []
    spec_draft: MappingSpecDraft | None = None
    overall_confidence: float | None = None
