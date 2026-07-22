"""멱등 저장소 — 프로세스 인메모리(재시작 소실·멀티워커 비공유)를 영속화한다 (D-② 커밋④).

사양: docs/04_api_contract.md §2.3 · docs/06_erd.md IDEMPOTENCY_RECORD · 99 안건 ⑨.
소유: 박진희 (db.repositories). 인터페이스로 API 계층과 저장 계층을 분리한다.

규약(D-② 확정):
- 키 스코프 = (tenant_id, endpoint, idempotency_key) — DB 유니크 제약과 동일.
- 같은 키 + 같은 snapshot_hash → 저장된 response_body 재반환.
  같은 키 + 다른 hash → 호출자가 409(IdempotencyConflict). 여기선 저장분만 돌려주고
  판정은 호출자(라우터)가 한다.
- TTL 30일 — alert_context 창과 정합(새 숫자 발명 없이 기존 창 재사용). 읽기 시 창 밖은 미스.
- **캐시 저장·조회 실패는 fail-open** — 경고 로그만 남기고, 저장 실패는 삼키고 조회 실패는
  미스로 취급한다. 멱등 캐시는 best-effort이며 원장(AI_RUN·SIGNAL)과 달리 요청을 막지 않는다
  (원장은 fail-closed — 커밋⑤). 동시 삽입 경합은 DB 유니크 제약이 원자적으로 막는다.

시간·id는 주입한다(datetime.now()/전역 random 직접 호출 금지 — 03_coding_rules §3).
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ai.db.models import IdempotencyRecord

logger = logging.getLogger(__name__)

#: 기본 TTL — IDEMPOTENCY_RECORD 창(06_erd 라인 70). 생성자 인자로 덮어쓸 수 있다.
DEFAULT_TTL = timedelta(days=30)

#: 키 스코프 = 유니크 제약. 인메모리 dict 키로도 그대로 쓴다.
_Scope = tuple[str, str, str]


def system_utc_now() -> datetime:
    """주입용 기본 시계 — 이 한 곳만 벽시계를 읽는다(테스트는 가짜 시계 주입)."""
    return datetime.now(UTC)


class IdempotencyHit(BaseModel):
    """저장된 멱등 레코드의 판정 재료 — snapshot_hash 비교 + 재반환 본문."""

    model_config = ConfigDict(frozen=True)

    snapshot_hash: str = Field(min_length=1)
    response_body: dict[str, Any]


@runtime_checkable
class IdempotencyStore(Protocol):
    """멱등 저장소 인터페이스 — 라우터는 이 타입에만 의존한다."""

    async def get(
        self, *, tenant_id: str, endpoint: str, idempotency_key: str
    ) -> IdempotencyHit | None:
        """스코프 키로 조회. TTL 밖·미존재·조회 실패(fail-open)는 모두 None."""
        ...

    async def put(
        self,
        *,
        tenant_id: str,
        endpoint: str,
        idempotency_key: str,
        snapshot_hash: str,
        response_body: dict[str, Any],
    ) -> None:
        """스코프 키로 저장. 실패는 삼킨다(fail-open) — 경고 로그만."""
        ...


class InMemoryIdempotencyStore:
    """프로세스 인메모리 구현 — 테스트·개발용(재시작 소실). 라우터 기본값이기도 하다.

    TTL은 실제 PG 구현과 동일하게 주입 시계로 판정해, 오프라인 테스트에서 만료를
    결정론적으로 재현할 수 있다.
    """

    def __init__(
        self,
        *,
        clock: Callable[[], datetime] = system_utc_now,
        ttl: timedelta = DEFAULT_TTL,
    ) -> None:
        self._clock = clock
        self._ttl = ttl
        self._rows: dict[_Scope, tuple[str, dict[str, Any], datetime]] = {}

    async def get(
        self, *, tenant_id: str, endpoint: str, idempotency_key: str
    ) -> IdempotencyHit | None:
        row = self._rows.get((tenant_id, endpoint, idempotency_key))
        if row is None:
            return None
        snapshot_hash, response_body, created_at = row
        if created_at < self._clock() - self._ttl:
            return None  # TTL 밖 — 미스
        return IdempotencyHit(snapshot_hash=snapshot_hash, response_body=response_body)

    async def put(
        self,
        *,
        tenant_id: str,
        endpoint: str,
        idempotency_key: str,
        snapshot_hash: str,
        response_body: dict[str, Any],
    ) -> None:
        scope = (tenant_id, endpoint, idempotency_key)
        # 유니크 스코프 — 이미 있으면 덮어쓰지 않는다(최초 저장분 유지, PG 유니크와 정합).
        self._rows.setdefault(scope, (snapshot_hash, response_body, self._clock()))

    def clear(self) -> None:
        """테스트 간 격리용 — 프로세스 저장소를 비운다."""
        self._rows.clear()


class PgIdempotencyStore:
    """PostgreSQL 영속 구현 — 멀티워커·재시작 간 공유. 실 DB 연동 시 라우터에 주입한다.

    실 PG 왕복 테스트는 docker PG(99 안건 ⑫) 확정 후 integration 마커로 붙인다.
    이 커밋에서는 인터페이스·fail-open 경로를 단위로 검증한다.
    """

    def __init__(
        self,
        *,
        sessionmaker: async_sessionmaker[AsyncSession],
        clock: Callable[[], datetime] = system_utc_now,
        new_id: Callable[[], uuid.UUID] = uuid.uuid4,
        ttl: timedelta = DEFAULT_TTL,
    ) -> None:
        self._sessionmaker = sessionmaker
        self._clock = clock
        self._new_id = new_id
        self._ttl = ttl

    async def get(
        self, *, tenant_id: str, endpoint: str, idempotency_key: str
    ) -> IdempotencyHit | None:
        cutoff = self._clock() - self._ttl
        stmt = select(IdempotencyRecord).where(
            IdempotencyRecord.tenant_id == tenant_id,
            IdempotencyRecord.endpoint == endpoint,
            IdempotencyRecord.idempotency_key == idempotency_key,
            IdempotencyRecord.created_at >= cutoff,
        )
        try:
            async with self._sessionmaker() as session:
                record = (await session.execute(stmt)).scalars().first()
        except SQLAlchemyError:
            logger.warning(
                "멱등 조회 실패 — fail-open(미스로 처리) tenant=%s endpoint=%s",
                tenant_id,
                endpoint,
                exc_info=True,
            )
            return None
        if record is None:
            return None
        return IdempotencyHit(
            snapshot_hash=record.snapshot_hash, response_body=record.response_body
        )

    async def put(
        self,
        *,
        tenant_id: str,
        endpoint: str,
        idempotency_key: str,
        snapshot_hash: str,
        response_body: dict[str, Any],
    ) -> None:
        record = IdempotencyRecord(
            id=self._new_id(),
            tenant_id=tenant_id,
            endpoint=endpoint,
            idempotency_key=idempotency_key,
            snapshot_hash=snapshot_hash,
            response_body=response_body,
            created_at=self._clock(),
        )
        try:
            async with self._sessionmaker() as session:
                session.add(record)
                await session.commit()
        except SQLAlchemyError:
            # 유니크 경합(동시 최초 저장) 포함 — 캐시 저장 실패는 요청을 막지 않는다.
            logger.warning(
                "멱등 저장 실패 — fail-open(무시) tenant=%s endpoint=%s key=%s",
                tenant_id,
                endpoint,
                idempotency_key,
                exc_info=True,
            )
