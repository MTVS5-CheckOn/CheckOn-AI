"""리포트 공용 계약의 근거·리비전·표시 경계."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import pytest
from pydantic import ValidationError

from ai.contracts.report import (
    ReportArtifact,
    ReportBlock,
    ReportBlockKind,
    ReportBlockRevision,
    ReportEvidenceRef,
    ReportRevisionKind,
)

BLOCK_ID = UUID("00000000-0000-4000-8000-000000000201")
REPORT_ID = UUID("00000000-0000-4000-8000-000000000202")


def _evidence() -> ReportEvidenceRef:
    return ReportEvidenceRef(
        source_table="benchmarks",
        record_id="national-percentile-1",
        summary="전국 동일 학년 백분위 71",
    )


def _revision(
    *,
    ai_original: str = "전국 동일 학년 기준 백분위는 71입니다.",
    teacher_edit: str | None = None,
    gate_passed: bool = True,
    evidence: tuple[ReportEvidenceRef, ...] | None = None,
    numbers_used: tuple[int, ...] = (71,),
) -> ReportBlockRevision:
    return ReportBlockRevision(
        revision_no=0,
        revision_kind=ReportRevisionKind.AI_DRAFT,
        ai_original=ai_original,
        teacher_edit=teacher_edit,
        evidence=(_evidence(),) if evidence is None else evidence,
        numbers_used=numbers_used,
        gate_passed=gate_passed,
    )


def _block(*, revision: ReportBlockRevision | None = None) -> ReportBlock:
    return ReportBlock(
        block_id=BLOCK_ID,
        seq=0,
        block_type=ReportBlockKind.CHART_ANALYSIS,
        revisions=(revision or _revision(),),
        active_revision_no=0,
    )


def test_report_block_kind_is_closed_vocabulary() -> None:
    assert {kind.value for kind in ReportBlockKind} == {
        "greeting",
        "fact",
        "chart_analysis",
        "suggestion",
        "closing",
    }


def test_report_artifact_accepts_grounded_gate_passed_block() -> None:
    artifact = ReportArtifact(
        report_id=REPORT_ID,
        tenant_id="tenant-1",
        guardian_ref="guardian-1",
        blocks=(_block(),),
    )

    assert artifact.blocks[0].active_revision.display_content.endswith("71입니다.")


def test_revision_requires_evidence() -> None:
    with pytest.raises(ValidationError, match="evidence"):
        _revision(evidence=())


def test_contract_rejects_extra_fields() -> None:
    payload = _revision().model_dump()
    payload["unexpected"] = True
    with pytest.raises(ValidationError, match="unexpected"):
        ReportBlockRevision.model_validate(payload)


def test_revision_numbers_are_non_negative_and_unique() -> None:
    with pytest.raises(ValidationError, match="greater than or equal"):
        _revision(numbers_used=(-1,))
    with pytest.raises(ValidationError, match="중복"):
        _revision(numbers_used=(71, 71))


def test_revisions_must_be_contiguous_from_zero() -> None:
    revision = _revision().model_copy(update={"revision_no": 1})
    with pytest.raises(ValidationError, match="0부터"):
        ReportBlock(
            block_id=BLOCK_ID,
            seq=0,
            block_type=ReportBlockKind.FACT,
            revisions=(revision,),
            active_revision_no=1,
        )


def test_teacher_edit_preserves_ai_original_and_ai_rewrite_clears_edit() -> None:
    original = _block()
    edited = original.apply_teacher_edit(
        "전국 동일 학년 기준 백분위는 71이며 성장 흐름이 보입니다.",
        numbers_used=(71,),
    )
    rewritten = edited.apply_ai_rewrite(
        "전국 동일 학년 기준 백분위 71을 유지했습니다.",
        numbers_used=(71,),
    )

    assert edited.active_revision.ai_original == original.active_revision.ai_original
    assert edited.active_revision.teacher_edit is not None
    assert not edited.active_revision.gate_passed
    assert rewritten.active_revision.teacher_edit is None
    assert rewritten.revisions[1].teacher_edit == edited.active_revision.teacher_edit
    assert rewritten.active_revision.revision_no == 2


def test_restore_revision_selects_preserved_ai_original() -> None:
    edited = _block().apply_teacher_edit("강사 수정 문면입니다.", numbers_used=())

    restored_at = datetime(2026, 8, 23, 9, 30, tzinfo=UTC)
    restored = edited.restore_revision(
        0,
        restored_by="teacher-alias-1",
        restored_at=restored_at,
    )

    assert restored.active_revision_no == 2
    assert restored.active_revision.revision_kind is ReportRevisionKind.ROLLBACK
    assert restored.active_revision.revert_to_revision_no == 0
    assert restored.active_revision.teacher_edit is None
    assert not restored.active_revision.gate_passed
    assert restored.active_revision.restored_by == "teacher-alias-1"
    assert restored.active_revision.restored_at == restored_at
    assert restored.revisions[0].ai_original == _block().revisions[0].ai_original
    assert restored.ai_rewrite_warning == "AI로 다시 쓰면 그 범위의 강사 수정분은 덮입니다"

    restored_from_edit = edited.restore_revision(
        1,
        restored_by="teacher-alias-1",
        restored_at=restored_at,
    )
    assert restored_from_edit.active_revision.ai_original == edited.revisions[1].ai_original
    assert restored_from_edit.active_revision.teacher_edit is None
    with pytest.raises(ValueError, match="복귀 대상"):
        edited.restore_revision(
            3,
            restored_by="teacher-alias-1",
            restored_at=restored_at,
        )


def test_report_artifact_rejects_gate_failed_block() -> None:
    block = _block(revision=_revision(gate_passed=False))
    with pytest.raises(ValidationError, match="게이트"):
        ReportArtifact(
            report_id=REPORT_ID,
            tenant_id="tenant-1",
            guardian_ref="guardian-1",
            blocks=(block,),
        )
