"""마이그레이션이 **실제로 돈다** — upgrade/downgrade 왕복 (99 #26).

🔴 **이 저장소에서 alembic을 실행하는 유일한 테스트다.** 종전에는 마이그레이션 6개가 전부
`downgrade()`를 갖고도 **실행 검증이 0건**이었다 — CI `integration-pg`는 `create_all`로
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

⚠ `integration` 마커라 기본 실행에서 빠지고 CI `integration-pg`(postgres:16)가 본다.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
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
        pytest.skip("실 PG 미가용 — docker compose -f compose.dev.yml up (99 #26)")
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
