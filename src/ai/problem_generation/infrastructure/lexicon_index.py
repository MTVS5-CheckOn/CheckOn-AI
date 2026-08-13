"""표준국어대사전 T1 최소 색인의 런타임 로딩 경계."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError

_DATA_ROOT = Path(__file__).resolve().parents[1] / "data"
_INDEX_PATH = _DATA_ROOT / "lexicon_index" / "stdict-20260805.json"


class LexiconIndexLoadError(RuntimeError):
    """사전 색인을 읽거나 검증할 수 없음."""


class LexiconNodeRule(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    label: str = Field(min_length=1)
    headwords: tuple[str, ...] = Field(min_length=1)
    allowed_pos: tuple[str, ...] = Field(min_length=1)


class LexiconNodeMap(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    version: str = Field(min_length=1)
    source_revision: str = Field(min_length=1)
    required_cat: str = Field(min_length=1)
    max_evidence_count: int = Field(ge=1)
    nodes: dict[str, LexiconNodeRule]


class LexiconIndexEntry(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    node_id: str = Field(min_length=1)
    target_code: str = Field(min_length=1)
    sense_code: str = Field(min_length=1)
    word: str = Field(min_length=1)
    pos: str = Field(min_length=1)
    cat: str = Field(min_length=1)
    word_type: str = Field(min_length=1)
    grammar_info: tuple[str, ...] = ()
    source_revision: str = Field(min_length=1)
    content_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class LexiconIndex(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    version: str = Field(min_length=1)
    source_revision: str = Field(min_length=1)
    max_evidence_count: int = Field(ge=1)
    nodes: dict[str, tuple[LexiconIndexEntry, ...]]


@lru_cache(maxsize=1)
def load_lexicon_index(path: Path = _INDEX_PATH) -> LexiconIndex:
    try:
        raw: object = json.loads(path.read_text(encoding="utf-8"))
        return LexiconIndex.model_validate(raw)
    except (OSError, json.JSONDecodeError, ValidationError) as exc:
        raise LexiconIndexLoadError(f"표준국어대사전 색인을 읽을 수 없다: {exc}") from exc


def select_node_entries(
    index: LexiconIndex, skill_node_id: str
) -> tuple[LexiconIndexEntry, ...]:
    return index.nodes.get(skill_node_id, ())[: index.max_evidence_count]


__all__ = [
    "LexiconIndex",
    "LexiconIndexEntry",
    "LexiconIndexLoadError",
    "LexiconNodeMap",
    "LexiconNodeRule",
    "load_lexicon_index",
    "select_node_entries",
]
