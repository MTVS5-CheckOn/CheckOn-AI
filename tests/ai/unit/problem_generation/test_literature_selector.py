"""T3 문학 작품·오프셋 선택의 결정론과 원문 보존."""

import hashlib
from datetime import date

import pytest

from ai.contracts.problem_generation import LiteratureGenre, WorkSelection
from ai.problem_generation.application.literature_selector import (
    LiteratureSelector,
)
from ai.problem_generation.domain.literature import (
    LiteraturePool,
    LiteraturePoolEntry,
    LiteratureWork,
)
from ai.problem_generation.infrastructure.literature_pool import load_literature_pool
from ai.runtime.redaction import redact


def test_same_selection_returns_the_same_work_and_exact_source_span() -> None:
    pool = load_literature_pool()
    selector = LiteratureSelector(pool)
    selection = WorkSelection(
        genre=LiteratureGenre.MODERN_NOVEL,
        era="근대",
        concept_keywords=("달",),
    )

    first = selector.select(selection)
    second = selector.select(selection)
    work = next(work for work in pool.works if work.metadata.slug == first.slug)

    assert second == first
    assert first.quote == work.content[first.start : first.end]
    assert first.evidence_ref.endswith(f":{first.start}:{first.end}")


def test_reversing_loaded_work_order_does_not_change_selection() -> None:
    pool = load_literature_pool()
    reversed_pool = pool.model_copy(update={"works": tuple(reversed(pool.works))})
    selection = WorkSelection(
        genre=LiteratureGenre.MODERN_NOVEL,
        era="근대",
        concept_keywords=("달",),
    )

    original = LiteratureSelector(pool).select(selection)
    reordered = LiteratureSelector(reversed_pool).select(selection)

    assert reordered.slug == original.slug
    assert (reordered.start, reordered.end) == (original.start, original.end)
    assert reordered.quote == original.quote


def test_short_modern_poem_is_selected_without_a_length_floor() -> None:
    # 풀 규모와 무관하게 성립해야 하는 불변식이라 짧은 시만 남긴 부분 풀로 좁혀 본다.
    pool = load_literature_pool()
    short_poems = tuple(
        work
        for work in pool.works
        if work.metadata.genre is LiteratureGenre.MODERN_POETRY
        and work.metadata.char_count < 200
    )
    assert short_poems

    excerpt = LiteratureSelector(
        pool.model_copy(update={"works": short_poems})
    ).select(WorkSelection(genre=LiteratureGenre.MODERN_POETRY))
    work = next(item for item in short_poems if item.metadata.slug == excerpt.slug)

    assert excerpt.quote == work.content


@pytest.mark.parametrize(
    "genre",
    [LiteratureGenre.CLASSICAL_POETRY, LiteratureGenre.MODERN_POETRY],
)
def test_poetry_excerpt_is_the_complete_work(genre: LiteratureGenre) -> None:
    pool = load_literature_pool()

    excerpt = LiteratureSelector(pool).select(WorkSelection(genre=genre))
    work = next(work for work in pool.works if work.metadata.slug == excerpt.slug)

    assert (excerpt.start, excerpt.end) == (0, len(work.content))
    assert excerpt.quote == work.content


def _work(
    *,
    slug: str,
    title: str,
    author: str,
    content: str,
    genre: LiteratureGenre = LiteratureGenre.MODERN_NOVEL,
) -> LiteratureWork:
    payload = content.encode("utf-8")
    return LiteratureWork(
        metadata=LiteraturePoolEntry(
            slug=slug,
            title=title,
            author=author,
            author_death_year=1930,
            era="근대",
            genre=genre,
            note="",
            source="ko.wikisource.org",
            source_ref="https://ko.wikisource.org/w/index.php?oldid=1",
            revision_id=1,
            collected_at=date(2026, 8, 19),
            content_sha256=f"sha256:{hashlib.sha256(payload).hexdigest()}",
            char_count=len(content),
            file=f"{slug}.txt",
        ),
        content=content,
    )


def _pool(*works: LiteratureWork) -> LiteraturePool:
    return LiteraturePool(schema_version="literature-pool.v1", works=works)


#: 등장인물 이름이 든 문단은 마스킹이 `확인필요`로 fail-closed 한다(불변식 3).
_MASKED_PARAGRAPH = "이차돈은 반달을 보았다. 달빛이 마당에 가득 내렸다."
_CLEAN_PARAGRAPH = "달빛이 마당에 가득 내렸고 바람이 마루를 지나갔다."


def test_selector_skips_a_span_that_redaction_cannot_transmit() -> None:
    work = _work(
        slug="test_work",
        title="달밤",
        author="아무개",
        content=f"{_MASKED_PARAGRAPH}\n\n{_CLEAN_PARAGRAPH}",
    )

    excerpt = LiteratureSelector(_pool(work)).select(
        WorkSelection(genre=LiteratureGenre.MODERN_NOVEL, concept_keywords=("달",))
    )

    assert excerpt.quote == _CLEAN_PARAGRAPH
    assert not redact(excerpt.quote).uncertain


