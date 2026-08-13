"""Kafka 이벤트 ↔ 내부 계약의 **순수 변환 참고 구현** — 우리 런타임 경로가 아니다.

소유: 염준영 (problem_generation 인프라)

🔴 **2026-08-12 재분류.** 백엔드가 *"AI는 HTTP만 제공하고 별도 Kafka-HTTP adapter가
Kafka를 전담한다"* 로 확정했다(`PROBLEM_STUDIO_AI_TEAM_HANDOFF.md` §2). 따라서 이 모듈은
**AI가 실행 중 부르는 코드가 아니라**, adapter가 만들어야 하는 변환을 **우리 계약으로
검증해 두는 참고 구현**이다. 백엔드도 *"Kafka fixture는 폐기하지 않고 adapter 참고 계약으로
쓴다"* 고 회신했다.

**그래도 여기 두는 이유:** ⓐ 백엔드 요청 이벤트가 우리 `ProblemRequest`로 **실제로 매핑되는지**
CI가 잡아 준다(필드 이름이 갈리는 자리가 있다) ⓑ 우리 잡 상태 어휘가 adapter가 발행할
`worker_job.*`와 어긋나면 그것도 CI가 잡는다. 계약 대조가 목적이지 실행이 목적이 아니다.

⚠ `aiokafka` import가 없고 I/O도 시계도 없다 — `CLAUDE.md` §3이 막은 실연동에 해당하지
않는다.

🔴 **이 파일이 있는 이유는 이름이 갈리기 때문이다.** 백엔드는 `problem_request_id`를
보내는데 우리 계약의 필드는 `request_id`이고, `ProblemRequest`는 `extra="forbid"`라
그대로 넣으면 검증에서 죽는다. 그 매핑을 컨슈머 안에 인라인으로 적으면 계약 대조
테스트가 붙을 자리가 없다.

⚠ **본문(발문·선지·해설)은 이벤트에 싣지 않는다** — `docs/08_kafka_events.md` §4·§5의
규약이다. 백엔드는 `GET /v1/problems/{job_id}/items`로 본문을 읽는다.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Final, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ai.contracts.agents import JobPhase, WorkerJob, WorkerKind
from ai.contracts.problem_generation import (
    ProblemGenerationOutcome,
    ProblemRequest,
    ProblemSetResult,
)

#: 백엔드 → AI 요청 이벤트의 고정 축 (BE 명세 §5.2·§5.3).
PG_REQUEST_EVENT_TYPE: Final = "problem_generation.requested"
PG_REQUEST_SCHEMA_VERSION: Final = "pg-request-1"

#: AI → 백엔드 결과 이벤트의 고정 축. 🔴 **공용 워커 이벤트 이름을 쓴다**(08 §4) —
#: 백엔드가 허용하는 `problem_generation.*` 별칭은 쓰지 않는다. 워커가 셋인데 M2만
#: 다른 이름을 내보내면 컨슈머가 워커마다 갈린다.
WORKER_JOB_SCHEMA_VERSION: Final = "worker-job-1"
WORKER_JOB_EVENT_TYPES: Final = {
    JobPhase.SUCCEEDED: "worker_job.succeeded",
    JobPhase.FAILED: "worker_job.failed",
    JobPhase.CANCELLED: "worker_job.cancelled",
}
WORKER_JOB_PROGRESS_EVENT_TYPE: Final = "worker_job.progress"

#: alias 형식 (BE 명세 §11). 🔴 **정규식으로 못 박는 이유**: 형식이 틀린 tenant alias는
#: 백엔드가 계약 위반으로 DLT에 격리한다 — 우리 쪽에서 먼저 걸러야 왕복이 줄어든다.
_TENANT_ALIAS: Final = r"^tn_[0-9a-f]{32}$"
_TARGET_ALIAS: Final = r"^(st|cl)_[0-9a-f]{32}$"
_PG_IDEMPOTENCY_KEY: Final = r"^pg_[0-9a-f]{32}$"
_SHA256: Final = r"^sha256:[0-9a-f]{64}$"

_TENANT_KEYED_TARGET: Final = {"student": "st_", "class": "cl_"}


class ProblemRequestedPayload(BaseModel):
    """요청 이벤트의 `payload` — 백엔드가 소유한 형태 그대로 받는다."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    problem_request_id: UUID
    idempotency_key: Annotated[str, Field(pattern=_PG_IDEMPOTENCY_KEY)]
    request: dict[str, Any]


