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
    # 🔴 **어절 하나로는 전송이 안 막힌다 — 값만 지워진다.**
    #   `policies/masking_redaction.md` §2 [A 확정 7/23]: 후보 **1개면 그 토큰만**,
    #   **2개 이상이면 문장 통째 + `uncertain=True`**. 종전 이 검사는 어절 단위로
    #   `uncertain`을 단정해 **구현이 스펙보다 넓게 막던 시절**을 고정하고 있었다.
    for word in ("이시옷이", "성립되는"):
        result = redact(word)
        assert "⟪확인필요⟫" in result.masked_text, f"후보로 안 잡혔다: {word}"
        assert not result.uncertain, f"후보 1개인데 전송을 막았다: {word}"
    # 조사가 없으면 후보 자체가 아니다(패턴이 `[성씨][가-힣]{2}` + 조사다).
    assert redact("이시옷").masked_text == "이시옷"
    assert redact("성립되").masked_text == "성립되"


def test_fortition_prompt_material_remains_fail_closed() -> None:
    rows = select_node_rows(load_grammar_norm_corpus(), _NODE_ID)
    prompt_material = "\n".join(("된소리되기", *(row.keyword for row in rows)))

    assert redact(prompt_material).uncertain
