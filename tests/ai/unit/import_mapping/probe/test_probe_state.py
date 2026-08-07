"""mapping_probe state 스키마 — langgraph_state §2.2를 값 대조로 고정.

기대 필드 집합·기본값·불변 상수는 손 작성이다(엔진 산출 금지) — §2.2가 회귀 없이 유지되는지.
"""

from __future__ import annotations

from uuid import UUID

from ai.import_mapping.probe.state import (
    STATE_SCHEMA_VERSION,
    UNCERTAIN_TOKEN,
    ColumnMapping,
    MappingProbeState,
    MappingSpecDraft,
    ProbeStep,
    UnresolvedColumn,
)

_PROFILE_ID = UUID("00000000-0000-4000-8000-000000000001")

# §2.2 코드블록의 기대 필드 집합(손 작성).
# ✅ **(8/11 · 99 #07 해소) 「문서 → 코드」 방향은 `test_doc_enum_parity.py`가 지킨다** —
#   §2.2를 실제로 파싱해 `MappingProbeState.model_fields`와 대조한다. 붙이자마자
#   문서에 `state_schema_version`이 빠진 것을 찾았다(여기 손사본엔 있었다 — `코드 ==
#   코드`라 조용했다). 여기가 지키는 것은 **기본값·불변 상수**다(아래 단정들).
_EXPECTED_STATE_FIELDS = {
    "state_schema_version",
    "tenant_id",
    "source_profile_id",
    "sheets_meta",
    "steps",
    "loop_count",
    "resolved_columns",
    "unresolved_columns",
    "spec_draft",
    "overall_confidence",
}
_EXPECTED_STEP_FIELDS = {"seq", "thought", "tool", "tool_args", "observation_masked"}


def test_state_field_set_matches_spec() -> None:
    assert set(MappingProbeState.model_fields) == _EXPECTED_STATE_FIELDS
    assert set(ProbeStep.model_fields) == _EXPECTED_STEP_FIELDS


def test_schema_version_and_constants() -> None:
    assert STATE_SCHEMA_VERSION == "mapping_probe.v1"
    assert UNCERTAIN_TOKEN == "⟪확인필요⟫"


def test_state_defaults() -> None:
    state = MappingProbeState(
        tenant_id="t1", source_profile_id=_PROFILE_ID, sheets_meta={}
    )
    assert state.state_schema_version == "mapping_probe.v1"
    assert state.loop_count == 0
    assert state.steps == [] and state.resolved_columns == {}
    assert state.unresolved_columns == [] and state.spec_draft is None
    assert state.overall_confidence is None


def test_probe_step_masked_defaults() -> None:
    step = ProbeStep(seq=0, thought="가설", tool="get_unique_values", tool_args={"col": "점수A"})
    assert step.observation_masked == "" and step.tool_args == {"col": "점수A"}


def test_spec_draft_column_count_invariant() -> None:
    """최종 spec 컬럼 수 = resolved + unresolved(불변식 ③)."""
    draft = MappingSpecDraft(
        resolved=(ColumnMapping(source="원생명", target="student_name", confidence=0.97),),
        unresolved=(UnresolvedColumn(source="점수B", reason="표준 필드 후보 없음"),),
        overall_confidence=0.8,
    )
    assert len(draft.resolved) + len(draft.unresolved) == 2
