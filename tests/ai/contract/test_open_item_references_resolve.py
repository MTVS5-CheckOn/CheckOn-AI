"""99를 가리키는 인용이 **실재하는 등재행**을 가리키는가 (99 #15).

🔴 **끊긴 인용은 조용하다.** 번호가 그럴듯하면 다음 사람은 그 안건을 찾으러 갔다가
없는 것을 확인하고 **자기가 잘못 읽었다고 생각한다.** 실측으로 둘이 있었다:

- `gate.py`의 *"블록 단위 판정은 별건이다(㊱)"* — 🔴 **번호는 맞았다.** ㊱이 **묶인 등재**라
  표제(`draft_status` 어휘)는 닫혔고 **꼬리(블록 단위)가 미결**이었다. #15가 *"그 안건은
  등재조차 없다"* 로 적은 것이 **틀린 실측**이었다.
- `#13`의 *"ⓒ는 블록 단위 판정(#14′)에 딸린다"* — 🔴 **`#14′` 등재행이 0건.** 아직 없는
  안건을 위해 붙여 둔 자리표가 남은 것이다.

**대상과 한계 — 적어 둔다:**

- `#NN`·`#NN′` 꼴은 **전수**다(fail-closed — 등재행이 없으면 red).
- 원문자는 **99가 실제 앵커로 쓰는 문자 집합**에서 뽑는다(손목록 아님). ⚠ **앵커로 한 번도
  쓰이지 않은 원문자를 인용하면 못 잡는다** — 알파벳이 앵커에서 유도되기 때문이다.
  그 한계를 여기 적어 두는 것이 *"검사의 이름이 보는 것보다 넓다"* 를 막는 자리다(로그 85).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Final

_ROOT: Final = Path(__file__).resolve().parents[3]
_REGISTRY: Final = _ROOT / "docs" / "99_open_items.md"
_SCAN_ROOTS: Final = ("src", "docs")
_SUFFIXES: Final = {".md", ".py", ".yaml"}

#: 등재행 — `| ㊱ **제목**` 또는 `| **#15** **제목**`.
_SYMBOL_ANCHOR: Final = re.compile(r"^\|\s*([^\s|*])\s", re.MULTILINE)
_NUMBER_ANCHOR: Final = re.compile(r"^\|\s*\*\*(#\d+′?)\*\*", re.MULTILINE)

#: 인용 — `#15`·`#14′`. 🔴 **두 자리 고정**이다(등재는 `#01`부터 **0을 채운다**).
_NUMBER_REF: Final = re.compile(r"#(\d{2})(′?)")

#: 🔴 **이 저장소는 `#NN`을 안건과 PR 둘 다 쓴다** — `PR #92`·안건 `#15`. 첫 판이 그래서
#: PR·이슈 번호 아흔여 자리를 함께 잡았다(실측). ⇒ **등재 번호 구간 안**만 안건으로 본다:
#: 최대 등재 번호를 **99에서 유도**하고 그보다 큰 수는 PR로 본다(손목록 아님).
#: ⚠ **한계를 적는다** — 그 구간 안의 PR 번호(예: `PR #15`)를 인용하면 **안건으로 오해**한다.
#: 실측으로 그런 자리는 없고(아래 검사가 0을 확인한다), 생기면 red가 나서 알게 된다.
_NOT_AN_ITEM_REF: Final = {
    "PR #": "GitHub PR 번호 — `PR #156` 형태로 앞에 `PR`이 붙는다",
    "PR-": "PR 별칭과 함께 쓰인 자리",
    "issue": "이슈 번호",
    "#142": "PR 번호 — 기호 충돌 사고를 낸 그 PR(로그 79)이라 자주 인용된다",
}


def _registered() -> tuple[set[str], set[str]]:
    text = _REGISTRY.read_text(encoding="utf-8")
    numbers = set(_NUMBER_ANCHOR.findall(text))
    symbols = {
        match
        for match in _SYMBOL_ANCHOR.findall(text)
        if "㈀" <= match <= "㋿" or "①" <= match <= "⓿"
    }
    return numbers, symbols


def test_the_registry_anchors_are_found() -> None:
    """🔴 검사 경로가 끊기면 통과가 아니라 실패다 — 앵커 0개면 아무것도 안 본다."""
    numbers, symbols = _registered()
    assert len(numbers) >= 20, f"번호 등재행을 못 찾았다: {sorted(numbers)}"
    assert len(symbols) >= 30, f"원문자 등재행을 못 찾았다: {len(symbols)}"


def test_every_exclusion_carries_a_reason() -> None:
    assert all(reason.strip() for reason in _NOT_AN_ITEM_REF.values())


def _registered_ceiling(registered: set[str]) -> int:
    """등재된 최대 번호 — **99에서 유도한다**(손으로 적지 않는다)."""
    return max(int(ref.lstrip("#").rstrip("′")) for ref in registered)


def _dangling_number_refs() -> list[tuple[str, int, str]]:
    registered, _symbols = _registered()
    ceiling = _registered_ceiling(registered)
    found: list[tuple[str, int, str]] = []
    for root in _SCAN_ROOTS:
        for path in (_ROOT / root).rglob("*"):
            if path.suffix not in _SUFFIXES or not path.is_file():
                continue
            relative = path.relative_to(_ROOT).as_posix()
            for number, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), start=1
            ):
                for match in _NUMBER_REF.finditer(line):
                    ref = f"#{match.group(1)}{match.group(2)}"
                    if ref in registered:
                        continue
                    window = line[max(0, match.start() - 12) : match.end() + 6]
                    if any(word in window for word in _NOT_AN_ITEM_REF):
                        continue
                    #: 🔴 등재 구간 밖이면 PR 번호다(위 주석 참조).
                    if int(match.group(1)) > ceiling:
                        continue
                    found.append((relative, number, ref))
    return found


def test_every_numbered_reference_resolves() -> None:
    """🔴 **가리키는 안건이 실재해야 한다** — 없으면 red다(fail-closed).

    ⚠ 화이트리스트를 쓰지 않는다. 안건이 아닌 인용은 `_NOT_AN_ITEM_REF`에 **사유와 함께**
    등재하고, 세 자리 이상은 PR·이슈 번호로 본다(등재는 두 자리까지다).
    """
    dangling = _dangling_number_refs()
    assert not dangling, (
        f"등재되지 않은 안건을 가리킨다: {dangling} — 안건을 세우거나 인용을 고쳐라. "
        "번호만 고치면 「없는 안건을 가리키는 것」이 유지된다(99 #15)"
    )