class ProblemRequestedEvent(BaseModel):
    """`problem_generation.requested` envelope (BE 명세 §5.3).

    ⚠ `extra="forbid"`가 아니다 — 백엔드가 envelope에 필드를 더해도 우리 컨슈머가 멈추면
    안 된다(하위호환). 대신 **우리가 읽는 필드는 전부 엄격히 검증**한다.
    """

    model_config = ConfigDict(frozen=True)

    event_id: UUID
    event_type: Literal["problem_generation.requested"]
    occurred_at: datetime
    tenant_id: Annotated[str, Field(pattern=_TENANT_ALIAS)]
    schema_version: str = Field(min_length=1)
    correlation_id: UUID | None = None
    payload: ProblemRequestedPayload

    @model_validator(mode="after")
    def validate_correlation(self) -> Self:
        if (
            self.correlation_id is not None
            and self.correlation_id != self.payload.problem_request_id
        ):
            raise ValueError(
                "correlation_id와 payload.problem_request_id가 다르다 — "
                "둘 중 어느 쪽이 백엔드 요청인지 정할 수 없다"
            )
        return self

    def to_problem_request(self) -> ProblemRequest:
        """AI 내부 command로 옮긴다 — **이름이 갈리는 세 축만** 여기서 채운다.

        🔴 `request_id`에 `problem_request_id`를 넣는다. 백엔드 요청 1건과 AI 요청 1건이
        1:1이므로 상관관계를 잃지 않는 유일한 값이다.
        🔴 `tenant_id`·`idempotency_key`는 **envelope·payload에서** 온다 — 백엔드가
        `request` 안에 같은 값을 또 넣어도 그쪽을 신뢰하지 않는다(두 값이 갈리면
        envelope이 정본이다. Kafka key가 envelope의 tenant이기 때문이다).
        """

        body = dict(self.payload.request)
        for header_derived in ("tenant_id", "request_id", "idempotency_key"):
            body.pop(header_derived, None)
        return ProblemRequest.model_validate(
            {
                **body,
                "request_id": str(self.payload.problem_request_id),
                "idempotency_key": self.payload.idempotency_key,
                "tenant_id": self.tenant_id,
            }
        )


def validate_target_alias(request: ProblemRequest) -> None:
    """`target_ref`가 대상 종류에 맞는 alias인지 본다 — 순수 판정.

    ⚠ `ProblemRequest`에 넣지 않았다. alias 형식은 **Kafka 경계의 사실**이고, REST 경로의
    테스트 픽스처는 `student-A` 같은 값을 쓴다(계약에 넣으면 그쪽이 전부 깨진다).
    """

    expected = _TENANT_KEYED_TARGET[request.target_kind.value]
    if not request.target_ref.startswith(expected):
        raise ValueError(
            f"{request.target_kind.value} 대상의 alias 접두는 {expected!r}여야 한다: "
            f"{request.target_ref!r}"
        )


def _result_status(outcome: ProblemGenerationOutcome | None) -> str | None:
    if outcome is None:
        return None
    if isinstance(outcome, ProblemSetResult):
        return outcome.status.value
    return outcome.status


def _set_id(outcome: ProblemGenerationOutcome | None) -> str | None:
    if isinstance(outcome, ProblemSetResult):
        return str(outcome.set_id)
    return None


