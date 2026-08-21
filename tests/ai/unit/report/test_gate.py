"""리포트 게이트가 기존 결정론 다섯 검사를 그대로 위임하는지 검증."""

from __future__ import annotations

from uuid import UUID

import pytest

from ai.composition.briefing_gate import check_brief_gate
from ai.contracts.report import (
    ReportBlock,
    ReportBlockKind,
    ReportBlockRevision,
    ReportEvidenceRef,
    ReportRevisionKind,
)
from ai.report.gate import check_report_block

BLOCK_ID = UUID("00000000-0000-4000-8000-000000000211")


def _block(text: str, *, numbers_used: tuple[int, ...] = ()) -> ReportBlock:
    evidence = ReportEvidenceRef(
        source_table="feature_week",
        record_id="feature-1",
        summary="리포트 블록 근거",
    )
    return ReportBlock(
        block_id=BLOCK_ID,
        seq=0,
        block_type=ReportBlockKind.FACT,
        revisions=(
            ReportBlockRevision(
                revision_no=0,
                revision_kind=ReportRevisionKind.AI_DRAFT,
                ai_original=text,
                evidence=(evidence,),
                numbers_used=numbers_used,
            ),
        ),
        active_revision_no=0,
    )


def _check(
    text: str,
    *,
    numbers_used: tuple[int, ...] = (),
    allowed_numbers: frozenset[str] = frozenset(),
    max_length: int = 80,
) -> tuple[bool, str]:
    result = check_report_block(
        _block(text, numbers_used=numbers_used),
        allowed_numbers,
        max_length=max_length,
        text_gate=check_brief_gate,
    )
    return result.passed, result.reason


def test_gate_passes_at_length_boundary_with_grounded_number() -> None:
    text = "3" + "가" * 9
    assert _check(
        text,
        numbers_used=(3,),
        allowed_numbers=frozenset({"3"}),
        max_length=10,
    ) == (True, "")


@pytest.mark.parametrize(
    "text, allowed_numbers, max_length, expected_reason",
    [
        ("⟪학생⟫은 성장 중입니다", frozenset(), 80, "token_leak"),
        ("학습*흐름입니다", frozenset(), 80, "symbol:*"),
        ("이 학생은 문제아 같아요", frozenset(), 80, "forbidden:문제아"),
        ("21일째 성장 중입니다", frozenset({"3"}), 80, "number_not_grounded:21"),
        ("가" * 11, frozenset(), 10, "too_long"),
    ],
)
def test_gate_delegates_five_failure_checks(
    text: str,
    allowed_numbers: frozenset[str],
    max_length: int,
    expected_reason: str,
) -> None:
    assert _check(
        text,
        numbers_used=(21,) if text.startswith("21") else (),
        allowed_numbers=allowed_numbers,
        max_length=max_length,
    ) == (False, expected_reason)


def test_gate_rejects_numbers_used_that_do_not_match_content() -> None:
    assert _check(
        "3주째 성장 중입니다",
        numbers_used=(),
        allowed_numbers=frozenset({"3"}),
    ) == (False, "numbers_used_mismatch")
