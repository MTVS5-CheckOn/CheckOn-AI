"""Import → mapping_probe 연결 — probing 기동 판정 시 WorkerJob enqueue (10_import_spec §6).

inference의 needs_probing이 참이고 필수 필드가 충족된 경우(blocked 아님), 프로파일을
ProfileStore에 넣어 payload_ref를 만들고 WorkerJob(mapping_probe.resolve)을 슈퍼바이저에
enqueue한다. 실행은 워커(MappingProbeRunner)가 별도로 lease해서 한다(비동기).

payload_ref는 불투명 URI(profile://…), payload_hash는 프로파일 내용의 sha256 — §5 계약대로
본문을 복제하지 않고 참조·해시만 경계를 넘는다.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import datetime
from uuid import UUID, uuid4

from ai.agents.supervisor import Supervisor, system_utc_now
from ai.contracts.agents import (
    OperationKind,
    PriorityClass,
    WorkerJob,
    WorkerKind,
    default_priority_for_operation,
)
from ai.import_mapping.probe.stores import ProfileRecord, ProfileStore, serialize_profile
from ai.import_mapping.profiling import SourceProfile


def _payload_hash(sheets: dict[str, object]) -> str:
    canonical = json.dumps(sheets, sort_keys=True, ensure_ascii=False, default=str)
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class ProbeEnqueuer:
    """needs_probing → WorkerJob enqueue. 프로파일 저장·잡 생성·큐 투입만(실행은 워커)."""

    def __init__(
        self,
        *,
        supervisor: Supervisor,
        profile_store: ProfileStore,
        new_id: Callable[[], UUID] = uuid4,
        now: Callable[[], datetime] = system_utc_now,
    ) -> None:
        self._sv = supervisor
        self._profiles = profile_store
        self._new_id = new_id
        self._now = now

    async def enqueue(
        self, *, tenant_id: str, profile: SourceProfile, file_hash: str
    ) -> WorkerJob:
        sheets = serialize_profile(profile)
        profile_ref = self._profiles.put(
            ProfileRecord(
                id=self._new_id(),
                tenant_id=tenant_id,
                file_hash=file_hash,
                filename=profile.filename,
                sheets=sheets,
                created_at=self._now(),
            )
        )
        operation = OperationKind.MAPPING_PROBE_RESOLVE
        job = WorkerJob(
            job_id=self._new_id(),
            execution_id=self._new_id(),
            tenant_id=tenant_id,
            worker_kind=WorkerKind.MAPPING_PROBE,
            operation=operation,
            payload_ref=profile_ref,
            payload_hash=_payload_hash(sheets),
            priority_class=default_priority_for_operation(operation),
            queued_at=self._now(),
        )
        return await self._sv.enqueue(job)


def make_probe_priority() -> PriorityClass:
    """mapping_probe.resolve 기본 우선순위(STANDARD) — 라우팅표 정합 확인용 헬퍼."""
    return default_priority_for_operation(OperationKind.MAPPING_PROBE_RESOLVE)