def _envelope(
    *,
    event_id: UUID,
    event_type: str,
    occurred_at: datetime,
    tenant_id: str,
    problem_request_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    return {
        "event_id": str(event_id),
        "event_type": event_type,
        "occurred_at": occurred_at.isoformat(),
        "tenant_id": tenant_id,
        "schema_version": WORKER_JOB_SCHEMA_VERSION,
        # 🔴 `correlation_id`와 `payload.problem_request_id`를 **둘 다** 싣는다 —
        #    백엔드가 어느 쪽을 읽든 같은 값이 되게(BE 명세 §6.2 권장).
        "correlation_id": problem_request_id,
        "payload": {
            "worker_kind": WorkerKind.PROBLEM_GENERATION.value,
            "problem_request_id": problem_request_id,
            **payload,
        },
    }


def worker_job_terminal_event(
    job: WorkerJob,
    *,
    problem_request_id: str,
    outcome: ProblemGenerationOutcome | None,
    event_id: UUID,
    occurred_at: datetime,
) -> dict[str, Any]:
    """종단 잡 1건의 결과 이벤트.

    🔴 **`(job_id, terminal phase)`가 유일 키다**(08 §5). 같은 잡의 같은 종단 phase는
    재발행에도 **저장된 같은 `event_id`**를 써야 하므로 `event_id`를 인자로 받는다 —
    여기서 만들면 재발행마다 새 값이 되어 백엔드 멱등이 깨진다.

    ⚠ `partial_success`·`rejected_insufficient`는 **실패가 아니다** — 유효한 결과를
    저장했으므로 `succeeded` 이벤트로 나가고 세트 결과는 `result_status`가 말한다
    (08 §4 · BE 명세 §6.5 동일 결론).
    """

    if job.phase not in WORKER_JOB_EVENT_TYPES:
        raise ValueError(f"종단 phase가 아닌 잡으로 결과 이벤트를 만들 수 없다: {job.phase}")

    payload: dict[str, Any] = {
        "job_id": str(job.job_id),
        "execution_id": str(job.execution_id),
        "operation": job.operation.value,
        "phase": job.phase.value,
    }
    set_id = _set_id(outcome)
    if set_id is not None:
        payload["set_id"] = set_id
    result_status = _result_status(outcome)
    if result_status is not None:
        payload["result_status"] = result_status
    if job.result_ref is not None:
        # 본문은 이 참조로 REST 조회한다 — 이벤트에 복제하지 않는다(08 §4).
        payload["result_ref"] = job.result_ref
    if job.error_code is not None:
        payload["error_code"] = job.error_code

    return _envelope(
        event_id=event_id,
        event_type=WORKER_JOB_EVENT_TYPES[job.phase],
        occurred_at=occurred_at,
        tenant_id=job.tenant_id,
        problem_request_id=problem_request_id,
        payload=payload,
    )


def worker_job_progress_event(
    job: WorkerJob,
    *,
    problem_request_id: str,
    progress: float,
    event_id: UUID,
    occurred_at: datetime,
) -> dict[str, Any]:
    """선택 진행 이벤트 — 상태만 옮기고 결과는 싣지 않는다(08 §4)."""

    if not 0.0 <= progress <= 1.0:
        raise ValueError("progress는 0.0~1.0이어야 한다")
    return _envelope(
        event_id=event_id,
        event_type=WORKER_JOB_PROGRESS_EVENT_TYPE,
        occurred_at=occurred_at,
        tenant_id=job.tenant_id,
        problem_request_id=problem_request_id,
        payload={
            "job_id": str(job.job_id),
            "execution_id": str(job.execution_id),
            "operation": job.operation.value,
            "phase": JobPhase.RUNNING.value,
            "progress": progress,
        },
    )


__all__ = [
    "PG_REQUEST_EVENT_TYPE",
    "PG_REQUEST_SCHEMA_VERSION",
    "WORKER_JOB_EVENT_TYPES",
    "WORKER_JOB_PROGRESS_EVENT_TYPE",
    "WORKER_JOB_SCHEMA_VERSION",
    "ProblemRequestedEvent",
    "ProblemRequestedPayload",
    "validate_target_alias",
    "worker_job_progress_event",
    "worker_job_terminal_event",
]
