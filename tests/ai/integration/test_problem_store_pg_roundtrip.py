"""`PgProblemItemStore` 실 PG 왕복 — 09 §2-20 해소 증명 (A 판정 2026-08-08).

§2-20이 막은 것은 *"계약이 무손실 왕복을 요구하는데 스키마가 그걸 담을 수 없다"* 였다.
🔴 **그 해소는 스키마 diff가 아니라 실 PG 왕복이 증명한다** — 모델만 고치고 왕복을 안 재면
"되겠지"가 그대로 남고, §2-20이 결손 12건을 낳은 이유가 정확히 「되겠지」였다.

`integration` 마커라 기본 실행에서 빠지고 CI `integration-pg`(postgres:16) 잡이 본다
(99 ⑫). PG 미가용이면 skip — 관례는 `test_probe_pg_roundtrip.py`와 같다.

여기서 재는 것 넷:

1. **무손실 왕복** — 결손 12건 중 컬럼으로 못 살리던 축(`candidate_ref`·`attempt_no`·
   `failure_detail`·`difficulty_band`·`review_reason`·`evidence`)이 그대로 돌아온다.
2. **조건 ⓑ** — 「스냅숏에서 유도한 값 == 파생 컬럼」을 **실 행**으로 단정한다.
3. **본문 없는 슬롯** — `item=None`인 행이 실제로 INSERT되고 다시 읽힌다(결정 ③ ⓒ).
4. **테넌트 격리** — 남의 테넌트 저장소로는 그 행이 안 보이고, 남의 세트에는 못 심는다.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from uuid import UUID

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from ai.contracts.problem_generation import (
    Answer,
    Choice,
    DifficultyBand,
    EvidenceAnchor,
    EvidenceKind,
    GeneratedItem,
    ItemResult,
    ProblemFailureReason,
    ProblemItemStatus,
    ReviewReason,
)
from ai.contracts.taxonomy import AreaTag, ItemFormat, TypeTag
from ai.problem_generation.domain.identity import problem_item_id

pytestmark = pytest.mark.integration

_NOW = datetime(2026, 8, 8, tzinfo=UTC)
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
        pytest.skip("실 PG 미가용 — docker compose -f compose.dev.yml up (99 ⑫)")


async def _make_problem_set(
    sm: async_sessionmaker[AsyncSession], *, tenant: str
) -> UUID:
    """ai_run → problem_set 부모를 만들고 set_id를 반환한다(FK 부모는 호출측 책임)."""
    from ai.db.models import AiRun, ProblemSet

    set_id, execution_id = uuid.uuid4(), uuid.uuid4()
    async with sm() as session, session.begin():
        session.add(
            AiRun(
                execution_id=execution_id,
                tenant_id=tenant,
                capability="problem_generation",
                pipeline_version="0.1.0",
                engine_version="problem-generation-0.1",
                schema_version="0.1",
                contract_version="0.1",
                input_snapshot_hash="h",
                created_at=_NOW,
            )
        )
        session.add(
            ProblemSet(
                id=set_id,
                run_id=execution_id,
                tenant_id=tenant,
                target_kind="student",
                target_ref="stu_1",
                target_source="weakness_map",
                request={},
                status="completed",
                diagnostic_purpose=False,
                created_at=_NOW,
            )
        )
    return set_id


def _item() -> GeneratedItem:
    return GeneratedItem(
        area_tag=AreaTag.LANGUAGE,
        type_tag=TypeTag.CONCEPT,
        item_format=ItemFormat.MCQ,
        skill_node_id="grammar.node-1",
        stem="다음 중 옳은 것을 고르시오.",
        choices=tuple(
            Choice(no=no, text=f"선지 {no}", why_wrong=None if no == 1 else f"오답 {no}")
            for no in range(1, 6)
        ),
        answer=Answer(correct_no=1),
        rationale="비공개 해설 원문",
        evidence=(
            EvidenceAnchor(kind=EvidenceKind.GRAMMAR_RULE, ref="grammar:rule-1"),
            EvidenceAnchor(
                kind=EvidenceKind.DICT_ENTRY, ref="stdict:12345", quote=None
            ),
        ),
    )


def _result(item_id: UUID) -> ItemResult:
    """🔴 §2-20이 「컬럼으로 못 살린다」고 센 축을 전부 채운 결과다."""
    return ItemResult(
        item_id=item_id,
        status=ProblemItemStatus.NEEDS_REVIEW,
        attempt_no=3,
        failure_detail=None,
        difficulty_est=0.62,
        difficulty_band=DifficultyBand.HIGH,
        review_reason=ReviewReason.DIFFICULTY_BAND_MISMATCH,
    )


def test_save_get_roundtrip_keeps_every_axis_the_columns_could_not() -> None:
    """무손실 왕복 — 결손 12건이 스냅숏으로 돌아온다."""
    tenant = f"t_{uuid.uuid4().hex[:8]}"

    async def scenario(sm: async_sessionmaker[AsyncSession]) -> None:
        from ai.db.repositories.problem_store import PgProblemItemStore

        set_id = await _make_problem_set(sm, tenant=tenant)
        store = PgProblemItemStore(sessionmaker=sm, tenant_id=tenant)
        item_id = problem_item_id(set_id, 2)
        candidate_ref = f"item-candidate:{set_id}:2:3"

        saved = await store.save(
            set_id=set_id,
            slot_index=2,
            result=_result(item_id),
            candidate_ref=candidate_ref,
            item=_item(),
        )
        loaded = await store.get(set_id, 2)

        assert loaded == saved
        # 컬럼이 없어서 잃던 축들(§2-20 #6·#7·#9·#10·#11).
        assert loaded.candidate_ref == candidate_ref
        assert loaded.result.attempt_no == 3
        assert loaded.result.difficulty_band is DifficultyBand.HIGH
        assert loaded.result.review_reason is ReviewReason.DIFFICULTY_BAND_MISMATCH
        assert loaded.item is not None
        assert len(loaded.item.evidence) == 2
        assert loaded.item.evidence[0].ref == "grammar:rule-1"

    _run(scenario)


def test_derived_columns_match_the_snapshot() -> None:
    """🔴 조건 ⓑ — 「스냅숏에서 유도한 값 == 파생 컬럼」을 실 행으로 단정한다."""
    tenant = f"t_{uuid.uuid4().hex[:8]}"

    async def scenario(sm: async_sessionmaker[AsyncSession]) -> None:
        from ai.db.models import ProblemItem
        from ai.db.repositories.problem_store import (
            PgProblemItemStore,
            problem_item_projection,
        )
        from ai.problem_generation.domain.models import StoredProblemItem

        set_id = await _make_problem_set(sm, tenant=tenant)
        store = PgProblemItemStore(sessionmaker=sm, tenant_id=tenant)
        await store.save(
            set_id=set_id,
            slot_index=0,
            result=_result(problem_item_id(set_id, 0)),
            candidate_ref=f"item-candidate:{set_id}:0:3",
            item=_item(),
        )

        async with sm() as session:
            row = (
                await session.execute(
                    select(ProblemItem).where(ProblemItem.set_id == set_id)
                )
            ).scalar_one()
            # 정본에서 다시 유도한 값과 컬럼에 앉은 값을 대조한다.
            expected = problem_item_projection(
                StoredProblemItem.model_validate(row.snapshot)
            )
            actual = {name: getattr(row, name) for name in expected}

        assert actual == expected
        # 파생이 아니라고 선언한 축은 저장소가 정한 값 그대로다.
        assert actual["review_badge"] is True

    _run(scenario)


def test_a_slot_without_a_body_is_a_real_row() -> None:
    """본문 없는 슬롯이 INSERT되고 다시 읽힌다 — 종전 스키마에서는 만들 수 없던 행이다."""
    tenant = f"t_{uuid.uuid4().hex[:8]}"

    async def scenario(sm: async_sessionmaker[AsyncSession]) -> None:
        from ai.db.repositories.problem_store import PgProblemItemStore

        set_id = await _make_problem_set(sm, tenant=tenant)
        store = PgProblemItemStore(sessionmaker=sm, tenant_id=tenant)
        item_id = problem_item_id(set_id, 1)

        saved = await store.save(
            set_id=set_id,
            slot_index=1,
            result=ItemResult(
                item_id=item_id,
                status=ProblemItemStatus.VERIFICATION_UNAVAILABLE,
                attempt_no=2,
                failure_reason=ProblemFailureReason.SOURCE_UNVERIFIED,
                failure_detail="어휘 사전 조회 실패",
            ),
            candidate_ref=None,
            item=None,
        )
        loaded = await store.get(set_id, 1)

        assert loaded == saved
        assert loaded.item is None
        # 🔴 폐기 사유 컬럼 오버로딩 없이 살아 돌아온다(§2-20 #12).
        assert loaded.result.failure_reason is ProblemFailureReason.SOURCE_UNVERIFIED
        assert loaded.result.failure_detail == "어휘 사전 조회 실패"

    _run(scenario)


def test_same_slot_twice_is_idempotent_and_conflicts_on_change() -> None:
    """멱등은 결정론 PK로 선다 — 같은 값은 통과, 다른 값은 `ImmutableStoreConflict`."""
    tenant = f"t_{uuid.uuid4().hex[:8]}"

    async def scenario(sm: async_sessionmaker[AsyncSession]) -> None:
        from ai.db.repositories.problem_store import PgProblemItemStore
        from ai.problem_generation.application.ports import ImmutableStoreConflict

        set_id = await _make_problem_set(sm, tenant=tenant)
        store = PgProblemItemStore(sessionmaker=sm, tenant_id=tenant)
        item_id = problem_item_id(set_id, 4)
        args = {
            "set_id": set_id,
            "slot_index": 4,
            "result": _result(item_id),
            "candidate_ref": f"item-candidate:{set_id}:4:3",
            "item": _item(),
        }

        first = await store.save(**args)  # type: ignore[arg-type]
        again = await store.save(**args)  # type: ignore[arg-type]
        assert again == first

        with pytest.raises(ImmutableStoreConflict):
            await store.save(
                set_id=set_id,
                slot_index=4,
                result=ItemResult(
                    item_id=item_id,
                    status=ProblemItemStatus.VERIFIED,
                    attempt_no=1,
                    difficulty_est=0.9,
                    difficulty_band=DifficultyBand.LOW,
                ),
                candidate_ref=f"item-candidate:{set_id}:4:1",
                item=_item(),
            )

    _run(scenario)


def test_another_tenant_can_neither_read_nor_write_the_slot() -> None:
    """🔴 테넌트 격리 — 부모 조인이 실제로 거른다(§2-20.3)."""
    mine = f"t_{uuid.uuid4().hex[:8]}"
    theirs = f"t_{uuid.uuid4().hex[:8]}"

    async def scenario(sm: async_sessionmaker[AsyncSession]) -> None:
        from ai.db.repositories.problem_store import PgProblemItemStore

        set_id = await _make_problem_set(sm, tenant=mine)
        owner = PgProblemItemStore(sessionmaker=sm, tenant_id=mine)
        intruder = PgProblemItemStore(sessionmaker=sm, tenant_id=theirs)
        await owner.save(
            set_id=set_id,
            slot_index=0,
            result=_result(problem_item_id(set_id, 0)),
            candidate_ref=f"item-candidate:{set_id}:0:3",
            item=_item(),
        )

        # 읽기 — 남의 행은 "없는 행"이다.
        with pytest.raises(LookupError):
            await intruder.get(set_id, 0)
        # 쓰기 — FK만으로는 안 막히는 자리라 부모 테넌트를 따로 본다.
        # ⚠ 계약상 유효한 레코드로 친다 — 계약 위반으로 먼저 막히면 격리를 안 잰 것이 된다.
        with pytest.raises(LookupError):
            await intruder.save(
                set_id=set_id,
                slot_index=9,
                result=ItemResult(
                    item_id=problem_item_id(set_id, 9),
                    status=ProblemItemStatus.VERIFICATION_UNAVAILABLE,
                    attempt_no=1,
                ),
                candidate_ref=None,
                item=None,
            )
        # 주인은 그대로 읽는다 — 격리가 자기 경로를 막지 않는다.
        assert (await owner.get(set_id, 0)).slot_index == 0

    _run(scenario)
