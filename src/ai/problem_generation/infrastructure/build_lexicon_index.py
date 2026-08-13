"""로컬 표준국어대사전 XML에서 T1 최소 색인을 만든다."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path
from xml.etree import ElementTree

import yaml  # type: ignore[import-untyped]
from pydantic import ValidationError

from ai.problem_generation.infrastructure.lexicon_index import (
    LexiconIndex,
    LexiconIndexEntry,
    LexiconNodeMap,
)

_DATA_ROOT = Path(__file__).resolve().parents[1] / "data"
_MAP_PATH = _DATA_ROOT / "lexicon_node_map.yaml"
_DEFAULT_OUTPUT = _DATA_ROOT / "lexicon_index" / "stdict-20260805.json"
WORD_SEPARATORS = ("^", "-")
HOMONYM_SUFFIX_PATTERN = re.compile(r"\d+$")
MISSING_POS = "품사 없음"


class LexiconIndexBuildError(RuntimeError):
    """사전 원문이나 노드 map으로 유효한 색인을 만들 수 없음."""


def normalize_headword(word: str) -> str:
    """붙임표를 없애고 끝 동형이의어 번호를 제거해 후보 비교형을 만든다."""

    normalized = word.strip()
    for separator in WORD_SEPARATORS:
        normalized = normalized.replace(separator, "")
    return HOMONYM_SUFFIX_PATTERN.sub("", normalized)


def build_index(source_dir: Path, mapping: LexiconNodeMap) -> LexiconIndex:
    approved: dict[str, set[str]] = defaultdict(set)
    for node_id, rule in mapping.nodes.items():
        for headword in rule.headwords:
            approved[normalize_headword(headword)].add(node_id)

    collected: dict[str, list[LexiconIndexEntry]] = defaultdict(list)
    for item in _iter_items(source_dir):
        word_info = item.find("word_info")
        if word_info is None:
            continue
        target_code = _text(item.find("target_code"))
        word = _text(word_info.find("word"))
        word_type = _text(word_info.find("word_type"))
        if target_code is None or word is None or word_type is None:
            continue
        node_ids = approved.get(normalize_headword(word), set())
        if not node_ids:
            continue
        for pos_info in word_info.findall("pos_info"):
            pos = _text(pos_info.find("pos")) or MISSING_POS
            grammar_info = tuple(
                value
                for element in pos_info.findall(".//grammar_info")
                if (value := _text(element)) is not None
            )
            for sense in pos_info.findall(".//sense_info"):
                sense_code = _text(sense.find("sense_code"))
                cat = _text(sense.find(".//cat"))
                # 뜻풀이는 후보 sense의 완결성 판별에 읽되 index에는 절대 남기지 않는다.
                definition = _text(sense.find("definition"))
                if sense_code is None or cat != mapping.required_cat or definition is None:
                    continue
                for node_id in node_ids:
                    if not _pos_is_allowed(pos, mapping.nodes[node_id].allowed_pos):
                        continue
                    collected[node_id].append(
                        _entry(
                            node_id=node_id,
                            target_code=target_code,
                            sense_code=sense_code,
                            word=word,
                            pos=pos,
                            cat=cat,
                            word_type=word_type,
                            grammar_info=grammar_info,
                            source_revision=mapping.source_revision,
                        )
                    )
    nodes: dict[str, tuple[LexiconIndexEntry, ...]] = {}
    for node_id in mapping.nodes:
        entries = sorted(collected.get(node_id, []), key=_sense_sort_key)
        if not entries:
            raise LexiconIndexBuildError(f"승인 표제어와 일치하는 언어 sense가 없다: {node_id}")
        nodes[node_id] = tuple(entries[: mapping.max_evidence_count])
    return LexiconIndex(
        version=mapping.version,
        source_revision=mapping.source_revision,
        max_evidence_count=mapping.max_evidence_count,
        nodes=nodes,
    )


def load_node_map(path: Path = _MAP_PATH) -> LexiconNodeMap:
    try:
        raw: object = yaml.safe_load(path.read_text(encoding="utf-8"))
        return LexiconNodeMap.model_validate(raw)
    except (OSError, yaml.YAMLError, ValidationError) as exc:
        raise LexiconIndexBuildError(f"사전 노드 map을 읽을 수 없다: {exc}") from exc


def write_index(index: LexiconIndex, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(index.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _iter_items(source_dir: Path) -> Iterable[ElementTree.Element]:
    paths = sorted(source_dir.glob("*.xml"))
    if not paths:
        raise LexiconIndexBuildError(f"표준국어대사전 XML이 없다: {source_dir}")
    for path in paths:
        for _, element in ElementTree.iterparse(path, events=("end",)):
            if element.tag == "item":
                yield element
                element.clear()


def _entry(
    *,
    node_id: str,
    target_code: str,
    sense_code: str,
    word: str,
    pos: str,
    cat: str,
    word_type: str,
    grammar_info: tuple[str, ...],
    source_revision: str,
) -> LexiconIndexEntry:
    values = {
        "node_id": node_id,
        "target_code": target_code,
        "sense_code": sense_code,
        "word": word,
        "pos": pos,
        "cat": cat,
        "word_type": word_type,
        "grammar_info": grammar_info,
        "source_revision": source_revision,
    }
    canonical = json.dumps(values, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return LexiconIndexEntry(
        node_id=node_id,
        target_code=target_code,
        sense_code=sense_code,
        word=word,
        pos=pos,
        cat=cat,
        word_type=word_type,
        grammar_info=grammar_info,
        source_revision=source_revision,
        content_hash="sha256:" + hashlib.sha256(canonical.encode()).hexdigest(),
    )


def _sense_sort_key(entry: LexiconIndexEntry) -> tuple[int, str]:
    try:
        return int(entry.sense_code), entry.sense_code
    except ValueError:
        return 2**63 - 1, entry.sense_code


def _pos_is_allowed(pos: str, allowed_pos: tuple[str, ...]) -> bool:
    return pos == MISSING_POS or pos in allowed_pos


def _text(element: ElementTree.Element | None) -> str | None:
    if element is None:
        return None
    value = "".join(element.itertext()).strip()
    return value or None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source_dir", type=Path)
    parser.add_argument("--output", type=Path, default=_DEFAULT_OUTPUT)
    args = parser.parse_args()
    write_index(build_index(args.source_dir, load_node_map()), args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
