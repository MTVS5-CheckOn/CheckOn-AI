"""4-5 재현 — FEATURE_WEEK 조회에 feature_version 필터가 없어 두 버전이 섞인다.

upsert 유니크 스코프엔 `feature_version`이 있는데(`(tenant, student, week, version)`)
**조회엔 없다.** 산식 개정을 배포하는 순간 같은 (student, week)에 0.1·0.2 두 행이 공존하고
**둘 다 로드**되며, 정렬에 버전 tiebreaker가 없어 같은 입력에 baseline이 흔들린다 —
같은 AI_RUN을 재현할 수 없다(불변식 8 위반).

InMemory 구현도 같은 시그니처·같은 필터여야 한다 — 두 구현이 갈리면 테스트가 거짓말한다.
"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Coroutine
from datetime import UTC, date, datetime
from uuid import UUID

from ai.db.repositories.detection_store import (
    FeatureWeekRow,
    InMemoryDetectionStore,
    LedgerWrite,
)

_TENANT = "t1"
_STUDENT = "st_1"
_WEEK = date(2026, 7, 13)
_OLD = "0.1"
_NEW = "0.2"


def _row(version: str, acc: float) -> FeatureWeekRow:
    """같은 (student, week)에 버전만 다른 행 — 산식 개정 배포 순간의 실제 상태."""
    return FeatureWeekRow(
        student_ref=_STUDENT,
        week_start=_WEEK,
        segment="normal",
        metrics={"accuracy": acc},
        feature_version=version,
    )


def _run[T](coro: Coroutine[object, object, T]) -> T:
    return asyncio.run(coro)


def _seeded_store() -> InMemoryDetectionStore:
    from ai.contracts.execution import Capability, RunMetadata

    store = InMemoryDetectionStore()
    run = RunMetadata(
        execution_id=UUID("00000000-0000-4000-8000-0000000000c1"),
        tenant_id=_TENANT,
        capability=Capability.DETECTION,
        pipeline_version="0.1",
        engine_version="detection-rules-0.2",
        schema_version="0.1",
        contract_version="0.1",
        input_snapshot_hash="h",
        created_at=datetime(2026, 7, 13, tzinfo=UTC),
    )
    _run(
        store.persist_ledger(
            LedgerWrite(
                run=run,
                signals=(),
                feature_weeks=(_row(_OLD, 0.40), _row(_NEW, 0.90)),
            )
        )
    )
    return store


def test_two_versions_coexist_after_upsert() -> None:
    """전제 확인 — upsert 스코프에 버전이 있으므로 두 행이 **공존한다**(덮어쓰지 않는다)."""
    store = _seeded_store()
    assert len(store.feature_weeks) == 2


def test_load_returns_only_requested_version() -> None:
    """핵심 재현 — 현재 버전만 로드돼야 한다(지금은 두 행 다 나온다)."""
    store = _seeded_store()
    rows = _run(store.load_feature_weeks(_TENANT, [_STUDENT], feature_version=_NEW))

    assert len(rows) == 1, f"버전이 섞였다: {[r.feature_version for r in rows]}"
    assert rows[0].feature_version == _NEW
    assert rows[0].metrics["accuracy"] == 0.90


def test_load_old_version_is_isolated() -> None:
    """구버전을 명시하면 구버전만 — 필터가 실제로 WHERE로 동작하는지."""
    store = _seeded_store()
    rows = _run(store.load_feature_weeks(_TENANT, [_STUDENT], feature_version=_OLD))

    assert [r.feature_version for r in rows] == [_OLD]
    assert rows[0].metrics["accuracy"] == 0.40


def test_unknown_version_loads_nothing() -> None:
    """등록되지 않은 버전이면 빈 결과 — 조용히 다른 버전으로 대체하지 않는다."""
    store = _seeded_store()
    assert _run(store.load_feature_weeks(_TENANT, [_STUDENT], feature_version="9.9")) == ()


def test_protocol_and_inmemory_signatures_match() -> None:
    """두 구현이 갈리면 테스트가 거짓말한다 — Protocol·InMemory·PG 시그니처 일치."""
    from ai.db.repositories.detection_store import DetectionStore, PgDetectionStore

    expected = inspect.signature(DetectionStore.load_feature_weeks)
    assert inspect.signature(InMemoryDetectionStore.load_feature_weeks) == expected
    assert inspect.signature(PgDetectionStore.load_feature_weeks) == expected
