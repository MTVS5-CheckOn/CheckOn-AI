"""저장소 팩토리 — settings.store_backend로 InMemory↔PG를 고른다 (D-② 커밋⑤).

소유: 박진희 (db). database_url에 기본값이 있어 URL 유무로는 못 고르므로 명시 플래그로
고른다. 기본 "memory"라 CI·데모는 DB 없이 돌고, 실 DB 연동 시 .env에서 "pg"로 전환한다.
PG 저장소 생성은 엔진을 lazy로 만들 뿐 접속하지 않는다(session.py) — import 시 DB 불필요.
"""

from __future__ import annotations

from ai.agents.job_store import InMemoryJobStore, JobStore
from ai.db.repositories.agent_job import PgJobStore
from ai.db.repositories.detection_store import (
    DetectionStore,
    InMemoryDetectionStore,
    PgDetectionStore,
)
from ai.db.repositories.idempotency import (
    IdempotencyStore,
    InMemoryIdempotencyStore,
    PgIdempotencyStore,
)
from ai.db.repositories.inquiry_class_store import (
    InMemoryInquiryClassStore,
    InquiryClassStore,
    PgInquiryClassStore,
)
from ai.db.repositories.probe_stores import (
    PgAgentStepSink,
    PgProfileStore,
    PgSpecResultStore,
)
from ai.db.session import get_sessionmaker
from ai.db.settings import DbSettings, get_db_settings
from ai.import_mapping.probe.stores import (
    AgentStepSink,
    InMemoryAgentStepSink,
    InMemoryProfileStore,
    InMemorySpecResultStore,
    ProfileStore,
    SpecResultStore,
)

_PG = "pg"


def build_inquiry_class_store(
    settings: DbSettings | None = None,
) -> InquiryClassStore:
    """분류 평가셋 저장소 — 적재 실패는 **fail-closed**(inquiry_class_store.py 참조)."""
    settings = settings or get_db_settings()
    if settings.store_backend == _PG:
        return PgInquiryClassStore(sessionmaker=get_sessionmaker())
    return InMemoryInquiryClassStore()


def build_agent_job_store(settings: DbSettings | None = None) -> JobStore:
    """슈퍼바이저 실행 원장 — PG 선택 시 재시작·멀티워커 안전 저장소."""
    settings = settings or get_db_settings()
    if settings.store_backend == _PG:
        return PgJobStore(sessionmaker=get_sessionmaker())
    return InMemoryJobStore()


def build_idempotency_store(settings: DbSettings | None = None) -> IdempotencyStore:
    """멱등 캐시 저장소 — 실패는 fail-open(idempotency.py 참조)."""
    settings = settings or get_db_settings()
    if settings.store_backend == _PG:
        return PgIdempotencyStore(sessionmaker=get_sessionmaker())
    return InMemoryIdempotencyStore()


def build_detection_store(settings: DbSettings | None = None) -> DetectionStore:
    """감지 원장 저장소 — 실패는 fail-closed(detection_store.py 참조)."""
    settings = settings or get_db_settings()
    if settings.store_backend == _PG:
        return PgDetectionStore(sessionmaker=get_sessionmaker())
    return InMemoryDetectionStore()


def build_profile_store(settings: DbSettings | None = None) -> ProfileStore:
    """mapping_probe 입력(source_profile) 저장소 — PG 선택 시 워커·enqueue가 공유."""
    settings = settings or get_db_settings()
    if settings.store_backend == _PG:
        return PgProfileStore(sessionmaker=get_sessionmaker())
    return InMemoryProfileStore()


def build_spec_result_store(settings: DbSettings | None = None) -> SpecResultStore:
    """mapping_probe 산출(mapping_spec) 저장소 — result_ref로 참조되는 조사 결과."""
    settings = settings or get_db_settings()
    if settings.store_backend == _PG:
        return PgSpecResultStore(sessionmaker=get_sessionmaker())
    return InMemorySpecResultStore()


def build_agent_step_sink(settings: DbSettings | None = None) -> AgentStepSink:
    """mapping_probe 스텝(agent_step) 싱크 — 도구 호출 이력(마스킹 통과분)."""
    settings = settings or get_db_settings()
    if settings.store_backend == _PG:
        return PgAgentStepSink(sessionmaker=get_sessionmaker())
    return InMemoryAgentStepSink()
