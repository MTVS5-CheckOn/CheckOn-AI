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
    """`add`·`snapshot` 이 **진짜로 대기한다** — PG 구현과 같은 모양."""

    def __init__(self) -> None:
        self._counts: Counter[LabelConfirmationKey] = Counter()

    async def add(self, key: LabelConfirmationKey) -> None:
        await asyncio.sleep(0)  # 🔴 실제 await — sync 이음매였다면 여기서 깨진다
        self._counts[key] += 1

    async def snapshot(self) -> dict[LabelConfirmationKey, int]:
        await asyncio.sleep(0)
        return dict(self._counts)

    def clear(self) -> None:
        self._counts.clear()


__all__ = ["AwaitingLabelConfirmationStore"]
