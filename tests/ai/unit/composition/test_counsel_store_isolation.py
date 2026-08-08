"""팩 저장소가 **구현 층에서** 남의 테넌트 행을 거르는가 (99 #23).

⚠ **시그니처 검사와 다른 질문이다** — 인자를 받고도 안 쓰면 그쪽은 통과한다.
`CounselPackResultRecord`에 `tenant_id`가 **이미 있으므로**(9필드 중 하나) 거를 수
있는데 안 걸렀던 것이고, 같은 파일의 `InMemoryContextStore`·`InMemoryDraftResultStore`는
*"저장소 수준 격리"* 로 이미 거른다 — **셋 중 하나만 규율 밖에 있었다.**
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Coroutine
from datetime import UTC, datetime

from ai.composition.counsel.stores import (
    CounselPackResultRecord,
    InMemoryPackResultStore,
)
from ai.contracts.composition import DraftStatus, StudentResult


def _run[T](coro: Coroutine[object, object, T]) -> T:
    return asyncio.run(coro)


def _record(tenant_id: str) -> CounselPackResultRecord:
    return CounselPackResultRecord(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        class_ref="cl_a1",
        summary="1명 중 1명 생성",
        results=(
            StudentResult(
                student_ref="st_8f2a",
                draft_id=uuid.uuid4(),
                status=DraftStatus.GENERATED,
            ),
        ),
        created_at=datetime(2026, 8, 8, tzinfo=UTC),
    )


def test_pack_store_does_not_return_another_tenants_row() -> None:
    """🔴 **남의 테넌트 참조는 `None`이다.**

    ⚠ 선례 셋(`ContextStore`·`DraftResultStore`·pg의 `result_of`)이 전부 `None`이라
    **여기만 예외를 던지면 비대칭이 반대 방향으로 생긴다.**
    """
    store = InMemoryPackResultStore()
    ref = _run(store.put(_record("t-a")))

    assert _run(store.get(ref, tenant_id="t-b")) is None, (
        "남의 테넌트 행이 참조 하나로 보인다 — 참조는 테넌트를 담지 않으므로 "
        "저장소가 안 거르면 거를 자리가 없다"
    )
    assert _run(store.get(ref, tenant_id="t-a")) is not None, "제 테넌트 행까지 막았다"
