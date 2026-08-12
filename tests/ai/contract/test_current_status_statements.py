"""문서의 **현재 상태 문면**이 코드와 맞는가 (지시서 73-R §7).

🔴 **문서 전체 grep은 하지 않는다.** 이 저장소의 문서는 **당시 기록을 일부러 보존**한다
(로그 43 · 99 ㊩ — 지우면 다음 사람이 *"원래 어느 쪽이었나"* 를 다시 판단한다). 그래서
`"미해소"`를 전문에서 찾으면 **정당한 과거 기록이 전부 red**가 되고, 그 검사는 곧
꺼진다.

⇒ **현재를 말하는 절만 잘라서** 본다:

| 자르는 곳 | 무엇을 말하는가 |
| --- | --- |
| 점검표 §「플립했다」의 실측 표 | **지금** 무엇이 해소됐는가 |
| 점검표의 「다음 순서」(⏭) 줄 | **지금** 다음에 할 일 |
| 99 #39 상태 칸 | 플립 안건의 **현재** 상태 |
| 99 ㉻ 상태 칸 | ㉻의 **현재** 상태 |

⚠ **상태 칸은 행의 마지막 `|` 구획**이다 — 본문(과거 기록)이 아니라 거기만 본다.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Final

import pytest

_DOCS: Final = Path(__file__).resolve().parents[3] / "docs"
_CHECKLIST: Final = _DOCS / "handoff" / "2026-08-08_store_backend_flip_checklist.md"
_OPEN_ITEMS: Final = _DOCS / "99_open_items.md"

#: 점검표에서 **현재 상태**를 말하는 절 — 이 제목 아래부터 다음 `## ` 전까지.
_CURRENT_SECTION: Final = "## 🔴 플립했다"

#: 「다음 순서」 **문단** — ⏭로 시작해 빈 줄까지다.
#: ⚠ 한 줄만 보면 여러 줄로 쓴 시제 표시를 놓친다(실측: 첫 판이 그래서 거짓 red였다).
_NEXT_STEP: Final = re.compile(r"^⏭ .*?(?=\n\n|\Z)", re.MULTILINE | re.DOTALL)


def _checklist_current_section() -> str:
    text = _CHECKLIST.read_text(encoding="utf-8")
    start = text.index(_CURRENT_SECTION)
    rest = text[start + len(_CURRENT_SECTION) :]
    end = rest.find("\n## ")
    return rest if end < 0 else rest[:end]


def _checklist_row(label: str) -> str:
    """현재 상태 표의 한 행 — **그 절 안에서만** 찾는다."""
    for line in _checklist_current_section().splitlines():
        if line.startswith("|") and label in line:
            return line
    raise AssertionError(f"현재 상태 표에 «{label}» 행이 없다 — 이 검사의 전제가 깨졌다")


def _status_cell(item: str) -> str:
    """99의 한 안건 행에서 **마지막 구획**(상태 칸)만 — 본문의 과거 기록은 안 본다."""
    for line in _OPEN_ITEMS.read_text(encoding="utf-8").splitlines():
        if line.startswith("|") and item in line[:400]:
            cells = [cell for cell in line.split("|") if cell.strip()]
            return cells[-1]
    raise AssertionError(f"99에 «{item}» 행이 없다 — 이 검사의 전제가 깨졌다")


# ───────────────────── 점검표: 현재 상태 표 ─────────────────────


def test_the_current_table_says_the_late_body_is_resolved() -> None:
    """🔴 **㉻는 지금 해소다** — 현재 표를 다시 «미해소»로 되돌리면 red."""
    row = _checklist_row("늦은 성공")
    assert "✅" in row, f"㉻ 현재 상태에 ✅가 없다: {row}"
    assert "미해소" not in row, f"㉻ 현재 상태에 «미해소»가 남았다: {row}"


def test_the_current_next_step_is_not_the_schema_decision_anymore() -> None:
    """🔴 **「다음은 ㉻ 스키마 결정」은 더 이상 현재형이 아니다.**

    ⚠ 문장을 지우라는 게 아니다 — **당시 기록으로 시제가 갈려 있어야** 한다.
    """
    for line in _NEXT_STEP.findall(_CHECKLIST.read_text(encoding="utf-8")):
        if "스키마 결정" not in line:
            continue
        assert "당시" in line, (
            f"「다음 순서」가 아직 현재형이다 — 당시 기록으로 갈라야 한다:\n  {line}"
        )
        assert "#218" in line, f"해소된 곳을 안 가리킨다:\n  {line}"


# ───────────────────── 99: 상태 칸 ─────────────────────


@pytest.mark.parametrize(
    ("item", "expected"),
    [
        #: 🔴 해소된 셋 — 상태 칸에 ✅가 있어야 한다.
        ("㉻ **뒤늦게 `succeeded`가 된 잡은", "✅"),
        ("**#46** **선등록한 AI_RUN을", "✅"),
    ],
)
def test_a_resolved_item_says_so_in_its_status_cell(item: str, expected: str) -> None:
    cell = _status_cell(item)
    assert expected in cell, f"«{item[:20]}…» 상태 칸: {cell}"


def test_the_flip_item_is_not_closed_because_retention_is_still_open() -> None:
    """🔴 **#39를 통째로 닫지 않는다** — ㉻ 범위는 닫혔지만 **보존 정책이 남았다.**

    ⚠ 전부 ✅로 바꾸면 **남은 축이 표에서 사라진다.** 그게 이 검사가 막는 것이다.
    """
    cell = _status_cell("**#39** **기본값을 `pg`로 뒤집었다")
    assert "☐" in cell, f"#39가 닫혔다 — 보존 정책이 아직 없다: {cell}"
    assert "보존 정책" in cell, f"#39 상태 칸이 남은 축을 안 적는다: {cell}"
    #: ⚠ 반대편 — ㉻ 범위가 닫힌 사실도 그 칸에 있어야 한다(«전부 미해소»로 읽히면 안 된다).
    assert "#218" in cell, f"#39 상태 칸이 ㉻ 범위 해소를 안 적는다: {cell}"


def test_the_shared_step_record_item_is_still_open() -> None:
    """⑱은 함께 닫지 않았다 — 같이 닫혔다고 적혔으면 red."""
    cell = _status_cell("⑱")
    assert "✅" not in cell, f"⑱이 닫혔다고 적혔다: {cell}"
