"""실 PG 왕복 — 저장 계층 통합의 마지막 조각 (D-② 커밋⑥ · 99 안건 ⑫).

**테스트 DB 전략(D-② 확정):**
- 단위·계약: fake 저장소(InMemory) + 마이그레이션 offline SQL 왕복 — DB 없이 CI 통과.
- 통합(이 파일): 실 PG 왕복. sqlite는 쓰지 않는다(JSONB·Uuid·on_conflict 미지원 —
  프로덕션과 다른 DB로 통과시키면 거짓 초록). docker PG는 CI 여건 확인 후 도입(⑫).

그래서 이 테스트는 `integration` 마커로 기본 실행에서 제외된다(pyproject addopts
`-m 'not integration'`). 실 PG로 돌릴 때만 `store_backend=pg` + 마이그레이션 upgrade head
전제로 수행한다 — DB가 없으면 조용히 skip(거짓 실패 방지).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest

pytestmark = pytest.mark.integration


async def _can_connect() -> bool:
    from sqlalchemy import text

    from ai.db.session import get_sessionmaker

    try:
        async with get_sessionmaker()() as session:
            await session.execute(text("SELECT 1"))
        return True
    except Exception:  # noqa: BLE001 — 접속 불가면 통합 전제 미충족, skip 판정용
        return False


@pytest.fixture
def _require_pg() -> None:
    import asyncio

    if not asyncio.run(_can_connect()):
        pytest.skip("실 PG 미가용 — docker PG(99 ⑫) 필요")


def test_idempotency_roundtrip_persists(_require_pg: None) -> None:
    """put → get 왕복이 저장분을 재반환(스코프·TTL 창 안)."""
    import asyncio

    from ai.db.repositories.idempotency import PgIdempotencyStore
    from ai.db.session import get_sessionmaker

    store = PgIdempotencyStore(sessionmaker=get_sessionmaker())
    scope = {
        "tenant_id": f"t_{uuid.uuid4().hex[:8]}",
        "endpoint": "POST /v1/detect",
        "idempotency_key": "k1",
    }
    body: dict[str, Any] = {"data": {"signals": []}, "error": None, "meta": {}}

    async def scenario() -> None:
        await store.put(**scope, snapshot_hash="h1", response_body=body)
        hit = await store.get(**scope)
        assert hit is not None
        assert hit.snapshot_hash == "h1"
        assert hit.response_body == body

    asyncio.run(scenario())


def test_ledger_roundtrip_persists_ai_run(_require_pg: None) -> None:
    """원장 적재가 AI_RUN을 남긴다(fail-closed 경로의 정상 케이스)."""
    import asyncio

    from ai.contracts.execution import Capability, RunMetadata
    from ai.db.repositories.detection_store import LedgerWrite, PgDetectionStore
    from ai.db.session import get_sessionmaker

    store = PgDetectionStore(sessionmaker=get_sessionmaker())
    run = RunMetadata(
        execution_id=uuid.uuid4(),
        tenant_id=f"t_{uuid.uuid4().hex[:8]}",
        capability=Capability.DETECTION,
        pipeline_version="0.1.0",
        engine_version="detection-rules-0.1",
        schema_version="0.1",
        contract_version="0.1",
        input_snapshot_hash="hash-1",
        created_at=datetime.now(UTC),
    )
    asyncio.run(store.persist_ledger(LedgerWrite(run=run)))
