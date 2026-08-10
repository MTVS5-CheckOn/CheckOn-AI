"""판정 문서의 생애주기 표가 **코드보다 좁지 않은가** (99 #36 · 준영님 지적 8/10).

🔴 **표가 코드보다 좁으면 다음 사람은 표를 읽고 「거기까지만 본다」고 믿는다.**
실제로 그랬다 — 표에 `leased`가 없었고 `cancelled` 두 갈래가 통째로 빠졌으며,
`failed` 무증거를 *"정상"* 이라 적었는데 **코드는 `unknown`**이었다.

⚠ **한 번 고치면 다음에 또 갈린다** — 상태가 늘거나 판정이 바뀌면 표만 낡는다.
⇒ **표를 코드에서 파생 검증**한다: `JobPhase` 전량과 판정값 전량이 표에 **실제로 적혀** 있는가.

🔴 **리터럴 목록을 이 파일에 안 박는다** — 박으면 코드가 바뀔 때 **표와 검사가 같이 낡아**
*"둘 다 틀린 채로 일치"* 를 통과시킨다. `JobPhase`·`LedgerVerdict`에서 **파생**한다.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Final

from ai.contracts.agents import JobPhase
from ai.db.ledger_completeness import LedgerVerdict

_DOC: Final = (
    Path(__file__).resolve().parents[3]
    / "docs"
    / "handoff"
    / "2026-08-10_pg_flip_agent_run_fk_order.md"
)
#: 생애주기 표가 사는 절 — 문서 전체를 훑으면 **다른 절의 언급**까지 세어 통과한다.
_SECTION: Final = "### 1-4-1."
_NEXT_SECTION: Final = "### 1-5."


def _lifecycle_section() -> str:
    text = _DOC.read_text(encoding="utf-8")
    assert _SECTION in text, f"생애주기 절을 못 찾았다 — 제목이 바뀌었나: {_SECTION}"
    body = text.split(_SECTION, 1)[1]
    return body.split(_NEXT_SECTION, 1)[0]


def _table_rows() -> list[str]:
    return [
        line
        for line in _lifecycle_section().splitlines()
        if line.startswith("|") and not re.match(r"^\|\s*-+", line)
    ]


def test_the_section_scan_finds_the_table() -> None:
    """🔴 절단 가드 — 표를 0행 찾으면 *"통과"* 가 아니라 **안 본 것**이다."""
    rows = _table_rows()
    assert len(rows) >= 5, f"생애주기 표를 못 읽었다({len(rows)}행) — 앵커가 틀렸나"


def test_every_job_phase_appears_in_the_table() -> None:
    """🔴 **`JobPhase` 전량이 「표 행」에 있어야 한다** — 하나라도 빠지면 안 적힌 것이다.

    ⚠ **절 전체를 훑으면 안 된다.** 실측으로 잡았다(8/10): 표에서 `leased` 행을 지웠는데
    **바로 위 정정 설명문의 「`leased`가 없었고…」가 대신 걸려** 검사가 통과했다.
    **산문이 표를 대신 통과시킨다** ⇒ **행만** 본다.
    """
    rows = "\n".join(_table_rows())
    missing = [phase.value for phase in JobPhase if f"`{phase.value}`" not in rows]
    assert not missing, (
        f"생애주기 표가 코드보다 좁다 — 안 적힌 상태: {missing}. "
        "표를 코드에 맞춘다(반대로 고치지 않는다)"
    )


def test_every_verdict_appears_in_the_table() -> None:
    """판정값 전량 — 표가 **어떤 결론이 나올 수 있는지**를 다 보여 줘야 한다.

    ⚠ 여기도 **행만** 본다 — 산문에 판정값 이름이 섞여 있다.
    """
    rows = "\n".join(_table_rows())
    missing = [verdict.value for verdict in LedgerVerdict if verdict.value not in rows]
    assert not missing, f"표에 안 적힌 판정값: {missing}"


def test_the_table_splits_cancelled_by_whether_it_started() -> None:
    """🔴 `cancelled`는 **한 줄로 못 적는다** — `started_at` 유무로 판정이 갈린다."""
    rows = [row for row in _table_rows() if "`cancelled`" in row]
    assert len(rows) >= 2, (
        f"cancelled가 {len(rows)}행이다 — 실행 전/후를 갈라 적어야 한다: {rows}"
    )


def test_the_table_carries_both_evidence_columns() -> None:
    """🔴 **호출 증거 유무가 축이다** — 한 열로 적으면 `paused`·`failed`가 뭉개진다."""
    header = next(row for row in _table_rows() if "상태" in row)
    assert header.count("증거") >= 2, f"증거 유무 두 열이 없다: {header}"
