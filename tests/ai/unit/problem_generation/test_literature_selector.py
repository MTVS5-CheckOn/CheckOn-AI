"""T3 문학 작품·오프셋 선택의 결정론과 원문 보존."""

import pytest

from ai.contracts.problem_generation import LiteratureGenre, WorkSelection
from ai.problem_generation.application.literature_selector import (
    LiteratureSelector,
)
from ai.problem_generation.infrastructure.literature_pool import load_literature_pool


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
    selector = LiteratureSelector(load_literature_pool())

    excerpt = selector.select(
        WorkSelection(genre=LiteratureGenre.MODERN_POETRY, era="근대")
    )

    assert excerpt.slug == "jindallaekkot"
    assert excerpt.quote


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
