"""ERD ↔ ORM 대조 — 06_erd.md 가 정본, ai.db.models 가 그걸 정확히 옮겼는지 검사한다.

이 검사가 커밋③의 핵심 안전망이다. 없으면 26테이블을 손으로 옮기다 컬럼 하나가
빠지거나 타입이 어긋나도 조용히 통과한다. ERD를 파싱해(erd_parser) 테이블·컬럼·
타입 카테고리·PK·FK·유니크를 한 항목씩 대조한다.

원칙: ERD에 없는 테이블은 만들지 않는다(B 전용 테이블 부재는 의도 — B가 자기 DB를
나중에 직접 추가). 그래서 대조는 양방향이다(누락도, 초과도 실패).
"""

from __future__ import annotations

import pytest
from erd_parser import ERD_PATH, ErdTable, parse_erd
from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.types import TypeEngine

from ai.db.models import Base

ERD_TABLES: dict[str, ErdTable] = parse_erd(ERD_PATH)
METADATA_TABLES = Base.metadata.tables


def _sa_category(t: TypeEngine[object]) -> str:
    """SQLAlchemy 컬럼 타입을 ERD와 같은 카테고리로 접는다.

    검사 순서 주의: Text ⊂ String, JSONB ⊂ JSON, DateTime·Date 형제 —
    더 좁은 타입을 먼저 본다.
    """
    if isinstance(t, Uuid):
        return "uuid"
    if isinstance(t, Boolean):
        return "boolean"
    if isinstance(t, JSON):  # JSONB ⊂ JSON
        return "json"
    if isinstance(t, DateTime):  # Date 보다 먼저
        return "datetime"
    if isinstance(t, Date):
        return "date"
    if isinstance(t, Text):  # String 보다 먼저
        return "text"
    if isinstance(t, Numeric):
        return "numeric"
    if isinstance(t, Integer):
        return "integer"
    if isinstance(t, String):
        return "string"
    return f"unknown:{type(t).__name__}"


def test_erd_parsed_26_tables() -> None:
    """파서 경로가 틀리면 아래 대조가 조용히 0건 통과한다 — 26테이블 상수로 고정."""
    assert ERD_PATH.is_file()
    assert len(ERD_TABLES) == 26, f"ERD 테이블 수 {len(ERD_TABLES)} != 26"


def test_table_set_matches_erd() -> None:
    """테이블 집합이 ERD와 정확히 일치 — 누락도, ERD에 없는 초과도 실패."""
    erd = set(ERD_TABLES)
    orm = set(METADATA_TABLES)
    assert orm == erd, (
        f"ERD에만: {sorted(erd - orm)} · ORM에만(ERD에 없는 테이블 금지): {sorted(orm - erd)}"
    )


@pytest.mark.parametrize("table_name", sorted(ERD_TABLES), ids=lambda n: n)
def test_table_columns_match_erd(table_name: str) -> None:
    erd = ERD_TABLES[table_name]
    orm = METADATA_TABLES.get(table_name)
    assert orm is not None, f"ORM에 테이블 없음: {table_name}"

    erd_cols = set(erd.columns)
    orm_cols = set(orm.columns.keys())
    assert orm_cols == erd_cols, (
        f"[{table_name}] ERD에만: {sorted(erd_cols - orm_cols)} · "
        f"ORM에만: {sorted(orm_cols - erd_cols)}"
    )


@pytest.mark.parametrize("table_name", sorted(ERD_TABLES), ids=lambda n: n)
def test_column_type_categories_match_erd(table_name: str) -> None:
    erd = ERD_TABLES[table_name]
    orm = METADATA_TABLES.get(table_name)
    assert orm is not None, f"ORM에 테이블 없음: {table_name}"

    for col_name, erd_col in erd.columns.items():
        orm_col = orm.columns.get(col_name)
        assert orm_col is not None, f"[{table_name}] ORM 컬럼 없음: {col_name}"
        actual = _sa_category(orm_col.type)
        assert actual == erd_col.type_category, (
            f"[{table_name}.{col_name}] 타입 카테고리 {actual} != ERD {erd_col.type_category}"
        )


@pytest.mark.parametrize("table_name", sorted(ERD_TABLES), ids=lambda n: n)
def test_primary_keys_match_erd(table_name: str) -> None:
    erd = ERD_TABLES[table_name]
    orm = METADATA_TABLES.get(table_name)
    assert orm is not None, f"ORM에 테이블 없음: {table_name}"

    orm_pk = {c.name for c in orm.primary_key.columns}
    assert orm_pk == erd.pk_columns, (
        f"[{table_name}] PK ORM {sorted(orm_pk)} != ERD {sorted(erd.pk_columns)}"
    )


@pytest.mark.parametrize("table_name", sorted(ERD_TABLES), ids=lambda n: n)
def test_foreign_keys_match_erd(table_name: str) -> None:
    """ERD의 FK 마커 컬럼 = ORM의 물리 FK 컬럼. `…_ref`(MySQL 논리참조)는 FK 아님."""
    erd = ERD_TABLES[table_name]
    orm = METADATA_TABLES.get(table_name)
    assert orm is not None, f"ORM에 테이블 없음: {table_name}"

    orm_fk = {c.name for c in orm.columns if c.foreign_keys}
    assert orm_fk == erd.fk_columns, (
        f"[{table_name}] FK ORM {sorted(orm_fk)} != ERD {sorted(erd.fk_columns)}"
    )


# 유니크 제약은 ERD가 산문·주석에 적어(파싱 취약) 명시 기대셋으로 대조한다.
# 근거: feature_week — 06_erd.md 라인 107 "UNIQUE(tenant·student·week·ver)"
#       idempotency_record — 라인 63-71 + 라인 310 확정 통보(tenant_id·endpoint·idempotency_key)
EXPECTED_UNIQUES: dict[str, set[frozenset[str]]] = {
    "feature_week": {
        frozenset({"tenant_id", "student_ref", "week_start", "feature_version"})
    },
    "idempotency_record": {frozenset({"tenant_id", "endpoint", "idempotency_key"})},
}


@pytest.mark.parametrize("table_name", sorted(EXPECTED_UNIQUES), ids=lambda n: n)
def test_unique_constraints_match_erd(table_name: str) -> None:
    orm = METADATA_TABLES.get(table_name)
    assert orm is not None, f"ORM에 테이블 없음: {table_name}"
    actual = {
        frozenset(c.name for c in con.columns)
        for con in orm.constraints
        if isinstance(con, UniqueConstraint)
    }
    expected = EXPECTED_UNIQUES[table_name]
    assert expected <= actual, (
        f"[{table_name}] 유니크 제약 누락 — 기대 {[sorted(e) for e in expected]}, 실제 "
        f"{[sorted(a) for a in actual]}"
    )
