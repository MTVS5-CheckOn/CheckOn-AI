"""`PgProblemItemStore` 실 PG 왕복 — 09 §2-20 해소 증명 (A 판정 2026-08-08).

§2-20이 막은 것은 *"계약이 무손실 왕복을 요구하는데 스키마가 그걸 담을 수 없다"* 였다.
🔴 **그 해소는 스키마 diff가 아니라 실 PG 왕복이 증명한다** — 모델만 고치고 왕복을 안 재면
"되겠지"가 그대로 남고, §2-20이 결손 12건을 낳은 이유가 정확히 「되겠지」였다.

`integration` 마커라 기본 실행에서 빠지고 PR 전 로컬 검증의 실 PG 단계가 본다
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
from typing import Any
from uuid import UUID

import pytest
from first_sql_barrier import (
    FirstSqlBarrierSession,
    RecordingSession,
    barrier_sessionmaker,
    wait_until_parked,
)
from pg_hint import pg_unavailable
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
# 정상 실 PG 4쓰기보다 충분히 길고, CI 잡 전체가 매달리기 전에는 확실히 끊는 상한이다.
_CONCURRENT_SAVE_TIMEOUT_SECONDS = 15.0


def _barrier_sessions(
    sessions: async_sessionmaker[AsyncSession], barrier: asyncio.Barrier
) -> Callable[[], Any]:
    """🔴 **공용 프록시를 쓴다** — 종전엔 counsel 경합 테스트와 **같은 15줄을 각자** 들고
    있었다(99 #02). 감싸는 대상만 여기서 정한다."""
    return barrier_sessionmaker(sessions, barrier)


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


def test_the_first_problem_item_sql_waits_for_all_writers() -> None:
    """🔴 **절단 가드 — 프록시가 네 writer의 「첫 SQL」을 같은 배리어에 세우는가.**

    ⚠ **종전 가드는 `asyncio.Barrier`만 봤다** — `FirstSqlBarrierSession.execute()`의
    `await barrier.wait()`를 **지워도 통과**했다(로그 85 계열).
    ⚠ **이 가드가 증명하는 것은 딱 여기까지다** — 최초 저장 경합의 승자/패자 판정은
    **아래 실 PG 검사**가 증명한다. PG 없이 돈다.
    """

    async def scenario() -> None:
        party = 4
        barrier = asyncio.Barrier(party)
        inners = [RecordingSession() for _ in range(party)]
        proxies = [FirstSqlBarrierSession(inner, barrier) for inner in inners]

        early: list[asyncio.Task[Any]] = [
            asyncio.ensure_future(proxy.execute(f"SQL-{index}"))
            for index, proxy in enumerate(proxies[:-1])
        ]
        await wait_until_parked(barrier, early, expected=party - 1)

        #: 🔴 **셋이 서 있어도 내부 SQL은 0회여야 한다.**
        assert barrier.n_waiting == party - 1, "세 writer가 배리어 앞에 안 섰다"
        assert sum(i.execute_calls for i in inners) == 0, (
            "party가 다 오기 전에 실제 SQL이 나갔다 — 겹침이 안 만들어진다"
        )

        last: asyncio.Task[Any] = asyncio.ensure_future(proxies[-1].execute("SQL-last"))
        async with asyncio.timeout(2.0):
            await asyncio.gather(*early, last)

        assert [i.execute_calls for i in inners] == [1] * party

    asyncio.run(scenario())


def test_concurrent_first_writes_lose_with_the_contract_error_not_a_driver_error() -> None:
    """🔴 같은 슬롯을 **동시에** 처음 쓰면 진 쪽이 계약 오류로 진다 — 500이 아니라.

    앞의 멱등 테스트는 두 저장을 **차례로** 부른다. 그건 「이미 있는 행」 경로라 최초 저장이
    겹치는 창을 안 지난다. 종전 구현은 `SELECT`(없다) → `INSERT`가 갈라져 있어서 동시에
    치면 **둘 다 「없다」를 보고 둘 다 INSERT**했고, 진 쪽은 계약이 정한
    `ImmutableStoreConflict`가 아니라 asyncpg `UniqueViolation`(23505)이 `IntegrityError`로
    올라와 **핸들러 없이 500**이 됐다.

    ⚠ **재는 것은 「하나만 이긴다」가 아니라 「진 쪽이 무엇으로 지는가」다.** 종전 코드도
    행은 하나만 남았다 — 갈린 것은 **밖으로 나가는 오류의 부류**뿐이다. 행 개수만 세면
    이 테스트는 옛 코드에서도 통과한다.
    """
    tenant = f"t_{uuid.uuid4().hex[:8]}"

    async def scenario(sm: async_sessionmaker[AsyncSession]) -> None:
        from ai.db.models import ProblemItem
        from ai.db.repositories.problem_store import PgProblemItemStore
        from ai.problem_generation.application.ports import ImmutableStoreConflict
        from ai.problem_generation.domain.models import StoredProblemItem

        set_id = await _make_problem_set(sm, tenant=tenant)
        item_id = problem_item_id(set_id, 7)
        barrier = asyncio.Barrier(4)
        # 저장소 인스턴스를 따로 준다 — 워커가 갈리는 상황이 이 형태다(세션도 갈린다).
        # `return_exceptions=True`는 도착한 태스크의 예외만 값으로 바꾼다. 한 writer가 첫
        # execute 전에 죽어 party가 모자라면 나머지 wait는 끝나지 않으므로 시간 상한이 필요하다.
        async with asyncio.timeout(_CONCURRENT_SAVE_TIMEOUT_SECONDS):
            outcomes = await asyncio.gather(
                *(
                    PgProblemItemStore(
                        sessionmaker=_barrier_sessions(  # type: ignore[arg-type]
                            sm, barrier
                        ),
                        tenant_id=tenant,
                    ).save(
                        set_id=set_id,
                        slot_index=7,
                        result=ItemResult(
                            item_id=item_id,
                            status=ProblemItemStatus.NEEDS_REVIEW,
                            attempt_no=1,
                            difficulty_est=0.5,
                            difficulty_band=DifficultyBand.MEDIUM,
                            review_reason=ReviewReason.DIFFICULTY_BAND_MISMATCH,
                        ),
                        # 🔴 경쟁자마다 **다른 값**이다 — 같은 값이면 진 쪽도 정상 반환이라
                        #    오류 부류가 안 드러난다. 가르는 축을 `candidate_ref`로 둔 것은
                        #    `attempt_no`가 재생성 상한(≤3 · 불변식 6)에 묶여 있어서다.
                        candidate_ref=f"item-candidate:{set_id}:7:1:{rival}",
                        item=_item(),
                    )
                    for rival in range(4)
                ),
                return_exceptions=True,
            )

        won = [one for one in outcomes if isinstance(one, StoredProblemItem)]
        lost = [one for one in outcomes if isinstance(one, BaseException)]
        assert len(won) == 1, f"최초 저장이 둘 이상 성공했다: {outcomes}"
        # 🔴 이 단정이 이 테스트의 전부다 — 진 쪽은 전부 계약 오류여야 한다.
        assert [type(one) for one in lost] == [ImmutableStoreConflict] * 3, (
            f"진 쪽이 계약 밖 오류로 샜다: {[repr(one) for one in lost]}"
        )

        async with sm() as session:
            rows = (
                (
                    await session.execute(
                        select(ProblemItem).where(ProblemItem.set_id == set_id)
                    )
                )
                .scalars()
                .all()
            )
        assert len(rows) == 1
        # 이긴 값이 그대로 앉아 있다 — 진 쪽이 덮어쓰지 않았다.
        assert StoredProblemItem.model_validate(rows[0].snapshot) == won[0]

    _run(scenario)


def test_concurrent_identical_writes_all_return_the_same_record() -> None:
    """같은 값을 동시에 쓰면 **전부 성공**하고 같은 레코드를 받는다 — 멱등의 동시 판이다.

    ⚠ 재시도·재개가 겹치는 실제 형태가 이쪽이다(`recover_expired` 재진입). 여기서
    `ImmutableStoreConflict`가 나면 **같은 값을 쓰는 재시도가 실패로 보이게** 된다.
    """
    tenant = f"t_{uuid.uuid4().hex[:8]}"

    async def scenario(sm: async_sessionmaker[AsyncSession]) -> None:
        from ai.db.repositories.problem_store import PgProblemItemStore

        set_id = await _make_problem_set(sm, tenant=tenant)
        barrier = asyncio.Barrier(4)
        args = {
            "set_id": set_id,
            "slot_index": 3,
            "result": _result(problem_item_id(set_id, 3)),
            "candidate_ref": f"item-candidate:{set_id}:3:3",
            "item": _item(),
        }
        async with asyncio.timeout(_CONCURRENT_SAVE_TIMEOUT_SECONDS):
            saved = await asyncio.gather(
                *(
                    PgProblemItemStore(
                        sessionmaker=_barrier_sessions(  # type: ignore[arg-type]
                            sm, barrier
                        ),
                        tenant_id=tenant,
                    ).save(**args)  # type: ignore[arg-type]
                    for _ in range(4)
                )
            )

        assert all(one == saved[0] for one in saved)

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
