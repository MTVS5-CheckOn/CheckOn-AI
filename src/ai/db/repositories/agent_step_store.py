"""`AGENT_STEP` 행의 **저수준 적재·조회** — capability 타입을 모른다 (99 #37).

🔴 **왜 층을 가르는가.** counsel과 `mapping_probe`는 **각자의 `AgentStepRecord`**를 들고
있고(필드 8개는 같지만 **별개 타입**), 규율상 **capability 간 직접 import가 금지**다.
그래서 두 축이 같은 SQL을 각자 복제하거나, 한쪽 타입으로 강제 변환하거나,
`cast`로 mypy만 침묵시키는 세 오답이 생긴다.

⇒ **SQL은 여기 하나**, **타입은 capability별 adapter**가 든다:

```
agent_step_store.py            ← 이 파일: INSERT/SELECT + 8필드 무손실 snapshot
├─ probe_stores.PgAgentStepSink        → probe AgentStepRecord
└─ counsel_step_store.PgCounselAgentStepSink → counsel AgentStepRecord
```

**이 파일이 하는 것** — ORM INSERT · `agent_run_id`별 조회 · **`seq` 오름차순** ·
8필드 무손실 snapshot.
**하지 않는 것** — capability별 모델 생성 · 마스킹 · LLM 판정 · 워커 상태 전이 ·
테넌트 판정 재설계. ⚠ 테넌트는 **부모 `AGENT_RUN`을 통해** 격리된다
(`agent_step`엔 `tenant_id` 컬럼이 없다 — 실측).

⚠ `tool_args_masked`는 **받은 값을 그대로** 쓴다 — 마스킹은 도구·워커에서 이미 통과했다(§5.2).
"""

from __future__ import annotations

from typing import Any, TypedDict
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ai.db.models import AgentStep as AgentStepRow


class AgentStepSnapshot(TypedDict):
    """`AGENT_STEP` 8필드의 **타입 없는** 표현 — adapter가 자기 모델로 옮긴다.

    🔴 **Pydantic 모델을 두지 않는다.** 여기 하나 더 만들면 **세 번째 정본**이 되고,
    두 capability가 그것으로 강제 변환되는 순간 ⑱(공통 계약 승격)을 **판정 없이** 해 버린다.
    """

    id: UUID
    agent_run_id: UUID
    seq: int
    node_name: str
    tool_called: str | None
    tool_args_masked: dict[str, Any]
    llm_call_id: UUID | None
    outcome: str


class PgAgentStepStore:
    """`AGENT_STEP` 저수준 저장소 — **adapter만 이것을 쓴다.**"""

    def __init__(self, *, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sessionmaker = sessionmaker

    async def insert(self, snapshot: AgentStepSnapshot) -> None:
        """한 행 적재 — ⚠ **`agent_run_id → agent_run.id` FK는 살아 있다.**

        부모 없는 행은 드라이버 예외로 올라간다. **삼켜서 성공으로 번역하지 않는다** —
        그러면 스텝이 어디에도 안 붙은 채 있다고 믿게 된다.
        """
        async with self._sessionmaker() as session, session.begin():
            session.add(AgentStepRow(**snapshot))

    async def select_by_run(self, agent_run_id: UUID) -> tuple[AgentStepSnapshot, ...]:
        """`seq` **오름차순** — 넣은 순서가 아니라 계약 순서로 돌려준다."""
        stmt = (
            select(AgentStepRow)
            .where(AgentStepRow.agent_run_id == agent_run_id)
            .order_by(AgentStepRow.seq)
        )
        async with self._sessionmaker() as session:
            rows = (await session.execute(stmt)).scalars().all()
        return tuple(
            AgentStepSnapshot(
                id=row.id,
                agent_run_id=row.agent_run_id,
                seq=row.seq,
                node_name=row.node_name,
                tool_called=row.tool_called,
                tool_args_masked=row.tool_args_masked,
                llm_call_id=row.llm_call_id,
                outcome=row.outcome,
            )
            for row in rows
        )


__all__ = ["AgentStepSnapshot", "PgAgentStepStore"]
