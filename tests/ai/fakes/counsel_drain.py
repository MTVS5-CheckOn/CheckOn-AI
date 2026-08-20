"""테스트에서 counsel 큐를 **한 회전** 돌린다 (№23 §A).

🔴 **테스트 전용 경로를 만들지 않는다.** 아래는 `runner.run_next(tenant_id=…)` 를 부르는
얇은 껍데기이고, 그 함수는 **프로덕션이 부르는 바로 그 함수**다:

  * `api/routers/counsel.py` 의 인라인 드레인 루프 — `await runner.run_next(tenant_id=…)`
  * `composition/counsel/drain.py` 의 배경 워커 — `run_job` 안에서 같은 호출

⚠ 다른 코드를 돌면 그 검사는 **프로덕션을 안 잰다**(결정 로그 127 · PR-07 이 배운 형태).

━━ 🔴 왜 이 함수가 이관을 「나눌 수 있게」 만드는가 (№23 §0-1) ━━

    K=1  POST 가 인라인으로 이미 끝냈다  → `drain_once` 는 돌 잡이 없어 **0** 을 돌려준다
    K=0  POST 는 `queued` 로 끝난다      → `drain_once` 가 그 잡을 **돌린다**

⇒ **양쪽에서 같은 결과**다. 그래서 이관을 K=1 인 채로 먼저 머지할 수 있고, `K=1→0` 은
마지막에 한 줄이 된다. 종전에는 K 를 먼저 내려야 해서 54 자리가 **한꺼번에** red 였다.
"""

from __future__ import annotations

import asyncio


async def adrain_once(tenant_id: str, *, rotations: int = 1) -> int:
    """이 테넌트의 큐를 `rotations` 회전 돌린다 — 돈 잡 수를 돌려준다.

    ⚠ 돌 잡이 없으면 **0** 이고 그것이 K=1 에서의 정상이다(no-op).
    """
    from ai.api.routers import counsel as router
    from ai.composition.counsel.assembly import open_counsel_pack_runner

    provider = router.require_counsel_provider()
    supervisor = router._build_supervisor()  # noqa: SLF001 — 라우터와 같은 인자로 만든다
    ran = 0
    async with open_counsel_pack_runner(
        supervisor=supervisor,
        context_store=router._context_store,  # noqa: SLF001
        step_sink=router._step_sink,  # noqa: SLF001
        draft_store=router._draft_store,  # noqa: SLF001
        pack_store=router._pack_store,  # noqa: SLF001
        planner=provider,
        writer=provider,
        regen_max=router._REGEN_MAX,  # noqa: SLF001
        lease_owner="counsel-test-drain",
        run_store=router._run_store,  # noqa: SLF001
    ) as runner:
        for _ in range(rotations):
            if await runner.run_next(tenant_id=tenant_id) is None:
                break
            ran += 1
    return ran


def drain_once(tenant_id: str, *, rotations: int = 1) -> int:
    """동기 테스트에서 부르는 형태 — 이 저장소의 `asyncio.run` 관례를 따른다."""
    return asyncio.run(adrain_once(tenant_id, rotations=rotations))
