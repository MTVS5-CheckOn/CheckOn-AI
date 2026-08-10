"""버전 고정 문학 작품 풀의 fail-closed 파일 로더."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from pydantic import ValidationError

from ai.problem_generation.domain.literature import (
    LiteraturePool,
    LiteraturePoolIndex,
    LiteratureWork,
)

DEFAULT_LITERATURE_POOL_ROOT = (
    Path(__file__).resolve().parents[1] / "data" / "literature_pool"
)


class LiteraturePoolLoadError(ValueError):
    """문학 풀 스키마·권리·본문 무결성이 유효하지 않음."""


def load_literature_pool(
    root: Path = DEFAULT_LITERATURE_POOL_ROOT,
) -> LiteraturePool:
    """인덱스와 모든 본문을 검증하고 메모리 풀로 복원한다."""

    index_path = root / "index.json"
    try:
        raw: object = json.loads(index_path.read_text(encoding="utf-8"))
        index = LiteraturePoolIndex.model_validate(raw)
    except (OSError, json.JSONDecodeError, ValidationError) as error:
        raise LiteraturePoolLoadError(f"문학 풀 인덱스가 유효하지 않다: {index_path}") from error

    indexed_files = {work.file for work in index.works}
    actual_files = {path.name for path in root.glob("*.txt") if path.is_file()}
    if indexed_files != actual_files:
        missing = sorted(indexed_files - actual_files)
        unindexed = sorted(actual_files - indexed_files)
        raise LiteraturePoolLoadError(
            f"문학 풀 파일 목록 불일치: missing={missing}, unindexed={unindexed}"
        )

    works: list[LiteratureWork] = []
    for metadata in index.works:
        path = root / metadata.file
        try:
            content_bytes = path.read_bytes()
            content = content_bytes.decode("utf-8")
        except (OSError, UnicodeDecodeError) as error:
            raise LiteraturePoolLoadError(f"문학 작품 본문을 읽을 수 없다: {path}") from error
        digest = f"sha256:{hashlib.sha256(content_bytes).hexdigest()}"
        if digest != metadata.content_sha256:
            raise LiteraturePoolLoadError(f"문학 작품 본문 해시 불일치: {metadata.slug}")
        if len(content) != metadata.char_count:
            raise LiteraturePoolLoadError(f"문학 작품 문자 수 불일치: {metadata.slug}")
        works.append(LiteratureWork(metadata=metadata, content=content))

    return LiteraturePool(schema_version=index.schema_version, works=tuple(works))


__all__ = [
    "DEFAULT_LITERATURE_POOL_ROOT",
    "LiteraturePoolLoadError",
    "load_literature_pool",
]
