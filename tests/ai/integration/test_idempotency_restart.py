"""멱등 재시작 시뮬레이션 — 영속 저장소가 프로세스 재시작을 넘겨 캐시를 지키는가 (D-② 커밋⑥).

99 안건 ⑨의 핵심: v0 인메모리는 재시작 시 소실(멀티워커 비공유)이라 같은 키가 재계산됐다.
영속 저장소(PG)는 재시작을 넘겨 저장분을 재반환해야 한다 — 이 계약을 DB 없이 검증한다.

"재시작"은 새 app 인스턴스(새 TestClient)로 흉내낸다:
- 저장소 인스턴스가 재시작을 **넘어 유지되면**(PG처럼) → 두 번째 프로세스가 캐시 재반환.
- 인메모리가 재시작에 **소실되면**(v0) → 두 번째 프로세스가 재계산(execution_id 달라짐).
같은 인메모리 구현으로 두 경우를 대비시켜, PG 저장소가 채우는 계약을 명확히 한다.
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from ai.api.app import create_app
from ai.api.routers.detect import (
    reset_detection_store,
    set_idempotency_store,
)
from ai.db.repositories.idempotency import InMemoryIdempotencyStore
from ai.evaluation.fake_snapshot import fixture_composite_risk, to_payload

_HEADERS = {
    "X-Tenant-Id": "teacher_alias_001",
    "X-Request-Id": "req-1",
    "Idempotency-Key": "teacher_alias_001:2026-07-13",
}


def _payload() -> dict[str, Any]:
    return to_payload(fixture_composite_risk())


def _execution_id(body: dict[str, Any]) -> str:
    execution_id = body["meta"]["execution_id"]
    assert isinstance(execution_id, str)
    return execution_id


def test_persistent_store_survives_restart() -> None:
    """저장소가 재시작을 넘어 유지되면 같은 키+바디는 재계산 없이 캐시 재반환."""
    store = InMemoryIdempotencyStore()  # 재시작을 넘어 유지되는 영속 백엔드 대역
    set_idempotency_store(store)
    reset_detection_store()

    payload = _payload()
    first = TestClient(create_app()).post("/v1/detect", json=payload, headers=_HEADERS)

    # ── 프로세스 재시작: 새 app, 그러나 같은 저장소 인스턴스가 살아 있다 ──
    set_idempotency_store(store)
    second = TestClient(create_app()).post("/v1/detect", json=payload, headers=_HEADERS)

    assert first.status_code == second.status_code == 200
    # 저장분 재반환 → execution_id까지 동일(재계산 아님).
    assert _execution_id(first.json()) == _execution_id(second.json())

    reset_detection_store()


def test_inmemory_restart_loses_cache_and_recomputes() -> None:
    """v0 인메모리는 재시작 시 소실 — 같은 키라도 재계산(execution_id 달라짐). ⑨가 푸는 문제."""
    set_idempotency_store(InMemoryIdempotencyStore())
    reset_detection_store()
    payload = _payload()
    first = TestClient(create_app()).post("/v1/detect", json=payload, headers=_HEADERS)

    # ── 재시작 = 빈 저장소로 교체(인메모리 소실) ──
    set_idempotency_store(InMemoryIdempotencyStore())
    second = TestClient(create_app()).post("/v1/detect", json=payload, headers=_HEADERS)

    assert first.status_code == second.status_code == 200
    assert _execution_id(first.json()) != _execution_id(second.json())  # 재계산

    reset_detection_store()
