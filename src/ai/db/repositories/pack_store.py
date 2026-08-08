"""`PackResultStore` PG 구현 — 팩 결과 영속 (99 ㉕ · A 본문 설계 2026-08-08).

`AGENT_RUN.result_ref`(`pack://{id}`)가 가리키던 대상이 여태 없어 인메모리 dict가
그 자리를 대신했다. `counsel_pack_result` 테이블이 서면서(#168) 이 파일이 성립한다.

🔴 **정본은 `snapshot` 한 곳이다.** `get`은 스냅숏만 읽는다 — 파생 컬럼을 읽어 레코드를
**재조립하지 않는다.** 재조립하면 컬럼이 계약보다 좁아지는 순간 조용히 값이 깎이고,
그것이 `PROBLEM_ITEM`에서 결손 12건을 낳은 형태다(선례: `problem_store.py`).

승인 조건 셋(㉕ A 판정 §2)이 이 파일에 산다:

- **ⓐ** 파생 투영은 `pack_result_projection()` **한 함수**가 스냅숏에서 유도한다 —
  호출자가 스냅숏과 컬럼 값을 따로 넘길 수 있는 자리를 두지 않는다.
- **ⓑ** 갈리면 red — `tests/ai/db/test_pack_result_projection.py`(순수 함수 층)와
  `tests/ai/integration/test_pack_result_pg_roundtrip.py`(실 행 층)가 함께 단정한다.
- **ⓒ** 어느 쪽이 정본인지는 `db/models.py`의 `CounselPackResult` docstring에 적혀 있다.

⚠ **조건 ⓓ(출력측 마스킹)는 8/8에 열렸다** — `emphasis_points`가 포착 시점에 마스킹을
거친다(99 #25 · `GatewayPlanner._mask_plan_output`). 그 판정 전에는 이 파일을 만들지
않았다. ⚠ **커버리지는 fail-closed가 아니다**(99 #28) — 문맥 신호 없는 이름꼴은 안 잡힌다.

🔴 **테넌트 술어는 인자다** — `PgProblemItemStore`가 생성자 주입인 것과 다르다. 그쪽은
Protocol에 `tenant_id`가 없어 인스턴스를 스코프해야 했지만 여기는 `get(ref, *, tenant_id)`가
**계약**이다(99 #23). 형태를 억지로 맞추지 않았다.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ai.composition.counsel.stores import (
    PACK_SCHEME,
    CounselPackResultRecord,
    make_ref,
    parse_ref,
)
from ai.db.models import CounselPackResult as CounselPackResultRow

logger = logging.getLogger(__name__)


class PackResultConflict(RuntimeError):
    """같은 `id`에 서로 다른 팩 결과를 쓰려 함 — 불변 레코드 위반.

    ⚠ **이름을 `ImmutableStoreConflict`와 갈라 뒀다** — 그쪽은 B 소유
    `problem_generation/application/ports.py`에 살고 capability 간 직접 import는 금지다
    (CLAUDE.md §6). **규약은 같고 층이 다르다**(99 ㉳와 같은 자리).
    """


#: 🔴 **파생이 아닌 컬럼** — `pack_result_projection()`이 내지 않는다.
#: `id`는 PK(참조 키 자신)이고 `snapshot`은 정본이다. ⚠ 여기 등재하는 것은
#: **「투영을 안 붙였다」가 아니라 「투영 대상이 아니다」**를 뜻한다 — 이유 없이 넣으면
#: 조건 ⓑ의 red가 그 컬럼만 조용히 비껴간다.
NON_PROJECTED_COLUMNS: frozenset[str] = frozenset({"id", "snapshot"})


def pack_result_projection(record: CounselPackResultRecord) -> dict[str, Any]:
    """스냅숏 → 조회용 파생 투영 (조건 ⓐ · 순수 함수).

    🔴 **이 함수만이 파생 컬럼을 만든다.** 저장 호출자는 `CounselPackResultRecord` 하나만
    넘기고 컬럼 값을 따로 넘기지 않는다 — 두 인자를 받는 순간 호출부마다 다른 값을 넣을
    수 있고, 그러면 정본과 투영이 갈린다(99 #20이 정확히 그 형태였다).

    ⚠ **투영 넷의 이유가 같지 않다**(설계 §1): `tenant_id`=격리 술어 ·
    `created_at`=보존기간·정리 배치 축 · `plan_outcome`=「강조점 0건, 왜」 집계 축 ·
    **`class_ref`만 조회가 아니라 파기 술어**다. ⚠ `results`·`emphasis_points`·`summary`·
    `plan_dropped`는 **투영하지 않는다** — 학생별 조회 축은 `DRAFT`가 이미 갖는다.
    """
    return {
        "tenant_id": record.tenant_id,
        "class_ref": record.class_ref,
        # ⚠ enum이 아니라 **값**이다 — 컬럼이 `String`이고 ERD 값목록 대조가 문자열을 본다.
        "plan_outcome": record.plan_outcome.value,
        "created_at": record.created_at,
    }


class PgPackResultStore:
    """`counsel_pack_result` 영속 — 스냅숏 정본 + 파생 투영. **INSERT-only**다.

    🔴 **UPSERT를 만들지 않는다**(#24 판정 ⓐ) — 팩 결과는 잡 하나의 **종단 기록**이고 같은
    행을 고쳐 쓸 정당한 이유가 없다. ⚠ 닫히는 이유가 *"UPSERT를 안 만든다"* 가 아니라
    **"UPSERT가 성립할 키가 없다"** 다: `worker.py`가 `id=self._new_id()`로 **매 호출 새
    UUID**를 만들어 「같은 키」가 애초에 안 생긴다(결정론 PK인 `PgProblemItemStore`와 다르다).

    ⇒ 🔴 **그래서 아래 멱등 분기는 「도달 미실증 방어」다.** 같은 `id`가 두 번 오는 경로가
    현재는 없지만, 없다고 UPSERT를 열어 두면 그날 조용히 덮인다.

    ⚠ **재시도는 고아 행을 남긴다**(#24) — `_store_pack`(⑤) 뒤 `succeed`(⑥) 앞에서 죽으면
    재개가 ⑤를 다시 돌고 **새 `id`**로 새 행을 만든다. `result_ref`는 나중 것만 가리키니
    **앞 행이 고아**다. 인메모리는 프로세스와 함께 사라지지만 **PG는 남는다** —
    이 파일이 고칠 축이 아니다(`worker.py`의 id 생성 + 재개 규약).
    """

    def __init__(self, *, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sessionmaker = sessionmaker
        #: 🔴 **두 경우를 갈라 센다**(#23 · 준영님 판정) — 반환값은 둘 다 `None`이라
        #: 운영에서 *"왜 404인가"* 를 물으면 **반환값으로는 답이 안 나온다.**
        #: ⚠ 로그만으로는 **테스트가 셀 수 없어** 리더를 만들 수 없다(로그 59) ⇒ 카운터다.
        self.miss_absent = 0
        self.miss_foreign_tenant = 0

    async def put(self, record: CounselPackResultRecord) -> str:
        async with self._sessionmaker() as session, session.begin():
            existing = await self._select_row(session, record.id)
            if existing is not None:
                stored = CounselPackResultRecord.model_validate(existing.snapshot)
                if stored != record:
                    raise PackResultConflict(f"팩 결과 멱등 충돌: id={record.id}")
                return make_ref(PACK_SCHEME, record.id)
            session.add(
                CounselPackResultRow(
                    id=record.id,
                    snapshot=record.model_dump(mode="json"),
                    **pack_result_projection(record),
                )
            )
        return make_ref(PACK_SCHEME, record.id)

    async def get(
        self, ref: str, *, tenant_id: str
    ) -> CounselPackResultRecord | None:
        """`pack://` 역참조 — 불일치·부재 **둘 다 `None`**(형제 셋과 대칭 · 99 #23).

        🔴 **반환값은 같고 로그가 갈린다.** 예외로 가르면 비대칭이 반대 방향으로 생기고
        (`ContextStore`·`DraftResultStore`·pg가 전부 `None`), 라우터는 `None`을
        `LLM_FAILED`/`job_no_result`로 번역한다 — 그 번역을 바꾸는 것은 04 계약 축이다.

        ⚠ **로그에 스냅숏 내용을 싣지 않는다** — `ref`·`tenant_id`·판정만이다.
        """
        pack_id = parse_ref(ref, PACK_SCHEME)
        async with self._sessionmaker() as session:
            row = await self._select_row(session, pack_id)
        if row is None:
            self.miss_absent += 1
            logger.info(
                "팩 결과 역참조 실패 — **행이 없다** ref=%s tenant_id=%s "
                "(잡이 결과를 저장하기 전이거나 참조가 낡았다)",
                ref,
                tenant_id,
            )
            return None
        if row.tenant_id != tenant_id:  # 저장소 수준 격리
            self.miss_foreign_tenant += 1
            logger.warning(
                "팩 결과 역참조 거부 — **남의 테넌트 행이다** ref=%s tenant_id=%s "
                "(행은 있다 · 상위의 잡 조회가 이미 걸러야 하는 자리다 · 99 #23)",
                ref,
                tenant_id,
            )
            return None
        return CounselPackResultRecord.model_validate(row.snapshot)

    async def _select_row(
        self, session: AsyncSession, pack_id: UUID
    ) -> CounselPackResultRow | None:
        result = await session.execute(
            select(CounselPackResultRow).where(CounselPackResultRow.id == pack_id)
        )
        return result.scalar_one_or_none()
