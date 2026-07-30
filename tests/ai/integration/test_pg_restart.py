"""실 PG 재시작 생존 — 이번 완결의 핵심 증명 (D-②b · 99 ⑫ 로컬분).

store_backend=pg에서 ① day1 적재 → ② "재시작"(새 store 인스턴스) → ③ day2 조회가
정상 판정 + 멱등 캐시도 재시작 생존하는지 검증한다. 재시작의 실체 = **데이터가 PG에
살아있음**이므로, 새 store 인스턴스가 같은 PG를 읽어 확인한다.

integration 마커라 기본 실행 제외. PG 미가용이면 skip. 전 과정을 단일 이벤트 루프 +
전용 NullPool 엔진으로 돌린다(async 엔진의 루프 교차 회피). 스키마는 이 테스트가 소유.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

pytestmark = pytest.mark.integration


def test_pg_survives_restart() -> None:
    outcome = asyncio.run(_run())
    if outcome == "skip":
        pytest.skip("실 PG 미가용 — docker compose -f compose.dev.yml up (99 ⑫)")


async def _run() -> str:
    from sqlalchemy import text

    from ai.api.routers.detect import _FEATURE_VERSION, _build_feature_weeks
    from ai.contracts.detection import SignalType
    from ai.contracts.execution import Capability, ExecutionContext, VersionSet
    from ai.db.models import Base
    from ai.db.repositories.detection_store import LedgerWrite, PgDetectionStore
    from ai.db.repositories.idempotency import PgIdempotencyStore
    from ai.db.settings import get_db_settings
    from ai.detection.engine import detect
    from ai.detection.features import week_features_from_metrics
    from ai.evaluation.fake_snapshot import (
        StudentPlan,
        alert_open,
        build_detect_request,
        take_single_week,
        take_week_range,
    )

    engine = create_async_engine(get_db_settings().database_url, poolclass=NullPool)
    try:
        try:
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
        except Exception:  # noqa: BLE001 — 접속 불가 → skip
            return "skip"

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
            await conn.run_sync(Base.metadata.create_all)

        sm = async_sessionmaker(engine, expire_on_commit=False)
        tenant = "t_restart"
        week0, week1 = "2026-07-06", "2026-07-13"
        plan = StudentPlan(
            student_ref="st_dec",
            class_ref="cl_a1",
            weeks=11,
            accuracy=tuple(round(0.9 - 0.05 * i, 4) for i in range(11)),
        )
        full = build_detect_request(week_start=week1, seed=5, students=[plan])
        day1 = take_week_range(full, week_start=week0, weeks_back=10)

        run = ExecutionContext(
            execution_id=UUID("00000000-0000-4000-8000-0000000000d1"),
            tenant_id=tenant,
            capability=Capability.DETECTION,
            input_snapshot_hash="hash-day1",
            versions=VersionSet(
                pipeline_version="0.1",
                engine_version="0.1",
                schema_version="0.1",
                contract_version="0.1",
            ),
        ).to_run_metadata(created_at=datetime.now(UTC))
        cached_body = {"data": {"marker": "day1"}, "error": None, "meta": {}}
        key = f"{tenant}:{week0}"

        # ── 프로세스 1: day1 적재 ──
        await PgDetectionStore(sessionmaker=sm).persist_ledger(
            LedgerWrite(run=run, feature_weeks=_build_feature_weeks(day1))
        )
        await PgIdempotencyStore(sessionmaker=sm).put(
            tenant_id=tenant,
            endpoint="POST /v1/detect",
            idempotency_key=key,
            snapshot_hash="hash-day1",
            response_body=cached_body,
        )

        # ── 재시작: 새 store 인스턴스(같은 PG) ──
        detection_b = PgDetectionStore(sessionmaker=sm)
        idem_b = PgIdempotencyStore(sessionmaker=sm)

        # ① 멱등 캐시 생존
        hit = await idem_b.get(
            tenant_id=tenant, endpoint="POST /v1/detect", idempotency_key=key
        )
        assert hit is not None and hit.response_body == cached_body

        # ② read-path 생존 — PG 축적분으로 baseline 구성 → 판정
        # feature_version은 호출부가 주입한다(4-5) — 라우터와 같은 상수를 쓴다.
        rows = await detection_b.load_feature_weeks(
            tenant, ["st_dec"], feature_version=_FEATURE_VERSION
        )
        assert len(rows) == 10  # day1 10주분 PG 생존
        stored = {
            "st_dec": [
                week_features_from_metrics(row.week_start, row.metrics) for row in rows
            ]
        }
        day2 = take_single_week(full, week_start=week1).model_copy(
            update={"alert_context": (alert_open("st_dec", SignalType.ACC_DROP),)}
        )
        signals = detect(day2, stored_features=stored).signals
        assert {
            (s.student_ref, s.signal_type.value, s.lifecycle.value) for s in signals
        } == {("st_dec", "acc_drop", "ongoing")}

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
    finally:
        await engine.dispose()
    return "ok"
