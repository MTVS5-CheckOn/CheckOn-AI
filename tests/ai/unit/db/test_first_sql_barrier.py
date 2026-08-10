"""공용 프록시가 **첫 SQL에서만** 세우는가 (지시서 61 · #197 제안).

🔴 **종전 절단 가드는 `asyncio.Barrier`만 봤다.** 검사 이름은 *"writer가 실제로 겹친다"* 인데
보는 것은 *"표준 라이브러리 배리어가 작동한다"* 였다 — **프록시의 `await barrier.wait()`를
지워도 통과**했다. 이 파일은 **프록시를 실제로 지난다.**

⚠ **PG 없이 돈다** — 기록용 대역을 넣어 **내부 `execute` 호출 수**로 판정한다.
"""

from __future__ import annotations

import asyncio

from first_sql_barrier import (
    FirstSqlBarrierSession,
    RecordingSession,
    wait_until_parked,
)


def test_the_second_sql_does_not_wait_again() -> None:
    """🔴 **첫 SQL 뒤에는 새 party 없이 지나야 한다.**

    ⚠ 매번 세우면 **두 번째 문에서 party가 모자라 영원히 멈춘다** — 트랜잭션이 안 끝난다.
    이 단정이 `_barrier`가 첫 실행 뒤 `None`으로 끊기는 동작을 고정한다.
    """

    async def scenario() -> None:
        barrier = asyncio.Barrier(1)  # party 1 — 첫 문은 혼자서도 열린다
        inner = RecordingSession()
        proxy = FirstSqlBarrierSession(inner, barrier)

        await proxy.execute("첫 SQL")
        assert inner.execute_calls == 1

        #: 두 번째는 **배리어를 아예 안 쓴다** — party가 1이라 다시 서면 여기서 멈춘다.
        async with asyncio.timeout(2.0):
            await proxy.execute("둘째 SQL")
        assert inner.execute_calls == 2, "두 번째 SQL이 안 지나갔다"

    asyncio.run(scenario())


def test_the_proxy_forwards_unknown_attributes() -> None:
    """`__getattr__` 위임 — 프록시가 세션 자리를 대신할 수 있어야 한다."""

    class _Inner(RecordingSession):
        marker = "inner"

    proxy = FirstSqlBarrierSession(_Inner(), asyncio.Barrier(1))
    assert proxy.marker == "inner"


def test_waiting_gives_up_when_a_task_finishes_early() -> None:
    """🔴 **`wait_until_parked`가 무한 대기로 결함을 숨기면 안 된다.**

    배리어를 안 쓰는 태스크는 **먼저 끝난다** — 그때 즉시 빠져서 호출자의 단정이
    **red가 되게** 해야 한다. 여기서 `TimeoutError`가 나면 그 자체가 결함이다.
    """

    async def scenario() -> None:
        barrier = asyncio.Barrier(2)
        finished = asyncio.get_running_loop().create_future()
        finished.set_result(None)
        task: asyncio.Task[None] = asyncio.ensure_future(asyncio.sleep(0))
        await asyncio.sleep(0)
        async with asyncio.timeout(2.0):
            await wait_until_parked(barrier, [task], expected=2, timeout=1.0)
        assert barrier.n_waiting == 0

    asyncio.run(scenario())
