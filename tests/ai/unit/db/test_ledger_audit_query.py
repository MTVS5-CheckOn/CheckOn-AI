"""원장 점검 조회가 **테넌트 범위를 강제하는가** (99 #36 G2 · CLAUDE.md §4).

🔴 **DB 없이 본다** — 쿼리 구조만 재는 검사라 실 PG가 필요 없다.
"""

from __future__ import annotations

import pytest

from ai.db.repositories.ledger_audit import observation_query


def test_an_empty_tenant_is_refused() -> None:
    """🔴 **전 테넌트 전수 조회를 만들 수 없어야 한다** — 문 앞에서 막는다."""
    with pytest.raises(ValueError):
        observation_query("")


def test_the_where_clause_binds_the_tenant() -> None:
    compiled = str(observation_query("t_scope"))
    assert "agent_run.tenant_id = " in compiled, f"테넌트 술어가 없다:\n{compiled}"


def test_the_ledger_join_also_binds_the_tenant() -> None:
    """🔴 **조인에도 테넌트를 건다** — `execution_id`만으로 이으면 **남의 원장이 붙는다.**

    그러면 「원장이 있다」가 거짓이 되고, 이 점검이 **정반대 방향으로** 틀린다.
    """
    compiled = str(observation_query("t_scope"))
    join = compiled.split("LEFT OUTER JOIN ai_run")[1].split("WHERE")[0]
    assert "tenant_id" in join, f"AI_RUN 조인에 테넌트가 없다:\n{join}"


def test_the_query_is_read_only() -> None:
    compiled = str(observation_query("t_scope")).upper()
    for verb in ("INSERT", "UPDATE", "DELETE"):
        assert verb not in compiled, f"점검 쿼리에 {verb}가 있다 — 읽기 전용이어야 한다"
