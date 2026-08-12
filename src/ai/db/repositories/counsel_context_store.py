"""`ContextStore` PG 구현 — 워커 **입력 묶음**의 정본 (㉻ · 지시서 73 §3).

🔴 **㉻ 결손 ⓐ가 여기서 닫힌다.** `ContextBundleRecord`는 대응 테이블이 없어
`InMemoryContextStore`에만 살았고, 그래서 **다른 프로세스의 워커가 `payload_ref`를
해소하지 못해** `context_bundle_missing`으로 잡을 실패시켰다(실측). 앱 인스턴스와 워커가
같은 프로세스일 때만 동작하는 배선이었던 셈이다.

⚠ **`Protocol`은 그대로다** — `put(record) -> context://…` · `get(ref, *, tenant_id)`.
인메모리 구현과 **표면이 같아야** 백엔드 교체가 의미를 갖는다.

🔴 **같은 id에 다른 전문은 덮지 않는다**(§3). 덮으면 재개한 워커가 **다른 입력**을 읽고,
`content_hash` 대조(불변식 ④)가 잡기 전까지 판정이 갈린다. `PgPackResultStore`가 같은
규약이고 예외 이름만 층에 맞춰 갈랐다(99 ㉳와 같은 자리).
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ai.composition.counsel.stores import (
    CONTEXT_SCHEME,
    ContextBundleRecord,
    make_ref,
    parse_ref,
)
from ai.db.models import CounselContextBundle as ContextBundleRow

logger = logging.getLogger(__name__)


class ContextBundleConflict(RuntimeError):
    """같은 `id`에 서로 다른 입력 묶음을 쓰려 함 — 불변 레코드 위반.

    ⚠ **SQL 장애가 아니라 판정이다** — 삼키면 재개가 남의 입력으로 돈다.
    문면에는 **id만** 싣는다(학생 원문·본문 금지).
    """


def _snapshot(record: ContextBundleRecord) -> dict[str, Any]:
    """`contexts`의 JSONB 표현 — 🔴 **무손실 정본**이다.

    ⚠ 여기서 파생한 **별도 손사본을 만들지 않는다.** 컬럼으로 흩으면 계약이 넓어질 때
    조용히 값이 깎인다(선례: `PROBLEM_ITEM`의 결손 12건 · `pack_store.py` 머리말).
    """
    return {
        ref: context.model_dump(mode="json")
        for ref, context in record.contexts.items()
    }


def _to_record(row: ContextBundleRow) -> ContextBundleRecord:
    """행 → 레코드. **컬럼과 JSONB를 합쳐** 계약 타입으로 되돌린다."""
    return ContextBundleRecord.model_validate(
        {
            "id": row.id,
            "tenant_id": row.tenant_id,
            "class_ref": row.class_ref,
            "contexts": row.contexts,
            "content_hash": row.content_hash,
            "created_at": row.created_at,
        }
    )


class PgContextStore:
    """`counsel_context_bundle` 영속 — 입력 묶음 1건.

    🔴 **조회 술어에 `tenant_id`가 들어간다**(CLAUDE.md §4). PK로 읽은 뒤 파이썬에서
    버리는 방식은 쓰지 않는다 — 그러면 *"남의 행을 읽기는 했다"* 가 되고, 로그·메트릭·
    커넥션 비용이 이미 발생한 뒤다.
    """

    def __init__(self, *, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sessionmaker = sessionmaker
        #: 🔴 **두 경우를 갈라 센다**(99 #23 · `PgPackResultStore`와 같은 규약) —
        #: 반환값이 둘 다 `None`이라 운영에서 *"왜 없나"* 를 물으면 답이 안 나온다.
        self.miss_absent = 0
        self.miss_foreign_tenant = 0

    async def put(self, record: ContextBundleRecord) -> str:
        """묶음 저장 — 멱등하고, **다른 전문이면 충돌**이다.

        🔴 **동시 최초 저장을 `ON CONFLICT DO NOTHING`으로 흘려보내지 않는다.** 그건
        *"내가 썼다"* 와 *"남이 먼저 썼다"* 를 같은 성공으로 만든다 — 뒤에서 **반드시
        기존 행을 정확 대조**해 같은 전문일 때만 성공으로 수렴시킨다(99 #46과 같은 규약).
        """
        payload = {
            "id": record.id,
            "tenant_id": record.tenant_id,
            "class_ref": record.class_ref,
            "contexts": _snapshot(record),
            "content_hash": record.content_hash,
            "created_at": record.created_at,
        }
        async with self._sessionmaker() as session, session.begin():
            result = await session.execute(
                pg_insert(ContextBundleRow)
                .values(**payload)
                .on_conflict_do_nothing(index_elements=["id"])
                .returning(ContextBundleRow.id)
            )
            if result.scalar_one_or_none() is None:
                #: 다른 트랜잭션이 먼저 썼거나 재저장이다 — **전문을 대조한다.**
                await self._require_same(session, record)
        return make_ref(CONTEXT_SCHEME, record.id)

    async def get(self, ref: str, *, tenant_id: str) -> ContextBundleRecord | None:
        """`context://` 역참조 — **부재와 남의 테넌트는 똑같이 `None`**(99 #23).

        ⚠ 스킴이 다르면 `parse_ref`가 `ValueError`다(fail-closed) — 잘못된 참조를
        *"없다"* 로 번역하지 않는다. 없는 것과 틀린 것은 다른 사실이다.
        """
        bundle_id = parse_ref(ref, CONTEXT_SCHEME)
        async with self._sessionmaker() as session:
            scoped = (
                await session.execute(
                    select(ContextBundleRow).where(
                        ContextBundleRow.id == bundle_id,
                        #: 🔴 **격리는 SQL에 있다** — 파이썬 후처리가 아니다.
                        ContextBundleRow.tenant_id == tenant_id,
                    )
                )
            ).scalar_one_or_none()
            if scoped is not None:
                return _to_record(scoped)
            #: ⚠ 여기서만 **판정용으로** 존재를 묻는다 — 값은 안 돌려준다.
            exists = (
                await session.execute(
                    select(ContextBundleRow.id).where(ContextBundleRow.id == bundle_id)
                )
            ).scalar_one_or_none()
        if exists is None:
            self.miss_absent += 1
            logger.info(
                "입력 묶음 역참조 실패 — **행이 없다** ref=%s tenant_id=%s", ref, tenant_id
            )
        else:
            self.miss_foreign_tenant += 1
            logger.warning(
                "입력 묶음 역참조 거부 — **남의 테넌트 행이다** ref=%s tenant_id=%s "
                "(상위의 잡 조회가 이미 걸러야 하는 자리다 · 99 #23)",
                ref,
                tenant_id,
            )
        return None

    async def _require_same(
        self, session: AsyncSession, record: ContextBundleRecord
    ) -> None:
        stored = (
            await session.execute(
                select(ContextBundleRow).where(ContextBundleRow.id == record.id)
            )
        ).scalar_one_or_none()
        if stored is None:  # pragma: no cover — 같은 트랜잭션에서 사라질 수 없다
            raise ContextBundleConflict(
                f"입력 묶음 저장이 충돌했는데 행이 없다: id={record.id}"
            )
        if _to_record(stored) != record:
            raise ContextBundleConflict(f"입력 묶음 멱등 충돌: id={record.id}")


__all__ = ["ContextBundleConflict", "PgContextStore"]
