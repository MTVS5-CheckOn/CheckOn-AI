"""국립국어원 어문규범 단일 테이블 로딩·선별 경계."""

from __future__ import annotations

import csv
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml  # type: ignore[import-untyped]
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ai.problem_generation.infrastructure.config import (
    DEFAULT_GRAMMAR_NORM_VERSION,
    grammar_norm_dir,
)

_MAP_PATH = Path(__file__).resolve().parents[1] / "data" / "grammar_norm_map.yaml"
_SOURCE_FILENAME = "SOURCE.txt"
_ATTRIBUTION_MARKER = "출처 표시 문구 (서비스에 노출할 때 이대로 쓴다)"
_NORMAL_STATUS = "정상"
_VISIBLE_VALUES = frozenset({"", "Y"})


class GrammarNormLoadError(RuntimeError):
    """어문규범 데이터 또는 매핑을 읽거나 검증할 수 없음."""


class GrammarNormNodeMap(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    label: str = Field(min_length=1)
    codes: tuple[str, ...] = Field(min_length=1)
    keywords: tuple[str, ...] = Field(min_length=1)


class GrammarNormMap(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    version: str = Field(min_length=1)
    source_table: str = Field(min_length=1)
    max_evidence_count: int = Field(ge=1)
    nodes: dict[str, GrammarNormNodeMap]


@dataclass(frozen=True, slots=True)
class GrammarNormRow:
    regulation_no: str
    regulation_code: str
    title: str
    keyword: str


@dataclass(frozen=True, slots=True)
class GrammarNormCorpus:
    version: str
    mapping: GrammarNormMap
    rows: tuple[GrammarNormRow, ...]
    attribution: str
    eligible_count: int
    duplicate_count: int


def parse_grammar_norm_rows(
    rows: Iterable[Mapping[str, str | None]],
) -> tuple[tuple[GrammarNormRow, ...], int, int]:
    """정상·노출 가능·인용 가능 행을 규정번호 기준으로 순수 선별한다."""

    eligible: list[GrammarNormRow] = []
    for raw in rows:
        keyword = (raw.get("주제어") or "").strip() or (raw.get("부주제어") or "").strip()
        if not keyword or (raw.get("상태") or "").strip() != _NORMAL_STATUS:
            continue
        if (raw.get("본문노출여부") or "").strip() not in _VISIBLE_VALUES:
            continue
        regulation_no = (raw.get("규정번호") or "").strip()
        regulation_code = (raw.get("규정코드") or "").strip()
        if not regulation_no or not regulation_code:
            continue
        eligible.append(
            GrammarNormRow(
                regulation_no=regulation_no,
                regulation_code=regulation_code,
                title=(raw.get("제목") or "").strip(),
                keyword=keyword,
            )
        )

    unique: dict[str, GrammarNormRow] = {}
    for row in eligible:
        unique.setdefault(row.regulation_no, row)
    return tuple(unique.values()), len(eligible), len(eligible) - len(unique)


def select_node_rows(corpus: GrammarNormCorpus, skill_node_id: str) -> tuple[GrammarNormRow, ...]:
    """노드의 승인 코드·키워드에 맞는 근거를 원본 순서와 상한대로 고른다."""

    node = corpus.mapping.nodes.get(skill_node_id)
    if node is None:
        return ()
    matched = tuple(
        row
        for row in corpus.rows
        if row.regulation_code in node.codes
        and any(keyword in f"{row.keyword}{row.title}" for keyword in node.keywords)
    )
    return matched[: corpus.mapping.max_evidence_count]


def count_node_matches(corpus: GrammarNormCorpus, skill_node_id: str) -> int:
    """상한 적용 전 노드 근거 후보 수를 반환한다."""

    node = corpus.mapping.nodes.get(skill_node_id)
    if node is None:
        return 0
    return sum(
        row.regulation_code in node.codes
        and any(keyword in f"{row.keyword}{row.title}" for keyword in node.keywords)
        for row in corpus.rows
    )


@lru_cache(maxsize=1)
def load_grammar_norm_corpus(
    version: str = DEFAULT_GRAMMAR_NORM_VERSION,
) -> GrammarNormCorpus:
    """프로세스당 한 번만 CSV·매핑·출처를 읽어 불변 코퍼스를 만든다."""

    directory = grammar_norm_dir(version)
    mapping = _load_mapping(_MAP_PATH)
    source_path = directory / mapping.source_table
    try:
        with source_path.open(encoding="utf-8-sig", newline="") as source:
            parsed_rows, eligible_count, duplicate_count = parse_grammar_norm_rows(
                csv.DictReader(source)
            )
        attribution = _load_attribution(directory / _SOURCE_FILENAME)
    except OSError as exc:
        raise GrammarNormLoadError(f"어문규범 자료를 읽을 수 없다: {exc}") from exc
    return GrammarNormCorpus(
        version=version,
        mapping=mapping,
        rows=parsed_rows,
        attribution=attribution,
        eligible_count=eligible_count,
        duplicate_count=duplicate_count,
    )


def _load_mapping(path: Path) -> GrammarNormMap:
    try:
        raw: object = yaml.safe_load(path.read_text(encoding="utf-8"))
        return GrammarNormMap.model_validate(raw)
    except (OSError, yaml.YAMLError, ValidationError) as exc:
        raise GrammarNormLoadError(f"어문규범 매핑을 읽을 수 없다: {exc}") from exc


def _load_attribution(path: Path) -> str:
    lines = path.read_text(encoding="utf-8").splitlines()
    try:
        start = lines.index(_ATTRIBUTION_MARKER) + 1
    except ValueError as exc:
        raise GrammarNormLoadError("SOURCE.txt에 출처 표시 문구가 없다") from exc
    parts: list[str] = []
    for line in lines[start:]:
        stripped = line.strip()
        if not stripped:
            if parts:
                break
            continue
        parts.append(stripped)
    if not parts:
        raise GrammarNormLoadError("SOURCE.txt의 출처 표시 문구가 비어 있다")
    return " ".join(parts)


__all__ = [
    "GrammarNormCorpus",
    "GrammarNormLoadError",
    "GrammarNormRow",
    "count_node_matches",
    "load_grammar_norm_corpus",
    "parse_grammar_norm_rows",
    "select_node_rows",
]
