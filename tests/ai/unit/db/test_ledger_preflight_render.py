"""preflight 리포트가 **네 축을 분리해 보여 주는가** (99 #36 G2).

🔴 **소스 문자열 grep이 아니라 렌더 결과를 본다** — #34가 그 미탐으로 새어 나갔다.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any, Final

import pytest

from ai.contracts.agents import JobPhase, WorkerKind
from ai.contracts.execution import Capability
from ai.db.ledger_completeness import (
    LedgerFinding,
    LedgerObservation,
    LedgerVerdict,
    judge_ledger_row,
)
from ai.evaluation.pg_ledger_preflight import preflight_blocks, render_report

_TENANT: Final = "t_render"
_RUN: Final = uuid.UUID("00000000-0000-0000-0000-0000000005a1")


def _finding(**over: Any) -> LedgerFinding:  # noqa: ANN401 — 픽스처 덮어쓰기
    base: dict[str, Any] = {
        "tenant_id": _TENANT,
        "job_id": uuid.uuid4(),
        "run_id": _RUN,
        "agent_kind": WorkerKind.COUNSEL_PACK,
        "status": JobPhase.SUCCEEDED,
        "started_at_is_set": True,
    }
    return judge_ledger_row(LedgerObservation(**(base | over)))


def _healthy() -> LedgerFinding:
    return _finding(
        ai_run_execution_id=_RUN,
        ai_run_tenant_id=_TENANT,
        ai_run_capability=Capability.COMPOSITION,
    )


def test_the_render_path_actually_runs() -> None:
    """절단 가드 — 렌더가 안 돌면 이 파일은 아무것도 안 본다."""
    assert "PG 원장 완전성 점검" in render_report([_healthy()], tenant_id=_TENANT)


def test_the_three_axes_are_printed_separately() -> None:
    """🔴 **violation · unknown · mapping_probe가 각각 따로** 나와야 한다.

    ⚠ 합치면 **어느 것이 확정 결함이고 어느 것이 증명 불가인지** 알 수 없다.
    """
    report = render_report(
        [
            _healthy(),
            _finding(status=JobPhase.SUCCEEDED),                      # violation
            _finding(status=JobPhase.FAILED),                         # unknown
            _finding(status=JobPhase.QUEUED),                         # allowed
        ],
        tenant_id=_TENANT,
    )
    for verdict in LedgerVerdict:
        assert verdict.value in report, f"{verdict.value} 축이 리포트에 없다"
    assert "## violation" in report
    assert "## unknown" in report


def test_zero_rows_is_not_reported_as_a_pass() -> None:
    """🔴 **관측 0건은 「통과」가 아니라 「측정 없음」이다.**"""
    report = render_report([], tenant_id=_TENANT)
    assert "0건" in report
    assert "「원장 완전성 통과」가 아니다" in report
    assert "✅ 플립 차단 사유 없음" not in report, "0건인데 통과로 읽힌다"


def test_a_clean_run_says_the_flip_is_not_blocked() -> None:
    report = render_report([_healthy()], tenant_id=_TENANT)
    assert "✅ 플립 차단 사유 없음" in report


@pytest.mark.parametrize(
    "case", [{"status": JobPhase.SUCCEEDED}, {"status": JobPhase.FAILED}]
)
def test_violation_or_unknown_blocks_the_flip_in_the_report(case: dict[str, Any]) -> None:
    """🔴 **unknown도 막는다** — 증명 못 한 것을 초록으로 세지 않는다."""
    report = render_report([_healthy(), _finding(**case)], tenant_id=_TENANT)
    assert "🔴 **플립 차단**" in report


def test_a_probe_without_a_ledger_now_blocks() -> None:
    """🔴 **㉾ 해소 뒤에는 조사 축도 관문을 막는다**(8/12).

    ⚠ 종전 이름은 `test_a_separate_gap_alone_does_not_block`이었고 *"㉾만 있으면 관문을
    막지 않는다"* 를 단정했다 — 그 판단은 **그 워커가 원장을 안 쓰던 동안** 옳았다.
    """
    report = render_report(
        [
            _healthy(),
            _finding(agent_kind=WorkerKind.MAPPING_PROBE, status=JobPhase.SUCCEEDED),
        ],
        tenant_id=_TENANT,
    )
    assert "🔴 **플립 차단**" in report
    assert "✅ 플립 차단 사유 없음" not in report


def test_the_report_no_longer_advertises_a_bypass() -> None:
    """🔴 리포트가 **「이건 안 막는다」**를 말하지 않는다 — 예외 통로가 없다."""
    report = render_report([_healthy()], tenant_id=_TENANT)
    assert "separate_gap" not in report


def test_the_report_carries_no_payload_text() -> None:
    """🔴 출력에 **개인정보·원 요청 본문**을 싣지 않는다 — 식별자는 UUID까지만."""
    report = render_report(
        [_finding(status=JobPhase.SUCCEEDED, result_ref="pack://abc", error_code="x")],
        tenant_id=_TENANT,
    )
    assert "pack://abc" not in report, "결과 참조 문자열이 그대로 나갔다"
    assert "payload" not in report


# ── (지시서 59) 리포트와 종료 코드가 **한 판정**을 공유하는가 ────


@pytest.mark.parametrize(
    ("findings_factory", "expect_block"),
    [
        pytest.param(lambda: [], True, id="0행 → 차단"),
        pytest.param(
            lambda: [_finding(status=JobPhase.FAILED)], True, id="unknown 1행 → 차단"
        ),
        pytest.param(
            lambda: [_finding(status=JobPhase.SUCCEEDED)], True, id="violation 1행 → 차단"
        ),
        pytest.param(
            lambda: [
                _healthy(),
                _finding(agent_kind=WorkerKind.MAPPING_PROBE, status=JobPhase.QUEUED),
            ],
            False,
            id="정상 + 조사 대기 → 통과",
        ),
        pytest.param(
            lambda: [
                _healthy(),
                _finding(
                    agent_kind=WorkerKind.MAPPING_PROBE, status=JobPhase.SUCCEEDED
                ),
            ],
            True,
            id="정상 + 원장 없는 조사 완주 → 차단(㉾ 해소 뒤)",
        ),
    ],
)
def test_the_report_and_the_exit_code_share_one_judgement(
    findings_factory: Callable[[], list[LedgerFinding]], expect_block: bool
) -> None:
    """🔴 **문면과 종료 코드가 갈리면 안 된다** — 각자 판정하면 언젠가 반대로 움직인다.

    ⚠ 소스 문자열이 아니라 **실제 함수 결과**를 본다.
    """
    findings = findings_factory()
    blocked = preflight_blocks(findings)
    report = render_report(findings, tenant_id=_TENANT)

    assert blocked is expect_block, "판정 함수가 기대와 다르다"
    assert ("🔴 **플립 차단**" in report) is blocked, (
        f"문면과 판정이 반대로 움직인다(blocked={blocked}):\n{report}"
    )
    assert ("✅ 플립 차단 사유 없음" in report) is (not blocked)
