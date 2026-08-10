"""원장 점검이 **실 PG에서** 도는가 (99 #36 G2 · 🔴 **G1 머지 전에는 절반만 잰다**).

🔴 **G1(FK 제거) 전에는 고아 상태를 만들 수 없다** — `fk_agent_run_run_id_ai_run`이 살아 있어
`AI_RUN` 없는 `AGENT_RUN`을 **INSERT 자체가 안 된다.**

⚠ **우회하지 않는다.** 제약을 로컬에서 임의로 지우거나 **부모 `AI_RUN`을 선삽입**해서
고아를 흉내 내면, **이 결함을 두 달 못 보게 만든 바로 그 형태**가 된다(99 #36 §1-5).
⇒ 고아 축은 **G1 뒤에** `xfail`을 걷고 잰다.

**지금 재는 것** — 조회가 실 PG에서 돌고, 테넌트 격리가 서고, **관측 0건이 「통과」가 아니라
「측정 없음」으로 남는가**.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from typing import Final

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from ai.db.ledger_completeness import LedgerVerdict, judge_ledger_row, summarize
from ai.db.repositories.ledger_audit import PgLedgerAudit
from ai.db.settings import get_db_settings

pytestmark = pytest.mark.integration

_TENANT: Final = f"t_audit_{uuid.uuid4().hex[:8]}"


def _run(scenario: Callable[[async_sessionmaker[AsyncSession]], Awaitable[None]]) -> None:
    async def go() -> str:
        engine = create_async_engine(get_db_settings().database_url, poolclass=NullPool)
        try:
            async with engine.connect():
                pass
        except Exception:  # noqa: BLE001
            await engine.dispose()
            return "skip"
        try:
            await scenario(async_sessionmaker(engine, expire_on_commit=False))
        finally:
            await engine.dispose()
        return "ok"

    if asyncio.run(go()) == "skip":
        pytest.skip("실 PG 미가용 — docker compose up -d")


def test_the_audit_query_runs_against_real_pg() -> None:
    """조회가 실 PG에서 **실제로 돈다** — 쿼리 구조 검사만으로는 이걸 못 본다."""

    async def scenario(sessions: async_sessionmaker[AsyncSession]) -> None:
        rows = await PgLedgerAudit(sessions).observe(tenant_id=_TENANT)
        assert isinstance(rows, list)

    _run(scenario)


def test_an_empty_tenant_is_measurement_absent_not_a_pass() -> None:
    """🔴 새 테넌트는 **0건**이다 — 그것을 「완전성 통과」로 세지 않는다."""

    async def scenario(sessions: async_sessionmaker[AsyncSession]) -> None:
        rows = await PgLedgerAudit(sessions).observe(tenant_id=_TENANT)
        assert rows == [], "격리된 테넌트에 남의 행이 보인다"
        counts = summarize([judge_ledger_row(o) for o in rows])
        assert sum(counts.values()) == 0
        assert set(counts) == set(LedgerVerdict), "판정 키가 사라지면 리더가 0으로 읽는다"

    _run(scenario)


@pytest.mark.xfail(
    reason="G1(fk_agent_run_run_id_ai_run 제거) 머지 전에는 고아 행을 만들 수 없다 — "
    "제약을 임의로 지우거나 부모를 선삽입해 우회하지 않는다(99 #36)",
    strict=True,
)
def test_an_orphan_agent_run_is_flagged() -> None:
    """🔴 **G1 뒤에 이 `xfail`을 걷는다.**

    걷은 뒤 재는 것: `AI_RUN` 없는 `succeeded` counsel 행을 실제로 저장하고,
    점검기가 그것을 `violation`으로 잡는가.
    """
    pytest.fail("G1 미머지 — 고아 상태를 만들 수 없다")
