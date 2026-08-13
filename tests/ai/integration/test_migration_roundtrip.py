"""마이그레이션이 **실제로 돈다** — upgrade/downgrade 왕복 (99 #26).

🔴 **이 저장소에서 alembic을 실행하는 유일한 테스트다.** 종전에는 마이그레이션 6개가 전부
`downgrade()`를 갖고도 **실행 검증이 0건**이었다 — 종전 CI는 `create_all`로
스키마를 만들고, `test_migration_parity.py`는 이름과 달리 **소스 텍스트**만 읽는다
(`def upgrade(` 위치 비교). ⇒ PG 이관 시 **첫 실배포가 첫 실행**이 되는 상태였다.

**실측이 그 공백의 값을 증명했다**(2026-08-08): 손으로 PG를 띄워 돌렸더니 리비전 id가
42자여서 `alembic_version.version_num varchar(32)`에 안 들어갔다. **DDL은 전부 돌고 버전
기록 UPDATE에서만** 죽어 DB가 「스키마는 새것, 기록은 옛것」으로 남는다. 오프라인 SQL
검증으로는 안 보인다 — 그 경로가 `UPDATE alembic_version`을 실제로 치지 않기 때문이다.

⚠ **이 왕복이 검증하지 못하는 축을 적어 둔다**(A 지적 · 안 적으면 *"마이그레이션은
검증됐다"* 로 읽힌다):

  · **빈 DB에서만 돈다.** `0007`의 「행 0건 전제」(server_default 없이 NOT NULL 추가)는
    여기서 안 걸린다 — 데이터가 있는 DB의 업그레이드는 다른 검사다.
  · **데이터 보존을 안 본다.** downgrade가 컬럼을 지우면 값이 사라지는데 그건 정상이다.
  · **동시성·잠금을 안 본다.**

⚠ `integration` 마커라 기본 실행에서 빠지고 PR 전 로컬 검증의 실 PG 단계가 본다.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from pathlib import Path
from typing import Final

import pytest
from alembic import command
from alembic.config import Config
from pg_hint import pg_unavailable
from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from ai.db.models import Base
from ai.db.settings import get_db_settings

pytestmark = pytest.mark.integration

_MIGRATIONS = (
    Path(__file__).resolve().parents[3] / "src" / "ai" / "db" / "migrations"
)
#: alembic이 만드는 원장 테이블 — 우리 metadata에는 없다.
_LEDGER = "alembic_version"


def _alembic_config() -> Config:
    """🔴 **`alembic.ini`를 읽지 않고 코드로 만든다.**

    ini에 한국어 주석이 있는데 `configparser`가 **로케일 인코딩**으로 읽어서, 한국어
    Windows(cp949)에서는 `UnicodeDecodeError`로 죽는다(2026-08-09 실측). 테스트가
    개발자 기기의 로케일에 달려 있으면 그건 검사가 아니다. 필요한 설정은 둘뿐이다.
    """
    config = Config()
    config.set_main_option("script_location", str(_MIGRATIONS))
    # ⚠ URL은 여기서 안 준다 — `env.py`가 `ai.db.settings`에서 읽는 것이 정본이고,
    #   여기서 덮으면 **프로덕션과 다른 경로**를 검증하게 된다.
    return config


#: ⚠ **비동기 드라이버만 쓴다** — 이 저장소에 `psycopg2`가 없다(asyncpg 단일).
#: alembic `command.*`는 동기 호출이지만 `env.py`가 자기 이벤트 루프를 만든다
#: (`asyncio.run(run_migrations_online())`) — 테스트가 루프 밖에서 부르므로 충돌하지 않는다.
async def _run(*statements: str) -> set[str] | None:
    """문장을 하나씩 친다. ⚠ asyncpg는 **한 prepared statement에 여러 문장을 못 넣는다** —
    `"DROP …; CREATE …"` 한 줄로 주면 `PostgresSyntaxError`다(2026-08-09 실측)."""
    engine = create_async_engine(get_db_settings().database_url, poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            if not statements:
                return set(
                    await connection.run_sync(
                        lambda sync: inspect(sync).get_table_names()
                    )
                )
            for statement in statements:
                await connection.execute(text(statement))
            await connection.commit()
            return None
    finally:
        await engine.dispose()


def _tables() -> set[str]:
    return asyncio.run(_run()) or set()


def _reset_schema() -> None:
    asyncio.run(_run("DROP SCHEMA public CASCADE", "CREATE SCHEMA public"))


@pytest.fixture
def clean_database() -> Iterator[None]:
    """빈 스키마에서 시작한다 — 앞선 테스트의 `create_all` 잔재를 걷는다."""
    try:
        asyncio.run(_run("SELECT 1"))
    except Exception:  # noqa: BLE001 — 접속 불가 → skip
        pytest.skip(pg_unavailable("(99 #26)"))
    _reset_schema()
    try:
        yield
    finally:
        _reset_schema()


def test_upgrade_head_creates_exactly_the_model_tables(clean_database: None) -> None:
    """🔴 `upgrade head`가 실제로 돌고, 만든 테이블이 모델과 정확히 같다.

    `test_migration_parity`는 **소스 텍스트**로 같은 것을 보는데, 그건 `op.create_table`이
    적혀 있다는 사실만 본다. 여기는 **DB가 실제로 그 테이블을 갖는지**를 본다.
    """
    command.upgrade(_alembic_config(), "head")

    created = _tables()
    assert _LEDGER in created, "버전 원장이 없다 — upgrade가 기록 단계까지 안 갔다"
    assert created - {_LEDGER} == set(Base.metadata.tables)


def test_the_version_row_survives_the_upgrade(clean_database: None) -> None:
    """🔴 **DDL이 아니라 「버전 기록」을 본다** — 42자 리비전 id가 죽은 자리가 여기다.

    `alembic_version.version_num`이 `varchar(32)`라 긴 id는 DDL을 다 돌고 **이 UPDATE에서만**
    죽는다. 테이블만 세면 그 실패가 안 보인다.
    """
    command.upgrade(_alembic_config(), "head")

    recorded = asyncio.run(_version_rows())
    assert len(recorded) == 1
    assert len(recorded[0]) <= 32


def test_downgrade_base_leaves_only_the_ledger(clean_database: None) -> None:
    """downgrade가 자기가 만든 것을 전부 되돌린다 — 6개 전부 실행된다."""
    config = _alembic_config()
    command.upgrade(config, "head")
    command.downgrade(config, "base")

    assert _tables() - {_LEDGER} == set()


def test_the_roundtrip_is_repeatable(clean_database: None) -> None:
    """🔴 왕복 뒤 다시 올라간다 — downgrade가 남긴 잔재로 두 번째 upgrade가 죽지 않는다.

    한 번만 보면 *"내려는 갔다"* 까지다. 제약·인덱스를 안 지우고 내려가면 **두 번째
    upgrade에서** 중복 생성으로 죽는데, 그건 실배포에서 롤백 뒤 재배포할 때 난다.
    """
    config = _alembic_config()
    command.upgrade(config, "head")
    command.downgrade(config, "base")
    command.upgrade(config, "head")

    assert _tables() - {_LEDGER} == set(Base.metadata.tables)


async def _version_rows() -> list[str]:
    """버전 원장의 행 — 이 값이 32자를 넘으면 upgrade가 여기서 죽는다."""
    engine = create_async_engine(get_db_settings().database_url, poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            result = await connection.execute(
                text(f"SELECT version_num FROM {_LEDGER}")  # noqa: S608 — 상수다
            )
            return [str(row[0]) for row in result]
    finally:
        await engine.dispose()


# ── (㉻ · 지시서 73 §2-3) 0009 ↔ 0010 단계 왕복 ──


_BUNDLE_TABLE: Final = "counsel_context_bundle"
_PREVIOUS: Final = "0009_drop_agent_run_ai_fk"
_TARGET: Final = "0010_counsel_ctx_draft_body"

#: 🔴 **0010이 건드리면 안 되는 것** — AI_RUN FK 다섯 중 `draft`의 것과 `draft`의 기존 컬럼.
#: ⚠ 이름을 적어 두지 않으면 *"컬럼이 늘었다"* 만 보고 **없어진 것을 못 본다.**
_DRAFT_COLUMNS_0009: Final = frozenset(
    {
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
    }
)


async def _columns(table: str) -> dict[str, bool]:
    """컬럼명 → nullable."""
    engine = create_async_engine(get_db_settings().database_url, poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            rows = await connection.execute(
                text(
                    "SELECT column_name, is_nullable FROM information_schema.columns"
                    " WHERE table_name = :t"
                ),
                {"t": table},
            )
            return {str(row[0]): row[1] == "YES" for row in rows}
    finally:
        await engine.dispose()


async def _draft_foreign_keys() -> set[str]:
    engine = create_async_engine(get_db_settings().database_url, poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            rows = await connection.execute(
                text(
                    "SELECT conname FROM pg_constraint"
                    " WHERE conrelid = 'draft'::regclass AND contype = 'f'"
                )
            )
            return {str(row[0]) for row in rows}
    finally:
        await engine.dispose()


def test_the_0010_step_adds_exactly_the_bundle_and_the_body(
    clean_database: None,
) -> None:
    """🔴 **0009 → 0010 → 0009 → 0010** 을 단계마다 직접 센다 (㉻ §2-3).

    ⚠ `head` 왕복은 *"전부 돌았다"* 까지다. **이 단계가 무엇을 더하고 무엇을 그대로 두는가**는
    거기서 안 보인다 — downgrade가 남의 컬럼을 지워도 다시 올라가면 같은 모습이 된다.
    """
    config = _alembic_config()

    #: ── 0009: 아직 둘 다 없다 ──
    command.upgrade(config, _PREVIOUS)
    assert _BUNDLE_TABLE not in _tables(), "0009인데 묶음 테이블이 있다"
    draft_0009 = asyncio.run(_columns("draft"))
    assert "content" not in draft_0009, "0009인데 본문 컬럼이 있다"
    assert set(draft_0009) == _DRAFT_COLUMNS_0009, set(draft_0009)
    fks_0009 = asyncio.run(_draft_foreign_keys())
    assert fks_0009, "draft의 AI_RUN FK가 0009에 없다 — 이 검사의 전제가 깨졌다"

    #: ── 0010: 둘 다 생기고 나머지는 그대로 ──
    command.upgrade(config, _TARGET)
    assert _BUNDLE_TABLE in _tables()
    bundle = asyncio.run(_columns(_BUNDLE_TABLE))
    assert set(bundle) == {
        "id",
        "tenant_id",
        "class_ref",
        "contexts",
        "content_hash",
        "created_at",
    }, set(bundle)
    assert not any(bundle.values()), f"nullable 컬럼이 있다: {bundle}"
    draft_0010 = asyncio.run(_columns("draft"))
    assert "content" in draft_0010, "본문 컬럼이 안 생겼다"
    assert draft_0010["content"] is False, "🔴 content가 nullable이다 — 조용히 낮춰졌다"
    assert set(draft_0010) - {"content"} == _DRAFT_COLUMNS_0009, (
        f"0010이 draft의 다른 컬럼을 건드렸다: {set(draft_0010)}"
    )
    assert asyncio.run(_draft_foreign_keys()) == fks_0009, "draft의 FK가 바뀌었다"
    assert asyncio.run(_version_rows()) == [_TARGET]
    assert len(_TARGET) <= 32

    #: ── downgrade 0009: 자기가 만든 것만 사라진다 ──
    command.downgrade(config, _PREVIOUS)
    assert _BUNDLE_TABLE not in _tables(), "downgrade가 묶음 테이블을 안 지웠다"
    back = asyncio.run(_columns("draft"))
    assert set(back) == _DRAFT_COLUMNS_0009, (
        f"downgrade가 draft를 0009 상태로 못 되돌렸다: {set(back)}"
    )
    assert asyncio.run(_draft_foreign_keys()) == fks_0009, "downgrade가 FK를 건드렸다"
    assert asyncio.run(_version_rows()) == [_PREVIOUS]

    #: ── 다시 0010: 잔재로 두 번째 upgrade가 죽지 않는다(인덱스 중복 등) ──
    command.upgrade(config, _TARGET)
    assert _BUNDLE_TABLE in _tables()
    assert asyncio.run(_columns("draft"))["content"] is False


#: 🔴 **0010 테스트의 `_PREVIOUS`·`_TARGET`을 재사용하지 않는다** — 그쪽이 쓰고 있다.
_SIGNAL_PREVIOUS: Final = "0010_counsel_ctx_draft_body"
_SIGNAL_TARGET: Final = "0011_signal_comparison"

#: 0011이 더하는 것 — 이 넷 말고는 아무것도 안 바뀌어야 한다.
_SIGNAL_ADDED: Final = frozenset({"metric", "observed", "baseline", "sample_size"})

#: 🔴 **0010 시점 `signal` 컬럼 전량** — 이름을 적어 두지 않으면 *"넷이 늘었다"* 만 보고
#: **없어진 것을 못 본다**(0010 선례의 `_DRAFT_COLUMNS_0009`와 같은 이유).
_SIGNAL_COLUMNS_0010: Final = frozenset(
    {
        "id",
        "run_id",
        "tenant_id",
        "student_ref",
        "rule_id",
        "signal_type",
        "display_label",
        "lifecycle",
        "score",
        "rank",
        "created_at",
    }
)


@pytest.mark.integration
def test_the_0011_step_adds_exactly_the_four_nullable_comparison_columns(
    clean_database: None,
) -> None:
    """🔴 **0010 → 0011 → 0010 → 0011** 을 단계마다 직접 센다 (99 #59·#60).

    🔴 **이 검사가 마이그레이션의 유일한 방어다.** `test_migration_parity`는
    `op.create_table`·`op.drop_table`만 정규식으로 센다 — **`op.add_column`은 아무도 안
    본다.** ⇒ 마이그레이션을 통째로 빼먹어도 **오프라인은 전부 초록이다**(실측).

    ⚠ **넷이 nullable인지 반드시 본다.** `NOT NULL`로 조용히 올라가면 **기존 행이 있는
    DB에서 업그레이드가 실패한다** — 이 저장소의 실 PG는 비어 있어 그 사고가 여기서는
    안 보이고 배포에서만 터진다.
    """
    config = _alembic_config()

    #: ── 0010: 넷 다 없다 ──
    command.upgrade(config, _SIGNAL_PREVIOUS)
    signal_0010 = asyncio.run(_columns("signal"))
    assert not (_SIGNAL_ADDED & set(signal_0010)), set(signal_0010)
    assert set(signal_0010) == _SIGNAL_COLUMNS_0010, set(signal_0010)

    #: ── 0011: 넷이 생기고 나머지는 그대로 ──
    command.upgrade(config, _SIGNAL_TARGET)
    signal_0011 = asyncio.run(_columns("signal"))
    assert _SIGNAL_ADDED <= set(signal_0011), set(signal_0011)
    #: 🔴 **넷 다 nullable** — 값의 부재가 정상이고, 기존 행에 채울 참값이 없다
    assert all(signal_0011[name] for name in _SIGNAL_ADDED), signal_0011
    #: 🔴 **나머지가 그대로다** — 넷을 빼면 0010과 같은 집합이어야 한다
    assert set(signal_0011) - _SIGNAL_ADDED == _SIGNAL_COLUMNS_0010, set(signal_0011)
    assert asyncio.run(_version_rows()) == [_SIGNAL_TARGET]
    assert len(_SIGNAL_TARGET) <= 32

    #: ── downgrade 0010: 자기가 만든 것만 사라진다 ──
    command.downgrade(config, _SIGNAL_PREVIOUS)
    assert set(asyncio.run(_columns("signal"))) == _SIGNAL_COLUMNS_0010

    #: ── 다시 0011: 같은 모습으로 돌아온다 ──
    command.upgrade(config, _SIGNAL_TARGET)
    assert set(asyncio.run(_columns("signal"))) - _SIGNAL_ADDED == _SIGNAL_COLUMNS_0010
