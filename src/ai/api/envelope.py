"""공통 응답 envelope 조립 — 04_api_contract.md §2.2.

소유: 공통 계약 (여러 라우터 공용 — A+B 확인 완료, 02_ownership §5 v3).

응답 형태(§2.2): {data, error, meta:{execution_id, versions}}.
versions 키는 VersionSet 필드에서 `_version` 접미사를 뗀 이름이다(pipeline·engine·…).
"""

from __future__ import annotations

from typing import Any

from ai.contracts.execution import VersionSet


def versions_dict(versions: VersionSet) -> dict[str, str | None]:
    """VersionSet → meta.versions dict (접미사 제거 — §2.2 키 표기)."""
    return {
        name.removesuffix("_version"): getattr(versions, name)
        for name in VersionSet.model_fields
    }


def success_envelope(
    data: dict[str, Any], execution_id: str, versions: VersionSet
) -> dict[str, Any]:
    """정상 응답 — data + meta."""
    return {
        "data": data,
        "error": None,
        "meta": {"execution_id": execution_id, "versions": versions_dict(versions)},
    }


def error_envelope(
    code: str,
    message: str,
    detail: object | None = None,
    versions: VersionSet | None = None,
) -> dict[str, Any]:
    """실패 응답 — error + meta.versions.

    04 §2.2 A판정(7/22): **실패에도 meta.versions는 항상 실린다**. 실행 전 오류(헤더
    누락 등)라 execution_id가 없으면 null로 두되, versions는 엔드포인트의 정적 버전으로
    채운다(호출자가 넘긴다). detail은 필드 경로 등 — 내부 상세는 싣지 않는다.
    """
    meta = (
        {"execution_id": None, "versions": versions_dict(versions)}
        if versions is not None
        else None
    )
    return {
        "data": None,
        "error": {"code": code, "message": message, "detail": detail},
        "meta": meta,
    }
