"""**PG가 실패했는데 캐시에는 남는가** — 쓰기 순서 (99 #35 · #186 보완).

🔴 **#186은 캐시를 먼저 바꿨다.** `_remember_view`가 `_view_cache.put()` → `save_view()`
순서라 **저장이 터져도 캐시에는 값이 남는다.** 그 뒤 GET·refine은 **방금 저장에 실패한 값**을
정상인 것처럼 돌려준다 — **프로세스가 죽으면 사라질 값**을 「있다」고 말하는 것이다.

⚠ **증상이 조용하다** — 예외는 호출자에게 올라가지만 캐시는 이미 오염됐고, 그 뒤의 조회는
**성공처럼 보인다.** 「저장 실패」와 「저장 성공 뒤 축출」이 구분되지 않는다.

⇒ **영속이 먼저, 캐시가 나중.** memory 백엔드에서는 `NullCounselDraftViewStore`가 성공하므로
**현행 동작이 그대로**다(캐시에 들어간다).

⚠ 실 PG가 필요 없다 — **터지는 저장소**를 넣으면 순서만으로 갈린다.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any, Final

import pytest
from counsel_read_model_fixtures import draft_snapshot, view_snapshot

from ai.api.routers import counsel as counsel_router
from ai.api.routers.counsel import (
    _CachedView,
    _draft_from_snapshot,
    _DraftState,
    _remember_draft,
    _remember_view,
    _view_from_snapshot,
    reset_counsel_stores,
    set_counsel_draft_view_store,
)
from ai.db.counsel_read_model import CacheKey, Snapshot
from ai.db.store_factory import reset_shared_agent_runtime

_KEY: Final = ("t_order", "job-order-1")


class _ExplodingStore:
    """저장이 **항상 터지는** 저장소 — 순서만 재려는 대역이다."""

    def __init__(self) -> None:
        self.saves = 0

    async def load(self, key: CacheKey) -> tuple[Snapshot | None, Snapshot | None]:
        del key
        return (None, None)

    async def save_view(self, key: CacheKey, *, snapshot: Snapshot) -> None:
        del key, snapshot
        self.saves += 1
        raise RuntimeError("PG 저장 실패(대역)")

    async def save_draft(self, key: CacheKey, *, snapshot: Snapshot) -> None:
        del key, snapshot
        self.saves += 1
        raise RuntimeError("PG 저장 실패(대역)")


class _RecordingStore(_ExplodingStore):
    """성공하는 저장소 — 캐시와 PG가 **같은 값**인지 보려고 받은 것을 적어 둔다."""

    def __init__(self) -> None:
        super().__init__()
        self.view: Snapshot | None = None
        self.draft: Snapshot | None = None

    async def load(self, key: CacheKey) -> tuple[Snapshot | None, Snapshot | None]:
        del key
        return (self.view, self.draft)

    async def save_view(self, key: CacheKey, *, snapshot: Snapshot) -> None:
        del key
        self.saves += 1
        self.view = snapshot

    async def save_draft(self, key: CacheKey, *, snapshot: Snapshot) -> None:
        del key
        self.saves += 1
        self.draft = snapshot


@pytest.fixture(autouse=True)
def _clean_stores() -> Any:  # noqa: ANN401 — pytest fixture
    #: ⚠ **둘을 나란히 부른다** — 잡과 체크포인트는 `thread_id`(=`job_id`)로 엮여 있어
    #: 한쪽만 지우면 짝 없는 것이 남는다(99 ㊒ · 기존 가드가 이 파일을 잡아 줬다).
    reset_shared_agent_runtime()
    reset_counsel_stores()
    yield
    reset_shared_agent_runtime()
    reset_counsel_stores()


def _cached_view() -> _CachedView:
    return _view_from_snapshot(
        view_snapshot(_KEY[1], execution_id=uuid.uuid4(), correlation_id=uuid.uuid4())
    )


def _draft_state() -> _DraftState:
    return _draft_from_snapshot(draft_snapshot())


def test_a_failed_view_save_leaves_no_cache_entry() -> None:
    async def scenario() -> None:
        """🔴 **PG가 터지면 캐시에 값이 남으면 안 된다.**"""
        set_counsel_draft_view_store(_ExplodingStore())
        with pytest.raises(RuntimeError):
            await _remember_view(_KEY, _cached_view())
        assert counsel_router._view_cache.get(_KEY) is None, (
            "저장이 실패했는데 캐시에 값이 남았다 — 조회가 없는 값을 있다고 말한다"
        )

    asyncio.run(scenario())


def test_a_failed_draft_save_leaves_no_cache_entry() -> None:
    async def scenario() -> None:
        set_counsel_draft_view_store(_ExplodingStore())
        with pytest.raises(RuntimeError):
            await _remember_draft(_KEY, _draft_state())
        assert counsel_router._drafts.get(_KEY) is None, (
            "저장이 실패했는데 초안 캐시에 값이 남았다"
        )

    asyncio.run(scenario())


def test_a_failed_save_is_not_visible_to_a_later_read() -> None:
    async def scenario() -> None:
        """🔴 **실패한 값을 그 다음 조회가 보면 안 된다** — 그게 이 순서의 이유다."""
        from ai.api.routers.counsel import _cached_view_of, _draft_state_of  # noqa: PLC0415

        set_counsel_draft_view_store(_ExplodingStore())
        with pytest.raises(RuntimeError):
            await _remember_view(_KEY, _cached_view())
        with pytest.raises(RuntimeError):
            await _remember_draft(_KEY, _draft_state())
        assert await _cached_view_of(_KEY) is None, "실패한 뷰가 조회에 보인다"
        assert await _draft_state_of(_KEY) is None, "실패한 초안이 조회에 보인다"

    asyncio.run(scenario())


def test_a_successful_save_puts_the_same_value_in_both() -> None:
    async def scenario() -> None:
        """성공 경로 회귀 — 캐시와 저장소가 **같은 값**이어야 한다."""
        store = _RecordingStore()
        set_counsel_draft_view_store(store)
        cached, state = _cached_view(), _draft_state()
        await _remember_view(_KEY, cached)
        await _remember_draft(_KEY, state)

        assert counsel_router._view_cache.get(_KEY) == cached
        assert counsel_router._drafts.get(_KEY) == state
        assert store.view is not None and store.draft is not None
        assert _view_from_snapshot(store.view) == cached, "PG에 간 값이 캐시와 다르다"
        assert _draft_from_snapshot(store.draft) == state, "PG에 간 값이 캐시와 다르다"

    asyncio.run(scenario())


def test_the_memory_backend_still_caches() -> None:
    async def scenario() -> None:
        """🔴 memory 회귀 — `Null…`은 성공하므로 **현행 동작 그대로** 캐시에 들어간다."""
        cached = _cached_view()
        await _remember_view(_KEY, cached)  # 기본 저장소 = 팩토리가 고른 것(memory)
        assert counsel_router._view_cache.get(_KEY) == cached

    asyncio.run(scenario())
