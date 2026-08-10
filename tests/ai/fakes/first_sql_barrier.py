"""**첫 SQL에서만** 배리어에 서는 세션 프록시 — 최초 저장 경합을 결정론으로 만든다.

🔴 **`asyncio.gather()`만으로는 두 트랜잭션이 같은 순간에 문 앞에 선다는 보장이 없다.**
저장소가 「읽고-합치고-쓴다」일 때 경합은 **첫 SELECT와 INSERT 사이**에서만 열리므로,
**모든 writer가 첫 SQL을 지나기 전에는 아무도 진행하지 못하게** 세워야 재현된다.

⚠ **프로덕션 코드에 시험용 갈고리를 안 넣는다** — 세션메이커를 감싼다.
⚠ **첫 SQL에서만** 기다린다. 그 뒤 SQL은 그대로 통과시켜야 트랜잭션이 끝난다 —
매번 세우면 **두 번째 문에서 party가 모자라 영원히 멈춘다.**

🔴 **counsel과 problem_item이 같은 것을 쓴다.** 종전에는 두 파일이 **같은 15줄을 각자** 들고
있었고, 한쪽만 고쳐지면 갈린다(99 #02). 여기 하나로 둔다.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any, Protocol


class SupportsExecute(Protocol):
    """이 프록시가 감싸는 최소 계약 — 실제 `AsyncSession`도 기록용 대역도 만족한다."""

    async def execute(self, *args: Any, **kwargs: Any) -> Any: ...  # noqa: ANN401


class FirstSqlBarrierSession:
    """첫 `execute()` **직전에** 배리어에 서고, 그 뒤로는 그대로 흘려보낸다."""

    def __init__(self, session: Any, barrier: asyncio.Barrier) -> None:  # noqa: ANN401
        self._session = session
        #: 🔴 첫 문에서 `None`으로 끊는다 — 이후 SQL은 대기 없이 지난다.
        self._barrier: asyncio.Barrier | None = barrier

    async def execute(self, *args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
        if self._barrier is not None:
            barrier, self._barrier = self._barrier, None
            await barrier.wait()
        return await self._session.execute(*args, **kwargs)

    def __getattr__(self, name: str) -> Any:  # noqa: ANN401
        return getattr(self._session, name)


def barrier_sessionmaker(
    inner: Callable[[], Any], barrier: asyncio.Barrier
) -> Callable[[], Any]:
    """세션메이커를 감싸 **열리는 세션마다** 같은 배리어를 물린다."""

    @asynccontextmanager
    async def factory() -> AsyncIterator[FirstSqlBarrierSession]:
        async with inner() as session:
            yield FirstSqlBarrierSession(session, barrier)

    return factory


class RecordingSession:
    """호출 수만 세는 대역 — **절단 가드는 PG 없이 돌아야 한다.**"""

    def __init__(self) -> None:
        self.execute_calls = 0

    async def execute(self, *args: Any, **kwargs: Any) -> object:  # noqa: ANN401
        del args, kwargs
        self.execute_calls += 1
        return object()


async def wait_until_parked(
    barrier: asyncio.Barrier,
    tasks: list[asyncio.Task[Any]],
    *,
    expected: int,
    timeout: float = 2.0,
) -> None:
    """`expected`개가 배리어 앞에 설 때까지 기다린다 — ⚠ **유한하다.**

    🔴 **timeout은 hang 방지 상한이지 동시성 판정 기준이 아니다.** 판정은 호출자가
    `barrier.n_waiting`과 **내부 `execute` 호출 수**로 한다.
    ⚠ 태스크가 **먼저 끝나면** 즉시 빠진다 — `await barrier.wait()`가 사라진 경우가 그렇고,
    그때 호출자의 단정이 **곧바로 red**가 되어야 한다(무한 대기로 숨지 않는다).
    """
    async with asyncio.timeout(timeout):
        while barrier.n_waiting < expected:
            if any(task.done() for task in tasks):
                return
            await asyncio.sleep(0)
