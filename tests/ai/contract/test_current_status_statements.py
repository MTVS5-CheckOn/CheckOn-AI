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


# ───────── 브리핑 매트릭스 문서의 **현재 결과 표** (79-R3 §3) ─────────
#
# 🔴 **문서 전체에서 `14.999`를 금지하면 과거 반례 기록까지 잡는다** — 그 절은 과거형·해소
#    표시가 붙어 **보존 대상**이다(§1). 그래서 **현재 결과 표만** 잘라서 본다.

_MATRIX: Final = _DOCS / "handoff" / "2026-08-12_detect_briefing_scenario_matrix.md"

#: 현재 결과를 말하는 두 절 — 상단 상태표와 `## 3. S01~S22 결과`의 표.
_STATUS_TABLE_HEAD: Final = "| 구간 | 상태 |"
_RESULT_SECTION: Final = "## 3. S01~S22 결과"


def _matrix_text() -> str:
    return _MATRIX.read_text(encoding="utf-8")


def _section(heading: str) -> str:
    """그 제목 아래부터 다음 `## ` 전까지 — 다른 절의 과거 기록을 끌어오지 않는다."""
    text = _matrix_text()
    start = text.index(heading)
    rest = text[start + len(heading) :]
    end = rest.find("\n## ")
    return rest if end < 0 else rest[:end]


def _result_row(label: str) -> str:
    for line in _section(_RESULT_SECTION).splitlines():
        if line.startswith("|") and label in line:
            return line
    raise AssertionError(f"현재 결과 표에 «{label}» 행이 없다 — 이 검사의 전제가 깨졌다")


def test_the_s15_row_states_the_fixed_verdict_not_the_old_defect() -> None:
    """🔴 **S15 현재 행에 `14.999…pp 미발화`가 남으면 안 된다** — 그건 고친 결함이다.

    ⚠ 그 문장은 *"버그를 기대값으로 적은 것"* 이었다(#230). 현재 결과에 남기면 다음 사람이
    **없는 계약**을 믿는다.
    """
    row = _result_row("S15 R1 경계")
    assert "14.999" not in row, f"현재 결과에 옛 결함이 남았다: {row}"
    assert "정확한 15.0pp" in row and "전부 발화" in row, row
    assert "14.5pp" in row and "미발화" in row, "실제 미달 사례가 없다"
    assert "20.0pp" in row, "실제 초과 사례가 없다"


def test_the_status_table_pins_both_boundary_fix_prs() -> None:
    """🔴 R4·R1 교정이 **어느 PR인지** 현재 표에 적혀 있다."""
    table = _section(_STATUS_TABLE_HEAD)
    for label, pr in (("R4 정확 경계 교정", "#229"), ("R1 정확 경계 교정", "#230")):
        row = next(
            line for line in table.splitlines() if line.startswith("|") and label in line
        )
        assert "✅" in row, row
        assert pr in row, f"{label} 행에 {pr}이 없다: {row}"


#: ⚠ **종전 가드는 여기 있었다** — *"B단계 두 줄은 ☐여야 한다"*(79-R3).
#: 🔴 **B단계가 #231에서 완료되면서 그 가드가 「지난 상태」를 고정하게 됐다.** S15가 결함을
#: 계약으로 굳혔던 것과 같은 형태라, **현재 판정을 고정하는 가드로 교체**했다 —
#: 아래 `test_the_b_stage_status_row_states_the_current_verdict`가 그 자리다
#: (B단계 ✅ · 프롬프트 현행 유지 ✅ · **R3만 ☐**).


def test_the_past_defect_record_is_preserved() -> None:
    """🔴 **반대편** — 과거 결함 설명과 실제 계산값은 **지워지지 않았다**.

    ⚠ 위 검사만 두면 *"S15 행에서 `14.999`를 뺐다"* 를 「문서에서 지웠다」로도 만족시킬 수
    있다. 발견 기록이 사라지면 다음 사람이 *"R1은 왜 R4와 다르게 갔나"* 를 다시 판단한다.
    """
    text = _matrix_text()
    assert "14.999999999999991" in text, "R1 반례의 실제 계산값이 사라졌다"
    assert "5.000000000000004" in text, "R4 반례의 실제 계산값이 사라졌다"
    assert "결함을 계약으로 굳혀" in text, "초판 S15가 왜 틀렸는지가 사라졌다"


