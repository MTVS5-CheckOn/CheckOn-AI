"""🔴 **실제로 `await` 를 타는** 라벨 집계 저장소 대역(2026-08-25 · 99 #241).

⚠ 🔴 №98 의 뒤집기는 «빈 **인메모리** 구현» 으로 갈아끼운 것이라 **갈아끼울 대상(PG)으로는
안 재본 것**이었다. 🔴 PG 구현은 `asyncpg` 를 타므로 **`await` 가 실제로 도는** 구현이라야
이음매를 잰다 — 이 대역이 그 자리다.
"""

from __future__ import annotations

import asyncio
from collections import Counter

from ai.db.repositories.label_confirmation_store import LabelConfirmationKey


class AwaitingLabelConfirmationStore:
    """`add`·`snapshot` 이 **진짜로 대기하고 만들어진 루프에 묶인다** — PG 와 같은 모양.

    ⚠ 🔴 **루프 친화성이 이 대역의 핵심이다**(2026-08-25 · 99 #243). 종전 판은
    `await asyncio.sleep(0)` 뿐이라 **어느 루프에서도 돌았고**, 그래서 «검사가
    `asyncio.run` 으로 **새 루프**를 만들어 읽는다» 는 결함을 **못 잡았다**(15 passed).
    🔴 `asyncpg` 는 연결이 **첫 루프에 묶여** 다른 루프에서 쓰면 터진다 — 99 #218 이
    그것으로 **11 failed** 였다. 이 대역이 그 성질을 흉내 낸다.
    """

    def __init__(self) -> None:
        self._counts: Counter[LabelConfirmationKey] = Counter()
        #: 🔴 **만들어진(=처음 쓰인) 루프**를 붙든다. `asyncpg` 풀과 같은 모양이다.
        self._loop: asyncio.AbstractEventLoop | None = None

    def _bind(self) -> None:
        running = asyncio.get_running_loop()
        if self._loop is None:
            self._loop = running
        elif self._loop is not running:
            raise RuntimeError(
                "Task got Future attached to a different loop — "
                "저장소가 다른 루프에서 쓰였다(99 #218 과 같은 형태)"
            )

    async def add(self, key: LabelConfirmationKey) -> None:
        self._bind()
        await asyncio.sleep(0)  # 🔴 실제 await — sync 이음매였다면 여기서 깨진다
        self._counts[key] += 1

    async def snapshot(self) -> dict[LabelConfirmationKey, int]:
        self._bind()
        await asyncio.sleep(0)
        return dict(self._counts)

    def clear(self) -> None:
        self._counts.clear()


__all__ = ["AwaitingLabelConfirmationStore"]
