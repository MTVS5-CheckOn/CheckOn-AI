"""A 소유 문서의 영역 **수·값 목록·레이더 축**이 **코드 정본과 같은가** (99 · #179 5영역 확정).

준영님 #179가 breaking change로 `speech` + `writing` → `speech_writing`을 확정했다 ⇒
**정본은 `contracts/taxonomy.AreaTag`의 5영역**이다.

🔴 **리터럴 `5`를 이 파일에 박지 않는다** — 코드에서 파생시킨다. 박으면 `AreaTag`가 또 바뀔 때
**문서와 테스트가 같이 낡고** 검사가 *"둘 다 틀린 채로 일치"* 를 통과시킨다.

⚠ **범위는 A 소유 `docs/part_a/`뿐이다** — 공용(`policies/`·`04`·`05`·`06_erd`·`07_standard_schema`·
`02_ownership`)은 **승인 축이 다르고** B 소유(`part_b/`)는 무접촉이며 `99`는 **이력 문면**이다.
🔴 **검사 이름을 문서 축보다 넓게 짓지 않는다**(로그 85) — `src/`를 범위에 넣지 않는다.

🔴 **축이 셋인 이유 — 하나씩 세울 때마다 앞 축이 못 보던 자리가 나왔다.**

1. **수 축**(「N영역」) 하나만 세웠더니 `03_usecases.md`의 I4 시나리오처럼 **수는 5로 고쳐지고
   바로 뒤 값 목록은 여섯인** 줄이 green으로 지나갔다 — **한 줄이 자기 안에서 모순**이다.
2. **값 목록 축**을 세웠더니 `09_detect_spec.md`의 요청 예시 주석처럼 **「N영역」 표기가 아예 없어
   수 축에 안 걸리는** 자리가 나왔다.
3. **레이더 축**은 07의 면제 대상 그 자체인데 **수 축과 다른 값**이다 — 영역 수만 정정하고
   화면 축을 안 고쳐도 *"맞춰졌다"* 로 읽혀 **면제가 거짓으로 만료**된다.

*"어휘 정합"* 이라는 **이름이 실제로 보는 것보다 넓었다**(로그 85 계열).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Final

import pytest

from ai.contracts.taxonomy import AreaTag

_ROOT: Final = Path(__file__).resolve().parents[3]
_PART_A: Final = _ROOT / "docs" / "part_a"

#: 문서가 영역 **수**를 말하는 자리 — `6영역`·`5영역` 꼴.
_AREA_COUNT: Final = re.compile(r"(\d+)영역")
#: 07 차트 ①의 **레이더 축 수** — 수 축과 **다른 값**이다(화면 산출물).
_AXIS_COUNT: Final = re.compile(r"레이더\((\d+)축\)")

#: 값 목록 축 — `AreaTag` 값과 **#179가 폐기한 옛 값**을 합쳐 후보 어휘로 본다.
_CANONICAL_VALUES: Final = frozenset(a.value for a in AreaTag)
_RETIRED_VALUES: Final = frozenset({"speech", "writing"})
#: 🔴 한 줄에 후보 어휘가 이만큼 모이면 **전체 enum을 선언하는 줄**로 본다 — 산문의 우연한
#: 단어 하나를 목록으로 오인하지 않기 위한 하한이다.
_LIST_ARITY: Final = 3
_WORD: Final = re.compile(r"[a-z_]+")

#: 🔴 **제품 확인이 선행이라 이 회차에 안 고치는 자리** — 값은 사유다.
#: ⚠ **파일 전체가 아니라 「차트 ① 행」 하나다.** 파일로 면제하면 같은 문서의 **다른 낡은 표기까지
#: 전부 조용히 통과**한다 — 면제는 **제품 확인이 걸린 두 값**에만 준다.
_PENDING_CHART_ROW: Final = {
    "07_report_spec.md": (
        "차트 ①의 영역 수·레이더 축 — 축 수 변경은 화면 산출물 영향이라 제품 확인이 선행이다"
        "(#179 · part_b/09 §1도 그 줄에만 「제품 확인 필요」를 달았다)"
    ),
}
#: 갈림을 공개하는 문면의 앵커.
_DISCLOSURE: Final = "영역 정본은"
#: 차트 ① 행 — `| ① | … |`.
_CHART_ONE: Final = re.compile(r"^\|\s*①\s*\|.*$", re.M)


def _chart_one_row(text: str) -> str | None:
    match = _CHART_ONE.search(text)
    return match.group(0) if match else None


def _chart_one_counts(text: str) -> tuple[int | None, int | None]:
    """차트 ① 행이 말하는 **(영역 수, 레이더 축 수)** — 없으면 `None`."""
    row = _chart_one_row(text)
    if row is None:
        return (None, None)
    area = _AREA_COUNT.search(row)
    axis = _AXIS_COUNT.search(row)
    return (
        int(area.group(1)) if area else None,
        int(axis.group(1)) if axis else None,
    )


def _pending_complaint(
    area: int | None, axis: int | None, *, disclosed: bool, canonical: int
) -> str | None:
    """🔴 면제 대상 행의 **네 상태**를 가르는 순수 판정 — `None`이면 정상.

    ⚠ **「한쪽만 정정」이 가장 위험하다** — 영역 수만 맞추면 대조는 *"맞춰졌다"* 로 읽는데
    **화면 축은 여전히 틀렸다.** 면제는 **두 값을 묶어** 받았으므로 **함께** 풀려야 한다.
    """
    #: 🔴 **이것이 앞 PR의 판정이다** — 레이더 축(`axis`)을 **받아 놓고 안 본다.**
    del axis
    diverged = area != canonical
    if diverged and not disclosed:
        return "정본과 갈렸는데 그 사실이 문서에 없다 — 다음 사람이 참인 쪽(코드)을 거짓으로 고친다"
    if not diverged and disclosed:
        return "갈림 공개 문면이 낡았다 — 정본과 맞춰졌으니 그 문단을 지워라(자동 만료)"
    return None


def _area_count_sites() -> list[tuple[str, int, int]]:
    """`part_a`의 「N영역」 자리 — `(파일, 행번호, 수)`. **면제 행은 뺀다.**"""
    found: list[tuple[str, int, int]] = []
    for path in sorted(_PART_A.rglob("*.md")):
        text = path.read_text(encoding="utf-8")
        #: 🔴 **이것이 앞 PR의 면제다** — 파일 하나를 통째로 안 본다.
        if path.name in _PENDING_CHART_ROW:
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            for match in _AREA_COUNT.finditer(line):
                found.append((path.name, lineno, int(match.group(1))))
    return found


def _area_value_lists() -> list[tuple[str, int, frozenset[str]]]:
    """part_a 문서에서 **영역 값을 열거하는 줄**과 그 줄이 쓴 어휘를 돌려준다."""
    vocabulary = _CANONICAL_VALUES | _RETIRED_VALUES
    found: list[tuple[str, int, frozenset[str]]] = []
    for path in sorted(_PART_A.rglob("*.md")):
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            used = frozenset(w for w in _WORD.findall(line) if w in vocabulary)
            if len(used) >= _LIST_ARITY:
                found.append((path.name, lineno, used))
    return found


def test_the_document_scan_finds_area_counts() -> None:
    """🔴 검사 경로가 끊기면 통과가 아니라 실패다 — 0건이면 **문서 축을 못 읽은 것**이다."""
    assert _area_count_sites(), f"`part_a/`에서 「N영역」 표기를 못 찾았다 — 경로가 틀렸나: {_PART_A}"


def test_every_exclusion_carries_a_reason() -> None:
    assert all(reason.strip() for reason in _PENDING_CHART_ROW.values())


def test_part_a_documents_agree_with_the_code() -> None:
    """🔴 **문서가 말하는 영역 수 == `AreaTag` 멤버 수.** 코드가 바뀌면 문서가 red다."""
    canonical = len(list(AreaTag))
    stale = [
        (name, lineno, count)
        for name, lineno, count in _area_count_sites()
        if count != canonical
    ]
    assert not stale, (
        f"A 소유 문서가 정본({canonical}영역)과 다른 수를 말한다: {stale} — "
        "정본은 `contracts/taxonomy.AreaTag`다"
    )


def test_the_value_list_scan_finds_enumerations() -> None:
    """🔴 절단 가드 — 값 목록을 0건 찾으면 *"위반 없음"* 이 아니라 **안 본 것**이다."""
    assert _area_value_lists(), (
        f"`part_a/`에서 영역 **값 목록**을 못 찾았다 — 어휘나 하한이 틀렸나: {_PART_A}"
    )


def test_part_a_value_lists_are_exactly_the_canonical_set() -> None:
    """🔴 **폐기값 금지보다 넓게 — 집합이 정확히 같은지** 본다.

    ⚠ *"옛 값을 안 썼다"* 만 보면 **정본 값 하나가 빠진 목록**과 **없는 값이 낀 목록**을 놓친다.
    이 저장소의 세 자리는 전부 **전체 enum을 선언하는 문면**이라 **정확 대조가 맞다.**
    ⚠ **순서는 계약이 아니다** — 집합으로 비교한다.
    """
    wrong = [
        (
            name,
            lineno,
            sorted(used - _CANONICAL_VALUES) or None,
            sorted(_CANONICAL_VALUES - used) or None,
        )
        for name, lineno, used in _area_value_lists()
        if used != _CANONICAL_VALUES
    ]
    assert not wrong, (
        f"A 소유 문서의 영역 값 목록이 정본 집합과 다르다 `(파일, 행, 남는 값, 빠진 값)`: {wrong} — "
        "정본은 `contracts/taxonomy.AreaTag`다(#179로 speech+writing → speech_writing)"
    )


def test_the_pending_row_is_the_only_thing_exempted() -> None:
    """🔴 **면제가 파일 전체를 덮지 않는다** — 같은 문서의 다른 낡은 표기는 그대로 걸려야 한다.

    ⚠ 파일 단위 면제는 *"이 문서는 안 본다"* 와 같다 — 제품 확인이 걸린 것은 **두 값뿐**이다.
    """
    canonical = len(list(AreaTag))
    for name in _PENDING_CHART_ROW:
        path = _PART_A / name
        assert path.is_file(), f"면제 대상 문서가 없다: {name}"
        row = _chart_one_row(path.read_text(encoding="utf-8"))
        assert row is not None, f"{name}에서 차트 ① 행을 못 찾았다 — 면제 범위를 못 정한다"
        others = [(lineno, count) for found, lineno, count in _area_count_sites() if found == name]
        assert all(count == canonical for _, count in others), (
            f"{name}의 차트 ① 행 **밖**에 낡은 영역 수가 있다: {others} — 면제는 그 행뿐이다"
        )


def test_the_pending_row_diverges_and_says_so() -> None:
    """🔴 이 회차의 실제 상태 — **둘 다 안 맞고 공개문이 있다**(정상)."""
    canonical = len(list(AreaTag))
    for name, reason in _PENDING_CHART_ROW.items():
        text = (_PART_A / name).read_text(encoding="utf-8")
        area, axis = _chart_one_counts(text)
        complaint = _pending_complaint(
            area, axis, disclosed=_DISCLOSURE in text, canonical=canonical
        )
        assert complaint is None, f"{name} — {complaint} (면제 사유: {reason})"


@pytest.mark.parametrize(
    ("area", "axis", "disclosed", "expected_red"),
    [
        pytest.param(6, 6, True, False, id="둘 다 안 맞고 공개문 있음 → green"),
        pytest.param(6, 6, False, True, id="둘 다 안 맞는데 공개문 없음 → red"),
        pytest.param(5, 6, True, True, id="영역 수만 정정 → red"),
        pytest.param(6, 5, True, True, id="레이더 축만 정정 → red"),
        pytest.param(5, 5, True, True, id="둘 다 정정 + 공개문 남음 → red"),
        pytest.param(5, 5, False, False, id="둘 다 정정 + 공개문 제거 → green"),
        pytest.param(None, 6, True, True, id="차트 행을 못 읽음 → red"),
    ],
)
def test_the_pending_verdict_covers_every_state(
    area: int | None, axis: int | None, *, disclosed: bool, expected_red: bool
) -> None:
    """🔴 **여섯 상태를 다 건다** — 특히 「한쪽만 정정」이 red여야 한다.

    ⚠ 정본 수를 **`AreaTag`에서 받아** 넣는다 — 여기서도 리터럴 `5`를 안 박는다.
    """
    canonical = len(list(AreaTag))
    fixed = 5 if canonical == 5 else canonical
    stale = fixed + 1
    remap = {5: fixed, 6: stale}
    complaint = _pending_complaint(
        None if area is None else remap[area],
        None if axis is None else remap[axis],
        disclosed=disclosed,
        canonical=canonical,
    )
    assert (complaint is not None) is expected_red, (
        f"(영역 {area} · 축 {axis} · 공개문 {disclosed}) 판정이 틀렸다: {complaint!r}"
    )
