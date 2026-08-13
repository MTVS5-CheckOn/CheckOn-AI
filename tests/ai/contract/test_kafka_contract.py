"""백엔드 M2 Kafka 명세(2026-08-12)와의 상호 계약 — 픽스처 4종이 정본이다.

백엔드가 §14에서 요청한 fixture 4개를 **코드가 만들고 코드가 읽는다.** 손으로 적은
JSON을 주고받으면 어느 쪽이 먼저 낡았는지 알 수 없다 — 여기서는 파일이 갈리면 CI가 죽는다.

픽스처 파일은 `fixtures/kafka/`에 있고 백엔드 CI가 그대로 복사해 쓸 수 있다.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest

from ai.contracts.agents import (
    JobPhase,
    OperationKind,
    PriorityClass,
    WorkerJob,
    WorkerKind,
)
from ai.contracts.problem_generation import (
    DifficultyBand,
    ProblemItemStatus,
    ProblemSetResult,
    ProblemSetStatus,
    TargetSource,
)
from ai.contracts.taxonomy import V1_TYPE_TAGS, AreaTag, ItemFormat, TypeTag
from ai.problem_generation.infrastructure.kafka_contract import (
    PG_REQUEST_SCHEMA_VERSION,
    WORKER_JOB_SCHEMA_VERSION,
    ProblemRequestedEvent,
    validate_target_alias,
    worker_job_progress_event,
    worker_job_terminal_event,
)

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "kafka"

_TENANT = "tn_0123456789abcdef0123456789abcdef"
_PROBLEM_REQUEST_ID = "0198f100-0000-7000-8000-000000000001"
_JOB_ID = UUID("0198f200-0000-7000-8000-000000000001")
_EXECUTION_ID = UUID("0198f200-0000-7000-8000-000000000002")
_SET_ID = UUID("0198f200-0000-7000-8000-000000000003")
_ITEM_ID = UUID("0198f200-0000-7000-8000-000000000004")
_OCCURRED_AT = datetime(2026, 8, 12, 5, 1, 20, tzinfo=UTC)
_QUEUED_AT = datetime(2026, 8, 12, 5, 0, 0, tzinfo=UTC)
_PAYLOAD_HASH = "sha256:" + "a" * 64


def _load(name: str) -> dict[str, Any]:
    loaded: object = json.loads((FIXTURE_DIR / f"{name}.json").read_text(encoding="utf-8"))
    assert isinstance(loaded, dict), f"픽스처 최상위는 JSON 객체여야 한다: {name}"
    return loaded


def _job(
    *,
    phase: JobPhase,
    result_ref: str | None = None,
    error_code: str | None = None,
) -> WorkerJob:
    #: 진행 중인 잡은 활성 lease를 들고 있어야 계약이 선다(`assert_job_transition`).
    active = phase in {JobPhase.LEASED, JobPhase.RUNNING}
    return WorkerJob(
        job_id=_JOB_ID,
        execution_id=_EXECUTION_ID,
        tenant_id=_TENANT,
        worker_kind=WorkerKind.PROBLEM_GENERATION,
        operation=OperationKind.PROBLEM_SET_GENERATE,
        payload_ref=f"problem-request:{_TENANT}:{_PROBLEM_REQUEST_ID}",
        payload_hash=_PAYLOAD_HASH,
        phase=phase,
        priority_class=PriorityClass.STANDARD,
        dispatch_attempt=1,
        lease_generation=1,
        lease_owner="problem-router" if active else None,
        lease_acquired_at=_QUEUED_AT if active else None,
        lease_expires_at=datetime(2026, 8, 12, 5, 5, tzinfo=UTC) if active else None,
        checkpoint_ref=f"problem-set:{_SET_ID}:slot:0" if active else None,
        result_ref=result_ref,
        error_code=error_code,
        queued_at=_QUEUED_AT,
        started_at=_QUEUED_AT,
        finished_at=None if active else _OCCURRED_AT,
    )


def _outcome() -> ProblemSetResult:
    return ProblemSetResult(
        set_id=_SET_ID,
        status=ProblemSetStatus.GENERATED,
        target_source=TargetSource.TEACHER_MANUAL,
        personalized=False,
        requested_count=1,
        processed_count=1,
        unstarted_count=0,
        items=(
            {
                "item_id": _ITEM_ID,
                "status": ProblemItemStatus.VERIFIED,
                "attempt_no": 1,
                "difficulty_band": DifficultyBand.MEDIUM,
            },
        ),
    )


# --------------------------------------------------------------------------
# 백엔드 → AI 요청 이벤트
# --------------------------------------------------------------------------


def test_backend_request_event_maps_onto_our_command() -> None:
    """🔴 이름이 갈리는 자리 — 백엔드는 `problem_request_id`, 우리 계약은 `request_id`다.

    `ProblemRequest`가 `extra="forbid"`라 매핑 없이 그대로 넣으면 검증에서 죽는다.
    """
    event = ProblemRequestedEvent.model_validate(_load("problem_generation.requested"))
    request = event.to_problem_request()

    assert request.request_id == _PROBLEM_REQUEST_ID
    assert request.tenant_id == _TENANT
    assert request.idempotency_key.startswith("pg_")
    assert request.area_tag is AreaTag.LANGUAGE
    assert request.item_format is ItemFormat.MCQ
    assert request.count == 3
    assert request.requested_difficulty is DifficultyBand.MEDIUM
    assert request.passage is None


def test_backend_type_tag_vocabulary_is_inside_what_v1_produces() -> None:
    """백엔드가 여는 4종이 우리 v1 산출 축과 같아야 400이 안 난다.

    예약 태그(`apply`)가 섞이면 `enqueue`가 문 앞에서 400으로 끊는다 — 백엔드가 화면에서
    그 값을 열어 두면 강사가 매번 실패한다.
    """
    event = ProblemRequestedEvent.model_validate(_load("problem_generation.requested"))
    request = event.to_problem_request()

    assert set(request.type_tags) <= V1_TYPE_TAGS
    assert TypeTag.APPLY not in V1_TYPE_TAGS


def test_target_alias_prefix_must_match_the_target_kind() -> None:
    """`student` 요청에 클래스 alias가 오면 우리가 먼저 끊는다."""
    raw = _load("problem_generation.requested")
    raw["payload"]["request"]["target_ref"] = "cl_" + "0" * 32
    request = ProblemRequestedEvent.model_validate(raw).to_problem_request()

    with pytest.raises(ValueError, match="st_"):
        validate_target_alias(request)


def test_correlation_id_and_problem_request_id_must_agree() -> None:
    """둘 다 보내는 것이 권장인데 값이 갈리면 어느 쪽이 요청인지 정할 수 없다."""
    raw = _load("problem_generation.requested")
    raw["correlation_id"] = "0198f100-0000-7000-8000-0000000000ff"

    with pytest.raises(ValueError, match="correlation_id"):
        ProblemRequestedEvent.model_validate(raw)


def test_request_fixture_declares_the_backend_schema_version() -> None:
    assert _load("problem_generation.requested")["schema_version"] == (
        PG_REQUEST_SCHEMA_VERSION
    )


# --------------------------------------------------------------------------
# AI → 백엔드 결과 이벤트
# --------------------------------------------------------------------------


def test_succeeded_fixture_is_what_our_code_emits() -> None:
    produced = worker_job_terminal_event(
        _job(phase=JobPhase.SUCCEEDED, result_ref=f"problem-result:{_JOB_ID}"),
        problem_request_id=_PROBLEM_REQUEST_ID,
        outcome=_outcome(),
        event_id=UUID("0198f300-0000-7000-8000-000000000001"),
        occurred_at=_OCCURRED_AT,
    )

    assert produced == _load("worker_job.succeeded")


def test_failed_fixture_is_what_our_code_emits() -> None:
    produced = worker_job_terminal_event(
        _job(phase=JobPhase.FAILED, error_code="problem_worker_internal"),
        problem_request_id=_PROBLEM_REQUEST_ID,
        outcome=None,
        event_id=UUID("0198f300-0000-7000-8000-000000000003"),
        occurred_at=_OCCURRED_AT,
    )

    assert produced == _load("worker_job.failed")


def test_progress_fixture_is_what_our_code_emits() -> None:
    produced = worker_job_progress_event(
        _job(phase=JobPhase.RUNNING),
        problem_request_id=_PROBLEM_REQUEST_ID,
        progress=0.5,
        event_id=UUID("0198f300-0000-7000-8000-000000000002"),
        occurred_at=datetime(2026, 8, 12, 5, 0, 30, tzinfo=UTC),
    )

    assert produced == _load("worker_job.progress")


def test_every_result_event_carries_both_correlation_axes() -> None:
    """백엔드 §6.2 권장 — 둘 다 실어야 어느 쪽을 읽어도 같은 값이 된다."""
    for name in ("worker_job.succeeded", "worker_job.failed", "worker_job.progress"):
        event = _load(name)
        assert event["correlation_id"] == event["payload"]["problem_request_id"]
        assert event["tenant_id"] == _TENANT
        assert event["schema_version"] == WORKER_JOB_SCHEMA_VERSION
        assert event["payload"]["worker_kind"] == "problem_generation"


def test_result_events_never_carry_item_bodies() -> None:
    """🔴 본문은 REST로 간다(08 §4·§5) — 이벤트에 발문·선지가 실리면 안 된다.

    개인정보성 자유 텍스트 규약과 Kafka 메시지 크기 규약이 여기서 함께 깨진다.
    """
    serialized = json.dumps(_load("worker_job.succeeded"), ensure_ascii=False)

    for forbidden in ("stem", "choices", "rationale", "evidence", "passage_text"):
        assert forbidden not in serialized

    payload = _load("worker_job.succeeded")["payload"]
    assert payload["result_ref"], "본문을 안 실으면 참조는 반드시 있어야 한다"


def test_terminal_ids_are_stable_across_republish() -> None:
    """같은 `(job_id, terminal phase)`는 저장된 같은 `event_id`로 재발행한다(08 §5).

    `event_id`를 인자로 받는 설계가 그 규약을 코드로 강제한다 — 함수 안에서 만들면
    재발행마다 새 값이 되어 백엔드 멱등(§9-2)이 깨진다.
    """
    job = _job(phase=JobPhase.SUCCEEDED, result_ref=f"problem-result:{_JOB_ID}")
    kwargs: dict[str, Any] = {
        "problem_request_id": _PROBLEM_REQUEST_ID,
        "outcome": _outcome(),
        "event_id": UUID("0198f300-0000-7000-8000-000000000001"),
        "occurred_at": _OCCURRED_AT,
    }

    assert worker_job_terminal_event(job, **kwargs) == worker_job_terminal_event(
        job, **kwargs
    )


def test_non_terminal_job_cannot_produce_a_terminal_event() -> None:
    with pytest.raises(ValueError, match="종단 phase"):
        worker_job_terminal_event(
            _job(phase=JobPhase.RUNNING),
            problem_request_id=_PROBLEM_REQUEST_ID,
            outcome=None,
            event_id=UUID("0198f300-0000-7000-8000-000000000009"),
            occurred_at=_OCCURRED_AT,
        )


def test_partial_success_is_a_succeeded_event_not_a_failure() -> None:
    """유효한 결과를 저장했으면 성공 이벤트다 — 세트 상태는 `result_status`가 말한다."""
    partial = ProblemSetResult(
        set_id=_SET_ID,
        status=ProblemSetStatus.PARTIAL_SUCCESS,
        stop_reason="drop_ratio_exceeded",
        target_source=TargetSource.TEACHER_MANUAL,
        personalized=False,
        requested_count=2,
        processed_count=2,
        unstarted_count=0,
        items=(
            {
                "item_id": _ITEM_ID,
                "status": ProblemItemStatus.VERIFIED,
                "attempt_no": 1,
            },
            {
                "status": ProblemItemStatus.DROPPED,
                "attempt_no": 3,
                "failure_reason": "generation_exhausted",
            },
        ),
    )
    event = worker_job_terminal_event(
        _job(phase=JobPhase.SUCCEEDED, result_ref=f"problem-result:{_JOB_ID}"),
        problem_request_id=_PROBLEM_REQUEST_ID,
        outcome=partial,
        event_id=UUID("0198f300-0000-7000-8000-000000000004"),
        occurred_at=_OCCURRED_AT,
    )

    assert event["event_type"] == "worker_job.succeeded"
    assert event["payload"]["result_status"] == "partial_success"
    assert "error_code" not in event["payload"]
