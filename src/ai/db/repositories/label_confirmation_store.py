"""라벨 확정 **집계** 저장소 — 인터페이스와 인메모리 구현(2026-08-25 · 99 #239).

🔴 **왜 인터페이스인가** — 구현이 **둘이 될 것이 이미 정해졌다**: 지금은 프로세스 안
카운터이고, 누적이 필요해지면 **PG** 다. 실측(#239)이 그 필요를 확정했다 —
🔴 **재배포마다 0 이 되는데 「군집의 착수 근거」는 한 달 단위**라 메모리로는 못 산다.
⚠ 🔴 **「미리 추상화」가 아니다** — 두 구현만 견디면 되고, 이 저장소에 같은 형태가
이미 여럿 있다(`InquiryClassStore` · `ContextStore` · `DraftResultStore`).

🔴 **`guardian_ref` 는 키에도 값에도 없다** — №92 판정의 전부다. 키는 넷뿐이다:
`(axis, 제안값, 확정값, action)`. ⚠ 🔴 `tenant_id` 도 **안 담는다** — «가르면 무엇이
좋아지나» 를 못 적었고 **테넌트가 적으면 그 자체가 식별 축**이다(99 #233).

⚠ 🔴 **PG 구현은 아직 없다** — `db/models.py` 와 마이그레이션이 **양자 승인**이라
준영님 승인 전에는 못 연다. 🔴 승인이 나면 **구현 하나를 더하는 것으로 끝난다** —
라우터도 검사도 안 바뀐다(그게 이 자리의 값이다).
"""

from __future__ import annotations

import uuid
from collections import Counter
from collections.abc import Callable
from datetime import date, datetime, timedelta
from typing import Protocol
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from ai.db.counsel_read_model import SessionFactory
from ai.db.models import LabelConfirmationStat
from ai.db.repositories.inquiry_class_store import system_utc_now

#: 🔴 주 경계 기준 시계 — 팀이 KST 다(03 §1b: 시간대는 `zoneinfo`).
_SEOUL = ZoneInfo("Asia/Seoul")

#: 🔴 집계 키 — `(tenant_id, axis, 제안값, 확정값, action)`. **개인 참조가 없다.**
#: ⚠ 🔴 **4칸 → 5칸(2026-08-25)** — `tenant_id` 가 들어갔다. 테이블의 격리 술어이고
#: **PG 모양의 일부**라, 나중에 넣으면 같은 자리를 두 번 연다(99 #241 의 교훈).
#: 🔴 «라우터도 검사도 안 바뀐다» 가 **이번엔 거짓이다** — 그 사실을 적어 둔다.
#: 다만 `_accept_label` 이 이미 `tenant_id` 를 인자로 받아 **라우터는 한 줄**이다.
LabelConfirmationKey = tuple[str, str, str, str, str]


class LabelConfirmationStore(Protocol):
    """라벨 확정 집계 — **더하고 읽는다.** 그 둘뿐이다."""

    async def add(self, key: LabelConfirmationKey) -> None:
        """확정 하나를 집계에 더한다. 🔴 400 으로 거절된 것은 안 부른다.

        🔴 **`async` 다**(2026-08-25 · 99 #241) — 갈아끼울 것이 **PG(asyncpg)** 라
        `async` 가 **아닐 수 없다.** ⚠ 종전 sync 였고, 그러면 승인 나는 순간
        Protocol·라우터·검사가 **전부** 바뀐다 ⇒ «구현 하나 더하면 끝» 이 거짓이 된다.
        🔴 선례가 전부 그렇다: `InquiryClassStore.apply_confirmation` · `run_store.record_run`
        · `agent_job.add` — **저장소 계열은 예외 없이 `async def`** 다.
        """
        ...

    async def snapshot(self) -> dict[LabelConfirmationKey, int]:
        """지금까지의 집계 — 🔴 **읽는 쪽도 개인 참조를 못 담는다**(키가 넷뿐이다)."""
        ...

    def clear(self) -> None:
        """비운다 — 검사 격리용(모듈 규약 · 99 #237).

        🔴 **여기만 sync 로 둔다.** 이유: 이건 **검사 격리 전용**이고 프로덕션 경로가
        안 부른다 — `reset_*` 규약이 동기 픽스처에서 불린다(`reset_inquiry_class_store`
        선례도 동기다). ⚠ PG 구현이 생기면 그 `clear` 는 **아무것도 안 하거나** 테스트
        전용 truncate 가 되는데, 어느 쪽이든 **동기 자리에서 불린다.**
        """
        ...


class InMemoryLabelConfirmationStore:
    """프로세스 안 카운터 — 🔴 **재배포하면 0 이 된다.**

    선례: `db/repositories/counsel_context_store.py` 의 `self.miss_absent`
    (프로세스 안 정수 · 개인 데이터 미보존 · 99 #23).
    """

    def __init__(self) -> None:
        self._counts: Counter[LabelConfirmationKey] = Counter()

    async def add(self, key: LabelConfirmationKey) -> None:
        self._counts[key] += 1

    async def snapshot(self) -> dict[LabelConfirmationKey, int]:
        return dict(self._counts)

    def clear(self) -> None:
        self._counts.clear()


