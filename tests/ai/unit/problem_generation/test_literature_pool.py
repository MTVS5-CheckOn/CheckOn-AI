"""버전 고정 문학 작품 풀 로더의 fail-closed 검증."""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from ai.problem_generation.infrastructure.literature_pool import (
    DEFAULT_LITERATURE_POOL_ROOT,
    LiteraturePoolLoadError,
    load_literature_pool,
)


def _copy_pool(tmp_path: Path) -> Path:
    root = tmp_path / "literature_pool"
    shutil.copytree(DEFAULT_LITERATURE_POOL_ROOT, root)
    return root


def _index(root: Path) -> dict[str, Any]:
    raw = json.loads((root / "index.json").read_text(encoding="utf-8"))
    assert isinstance(raw, dict)
    return raw


def _write_index(root: Path, raw: dict[str, Any]) -> None:
    (root / "index.json").write_text(
        json.dumps(raw, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def test_bundled_literature_pool_loads_all_five_verified_works() -> None:
    pool = load_literature_pool()

    assert pool.schema_version == "literature-pool.v1"
    assert len(pool.works) == 5
    assert {work.metadata.slug for work in pool.works} == {
        "cheongsan_byeolgok",
        "gwandong_byeolgok",
        "jindallaekkot",
        "memilkkot",
        "unsu_joeun_nal",
    }


def test_loader_rejects_content_hash_mismatch(tmp_path: Path) -> None:
    root = _copy_pool(tmp_path)
    target = root / "jindallaekkot.txt"
    target.write_bytes(target.read_bytes() + b"tampered")

    with pytest.raises(LiteraturePoolLoadError, match="해시 불일치"):
        load_literature_pool(root)


@pytest.mark.parametrize(
    "marker",
    ["[편집]", "Public domain", "자매 프로젝트", "저자:", "↑ ", "←", "→"],
)
def test_loader_rejects_ui_marker_even_when_hash_and_char_count_match(
    tmp_path: Path,
    marker: str,
) -> None:
    root = _copy_pool(tmp_path)
    target = root / "jindallaekkot.txt"
    contaminated = target.read_text(encoding="utf-8") + f"\n{marker}\n"
    target.write_text(contaminated, encoding="utf-8")
    raw = _index(root)
    metadata = next(work for work in raw["works"] if work["file"] == target.name)
    metadata["content_sha256"] = (
        f"sha256:{hashlib.sha256(target.read_bytes()).hexdigest()}"
    )
    metadata["char_count"] = len(target.read_bytes().decode("utf-8"))
    _write_index(root, raw)

    with pytest.raises(LiteraturePoolLoadError, match="UI 표식"):
        load_literature_pool(root)


def test_loader_rejects_unexpired_author(tmp_path: Path) -> None:
    root = _copy_pool(tmp_path)
    raw = _index(root)
    raw["works"][1]["author_death_year"] = 2020
    _write_index(root, raw)

    with pytest.raises(LiteraturePoolLoadError, match="인덱스"):
        load_literature_pool(root)


def test_loader_rejects_unknown_author_without_reason(tmp_path: Path) -> None:
    root = _copy_pool(tmp_path)
    raw = _index(root)
    raw["works"][0]["note"] = ""
    _write_index(root, raw)

    with pytest.raises(LiteraturePoolLoadError, match="인덱스"):
        load_literature_pool(root)


@pytest.mark.parametrize("mismatch", ["missing", "unindexed"])
def test_loader_rejects_index_and_text_file_set_mismatch(
    tmp_path: Path,
    mismatch: str,
) -> None:
    root = _copy_pool(tmp_path)
    if mismatch == "missing":
        (root / "jindallaekkot.txt").unlink()
    else:
        (root / "unindexed.txt").write_text("인덱스에 없는 원문", encoding="utf-8")

    with pytest.raises(LiteraturePoolLoadError, match="파일 목록 불일치"):
        load_literature_pool(root)
