"""🔴 **문서에 적힌 enum 값 집합과 코드가 같다 — 문서를 실제로 읽어서** (99 #04 첫 적용).

⚠ **손으로 옮긴 기대 집합을 여기 두지 않는다.** 그건 *"문서와 대조한다"* 가 아니라
**두 번째 사본**을 만드는 것이고, 문서가 바뀌어도 영원히 초록이다 —
`test_counsel_state.py::test_state_fields_match_doc_exactly`가 정확히 그 형태다(그 파일에
한계를 적어 뒀다 · 99 #07). **이 파일은 문서 파일을 파싱한다.**

## 왜 필요했나

#146이 `PlanOutcome`에 값을 하나 넣고 `langgraph_state.md`를 *"다섯 가지"* 로 고쳤는데
**계약·refine·stores 세 자리의 산문이 「넷」으로 남았다.** 그때 아무 테스트도 안 걸렸다.

🔴 **다만 이 파일이 그 누락을 잡았을 것은 아니다** — 그 셋은 **docstring 산문**이고 값
집합이 아니다. 이 파일이 잡는 축은 **「문서 표의 값 집합 ↔ enum 멤버」** 이고, #146은
그 축이 **우연히 맞은** 경우다(둘 다 고쳤다). **산문은 여전히 아무도 안 본다** — 아래
「못 보는 것」 참조.

## 🔴 이 검사가 못 보는 것 (로그 67 — 목록형을 자백한다)

  · **산문 열거를 못 본다.** *"네 경우"*·*"셋 중 하나"* 같은 문장은 값 집합이 아니라
    파싱 대상이 아니다. #146이 놓친 세 자리가 정확히 그 형태다.
  · **대상이 목록형이다.** 아래 `_TABLE_PAIRS`·`_ERD_PAIRS`에 적은 쌍만 본다 — 새 enum이
    문서에 적혀도 여기 등재하지 않으면 안 걸린다. **전칭이 불가능한 대상**이라
    (문서마다 표기 형식이 다르다) 의식적으로 목록형을 골랐고, 대신
    **「목록이 비면 실패」**(`test_the_scan_finds_...`)로 검사 경로 절단을 막는다.
  · **ERD의 값 목록이 아닌 주석은 대상이 아니다** — `lease_owner "leased|running에서만"`,
    `error_code "failed|cancelled 사유"` 처럼 `|`가 있어도 **설명 문장**인 것이 있다.
    그래서 컬럼명을 명시 등재한다(정규식으로 `|`를 싹 훑으면 거짓 쌍이 생긴다).
"""

from __future__ import annotations

import re
from enum import StrEnum
from pathlib import Path
from typing import Final, NamedTuple

import pytest

from ai.contracts.agents import JobPhase, PriorityClass, WorkerKind
from ai.contracts.composition import PlanOutcome
from ai.contracts.execution import Capability

_DOCS: Final = Path(__file__).resolve().parents[3] / "docs"


class _Pair(NamedTuple):
    """문서의 값 집합 하나와 그것이 정본으로 삼는 enum."""

    label: str
    doc: Path
    enum: type[StrEnum]


# ── ⓐ 마크다운 표 — 첫 열이 백틱으로 감싼 값 ──────────────────────

#: `> | \`value\` | 뜻 | 대응 |` 형태의 인용 표에서 첫 열 값을 뽑는다.
_QUOTED_TABLE_CELL: Final = re.compile(r"^>\s*\|\s*`([a-z_]+)`\s*\|", re.MULTILINE)

#: 표가 사는 절을 잘라내는 앵커 — 문서 전체를 훑으면 다른 표의 값이 섞인다.
_TABLE_PAIRS: Final = (
    (
        _Pair("plan_outcome", _DOCS / "policies" / "langgraph_state.md", PlanOutcome),
        "`plan_outcome`·`plan_dropped`의 단위와 근거",
        "🔴 **분모는 잡이다.**",
    ),
)


def _table_values(doc: Path, start: str, end: str) -> set[str]:
    """`start`와 `end` 사이 구간의 인용 표에서 첫 열 값을 모은다."""
    text = doc.read_text(encoding="utf-8")
    i = text.find(start)
    j = text.find(end, i + 1) if i >= 0 else -1
    if i < 0 or j < 0:
        return set()
    return set(_QUOTED_TABLE_CELL.findall(text[i:j]))


# ── ⓑ mermaid ERD — `varchar col "a|b|c"` ─────────────────────────

#: 🔴 **컬럼명을 명시 등재한다** — `|`가 있는 주석 중 **값 목록이 아닌 것**이 있다
#: (`lease_owner "leased|running에서만"`). 정규식으로 싹 훑으면 거짓 쌍이 생긴다.
_ERD_PAIRS: Final = (
    _Pair("capability", _DOCS / "06_erd.md", Capability),
    _Pair("agent_kind", _DOCS / "06_erd.md", WorkerKind),
    _Pair("priority_class", _DOCS / "06_erd.md", PriorityClass),
    _Pair("status", _DOCS / "06_erd.md", JobPhase),
)


