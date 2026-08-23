"""리포트 스튜디오 조회·강사 수정 HTTP 경계 — 결정론 조립, LLM·발송 없음.

이 라우터의 상세 응답 audience는 학부모(`guardian`)로 고정한다. 저장소에는 강사용
지표도 보존할 수 있지만 조립기에 audience를 명시해 응답 직렬화 전에 물리적으로 제거한다.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Final
from uuid import UUID

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ai.api.envelope import success_envelope
from ai.api.version_scope import RouterScope
from ai.contracts.execution import VersionSet
from ai.contracts.report import (
    ReportAudience,
    ReportBlock,
    ReportRevisionKind,
    ReportValueStatus,
)
from ai.report.assembler import assemble_report_studio_data
from ai.report.memory_store import InMemoryReportStore
from ai.report.root_cause import build_default_root_cause_metrics
from ai.report.store import (
    ReportBlockNotFound,
    ReportRevisionConflict,
    ReportStore,
    StoredReport,
)
from ai.report.vocabulary import default_report_unproduced_vocabulary
from ai.runtime.errors import DomainException, NotFound, SnapshotInvalid

router = APIRouter()

_PREFIX: Final = "/v1/reports"
_REQUIRED_HEADERS: Final = ("X-Tenant-Id", "X-Request-Id")
_VERSIONS: Final = VersionSet(
    pipeline_version="0.1.0",
    engine_version="report-0.1",
    schema_version="0.1",
    contract_version="0.1",
)
VERSION_SCOPE: Final = RouterScope(_PREFIX, lambda: _VERSIONS)


def _system_utc_now() -> datetime:
    return datetime.now(UTC)


_store: ReportStore = InMemoryReportStore()
_clock: Callable[[], datetime] = _system_utc_now


class ReportHttpRevisionConflict(DomainException):
    """강사 화면이 읽은 리비전보다 저장소 리비전이 앞선 경우."""

    code = "REVISION_CONFLICT"
    http_status = 409


class TeacherEditBody(BaseModel):
    """블록 한 개의 강사 수정 저장 요청."""

    model_config = ConfigDict(extra="forbid")

    base_revision_no: int = Field(ge=0)
    content: str = Field(min_length=1)


class RestoreBody(BaseModel):
    """보존된 AI 원문 리비전 복귀 요청."""

    model_config = ConfigDict(extra="forbid")

    base_revision_no: int = Field(ge=0)
    revert_to_revision_no: int = Field(ge=0)
    teacher_ref: str = Field(min_length=1)


def set_report_store(store: ReportStore) -> None:
    """기동 조립 또는 테스트가 저장 구현을 주입한다."""

    global _store
    _store = store


def reset_report_store() -> None:
    """기본 인메모리 저장소로 격리한다."""

    global _store
    _store = InMemoryReportStore()


def set_report_clock(clock: Callable[[], datetime]) -> None:
    """복귀 감사 시각 테스트용 주입 경계."""

    global _clock
    _clock = clock


def reset_report_clock() -> None:
    """UTC 시스템 시각으로 되돌린다."""

    global _clock
    _clock = _system_utc_now


def _headers(request: Request) -> tuple[str, str]:
    missing = [name for name in _REQUIRED_HEADERS if not request.headers.get(name)]
    if missing:
        raise SnapshotInvalid("필수 헤더 누락", {"missing_headers": missing})
    return request.headers["X-Tenant-Id"], request.headers["X-Request-Id"]


def _uuid(raw: str, *, field: str) -> UUID:
    try:
        return UUID(raw)
    except ValueError as exc:
        raise NotFound(f"{field} 부재", {field: raw}) from exc


async def _body[BodyT: BaseModel](request: Request, model: type[BodyT]) -> BodyT:
    try:
        raw = await request.json()
    except ValueError as exc:
        raise SnapshotInvalid("요청 바디가 유효한 JSON이 아님", str(exc)) from exc
    if not isinstance(raw, dict):
        raise SnapshotInvalid("요청 바디는 JSON 객체여야 한다")
    try:
        return model.model_validate(raw)
    except ValidationError as exc:
        detail = [
            {"field": ".".join(str(part) for part in error["loc"]), "type": error["type"]}
            for error in exc.errors()
        ]
        raise SnapshotInvalid("요청 바디 스키마 위반", detail) from exc


def _status(report: StoredReport) -> str:
    for block in report.blocks:
        active = block.active_revision
        if (
            active.revision_kind
            in {
                ReportRevisionKind.AI_DRAFT,
                ReportRevisionKind.AI_REWRITE,
            }
            and not active.gate_passed
        ):
            return "gate_rejected"
    return "ready"


def _summary(report: StoredReport) -> dict[str, Any]:
    return {
        "report_id": str(report.report_id),
        "guardian_ref": report.guardian_ref,
        "status": _status(report),
        "block_count": len(report.blocks),
        "updated_at": report.updated_at.isoformat(),
    }


def _detail(report: StoredReport) -> dict[str, Any]:
    source = report.source
    unproduced_vocabulary = default_report_unproduced_vocabulary()
    studio_data = assemble_report_studio_data(
        source.weakness_map,
        source.misconceptions,
        source.item_results,
        cell_min_items=source.cell_min_items,
        audience=ReportAudience.GUARDIAN,
        metrics=(*source.metrics, *build_default_root_cause_metrics(source.weakness_map)),
    )
    return {
        **_summary(report),
        "audience": ReportAudience.GUARDIAN.value,
        "created_at": report.created_at.isoformat(),
        "studio_data": studio_data.model_dump(mode="json"),
        "blocks": [block.model_dump(mode="json") for block in report.blocks],
        "unproduced_sections": [
            {
                "key": metric.value,
                "status": ReportValueStatus.NOT_PRODUCED.value,
                "reason": unproduced_vocabulary.reason_for(metric),
            }
            for metric in studio_data.unproduced
        ],
    }


def _success(data: dict[str, Any], request_id: str) -> dict[str, Any]:
    return success_envelope(data, request_id, _VERSIONS)


def _block(report: StoredReport, block_id: UUID) -> ReportBlock:
    block = next((item for item in report.blocks if item.block_id == block_id), None)
    if block is None:
        raise NotFound("block_id 부재", {"block_id": str(block_id)})
    return block


async def _report(tenant_id: str, report_id: UUID) -> StoredReport:
    report = await _store.get(tenant_id=tenant_id, report_id=report_id)
    if report is None:
        raise NotFound("report_id 부재", {"report_id": str(report_id)})
    return report


async def _save_block(
    *,
    report: StoredReport,
    block_id: UUID,
    base_revision_no: int,
    updated_block: ReportBlock,
    updated_at: datetime,
) -> StoredReport:
    try:
        updated = await _store.update_block(
            tenant_id=report.tenant_id,
            report_id=report.report_id,
            block_id=block_id,
            base_revision_no=base_revision_no,
            updated_block=updated_block,
            updated_at=updated_at,
        )
    except ReportRevisionConflict as exc:
        raise ReportHttpRevisionConflict(
            "리포트 블록 리비전 충돌",
            {
                "base_revision_no": base_revision_no,
                "current_revision_no": exc.current_revision_no,
            },
        ) from exc
    except ReportBlockNotFound as exc:
        raise NotFound("block_id 부재", {"block_id": str(block_id)}) from exc
    if updated is None:
        raise NotFound("report_id 부재", {"report_id": str(report.report_id)})
    return updated


@router.get(_PREFIX)
async def list_reports(request: Request) -> dict[str, Any]:
    """현재 tenant가 소유한 리포트 목록만 반환한다."""

    tenant_id, request_id = _headers(request)
    reports = await _store.list(tenant_id=tenant_id)
    return _success({"reports": [_summary(report) for report in reports]}, request_id)


@router.get(f"{_PREFIX}/{{report_id}}")
async def get_report(report_id: str, request: Request) -> dict[str, Any]:
    """guardian audience로 여섯 결정론 블록과 전체 본문 리비전을 반환한다."""

    tenant_id, request_id = _headers(request)
    report = await _report(tenant_id, _uuid(report_id, field="report_id"))
    return _success(_detail(report), request_id)


@router.patch(f"{_PREFIX}/{{report_id}}/blocks/{{block_id}}")
async def save_teacher_edit(
    report_id: str,
    block_id: str,
    request: Request,
) -> dict[str, Any]:
    """강사 수정본을 기존 AI 원문을 보존한 새 리비전으로 저장한다."""

    tenant_id, request_id = _headers(request)
    parsed_report_id = _uuid(report_id, field="report_id")
    parsed_block_id = _uuid(block_id, field="block_id")
    body = await _body(request, TeacherEditBody)
    report = await _report(tenant_id, parsed_report_id)
    current = _block(report, parsed_block_id)
    updated_block = current.apply_teacher_edit(body.content, numbers_used=())
    updated = await _save_block(
        report=report,
        block_id=parsed_block_id,
        base_revision_no=body.base_revision_no,
        updated_block=updated_block,
        updated_at=_clock(),
    )
    return _success(_detail(updated), request_id)


@router.post(f"{_PREFIX}/{{report_id}}/blocks/{{block_id}}/restore")
async def restore_ai_original(
    report_id: str,
    block_id: str,
    request: Request,
) -> dict[str, Any]:
    """선택한 과거 AI 원문을 감사 필드가 있는 rollback 리비전으로 복귀한다."""

    tenant_id, request_id = _headers(request)
    parsed_report_id = _uuid(report_id, field="report_id")
    parsed_block_id = _uuid(block_id, field="block_id")
    body = await _body(request, RestoreBody)
    report = await _report(tenant_id, parsed_report_id)
    current = _block(report, parsed_block_id)
    restored_at = _clock()
    try:
        updated_block = current.restore_revision(
            body.revert_to_revision_no,
            restored_by=body.teacher_ref,
            restored_at=restored_at,
        )
    except ValueError as exc:
        raise SnapshotInvalid(
            "복귀 대상 리비전이 없음",
            {"revert_to_revision_no": body.revert_to_revision_no},
        ) from exc
    updated = await _save_block(
        report=report,
        block_id=parsed_block_id,
        base_revision_no=body.base_revision_no,
        updated_block=updated_block,
        updated_at=restored_at,
    )
    return _success(_detail(updated), request_id)


__all__ = [
    "VERSION_SCOPE",
    "reset_report_clock",
    "reset_report_store",
    "router",
    "set_report_clock",
    "set_report_store",
]