# ───────── B단계 기록의 **현재 표면** (지시서 81 §6) ─────────
#
# 🔴 **문서 전체에서 「Windows에서 실행」을 금지하지 않는다** — 과거 기록에는 정당하게
#    남는다(실행 전 계획을 보존한 절). 현재를 말하는 셋만 잘라서 본다:
#      ⓐ B단계 상태표 ⓑ 「현재 남은 작업」 절 ⓒ 원장·호출 범위 절.

_B_STATUS_HEAD: Final = "### B단계 (지시서 80"
_REMAINING_HEAD: Final = "### 현재 남은 작업"


def _matrix_block(heading: str, *, stop: str = "\n#") -> str:
    text = _matrix_text()
    start = text.index(heading)
    rest = text[start + len(heading) :]
    end = rest.find(stop)
    return rest if end < 0 else rest[:end]


def _b_status_row(label: str) -> str:
    for line in _matrix_block(_B_STATUS_HEAD).splitlines():
        if line.startswith("|") and label in line:
            return line
    raise AssertionError(f"B단계 상태표에 «{label}» 행이 없다 — 전제가 깨졌다")


@pytest.mark.parametrize(
    ("label", "mark"),
    [
        ("Windows 실제 OpenAI 실행", "✅"),
        ("프롬프트 고도화 판정", "✅"),
        ("R3 판정 근거 ↔ 브리핑 facts 정합", "☐"),
    ],
)
def test_the_b_stage_status_row_states_the_current_verdict(
    label: str, mark: str
) -> None:
    """🔴 B단계는 **완료**, 프롬프트는 **현행 유지**, R3만 **미해소**다."""
    row = _b_status_row(label)
    assert mark in row, row
    if mark == "☐":
        assert "✅" not in row, f"R3가 해소로 적혔다: {row}"


def test_the_remaining_work_does_not_ask_for_a_full_rerun() -> None:
    """🔴 **현재 남은 작업에 「Windows 전체 14건 재실행」이 없다.**

    ⚠ 남은 것은 **R3 두 사례**뿐이다 — 전체 재실행을 적으면 이미 끝난 일을 다시 시키고
    실 OpenAI 비용이 또 든다.
    """
    block = _matrix_block(_REMAINING_HEAD)
    assert "R3" in block, block
    for forbidden in ("14건 실행", "대표 14건", "전체 재실행"):
        assert forbidden not in block, f"현재 작업에 «{forbidden}»가 있다:\n{block}"


def test_the_past_windows_plan_is_marked_as_a_record() -> None:
    """⚠ 실행 전 지침은 **지우지 않고** 「당시 기록·완료된 지침」으로 가려져 있다."""
    text = _matrix_text()
    assert "## 8. B단계 실행 환경 판정 — **당시 기록**" in text
    assert "## 9. B단계 실행 전 순서 — **완료된 지침**" in text
    assert "이 순서는 #231에서 실행 완료됐다" in text
    #: 🔴 반대편 — 과거 판정 근거가 사라지지 않았다.
    assert "Missing scopes: api.model.read" in text, "당시 환경 판정 근거가 지워졌다"


def test_the_call_total_and_the_ledger_are_not_one_population() -> None:
    """🔴 **28회(실행기 카운터)와 원장 14행(한 논리 회차)을 같은 합계로 적지 않는다.**

    ⚠ 확인하지 못한 칸은 **0이 아니라 미확정**이다 — 0으로 쓰면 «중복 회차가 원장을 안
    남겼다»는 **없는 사실**이 된다.
    """
    text = _matrix_text()
    assert "채점에 사용한 논리 회차 1세트" in text
    assert "같은 모집단이 아니다" in text
    assert "미확정" in text, "확인 못 한 범위가 미확정으로 남지 않았다"
    assert "사후 확정 불가" in text
    #: 🔴 그 표에 `0`으로 채운 칸이 없어야 한다.
    for line in _matrix_block("| 범위 | `AI_RUN` |").splitlines():
        if line.startswith("|") and "중복 실행 회차" in line:
            assert "0" not in line.replace("`", ""), f"미확정을 0으로 적었다: {line}"
