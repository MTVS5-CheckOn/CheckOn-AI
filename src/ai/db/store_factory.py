"""저장소 팩토리 — settings.store_backend로 InMemory↔PG를 고른다 (D-② 커밋⑤).

소유: 박진희 (db). database_url에 기본값이 있어 URL 유무로는 못 고르므로 명시 플래그로
고른다. 기본 "memory"라 CI·데모는 DB 없이 돌고, 실 DB 연동 시 .env에서 "pg"로 전환한다.
PG 저장소 생성은 엔진을 lazy로 만들 뿐 접속하지 않는다(session.py) — import 시 DB 불필요.
"""

from __future__ import annotations

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
from ai.db.session import get_sessionmaker
from ai.db.settings import DbSettings, get_db_settings

_PG = "pg"


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
