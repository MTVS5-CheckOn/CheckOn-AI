"""**현재 상태 표**가 실제 관문 상태를 말하는가 (99 #36 해소 · #37 신설 · 지시서 64-R §5).

🔴 **문서 전체를 grep하지 않는다.** 이 문서들은 **과거 기록을 일부러 보존**한다 —
「G1 대기」·「B 대기」 같은 문장이 **당시 기록으로** 남아 있고, 전체 grep은 그것을
**현재 표 대신 통과시킨다**(같은 형태를 이미 두 번 겪었다 · 로그 85 계열).
⇒ **「현재 상태 표」만 잘라서** 본다.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Final

_ROOT: Final = Path(__file__).resolve().parents[3]
_CHECKLIST: Final = _ROOT / "docs" / "handoff" / "2026-08-08_store_backend_flip_checklist.md"
_VERDICT: Final = _ROOT / "docs" / "handoff" / "2026-08-10_pg_flip_agent_run_fk_order.md"
_OPEN_ITEMS: Final = _ROOT / "docs" / "99_open_items.md"


def _section(path: Path, start: str, end: str | None) -> str:
    text = path.read_text(encoding="utf-8")
    assert start in text, f"{path.name}에서 절을 못 찾았다 — 제목이 바뀌었나: {start}"
    body = text.split(start, 1)[1]
    return body.split(end, 1)[0] if end and end in body else body


def _rows(section: str) -> list[str]:
    return [
        line
        for line in section.splitlines()
        if line.startswith("|") and not re.match(r"^\|\s*-+", line)
    ]


def _item_row(marker: str) -> str:
    rows = [
        line
        for line in _OPEN_ITEMS.read_text(encoding="utf-8").splitlines()
        if line.startswith(f"| **{marker}**")
    ]
    assert len(rows) == 1, f"99에서 {marker} 행이 {len(rows)}개다"
    return rows[0]


def test_the_scans_find_their_tables() -> None:
    """🔴 절단 가드 — 절을 못 읽으면 *"통과"* 가 아니라 **안 본 것**이다."""
    for name, section in (
        ("점검표 §0-1", _section(_CHECKLIST, "## 0-1.", "## 1. 점검표")),
        ("점검표 §4", _section(_CHECKLIST, "## 4. 플립 전에 A 축에서 남은 것", None)),
        ("판정 문서 §5", _section(_VERDICT, "## 5. 완료 뒤 남은 후속", None)),
    ):
        assert len(_rows(section)) >= 3, f"{name} 표를 못 읽었다"


def test_the_checklist_gate_table_says_g1_g2_g3_are_done() -> None:
    """🔴 **관문 셋이 완료로 적혀 있어야 한다** — 종전 표는 「B 대기」였다."""
    section = _section(_CHECKLIST, "## 0-1.", "## 1. 점검표")
    for gate in ("G1", "G2", "G3"):
        row = next((r for r in _rows(section) if f"**{gate}**" in r), None)
        assert row is not None, f"§0-1에 {gate} 행이 없다"
        assert "✅" in row, f"§0-1의 {gate}가 완료로 안 적혔다: {row}"


def test_the_checklist_remaining_table_is_current() -> None:
    """§4는 **지금 남은 것**을 말해야 한다 — 완료 셋 ✅ · #37과 ㉾는 미해소."""
    rows = _rows(_section(_CHECKLIST, "## 4. 플립 전에 A 축에서 남은 것", None))
    joined = "\n".join(rows)
    for gate in ("G1", "G2", "G3", "#36"):
        row = next((r for r in rows if gate in r), None)
        assert row is not None and "✅" in row, f"§4의 {gate}가 완료로 안 적혔다: {row}"
    assert "#37" in joined, "§4에 #37이 없다 — 지금 막는 것이 안 적혔다"
    assert "㉾" in joined, "§4에 ㉾가 없다"


def test_the_verdict_followup_table_is_post_completion() -> None:
    """판정 문서 §5는 **완료 뒤 후속**이어야 한다 — 종전엔 「다음 단계 · B 대기」였다."""
    rows = _rows(_section(_VERDICT, "## 5. 완료 뒤 남은 후속", None))
    joined = "\n".join(rows)
    assert "#201" in joined and "#202" in joined, f"완료 근거 PR이 없다:\n{joined}"
    assert "#37" in joined, "§5에 #37이 없다"
    assert "㉾" in joined, "§5에 ㉾가 없다"


def test_item_36_is_resolved_and_37_is_open() -> None:
    """🔴 **#36은 해소, #37은 미해소** — 한 줄에 둘을 뭉치지 않는다."""
    row36 = _item_row("#36")
    assert row36.rstrip().endswith("|")
    assert "✅" in row36.rsplit("|", 2)[1], f"#36 상태 칸이 해소가 아니다: {row36[-160:]}"
    row37 = _item_row("#37")
    assert "☐" in row37.rsplit("|", 2)[1], f"#37 상태 칸이 미해소가 아니다: {row37[-160:]}"


def test_the_probe_ledger_gap_stays_open() -> None:
    """㉾는 **닫지 않는다** — #36 해소가 그것을 덮으면 안 된다."""
    rows = [
        line
        for line in _OPEN_ITEMS.read_text(encoding="utf-8").splitlines()
        if line.startswith("| ㉾ ")
    ]
    assert len(rows) == 1, f"99에서 ㉾ 행이 {len(rows)}개다"
    assert "☐" in rows[0].rsplit("|", 2)[1], "㉾가 닫혔다"
