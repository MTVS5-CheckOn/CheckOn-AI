"""Import 계약 모델 — 경계 필드 검증·extra 차단 (10_import_spec §1)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from ai.contracts.imports import (
    ConfirmRequest,
    ImportCreateRequest,
    ImportJobView,
    ImportStatus,
    MappingColumn,
    MappingPreview,
    SpecOverride,
)


def test_create_request_minimal() -> None:
    req = ImportCreateRequest(source_url="s3://bucket/f.xlsx", filename="f.xlsx")
    assert req.sheet_hint is None


def test_create_request_forbids_extra() -> None:
    with pytest.raises(ValidationError):
        ImportCreateRequest(source_url="u", filename="f", secret="x")  # type: ignore[call-arg]


def test_confidence_bounds() -> None:
    MappingColumn(source="점수A", target="score", confidence=0.41)
    with pytest.raises(ValidationError):
        MappingColumn(source="점수A", confidence=1.5)


def test_unmapped_column_target_null() -> None:
    col = MappingColumn(source="주소", target=None, unmapped_reason="개인정보 · 표준 목적지 없음")
    assert col.target is None and col.needs_review is False


def test_confirm_request_defaults_empty_overrides() -> None:
    assert ConfirmRequest().spec_overrides == ()
    ov = SpecOverride(source_column="점수B", target_field="score")
    assert ConfirmRequest(spec_overrides=(ov,)).spec_overrides[0].target_field == "score"


def test_preview_reports_unmapped_targets_without_blocking() -> None:
    """구 `test_preview_blocked_shape`의 자리 — 판정 축이 정보 축으로 바뀌었다.

    같은 관심사(무엇이 안 채워졌나)를 구조화 목록으로 검사한다(2026-07-30 백엔드 확정).
    """
    pv = MappingPreview(
        spec_version=1,
        reused=False,
        columns=(MappingColumn(source="이름", target="student_name", confidence=0.97),),
        unmapped_target_fields=("event_type", "occurred_at"),
        source_fingerprint="a" * 64,
    )
    assert pv.unmapped_target_fields == ("event_type", "occurred_at")
    assert pv.source_fingerprint == "a" * 64
    assert pv.sample_rows == ()
    assert not hasattr(pv, "blocked")


def test_preview_has_no_blocking_axis() -> None:
    """`blocked`·`blocked_reason`이 계약에서 사라졌다 — AI는 확정을 차단하지 않는다."""
    assert not {"blocked", "blocked_reason"} & set(MappingPreview.model_fields)


def test_job_view_status_enum() -> None:
    view = ImportJobView(job_id="j1", status=ImportStatus.PREVIEW_READY)
    assert view.status is ImportStatus.PREVIEW_READY
    assert view.mapping_preview is None


def test_job_view_has_no_transform_result() -> None:
    """전체 행 변환 결과는 AI 응답에 없다 — 백엔드 소유(10 §4, 2026-07-30)."""
    assert "result" not in ImportJobView.model_fields
    assert not {"output_url", "row_total", "row_ok", "row_errors"} & set(ImportJobView.model_fields)


def test_import_status_values_frozen() -> None:
    """10 §2 상태기계 — transforming·blocked 없음(둘 다 백엔드 소유로 이관)."""
    assert {status.value for status in ImportStatus} == {
        "profiling",
        "inferring",
        "probing",
        "preview_ready",
        "done",
        "failed",
    }
