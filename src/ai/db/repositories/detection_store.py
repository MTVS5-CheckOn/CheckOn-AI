"""감지 원장 저장·적재 — AI_RUN·SIGNAL·FEATURE_WEEK 축적 (D-② 커밋⑤).

사양: docs/06_erd.md(FEATURE_WEEK·AI_RUN·SIGNAL) · docs/part_a/09_detect_spec.md §2·§4.
소유: 박진희 (db.repositories · detection 적재).

**저장 실패 정책이 캐시와 다르다 (D-② 확정):**
- 멱등 캐시(idempotency.py)는 **fail-open** — 저장 실패를 삼킨다(best-effort, 요청 안 막음).
- 원장(이 파일)은 **fail-closed** — 산출물의 근거·재현 기록이라 저장 실패 시
  `LedgerWriteFailed`(500)로 요청을 실패시킨다. 백엔드가 재시도한다.

**축적 대상 = 파생물이다.** raw learning_events(record_id 원본)는 백엔드 DB 소유
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
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import Insert as PgInsert
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ai.contracts.detection import LearningEvent, Signal
from ai.contracts.execution import RunMetadata
from ai.db.models import FeatureWeek
from ai.db.models import Signal as SignalRow
from ai.db.models import SignalBrief as SignalBriefRow
from ai.db.repositories.idempotency import system_utc_now
from ai.db.repositories.run_store import ai_run_orm  # AI_RUN 매퍼 정본(불변식 8)
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

    async def load_feature_weeks(
        self, tenant_id: str, student_refs: Sequence[str], *, feature_version: str
    ) -> tuple[FeatureWeekRow, ...]:
        """축적 FEATURE_WEEK 조회(baseline read-path). 조회 실패는 fail-closed(500).

        `feature_version`은 **호출부가 주입한다** — 저장소 층이 detection 상수를 직접
        import하지 않는다(계산과 I/O 분리, 03 §1). 산식 개정 배포 중에는 같은
        (student, week)에 두 버전 행이 공존하므로(upsert 유니크 스코프에 버전이 있다)
        필터가 없으면 둘 다 로드돼 baseline이 흔들린다 — 같은 입력에 같은 출력이
        안 나오면 AI_RUN을 재현할 수 없다(불변식 8).
        """
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

    async def load_feature_weeks(
        self, tenant_id: str, student_refs: Sequence[str], *, feature_version: str
    ) -> tuple[FeatureWeekRow, ...]:
        """PG와 **같은 필터**다 — 두 구현이 갈리면 테스트가 거짓말한다."""
        refs = set(student_refs)
        rows = [
            row
            for (tenant, student, _week, version), row in self.feature_weeks.items()
            if tenant == tenant_id and student in refs and version == feature_version
        ]
        return tuple(sorted(rows, key=lambda row: (row.student_ref, row.week_start)))

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
                    session.add(ai_run_orm(run))
                    for signal in ledger.signals:
                        #: 🔴 행 id 를 여기서 잡는다 — `signal_brief.signal_ref` 가 이 값을
                        #: FK 로 참조한다. 같은 트랜잭션이라 SQLAlchemy 가 의존 순서대로 넣는다.
                        signal_id = self._new_id()
                        session.add(_signal_orm(signal, run, signal_id, self._clock()))
                        session.add(
                            _signal_brief_orm(signal, run, signal_id, self._new_id())
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

    async def load_feature_weeks(
        self, tenant_id: str, student_refs: Sequence[str], *, feature_version: str
    ) -> tuple[FeatureWeekRow, ...]:
        if not student_refs:
            return ()
        stmt = (
            select(FeatureWeek)
            .where(
                FeatureWeek.tenant_id == tenant_id,
                FeatureWeek.student_ref.in_(list(student_refs)),
                # 산식 개정 배포 중 두 버전 공존 → 현재 버전만(불변식 8). upsert 유니크
                # 스코프와 조회 스코프를 일치시킨다.
                FeatureWeek.feature_version == feature_version,
            )
            .order_by(FeatureWeek.student_ref, FeatureWeek.week_start)
        )
        try:
            async with self._sessionmaker() as session:
                records = (await session.execute(stmt)).scalars().all()
        except SQLAlchemyError as exc:
            # 조회 실패도 fail-closed — baseline이 반쪽이면 판정이 조용히 왜곡(미탐)된다.
            logger.error(
                "baseline 조회 실패 — fail-closed(500) tenant=%s", tenant_id, exc_info=True
            )
            raise LedgerWriteFailed("baseline 조회 실패", {"tenant_id": tenant_id}) from exc
        return tuple(_feature_week_row_from_orm(record) for record in records)


# ───────────────────────── ORM 매핑 (PG 전용) ─────────────────────────


def _feature_week_row_from_orm(orm: FeatureWeek) -> FeatureWeekRow:
    """FEATURE_WEEK ORM → FeatureWeekRow(조회분). 라우터가 WeekFeatures로 되살린다."""
    return FeatureWeekRow(
        student_ref=orm.student_ref,
        week_start=orm.week_start,
        segment=orm.segment,
        metrics=orm.metrics,
        feature_version=orm.feature_version,
    )


def _signal_orm(
    signal: Signal, run: RunMetadata, row_id: uuid.UUID, created_at: datetime
) -> SignalRow:
    """Signal(계약) → SIGNAL 행 — 코어 + **비교값 4필드**(99 #59·#60).

    🔴 **`evidence`는 여전히 안 남긴다.** `evidence_item` 테이블은 **쓰는 코드가 0건**이고
    (`db/models.py`의 `EvidenceItem` docstring) 컬럼도 안 열었다. `brief`는 `SIGNAL_BRIEF`
    소관이다.
    """
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
        metric=signal.metric,
        observed=_decimal_or_none(signal.observed),
        baseline=_decimal_or_none(signal.baseline),
        sample_size=signal.sample_size,
        created_at=created_at,
    )


def _signal_brief_orm(
    signal: Signal, run: RunMetadata, signal_id: uuid.UUID, row_id: uuid.UUID
) -> SignalBriefRow:
    """Signal.brief(계약) → SIGNAL_BRIEF 행 — 🔴 **브리핑 품질의 유일한 관측 자리**.

    🔴 **왜 이게 필요한가 (2026-08-14 실측):** 윈도우 AI 서버에서 브리핑 11건이 **전부**
    템플릿 폴백으로 나갔는데(실 LLM opt-in 이 `.env` 에서 안 읽혔다 · 99 #32) **원장에
    안 남아 폴백률을 셀 수 없었다.** `signal` 70행 · `signal_brief` **0행**이었다.

    ⚠ **`evidence_item` 과 성격이 다르다.** evidence 는 응답으로 백엔드에 가서 **거기
    어딘가엔 있다.** 🔴 **`gate_passed`·`fallback_used` 는 백엔드에 안 간다** — 응답
    계약(`Brief`)에는 있지만 백엔드가 저장하지 않는 우리 관측값이다. **여기 안 남기면
    어디에도 없다.**

    ━━ `llm_call_id` 를 왜 `None` 으로 두나 ━━

    🔴 **채울 수 없다 — 지어내지 않는다.** 두 가지가 막는다:

    1. **순서** — `LLM_CALL` 행은 `persist_ledger` **뒤에** 적재된다
       (`api/routers/detect.py`: `llm_call.run_id` 가 `ai_run.execution_id` 를 NOT NULL
       FK 로 참조해 AI_RUN 이 먼저 서야 한다). 여기서 FK 를 채우면 없는 행을 가리킨다.
    2. **대응** — 수집기는 **실행 단위**로 콜을 모은다. 「이 signal 의 brief 를 만든 콜」
       이라는 신호별 대응이 지금 계약에 없다.

    ⇒ 컬럼은 `nullable=True` 이고 `None` 으로 둔다. 채우려면 **콜↔신호 대응을 먼저**
    세워야 하고 그건 별건이다.
    """
    return SignalBriefRow(
        id=row_id,
        tenant_id=run.tenant_id,
        signal_ref=signal_id,
        brief_text=signal.brief.text,
        gate_passed=signal.brief.gate_passed,
        fallback_used=signal.brief.fallback_used,
        llm_call_id=None,
    )


def _decimal_or_none(value: float | None) -> Decimal | None:
    """`float | None` → `Numeric` 값. 🔴 **`if value`로 가르지 마라 — `0.0`이 접힌다.**

    `observed=0.0`은 **실제로 나온다**: 정답률 0%(전부 오답) · 활동 0건 · 제출 0건.
    그 값을 `None`으로 적으면 원장이 *"안 쟀다"* 로 읽혀 **「0이었다」와 「모른다」가
    합쳐진다** — 이 저장소가 반복해서 겪은 실패 형태다(99 #43·#54 계열).

    ⚠ `float` → `Numeric`은 **`Decimal(str(x))`가 이 파일의 관례**다(`score` 선례).
    `Decimal(x)`는 이진 부동소수 오차를 그대로 옮긴다.
    """
    return None if value is None else Decimal(str(value))


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
