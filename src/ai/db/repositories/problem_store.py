"""`ProblemItemStore` PG 구현 — 슬롯 최종본 영속 (09 §2-20 · A 판정 2026-08-08).

`ProblemItemStore` Protocol(`problem_generation/application/ports.py`)의 `save`/`get`이
`StoredProblemItem`을 **무손실로 왕복**해야 하는데, 종전 `problem_item` 스키마로는 12건이
맞지 않아 구현 자체가 불가능했다(§2-20). A 판정으로 조회 키(`slot_index`)·스냅숏 정본·
본문 nullable이 서면서 이 파일이 성립한다.

🔴 **정본은 `problem_item.snapshot` 한 곳이다.** `get`은 스냅숏만 읽는다 — 파생 컬럼을
읽어 레코드를 **재조립하지 않는다.** 재조립하면 컬럼이 계약보다 좁아지는 순간 조용히
값이 깎이고, 그것이 §2-20을 낳은 형태다.

승인 조건 셋(A 판정 결정 ②)이 이 파일에 산다:

- **ⓐ** 파생 투영은 `problem_item_projection()` **한 함수**가 스냅숏에서 유도한다 —
  호출자가 스냅숏과 컬럼 값을 따로 넘길 수 있는 자리를 두지 않는다.
- **ⓑ** 갈리면 red — `tests/ai/db/test_problem_store_projection.py`가 「유도값 == 컬럼」을
  단정한다(99 #04).
- **ⓒ** 어느 쪽이 정본인지는 `db/models.py`의 `ProblemItem` docstring에 적혀 있다.

⚠ **테넌트 격리는 생성자 주입이다**(§2-20.3) — Protocol 시그니처에 `tenant_id`가 없어
인스턴스를 테넌트 단위로 스코프하고 모든 조회에서 부모 `problem_set`을 조인한다.
`problem_item`에 `tenant_id` 직접 컬럼을 두지 않은 것은 누락이 아니다(A 판정 §1).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ai.contracts.problem_generation import GeneratedItem, ItemResult
from ai.db.models import ProblemItem as ProblemItemRow
from ai.db.models import ProblemSet as ProblemSetRow
from ai.problem_generation.application.ports import ImmutableStoreConflict
from ai.problem_generation.domain.identity import problem_item_id
from ai.problem_generation.domain.models import StoredProblemItem

#: 최초 저장의 낙관적 잠금 기준 — 리비전이 아직 없다. `item_revision`이 올린다.
_INITIAL_REVISION_NO = 0

#: 🔴 **파생이 아닌 컬럼** — `problem_item_projection()`이 내지 않는다.
#: `current_revision_no`는 저장소 소유 상태이고, `passage_id`·`drop_reason`은
#: `StoredProblemItem`에 대응 값이 없다(후자는 폐기 전용이라 이 경로에서 항상 null).
NON_PROJECTED_COLUMNS: frozenset[str] = frozenset(
    {"id", "set_id", "snapshot", "passage_id", "current_revision_no", "drop_reason"}
)


def problem_item_projection(record: StoredProblemItem) -> dict[str, Any]:
    """스냅숏 → 조회용 파생 투영 (승인 조건 ⓐ · 순수 함수).

    🔴 **이 함수만이 파생 컬럼을 만든다.** 저장 호출자는 `StoredProblemItem` 하나만
    넘기고 컬럼 값을 따로 넘기지 않는다 — 두 인자를 받는 순간 호출부마다 다른 값을
    넣을 수 있고, 그러면 정본과 투영이 갈린다(99 #20이 정확히 그 형태였다).

    본문(`item`)이 없는 슬롯에서는 본문 투영 여덟이 전부 `None`이다 — 투영은 원본이
    없을 때 NULL이라는 것이 결정 ③ ⓒ의 정의다.
    """
    body = record.item
    dumped = body.model_dump(mode="json") if body is not None else None
    result = record.result
    return {
        "slot_index": record.slot_index,
        "area_tag": None if body is None else body.area_tag.value,
        "type_tag": None if body is None else body.type_tag.value,
        "item_format": None if body is None else body.item_format.value,
        "skill_node_id": None if body is None else body.skill_node_id,
        "stem": None if body is None else body.stem,
        "choices": None if dumped is None else dumped["choices"],
        "answer": None if dumped is None else dumped["answer"],
        "rationale": None if body is None else body.rationale,
        "difficulty_est": _as_numeric(result.difficulty_est),
        # 계약이 `None`으로 못 박은 자리다(`ItemResult.difficulty_fit: None`) — v1 항상 null.
        "difficulty_fit": None,
        # 🔴 저장 시점에 대응 값이 없다(§2-20 #5) — 없는 값을 지어내지 않는다.
        "difficulty_calib_ver": None,
        # ⚠ 사유(`review_reason`)는 스냅숏 안이다. 이 boolean은 목록에서 "검토 필요"만
        #   거르기 위한 투영이고, boolean 하나로 사유를 되살릴 수 없다(§2-20 #10).
        "review_badge": result.review_reason is not None,
        "status": result.status.value,
    }


def _as_numeric(value: float | None) -> Decimal | None:
    """float → Numeric. `str` 경유는 이진 부동소수 잔여를 컬럼에 흘리지 않기 위해서다."""
    return None if value is None else Decimal(str(value))


class PgProblemItemStore:
    """`problem_item` 영속 — 스냅숏 정본 + 파생 투영. 테넌트 스코프 인스턴스다.

    멱등은 PK로 선다 — `problem_item_id(set_id, slot_index)`가 결정론 uuid5라
    같은 슬롯은 같은 행이고, 내용이 다르면 `ImmutableStoreConflict`다(인메모리와 같은 규약).
    """

    def __init__(
        self,
        *,
        sessionmaker: async_sessionmaker[AsyncSession],
        tenant_id: str,
    ) -> None:
        self._sessionmaker = sessionmaker
        self._tenant_id = tenant_id

    async def save(
        self,
        *,
        set_id: UUID,
        slot_index: int,
        result: ItemResult,
        candidate_ref: str | None,
        item: GeneratedItem | None,
    ) -> StoredProblemItem:
        record = StoredProblemItem(
            item_id=problem_item_id(set_id, slot_index),
            set_id=set_id,
            slot_index=slot_index,
            result=result,
            candidate_ref=candidate_ref,
            item=item,
        )
        async with self._sessionmaker() as session, session.begin():
            await self._require_own_set(session, set_id)
            existing = await self._select_row(session, set_id, slot_index)
            if existing is not None:
                stored = StoredProblemItem.model_validate(existing.snapshot)
                if stored != record:
                    raise ImmutableStoreConflict(
                        f"문항 최종본 멱등 충돌: set={set_id}, slot={slot_index}"
                    )
                return stored
            session.add(
                ProblemItemRow(
                    id=record.item_id,
                    set_id=set_id,
                    snapshot=record.model_dump(mode="json"),
                    passage_id=None,
                    current_revision_no=_INITIAL_REVISION_NO,
                    drop_reason=None,
                    **problem_item_projection(record),
                )
            )
        return record

    async def get(self, set_id: UUID, slot_index: int) -> StoredProblemItem:
        async with self._sessionmaker() as session:
            row = await self._select_row(session, set_id, slot_index)
        if row is None:
            raise LookupError(f"저장되지 않은 문항: set={set_id}, slot={slot_index}")
        # 🔴 스냅숏만 읽는다 — 파생 컬럼으로 재조립하지 않는다.
        return StoredProblemItem.model_validate(row.snapshot)

    async def _select_row(
        self, session: AsyncSession, set_id: UUID, slot_index: int
    ) -> ProblemItemRow | None:
        """부모 `problem_set`을 조인해 테넌트로 거른다(§2-20.3)."""
        statement = (
            select(ProblemItemRow)
            .join(ProblemSetRow, ProblemItemRow.set_id == ProblemSetRow.id)
            .where(
                ProblemSetRow.tenant_id == self._tenant_id,
                ProblemItemRow.set_id == set_id,
                ProblemItemRow.slot_index == slot_index,
            )
        )
        return (await session.execute(statement)).scalar_one_or_none()

    async def _require_own_set(self, session: AsyncSession, set_id: UUID) -> None:
        """남의 테넌트 세트에 슬롯을 심지 못하게 한다 — FK만으로는 안 막힌다."""
        statement = select(ProblemSetRow.id).where(
            ProblemSetRow.id == set_id,
            ProblemSetRow.tenant_id == self._tenant_id,
        )
        if (await session.execute(statement)).scalar_one_or_none() is None:
            raise LookupError(f"다른 테넌트의 세트이거나 없는 세트: {set_id}")
