"""`/v1/problems*`의 OpenAPI 표기 상수 — 문서 전용."""

from typing import Final

PROBLEMS_TAG: Final = "출제"

PROBLEMS_OPERATION_ID: Final = "createProblemSet"
PROBLEMS_SUMMARY: Final = "문제 세트 생성 작업을 요청한다"

PROBLEM_JOB_OPERATION_ID: Final = "getProblemSetJob"
PROBLEM_JOB_SUMMARY: Final = "문제 세트 생성 작업을 조회한다"

PROBLEM_ITEMS_OPERATION_ID: Final = "listProblemItems"
PROBLEM_ITEMS_SUMMARY: Final = "문제 세트의 문항을 조회한다"

PROBLEM_ITEM_OPERATION_ID: Final = "getProblemItem"
PROBLEM_ITEM_SUMMARY: Final = "문항 상세를 조회한다"

PROBLEM_ITEM_REVISION_OPERATION_ID: Final = "createProblemItemRevision"
PROBLEM_ITEM_REVISION_SUMMARY: Final = "문항 수정 리비전을 생성한다"

__all__ = [
    "PROBLEMS_OPERATION_ID",
    "PROBLEMS_SUMMARY",
    "PROBLEMS_TAG",
    "PROBLEM_ITEMS_OPERATION_ID",
    "PROBLEM_ITEMS_SUMMARY",
    "PROBLEM_ITEM_OPERATION_ID",
    "PROBLEM_ITEM_REVISION_OPERATION_ID",
    "PROBLEM_ITEM_REVISION_SUMMARY",
    "PROBLEM_ITEM_SUMMARY",
    "PROBLEM_JOB_OPERATION_ID",
    "PROBLEM_JOB_SUMMARY",
]
