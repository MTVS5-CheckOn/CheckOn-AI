"""**행이 없는 상태에서 두 쓰기가 겹치면** 어떻게 되는가 (99 #35 · #186 보완).

🔴 **`SELECT … FOR UPDATE`는 없는 행을 못 잠근다.** #186의 저장소는 「읽고-합치고-쓴다」인데,
최초 저장에서는 **읽을 행이 없어 잠글 대상도 없다** ⇒ 두 트랜잭션이 나란히
*"행이 없다"* 를 보고 **둘 다 INSERT**한다. `uq_counsel_draft_view_job`이 하나를 죽인다.

⚠ **#186의 순차 양방향 테스트는 이 경합을 구조적으로 못 본다** — 첫 쓰기가 **커밋된 뒤에**
둘째가 시작하므로 둘째는 항상 행을 본다. **순서를 바꿔 봐도 같은 축을 두 번 재는 것**이다.

🔴 **그리고 이건 정상 경로다** — POST 한 번이 뷰와 초안을 **둘 다** 쓴다. 지금은 `await`가
직렬화하지만 워커·라우터가 갈리거나 프로세스가 둘이면 바로 겹친다.

**결정론적으로 겹치게 한다** — `asyncio.gather()`만으로는 두 트랜잭션이 **같은 순간에 문 앞에**
선다는 보장이 없다. 세션 프록시가 **첫 SQL 문에서 배리어에 걸리게** 해서, 둘 다 첫 문을
지나기 전에는 아무도 진행하지 못하게 만든다. ⚠ 프로덕션 코드에 시험용 갈고리를 안 넣는다 —
**세션메이커를 감싼다.**
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any, Final

import pytest
from counsel_read_model_fixtures import draft_snapshot, view_snapshot
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from ai.contracts.agents import JobPhase
from ai.db.counsel_read_model import PgCounselDraftViewStore
from ai.db.models import CounselDraftView as CounselDraftViewRow
from ai.db.settings import get_db_settings

pytestmark = pytest.mark.integration

_TENANT: Final = "t_concurrent"
#: 🔴 각 방향을 여러 번 돈다 — 경합은 한 번에 안 드러날 수 있고, **한 번이라도** 유니크
#: 충돌이 나면 그건 결함이다(간헐이 아니라 **가능**이 판정 기준이다).
_ROUNDS: Final = 5


class _BarrierSession:
    """첫 SQL 문에서 배리어에 걸리는 세션 — **겹침을 결정론으로 만든다.**"""

    def __init__(self, session: AsyncSession, barrier: asyncio.Barrier) -> None:
        self._session = session
        self._barrier: asyncio.Barrier | None = barrier

    async def execute(self, *args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
        if self._barrier is not None:
            barrier, self._barrier = self._barrier, None
            await barrier.wait()
        return await self._session.execute(*args, **kwargs)

    def __getattr__(self, name: str) -> Any:  # noqa: ANN401
        return getattr(self._session, name)


def _barrier_sessions(
    engine: Any, barrier: asyncio.Barrier  # noqa: ANN401
) -> Callable[[], Any]:
    inner = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def factory() -> AsyncIterator[_BarrierSession]:
        async with inner() as session:
            yield _BarrierSession(session, barrier)

    return factory


type _Save = Callable[[PgCounselDraftViewStore, tuple[str, str]], Awaitable[None]]


async def _save_view(store: PgCounselDraftViewStore, key: tuple[str, str]) -> None:
    await store.save_view(key, snapshot=view_snapshot(key[1]))


async def _save_draft(store: PgCounselDraftViewStore, key: tuple[str, str]) -> None:
    await store.save_draft(key, snapshot=draft_snapshot())


async def _clean(sessions: async_sessionmaker[AsyncSession]) -> None:
    async with sessions() as session, session.begin():
        rows = (
            await session.execute(
                select(CounselDraftViewRow).where(
                    CounselDraftViewRow.tenant_id == _TENANT
                )
            )
        ).scalars()
        for row in rows:
            await session.delete(row)


async def _race(first: _Save, second: _Save) -> list[dict[str, Any]]:
    """같은 **새 키**에 두 최초 저장을 겹친다 — 라운드마다 결과 행을 돌려준다."""
    dsn = get_db_settings().database_url
    engine = create_async_engine(dsn, poolclass=NullPool)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    results: list[dict[str, Any]] = []
    try:
        await _clean(sessions)
        for _ in range(_ROUNDS):
            key = (_TENANT, f"job-{uuid.uuid4()}")
            barrier = asyncio.Barrier(2)
            stores = [
                PgCounselDraftViewStore(_barrier_sessions(engine, barrier))
                for _ in range(2)
            ]
            await asyncio.gather(first(stores[0], key), second(stores[1], key))
            async with sessions() as session:
                rows = (
                    await session.execute(
                        select(CounselDraftViewRow).where(
                            CounselDraftViewRow.tenant_id == _TENANT,
                            CounselDraftViewRow.job_id == key[1],
                        )
                    )
                ).scalars().all()
            results.append(
                {
                    "rows": len(rows),
                    "view": rows[0].view_snapshot if rows else None,
                    "draft": rows[0].draft_snapshot if rows else None,
                    "status": rows[0].status if rows else None,
                    "execution_id": rows[0].execution_id if rows else None,
                }
            )
        await _clean(sessions)
    finally:
        await engine.dispose()
    return results


def _run(first: _Save, second: _Save) -> list[dict[str, Any]]:
    try:
        return asyncio.run(_race(first, second))
    except Exception as exc:  # noqa: BLE001 — 접속 실패만 skip으로 가른다
        if "connect" in str(exc).lower() or "refused" in str(exc).lower():
            pytest.skip("실 PG 미가용 — docker compose up -d")
        raise


def test_the_barrier_really_overlaps_the_two_writers() -> None:
    """🔴 절단 가드 — 배리어가 안 걸리면 이 파일은 **직렬 저장을 두 번 재는 것**이다."""
    barrier = asyncio.Barrier(2)

    async def scenario() -> list[int]:
        order: list[int] = []

        async def leg(index: int) -> None:
            order.append(index)
            await barrier.wait()
            order.append(index + 10)

        await asyncio.gather(leg(0), leg(1))
        return order

    order = asyncio.run(scenario())
    assert set(order[:2]) == {0, 1}, f"둘 다 배리어 앞에 서지 않았다: {order}"
    assert set(order[2:]) == {10, 11}, f"배리어 뒤가 안 열렸다: {order}"


def test_view_and_draft_first_writes_converge_to_one_row() -> None:
    """🔴 **정상 경로다** — POST 한 번이 뷰와 초안을 둘 다 쓴다."""
    for round_index, result in enumerate(_run(_save_view, _save_draft)):
        assert result["rows"] == 1, f"{round_index}회차 행 수가 {result['rows']}다"
        assert result["view"] is not None, f"{round_index}회차에서 뷰가 사라졌다"
        assert result["draft"] is not None, f"{round_index}회차에서 초안이 사라졌다"
        assert result["status"] == JobPhase.SUCCEEDED.value
        #: 뷰가 살아 있으면 파생은 **뷰 기준**이다 — draft-only 판정이 이기면 안 된다.
        assert result["execution_id"] is not None, (
            f"{round_index}회차 파생이 draft-only 판정으로 덮였다"
        )


@pytest.mark.parametrize("side", ["view", "draft"])
def test_two_first_writes_on_the_same_side_converge(side: str) -> None:
    """같은 면을 동시에 최초 저장 — **승자 정책은 정하지 않는다.**

    ⚠ 최소 요구는 **무결성 오류 없이 유효한 한 행으로 수렴**하는 것이다. 어느 쓰기가
    남는지는 제품 판정이 필요한 축이라 여기서 임의로 못 박지 않는다.
    """
    save = _save_view if side == "view" else _save_draft
    other = "draft" if side == "view" else "view"
    for round_index, result in enumerate(_run(save, save)):
        assert result["rows"] == 1, f"{round_index}회차 행 수가 {result['rows']}다"
        assert result[side] is not None, f"{round_index}회차에서 {side}가 비었다"
        assert result[other] is None, f"{round_index}회차에서 안 쓴 면이 찼다"


def test_no_row_is_left_behind_by_a_race() -> None:
    """경합 뒤에도 **테넌트에 남는 행은 라운드 수만큼**이어야 한다(중복 행 없음)."""
    dsn = get_db_settings().database_url

    async def scenario() -> int:
        engine = create_async_engine(dsn, poolclass=NullPool)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        try:
            await _clean(sessions)
            key = (_TENANT, f"job-{uuid.uuid4()}")
            barrier = asyncio.Barrier(2)
            stores = [
                PgCounselDraftViewStore(_barrier_sessions(engine, barrier))
                for _ in range(2)
            ]
            await asyncio.gather(_save_view(stores[0], key), _save_draft(stores[1], key))
            async with sessions() as session:
                count = int(
                    (
                        await session.execute(
                            select(func.count())
                            .select_from(CounselDraftViewRow)
                            .where(CounselDraftViewRow.tenant_id == _TENANT)
                        )
                    ).scalar_one()
                )
            await _clean(sessions)
            return count
        finally:
            await engine.dispose()

    try:
        assert asyncio.run(scenario()) == 1
    except Exception as exc:  # noqa: BLE001
        if "connect" in str(exc).lower() or "refused" in str(exc).lower():
            pytest.skip("실 PG 미가용 — docker compose up -d")
        raise
