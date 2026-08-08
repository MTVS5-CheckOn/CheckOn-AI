"""전체 마이그레이션 체인 ↔ metadata 대조 — 세 정본의 드리프트를 막는다.

test_erd_model_parity 가 ERD==모델 을 강제하고, 이 검사가 모델==마이그레이션 을 강제해
루프를 닫는다. 마이그레이션은 metadata 에서 생성했으므로 지금은 일치하지만, 누가
모델만 고치고 마이그레이션을 안 만들면(혹은 반대로) 여기서 걸린다.

대조는 정적 파싱이다(DB 불필요) — 오프라인 테스트 전략(fake + offline SQL)에 맞춘다.
"""

from __future__ import annotations

import re
from pathlib import Path

from ai.db.models import Base

_VERSIONS = Path(__file__).resolve().parents[3] / "src" / "ai" / "db" / "migrations" / "versions"

_CREATE = re.compile(r"op\.create_table\(\s*['\"](\w+)['\"]")
_DROP = re.compile(r"op\.drop_table\(\s*['\"](\w+)['\"]")


def _split_upgrade_downgrade(src: str) -> tuple[str, str]:
    up_idx = src.index("def upgrade(")
    down_idx = src.index("def downgrade(")
    return src[up_idx:down_idx], src[down_idx:]


def _migration_paths() -> tuple[Path, ...]:
    return tuple(sorted(_VERSIONS.glob("[0-9]*.py")))


def test_migration_chain_exists() -> None:
    paths = _migration_paths()
    assert paths, "마이그레이션 파일이 없다"
    assert paths[0].name == "0001_initial_schema.py"


def test_upgrades_create_exactly_metadata_tables() -> None:
    created = {
        table
        for path in _migration_paths()
        for table in _CREATE.findall(_split_upgrade_downgrade(path.read_text(encoding="utf-8"))[0])
    }
    expected = set(Base.metadata.tables)
    assert created == expected, (
        f"마이그레이션에만: {sorted(created - expected)} · 모델에만: {sorted(expected - created)}"
    )


def test_downgrades_drop_exactly_metadata_tables() -> None:
    dropped = {
        table
        for path in _migration_paths()
        for table in _DROP.findall(_split_upgrade_downgrade(path.read_text(encoding="utf-8"))[1])
    }
    expected = set(Base.metadata.tables)
    assert dropped == expected, (
        f"drop에만: {sorted(dropped - expected)} · 모델에만: {sorted(expected - dropped)}"
    )


def test_each_downgrade_reverses_its_upgrade_table_order() -> None:
    """각 리비전은 자신이 만든 테이블을 FK 의존 역순으로 제거해야 한다."""
    for path in _migration_paths():
        up, down = _split_upgrade_downgrade(path.read_text(encoding="utf-8"))
        created = _CREATE.findall(up)
        dropped = _DROP.findall(down)
        assert dropped == list(reversed(created)), (
            f"{path.name}: downgrade가 upgrade의 테이블 생성 순서를 역전하지 않는다"
        )


#: `alembic_version.version_num`의 폭 — alembic이 만드는 테이블이라 우리가 못 넓힌다.
_VERSION_NUM_WIDTH = 32
_REVISION = re.compile(r'^revision: str = "(.+)"', re.M)


def test_revision_ids_fit_the_alembic_version_column() -> None:
    """🔴 리비전 id가 32자를 넘으면 **DDL은 다 돌고 버전 기록에서만** 죽는다.

    `alembic_version.version_num varchar(32)`라 초과분은
    `StringDataRightTruncationError`가 되는데, 그때는 이미 스키마가 바뀐 뒤라 DB가
    「테이블은 새것인데 버전은 옛것」인 상태로 남는다. **정적 파싱으로 미리 막는다** —
    실 PG를 띄워야만 보이는 결함을 CI 앞단에서 세운다(2026-08-08 실측으로 발견).
    """
    too_long = {
        path.name: revision
        for path in _migration_paths()
        for revision in _REVISION.findall(path.read_text(encoding="utf-8"))
        if len(revision) > _VERSION_NUM_WIDTH
    }
    assert not too_long, (
        f"리비전 id가 {_VERSION_NUM_WIDTH}자를 넘는다(alembic_version.version_num 폭): "
        f"{ {name: len(rev) for name, rev in too_long.items()} }"
    )


def test_the_revision_scan_finds_every_migration() -> None:
    """정규식이 조용히 0건을 내면 위 검사가 「위반 없음」이 아니라 「안 봤다」가 된다."""
    found = [
        revision
        for path in _migration_paths()
        for revision in _REVISION.findall(path.read_text(encoding="utf-8"))
    ]
    assert len(found) == len(_migration_paths()), (
        f"리비전 id를 못 읽은 파일이 있다 — 찾은 것 {len(found)}개, "
        f"파일 {len(_migration_paths())}개"
    )
