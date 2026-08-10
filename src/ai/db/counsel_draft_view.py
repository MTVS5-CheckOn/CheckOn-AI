"""상담 읽기 모델 스냅숏의 조회 투영.

이 모듈은 저장소가 아니다. `_view_cache`·`_drafts`를 PostgreSQL에 배선하지 않고, A가
후속 배선에서 사용할 순수 유도 함수만 제공한다. 호출자는 캐시 키와 두 정본 스냅숏을
넘기며 파생 컬럼 값을 따로 정할 수 없다.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Final
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from ai.contracts.agents import JobPhase
from ai.contracts.composition import DraftContext
from ai.contracts.counsel import Citation, CounselDraftJobView


class _CachedViewSnapshot(BaseModel):
    """`api.routers.counsel._CachedView`의 영속 표현."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    view: CounselDraftJobView
    execution_id: UUID | None
    correlation_id: UUID


class _DraftStateSnapshot(BaseModel):
    """`api.routers.counsel._DraftState`의 영속 표현."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    context: DraftContext
    citations: tuple[Citation, ...]
    text: str
    snapshot_hash: str
    emphasis: tuple[str, ...] = ()


#: ORM 컬럼 중 스냅숏 파생이 아닌 축. `updated_at`은 두 캐시에 없는 쓰기 시각이다.
NON_PROJECTED_COLUMNS: Final[frozenset[str]] = frozenset(
    {"id", "updated_at", "view_snapshot", "draft_snapshot"}
)


def counsel_draft_view_projection(
    key: tuple[str, str],
    *,
    view_snapshot: Mapping[str, object] | None,
    draft_snapshot: Mapping[str, object] | None,
) -> dict[str, Any]:
    """캐시 키와 두 정본 스냅숏에서 조회 투영 넷을 유도한다.

    `_DraftState`는 생성 결과 레코드가 있을 때만 만들어지므로 draft-only 행의 잡 상태는
    결정론적으로 `succeeded`다. 그 스냅숏에는 실행 원장 키가 없으므로 `execution_id`는
    null로 둔다. 없는 실행을 가리키는 UUID를 만들지 않는다.
    """

    tenant_id, job_id = key
    if not tenant_id or not job_id:
        raise ValueError("counsel_draft_view 캐시 키는 빈 문자열일 수 없다")

    parsed_view = (
        _CachedViewSnapshot.model_validate(view_snapshot)
        if view_snapshot is not None
        else None
    )
    if draft_snapshot is not None:
        _DraftStateSnapshot.model_validate(draft_snapshot)
    if parsed_view is None and draft_snapshot is None:
        raise ValueError("view_snapshot 또는 draft_snapshot 중 하나는 있어야 한다")

    if parsed_view is None:
        status = JobPhase.SUCCEEDED
        execution_id = None
    else:
        if parsed_view.view.job_id != job_id:
            raise ValueError("view_snapshot.job_id가 캐시 키와 다르다")
        status = JobPhase(parsed_view.view.status)
        execution_id = parsed_view.execution_id

    return {
        "tenant_id": tenant_id,
        "job_id": job_id,
        "status": status.value,
        "execution_id": execution_id,
    }


__all__ = ["NON_PROJECTED_COLUMNS", "counsel_draft_view_projection"]
