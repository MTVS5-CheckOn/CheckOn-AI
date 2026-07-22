"""감지 원장 저장소 검사 — dedupe·FEATURE_WEEK upsert·fail-closed (D-② 커밋⑤).

순수 로직(dedupe)과 InMemory upsert는 결정론적으로, PG는 DB 없이 검증 가능한 fail-closed
경로(적재 실패가 요청을 500으로 막음 — 캐시의 fail-open과 정반대)를 가짜 sessionmaker로.
실 PG 왕복은 docker PG(99 ⑫) 후 integration 마커로 붙인다.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Coroutine
from datetime import UTC, date, datetime

import pytest

from ai.contracts.detection import EventSource, EventType, LearningEvent
from ai.contracts.execution import Capability, RunMetadata
from ai.db.repositories.detection_store import (
    DetectionStore,
    FeatureWeekRow,
    InMemoryDetectionStore,
    LedgerWrite,
    PgDetectionStore,
    dedupe_learning_events,
)
from ai.runtime.errors import LedgerWriteFailed

_T0 = datetime(2026, 7, 22, 9, 0, tzinfo=UTC)
_WEEK = date(2026, 7, 20)


def _run(coro: Coroutine[object, object, None]) -> None:
    asyncio.run(coro)


def _event(record_id: str, *, correct: bool | None = True) -> LearningEvent:
    return LearningEvent(
        record_id=record_id,
        student_ref="stu_1",
        type=EventType.SOLVE,
        occurred_at=_T0,
        correct=correct,
        source=EventSource.TRACK_A,
    )


def _run_meta(tenant: str = "teacher_1") -> RunMetadata:
    return RunMetadata(
        execution_id=uuid.UUID("00000000-0000-4000-8000-000000000001"),
        tenant_id=tenant,
        capability=Capability.DETECTION,
        pipeline_version="0.1.0",
        engine_version="detection-rules-0.1",
        threshold_version="thr-0.1",
        schema_version="0.1",
        contract_version="0.1",
        input_snapshot_hash="hash-1",
        created_at=_T0,
    )


def _feature_row(*, metrics: dict[str, object], week: date = _WEEK) -> FeatureWeekRow:
    return FeatureWeekRow(
        student_ref="stu_1",
        week_start=week,
        segment="normal",
        metrics=metrics,
        feature_version="0.1",
    )


# ───────────────────────── dedupe (순수) ─────────────────────────


def test_dedupe_no_duplicates() -> None:
    events = (_event("r1"), _event("r2"))
    merged, corrections = dedupe_learning_events(events)
    assert {e.record_id for e in merged} == {"r1", "r2"}
    assert corrections == ()


def test_dedupe_same_content_no_correction() -> None:
    """같은 record_id에 동일 내용 재등장 — dedupe만, 정정 아님."""
    events = (_event("r1"), _event("r1"))
    merged, corrections = dedupe_learning_events(events)
    assert len(merged) == 1
    assert corrections == ()


def test_dedupe_latest_wins_with_correction() -> None:
    """같은 record_id, 다른 내용(채점 정정) — 최신 승리 + 정정 로그. 에러 아님."""
    events = (_event("r1", correct=True), _event("r1", correct=False))
    merged, corrections = dedupe_learning_events(events)
    assert len(merged) == 1
    assert merged[0].correct is False  # 최신 승리
    assert len(corrections) == 1
    assert corrections[0].record_id == "r1"


# ───────────────────────── InMemory 적재 ─────────────────────────


def test_inmemory_satisfies_protocol() -> None:
    assert isinstance(InMemoryDetectionStore(), DetectionStore)


def test_inmemory_persist_appends_run_and_feature_weeks() -> None:
    store = InMemoryDetectionStore()
    ledger = LedgerWrite(
        run=_run_meta(),
        feature_weeks=(_feature_row(metrics={"accuracy": 0.9}),),
    )
    _run(store.persist_ledger(ledger))
    assert len(store.runs) == 1
    assert len(store.feature_weeks) == 1


def test_inmemory_feature_week_upsert_overwrites_on_change() -> None:
    """같은 (tenant·student·week·version) 재적재 — 최신 metrics로 갱신(1행 유지)."""
    store = InMemoryDetectionStore()
    _run(store.persist_ledger(LedgerWrite(run=_run_meta(), feature_weeks=(
        _feature_row(metrics={"accuracy": 0.5}),
    ))))
    _run(store.persist_ledger(LedgerWrite(run=_run_meta(), feature_weeks=(
        _feature_row(metrics={"accuracy": 0.9}),
    ))))
    assert len(store.feature_weeks) == 1
    (row,) = store.feature_weeks.values()
    assert row.metrics == {"accuracy": 0.9}  # 최신 승리


def test_inmemory_scope_distinguishes_week() -> None:
    store = InMemoryDetectionStore()
    _run(store.persist_ledger(LedgerWrite(run=_run_meta(), feature_weeks=(
        _feature_row(metrics={"accuracy": 0.5}, week=date(2026, 7, 13)),
        _feature_row(metrics={"accuracy": 0.9}, week=date(2026, 7, 20)),
    ))))
    assert len(store.feature_weeks) == 2


def test_inmemory_scope_distinguishes_tenant() -> None:
    store = InMemoryDetectionStore()
    _run(store.persist_ledger(LedgerWrite(run=_run_meta("t_a"), feature_weeks=(
        _feature_row(metrics={"accuracy": 0.5}),
    ))))
    _run(store.persist_ledger(LedgerWrite(run=_run_meta("t_b"), feature_weeks=(
        _feature_row(metrics={"accuracy": 0.5}),
    ))))
    assert len(store.feature_weeks) == 2  # 테넌트 격리


# ───────────────── PG fail-closed (DB 없이 가짜 sessionmaker) ─────────────────


class _RaisingSessionmaker:
    """with 진입 즉시 SQLAlchemyError를 내는 가짜 — DB 장애를 흉내낸다."""

    def __call__(self) -> _RaisingSessionmaker:
        return self

    async def __aenter__(self) -> _RaisingSessionmaker:
        from sqlalchemy.exc import OperationalError

        raise OperationalError("boom", None, Exception("db down"))

    async def __aexit__(self, *exc: object) -> None:
        return None


def _pg_store() -> PgDetectionStore:
    return PgDetectionStore(sessionmaker=_RaisingSessionmaker())  # type: ignore[arg-type]


def test_pg_satisfies_protocol() -> None:
    assert isinstance(_pg_store(), DetectionStore)


def test_pg_persist_failure_is_fail_closed() -> None:
    """원장 적재 중 DB 장애 → LedgerWriteFailed(500). 삼키지 않는다(캐시와 반대)."""
    ledger = LedgerWrite(
        run=_run_meta(), feature_weeks=(_feature_row(metrics={"accuracy": 0.9}),)
    )
    with pytest.raises(LedgerWriteFailed) as exc:
        _run(_pg_store().persist_ledger(ledger))
    assert exc.value.http_status == 500
    assert exc.value.code == "INTERNAL"
