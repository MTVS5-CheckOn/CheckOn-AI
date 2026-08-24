"""`/v1/reports*`의 OpenAPI 표기 상수 — 문서 전용."""

from typing import Final

REPORTS_TAG: Final = "리포트"

REPORTS_OPERATION_ID: Final = "listReports"
REPORTS_SUMMARY: Final = "리포트 목록을 조회한다"

REPORT_OPERATION_ID: Final = "getReport"
REPORT_SUMMARY: Final = "리포트 상세를 조회한다"

REPORT_BLOCK_OPERATION_ID: Final = "updateReportBlock"
REPORT_BLOCK_SUMMARY: Final = "리포트 블록의 강사 수정본을 저장한다"

REPORT_BLOCK_RESTORE_OPERATION_ID: Final = "restoreReportBlock"
REPORT_BLOCK_RESTORE_SUMMARY: Final = "리포트 블록의 AI 원문을 복원한다"

__all__ = [
    "REPORTS_OPERATION_ID",
    "REPORTS_SUMMARY",
    "REPORTS_TAG",
    "REPORT_BLOCK_OPERATION_ID",
    "REPORT_BLOCK_RESTORE_OPERATION_ID",
    "REPORT_BLOCK_RESTORE_SUMMARY",
    "REPORT_BLOCK_SUMMARY",
    "REPORT_OPERATION_ID",
    "REPORT_SUMMARY",
]
