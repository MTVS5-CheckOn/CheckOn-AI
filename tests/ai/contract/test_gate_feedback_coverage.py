"""게이트 사유 코드 전수가 **소비자 양쪽에** 등재됐는가 — 05 §5·§6-2 CI 대조.

소비자는 둘이다. 🔴 **한쪽만 가드하면 절반만 잡힌다** — #113이 `internal_term`을 만들며
`gate_feedback.yaml`에는 등재했는데 `_GATE_REASON_TO_BLOCK`에는 빠뜨렸고, 이 파일이
전자만 봐서 통과시켰다.

| 소비자 | 미등재 시 |
| --- | --- |
| `gate_feedback.yaml` | 재생성 지시가 `default`로 조용히 퇴화(㉙) |
| `refine._GATE_REASON_TO_BLOCK` | 차단 사유가 `tone_violation`으로 조용히 수렴 |

⚠ `contracts.counsel.wire_status_for`는 **소비자가 아니다.** 그건 `fail_reason` 접두
(`gate_exhausted`)만 보고, 게이트 사유는 콜론 뒤 detail이라 와이어에서 버려진다.

**왜 CI에서 잡아야 하나.** 미등재 코드는 런타임에서 터지지 않는다 — `instruction_for`가
조용히 `default` 문구로 퇴화한다. 새 게이트 사유를 추가한 사람은 아무 신호도 못 받고,
그 사유로 실패한 재생성은 "직전 시도가 왜 막혔는지"를 못 전한 채 상한까지 돈다(㉙ 부활).
`_LIFECYCLE_KO` 값 대조(⑯ 4-2)와 같은 형태다 — 크래시 대신 CI 검출.

추출은 게이트 소스에서 `reason=` 리터럴을 읽는 정적 방식이다. **추출이 끊기면 이 테스트는
조용히 통과한다** — 그래서 하한 개수를 함께 단정한다(검사 경로 절단 검출).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from ai.composition.counsel.refine import _GATE_REASON_TO_BLOCK
from ai.composition.gate_feedback import instruction_for, load_gate_feedback

_SRC = Path(__file__).resolve().parents[3] / "src" / "ai" / "composition"

#: 사유 코드를 내는 게이트 소스 — 새 게이트가 생기면 여기에 추가한다.
_GATE_SOURCES = {
    "counsel": _SRC / "counsel" / "gate.py",
    "briefing": _SRC / "briefing_gate.py",
}

#: `reason="empty"` · `reason=f"forbidden:{hits[0]}"` 양쪽을 잡는다.
_REASON_RE = re.compile(r"""reason=f?["']([a-z_]+)""")

#: 실측 하한(8/6 갱신 — counsel에 `internal_term` 추가): counsel 7종 · briefing 5종.
#: 추출 정규식이 소스 변화로 안 맞게 되면 이 하한이 먼저 깨져 "검사 경로가 끊겼다"를 알린다.
_MIN_CODES = {"counsel": 7, "briefing": 5}


def _emitted_codes(path: Path) -> set[str]:
    return set(_REASON_RE.findall(path.read_text(encoding="utf-8")))


@pytest.mark.parametrize("gate", sorted(_GATE_SOURCES))
def test_extraction_still_finds_reason_codes(gate: str) -> None:
    """검사 경로 절단 검출 — 추출이 0건이면 아래 대조는 공허하게 통과한다."""
    codes = _emitted_codes(_GATE_SOURCES[gate])
    assert len(codes) >= _MIN_CODES[gate], f"{gate} 게이트에서 사유 코드 추출 실패: {codes}"


@pytest.mark.parametrize("gate", sorted(_GATE_SOURCES))
def test_every_emitted_reason_has_an_instruction(gate: str) -> None:
    """게이트가 내는 코드 전수가 yaml에 등재돼 있다(05 §5)."""
    registered = set(load_gate_feedback().instructions)
    missing = sorted(_emitted_codes(_GATE_SOURCES[gate]) - registered)
    assert not missing, (
        f"{gate} 게이트 사유가 gate_feedback.yaml에 없다: {missing} — "
        "등재하지 않으면 default로 조용히 퇴화한다(99 D ㉙)"
    )


