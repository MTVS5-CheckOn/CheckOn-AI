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
    row37 = next((r for r in rows if "#37" in r), None)
    assert row37 is not None and "✅" in row37, f"§4의 #37이 완료로 안 적혔다: {row37}"
    assert "㉾" in joined, "§4에 ㉾가 없다"


def test_the_verdict_followup_table_is_post_completion() -> None:
    """판정 문서 §5는 **완료 뒤 후속**이어야 한다 — 종전엔 「다음 단계 · B 대기」였다."""
    rows = _rows(_section(_VERDICT, "## 5. 완료 뒤 남은 후속", None))
    joined = "\n".join(rows)
    assert "#201" in joined and "#202" in joined, f"완료 근거 PR이 없다:\n{joined}"
    assert "#37" in joined, "§5에 #37이 없다"
    assert "㉾" in joined, "§5에 ㉾가 없다"


def test_the_closed_flip_gate_items_are_marked_resolved() -> None:
    """🔴 **플립 전 코드 관문 둘이 해소로 적혀 있어야 한다.**

    ⚠ **8/12 갱신** — 종전 이 검사는 *"#37은 미해소"* 를 단정했다. **그때는 참**이었고
    #37이 닫히면서 **검사 자신이 낡았다.** 상태를 박은 단정은 그 상태가 바뀌면 red가 된다 —
    ⇒ **지금 사실**로 옮긴다(가드를 지우지 않는다).
    """
    for marker in ("#36", "#37"):
        row = _item_row(marker)
        assert row.rstrip().endswith("|")
        assert "✅" in row.rsplit("|", 2)[1], (
            f"{marker} 상태 칸이 해소가 아니다: {row[-160:]}"
        )


def test_the_contract_promotion_item_stays_open() -> None:
    """🔴 **⑱은 안 닫는다** — #37 해소가 그것을 덮으면 안 된다.

    ⑱은 **두 `AgentStepRecord`를 공통 계약으로 승격할지**의 판정이고,
    #37은 **실제 PG 소비·배선**이었다. **다른 축이다.**
    """
    rows = [
        line
        for line in _OPEN_ITEMS.read_text(encoding="utf-8").splitlines()
        if line.startswith("| **⑱**") or line.startswith("| ⑱ ")
    ]
    assert len(rows) == 1, f"99에서 ⑱ 행이 {len(rows)}개다"
    assert "☐" in rows[0].rsplit("|", 2)[1] or "◐" in rows[0].rsplit("|", 2)[1], (
        f"⑱이 닫혔다: {rows[0][-160:]}"
    )


def test_the_probe_ledger_gap_is_closed_by_measurement() -> None:
    """㉾는 **8/12에 닫혔다** — 실 PG 실측으로.

    ⚠ **종전 이름은 `..._stays_open`이었다** — 그때는 *"#36 해소가 ㉾를 덮으면 안 된다"* 가
    지킬 값이었다(#36은 잡이 PG에 앉는 축이고 `record_run()` 0건은 그대로였다).
    그 결손이 실제로 없어졌으므로 **검사도 현재 사실로 옮긴다** — 낡은 가드를 남겨 두면
    다음 사람이 **사실이 아닌 것을 지키려고** 코드를 되돌린다.

    🔴 **닫힘 표시만 보지 않는다** — 무엇으로 닫았는지가 문면에 있어야 한다.
    """
    rows = [
        line
        for line in _OPEN_ITEMS.read_text(encoding="utf-8").splitlines()
        if line.startswith("| ㉾ ")
    ]
    assert len(rows) == 1, f"99에서 ㉾ 행이 {len(rows)}개다"
    status = rows[0].rsplit("|", 2)[1]
    assert "✅" in status, "㉾가 다시 열렸다 — 원장 배선이 되돌아갔는지 확인하라"
    assert "실 PG" in status, "닫은 근거(실측)가 상태 칸에 없다"


def test_the_step_record_duplication_stays_open() -> None:
    """🔴 **㉾가 닫혔다고 ⑱까지 닫히지 않는다** — 같은 회차에 섞이기 쉬운 자리다.

    ⑱은 `AgentStepRecord`·`AgentStepSink`가 두 capability에 **각자 정의**된 것이고,
    조사 축이 원장을 쓰게 된 것과 **다른 축**이다.
    """
    rows = [
        line
        for line in _OPEN_ITEMS.read_text(encoding="utf-8").splitlines()
        if line.startswith("| ⑱ ")
    ]
    assert len(rows) == 1, f"99에서 ⑱ 행이 {len(rows)}개다"
    assert "☐" in rows[0].rsplit("|", 2)[1], "⑱이 ㉾와 함께 닫혔다 — 별건이다"
