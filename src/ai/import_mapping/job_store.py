"""Import 작업 상태 저장소 + 원본 로더 — 비동기 작업의 상태·산출을 보관 (10_import_spec §1·§2).

v0는 프로세스 인메모리(재시작 소실). PG 영속은 후속(99 ⑨ — IMPORT_JOB 테이블). 라우터는
Protocol에만 의존한다(멱등·감지 저장소 선례와 동일). 원본 파일 fetch(SourceLoader)는
스토리지 규격이 [백엔드 확인 대기](§6.1)라 기본 구현을 두지 않고 주입한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ai.contracts.imports import ImportStatus, MappingPreview
from ai.import_mapping.profiling import SourceProfile


@dataclass
class ImportJob:
    """한 Import 작업의 상태 — 상태기계(§2)를 따라 갱신된다(가변)."""

    job_id: str
    tenant_id: str
    status: ImportStatus
    profile: SourceProfile | None = None
    preview: MappingPreview | None = None
    status_reason: str | None = None

    #: 🔴 **조사 워커의 실행 신원**(99 ㉾ · ㊯). probe를 띄운 경우에만 채워진다 —
    #: 그 값이 `AI_RUN.execution_id`(=PK)와 **같은 값**이라 응답의 `meta.execution_id`가
    #: 처음으로 **실재하는 원장 행**을 가리킨다.
    #: ⚠ **`None`이 거짓이 아니다** — `preview_ready`·`failed`처럼 조사를 안 띄운 경로는
    #:   가리킬 실행이 **없고**, 그때 응답은 `job_id`를 **상관 ID**로 싣는다(04 §2.2 부류).
    #: ⚠ **`str`이다** — `WorkerJob.execution_id`는 `UUID`지만 이 dataclass의 `job_id`가
    #:   `str`이고 응답 envelope도 `str`을 받는다. 한 자리에서만 변환한다.
    execution_id: str | None = None


class ImportJobStore(Protocol):
    """작업 저장소 인터페이스 — 라우터는 이 타입에만 의존한다."""

    def create(self, job: ImportJob) -> None: ...

    def get(self, job_id: str) -> ImportJob | None: ...

    def update(self, job: ImportJob) -> None: ...


class InMemoryImportJobStore:
    """프로세스 인메모리 구현 — 테스트·개발용. PG 영속은 후속(99 ⑨)."""

    def __init__(self) -> None:
        self._jobs: dict[str, ImportJob] = {}

    def create(self, job: ImportJob) -> None:
        self._jobs[job.job_id] = job

    def get(self, job_id: str) -> ImportJob | None:
        return self._jobs.get(job_id)

    def update(self, job: ImportJob) -> None:
        self._jobs[job.job_id] = job

    def clear(self) -> None:
        self._jobs.clear()


class SourceLoader(Protocol):
    """source_url → 파일 바이트. 스토리지 규격은 [백엔드 확인 대기](§6.1)."""

    def load(self, source_url: str) -> bytes: ...


class StubSourceLoader:
    """기본 로더 — 스토리지 fetch 미배선(후속). 실제 fetch는 인프라 확정 후 주입한다."""

    def load(self, source_url: str) -> bytes:
        raise NotImplementedError(
            "스토리지 fetch 후속 — Open-3 URL 형식 [백엔드 확인 대기](10_import_spec §6.1)"
        )
