"""T3 문학 작품·원문 구간의 결정론 선택과 ContextPack 결합."""

from __future__ import annotations

import hashlib
import re
from uuid import uuid5

from ai.contracts.graphrag import ContextPack
from ai.contracts.problem_generation import (
    LiteratureGenre,
    WorkExcerpt,
    WorkSelection,
)
from ai.problem_generation.domain.identity import canonical_json, sha256_hex
from ai.problem_generation.domain.literature import LiteraturePool, LiteratureWork

_PARAGRAPH = re.compile(r"\S(?:.*?\S)?(?=\n{2,}|\Z)", re.DOTALL)


class LiteratureSelectionUnavailable(ValueError):
    """요청 조건에 맞는 작품·원문 구간이 없음."""


class LiteratureSelector:
    """버전 검증이 끝난 작품 풀에서 LLM 없이 원문 구간을 고른다."""

    def __init__(self, pool: LiteraturePool) -> None:
        self._pool = pool

    def select(self, selection: WorkSelection) -> WorkExcerpt:
        candidates = tuple(
            work
            for work in self._pool.works
            if work.metadata.genre is selection.genre
            and (selection.era is None or work.metadata.era == selection.era)
        )
        if not candidates:
            raise LiteratureSelectionUnavailable("문학 작품 선택 조건에 맞는 풀이 없다")

        seed = canonical_json(selection.model_dump(mode="json"))
        best_works = _best_keyword_matches(candidates, selection)
        # 선택은 index 배열 순서가 아니라 불변 메타데이터인 slug 정렬에만 기대게 한다.
        ordered_works = tuple(sorted(best_works, key=lambda value: value.metadata.slug))
        work = ordered_works[_stable_index(len(ordered_works), seed)]
        spans = _excerpt_spans(work)
        if not spans:
            raise LiteratureSelectionUnavailable("선택한 작품에 발췌 가능한 원문이 없다")
        best_spans = _best_span_matches(work.content, spans, selection)
        ordered_spans = tuple(sorted(best_spans))
        start, end = ordered_spans[
            _stable_index(len(ordered_spans), f"{seed}:{work.metadata.slug}")
        ]
        metadata = work.metadata
        return WorkExcerpt(
            slug=metadata.slug,
            title=metadata.title,
            author=metadata.author,
            era=metadata.era,
            genre=metadata.genre,
            source_ref=metadata.source_ref,
            revision_id=metadata.revision_id,
            content_sha256=metadata.content_sha256,
            start=start,
            end=end,
            quote=work.content[start:end],
        )


def attach_work_excerpt(context_pack: ContextPack, excerpt: WorkExcerpt) -> ContextPack:
    """선택 원문과 work_span 승인 앵커를 결정론 ContextPack에 결합한다."""

    existing_raw = context_pack.retrieval_trace.get("work_excerpt")
    if existing_raw is not None:
        existing = WorkExcerpt.model_validate(existing_raw)
        if existing == excerpt:
            return context_pack
        raise LiteratureSelectionUnavailable("ContextPack의 work_excerpt를 바꿀 수 없다")

    excerpt_payload = excerpt.model_dump(mode="json")
    anchor = {
        "kind": "work_span",
        "ref": excerpt.evidence_ref,
        "quote": excerpt.quote,
        "source_ref": excerpt.source_ref,
        "content_sha256": excerpt.content_sha256,
        "start": excerpt.start,
        "end": excerpt.end,
    }
    trace = context_pack.retrieval_trace
    allowed_refs = _string_list(trace.get("allowed_evidence_refs"))
    anchors = _dict_list(trace.get("evidence_anchors"))
    payload = context_pack.model_dump(mode="json")
    payload["retrieval_trace"] = {
        **trace,
        "work_excerpt": excerpt_payload,
        "allowed_evidence_refs": [*allowed_refs, excerpt.evidence_ref],
        "evidence_anchors": [*anchors, anchor],
    }
    derived_id = uuid5(
        context_pack.context_pack_id,
        f"work-excerpt:{context_pack.context_pack_hash}:"
        f"{sha256_hex(canonical_json(excerpt_payload))}",
    )
    payload["context_pack_id"] = str(derived_id)
    payload.pop("context_pack_hash")
    payload["context_pack_hash"] = sha256_hex(canonical_json(payload))
    return ContextPack.model_validate(payload)


def _best_keyword_matches(
    works: tuple[LiteratureWork, ...],
    selection: WorkSelection,
) -> tuple[LiteratureWork, ...]:
    if not selection.concept_keywords:
        return works
    scores = {
        work.metadata.slug: _keyword_score(work.content, selection)
        for work in works
    }
    best = max(scores.values())
    return tuple(work for work in works if scores[work.metadata.slug] == best)


def _best_span_matches(
    content: str,
    spans: tuple[tuple[int, int], ...],
    selection: WorkSelection,
) -> tuple[tuple[int, int], ...]:
    if not selection.concept_keywords:
        return spans
    scores = {
        span: _keyword_score(content[span[0] : span[1]], selection)
        for span in spans
    }
    best = max(scores.values())
    return tuple(span for span in spans if scores[span] == best)


def _keyword_score(text: str, selection: WorkSelection) -> int:
    normalized = text.casefold()
    return sum(normalized.count(keyword.casefold()) for keyword in selection.concept_keywords)


def _paragraph_spans(content: str) -> tuple[tuple[int, int], ...]:
    return tuple((match.start(), match.end()) for match in _PARAGRAPH.finditer(content))


def _excerpt_spans(work: LiteratureWork) -> tuple[tuple[int, int], ...]:
    # 수능 제시 단위에 맞춰 운문은 전문, 소설은 문단 발췌를 쓴다. 길이 하한으로 짧은
    # 작품을 배제하지 않고 갈래 자체가 요구하는 읽기 단위를 보존하기 위한 분기다.
    if work.metadata.genre in {
        LiteratureGenre.CLASSICAL_POETRY,
        LiteratureGenre.MODERN_POETRY,
    }:
        return ((0, len(work.content)),)
    return _paragraph_spans(work.content)


def _stable_index(length: int, seed: str) -> int:
    digest = hashlib.sha256(seed.encode("utf-8")).digest()
    return int.from_bytes(digest, "big") % length


def _string_list(value: object) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise LiteratureSelectionUnavailable("ContextPack 승인 근거 목록이 유효하지 않다")
    return value


def _dict_list(value: object) -> list[dict[str, object]]:
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise LiteratureSelectionUnavailable("ContextPack 근거 앵커 목록이 유효하지 않다")
    return value


__all__ = [
    "LiteratureSelectionUnavailable",
    "LiteratureSelector",
    "attach_work_excerpt",
]
