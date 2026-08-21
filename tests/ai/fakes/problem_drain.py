"""테스트에서 PG 큐를 프로덕션과 같은 실행 경로로 유한하게 비운다."""

from __future__ import annotations

import asyncio


async def adrain_problem_once(tenant_id: str, *, rotations: int = 1) -> int:
    """라우터의 실제 ``_run_next_for_tenant``를 지정 횟수까지 실행한다."""

    from ai.api.routers import problem as router

    ran = 0
    for _ in range(rotations):
        if await router._run_next_for_tenant(tenant_id) is None:  # noqa: SLF001
            break
        ran += 1
    return ran


def drain_problem_once(tenant_id: str, *, rotations: int = 1) -> int:
    """동기 TestClient 검사에서 쓰는 PG 드레인 다리."""

    return asyncio.run(adrain_problem_once(tenant_id, rotations=rotations))


__all__ = ["adrain_problem_once", "drain_problem_once"]
