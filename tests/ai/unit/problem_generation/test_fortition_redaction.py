"""된소리되기 규범 근거의 A 소유 redaction 오탐 재현."""

from ai.problem_generation.infrastructure.grammar_norm import (
    load_grammar_norm_corpus,
    select_node_rows,
)
from ai.runtime.redaction import redact

_NODE_ID = "language.grammar.fortition"


def test_fortition_label_is_not_the_redaction_trigger() -> None:
    assert not redact("된소리되기").uncertain


def test_fortition_quote_has_two_minimal_name_candidate_false_positives() -> None:
    rows = select_node_rows(load_grammar_norm_corpus(), _NODE_ID)
    uncertain_rows = [row.keyword for row in rows if redact(row.keyword).uncertain]

    assert len(rows) == 7
    assert len(uncertain_rows) == 1
    assert redact("이시옷이").uncertain
    assert redact("성립되는").uncertain
    assert not redact("이시옷").uncertain
    assert not redact("성립되").uncertain


def test_fortition_prompt_material_remains_fail_closed() -> None:
    rows = select_node_rows(load_grammar_norm_corpus(), _NODE_ID)
    prompt_material = "\n".join(("된소리되기", *(row.keyword for row in rows)))

    assert redact(prompt_material).uncertain