class PgLabelConfirmationStore:
    """PG 누적 — 🔴 **주간 버킷에 UPSERT** 한다(99 #239 · 2026-08-25).

    🔴 **`week_start` 는 「확정 접수 시각」에서 계산한다** — BE 스냅숏의 주 값이 아니다.
    `Asia/Seoul` 로 옮겨 그 주의 **월요일**을 잡는다.
    ⚠ 🔴 **감지 피처의 같은 이름과 다른 규칙이다**(BE 주 경계는 04:1228 의 **[제안]**) —
    조인하지 마라.
    🔴 **clock 을 주입한다**(`PgInquiryClassStore` 선례) — 안 하면 검사가 «이번 주» 에
    묶여 **다음 주에 red** 가 된다.
    """

    def __init__(
        self,
        *,
        sessionmaker: SessionFactory,
        clock: Callable[[], datetime] = system_utc_now,
        new_id: Callable[[], uuid.UUID] = uuid.uuid4,
    ) -> None:
        self._sessionmaker = sessionmaker
        self._clock = clock
        self._new_id = new_id

    def _bucket(self) -> date:
        """확정 접수 시각 → `Asia/Seoul` 그 주의 **월요일**."""
        local = self._clock().astimezone(_SEOUL)
        return (local - timedelta(days=local.weekday())).date()

    async def add(self, key: LabelConfirmationKey) -> None:
        tenant_id, axis, suggested, confirmed, action = key
        now = self._clock()
        statement = (
            insert(LabelConfirmationStat)
            .values(
                id=self._new_id(),
                tenant_id=tenant_id,
                week_start=self._bucket(),
                axis=axis,
                suggested_value=suggested,
                confirmed_value=confirmed,
                action=action,
                count=1,
                created_at=now,
            )
            #: 🔴 **유니크가 곧 UPSERT 키다** — 같은 버킷이면 행 하나에 수만 는다.
            .on_conflict_do_update(
                constraint="uq_label_confirmation_stat_bucket",
                set_={"count": LabelConfirmationStat.count + 1},
            )
        )
        async with self._sessionmaker() as session, session.begin():
            await session.execute(statement)

    async def snapshot(self) -> dict[LabelConfirmationKey, int]:
        """전 버킷 합계 — 🔴 **주는 합쳐서** 돌려준다(키가 5칸이라 주가 안 들어간다).

        ⚠ 🔴 읽는 통로를 만들 때 **`k` 임계**를 같이 정한다(99 #239) — 테넌트에 학부모가
        하나면 이 집계가 그 사람의 프로필이 된다.
        """
        columns = (
            LabelConfirmationStat.tenant_id,
            LabelConfirmationStat.axis,
            LabelConfirmationStat.suggested_value,
            LabelConfirmationStat.confirmed_value,
            LabelConfirmationStat.action,
        )
        statement = select(*columns, func.sum(LabelConfirmationStat.count)).group_by(
            *columns
        )
        async with self._sessionmaker() as session:
            rows = (await session.execute(statement)).all()
        return {(a, b, c, d, e): int(total) for a, b, c, d, e, total in rows}

    def clear(self) -> None:
        """🔴 **아무것도 안 한다(no-op).**

        ⚠ 🔴 여기서 I/O 를 하면 99 #243·#218 이 그 자리에서 재발한다 — 이 메서드는
        **검사 격리 전용**이고 `reset_*` 규약이 **동기 픽스처**에서 부른다. 동기 자리에서
        `asyncio.run` 으로 DB 를 건드리면 **다른 루프**가 되고 asyncpg 가 터진다.
        🔴 PG 를 쓰는 검사는 **자기 테넌트를 나눠** 격리한다(그게 이 저장소의 격리 축이다).
        """


_default: LabelConfirmationStore | None = None


def default_label_confirmation_store() -> LabelConfirmationStore:
    """인메모리 공용 저장소 — **프로세스 하나에 하나**다.

    ⚠ 🔴 **백엔드 분기는 여기 없다** — `db/store_factory.py` 가 조립 루트다.
    🔴 `test_store_backend_default_assembly.py` 가 «`store_backend` 를 보는데 **아무도
    안 보는 자리**» 를 잡는다 — 저장소 모듈 안에 분기를 두면 조립 루트가 그것을 안 부른다
    (실측 2026-08-25: 그 가드가 이 회차에 red 를 냈다).
    """
    global _default
    if _default is None:
        _default = InMemoryLabelConfirmationStore()
    return _default


def reset_default_label_confirmation_store() -> None:
    """공용 저장소를 비운다 — 검사 격리용."""
    if _default is not None:
        _default.clear()


__all__ = [
    "InMemoryLabelConfirmationStore",
    "PgLabelConfirmationStore",
    "LabelConfirmationKey",
    "LabelConfirmationStore",
    "default_label_confirmation_store",
    "reset_default_label_confirmation_store",
]
