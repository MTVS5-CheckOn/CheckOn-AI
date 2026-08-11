"""저장소 팩토리 — settings.store_backend로 InMemory↔PG 선택 (D-② 커밋⑤).

PG 저장소 생성은 엔진을 lazy로 만들 뿐 접속하지 않으므로 DB 없이 검증 가능하다.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from ai.agents.job_store import InMemoryJobStore
from ai.db.repositories.agent_job import PgJobStore
from ai.db.repositories.detection_store import (
    InMemoryDetectionStore,
    PgDetectionStore,
)
from ai.db.repositories.idempotency import (
    InMemoryIdempotencyStore,
    PgIdempotencyStore,
)
from ai.db.repositories.probe_stores import (
    PgAgentStepSink,
    PgProfileStore,
    PgSpecResultStore,
)
from ai.db.settings import DbSettings
from ai.db.store_factory import (
    build_agent_job_store,
    build_agent_step_sink,
    build_detection_store,
    build_idempotency_store,
    build_profile_store,
    build_spec_result_store,
)
from ai.import_mapping.probe.stores import (
    InMemoryAgentStepSink,
    InMemoryProfileStore,
    InMemorySpecResultStore,
)


def _settings(backend: str) -> DbSettings:
    """⚠ 인자는 `str`이다 — **잘못된 값이 거부되는지**를 재려면 타입으로 미리 막으면 안 된다."""
    return DbSettings(store_backend=backend)


def test_default_backend_is_memory() -> None:
    """기본값 memory — CI·데모는 DB 없이 돈다."""
    assert DbSettings().store_backend == "memory"


def test_memory_backend_builds_inmemory_stores() -> None:
    assert isinstance(build_agent_job_store(_settings("memory")), InMemoryJobStore)
    assert isinstance(build_idempotency_store(_settings("memory")), InMemoryIdempotencyStore)
    assert isinstance(build_detection_store(_settings("memory")), InMemoryDetectionStore)


def test_memory_backend_builds_inmemory_probe_stores() -> None:
    """mapping_probe 저장소 3종도 기본 memory — 워커 골격은 DB 없이 돈다(99 ⑮)."""
    assert isinstance(build_profile_store(_settings("memory")), InMemoryProfileStore)
    assert isinstance(build_spec_result_store(_settings("memory")), InMemorySpecResultStore)
    assert isinstance(build_agent_step_sink(_settings("memory")), InMemoryAgentStepSink)


def test_pg_backend_builds_pg_stores() -> None:
    """pg 선택 시 PG 구현 — 생성 시 접속하지 않는다(lazy engine)."""
    assert isinstance(build_agent_job_store(_settings("pg")), PgJobStore)
    assert isinstance(build_idempotency_store(_settings("pg")), PgIdempotencyStore)
    assert isinstance(build_detection_store(_settings("pg")), PgDetectionStore)


def test_pg_backend_builds_pg_probe_stores() -> None:
    """pg 선택 시 mapping_probe 저장소도 PG 구현(lazy engine — 접속 없음)."""
    assert isinstance(build_profile_store(_settings("pg")), PgProfileStore)
    assert isinstance(build_spec_result_store(_settings("pg")), PgSpecResultStore)
    assert isinstance(build_agent_step_sink(_settings("pg")), PgAgentStepSink)


def test_an_unknown_backend_is_refused_before_any_factory() -> None:
    """🔴 **알 수 없는 값은 기동 설정 오류다**(99 #38) — memory로 강등되지 않는다.

    ⚠ **이 검사는 뒤집힌 것이다.** 종전엔 *"알 수 없는 값은 안전하게 memory로 — 실 DB
    오적재보다 낫다"* 였는데, **PG 기본 플립 이후에는 그게 안전이 아니다.**
    `pgg` 하나로 **잡 원장·실행 원장·멱등 저장·읽기 모델·`AGENT_STEP` 영속성이 한꺼번에,
    조용히** 꺼진다 — 아무것도 안 터지고 다음 재기동에서 데이터가 없는 것으로만 안다.
    ⇒ **설정 경계**가 막으므로 어느 팩토리에도 그 값이 도달하지 않는다.
    """
    with pytest.raises(ValidationError):
        _settings("bogus")
