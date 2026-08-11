"""문제 세트 생성 요청 저장 후 슈퍼바이저 잡 enqueue."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Protocol
from uuid import UUID, uuid4

from ai.agents.supervisor import Supervisor, system_utc_now
from ai.contracts.agents import (
    OperationKind,
    PriorityClass,
    WorkerJob,
    WorkerKind,
    default_priority_for_operation,
)
from ai.contracts.problem_generation import ProblemRequest
from ai.contracts.taxonomy import V1_TYPE_TAGS
from ai.problem_generation.domain.identity import request_hash
from ai.problem_generation.domain.policy import supports_source_procurement
from ai.runtime.errors import DomainException

#: 요청에 v1 산출 축 밖의 유형 태그가 실렸다 — `error_codes` §6 · 04 §3.11.
TYPE_TAG_NOT_SUPPORTED = "type_tag_not_supported"
#: 현재 워크플로에 없는 자료 조달 방식이 필요하다 — `error_codes` §6 · 04 §3.11.
SOURCE_PROCUREMENT_NOT_IMPLEMENTED = "source_procurement_not_implemented"


class ProblemTypeTagUnsupported(DomainException):
    """v1이 산출하지 않는 예약 태그가 출제 요청에 실렸다 (99 ㊣·㊨).

    🔴 **문 앞 예외다.** 자료 조달 미구현도 문 앞으로 이동했다(2026-08-09 · 99 #01).
    둘 다 위반한 요청은 기존 관측 순서를 보존해 이 예외가 먼저 나며, 어느 400도 실패 잡을
    남기지 않는다.
    """

    code = "INVALID_SCHEMA"
    http_status = 400


class ProblemSourceProcurementUnsupported(DomainException):
    """현재 구현이 조달할 수 없는 자료가 필요한 출제 요청."""

    code = "INVALID_SCHEMA"
    http_status = 400


def reject_unsupported_type_tags(request: ProblemRequest) -> None:
    """v1 산출 축 밖의 유형 태그를 400으로 끊는다 — 순수 판정, I/O 없음.

    🔴 **`RESERVED_TYPE_TAGS`가 아니라 `V1_TYPE_TAGS` 뺄셈으로 본다.** 예약을 열거하면
    새 enum 값이 **조용히 지원 목록에 안 들어가고**(fail-open) 아무도 모른다 — 뺄셈이면
    새 값은 기본이 「지원」이고, 막으려면 `RESERVED_TYPE_TAGS`에 **명시해야** 한다
    (`contracts/taxonomy.py`의 정의와 같은 판단).

    ⚠ `ProblemRequest`의 `model_validator`로 옮기지 마라 — 라우터가 `ValidationError`를
    `SnapshotInvalid`(필드·타입 목록)로 감싸므로 **사유 코드가 사라진다.** BE는 무엇으로
    바꿔야 하는지를 `detail`에서 읽는다.
    """

    unsupported = sorted(tag.value for tag in set(request.type_tags) - V1_TYPE_TAGS)
    if not unsupported:
        return
    raise ProblemTypeTagUnsupported(
        "v1이 산출하지 않는 유형 태그가 출제 요청에 실렸다 — 예약 태그는 학습 이벤트로만 받는다",
        {
            "reason": TYPE_TAG_NOT_SUPPORTED,
            "type_tags": unsupported,
            "supported": sorted(tag.value for tag in V1_TYPE_TAGS),
        },
    )


def reject_unsupported_source_procurement(request: ProblemRequest) -> None:
    """현재 구현된 자료 조달 범위 밖의 요청을 400으로 끊는다 — 순수 판정, I/O 없음."""

    if not supports_source_procurement(
        area_tag=request.area_tag,
        has_passage_request=request.passage is not None,
        has_work_selection=request.work_selection is not None,
    ):
        raise ProblemSourceProcurementUnsupported(
            "지원되는 자료 조달 조합은 language+자료 없음, reading+PassageRequest, "
            "literature+WorkSelection, speech_writing·media+생성 자료 요청이다",
            {
                "reason": SOURCE_PROCUREMENT_NOT_IMPLEMENTED,
                "area_tag": request.area_tag.value,
                "passage": request.passage is not None,
                "work_selection": request.work_selection is not None,
            },
        )


class ProblemRequestStore(Protocol):
    """잡 payload_ref로 문제 생성 요청을 역참조하는 저장 경계."""

    async def put(self, request: ProblemRequest) -> str: ...

    async def get(
        self, request_ref: str, *, tenant_id: str
    ) -> ProblemRequest | None: ...


class ProblemGenerationEnqueuer:
    """문제 생성 요청을 저장하고 ``problem_set.generate`` 잡을 넣는다."""

    def __init__(
        self,
        *,
        supervisor: Supervisor,
        request_store: ProblemRequestStore,
        new_id: Callable[[], UUID] = uuid4,
        now: Callable[[], datetime] = system_utc_now,
    ) -> None:
        self._supervisor = supervisor
        self._requests = request_store
        self._new_id = new_id
        self._now = now

    async def enqueue(self, request: ProblemRequest) -> WorkerJob:
        # 🔴 **최상단이다** — 아래 `put()`보다 앞에서 호출자가 고칠 두 400을 끊어 요청 레코드와
        #    잡을 만들지 않는다. 순서는 현행 관측 보존이다: 둘 다 위반한 요청은 종전에도
        #    `type_tag_not_supported`가 먼저였고, 바꾸면 사유 코드가 조용히 달라진다.
        reject_unsupported_type_tags(request)
        reject_unsupported_source_procurement(request)
        payload_hash = request_hash(request)
        request_ref = await self._requests.put(request)
        operation = OperationKind.PROBLEM_SET_GENERATE
        job = WorkerJob(
            job_id=self._new_id(),
            execution_id=self._new_id(),
            tenant_id=request.tenant_id,
            worker_kind=WorkerKind.PROBLEM_GENERATION,
            operation=operation,
            payload_ref=request_ref,
            payload_hash=payload_hash,
            priority_class=default_priority_for_operation(operation),
            queued_at=self._now(),
        )
        return await self._supervisor.enqueue(job)


def problem_generation_priority() -> PriorityClass:
    """문제 세트 생성의 공용 계약 기본 우선순위."""

    return default_priority_for_operation(OperationKind.PROBLEM_SET_GENERATE)


__all__ = [
    "SOURCE_PROCUREMENT_NOT_IMPLEMENTED",
    "TYPE_TAG_NOT_SUPPORTED",
    "ProblemGenerationEnqueuer",
    "ProblemRequestStore",
    "ProblemSourceProcurementUnsupported",
    "ProblemTypeTagUnsupported",
    "problem_generation_priority",
    "reject_unsupported_source_procurement",
    "reject_unsupported_type_tags",
]
