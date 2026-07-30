"""mapping_probe 워커 조립 seam — 설정으로 저장소·체크포인터를 고른다 (99 ⑮ §4).

**조립부(composition)** — 순수 워커 로직(graph·planner·tools·state·stores Protocol)은
infra에 무의존이고, 구체 저장소(db)와 체크포인터(agents)를 여기서 한 번에 주입한다.
worker.py·enqueue.py가 이미 ai.agents를 소비하는 것과 같은 seam이다(캐패빌리티 코어는
여전히 db 무의존 — 이 모듈만 db.store_factory를 소비).

- `store_backend=memory`(기본·CI·데모): InMemory 저장소 3종 + InMemorySaver — DB 없이 동작.
- `store_backend=pg`: PG 저장소 3종 + AsyncPostgresSaver(agents/checkpointer).

PostgresSaver가 커넥션 컨텍스트 매니저라 이 조립도 async 컨텍스트 매니저다 — 체크포인터
수명을 러너 수명에 맞춘다. supervisor·lease_owner는 주입(워커 런타임 루프는 후속 — 여기선
주입 경로만 세운다). loop_max는 ImportSettings에서(하드코딩 금지).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from uuid import UUID, uuid4

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver

from ai.agents.checkpointer import open_checkpointer
from ai.agents.supervisor import Supervisor
from ai.db.settings import DbSettings, get_db_settings
from ai.db.store_factory import (
    build_agent_step_sink,
    build_profile_store,
    build_spec_result_store,
)
from ai.import_mapping.probe.worker import MappingProbeRunner
from ai.import_mapping.settings import ImportSettings, get_import_settings
from ai.runtime.tracing import require_tracing_disabled

_PG = "pg"  # store_factory._PG와 동일 스위치 — 저장소·체크포인터를 같은 플래그로 고른다.


@asynccontextmanager
async def _open_saver(settings: DbSettings) -> AsyncIterator[BaseCheckpointSaver]:  # type: ignore[type-arg]
    """store_backend에 맞춘 체크포인터. memory면 InMemorySaver, pg면 PostgresSaver 커넥션."""
    if settings.store_backend == _PG:
        async with open_checkpointer(settings) as saver:
            yield saver
    else:
        yield InMemorySaver()


@asynccontextmanager
async def open_mapping_probe_runner(
    *,
    supervisor: Supervisor,
    lease_owner: str,
    db_settings: DbSettings | None = None,
    import_settings: ImportSettings | None = None,
    new_id: Callable[[], UUID] = uuid4,
) -> AsyncIterator[MappingProbeRunner]:
    """설정에 맞춘 저장소·체크포인터를 주입한 러너를 연다(memory 기본, pg 전환)."""
    # ㉒-a fail-closed — counsel_pack과 같은 위험 표면이다(11 §3 — LangGraph 워커 둘).
    require_tracing_disabled("mapping_probe")

    resolved_db = db_settings or get_db_settings()
    resolved_import = import_settings or get_import_settings()
    async with _open_saver(resolved_db) as checkpointer:
        yield MappingProbeRunner(
            supervisor=supervisor,
            profile_store=build_profile_store(resolved_db),
            spec_store=build_spec_result_store(resolved_db),
            step_sink=build_agent_step_sink(resolved_db),
            checkpointer=checkpointer,
            loop_max=resolved_import.import_probe_loop_max,
            lease_owner=lease_owner,
            new_id=new_id,
        )
