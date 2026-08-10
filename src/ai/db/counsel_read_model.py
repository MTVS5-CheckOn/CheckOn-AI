"""상담 읽기 모델(`COUNSEL_DRAFT_VIEW`) 저장소 — `_view_cache`·`_drafts`의 PG 자리 (99 ㉿).

#182가 테이블과 **파생 투영 함수**를 세웠고(`db/counsel_draft_view.py`), 이 파일이 그 위에
**저장·복원**을 얹는다. 라우터는 캐시를 그대로 두고(프로세스 내 LRU) 미스일 때 여기서 읽는다.

🔴 **이 읽기 모델은 INSERT-only가 아니다.** 같은 `(tenant_id, job_id)`가 **갱신**된다 —
뷰는 POST와 GET이, 초안은 생성 turn이 **각각 다른 시점에** 쓴다. 그래서
`PgPackResultStore`의 충돌 규약(*있으면 실패*)을 **복사하지 않는다.** 선례를 복사할지
재사용할지 참고만 할지는 매번 다른 판단이다(99 로그 104).

⚠ **가장 쉬운 오답은 행 전체 교체다.** 초안을 저장하며 `view_snapshot`을 안 실으면
**직전에 저장된 뷰가 null로 덮이고** GET이 404가 된다 — 증상이 캐시 축출(㉿ ⓐ)과 똑같아서
원인을 못 가린다. 그래서 쓰기는 전부 `merge_snapshots()`를 지난다.

🔴 **둘 다 null인 행을 DB가 막지 않는다**(실측 2026-08-10 · #182 A 리뷰). `CHECK` 제약이
없어서 그런 행이 그냥 들어간다 ⇒ **막는 자리는 `merge_snapshots()` 하나뿐**이고,
저장소는 그 함수를 **유일한 입구**로 쓴다. 컬럼 값을 따로 받는 인자를 두지 않는다.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ai.db.counsel_draft_view import counsel_draft_view_projection
from ai.db.models import CounselDraftView as CounselDraftViewRow
from ai.db.repositories.idempotency import system_utc_now

#: `(tenant_id, job_id)` — 라우터 캐시와 **같은 키**다.
type CacheKey = tuple[str, str]
type Snapshot = Mapping[str, Any]


def merge_snapshots(
    key: CacheKey,
    *,
    existing_view: Snapshot | None,
    existing_draft: Snapshot | None,
    new_view: Snapshot | None = None,
    new_draft: Snapshot | None = None,
) -> dict[str, Any]:
    """한쪽만 쓰는 저장을 **행 전체 값**으로 바꾼다 — 반대쪽은 **읽은 값 그대로** 남는다.

    🔴 **파생 넷은 병합 「뒤」의 두 스냅숏에서 유도한다.** 초안만 갱신할 때 파생을 초안
    쪽에서 뽑으면 **살아 있는 뷰의 `status`·`execution_id`가 지워진다** — draft-only 판정
    (`succeeded` · `execution_id=None`)은 **뷰가 없을 때만** 정직하다(#182 A 승인 근거).

    ⚠ 아무것도 안 쓰는 호출은 거부한다. 「갱신할 것이 없다」와 「빈 행을 만든다」는
    같은 코드 경로에서 구분이 안 되고, 후자를 DB가 안 막는다.
    """
    if new_view is None and new_draft is None:
        raise ValueError("저장할 스냅숏이 없다 — view·draft 중 하나는 넘겨야 한다")

    resolved_view = new_view if new_view is not None else existing_view
    resolved_draft = new_draft if new_draft is not None else existing_draft
    projection = counsel_draft_view_projection(
        key, view_snapshot=resolved_view, draft_snapshot=resolved_draft
    )
    return {
        **projection,
        "view_snapshot": dict(resolved_view) if resolved_view is not None else None,
        "draft_snapshot": dict(resolved_draft) if resolved_draft is not None else None,
    }


class CounselDraftViewStore(Protocol):
    """읽기 모델 영속 접점 — 라우터는 이 셋만 안다."""

    async def load(self, key: CacheKey) -> tuple[Snapshot | None, Snapshot | None]:
        """`(view_snapshot, draft_snapshot)` — 없으면 `(None, None)`."""
        ...

    async def save_view(self, key: CacheKey, *, snapshot: Snapshot) -> None: ...

    async def save_draft(self, key: CacheKey, *, snapshot: Snapshot) -> None: ...


class NullCounselDraftViewStore:
    """`store_backend=memory`의 구현 — **영속하지 않는다.**

    🔴 **이름이 「InMemory」가 아닌 이유**가 있다. 인메모리 dict를 하나 더 두면 라우터
    캐시와 **정본이 둘**이 되고, 축출·재시작 증상이 어느 쪽 때문인지 못 가린다.
    memory 백엔드의 v1 정본은 `_JobCache` 그대로다 — 이 구현은 **아무 일도 안 한다**는
    사실을 이름으로 말한다.
    """

    async def load(self, key: CacheKey) -> tuple[Snapshot | None, Snapshot | None]:
        del key
        return (None, None)

    async def save_view(self, key: CacheKey, *, snapshot: Snapshot) -> None:
        del key, snapshot

    async def save_draft(self, key: CacheKey, *, snapshot: Snapshot) -> None:
        del key, snapshot


class PgCounselDraftViewStore:
    """`counsel_draft_view` 한 행을 읽고-합치고-쓴다.

    🔴 **`SELECT … FOR UPDATE`로 잠그고 한 트랜잭션에서 끝낸다.** 뷰와 초안이 **다른 시점에**
    쓰이므로, 잠그지 않으면 두 쓰기가 각자 읽은 옛 행 위에 써서 **나중 것이 먼저 것을
    지운다**(lost update). 이 테이블은 그 경합이 **정상 경로**다 — POST가 뷰를 쓰는 사이
    같은 잡의 초안이 쓰인다.
    """

    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        *,
        now: Callable[[], datetime] = system_utc_now,
    ) -> None:
        self._sessions = sessionmaker
        self._now = now

    async def load(self, key: CacheKey) -> tuple[Snapshot | None, Snapshot | None]:
        tenant_id, job_id = key
        async with self._sessions() as session:
            row = (
                await session.execute(
                    select(CounselDraftViewRow).where(
                        CounselDraftViewRow.tenant_id == tenant_id,
                        CounselDraftViewRow.job_id == job_id,
                    )
                )
            ).scalar_one_or_none()
        if row is None:
            return (None, None)
        return (row.view_snapshot, row.draft_snapshot)

    async def save_view(self, key: CacheKey, *, snapshot: Snapshot) -> None:
        await self._save(key, new_view=snapshot)

    async def save_draft(self, key: CacheKey, *, snapshot: Snapshot) -> None:
        await self._save(key, new_draft=snapshot)

    async def _save(
        self,
        key: CacheKey,
        *,
        new_view: Snapshot | None = None,
        new_draft: Snapshot | None = None,
    ) -> None:
        #: 🔴 문 앞에서 거른다 — 접속을 열기 전에 거부한다(빈 쓰기는 DB가 안 막는다).
        if new_view is None and new_draft is None:
            raise ValueError("저장할 스냅숏이 없다 — view·draft 중 하나는 넘겨야 한다")
        tenant_id, job_id = key
        async with self._sessions() as session, session.begin():
            row = (
                await session.execute(
                    select(CounselDraftViewRow)
                    .where(
                        CounselDraftViewRow.tenant_id == tenant_id,
                        CounselDraftViewRow.job_id == job_id,
                    )
                    .with_for_update()
                )
            ).scalar_one_or_none()
            values = merge_snapshots(
                key,
                existing_view=row.view_snapshot if row is not None else None,
                existing_draft=row.draft_snapshot if row is not None else None,
                new_view=new_view,
                new_draft=new_draft,
            )
            if row is None:
                session.add(
                    CounselDraftViewRow(
                        id=uuid.uuid4(), updated_at=self._now(), **values
                    )
                )
                return
            for column, value in values.items():
                setattr(row, column, value)
            #: ⚠ 스냅숏 파생이 아닌 축 — 쓰기 시각이다(#182 `NON_PROJECTED_COLUMNS`).
            row.updated_at = self._now()


__all__ = [
    "CacheKey",
    "CounselDraftViewStore",
    "NullCounselDraftViewStore",
    "PgCounselDraftViewStore",
    "Snapshot",
    "merge_snapshots",
]
