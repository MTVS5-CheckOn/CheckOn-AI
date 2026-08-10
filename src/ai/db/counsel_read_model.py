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
from contextlib import AbstractAsyncContextManager
from datetime import datetime
from typing import Any, Protocol

from sqlalchemy import null, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ai.db.counsel_draft_view import counsel_draft_view_projection
from ai.db.models import CounselDraftView as CounselDraftViewRow
from ai.db.repositories.idempotency import system_utc_now

#: `(tenant_id, job_id)` — 라우터 캐시와 **같은 키**다.
type CacheKey = tuple[str, str]
#: 세션 하나를 여는 것 — `async_sessionmaker`가 이 모양이다. 🔴 **필요한 만큼만 요구한다**:
#: 이 저장소는 세션을 열 줄만 알면 되고, 그래야 테스트가 감싼 세션을 넣을 수 있다.
type SessionFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]
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


def _as_column_value(snapshot: dict[str, Any] | None) -> Any:  # noqa: ANN401 — SQL NULL 센티널
    """🔴 **`None`을 그대로 넣으면 SQL NULL이 아니라 JSON `null`이 된다.**

    `JSON.none_as_null`의 기본값이 `False`라 파이썬 `None`이 `'null'::jsonb`로 저장된다 —
    파이썬으로 읽으면 똑같이 `None`이라 **왕복 테스트는 통과하는데** SQL의
    `view_snapshot IS NULL`이 **거짓**이 된다. 실측으로 잡았다(2026-08-10): 뷰를 한 번도
    안 쓴 행이 `IS NOT NULL`로 세어져 **뒤집기가 안 물었다.**
    ⚠ `db/models.py`는 무접촉이라 컬럼 설정 대신 **쓰는 쪽에서 센티널**을 준다.
    """
    return null() if snapshot is None else snapshot


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

    🔴 **자연키를 자문 잠금으로 직렬화하고 한 트랜잭션에서 끝낸다.**

    ⚠ **`SELECT … FOR UPDATE`만으로는 모자란다** — 그건 **있는 행**만 잠근다. 최초 저장에는
    잠글 행이 없어 두 트랜잭션이 나란히 *"행이 없다"* 를 보고 **둘 다 INSERT**하고,
    `uq_counsel_draft_view_job`이 하나를 죽인다(실측: `UniqueViolationError` · 99 #35).
    ⚠ **현재 동작을 과장하지 않는다** — 단일 POST 내부는 `await`로 **직렬 실행**된다.
    다만 뷰와 초안은 **독립 갱신 축**이고, **독립 요청·향후 배경 워커 분리·다중 소비자**가
    같은 자연키를 갱신하면 **동시 최초 저장이 가능하다.** 저장소는 그 배포·실행 형상에서도
    무결성을 보장해야 한다 — 그래서 이 잠금은 **지금 쓰이지 않아도 필요하다.**

    ⇒ **행이 있든 없든 같은 키에 서는 것**을 `pg_advisory_xact_lock`으로 만든다. 뒤에 온
    트랜잭션은 앞이 **커밋할 때까지 기다렸다가** 그 결과를 **읽고 합친다** —
    ⚠ **`IntegrityError`를 삼키고 성공으로 치는 것이 아니다.** 충돌 자체가 안 난다.

    ⚠ 잠금은 트랜잭션과 함께 풀린다(`_xact_`) — 세션에 남지 않는다.
    ⚠ 해시 충돌이면 **무관한 키가 잠깐 줄을 설 뿐** 정확성은 그대로다.
    ⚠ `FOR UPDATE`도 남겨 둔다 — 기존 행의 lost update 방지는 그 줄이 말한다.
    """

    #: 🔴 **잠금은 「이 테이블의 이 키」 하나에만 건다** — 테이블 이름을 섞어 두지 않으면
    #: 다른 테이블이 같은 자연키를 쓸 때 서로를 막는다.
    _LOCK_SQL = text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))")

    def __init__(
        self,
        sessionmaker: SessionFactory,
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
            #: 🔴 **읽기 전에 잠근다** — 행이 없어도 키에 줄을 세우려면 이 순서여야 한다.
            await session.execute(
                self._LOCK_SQL,
                #: ⚠ 구분자는 `\x00`이 아니라 `\x1f`다 — PG는 문자열에 NUL 바이트를 못 넣는다
                #: (실측: `invalid byte sequence for encoding "UTF8": 0x00`).
                {"key": f"counsel_draft_view\x1f{tenant_id}\x1f{job_id}"},
            )
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
            columns = {
                name: _as_column_value(value) if name.endswith("_snapshot") else value
                for name, value in values.items()
            }
            if row is None:
                session.add(
                    CounselDraftViewRow(
                        id=uuid.uuid4(), updated_at=self._now(), **columns
                    )
                )
                return
            for column, value in columns.items():
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