# ── 소비자 ② refine의 차단 사유 매핑 ─────────────────────────────


def test_every_counsel_reason_has_a_block_reason() -> None:
    """🔴 **이번 결함을 막는 가드.** counsel 게이트 사유 전수가 매핑에 등재돼 있다.

    ⚠ **등재 여부만 본다 — 값은 강제하지 않는다.** 형식 실패 4종과 `internal_term`이
    `tone_violation`으로 수렴하는 건 `BlockedReason`(양자)에 맞는 값이 없어서이고(99 ㊴),
    그건 이 가드가 판정할 문제가 아니다. 가드는 **누락**을 잡는다.

    ⚠ briefing 게이트는 대상이 아니다 — refine은 counsel 초안만 다듬는다.
    """
    missing = sorted(_emitted_codes(_GATE_SOURCES["counsel"]) - set(_GATE_REASON_TO_BLOCK))
    assert not missing, (
        f"counsel 게이트 사유가 _GATE_REASON_TO_BLOCK에 없다: {missing} — "
        "등재하지 않으면 tone_violation으로 조용히 수렴해 "
        "'의도적 수렴'과 '누락'이 구분되지 않는다"
    )


def test_no_orphan_block_reason_mappings() -> None:
    """반대 방향 — 어느 게이트도 내지 않는 매핑이 남아 있지 않다(죽은 데이터, 03 §1)."""
    orphans = sorted(set(_GATE_REASON_TO_BLOCK) - _emitted_codes(_GATE_SOURCES["counsel"]))
    assert not orphans, f"counsel 게이트가 내지 않는 사유 매핑: {orphans}"


def test_both_consumers_see_the_same_reason_list() -> None:
    """🔴 두 가드가 **같은 출처**를 읽는다 — 다르면 한쪽만 통과하는 상태가 생긴다.

    그게 이번 결함의 확대판이다. 사유 목록의 정본은 아직 `gate.py`의 `reason=` 리터럴이고
    (enum화는 99 ㉟), 그래서 두 대조가 **같은 추출 함수**를 쓴다 — 복제하면 드리프트한다.
    """
    counsel_codes = _emitted_codes(_GATE_SOURCES["counsel"])
    assert counsel_codes <= set(load_gate_feedback().instructions)
    assert counsel_codes <= set(_GATE_REASON_TO_BLOCK)


def test_no_orphan_instructions() -> None:
    """반대 방향 — 어느 게이트도 내지 않는 문구가 남아 있지 않다(죽은 데이터, 03 §1)."""
    emitted = set().union(*(_emitted_codes(p) for p in _GATE_SOURCES.values()))
    orphans = sorted(set(load_gate_feedback().instructions) - emitted)
    assert not orphans, f"어느 게이트도 내지 않는 사유 문구: {orphans}"


def test_empty_reason_yields_no_instruction() -> None:
    """1회차 계약 — 빈 사유는 빈 문구다(프롬프트 바이트 동일의 근거, 05 §6-2)."""
    assert instruction_for("") == ""


def test_detail_is_appended_not_swallowed() -> None:
    """콜론 뒤 가변부가 문구에 실린다 — 어느 숫자가 문제였는지 LLM이 알아야 한다."""
    assert "83" in instruction_for("ungrounded_number:83")
    assert "게으르" in instruction_for("forbidden:게으르")


def test_detail_free_reason_has_no_dangling_suffix() -> None:
    """가변부가 없는 코드(`token_leak`)에 빈 괄호가 붙지 않는다."""
    text = instruction_for("token_leak")
    assert text == load_gate_feedback().instructions["token_leak"]


def test_unknown_reason_falls_back_to_default() -> None:
    """미등재 코드도 빈 지시로 퇴화하지 않는다 — 안전망은 있되 CI가 먼저 막는다."""
    assert instruction_for("brand_new_code") == load_gate_feedback().default
