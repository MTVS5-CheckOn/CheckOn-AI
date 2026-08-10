"""A 소유 문서가 말하는 영역 수가 **코드 정본과 같은가** (99 · #179 5영역 확정).

준영님 #179가 breaking change로 `speech` + `writing` → `speech_writing`을 확정했다 ⇒
**정본은 `contracts/taxonomy.AreaTag`의 5영역**이다.

🔴 **리터럴 `5`를 이 파일에 박지 않는다** — 코드에서 파생시킨다. 박으면 `AreaTag`가 또 바뀔 때
**문서와 테스트가 같이 낡고** 검사가 *"둘 다 틀린 채로 일치"* 를 통과시킨다.

⚠ **범위는 A 소유 `docs/part_a/`뿐이다** — 공용(`policies/`·`04`·`05`·`06_erd`·`07_standard_schema`·
`02_ownership`)은 **승인 축이 다르고** B 소유(`part_b/`)는 무접촉이며 `99`는 **이력 문면**이다.
🔴 **검사 이름을 문서 축보다 넓게 짓지 않는다**(로그 85) — `src/`를 범위에 넣지 않는다.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Final

from ai.contracts.taxonomy import AreaTag

_ROOT: Final = Path(__file__).resolve().parents[3]
_PART_A: Final = _ROOT / "docs" / "part_a"

#: 문서가 영역 **수**를 말하는 자리 — `6영역`·`5영역` 꼴.
_AREA_COUNT: Final = re.compile(r"(\d+)영역")

#: 🔴 **제품 확인이 선행이라 이 회차에 안 고치는 자리** — 값은 사유다.
#: 레이더 축 수 변경은 **화면 산출물 영향**이라 제품 확인이 먼저다
#: (B도 그 줄에 「제품 확인 필요」를 달았다).
#: ⚠ **조용히 범위 밖으로 밀지 않는다** — 아래 검사가 **공개 문면을 요구**하고,
#: 5축으로 고쳐지면 **그 문면이 남는 것이 red**가 된다(자동 만료).
_PENDING_PRODUCT_REVIEW: Final = {
    "07_report_spec.md": (
        "레이더 6축 → 5축은 화면 산출물 영향이라 제품 확인이 선행이다"
        "(#179 · B도 「제품 확인 필요」를 달았다)"
    ),
}
_DISCLOSURE: Final = "영역 정본은"


def _area_count_sites() -> list[tuple[str, int]]:
    found: list[tuple[str, int]] = []
    for path in sorted(_PART_A.rglob("*.md")):
        for match in _AREA_COUNT.finditer(path.read_text(encoding="utf-8")):
            found.append((path.name, int(match.group(1))))
    return found


def test_the_document_scan_finds_area_counts() -> None:
    """🔴 검사 경로가 끊기면 통과가 아니라 실패다 — 0건이면 **문서 축을 못 읽은 것**이다."""
    sites = _area_count_sites()
    assert sites, f"`part_a/`에서 「N영역」 표기를 못 찾았다 — 경로가 틀렸나: {_PART_A}"


def test_every_exclusion_carries_a_reason() -> None:
    assert all(reason.strip() for reason in _PENDING_PRODUCT_REVIEW.values())


def test_part_a_documents_agree_with_the_code() -> None:
    """🔴 **문서가 말하는 영역 수 == `AreaTag` 멤버 수.** 코드가 바뀌면 문서가 red다."""
    canonical = len(list(AreaTag))
    stale = [
        (name, count)
        for name, count in _area_count_sites()
        if count != canonical and name not in _PENDING_PRODUCT_REVIEW
    ]
    assert not stale, (
        f"A 소유 문서가 정본({canonical}영역)과 다른 수를 말한다: {stale} — "
        "정본은 `contracts/taxonomy.AreaTag`다"
    )


def test_the_pending_document_discloses_the_divergence() -> None:
    """🔴 **고칠 수 없는 쪽이 있으면 그 문서가 갈림을 말한다** — 그리고 **자동 만료**한다.

    ⚠ 안 적으면 **다음 사람이 참인 쪽(코드)을 거짓으로 고친다**(99 #02 · PR-ν 선례).
    5축으로 고쳐지면 갈림이 없어지고 **공개 문면이 남은 것이 red**가 되어 지우게 만든다.
    """
    canonical = len(list(AreaTag))
    for name in _PENDING_PRODUCT_REVIEW:
        path = _PART_A / name
        assert path.is_file(), f"예외 대상 문서가 없다: {name}"
        text = path.read_text(encoding="utf-8")
        diverged = any(
            count != canonical
            for found, count in _area_count_sites()
            if found == name
        )
        if diverged:
            assert _DISCLOSURE in text, (
                f"{name}이 정본({canonical}영역)과 갈렸는데 그 사실이 문서에 없다 — "
                "다음 사람이 참인 쪽(코드)을 거짓으로 고친다"
            )
        else:
            assert _DISCLOSURE not in text, (
                f"{name}의 갈림 공개 문면이 낡았다 — 정본과 맞춰졌으니 그 줄을 지워라(자동 만료)"
            )
