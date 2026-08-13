"""실 PG 왕복 — 저장 계층 통합 (D-② 커밋⑥ · 99 안건 ⑫).

**테스트 DB 전략(D-② 확정):**
- 단위·계약: fake 저장소(InMemory) + 마이그레이션 offline SQL 왕복 — DB 없이 CI 통과.
- 통합(이 파일): 실 PG 왕복. sqlite는 쓰지 않는다(JSONB·Uuid·on_conflict 미지원 —
  프로덕션과 다른 DB로 통과시키면 거짓 초록). docker PG는 CI 여건 확인 후 도입(⑫).

`integration` 마커로 기본 실행 제외. PG 미가용이면 skip. 각 테스트는 전용 NullPool 엔진 +
단일 이벤트 루프로 돈다 — 전역 lru_cache 엔진을 여러 asyncio.run에서 쓰면 커넥션이 다른
루프에 묶인다(프로덕션 uvicorn은 단일 루프라 무관·테스트 아티팩트). 스키마는 create_all로
자체 준비(순서 독립).
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

import pytest
from pg_hint import pg_unavailable
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

pytestmark = pytest.mark.integration

_Scenario = Callable[[async_sessionmaker[AsyncSession]], Awaitable[None]]


async def _with_pg(scenario: _Scenario) -> str:
    from ai.db.models import Base
    from ai.db.settings import get_db_settings

    engine = create_async_engine(get_db_settings().database_url, poolclass=NullPool)
    try:
        try:
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
        except Exception:  # noqa: BLE001 — 접속 불가 → skip
            return "skip"
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)  # checkfirst=True
        await scenario(async_sessionmaker(engine, expire_on_commit=False))
    finally:
        await engine.dispose()
    return "ok"


def _run(scenario: _Scenario) -> None:
    if asyncio.run(_with_pg(scenario)) == "skip":
        pytest.skip(pg_unavailable("(99 ⑫)"))


def test_idempotency_roundtrip_persists() -> None:
    """put → get 왕복이 저장분을 재반환(스코프·TTL 창 안)."""
    scope = {
        "tenant_id": f"t_{uuid.uuid4().hex[:8]}",
        "endpoint": "POST /v1/detect",
        "idempotency_key": "k1",
    }
    body: dict[str, Any] = {"data": {"signals": []}, "error": None, "meta": {}}

    async def scenario(sm: async_sessionmaker[AsyncSession]) -> None:
        from ai.db.repositories.idempotency import PgIdempotencyStore

        store = PgIdempotencyStore(sessionmaker=sm)
        await store.put(**scope, snapshot_hash="h1", response_body=body)
        hit = await store.get(**scope)
        assert hit is not None
        assert hit.snapshot_hash == "h1"
        assert hit.response_body == body

    _run(scenario)


def test_ledger_roundtrip_persists_ai_run() -> None:
    """원장 적재가 AI_RUN을 남긴다(fail-closed 경로의 정상 케이스) + 재조회 확인."""
    tenant = f"t_{uuid.uuid4().hex[:8]}"

    async def scenario(sm: async_sessionmaker[AsyncSession]) -> None:
        from sqlalchemy import func, select

        from ai.contracts.execution import Capability, RunMetadata
        from ai.db.models import AiRun
        from ai.db.repositories.detection_store import LedgerWrite, PgDetectionStore

        run = RunMetadata(
            execution_id=uuid.uuid4(),
            tenant_id=tenant,
            capability=Capability.DETECTION,
            pipeline_version="0.1.0",
            engine_version="detection-rules-0.1",
            schema_version="0.1",
            contract_version="0.1",
            input_snapshot_hash="hash-1",
            created_at=datetime.now(UTC),
        )
        await PgDetectionStore(sessionmaker=sm).persist_ledger(LedgerWrite(run=run))
        async with sm() as session:
            count = await session.scalar(
                select(func.count()).select_from(AiRun).where(AiRun.tenant_id == tenant)
            )
        assert count == 1

    _run(scenario)


def test_ledger_roundtrip_persists_the_brief_next_to_its_signal() -> None:
    """🔴 SIGNAL 과 SIGNAL_BRIEF 가 **한 트랜잭션에 같이** 남고 FK 가 이어진다.

    ⚠ **오프라인 검사로는 이 축이 안 보인다** — `signal_brief.signal_ref` 는 FK 라
    실제 INSERT 순서가 틀리면 **실 PG 에서만** 터진다(99 #62 의 「마이그레이션이 정적
    검사에 안 보인다」와 같은 층).

    🔴 **폴백 하나 + 정상 하나**를 같이 넣는다 — 한쪽만 넣으면 상수로 짜도 통과한다.
    """
    tenant = f"t_{uuid.uuid4().hex[:8]}"

    async def scenario(sm: async_sessionmaker[AsyncSession]) -> None:
        from sqlalchemy import select

        from ai.contracts.detection import (
            DISPLAY_LABELS,
            Brief,
            EvidenceItem,
            EvidenceRole,
            Lifecycle,
            RuleId,
            Signal,
            SignalType,
        )
        from ai.contracts.execution import Capability, RunMetadata
        from ai.db.models import Signal as SignalRow
        from ai.db.models import SignalBrief
        from ai.db.repositories.detection_store import LedgerWrite, PgDetectionStore

        def _signal(n: int, brief: Brief) -> Signal:
            return Signal(
                signal_id=f"sig_{n}",
                student_ref=f"st_{n}",
                class_ref="cl_a1",
                rule_id=RuleId.R1,
                signal_type=SignalType.ACC_DROP,
                display_label=DISPLAY_LABELS[SignalType.ACC_DROP],
                score=0.5,
                rank=n,
                lifecycle=Lifecycle.NEW,
                brief=brief,
                evidence=(
                    EvidenceItem(
                        source_table="learning_event",
                        record_id=f"le_{n}",
                        summary="x",
                        role=EvidenceRole.TRIGGER,
                    ),
                ),
            )

        run = RunMetadata(
            execution_id=uuid.uuid4(),
            tenant_id=tenant,
            capability=Capability.DETECTION,
            pipeline_version="0.1.0",
            engine_version="detection-rules-0.1",
            schema_version="0.1",
            contract_version="0.1",
            input_snapshot_hash="hash-brief",
            created_at=datetime.now(UTC),
        )
        await PgDetectionStore(sessionmaker=sm).persist_ledger(
            LedgerWrite(
                run=run,
                signals=(
                    _signal(1, Brief(text="정상", gate_passed=True, fallback_used=False)),
                    _signal(2, Brief(text="폴백", gate_passed=False, fallback_used=True)),
                ),
            )
        )

        async with sm() as session:
            briefs = (
                await session.scalars(
                    select(SignalBrief).where(SignalBrief.tenant_id == tenant)
                )
            ).all()
            signal_ids = set(
                (
                    await session.scalars(
                        select(SignalRow.id).where(SignalRow.tenant_id == tenant)
                    )
                ).all()
            )

        assert len(briefs) == 2, briefs
        #: 🔴 FK 가 **실제로 그 신호 행**을 가리킨다 — 새로 뽑은 id 가 아니다.
        assert {b.signal_ref for b in briefs} == signal_ids
        assert {b.fallback_used for b in briefs} == {True, False}
        assert {(b.brief_text, b.gate_passed) for b in briefs} == {
            ("정상", True),
            ("폴백", False),
        }
        assert all(b.llm_call_id is None for b in briefs)

    _run(scenario)
