"""원장 점검이 **실 PG에서** 도는가 (99 #36 G2 · 지시서 59 보완).

🔴 **G1 대기를 「무조건 fail + xfail」로 두지 않는다.** 그러면 **G1이 머지돼도 영원히 xfail**이라
관문이 열린 사실을 아무도 모른다. ⇒ **본문은 지금부터 진짜 고아 시나리오를 돌리고**,
`xfail` 조건은 **ORM 메타데이터의 FK 실재 여부**에서 파생한다.
G1로 그 FK가 사라지면 조건이 **자동으로 거짓**이 되고 본 단정까지 도달한다.

⚠ **우회하지 않는다** — 테스트 중 `DROP CONSTRAINT`도, **부모 `AI_RUN` 선삽입**도 없다.
**그게 이 결함을 두 달 못 보게 만든 형태다**(99 #36 §1-5).
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from typing import Final

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from ai.contracts.execution import Capability
from ai.db.ledger_completeness import LedgerVerdict, judge_ledger_row, summarize
from ai.db.models import AgentRun
from ai.db.repositories.ledger_audit import PgLedgerAudit
from ai.db.settings import get_db_settings

pytestmark = pytest.mark.integration

_TENANT: Final = f"t_audit_{uuid.uuid4().hex[:8]}"
_OTHER: Final = f"t_other_{uuid.uuid4().hex[:8]}"
_HASH: Final = "sha256:" + "a" * 64


def _agent_run_ai_run_fk_still_exists() -> bool:
    """🔴 **G1 만료 조건을 ORM 정본에서 파생한다** — 날짜·수동 boolean·파일명 금지."""
    return any(
        fk.column.table.name == "ai_run"
        for column in AgentRun.__table__.columns
        if column.name == "run_id"
        for fk in column.foreign_keys
    )


def _run(scenario: Callable[[async_sessionmaker[AsyncSession]], Awaitable[None]]) -> None:
    async def go() -> str:
        engine = create_async_engine(get_db_settings().database_url, poolclass=NullPool)
        try:
            async with engine.connect():
                pass
        except Exception:  # noqa: BLE001
            await engine.dispose()
            return "skip"
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        try:
            await scenario(sessions)
        finally:
            await _clean(sessions)
            await engine.dispose()
        return "ok"

    if asyncio.run(go()) == "skip":
        pytest.skip("실 PG 미가용 — docker compose up -d")


async def _clean(sessions: async_sessionmaker[AsyncSession]) -> None:
    async with sessions() as session, session.begin():
        for tenant in (_TENANT, _OTHER):
            await session.execute(
                text("DELETE FROM agent_run WHERE tenant_id = :t"), {"t": tenant}
            )
            await session.execute(
                text("DELETE FROM ai_run WHERE tenant_id = :t"), {"t": tenant}
            )


async def _insert_ai_run(
    sessions: async_sessionmaker[AsyncSession],
    *,
    execution_id: uuid.UUID,
    tenant_id: str,
    capability: str = Capability.COMPOSITION.value,
) -> None:
    async with sessions() as session, session.begin():
        await session.execute(
            text(
                "INSERT INTO ai_run (execution_id, tenant_id, capability, pipeline_version,"
                " engine_version, schema_version, contract_version, input_snapshot_hash,"
                " created_at) VALUES (:x, :t, :c, '0.1', 'e', '0.1', '0.1', :h, now())"
            ),
            {"x": execution_id, "t": tenant_id, "c": capability, "h": _HASH},
        )


async def _insert_agent_run(
    sessions: async_sessionmaker[AsyncSession],
    *,
    run_id: uuid.UUID,
    tenant_id: str,
    status: str,
) -> uuid.UUID:
    job_id = uuid.uuid4()
    async with sessions() as session, session.begin():
        await session.execute(
            text(
                "INSERT INTO agent_run (id, run_id, tenant_id, agent_kind, operation,"
                " payload_ref, payload_hash, priority_class, dispatch_attempt,"
                " lease_generation, recovery_count, max_recovery_attempts,"
                " state_checkpoint, progress, status, queued_at, updated_at)"
                " VALUES (:j, :x, :t, 'counsel_pack', 'counsel_pack.generate',"
                " 'context://p', :h, 'batch', 0, 0, 0, 3, '{}', '0', :s, now(), now())"
            ),
            {"j": job_id, "x": run_id, "t": tenant_id, "h": _HASH, "s": status},
        )
    return job_id


def test_the_audit_query_runs_against_real_pg() -> None:
    """조회가 실 PG에서 **실제로 돈다** — 쿼리 구조 검사만으로는 못 본다."""

    async def scenario(sessions: async_sessionmaker[AsyncSession]) -> None:
        assert isinstance(await PgLedgerAudit(sessions).observe(tenant_id=_TENANT), list)

    _run(scenario)


def test_an_empty_tenant_is_measurement_absent_not_a_pass() -> None:
    """🔴 새 테넌트는 **0건** — 「완전성 통과」로 세지 않는다."""

    async def scenario(sessions: async_sessionmaker[AsyncSession]) -> None:
        rows = await PgLedgerAudit(sessions).observe(tenant_id=_TENANT)
        assert rows == [], "격리된 테넌트에 남의 행이 보인다"
        counts = summarize([judge_ledger_row(o) for o in rows])
        assert sum(counts.values()) == 0
        assert set(counts) == set(LedgerVerdict), "판정 키가 사라지면 리더가 0으로 읽는다"

    _run(scenario)


def test_a_healthy_pair_passes() -> None:
    """정상 결합 — 같은 테넌트의 `AI_RUN`이 있으면 `ok`."""

    async def scenario(sessions: async_sessionmaker[AsyncSession]) -> None:
        run_id = uuid.uuid4()
        await _insert_ai_run(sessions, execution_id=run_id, tenant_id=_TENANT)
        await _insert_agent_run(
            sessions, run_id=run_id, tenant_id=_TENANT, status="succeeded"
        )
        findings = [
            judge_ledger_row(o)
            for o in await PgLedgerAudit(sessions).observe(tenant_id=_TENANT)
        ]
        assert [f.verdict for f in findings] == [LedgerVerdict.OK], [
            f.reason for f in findings
        ]

    _run(scenario)


def test_a_run_id_owned_by_another_tenant_is_caught() -> None:
    """🔴 **이 축은 G1 전에도 잰다** — FK는 `execution_id`만 보므로 INSERT가 된다.

    종전 조회는 `AI_RUN`을 테넌트까지 걸어 조인해 **열이 전부 `None`**이 됐고,
    판정이 **`allowed_absence`**로 통과했다(실측 8/10).
    """

    async def scenario(sessions: async_sessionmaker[AsyncSession]) -> None:
        run_id = uuid.uuid4()
        await _insert_ai_run(sessions, execution_id=run_id, tenant_id=_OTHER)
        await _insert_agent_run(
            sessions, run_id=run_id, tenant_id=_TENANT, status="queued"
        )
        observations = await PgLedgerAudit(sessions).observe(tenant_id=_TENANT)
        assert len(observations) == 1
        observation = observations[0]
        assert observation.ai_run_execution_id is None, "남의 원장이 결합됐다"
        assert observation.run_id_exists_in_other_tenant is True
        finding = judge_ledger_row(observation)
        assert finding.verdict is LedgerVerdict.VIOLATION, finding.reason
        #: 🔴 **남의 테넌트 식별자를 관측에 안 싣는다** — 존재 여부만 받는다.
        assert _OTHER not in finding.reason

    _run(scenario)


def test_the_other_tenant_is_not_leaked_into_the_scan() -> None:
    """테넌트 격리 — 남의 행이 관측에 안 들어온다."""

    async def scenario(sessions: async_sessionmaker[AsyncSession]) -> None:
        run_id = uuid.uuid4()
        await _insert_ai_run(sessions, execution_id=run_id, tenant_id=_OTHER)
        await _insert_agent_run(
            sessions, run_id=run_id, tenant_id=_OTHER, status="succeeded"
        )
        assert await PgLedgerAudit(sessions).observe(tenant_id=_TENANT) == []

    _run(scenario)


def test_an_unknown_capability_does_not_kill_the_scan() -> None:
    """🔴 낯선 `capability` 하나가 **점검 전체를 예외로 죽이면 안 된다.**"""

    async def scenario(sessions: async_sessionmaker[AsyncSession]) -> None:
        odd, fine = uuid.uuid4(), uuid.uuid4()
        await _insert_ai_run(
            sessions, execution_id=odd, tenant_id=_TENANT, capability="future_unknown"
        )
        await _insert_ai_run(sessions, execution_id=fine, tenant_id=_TENANT)
        await _insert_agent_run(
            sessions, run_id=odd, tenant_id=_TENANT, status="succeeded"
        )
        await _insert_agent_run(
            sessions, run_id=fine, tenant_id=_TENANT, status="succeeded"
        )
        findings = [
            judge_ledger_row(o)
            for o in await PgLedgerAudit(sessions).observe(tenant_id=_TENANT)
        ]
        counts = summarize(findings)
        assert len(findings) == 2, "낯선 값 때문에 다른 행이 사라졌다"
        assert counts[LedgerVerdict.VIOLATION] == 1
        assert counts[LedgerVerdict.OK] == 1

    _run(scenario)


@pytest.mark.xfail(
    condition=_agent_run_ai_run_fk_still_exists(),
    reason="G1(fk_agent_run_run_id_ai_run 제거) 대기 — 고아 행을 만들 수 없다. "
    "제약을 임의로 지우거나 부모를 선삽입해 우회하지 않는다(99 #36)",
    strict=True,
)
def test_an_orphan_agent_run_is_flagged() -> None:
    """🔴 **G1이 머지되면 이 테스트가 자동으로 살아난다** — 조건이 ORM FK에서 온다.

    지금은 `AI_RUN` 없는 `AGENT_RUN`을 넣는 **INSERT 자체가 FK 위반**이라 `xfail`이고,
    G1 뒤에는 삽입이 성공해 **아래 단정까지 도달**한다.
    """

    async def scenario(sessions: async_sessionmaker[AsyncSession]) -> None:
        await _insert_agent_run(
            sessions, run_id=uuid.uuid4(), tenant_id=_TENANT, status="succeeded"
        )
        findings = [
            judge_ledger_row(o)
            for o in await PgLedgerAudit(sessions).observe(tenant_id=_TENANT)
        ]
        assert [f.verdict for f in findings] == [LedgerVerdict.VIOLATION], [
            f.reason for f in findings
        ]

    _run(scenario)
