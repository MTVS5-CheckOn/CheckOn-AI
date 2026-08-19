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
from ai.runtime.redaction import redact

_PARAGRAPH = re.compile(r"\S(?:.*?\S)?(?=\n{2,}|\Z)", re.DOTALL)


class LiteratureSelectionUnavailable(ValueError):
    """요청 조건에 맞는 작품·원문 구간이 없음."""


class LiteratureSelector:
    """버전 검증이 끝난 작품 풀에서 LLM 없이 원문 구간을 고른다."""

    def __init__(
        self,
        pool: LiteraturePool,
        *,
        excerpt_min_chars: int = 1,
        excerpt_max_chars: int | None = None,
    ) -> None:
        if excerpt_max_chars is not None and excerpt_max_chars < excerpt_min_chars:
            raise ValueError("발췌 창 하한이 상한보다 클 수 없다")
        self._pool = pool
        self._excerpt_min_chars = excerpt_min_chars
        self._excerpt_max_chars = excerpt_max_chars

    def select(self, selection: WorkSelection) -> WorkExcerpt:
        candidates = tuple(
            work
            for work in self._pool.works
            if work.metadata.genre is selection.genre
            and (selection.era is None or work.metadata.era == selection.era)
        )
        if not candidates:
            raise LiteratureSelectionUnavailable("문학 작품 선택 조건에 맞는 풀이 없다")

        # 보낼 수 없는 작품은 개념어 점수를 매기기 전에 후보에서 뺀다. 점수가 그런
        # 작품 하나로 좁혀 버리면 뒤에서 회전할 자리가 남지 않기 때문이다.
        sendable_works = tuple(work for work in candidates if _sendable_work(work))
        candidates = sendable_works or candidates

        seed = canonical_json(selection.model_dump(mode="json"))
        best_works = _best_keyword_matches(candidates, selection)
        # 선택은 index 배열 순서가 아니라 불변 메타데이터인 slug 정렬에만 기대게 한다.
        ordered_works = tuple(sorted(best_works, key=lambda value: value.metadata.slug))
        work_start = _stable_index(len(ordered_works), seed)

        fallback: tuple[LiteratureWork, tuple[int, int]] | None = None
        for work_offset in range(len(ordered_works)):
            work = ordered_works[(work_start + work_offset) % len(ordered_works)]
            spans = _excerpt_spans(
                work,
                minimum=self._excerpt_min_chars,
                maximum=self._excerpt_max_chars,
            )
            if not spans:
                continue
            if fallback is None:
                fallback = (work, _pick_span(work, spans, selection, seed))
            # 등장인물 이름이 든 구간은 마스킹이 `확인필요`로 fail-closed 하고(불변식 3)
            # 전송 직전에 T3 가 죽는다. 마스킹 규칙을 무르는 게 아니라, 개념어 점수를
            # 매기기 전에 보낼 수 없는 구간을 후보에서 빼는 것이다.
            sendable_spans = tuple(
                span
                for span in spans
                if not redact(work.content[span[0] : span[1]]).uncertain
            )
            if not sendable_spans:
                continue
            return _excerpt(work, _pick_span(work, sendable_spans, selection, seed))

        if fallback is None:
            raise LiteratureSelectionUnavailable("선택한 작품에 발췌 가능한 원문이 없다")
        # 풀 전체가 막힌 경우다. 여기서 삼키지 않고 종전 선택을 그대로 돌려보내
        # 차단이 redaction 경계에서 제 이름으로 드러나게 한다.
        return _excerpt(*fallback)


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


def _pick_span(
    work: LiteratureWork,
    spans: tuple[tuple[int, int], ...],
    selection: WorkSelection,
    seed: str,
) -> tuple[int, int]:
    ordered = tuple(sorted(_best_span_matches(work.content, spans, selection)))
    return ordered[_stable_index(len(ordered), f"{seed}:{work.metadata.slug}")]


def _metadata_blocked(work: LiteratureWork) -> bool:
    # 제목·저자도 ContextPack 에 실려 프롬프트로 나간다. 「이차돈의 사」처럼 제목 자체가
    # 이름+조사로 읽히면 어느 구간을 골라도 그 작품은 못 보낸다.
    metadata = work.metadata
    return redact(metadata.title).uncertain or redact(metadata.author).uncertain


def _sendable_work(work: LiteratureWork) -> bool:
    if _metadata_blocked(work):
        return False
    spans = _excerpt_spans(work)
    if len(spans) != 1:
        # 산문은 문단이 많아 전문을 미리 훑는 값이 비싸다 — 구간 단계에서 거른다.
        return True
    # 운문은 발췌 단위가 전문이라 작품 단계와 구간 단계가 같은 판정이다. 여기서
    # 걸러 두지 않으면 개념어 점수가 막힌 작품 하나로 좁혀 회전이 무의미해진다.
    start, end = spans[0]
    return not redact(work.content[start:end]).uncertain


def _excerpt(work: LiteratureWork, span: tuple[int, int]) -> WorkExcerpt:
    start, end = span
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


def _excerpt_spans(
    work: LiteratureWork,
    *,
    minimum: int = 1,
    maximum: int | None = None,
) -> tuple[tuple[int, int], ...]:
    # 수능 제시 단위에 맞춰 운문은 전문, 산문은 문단 발췌를 쓴다. 길이 하한으로 짧은
    # 작품을 배제하지 않고 갈래 자체가 요구하는 읽기 단위를 보존하기 위한 분기다.
    if work.metadata.genre in {
        LiteratureGenre.CLASSICAL_POETRY,
        LiteratureGenre.MODERN_POETRY,
    }:
        return ((0, len(work.content)),)
    return _window_spans(_paragraph_spans(work.content), minimum, maximum)


def _window_spans(
    paragraphs: tuple[tuple[int, int], ...],
    minimum: int,
    maximum: int | None,
) -> tuple[tuple[int, int], ...]:
    """연속 문단을 하한까지 이어 붙인 발췌 창.

    🔴 **산문에서 문단 하나는 발췌 단위가 아니다.** 소설 문단의 중앙값이 41자이고 78%가
    100자 미만인데(실측 2026-08-19 · 대화 한 줄이 한 문단이다), 문단을 그대로 고르면
    「거짓말을 해?」 9자가 제시문이 된다. 창은 원문에서 **연속**이라 `content[start:end]`
    가 그대로 인용이고 근거 오프셋도 정확히 남는다.
    """

    if not paragraphs:
        return ()
    windows: list[tuple[int, int]] = []
    for index, (start, _) in enumerate(paragraphs):
        end = paragraphs[index][1]
        cursor = index
        while end - start < minimum and cursor + 1 < len(paragraphs):
            cursor += 1
            end = paragraphs[cursor][1]
        if maximum is not None and end - start > maximum:
            continue
        windows.append((start, end))
    long_enough = tuple(
        window for window in windows if window[1] - window[0] >= minimum
    )
    # 작품이 하한에 못 미치면 배제하지 않는다 — 짧은 작품을 버리지 않는 것이 갈래 규칙이다.
    return long_enough or tuple(dict.fromkeys(windows)) or paragraphs


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
