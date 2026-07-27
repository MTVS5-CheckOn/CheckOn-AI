"""워커 저장소 — 레코드↔ORM 1:1 대응·URI ref·직렬화 roundtrip (langgraph_state §5 · §3.3).

레코드 필드가 대응 ORM 컬럼과 정확히 일치함을 값 대조로 고정한다 — 후속 PG 연결이 기계적이
되도록(사용자 확정 규약 ①). ref는 불투명 URI(규약 ②).
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from ai.db.models import AgentStep, MappingSpec, SourceProfile
from ai.import_mapping.probe.stores import (
    AgentStepRecord,
    InMemoryAgentStepSink,
    InMemoryProfileStore,
    InMemorySpecResultStore,
    ProfileRecord,
    SpecRecord,
    deserialize_profile,
    make_ref,
    parse_ref,
    serialize_profile,
)
from ai.import_mapping.profiling import ColumnProfile, SheetProfile
from ai.import_mapping.profiling import SourceProfile as ProfileData

_ID = UUID("00000000-0000-4000-8000-000000000001")
_NOW = datetime(2026, 7, 27, tzinfo=UTC)


def _orm_columns(model: type) -> set[str]:
    return set(model.__table__.columns.keys())  # type: ignore[attr-defined]


def test_records_are_1to1_with_orm_columns() -> None:
    """레코드 필드 집합 == ORM 컬럼 집합(1:1) — PG 연결이 기계적이 되게."""
    assert set(ProfileRecord.model_fields) == _orm_columns(SourceProfile)
    assert set(SpecRecord.model_fields) == _orm_columns(MappingSpec)
    assert set(AgentStepRecord.model_fields) == _orm_columns(AgentStep)


def test_ref_is_opaque_uri_roundtrip() -> None:
    ref = make_ref("profile", _ID)
    assert ref == f"profile://{_ID}"
    assert parse_ref(ref, "profile") == _ID


def test_ref_scheme_mismatch_rejected() -> None:
    try:
        parse_ref(f"spec://{_ID}", "profile")
    except ValueError:
        return
    raise AssertionError("스킴 불일치는 ValueError여야 한다")


def _profile() -> ProfileData:
    cols = (
        ColumnProfile(
            name="원생명", n_total=2, n_null=0, n_unique=2, dtype_guess="string", suspect_pii=True
        ),
    )
    return ProfileData(filename="r.xlsx", sheets=(SheetProfile("s", 2, cols, ()),))


def test_profile_serialize_roundtrip() -> None:
    profile = _profile()
    restored = deserialize_profile(serialize_profile(profile))
    assert restored == profile


def test_profile_store_put_get() -> None:
    store = InMemoryProfileStore()
    rec = ProfileRecord(
        id=_ID, tenant_id="t1", file_hash="h", filename="r.xlsx",
        sheets=serialize_profile(_profile()), created_at=_NOW,
    )
    ref = store.put(rec)
    assert store.get(ref) == rec
    assert store.get(make_ref("profile", UUID(int=2))) is None


def test_spec_store_put_get() -> None:
    store = InMemorySpecResultStore()
    rec = SpecRecord(
        id=_ID, tenant_id="t1", source_profile_id=UUID(int=9), version=1,
        spec={"resolved": []}, status="succeeded", probe_agent_run=UUID(int=3),
    )
    ref = store.put(rec)
    assert ref == f"spec://{_ID}" and store.get(ref) == rec


def test_agent_step_sink_records_in_order() -> None:
    sink = InMemoryAgentStepSink()
    run = UUID(int=7)
    for seq in range(3):
        sink.record(
            AgentStepRecord(
                id=UUID(int=100 + seq), agent_run_id=run, seq=seq, node_name="tool_call",
                tool_called="get_unique_values", tool_args_masked={"column": "점수A"},
                llm_call_id=None, outcome="ok",
            )
        )
    steps = sink.steps(run)
    assert [s.seq for s in steps] == [0, 1, 2]
    assert sink.steps(UUID(int=999)) == ()
