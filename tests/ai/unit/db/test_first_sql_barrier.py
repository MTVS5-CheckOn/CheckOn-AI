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
    """🔴 **첫 SQL 뒤에는 새 party 없이 지나야 한다** — `_barrier`가 끊기는 동작을 고정한다.

    ⚠ 매번 세우면 **두 번째 문에서 party가 모자라 영원히 멈춘다** — 트랜잭션이 안 끝난다.

    🔴 **종전 판은 `Barrier(1)`이라 이 사실을 못 봤다**(미탐 · 8/10 지적). party가 1이면
    **재진입해도 혼자 즉시 통과**하므로, 프록시가 `_barrier`를 안 끊어도 green이었다.
    ⇒ **party 2 + 프록시 둘**로 세대를 실제로 연 뒤 **한 쪽만** 두 번째 SQL을 낸다.
    끊기지 않았다면 **두 번째 party를 기다리다 `TimeoutError`**가 나야 한다.
    """

    async def scenario() -> None:
        barrier = asyncio.Barrier(2)
        inners = [RecordingSession(), RecordingSession()]
        proxies = [
            FirstSqlBarrierSession(inners[0], barrier),
            FirstSqlBarrierSession(inners[1], barrier),
        ]

        #: ① 첫 세대를 **실제로** 연다 — 둘이 함께 지나야 열린다.
        async with asyncio.timeout(2.0):
            await asyncio.gather(
                proxies[0].execute("첫 SQL A"),
                proxies[1].execute("첫 SQL B"),
            )
        assert [inner.execute_calls for inner in inners] == [1, 1]

        #: ② 🔴 **한 프록시만** 두 번째 SQL을 낸다 — 새 party는 오지 않는다.
        #:    `_barrier`가 안 끊겼으면 여기서 두 번째 party를 기다려 timeout이다.
        async with asyncio.timeout(2.0):
            await proxies[0].execute("둘째 SQL A")
        assert [inner.execute_calls for inner in inners] == [2, 1], (
            "두 번째 SQL이 안 지나갔거나 엉뚱한 세션으로 갔다"
        )

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
        #: ⚠ 조기 종료 태스크 **하나면 충분하다** — 종전엔 안 쓰는 future도 만들었다.
        task: asyncio.Task[None] = asyncio.ensure_future(asyncio.sleep(0))
        await asyncio.sleep(0)
        async with asyncio.timeout(2.0):
            await wait_until_parked(barrier, [task], expected=2, timeout=1.0)
        assert barrier.n_waiting == 0

    asyncio.run(scenario())
