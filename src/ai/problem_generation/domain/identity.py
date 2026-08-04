"""결정론 식별자·해시 — 동일 입력이면 바이트 동일(불변식 8)."""

from __future__ import annotations

import hashlib
import json
from uuid import UUID, uuid5

from ai.contracts.problem_generation import GeneratedItem, ProblemRequest


def item_stem_hash(item: GeneratedItem) -> str:
    return sha256_hex(item.stem)


def problem_item_id(set_id: UUID, slot_index: int) -> UUID:
    return uuid5(set_id, f"problem-item:{slot_index}")


def canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def sha256_hex(value: str) -> str:
    return f"sha256:{hashlib.sha256(value.encode('utf-8')).hexdigest()}"


def request_hash(request: ProblemRequest) -> str:
    canonical = json.dumps(
        request.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return f"sha256:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}"