def _erd_values(doc: Path, column: str) -> set[str]:
    """`varchar <column> "a|b|c"` 주석의 값 집합."""
    match = re.search(rf'^\s*varchar {column} "([^"]+)"', doc.read_text("utf-8"), re.M)
    return set(match.group(1).split("|")) if match else set()


# ── 검사 ───────────────────────────────────────────────────────────


def test_the_scan_finds_documented_value_sets() -> None:
    """🔴 **검사 경로가 끊기면 통과가 아니라 실패다.**

    파싱이 0건을 돌려주면 *"위반이 없다"* 가 아니라 *"안 봤다"* 다 — 문서 형식이 바뀌거나
    절이 개명되면 정규식이 조용히 빈 집합을 낸다. 선례는
    `test_execution_id_identity.py::test_the_scan_finds_envelope_calls`.
    """
    for pair, start, end in _TABLE_PAIRS:
        assert _table_values(pair.doc, start, end), (
            f"{pair.doc.name}의 {pair.label} 표에서 값을 하나도 못 찾았다 — 위반이 없는 게 "
            "아니라 **검사가 끊긴 것**이다(절 제목·표 형식 변경 확인)"
        )
    for pair in _ERD_PAIRS:
        assert _erd_values(pair.doc, pair.label), (
            f"{pair.doc.name}에서 `varchar {pair.label} \"…\"` 주석을 못 찾았다 — "
            "검사가 끊겼다"
        )


@pytest.mark.parametrize(
    ("pair", "start", "end"),
    _TABLE_PAIRS,
    ids=[p.label for p, _s, _e in _TABLE_PAIRS],
)
def test_documented_table_matches_the_enum(
    pair: _Pair, start: str, end: str
) -> None:
    """🔴 문서 표의 값 집합 == enum 멤버. **문서를 읽어서** 비교한다."""
    documented = _table_values(pair.doc, start, end)
    coded = {member.value for member in pair.enum}
    assert documented == coded, (
        f"{pair.doc.name}의 {pair.label} 표와 `{pair.enum.__name__}`이 갈렸다.\n"
        f"  문서에만: {sorted(documented - coded)}\n"
        f"  코드에만: {sorted(coded - documented)}\n"
        "🔴 값을 늘렸으면 문서 표도 같이 늘린다 — 한쪽만 고치면 문서가 거짓이 된다."
    )


@pytest.mark.parametrize("pair", _ERD_PAIRS, ids=[p.label for p in _ERD_PAIRS])
def test_erd_comment_matches_the_enum(pair: _Pair) -> None:
    """🔴 ERD 주석의 값 목록 == enum 멤버.

    ⚠ **`part_a/02_design.md`에 같은 블록을 두지 않는 것이 전제다**(99 #02 ⓑ) — 두 곳에
    있으면 이 검사가 한 곳만 지키고 다른 곳이 조용히 갈린다. 실제로 그렇게 갈렸었다
    (`capability`가 거기서 **3종**이었다).
    """
    documented = _erd_values(pair.doc, pair.label)
    coded = {member.value for member in pair.enum}
    assert documented == coded, (
        f"{pair.doc.name}의 `{pair.label}` 주석과 `{pair.enum.__name__}`이 갈렸다.\n"
        f"  문서에만: {sorted(documented - coded)}\n"
        f"  코드에만: {sorted(coded - documented)}"
    )


def test_the_guard_would_catch_a_violation(tmp_path: Path) -> None:
    """⚠ **가드가 실제로 잡는지**를 이 파일 안에서 확인한다 — 뒤집기의 상시화(로그 70).

    ⚠ `tempfile.NamedTemporaryFile`을 쓰지 마라 — win32에서 열려 있는 동안 그 경로를 다시
    열 수 없다(#134 실측).
    """
    erd = tmp_path / "fake_erd.md"
    erd.write_text('    varchar capability "detection|composition"\n', encoding="utf-8")
    assert _erd_values(erd, "capability") == {"detection", "composition"}
    assert _erd_values(erd, "capability") != {m.value for m in Capability}, (
        "두 값짜리 가짜 주석이 실제 enum과 같다고 나온다 — 파싱이 안 되고 있다"
    )

    table = tmp_path / "fake_table.md"
    table.write_text("시작\n> | `ok` | 뜻 | 대응 |\n> | `nope` | 뜻 | 대응 |\n끝\n", "utf-8")
    assert _table_values(table, "시작", "끝") == {"ok", "nope"}
    assert _table_values(table, "없는앵커", "끝") == set(), (
        "앵커를 못 찾았는데 값을 돌려준다 — 검사 경로 절단을 못 잡는다"
    )
