"""🔴 직렬화 규칙 문서와 코드가 갈리는지 잰다 (99 #167 · 로그 167).

⚠ **값을 재는 검사가 아니다** — 「문서의 문면」과 「코드의 사실」이 **갈리는지**를 잰다.
선례: `test_timeout_budget_relations.py` 의
`test_the_third_connection_term_is_named_even_though_it_is_unbounded`
(*"이 검사는 값을 재는 것이 아니라 「그 사실이 문면에 있는가」를 잰다"*).

🔴 **왜 필요한가** — 이 문서는 백엔드와의 **양자 합의 문면**인데 저장소 **밖**에 있었다
(`grep "ensure_ascii" docs/` → **0건**). 그 사이 §2-3 의 **적용 범위**가 틀린 채로 살아 있었고,
상대가 그것을 믿고 자기 DTO 의 `@JsonInclude(NON_NULL)` 을 떼려다 **물어보고 멈췄다.**
⚠ 🔴 **물어봐 준 덕에 사고가 안 났다** — 문서 하나가 **두 저장소를 잘못 움직일 수 있다.**

⚠ **하지 않는 것**: 문서 전문을 상수로 복제하지 않는다(정본이 둘이 된다) · 해시 값을
단언하지 않는다(「각자 계산 → 동시 공개」 합의) · 문서를 파싱해 표를 구조화하지 않는다
(문서가 코드가 된다 — **문자열 포함 검사면 충분하다**).
"""

from __future__ import annotations

import inspect
import pathlib
from typing import Final

import pytest

from ai.detection import canonical

_DOC: Final = pathlib.Path("docs/part_a/15_canonical_serialization.md")


@pytest.fixture(scope="module")
def doc() -> str:
    return _DOC.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def code() -> str:
    return inspect.getsource(canonical)


@pytest.mark.parametrize(
    "fragment", ("sort_keys=True", "ensure_ascii=False", 'separators=(",", ":")')
)
def test_the_serialization_arguments_are_in_both(fragment: str, doc: str, code: str) -> None:
    """① 직렬화 인자 셋이 **코드에도 문서에도** 있다.

    🔴 셋 중 하나가 코드에서 사라지면 red 다 — 그때 문서 §2 표도 같이 고쳐야 한다.
    """
    assert fragment in code, (
        f"`detection/canonical.py` 에서 `{fragment}` 가 사라졌다 — "
        f"직렬화가 바뀌었다면 문서 §2 표도 같이 고쳐라({_DOC})"
    )
    assert fragment in doc, (
        f"문서 §2 표에서 `{fragment}` 가 사라졌다 — 코드는 아직 그 인자를 쓴다"
    )


def test_the_sort_keys_are_in_the_document(doc: str) -> None:
    """② `detection_evidence` 정렬 키 다섯이 **그 순서로** 문서에 있다.

    ⚠ 코드가 정본이다 — 문서에서 사라지거나 순서가 달라지면 red 다.
    """
    fields = canonical._SORT_FIELDS  # noqa: SLF001 — 정본을 읽는 것이 목적이다
    assert fields == ("kind", "student_ref", "at", "source_table", "record_id"), fields
    assert ", ".join(fields) in doc, (
        f"문서 §1-2 의 정렬 키가 코드({fields})와 다르다 — 코드가 정본이다({_DOC})"
    )


def test_the_document_says_detection_evidence_is_out_of_scope_for_null_rule(doc: str) -> None:
    """🔴 ③ **§2-3 이 `detection_evidence` 에 적용되지 않는다**는 문면이 있다.

    ⚠ **이 한 줄이 이번 사고의 자리다.** 종전 문면은 *"대상 필드"* 로
    `learning_events`·`alert_context` 만 나열했고 **「나머지는 다르다」는 뜻을 안 밝혔다** —
    상대가 그 때문에 `NON_NULL` 을 떼려 했다. 🔴 **떼면 오히려 갈린다**:
    `weekly_activity` 행에 `"expected_count":null` 이 붙는데 AI 쪽에는 그 키가 없다.
    ⇒ 문면에서 사라지면 red 다.
    """
    assert "적용되지 **않는** 배열" in doc and "detection_evidence" in doc, (
        f"§2-3 의 적용 범위 정정이 문서에서 사라졌다 — 그 한 줄이 99 #167 의 자리다({_DOC})"
    )
    #: 🔴 코드가 그 사실의 근거다 — kind별로 필드를 **짓는다**(공통 dict 를 안 쓴다).
    assert "_evidence_row" in inspect.getsource(canonical), (
        "`_evidence_row` 가 사라졌다 — §2-3 의 「적용 안 됨」 근거가 코드에서 없어졌다"
    )


def test_the_passage_ref_exclusion_is_in_both(doc: str, code: str) -> None:
    """④ `passage_ref` 제외가 **코드에도 문서에도** 있다(§4)."""
    assert 'exclude={"passage_ref"}' in code, (
        f"`passage_ref` 제외가 코드에서 사라졌다 — 문서 §4 도 같이 고쳐라({_DOC})"
    )
    assert "passage_ref" in doc, f"문서 §4 에서 `passage_ref` 제외가 사라졌다({_DOC})"
