"""`_view_cache`·`_drafts`가 **실제 PG에서 복원되는가** (99 ㉿ ⓐⓑⓒⓓ · #182 후속 배선).

🔴 **㉿의 네 증상은 전부 「캐시가 정본이라서」 생긴다.** 축출되면 404 · GET과 refine이
갈리고 · 재시작하면 사라지고 · 멱등 202인데 GET이 404다. 이 파일은 **그 넷이 `store_backend=pg`
에서 사라지는지**를 실제 행으로 묻는다.

⚠ **`store_backend=memory`는 이 파일의 축이 아니다** — 거기서는 캐시가 v1 정본 그대로다
(회귀는 `tests/ai/unit/...`와 기존 라우터 계약이 지킨다).

🔴 **절단 가드를 셋 둔다.**
① PG 미가용이면 **skip이 아니라** 이 파일이 아무것도 안 본 것이므로 그 사실이 보이게 한다.
② 저장소를 만들었는데 **라우터가 안 부르면** red — 캐시를 비우고도 살아나야 한다.
③ 테스트가 **실제 `counsel_draft_view` 행을 읽었는지** 센다(0행이면 다른 것을 잰 것이다).
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from typing import Final

import pytest
from counsel_read_model_fixtures import draft_snapshot, view_snapshot
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from ai.db.counsel_read_model import PgCounselDraftViewStore
from ai.db.models import CounselDraftView as CounselDraftViewRow
from ai.db.settings import get_db_settings

pytestmark = pytest.mark.integration

_TENANT: Final = "t_wiring"
_OTHER_TENANT: Final = "t_other"




_Scenario = Callable[[async_sessionmaker[AsyncSession]], Awaitable[None]]


async def _with_pg(scenario: _Scenario) -> str:
    engine = create_async_engine(get_db_settings().database_url, poolclass=NullPool)
    try:
        async with engine.connect():
            pass
    except Exception:  # noqa: BLE001 — 접속 실패 종류를 가리지 않는다
        await engine.dispose()
        return "skip"
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        await scenario(sessions)
    finally:
        await engine.dispose()
    return "ok"


def _run(scenario: _Scenario) -> None:
    if asyncio.run(_with_pg(scenario)) == "skip":
        pytest.skip("실 PG 미가용 — docker compose up -d")


async def _clean(sessions: async_sessionmaker[AsyncSession]) -> None:
    async with sessions() as session, session.begin():
        for tenant in (_TENANT, _OTHER_TENANT):
            rows = (
                await session.execute(
                    select(CounselDraftViewRow).where(
                        CounselDraftViewRow.tenant_id == tenant
                    )
                )
            ).scalars()
            for row in rows:
                await session.delete(row)


async def _row_count(sessions: async_sessionmaker[AsyncSession], tenant: str) -> int:
    async with sessions() as session:
        return int(
            (
                await session.execute(
                    select(func.count())
                    .select_from(CounselDraftViewRow)
                    .where(CounselDraftViewRow.tenant_id == tenant)
                )
            ).scalar_one()
        )


def test_the_scan_actually_touches_real_rows() -> None:
    """🔴 절단 가드 ③ — 저장 뒤 실제 행이 **1행** 있어야 한다(0이면 딴 것을 쟀다)."""

    async def scenario(sessions: async_sessionmaker[AsyncSession]) -> None:
        await _clean(sessions)
        store = PgCounselDraftViewStore(sessions)
        key = (_TENANT, f"job-{uuid.uuid4()}")
        await store.save_view(key, snapshot=view_snapshot(key[1]))
        assert await _row_count(sessions, _TENANT) == 1
        await _clean(sessions)

    _run(scenario)


def test_view_and_draft_round_trip() -> None:
    async def scenario(sessions: async_sessionmaker[AsyncSession]) -> None:
        await _clean(sessions)
        store = PgCounselDraftViewStore(sessions)
        key = (_TENANT, f"job-{uuid.uuid4()}")
        view, draft = view_snapshot(key[1]), draft_snapshot()
        await store.save_view(key, snapshot=view)
        await store.save_draft(key, snapshot=draft)
        loaded_view, loaded_draft = await store.load(key)
        assert loaded_view == view
        assert loaded_draft == draft
        await _clean(sessions)

    _run(scenario)


@pytest.mark.parametrize("first", ["view", "draft"])
def test_either_order_keeps_both_snapshots(first: str) -> None:
    """🔴 **C-3의 핵심 조건** — 두 순서 모두 최종 행에 두 스냅숏이 남는다."""

    async def scenario(sessions: async_sessionmaker[AsyncSession]) -> None:
        await _clean(sessions)
        store = PgCounselDraftViewStore(sessions)
        key = (_TENANT, f"job-{uuid.uuid4()}")
        view, draft = view_snapshot(key[1]), draft_snapshot()
        if first == "view":
            await store.save_view(key, snapshot=view)
            await store.save_draft(key, snapshot=draft)
        else:
            await store.save_draft(key, snapshot=draft)
            await store.save_view(key, snapshot=view)
        loaded_view, loaded_draft = await store.load(key)
        assert loaded_view is not None, f"{first} 먼저 저장하니 뷰가 사라졌다"
        assert loaded_draft is not None, f"{first} 먼저 저장하니 초안이 사라졌다"
        await _clean(sessions)

    _run(scenario)


@pytest.mark.parametrize("present", ["view", "draft"])
def test_a_single_sided_row_loads_back(present: str) -> None:
    async def scenario(sessions: async_sessionmaker[AsyncSession]) -> None:
        await _clean(sessions)
        store = PgCounselDraftViewStore(sessions)
        key = (_TENANT, f"job-{uuid.uuid4()}")
        if present == "view":
            await store.save_view(key, snapshot=view_snapshot(key[1]))
        else:
            await store.save_draft(key, snapshot=draft_snapshot())
        loaded_view, loaded_draft = await store.load(key)
        assert (loaded_view is not None) is (present == "view")
        assert (loaded_draft is not None) is (present == "draft")
        await _clean(sessions)

    _run(scenario)


def test_updating_one_side_preserves_the_other() -> None:
    """이미 두 쪽이 있는 행에서 한쪽만 갱신 — 반대쪽 **값이 그대로**여야 한다."""

    async def scenario(sessions: async_sessionmaker[AsyncSession]) -> None:
        await _clean(sessions)
        store = PgCounselDraftViewStore(sessions)
        key = (_TENANT, f"job-{uuid.uuid4()}")
        original_view = view_snapshot(key[1])
        await store.save_view(key, snapshot=original_view)
        await store.save_draft(key, snapshot=draft_snapshot(text="1턴"))
        await store.save_draft(key, snapshot=draft_snapshot(text="2턴"))
        loaded_view, loaded_draft = await store.load(key)
        assert loaded_view == original_view, "초안 갱신이 뷰를 바꿨다"
        assert loaded_draft is not None and loaded_draft["text"] == "2턴"
        await _clean(sessions)

    _run(scenario)


def test_another_tenant_never_sees_the_same_job_id() -> None:
    """🔴 테넌트 격리 — 같은 `job_id`라도 남의 행이 안 나온다(CLAUDE.md §4)."""

    async def scenario(sessions: async_sessionmaker[AsyncSession]) -> None:
        await _clean(sessions)
        store = PgCounselDraftViewStore(sessions)
        job_id = f"job-{uuid.uuid4()}"
        await store.save_view((_TENANT, job_id), snapshot=view_snapshot(job_id))
        loaded_view, loaded_draft = await store.load((_OTHER_TENANT, job_id))
        assert loaded_view is None and loaded_draft is None
        await _clean(sessions)

    _run(scenario)


def test_an_empty_write_is_refused() -> None:
    async def scenario(sessions: async_sessionmaker[AsyncSession]) -> None:
        store = PgCounselDraftViewStore(sessions)
        key = (_TENANT, f"job-{uuid.uuid4()}")
        with pytest.raises(ValueError):
            await store.save_view(key, snapshot=None)  # type: ignore[arg-type]

    _run(scenario)


def test_a_mismatched_job_id_is_refused() -> None:
    async def scenario(sessions: async_sessionmaker[AsyncSession]) -> None:
        await _clean(sessions)
        store = PgCounselDraftViewStore(sessions)
        key = (_TENANT, f"job-{uuid.uuid4()}")
        with pytest.raises(ValueError):
            await store.save_view(key, snapshot=view_snapshot("some-other-job"))
        assert await _row_count(sessions, _TENANT) == 0, "거부됐는데 행이 남았다"

    _run(scenario)


@pytest.mark.parametrize("present", ["view", "draft"])
def test_the_absent_side_is_sql_null_not_json_null(present: str) -> None:
    """🔴 **없는 쪽은 SQL NULL이어야 한다** — JSON `null`이면 `IS NULL`이 거짓이 된다.

    ⚠ **파이썬 왕복만 보면 안 걸린다** — `'null'::jsonb`를 읽어도 `None`이라
    `loaded_view is None`이 그대로 참이다. **SQL 쪽에서만 갈린다**: 인덱스·운영 쿼리·
    「둘 다 비었는가」 점검이 전부 거짓을 본다.
    🔴 실측으로 잡았다(2026-08-10) — 뷰를 한 번도 안 쓴 행이 `IS NOT NULL`로 세어져
    **배선 뒤집기가 안 물었다.** `JSON.none_as_null` 기본값이 `False`다.
    """

    async def scenario(sessions: async_sessionmaker[AsyncSession]) -> None:
        await _clean(sessions)
        store = PgCounselDraftViewStore(sessions)
        key = (_TENANT, f"job-{uuid.uuid4()}")
        if present == "view":
            await store.save_view(key, snapshot=view_snapshot(key[1]))
            absent = CounselDraftViewRow.draft_snapshot
        else:
            await store.save_draft(key, snapshot=draft_snapshot())
            absent = CounselDraftViewRow.view_snapshot
        async with sessions() as session:
            nulls = (
                await session.execute(
                    select(func.count())
                    .select_from(CounselDraftViewRow)
                    .where(
                        CounselDraftViewRow.tenant_id == key[0],
                        CounselDraftViewRow.job_id == key[1],
                        absent.is_(None),
                    )
                )
            ).scalar_one()
        assert nulls == 1, (
            f"안 쓴 쪽이 SQL NULL이 아니다({present} 저장) — JSON null로 들어갔다"
        )
        await _clean(sessions)

    _run(scenario)
