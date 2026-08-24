"""B 소유 열 경로의 tag·operationId·summary를 고정한다."""

from typing import Any, Final

import pytest

from ai.api.app import create_app
from ai.api.routers.diagnosis_openapi import (
    DIAGNOSIS_OPERATION_ID,
    DIAGNOSIS_SUMMARY,
    DIAGNOSIS_TAG,
)
from ai.api.routers.problem_openapi import (
    PROBLEM_ITEM_OPERATION_ID,
    PROBLEM_ITEM_REVISION_OPERATION_ID,
    PROBLEM_ITEM_REVISION_SUMMARY,
    PROBLEM_ITEM_SUMMARY,
    PROBLEM_ITEMS_OPERATION_ID,
    PROBLEM_ITEMS_SUMMARY,
    PROBLEM_JOB_OPERATION_ID,
    PROBLEM_JOB_SUMMARY,
    PROBLEMS_OPERATION_ID,
    PROBLEMS_SUMMARY,
    PROBLEMS_TAG,
)
from ai.api.routers.report_openapi import (
    REPORT_BLOCK_OPERATION_ID,
    REPORT_BLOCK_RESTORE_OPERATION_ID,
    REPORT_BLOCK_RESTORE_SUMMARY,
    REPORT_BLOCK_SUMMARY,
    REPORT_OPERATION_ID,
    REPORT_SUMMARY,
    REPORTS_OPERATION_ID,
    REPORTS_SUMMARY,
    REPORTS_TAG,
)

_B_OWNED_ROUTES: Final = [
    (
        "/v1/diagnosis",
        "post",
        DIAGNOSIS_TAG,
        DIAGNOSIS_OPERATION_ID,
        DIAGNOSIS_SUMMARY,
        "createDiagnosis",
    ),
    (
        "/v1/problems",
        "post",
        PROBLEMS_TAG,
        PROBLEMS_OPERATION_ID,
        PROBLEMS_SUMMARY,
        "createProblemSet",
    ),
    (
        "/v1/problems/{job_id}",
        "get",
        PROBLEMS_TAG,
        PROBLEM_JOB_OPERATION_ID,
        PROBLEM_JOB_SUMMARY,
        "getProblemSetJob",
    ),
    (
        "/v1/problems/{set_id}/items",
        "get",
        PROBLEMS_TAG,
        PROBLEM_ITEMS_OPERATION_ID,
        PROBLEM_ITEMS_SUMMARY,
        "listProblemItems",
    ),
    (
        "/v1/problems/{set_id}/items/{slot_index}",
        "get",
        PROBLEMS_TAG,
        PROBLEM_ITEM_OPERATION_ID,
        PROBLEM_ITEM_SUMMARY,
        "getProblemItem",
    ),
    (
        "/v1/problems/{set_id}/items/{slot_index}/revisions",
        "post",
        PROBLEMS_TAG,
        PROBLEM_ITEM_REVISION_OPERATION_ID,
        PROBLEM_ITEM_REVISION_SUMMARY,
        "createProblemItemRevision",
    ),
    ("/v1/reports", "get", REPORTS_TAG, REPORTS_OPERATION_ID, REPORTS_SUMMARY, "listReports"),
    (
        "/v1/reports/{report_id}",
        "get",
        REPORTS_TAG,
        REPORT_OPERATION_ID,
        REPORT_SUMMARY,
        "getReport",
    ),
    (
        "/v1/reports/{report_id}/blocks/{block_id}",
        "patch",
        REPORTS_TAG,
        REPORT_BLOCK_OPERATION_ID,
        REPORT_BLOCK_SUMMARY,
        "updateReportBlock",
    ),
    (
        "/v1/reports/{report_id}/blocks/{block_id}/restore",
        "post",
        REPORTS_TAG,
        REPORT_BLOCK_RESTORE_OPERATION_ID,
        REPORT_BLOCK_RESTORE_SUMMARY,
        "restoreReportBlock",
    ),
]


def _spec() -> dict[str, Any]:
    return create_app().openapi()


def test_the_b_owned_route_list_has_exactly_ten_paths() -> None:
    assert len(_B_OWNED_ROUTES) == 10, _B_OWNED_ROUTES


@pytest.mark.parametrize(
    ("path", "method", "tag", "operation_id", "summary", "literal_id"),
    _B_OWNED_ROUTES,
    ids=[route[0] for route in _B_OWNED_ROUTES],
)
def test_b_owned_route_has_a_frozen_openapi_name(
    path: str,
    method: str,
    tag: str,
    operation_id: str,
    summary: str,
    literal_id: str,
) -> None:
    operation = _spec()["paths"][path][method]
    assert operation.get("tags") == [tag]
    assert operation.get("operationId") == operation_id
    assert operation["operationId"] == literal_id
    assert operation.get("summary") == summary
    assert summary.strip()