def test_selector_drops_a_work_whose_title_redaction_cannot_transmit() -> None:
    blocked = _work(
        slug="blocked_work",
        title="이차돈의 사",
        author="아무개",
        content=f"{_CLEAN_PARAGRAPH} 달 달 달",
    )
    usable = _work(
        slug="usable_work",
        title="달밤",
        author="아무개",
        content=_CLEAN_PARAGRAPH,
    )

    excerpt = LiteratureSelector(_pool(blocked, usable)).select(
        WorkSelection(genre=LiteratureGenre.MODERN_NOVEL, concept_keywords=("달",))
    )

    assert excerpt.slug == "usable_work"


def test_fully_masked_pool_still_returns_a_span_so_redaction_reports_it() -> None:
    #: 여기서 삼키면 차단 사유가 「선택 불가」로 바뀐다. 실패는 마스킹 경계에서 난다.
    work = _work(
        slug="masked_only",
        title="달밤",
        author="아무개",
        content=_MASKED_PARAGRAPH,
    )

    excerpt = LiteratureSelector(_pool(work)).select(
        WorkSelection(genre=LiteratureGenre.MODERN_NOVEL)
    )

    assert excerpt.quote == _MASKED_PARAGRAPH
    assert redact(excerpt.quote).uncertain


def test_masked_poem_does_not_win_the_keyword_score_and_block_the_genre() -> None:
    #: 운문은 발췌 단위가 전문이라 회전할 구간이 없다. 작품 단계에서 걸러야 한다.
    masked = _work(
        slug="masked_poem",
        title="달의 노래",
        author="아무개",
        content=f"{_MASKED_PARAGRAPH} 달 달 달 달",
        genre=LiteratureGenre.MODERN_POETRY,
    )
    clean = _work(
        slug="clean_poem",
        title="달빛",
        author="아무개",
        content=f"{_CLEAN_PARAGRAPH} 달",
        genre=LiteratureGenre.MODERN_POETRY,
    )

    excerpt = LiteratureSelector(_pool(masked, clean)).select(
        WorkSelection(genre=LiteratureGenre.MODERN_POETRY, concept_keywords=("달",))
    )

    assert excerpt.slug == "clean_poem"
    assert not redact(excerpt.quote).uncertain


def test_prose_excerpt_joins_paragraphs_until_the_floor() -> None:
    #: 🔴 산문 문단 하나는 발췌 단위가 아니다 — 소설 문단 중앙값이 41자다(실측).
    #: 하한을 주면 연속 문단을 이어 붙이되, 원문에서 **연속**이라 인용이 정확히 남는다.
    work = _work(
        slug="dialogue_work",
        title="달밤",
        author="아무개",
        content="\n\n".join(("“응.”", "“그래?”", _CLEAN_PARAGRAPH, "“알겠다.”")),
    )

    excerpt = LiteratureSelector(_pool(work), excerpt_min_chars=30).select(
        WorkSelection(genre=LiteratureGenre.MODERN_NOVEL)
    )

    assert len(excerpt.quote) >= 30
    assert excerpt.quote == work.content[excerpt.start : excerpt.end]


def test_prose_excerpt_never_exceeds_the_ceiling() -> None:
    work = _work(
        slug="long_work",
        title="달밤",
        author="아무개",
        content="\n\n".join([_CLEAN_PARAGRAPH] * 12),
    )

    excerpt = LiteratureSelector(
        _pool(work), excerpt_min_chars=60, excerpt_max_chars=200
    ).select(WorkSelection(genre=LiteratureGenre.MODERN_NOVEL))

    assert 60 <= len(excerpt.quote) <= 200


def test_short_prose_work_is_not_excluded_by_the_floor() -> None:
    #: 하한은 발췌 단위를 키우는 규칙이지 짧은 작품을 버리는 규칙이 아니다.
    work = _work(
        slug="short_prose",
        title="달밤",
        author="아무개",
        content=_CLEAN_PARAGRAPH,
    )

    excerpt = LiteratureSelector(_pool(work), excerpt_min_chars=5000).select(
        WorkSelection(genre=LiteratureGenre.MODERN_NOVEL)
    )

    assert excerpt.quote == _CLEAN_PARAGRAPH


def test_poetry_ignores_the_prose_window_bounds() -> None:
    work = _work(
        slug="poem_work",
        title="달밤",
        author="아무개",
        content=f"{_CLEAN_PARAGRAPH}\n\n{_CLEAN_PARAGRAPH}",
        genre=LiteratureGenre.MODERN_POETRY,
    )

    excerpt = LiteratureSelector(
        _pool(work), excerpt_min_chars=10, excerpt_max_chars=20
    ).select(WorkSelection(genre=LiteratureGenre.MODERN_POETRY))

    assert excerpt.quote == work.content
