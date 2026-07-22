"""감지 원장 저장·적재 — AI_RUN·SIGNAL·FEATURE_WEEK 축적 (D-② 커밋⑤).

사양: docs/06_erd.md(FEATURE_WEEK·AI_RUN·SIGNAL) · docs/part_a/09_detect_spec.md §2·§4.
소유: 박진희 (db.repositories · detection 적재).

**저장 실패 정책이 캐시와 다르다 (D-② 확정):**
- 멱등 캐시(idempotency.py)는 **fail-open** — 저장 실패를 삼킨다(best-effort, 요청 안 막음).
- 원장(이 파일)은 **fail-closed** — 산출물의 근거·재현 기록이라 저장 실패 시
  `LedgerWriteFailed`(500)로 요청을 실패시킨다. 백엔드가 재시도한다.

**축적 대상 = 파생물이다.** raw learning_events(record_id 원본)는 백엔드 MySQL 소유
도메인 원본이라 AI PG에 저장하지 않는다(CLAUDE.md — 도메인 원본 테이블 금지). 대신
주차 피처(FEATURE_WEEK)를 upsert한다. record_id dedupe는 요청 이벤트에 대한 순수 로직
(재전송=정정, 최신 승리 + 로그)이며 엔진은 여전히 요청 구동이다(시그니처 무변경) —
요청만으로 이력이 충분하면 DB 없이도 동일하게 동작한다(데모·골든·초기 연동).

시간·id는 주입한다(datetime.now()/전역 random 직접 호출 금지 — 03_coding_rules §3).
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable, Sequence
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.dialects.postgresql import Insert as PgInsert
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ai.contracts.detection import LearningEvent, Signal
from ai.contracts.execution import RunMetadata
from ai.db.models import AiRun, FeatureWeek
from ai.db.models import Signal as SignalRow
from ai.db.repositories.idempotency import system_utc_now
from ai.runtime.errors import LedgerWriteFailed

logger = logging.getLogger(__name__)


# ───────────────────────── 순수 로직 (DB 무관) ─────────────────────────


class EventCorrection(BaseModel):
    """재전송된 정정 이벤트 1건 — 로그·감사용. 같은 record_id, 다른 내용."""

    model_config = ConfigDict(frozen=True)

    record_id: str
    detail: str


def dedupe_learning_events(
    events: Sequence[LearningEvent],
) -> tuple[tuple[LearningEvent, ...], tuple[EventCorrection, ...]]:
    """record_id로 dedupe — 같은 id 재등장은 최신(뒤에 온 것) 승리, 내용 다르면 정정 로그.

    강사의 채점 정정(재전송)이 정상 업무이므로 충돌 에러로 튕기지 않는다(09 §2). 최초
    등장 순서를 보존하되 값은 최신본으로 덮는다 — 결정론(같은 입력 → 같은 출력).
    """
    latest: dict[str, LearningEvent] = {}
    corrections: list[EventCorrection] = []
    for event in events:
        prior = latest.get(event.record_id)
        if prior is not None and prior != event:
            corrections.append(
                EventCorrection(
                    record_id=event.record_id,
                    detail="재전송 정정 — 최신 수신본으로 갱신",
                )
            )
        latest[event.record_id] = event
    return tuple(latest.values()), tuple(corrections)


class FeatureWeekRow(BaseModel):
    """FEATURE_WEEK 한 행의 적재 재료 — metrics는 파생 피처 스냅숏."""

    model_config = ConfigDict(frozen=True)

    student_ref: str = Field(min_length=1)
    week_start: date
    segment: str = Field(min_length=1)
    metrics: dict[str, Any]
    feature_version: str = Field(min_length=1)


class LedgerWrite(BaseModel):
    """원장 적재 한 묶음 — 한 번의 감지 실행이 남기는 것."""

    model_config = ConfigDict(frozen=True)

    run: RunMetadata
    signals: tuple[Signal, ...] = ()
    feature_weeks: tuple[FeatureWeekRow, ...] = ()


# ───────────────────────── 저장소 인터페이스 ─────────────────────────


@runtime_checkable
class DetectionStore(Protocol):
    """감지 원장 저장소 — 라우터는 이 타입에만 의존한다. 적재 실패는 fail-closed."""

    async def persist_ledger(self, ledger: LedgerWrite) -> None:
        """AI_RUN·SIGNAL·FEATURE_WEEK 적재. 실패 시 LedgerWriteFailed(500)를 올린다."""
        ...


class InMemoryDetectionStore:
    """프로세스 인메모리 구현 — 테스트·개발용(재시작 소실). memory 백엔드 기본값.

    FEATURE_WEEK upsert(주차 스코프)와 최신 승리 + 정정 로그를 PG와 동일 의미로 흉내낸다.
    """

    def __init__(self) -> None:
        self.runs: list[RunMetadata] = []
        self.signals: list[Signal] = []
        #: (tenant, student, week, version) → 행. upsert 스코프 = FEATURE_WEEK 유니크.
        self.feature_weeks: dict[tuple[str, str, date, str], FeatureWeekRow] = {}

    async def persist_ledger(self, ledger: LedgerWrite) -> None:
        tenant = ledger.run.tenant_id
        self.runs.append(ledger.run)
        self.signals.extend(ledger.signals)
        for row in ledger.feature_weeks:
            key = (tenant, row.student_ref, row.week_start, row.feature_version)
            prior = self.feature_weeks.get(key)
            if prior is not None and prior.metrics != row.metrics:
                logger.info(
                    "FEATURE_WEEK 갱신(재전송 정정) tenant=%s student=%s week=%s",
                    tenant,
                    row.student_ref,
                    row.week_start.isoformat(),
                )
            self.feature_weeks[key] = row  # 최신 승리

    def clear(self) -> None:
        self.runs.clear()
        self.signals.clear()
        self.feature_weeks.clear()


class PgDetectionStore:
    """PostgreSQL 원장 적재 — 한 트랜잭션에 AI_RUN·SIGNAL·FEATURE_WEEK를 쓴다.

    fail-closed: 어떤 SQLAlchemyError든 LedgerWriteFailed(500)로 올려 요청을 실패시킨다
    (원장은 재현·근거라 저장 실패를 삼키지 않는다). 실 PG 왕복은 docker PG(99 ⑫) 후
    integration 마커로 붙인다 — 이 커밋은 fail-closed 경로를 단위로 검증한다.
    """

    def __init__(
        self,
        *,
        sessionmaker: async_sessionmaker[AsyncSession],
        new_id: Callable[[], uuid.UUID] = uuid.uuid4,
        clock: Callable[[], datetime] = system_utc_now,
    ) -> None:
        self._sessionmaker = sessionmaker
        self._new_id = new_id
        self._clock = clock

    async def persist_ledger(self, ledger: LedgerWrite) -> None:
        run = ledger.run
        try:
            async with self._sessionmaker() as session:
                async with session.begin():
                    session.add(_ai_run_orm(run))
                    for signal in ledger.signals:
                        session.add(
                            _signal_orm(signal, run, self._new_id(), self._clock())
                        )
                    for row in ledger.feature_weeks:
                        await session.execute(
                            _feature_week_upsert(row, run, self._new_id())
                        )
        except SQLAlchemyError as exc:
            # fail-closed — 캐시(fail-open)와 정반대. 요청을 실패시켜 재시도를 유도.
            logger.error(
                "원장 적재 실패 — fail-closed(500) execution_id=%s tenant=%s",
                run.execution_id,
                run.tenant_id,
                exc_info=True,
            )
            raise LedgerWriteFailed(
                "원장 적재 실패", {"execution_id": str(run.execution_id)}
            ) from exc


# ───────────────────────── ORM 매핑 (PG 전용) ─────────────────────────


def _ai_run_orm(run: RunMetadata) -> AiRun:
    """RunMetadata → AI_RUN 행. 버전 세트 10종을 1:1로 옮긴다(대조 테스트가 강제)."""
    gen = run.generation_params
    return AiRun(
        execution_id=run.execution_id,
        tenant_id=run.tenant_id,
        capability=run.capability.value,
        pipeline_version=run.pipeline_version,
        engine_version=run.engine_version,
        threshold_version=run.threshold_version,
        prompt_version=run.prompt_version,
        schema_version=run.schema_version,
        contract_version=run.contract_version,
        graph_version=run.graph_version,
        taxonomy_version=run.taxonomy_version,
        verify_config_version=run.verify_config_version,
        difficulty_calib_version=run.difficulty_calib_version,
        model_provider=run.model_provider,
        model_name=run.model_name,
        generation_params=gen.model_dump(mode="json") if gen is not None else None,
        input_snapshot_hash=run.input_snapshot_hash,
        created_at=run.created_at,
    )


def _signal_orm(
    signal: Signal, run: RunMetadata, row_id: uuid.UUID, created_at: datetime
) -> SignalRow:
    """Signal(계약) → SIGNAL 행. brief·evidence는 별 테이블(SIGNAL_BRIEF·EVIDENCE_ITEM)
    소관 — 이 커밋은 SIGNAL 코어만 적재한다(후속 확장)."""
    return SignalRow(
        id=row_id,
        run_id=run.execution_id,
        tenant_id=run.tenant_id,
        student_ref=signal.student_ref,
        rule_id=signal.rule_id.value,
        signal_type=signal.signal_type.value,
        display_label=signal.display_label,
        lifecycle=signal.lifecycle.value,
        score=Decimal(str(signal.score)),
        rank=signal.rank,
        created_at=created_at,
    )


def _feature_week_upsert(
    row: FeatureWeekRow, run: RunMetadata, row_id: uuid.UUID
) -> PgInsert:
    """FEATURE_WEEK upsert — 유니크 스코프 충돌 시 metrics·segment 갱신(최신 승리)."""
    values = {
        "id": row_id,
        "tenant_id": run.tenant_id,
        "student_ref": row.student_ref,
        "week_start": row.week_start,
        "segment": row.segment,
        "metrics": row.metrics,
        "feature_version": row.feature_version,
        "created_at": run.created_at,
    }
    stmt = pg_insert(FeatureWeek).values(**values)
    return stmt.on_conflict_do_update(
        constraint="uq_feature_week_scope",
        set_={"segment": row.segment, "metrics": row.metrics},
    )
