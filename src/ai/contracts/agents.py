"""슈퍼바이저와 워커 사이의 공통 실행 계약.

슈퍼바이저는 요청을 결정론적으로 라우팅하고 실행 상태만 관리한다. 워커 산출물은
각 capability 저장소가 소유하며, 이 계약에는 불투명한 ``result_ref``만 기록한다.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Final, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

DEFAULT_MAX_RECOVERY_ATTEMPTS: Final = 3
"""lease 만료 회수 상한 — 모든 실행 루프에 상한을 둔다(CLAUDE.md 불변식 6)."""

WORKER_RECOVERY_EXHAUSTED: Final = "worker_recovery_exhausted"
"""lease 만료 회수 상한을 소진했을 때의 슈퍼바이저 오류 코드."""


class WorkerKind(StrEnum):
    """독립 state와 체크포인트를 소유하는 워커 종류."""

    COUNSEL_PACK = "counsel_pack"
    MAPPING_PROBE = "mapping_probe"
    PROBLEM_GENERATION = "problem_generation"


class OperationKind(StrEnum):
    """슈퍼바이저가 결정론적으로 라우팅하는 명령 종류."""

    COUNSEL_PACK_GENERATE = "counsel_pack.generate"
    MAPPING_PROBE_RESOLVE = "mapping_probe.resolve"
    PROBLEM_SET_GENERATE = "problem_set.generate"
    PROBLEM_ITEM_REFINE = "problem_item.refine"
    PROBLEM_ITEM_REVERIFY = "problem_item.reverify"


class JobPhase(StrEnum):
    """공통 실행 상태. 워커별 도메인 결과 상태와 섞지 않는다."""

    QUEUED = "queued"
    LEASED = "leased"
    RUNNING = "running"
    PAUSED = "paused"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class PriorityClass(StrEnum):
    """새 작업을 lease할 때 적용하는 고정 우선순위 등급."""

    BATCH = "batch"
    STANDARD = "standard"
    INTERACTIVE = "interactive"


_OPERATION_WORKER: Final[dict[OperationKind, WorkerKind]] = {
    OperationKind.COUNSEL_PACK_GENERATE: WorkerKind.COUNSEL_PACK,
    OperationKind.MAPPING_PROBE_RESOLVE: WorkerKind.MAPPING_PROBE,
    OperationKind.PROBLEM_SET_GENERATE: WorkerKind.PROBLEM_GENERATION,
    OperationKind.PROBLEM_ITEM_REFINE: WorkerKind.PROBLEM_GENERATION,
    OperationKind.PROBLEM_ITEM_REVERIFY: WorkerKind.PROBLEM_GENERATION,
}
OPERATION_WORKER: Final[Mapping[OperationKind, WorkerKind]] = MappingProxyType(
    _OPERATION_WORKER
)
"""operation → worker 고정 라우팅 표. 런타임 등록이나 LLM 선택을 허용하지 않는다."""

_DEFAULT_OPERATION_PRIORITY: Final[dict[OperationKind, PriorityClass]] = {
    OperationKind.COUNSEL_PACK_GENERATE: PriorityClass.BATCH,
    OperationKind.MAPPING_PROBE_RESOLVE: PriorityClass.STANDARD,
    OperationKind.PROBLEM_SET_GENERATE: PriorityClass.STANDARD,
    OperationKind.PROBLEM_ITEM_REFINE: PriorityClass.INTERACTIVE,
    OperationKind.PROBLEM_ITEM_REVERIFY: PriorityClass.INTERACTIVE,
}
DEFAULT_OPERATION_PRIORITY: Final[Mapping[OperationKind, PriorityClass]] = MappingProxyType(
    _DEFAULT_OPERATION_PRIORITY
)

_NORMAL_TRANSITIONS: Final[dict[JobPhase, frozenset[JobPhase]]] = {
    JobPhase.QUEUED: frozenset({JobPhase.LEASED, JobPhase.CANCELLED}),
    JobPhase.LEASED: frozenset({JobPhase.RUNNING, JobPhase.FAILED, JobPhase.CANCELLED}),
    JobPhase.RUNNING: frozenset(
        {JobPhase.PAUSED, JobPhase.SUCCEEDED, JobPhase.FAILED, JobPhase.CANCELLED}
    ),
    JobPhase.PAUSED: frozenset({JobPhase.CANCELLED}),
    JobPhase.SUCCEEDED: frozenset(),
    JobPhase.FAILED: frozenset(),
    JobPhase.CANCELLED: frozenset(),
}
NORMAL_TRANSITIONS: Final[Mapping[JobPhase, frozenset[JobPhase]]] = MappingProxyType(
    _NORMAL_TRANSITIONS
)
TERMINAL_PHASES: Final = frozenset(
    {JobPhase.SUCCEEDED, JobPhase.FAILED, JobPhase.CANCELLED}
)
ACTIVE_LEASE_PHASES: Final = frozenset({JobPhase.LEASED, JobPhase.RUNNING})


class InvalidJobTransition(ValueError):
    """허용되지 않은 실행 상태 전이."""


def worker_for_operation(operation: OperationKind) -> WorkerKind:
    """고정 라우팅 표로 operation의 유일한 워커를 반환한다."""

    return OPERATION_WORKER[operation]


def default_priority_for_operation(operation: OperationKind) -> PriorityClass:
    """operation별 기본 우선순위를 반환한다."""

    return DEFAULT_OPERATION_PRIORITY[operation]


class WorkerJob(BaseModel):
    """슈퍼바이저가 보관하는 불변 작업 스냅숏.

    payload와 결과 본문은 복제하지 않는다. ``payload_ref``·``result_ref``와 입력
    불변성을 확인하는 ``payload_hash``만 경계를 지난다.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    job_id: UUID
    execution_id: UUID
    tenant_id: str = Field(min_length=1)
    worker_kind: WorkerKind
    operation: OperationKind
    payload_ref: str = Field(min_length=1)
    payload_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    phase: JobPhase = JobPhase.QUEUED
    priority_class: PriorityClass
    dispatch_attempt: int = Field(default=0, ge=0)
    lease_generation: int = Field(default=0, ge=0)
    recovery_count: int = Field(default=0, ge=0)
    max_recovery_attempts: int = Field(default=DEFAULT_MAX_RECOVERY_ATTEMPTS, ge=1)
    lease_owner: str | None = Field(default=None, min_length=1)
    lease_acquired_at: datetime | None = None
    lease_expires_at: datetime | None = None
    checkpoint_ref: str | None = Field(default=None, min_length=1)
    result_ref: str | None = Field(default=None, min_length=1)
    error_code: str | None = Field(default=None, min_length=1)
    queued_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None

    @field_validator(
        "queued_at",
        "lease_acquired_at",
        "lease_expires_at",
        "started_at",
        "finished_at",
    )
    @classmethod
    def validate_aware_datetime(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("작업 시각은 timezone-aware datetime이어야 한다")
        return value

    @model_validator(mode="after")
    def validate_job(self) -> Self:
        if worker_for_operation(self.operation) is not self.worker_kind:
            raise ValueError("operation과 worker_kind의 고정 라우팅이 일치하지 않는다")
        if self.recovery_count > self.max_recovery_attempts:
            raise ValueError("recovery_count는 max_recovery_attempts를 초과할 수 없다")
        self._validate_lease_fields()
        self._validate_phase_fields()
        self._validate_timestamp_order()
        return self

    def _validate_lease_fields(self) -> None:
        lease_values = (self.lease_owner, self.lease_acquired_at, self.lease_expires_at)
        if self.phase in ACTIVE_LEASE_PHASES:
            if any(value is None for value in lease_values):
                raise ValueError("leased/running 작업에는 완전한 lease 정보가 필요하다")
            if self.lease_generation == 0:
                raise ValueError("활성 lease의 lease_generation은 1 이상이어야 한다")
            if self.lease_acquired_at is not None and self.lease_expires_at is not None:
                if self.lease_expires_at <= self.lease_acquired_at:
                    raise ValueError("lease_expires_at은 lease_acquired_at보다 뒤여야 한다")
        elif any(value is not None for value in lease_values):
            raise ValueError("활성 lease가 아닌 작업에는 lease 정보를 남길 수 없다")

    def _validate_phase_fields(self) -> None:
        if self.phase is JobPhase.QUEUED and self.dispatch_attempt == 0:
            if self.started_at is not None or self.checkpoint_ref is not None:
                raise ValueError("최초 queued 작업에는 시작·체크포인트 정보가 없어야 한다")
        if self.phase is JobPhase.RUNNING and (
            self.started_at is None or self.checkpoint_ref is None
        ):
            raise ValueError("running 작업에는 started_at과 checkpoint_ref가 필요하다")
        if self.phase is JobPhase.PAUSED and (
            self.started_at is None or self.checkpoint_ref is None
        ):
            raise ValueError("paused 작업에는 started_at과 checkpoint_ref가 필요하다")
        if self.phase in TERMINAL_PHASES and self.finished_at is None:
            raise ValueError("종단 작업에는 finished_at이 필요하다")
        if self.phase not in TERMINAL_PHASES and self.finished_at is not None:
            raise ValueError("비종단 작업에는 finished_at을 기록할 수 없다")
        if self.phase is JobPhase.SUCCEEDED and self.result_ref is None:
            raise ValueError("succeeded 작업에는 result_ref가 필요하다")
        if self.phase is JobPhase.SUCCEEDED and self.started_at is None:
            raise ValueError("succeeded 작업에는 started_at이 필요하다")
        if self.phase is JobPhase.FAILED and self.error_code is None:
            raise ValueError("failed 작업에는 error_code가 필요하다")
        if self.phase not in TERMINAL_PHASES and self.result_ref is not None:
            raise ValueError("비종단 작업에는 result_ref를 기록할 수 없다")
        if self.phase not in {JobPhase.FAILED, JobPhase.CANCELLED} and self.error_code:
            raise ValueError("error_code는 failed/cancelled 작업에만 기록할 수 있다")

    def _validate_timestamp_order(self) -> None:
        if self.started_at is not None and self.started_at < self.queued_at:
            raise ValueError("started_at은 queued_at보다 앞설 수 없다")
        if self.finished_at is not None and self.finished_at < self.queued_at:
            raise ValueError("finished_at은 queued_at보다 앞설 수 없다")
        if (
            self.started_at is not None
            and self.finished_at is not None
            and self.finished_at < self.started_at
        ):
            raise ValueError("finished_at은 started_at보다 앞설 수 없다")
        if self.lease_acquired_at is not None and self.lease_acquired_at < self.queued_at:
            raise ValueError("lease_acquired_at은 queued_at보다 앞설 수 없다")


def assert_job_transition(previous: WorkerJob, current: WorkerJob) -> None:
    """일반 상태 전이와 불변 명령 필드 보존을 검증한다."""

    _assert_same_command(previous, current)
    if current.phase not in NORMAL_TRANSITIONS[previous.phase]:
        raise InvalidJobTransition(f"{previous.phase.value} → {current.phase.value} 전이 불가")
    expected_increment = int(current.phase is JobPhase.LEASED)
    _assert_generation_delta(previous, current, expected_increment)
    _assert_recovery_count_unchanged(previous, current)
    _assert_preserved_started_at(previous, current)


def assert_expired_recovery(
    previous: WorkerJob,
    current: WorkerJob,
    *,
    recovered_at: datetime,
) -> None:
    """만료된 leased/running 작업의 전용 회수 전이를 검증한다."""

    _assert_same_command(previous, current)
    if previous.phase not in ACTIVE_LEASE_PHASES or previous.lease_expires_at is None:
        raise InvalidJobTransition("활성 lease 작업만 회수할 수 있다")
    if previous.lease_expires_at > recovered_at:
        raise InvalidJobTransition("만료되지 않은 lease는 회수할 수 없다")
    expected_phase = (
        JobPhase.FAILED
        if current.recovery_count >= previous.max_recovery_attempts
        else JobPhase.QUEUED
    )
    if current.phase is not expected_phase:
        raise InvalidJobTransition("회수 상한에 맞지 않는 phase 전이")
    _assert_generation_delta(previous, current, 0)
    if current.recovery_count != previous.recovery_count + 1:
        raise InvalidJobTransition("회수 시 recovery_count를 정확히 1 증가시켜야 한다")
    _assert_preserved_started_at(previous, current)
    if current.checkpoint_ref != previous.checkpoint_ref:
        raise InvalidJobTransition("회수 시 워커 checkpoint_ref를 보존해야 한다")
    if expected_phase is JobPhase.FAILED and current.error_code != WORKER_RECOVERY_EXHAUSTED:
        raise InvalidJobTransition("회수 상한 소진 오류 코드가 올바르지 않다")


def assert_paused_resume(previous: WorkerJob, current: WorkerJob) -> None:
    """명시적 resume 신호에 의한 paused → queued 전이를 검증한다."""

    _assert_same_command(previous, current)
    if previous.phase is not JobPhase.PAUSED or current.phase is not JobPhase.QUEUED:
        raise InvalidJobTransition("resume은 paused → queued 전이만 허용한다")
    _assert_generation_delta(previous, current, 0)
    _assert_preserved_started_at(previous, current)
    _assert_recovery_count_unchanged(previous, current)
    if current.checkpoint_ref != previous.checkpoint_ref:
        raise InvalidJobTransition("resume 시 checkpoint_ref를 보존해야 한다")


def _command_identity(job: WorkerJob) -> tuple[object, ...]:
    return (
        job.job_id,
        job.execution_id,
        job.tenant_id,
        job.worker_kind,
        job.operation,
        job.payload_ref,
        job.payload_hash,
        job.priority_class,
        job.max_recovery_attempts,
        job.queued_at,
    )


def _assert_same_command(previous: WorkerJob, current: WorkerJob) -> None:
    if _command_identity(previous) != _command_identity(current):
        raise InvalidJobTransition("상태 전이 중 불변 작업 명령 필드를 변경할 수 없다")


def _assert_generation_delta(
    previous: WorkerJob,
    current: WorkerJob,
    expected_increment: int,
) -> None:
    if current.lease_generation != previous.lease_generation + expected_increment:
        raise InvalidJobTransition("lease_generation 전이가 올바르지 않다")
    if current.dispatch_attempt != previous.dispatch_attempt + expected_increment:
        raise InvalidJobTransition("dispatch_attempt 전이가 올바르지 않다")


def _assert_preserved_started_at(previous: WorkerJob, current: WorkerJob) -> None:
    if previous.started_at is not None and current.started_at != previous.started_at:
        raise InvalidJobTransition("최초 started_at은 상태 전이 중 변경할 수 없다")


def _assert_recovery_count_unchanged(previous: WorkerJob, current: WorkerJob) -> None:
    if current.recovery_count != previous.recovery_count:
        raise InvalidJobTransition("일반 전이는 recovery_count를 변경할 수 없다")
