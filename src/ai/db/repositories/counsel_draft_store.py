"""`DraftResultStore` PG 구현 — 초안 **본문**의 정본 (㉻ · 지시서 73 §4).

🔴 **㉻ 결손 ⓑ가 여기서 닫힌다.** `draft`에 본문 컬럼이 없어 `DraftRecord.content`가
`InMemoryDraftResultStore`에만 살았다 — 늦게 성공한 잡은 `phase=succeeded` ·
`result_ref=pack://…` 인데 GET이 `result=None`이었다(실측). 상태는 남고 **본문만 사라진**
비대칭이라 화면에서는 *"됐다는데 아무것도 없다"* 로 보였다.

🔴 **부모는 `AI_RUN`이다** — `draft.run_id → ai_run.execution_id`는 NOT NULL FK고, 그 부모는
워커가 `begin_run()`으로 **그래프 진입 전에** 세운다(99 #46). 이 저장소는 부모를 만들지
않는다: 없으면 **FK 오류가 그대로 올라간다**(⚠ 성공으로 번역하면 *"본문을 저장했다"* 가
거짓이 되고 GET은 영영 `result=None`이다 — ㉻가 정확히 그 형태였다).

⚠ **게이트 통과분만 들어온다** — 호출 자리가 `graph.py`의 게이트 통과 직후이고
**학생 경계 체크포인트보다 먼저**다. 순서가 곧 불변식 1이다.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ai.composition.counsel.stores import (
    DRAFT_SCHEME,
    DraftRecord,
    make_ref,
    parse_ref,
)
from ai.db.models import Draft as DraftRow

logger = logging.getLogger(__name__)

#: 🔴 **레코드 ↔ 컬럼이 1:1이다** — 스냅숏 JSONB를 따로 두지 않는다. `DRAFT`는 ERD에
#: 이미 전 필드의 컬럼을 갖고 있어서(본문만 없었다) 재조립이 손실을 만들지 않는다.
#: ⚠ 목록을 여기 한 번만 적는다 — 저장과 대조가 **같은 축**을 봐야 한다(99 #02).
_COLUMNS: tuple[str, ...] = (
    "id",
    "run_id",
    "agent_run_id",
    "tenant_id",
    "kind",
    "student_ref",
    "guardian_ref",
    "label_snapshot",
    "status",
    "fail_reason",
    "created_at",
    "content",
)


class DraftRecordConflict(RuntimeError):
    """같은 초안 `id`에 서로 다른 전문을 쓰려 함 — 불변 레코드 위반.

    ⚠ **본문만 보지 않는다** — 메타(상태·사유·라벨)가 달라도 충돌이다. 한쪽만 보면
    다른 쪽이 조용히 덮인다. 문면에는 **id만** 싣는다(본문·학생 원문 금지).
    """


def _values(record: DraftRecord) -> dict[str, Any]:
    return {name: getattr(record, name) for name in _COLUMNS}


def _to_record(row: DraftRow) -> DraftRecord:
    return DraftRecord.model_validate({name: getattr(row, name) for name in _COLUMNS})


class PgDraftResultStore:
    """`draft` 영속 — 학생 1명의 초안 1행.

    ⚠ **본문을 손질하지 않는다** — 자르기·정규화·공백 제거가 없다. 저장소가 문면을 고치면
    게이트가 통과시킨 것과 학부모가 받는 것이 달라진다.
    """

    def __init__(self, *, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sessionmaker = sessionmaker
        #: 부재와 남의 테넌트를 갈라 센다(99 #23 · 형제 저장소와 같은 규약).
        self.miss_absent = 0
        self.miss_foreign_tenant = 0

    async def put(self, record: DraftRecord) -> str:
        """초안 저장 — 멱등하고, **다른 전문이면 충돌**이다.

        ⚠ 재개가 같은 학생을 다시 처리하면 같은 `id`로 다시 온다(결정론 id) — 그때는
        행이 안 늘어야 한다. 그러나 **본문이 달라졌다면** 그건 재개가 아니라 다른 실행이다.
        """
        async with self._sessionmaker() as session, session.begin():
            result = await session.execute(
                pg_insert(DraftRow)
                .values(**_values(record))
                .on_conflict_do_nothing(index_elements=["id"])
                .returning(DraftRow.id)
            )
            if result.scalar_one_or_none() is None:
                #: 🔴 충돌을 성공으로 간주하지 않는다 — 기존 행을 **정확 대조**한다.
                await self._require_same(session, record)
        return make_ref(DRAFT_SCHEME, record.id)

    async def get(self, ref: str, *, tenant_id: str) -> DraftRecord | None:
        """`draft://` 역참조 — **부재와 남의 테넌트는 똑같이 `None`**.

        🔴 **초안 본문 유출의 마지막 층이다** — 상위(라우터·워커)의 귀속 검증은 이중 방어고
        여기가 저장소 수준 격리다.
        """
        draft_id = parse_ref(ref, DRAFT_SCHEME)
        async with self._sessionmaker() as session:
            scoped = (
                await session.execute(
                    select(DraftRow).where(
                        DraftRow.id == draft_id,
                        #: 🔴 **격리는 SQL에 있다.**
                        DraftRow.tenant_id == tenant_id,
                    )
                )
            ).scalar_one_or_none()
            if scoped is not None:
                return _to_record(scoped)
            exists = (
                await session.execute(
                    select(DraftRow.id).where(DraftRow.id == draft_id)
                )
            ).scalar_one_or_none()
        if exists is None:
            self.miss_absent += 1
            logger.info(
                "초안 역참조 실패 — **행이 없다** ref=%s tenant_id=%s", ref, tenant_id
            )
        else:
            self.miss_foreign_tenant += 1
            logger.warning(
                "초안 역참조 거부 — **남의 테넌트 행이다** ref=%s tenant_id=%s "
                "(본문 유출 방어의 마지막 층이다 · 99 #23)",
                ref,
                tenant_id,
            )
        return None

    async def _require_same(self, session: AsyncSession, record: DraftRecord) -> None:
        stored = (
            await session.execute(select(DraftRow).where(DraftRow.id == record.id))
        ).scalar_one_or_none()
        if stored is None:  # pragma: no cover — 같은 트랜잭션에서 사라질 수 없다
            raise DraftRecordConflict(f"초안 저장이 충돌했는데 행이 없다: id={record.id}")
        if _to_record(stored) != record:
            raise DraftRecordConflict(f"초안 멱등 충돌: id={record.id}")


__all__ = ["DraftRecordConflict", "PgDraftResultStore"]
