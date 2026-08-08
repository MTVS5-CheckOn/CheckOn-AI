"""`PackResultStore` — 인메모리와 PG가 **같은 계약**을 지키는가 (99 ㉕ 설계 §4·#23·#24).

설계 문서 §4의 수용 기준 중 구현 후에만 볼 수 있는 둘을 여기서 본다:

- **테스트 10(조건 ⓑ)** — 「스냅숏에서 유도한 값 == 파생 컬럼」 넷 전부. 실 행을 읽어
  대조한다(순수 함수 층은 `tests/ai/db/test_pack_result_projection.py`가 본다).
- **테스트 11(#23)** — 다른 테넌트의 `pack://` ref로 `get`하면 **`None`**(예외 아님).

🔴 **두 구현을 한 테스트로 파라미터화해 묻는다.** 두 벌로 쓰면 따로 늙는다(99 ⑰·㉚가
그 형태였다) — 계약이 갈리는 것을 잡는 것이 이 파일의 목적이다.

⚠ **PG 미가용이면 skip**이다(관례: `test_problem_store_pg_roundtrip.py`). 🔴 skip이
「통과」로 읽히지 않게 **절단 가드**를 둔다 — 대상 구현이 0개면 red다.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Final

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from ai.composition.counsel.stores import (
    CounselPackResultRecord,
    InMemoryPackResultStore,
    PackResultStore,
)
from ai.contracts.composition import DraftStatus, PlanOutcome, StudentResult
from ai.db.models import CounselPackResult as CounselPackResultRow
from ai.db.repositories.pack_store import (
    PackResultConflict,
    PgPackResultStore,
    pack_result_projection,
)

pytestmark = pytest.mark.integration

_NOW: Final = datetime(2026, 8, 8, tzinfo=UTC)
_Scenario = Callable[[async_sessionmaker[AsyncSession]], Awaitable[None]]


async def _with_pg(scenario: _Scenario) -> str:
    from ai.db.models import Base
    from ai.db.settings import get_db_settings

    engine = create_async_engine(get_db_settings().database_url, poolclass=NullPool)
    try:
        try:
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
        except Exception:  # noqa: BLE001 — 접속 불가 → skip
            return "skip"
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)  # checkfirst=True
        await scenario(async_sessionmaker(engine, expire_on_commit=False))
    finally:
        await engine.dispose()
    return "ok"


def _run(scenario: _Scenario) -> None:
    if asyncio.run(_with_pg(scenario)) == "skip":
        pytest.skip("실 PG 미가용 — docker compose -f compose.dev.yml up (99 ⑫)")


def _record(*, tenant_id: str = "t-a") -> CounselPackResultRecord:
    return CounselPackResultRecord(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        class_ref="cl_a1",
        summary="2명 중 1명 생성",
        results=(
            StudentResult(
                student_ref="st_1", draft_id=uuid.uuid4(), status=DraftStatus.GENERATED
            ),
            StudentResult(
                student_ref="st_2",
                status=DraftStatus.REJECTED_INSUFFICIENT,
                fail_reason="evidence_missing",
            ),
        ),
        created_at=_NOW,
        plan_outcome=PlanOutcome.ALL_DROPPED,
        plan_dropped=3,
        emphasis_points={"st_1": ("어휘 정확도",)},
    )


# ── 계약 대칭: 두 구현을 한 테스트로 ──────────────────────────────

_MEMORY: Final = "memory"
_PG: Final = "pg"
_BACKENDS: Final = (_MEMORY, _PG)


def test_the_matrix_covers_both_backends() -> None:
    """🔴 절단 가드 — 대상이 0개나 1개면 「대칭을 봤다」가 거짓이다.

    ⚠ PG가 없어 **skip**되는 것과 **대상이 아예 없는** 것은 다르다. 이 단정은 DB 없이도
    돌아 *"파라미터가 사라졌다"* 를 잡는다.
    """
    assert set(_BACKENDS) == {_MEMORY, _PG}, _BACKENDS


def _exercise(
    store: PackResultStore, record: CounselPackResultRecord
) -> None:
    async def scenario() -> None:
        ref = await store.put(record)
        same = await store.get(ref, tenant_id=record.tenant_id)
        assert same == record, f"왕복이 무손실이 아니다: {same!r}"
        # 🔴 **테스트 11** — 남의 테넌트는 `None`이다. **예외가 아니다**(형제 셋과 대칭 ·
        #    준영님 판정). 여기서 예외를 던지면 비대칭이 반대 방향으로 생긴다.
        assert await store.get(ref, tenant_id="t-other") is None
        # 부재도 `None`이다 — 두 경우가 반환값으로는 같고 **로그로 갈린다**(#23).
        assert await store.get(f"pack://{uuid.uuid4()}", tenant_id=record.tenant_id) is None

    asyncio.run(scenario())


@pytest.mark.parametrize("backend", _BACKENDS)
def test_both_backends_keep_the_same_contract(backend: str) -> None:
    record = _record()
    if backend == _MEMORY:
        _exercise(InMemoryPackResultStore(), record)
        return

    def scenario(sm: async_sessionmaker[AsyncSession]) -> Awaitable[None]:
        async def inner() -> None:
            store = PgPackResultStore(sessionmaker=sm)
            ref = await store.put(record)
            assert await store.get(ref, tenant_id=record.tenant_id) == record
            assert await store.get(ref, tenant_id="t-other") is None
            assert (
                await store.get(f"pack://{uuid.uuid4()}", tenant_id=record.tenant_id)
                is None
            )

        return inner()

    _run(scenario)


# ── 테스트 10(조건 ⓑ): 실 행의 파생 컬럼이 유도값과 같다 ──────────


def test_the_row_columns_match_the_derivation() -> None:
    record = _record()

    def scenario(sm: async_sessionmaker[AsyncSession]) -> Awaitable[None]:
        async def inner() -> None:
            await PgPackResultStore(sessionmaker=sm).put(record)
            async with sm() as session:
                row = (
                    await session.execute(
                        select(CounselPackResultRow).where(
                            CounselPackResultRow.id == record.id
                        )
                    )
                ).scalar_one()
            derived = pack_result_projection(record)
            for name, expected in derived.items():
                assert getattr(row, name) == expected, (
                    f"파생 컬럼 {name}이 유도값과 다르다: "
                    f"{getattr(row, name)!r} != {expected!r}"
                )
            # 🔴 정본은 스냅숏이다 — 컬럼을 읽어 레코드를 재조립하지 않는다.
            assert CounselPackResultRecord.model_validate(row.snapshot) == record

        return inner()

    _run(scenario)


# ── INSERT-only: 같은 id로 두 번 put하면 조용히 덮이지 않는다 ──────


def test_the_same_id_is_not_silently_overwritten() -> None:
    """🔴 **판정 ⓐ(#24)** — 팩 결과는 잡 하나의 종단 기록이고 고쳐 쓸 이유가 없다.

    같은 내용이면 통과(멱등), 내용이 다르면 `PackResultConflict`다 —
    `PgProblemItemStore`와 같은 규약이다. ⚠ **「조용히 덮인다」만은 어느 쪽이든 red**다.
    """
    record = _record()
    other = record.model_copy(update={"summary": "다른 요약"})

    def scenario(sm: async_sessionmaker[AsyncSession]) -> Awaitable[None]:
        async def inner() -> None:
            store = PgPackResultStore(sessionmaker=sm)
            ref = await store.put(record)
            assert await store.put(record) == ref, "같은 내용 재저장이 멱등이 아니다"
            with pytest.raises(PackResultConflict):
                await store.put(other)
            stored = await store.get(ref, tenant_id=record.tenant_id)
            assert stored == record, "충돌인데 내용이 덮였다"

        return inner()

    _run(scenario)
