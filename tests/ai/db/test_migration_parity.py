"""초기 마이그레이션 ↔ metadata 대조 — 세 정본(ERD·모델·마이그레이션)이 어긋나지 않게.

test_erd_model_parity 가 ERD==모델 을 강제하고, 이 검사가 모델==마이그레이션 을 강제해
루프를 닫는다. 마이그레이션은 metadata 에서 생성했으므로 지금은 일치하지만, 누가
모델만 고치고 마이그레이션을 안 만들면(혹은 반대로) 여기서 걸린다.

대조는 정적 파싱이다(DB 불필요) — 오프라인 테스트 전략(fake + offline SQL)에 맞춘다.
"""

from __future__ import annotations

import re
from pathlib import Path

from ai.db.models import Base

_INITIAL = (
    Path(__file__).resolve().parents[3]
    / "src"
    / "ai"
    / "db"
    / "migrations"
    / "versions"
    / "0001_initial_schema.py"
)

_CREATE = re.compile(r"op\.create_table\(\s*['\"](\w+)['\"]")
_DROP = re.compile(r"op\.drop_table\(\s*['\"](\w+)['\"]")


def _split_upgrade_downgrade(src: str) -> tuple[str, str]:
    up_idx = src.index("def upgrade(")
    down_idx = src.index("def downgrade(")
    return src[up_idx:down_idx], src[down_idx:]


def test_initial_migration_exists() -> None:
    assert _INITIAL.is_file(), "초기 마이그레이션 파일이 없다"


def test_upgrade_creates_exactly_metadata_tables() -> None:
    up, _ = _split_upgrade_downgrade(_INITIAL.read_text(encoding="utf-8"))
    created = set(_CREATE.findall(up))
    expected = set(Base.metadata.tables)
    assert created == expected, (
        f"마이그레이션에만: {sorted(created - expected)} · "
        f"모델에만: {sorted(expected - created)}"
    )


def test_downgrade_drops_exactly_metadata_tables() -> None:
    _, down = _split_upgrade_downgrade(_INITIAL.read_text(encoding="utf-8"))
    dropped = set(_DROP.findall(down))
    expected = set(Base.metadata.tables)
    assert dropped == expected, (
        f"drop에만: {sorted(dropped - expected)} · 모델에만: {sorted(expected - dropped)}"
    )


def test_downgrade_is_reverse_of_upgrade() -> None:
    """FK 의존 역순으로 drop 해야 한다 — create 순서의 정확한 역순."""
    up, down = _split_upgrade_downgrade(_INITIAL.read_text(encoding="utf-8"))
    created = _CREATE.findall(up)
    dropped = _DROP.findall(down)
    assert dropped == list(reversed(created)), "downgrade 가 upgrade 의 역순이 아니다"
