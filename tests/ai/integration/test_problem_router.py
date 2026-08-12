"""문제 생성 슈퍼바이저 배선·라우터 통합 계약."""

from __future__ import annotations

import asyncio
import sys
import time
from collections.abc import Coroutine, Sequence
from datetime import timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from ai.agents.job_store import InMemoryJobStore
from ai.agents.supervisor import Supervisor
from ai.api.app import create_app
from ai.api.routers import problem as problem_router
from ai.contracts.agents import JobPhase, WorkerKind
from ai.contracts.diagnosis import DiagnosisResult
from ai.contracts.execution import ExecutionContext, RunMetadata
from ai.contracts.llm import (
    CallOutcome,
    LlmTimeout,
    LlmUnavailable,
    ModelRole,
    RedactionBlocked,
)
from ai.contracts.problem_generation import (
    Answer,
    Choice,
    EvidenceAnchor,
    EvidenceKind,
    GeneratedItem,
    ItemResult,
    ProblemRequest,
    SolveResult,
    SourceMaterialDraft,
)
from ai.contracts.taxonomy import AreaTag, ItemFormat, TypeTag
from ai.db.repositories.run_store import (
    CollectedCall,
    InMemoryRunStore,
    LlmCallCollector,
    default_llm_call_collector,
)
from ai.db.store_factory import build_agent_job_store, reset_shared_agent_runtime
from ai.llm.gateway import LlmCallRecord
from ai.problem_generation.assembly import (
    ProblemGenerationRunner,
    ProblemRuntimeStores,
    problem_runtime_stores,
)
from ai.problem_generation.domain.models import StoredProblemItem
from ai.problem_generation.enqueue import ProblemGenerationEnqueuer
from ai.problem_generation.infrastructure.memory_store import (
    InMemoryProblemItemStore,
)
from ai.problem_generation.provider import ProblemProviders
from ai.runtime.errors import (
    DomainException,
    LlmUpstreamDown,
    LlmUpstreamTimeout,
    RedactionUncertain,
)

_FAKES_DIR = Path(__file__).parents[1] / "fakes"
sys.path.insert(0, str(_FAKES_DIR))

from fake_graph_context import (  # noqa: E402
    FakeGraphContextService,
)
from fake_provider import FakeProvider  # noqa: E402

_HEADERS = {
    "X-Tenant-Id": "tenant-pg-router",
    "X-Request-Id": "request-pg-router",
    "Idempotency-Key": "idem-pg-router",
}
_SKILL_NODE_ID = "grammar.sentence-structure"


def _run[T](coro: Coroutine[object, object, T]) -> T:
    return asyncio.run(coro)


async def _unused_diagnosis(_: ProblemRequest) -> DiagnosisResult:
    raise AssertionError("teacher_manual 요청은 진단을 호출하지 않아야 한다")


def _body(
    *,
    target_ref: str = "student-A",
    area_tag: str = "language",
    count: int = 1,
    type_tags: tuple[str, ...] = ("infer",),
) -> dict[str, Any]:
    return {
        "target_kind": "student",
        "target_ref": target_ref,
        "target_source": "teacher_manual",
        "manual_targets": [_SKILL_NODE_ID],
        "snapshot_hash": "snapshot-pg-router",
        "taxonomy_version": "v1",
        "area_tag": area_tag,
        "type_tags": list(type_tags),
        "item_format": "mcq",
        "count": count,
    }


def _problem_request(*, request_id: str, target_ref: str) -> ProblemRequest:
    return ProblemRequest.model_validate(
        {
            **_body(target_ref=target_ref),
            "request_id": request_id,
            "idempotency_key": f"idem-{request_id}",
            "tenant_id": _HEADERS["X-Tenant-Id"],
        }
    )


def _generated_item_json(
    type_tag: TypeTag = TypeTag.INFER,
    area_tag: AreaTag = AreaTag.LANGUAGE,
) -> str:
    stem = (
        "밑줄 친 절이 문장에서 담당하는 기능을 추론한 것으로 옳은 것을 고르시오."
        if type_tag is TypeTag.INFER
        else "문장 성분의 개념과 종류를 설명한 것으로 옳은 것을 고르시오."
    )
    return GeneratedItem(
        area_tag=area_tag,
        type_tag=type_tag,
        item_format=ItemFormat.MCQ,
        skill_node_id=_SKILL_NODE_ID,
        stem=stem,
        choices=tuple(
            Choice(
                no=no,
                text=f"문장 구조 선택지 {no}",
                why_wrong=None if no == 1 else f"{no}번은 문법 근거와 다르다.",
            )
            for no in range(1, 6)
        ),
        answer=Answer(correct_no=1),
        rationale="승인된 문법 근거에 따르면 1번이 옳다.",
        evidence=(EvidenceAnchor(kind=EvidenceKind.GRAMMAR_RULE, ref="grammar:rule-1"),),
    ).model_dump_json()


def _solve_result_json() -> str:
    return SolveResult(
        chosen=1,
        reasoning="문법 근거를 독립적으로 확인했다.",
        confidence=0.95,
        target_skill_node_id=_SKILL_NODE_ID,
        measured_skill_node_id=_SKILL_NODE_ID,
        aligned=True,
        alignment_confidence=0.95,
        alignment_reason="목표 문법 노드와 일치한다.",
    ).model_dump_json()


def _providers(
    *,
    calls: int = 1,
    item_types: tuple[TypeTag, ...] | None = None,
    generator_steps: tuple[str, ...] | None = None,
) -> tuple[ProblemProviders, FakeProvider, FakeProvider]:
    resolved_types = item_types or tuple(TypeTag.INFER for _ in range(calls))
    generator = FakeProvider(
        generator_steps
        or tuple(_generated_item_json(type_tag) for type_tag in resolved_types),
        name="explicit-test-generator",
    )
    verifier = FakeProvider(
        tuple(_solve_result_json() for _ in range(calls)),
        name="explicit-test-verifier",
    )
    return (
        ProblemProviders(
            generator=generator,
            verifier=verifier,
            has_dedicated_verifier=False,
        ),
        generator,
        verifier,
    )


def _prepare(
    *,
    calls: int = 1,
    stores: ProblemRuntimeStores | None = None,
    generator_steps: tuple[str, ...] | None = None,
) -> tuple[InMemoryRunStore, ProblemRuntimeStores, FakeProvider, FakeProvider]:
    reset_shared_agent_runtime()
    problem_router.reset_problem_router()
    providers, generator, verifier = _providers(
        calls=calls,
        generator_steps=generator_steps,
    )
    resolved_stores = stores or problem_runtime_stores()
    run_store = InMemoryRunStore()
    problem_router.set_problem_providers(providers)
    problem_router.set_problem_services(
        graph_context=FakeGraphContextService(), diagnosis=_unused_diagnosis
    )
    problem_router.set_problem_stores(resolved_stores)
    problem_router.set_problem_run_store(run_store)
    return run_store, resolved_stores, generator, verifier


def test_problem_router_roundtrip_and_prompt_version_ledger_match() -> None:
    run_store, _stores, generator, verifier = _prepare()

    with TestClient(create_app()) as client:
        posted = client.post("/v1/problems", headers=_HEADERS, json=_body())
        assert posted.status_code == 202
        job_id = posted.json()["data"]["job_id"]
        fetched = client.get(
            f"/v1/problems/{job_id}",
            headers={"X-Tenant-Id": _HEADERS["X-Tenant-Id"]},
        )

    assert fetched.status_code == 200
    assert fetched.json()["data"]["status"] == "succeeded"
    assert fetched.json()["data"]["result"]["outcome"] == "problem_set"
    prompt_version = posted.json()["meta"]["versions"]["prompt"]
    assert prompt_version == "v4"
    assert len(run_store.runs) == 1
    run = next(iter(run_store.runs.values()))
    assert run.prompt_version == prompt_version
    assert {call.prompt_version for call in run_store.calls} == {"v4"}
    assert len(posted.json()["meta"]["versions"]) == 10
    assert len(generator.requests) == 1
    assert len(verifier.requests) == 1
    assert default_llm_call_collector().evicted_runs == 0


def test_problem_post_replays_202_and_conflicts_on_different_body() -> None:
    _prepare()

    with TestClient(create_app()) as client:
        first = client.post("/v1/problems", headers=_HEADERS, json=_body())
        replay = client.post("/v1/problems", headers=_HEADERS, json=_body())
        conflict = client.post(
            "/v1/problems",
            headers=_HEADERS,
            json=_body(target_ref="student-B"),
        )

    assert first.status_code == replay.status_code == 202
    assert replay.json() == first.json()
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"


def test_idempotent_replay_does_not_recall_confirmed_slots() -> None:
    reset_shared_agent_runtime()
    problem_router.reset_problem_router()
    providers, generator, verifier = _providers(
        calls=2, item_types=(TypeTag.INFER, TypeTag.CONCEPT)
    )
    problem_router.set_problem_providers(providers)
    problem_router.set_problem_services(
        graph_context=FakeGraphContextService(), diagnosis=_unused_diagnosis
    )
    problem_router.set_problem_run_store(InMemoryRunStore())
    body = _body(count=2, type_tags=("infer", "concept"))

    with TestClient(create_app()) as client:
        first = client.post("/v1/problems", headers=_HEADERS, json=body)
        replay = client.post("/v1/problems", headers=_HEADERS, json=body)

    assert first.status_code == replay.status_code == 202
    assert len(generator.requests) == len(verifier.requests) == 2
    for type_tag in ("infer", "concept"):
        assert sum(
            f'"type_tag":"{type_tag}"' in request.prompt
            for request in generator.requests
        ) == 1


def test_problem_post_never_returns_another_queued_jobs_result() -> None:
    _run_store, stores, _generator, _verifier = _prepare(calls=2)
    supervisor = Supervisor(
        store=build_agent_job_store(),
        lease_duration=timedelta(minutes=5),
        priority_aging_interval=timedelta(minutes=10),
    )
    _run(
        ProblemGenerationEnqueuer(
            supervisor=supervisor, request_store=stores.requests
        ).enqueue(_problem_request(request_id="other", target_ref="student-other"))
    )

    headers_mine = {**_HEADERS, "X-Request-Id": "request-mine", "Idempotency-Key": "idem-mine"}
    with TestClient(create_app()) as client:
        posted = client.post("/v1/problems", headers=headers_mine, json=_body())
        job_id = posted.json()["data"]["job_id"]
        deadline = time.monotonic() + 5.0
        for _ in range(500):
            after = client.get(
                f"/v1/problems/{job_id}",
                headers={"X-Tenant-Id": _HEADERS["X-Tenant-Id"]},
            )
            if after.json()["data"]["status"] == "succeeded":
                break
            if time.monotonic() >= deadline:
                raise AssertionError("추가 POST 없이 자기 잡이 종단되지 않았다")
            time.sleep(0.01)
        else:
            raise AssertionError("추가 POST 없이 자기 잡이 종단되지 않았다")

    assert posted.json()["data"] == {
        "job_id": job_id,
        "status": "queued",
    }
    assert after.json()["data"]["job_id"] == job_id
    assert after.json()["data"]["status"] == "succeeded"
    assert after.json()["data"]["result"] is not None


def test_problem_startup_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    reset_shared_agent_runtime()
    problem_router.reset_problem_router()
    providers, _generator, _verifier = _providers()
    calls = 0

    def build_once() -> ProblemProviders:
        nonlocal calls
        calls += 1
        return providers

    monkeypatch.setattr(problem_router, "build_problem_providers", build_once)
    problem_router.bootstrap_problem_providers()
    first = problem_router.require_problem_providers()
    problem_router.bootstrap_problem_providers()
    second = problem_router.require_problem_providers()

    assert calls == 1
    assert first is second


def test_problem_provider_has_no_silent_fake_fallback() -> None:
    reset_shared_agent_runtime()
    problem_router.reset_problem_router()

    with pytest.raises(problem_router.ProblemProviderNotWired):
        problem_router.require_problem_providers()


def test_problem_reading_without_passage_is_400_with_reason_code() -> None:
    run_store, _stores, _generator, _verifier = _prepare()
    job_store = build_agent_job_store()
    assert isinstance(job_store, InMemoryJobStore)
    body = _body(area_tag="reading")

    with TestClient(create_app()) as client:
        response = client.post("/v1/problems", headers=_HEADERS, json=body)

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_SCHEMA"
    assert response.json()["error"]["detail"] == {
        "reason": "source_procurement_not_implemented",
        "area_tag": "reading",
        "passage": False,
        "work_selection": False,
    }
    assert len(job_store) == 0
    assert job_store.added == 0
    assert run_store.runs == {}


def test_problem_literature_without_work_selection_is_400_before_job() -> None:
    run_store, _stores, _generator, _verifier = _prepare()
    job_store = build_agent_job_store()
    assert isinstance(job_store, InMemoryJobStore)

    with TestClient(create_app()) as client:
        response = client.post(
            "/v1/problems",
            headers=_HEADERS,
            json=_body(area_tag="literature"),
        )

    assert response.status_code == 400
    assert response.json()["error"]["detail"] == {
        "reason": "source_procurement_not_implemented",
        "area_tag": "literature",
        "passage": False,
        "work_selection": False,
    }
    assert len(job_store) == 0
    assert job_store.added == 0
    assert run_store.runs == {}


@pytest.mark.parametrize(
    ("area_tag", "source_kind"),
    [
        ("speech_writing", "presentation"),
        ("media", "paired"),
    ],
)
def test_generated_source_areas_are_accepted_at_the_front_door(
    area_tag: str,
    source_kind: str,
) -> None:
    material = SourceMaterialDraft(
        material_text="학생 A가 승인 근거를 활용해 자료를 구성했다.",
        evidence_anchor_ids=("grammar:rule-1",),
    )
    _run_store, _stores, _generator, _verifier = _prepare(
        generator_steps=(
            material.model_dump_json(),
            _generated_item_json(area_tag=AreaTag(area_tag)),
        )
    )
    job_store = build_agent_job_store()
    assert isinstance(job_store, InMemoryJobStore)
    body = _body(area_tag=area_tag)
    body["passage"] = {
        "area_tag": area_tag,
        "source_kind": source_kind,
        "banned_topics_version": "pg-banned-v1",
    }

    with TestClient(create_app()) as client:
        response = client.post("/v1/problems", headers=_HEADERS, json=body)

    assert response.status_code == 202
    assert response.json()["data"]["status"] == "succeeded"
    assert len(job_store) == 1


def test_type_tag_reason_precedes_source_reason_for_a_request_violating_both() -> None:
    run_store, _stores, _generator, _verifier = _prepare()
    job_store = build_agent_job_store()
    assert isinstance(job_store, InMemoryJobStore)

    with TestClient(create_app()) as client:
        response = client.post(
            "/v1/problems",
            headers=_HEADERS,
            json=_body(area_tag="reading", type_tags=("apply",)),
        )

    assert response.status_code == 400
    assert response.json()["error"]["detail"]["reason"] == "type_tag_not_supported"
    assert len(job_store) == 0
    assert job_store.added == 0
    assert run_store.runs == {}


def test_problem_get_hides_other_tenants_job() -> None:
    _prepare()

    with TestClient(create_app()) as client:
        posted = client.post("/v1/problems", headers=_HEADERS, json=_body())
        job_id = posted.json()["data"]["job_id"]
        hidden = client.get(
            f"/v1/problems/{job_id}", headers={"X-Tenant-Id": "tenant-other"}
        )

    assert hidden.status_code == 404
    assert hidden.json()["error"]["code"] == "NOT_FOUND"


class _RecordingProblemItemStore(InMemoryProblemItemStore):
    def __init__(self) -> None:
        super().__init__()
        self.saved_slots: list[int] = []

    async def save(
        self,
        *,
        set_id: UUID,
        slot_index: int,
        result: ItemResult,
        candidate_ref: str | None,
        item: GeneratedItem | None,
    ) -> StoredProblemItem:
        self.saved_slots.append(slot_index)
        return await super().save(
            set_id=set_id,
            slot_index=slot_index,
            result=result,
            candidate_ref=candidate_ref,
            item=item,
        )


def test_problem_store_injection_seam_uses_the_supplied_item_store() -> None:
    reset_shared_agent_runtime()
    problem_router.reset_problem_router()
    item_store = _RecordingProblemItemStore()
    stores = problem_runtime_stores(item_store=item_store)
    _prepare(stores=stores)

    with TestClient(create_app()) as client:
        response = client.post("/v1/problems", headers=_HEADERS, json=_body())

    assert response.status_code == 202
    assert item_store.saved_slots == [0]


def test_pg_job_is_not_leased_by_counsel_worker_kind() -> None:
    reset_shared_agent_runtime()
    problem_router.reset_problem_router()
    stores = problem_runtime_stores()
    supervisor = Supervisor(
        store=build_agent_job_store(),
        lease_duration=timedelta(minutes=5),
        priority_aging_interval=timedelta(minutes=10),
    )

    async def scenario() -> tuple[object | None, object | None]:
        await ProblemGenerationEnqueuer(
            supervisor=supervisor, request_store=stores.requests
        ).enqueue(_problem_request(request_id="lease", target_ref="student-lease"))
        stolen = await supervisor.lease_next(
            tenant_id=_HEADERS["X-Tenant-Id"],
            worker_kind=WorkerKind.COUNSEL_PACK,
            lease_owner="counsel-router",
        )
        mine = await supervisor.lease_next(
            tenant_id=_HEADERS["X-Tenant-Id"],
            worker_kind=WorkerKind.PROBLEM_GENERATION,
            lease_owner="problem-router",
        )
        return stolen, mine

    stolen, mine = _run(scenario())
    assert stolen is None
    assert mine is not None


class _ExplodingWorkflow:
    def __init__(self, exc: Exception, collector: LlmCallCollector) -> None:
        self._exc = exc
        self._collector = collector

    async def run(
        self, _request: ProblemRequest, context: ExecutionContext
    ) -> None:
        self._collector(
            LlmCallRecord(
                role=ModelRole.GENERATOR,
                prompt_id="pg.items.v1",
                prompt_version="v4",
                provider="failure-provider",
                model="failure-model",
                usage=None,
                latency_ms=0,
                outcome=CallOutcome.PROVIDER_ERROR,
            ),
            context,
        )
        raise self._exc


class _FailingRunStore(InMemoryRunStore):
    async def record_run(
        self, run: RunMetadata, calls: Sequence[CollectedCall]
    ) -> None:
        del run, calls
        raise RuntimeError("ledger-down")


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (RedactionBlocked("blocked"), RedactionUncertain),
        (LlmTimeout("timeout"), LlmUpstreamTimeout),
        (LlmUnavailable("down"), LlmUpstreamDown),
        (DomainException("domain"), DomainException),
    ],
)
def test_problem_ledger_survives_every_failure_kind(
    exc: Exception, expected: type[Exception]
) -> None:
    reset_shared_agent_runtime()
    problem_router.reset_problem_router()
    stores = problem_runtime_stores()
    collector = LlmCallCollector()
    run_store = InMemoryRunStore()
    supervisor = Supervisor(
        store=build_agent_job_store(),
        lease_duration=timedelta(minutes=5),
        priority_aging_interval=timedelta(minutes=10),
    )

    async def scenario() -> tuple[Any, Any]:
        job = await ProblemGenerationEnqueuer(
            supervisor=supervisor, request_store=stores.requests
        ).enqueue(_problem_request(request_id="failure", target_ref="student-failure"))
        runner = ProblemGenerationRunner(
            supervisor=supervisor,
            request_store=stores.requests,
            result_store=stores.results,
            workflow=_ExplodingWorkflow(exc, collector),
            run_store=run_store,
            call_log=collector,
            verify_config_version="verify-config.v1",
            prompt_version="v4",
            lease_owner="problem-router",
        )
        with pytest.raises(expected):
            await runner.run_next(tenant_id=job.tenant_id)
        failed = await supervisor.get(tenant_id=job.tenant_id, job_id=job.job_id)
        return job, failed

    job, failed = _run(scenario())
    assert failed is not None and failed.phase.value == "failed"
    assert job.execution_id in run_store.runs
    assert len(run_store.calls) == 1
    assert collector.peek(job.execution_id) == ()
    assert collector.evicted_runs == 0


def test_failed_path_ledger_error_does_not_replace_the_original_error() -> None:
    reset_shared_agent_runtime()
    problem_router.reset_problem_router()
    stores = problem_runtime_stores()
    collector = LlmCallCollector()
    supervisor = Supervisor(
        store=build_agent_job_store(),
        lease_duration=timedelta(minutes=5),
        priority_aging_interval=timedelta(minutes=10),
    )

    async def scenario() -> None:
        job = await ProblemGenerationEnqueuer(
            supervisor=supervisor, request_store=stores.requests
        ).enqueue(_problem_request(request_id="ledger", target_ref="student-ledger"))
        runner = ProblemGenerationRunner(
            supervisor=supervisor,
            request_store=stores.requests,
            result_store=stores.results,
            workflow=_ExplodingWorkflow(LlmTimeout("timeout"), collector),
            run_store=_FailingRunStore(),
            call_log=collector,
            verify_config_version="verify-config.v1",
            prompt_version="v4",
            lease_owner="problem-router",
        )
        with pytest.raises(LlmUpstreamTimeout):
            await runner.run_next(tenant_id=job.tenant_id)

    _run(scenario())


def test_ledger_generation_params_are_a_usage_axis_not_a_path_axis() -> None:
    """🔴 `AI_RUN.generation_params`는 **그 실행이 실제로 쓴 값**이다 (99 ㊼ · A 8/9 통일).

    LLM을 한 번도 안 부른 실행이 pg에도 실재한다 — R-1 기준 자료가 없으면 생성 호출
    **전에** 수렴한다. 그때 `seed`·`temperature`를 적어 두면 원장이 *"그 파라미터로
    돌렸다"* 는 없는 사실을 말하고, 그 컬럼을 재현 키로 읽는 사람이 속는다.

    ⚠ 대조군을 같이 본다 — 실제로 부른 실행에서는 값이 **남아야** 한다. 안 그러면 이
    단정은 "항상 None"이라는 다른 결함과 구분되지 않는다.
    """
    run_store, _stores, generator, verifier = _prepare()
    problem_router.set_problem_services(
        graph_context=FakeGraphContextService(((),)), diagnosis=_unused_diagnosis
    )

    with TestClient(create_app()) as client:
        posted = client.post("/v1/problems", headers=_HEADERS, json=_body())

    assert posted.status_code == 202
    assert not generator.requests, "기준 자료 없음 경로가 생성 LLM을 불렀다 — 전제가 깨졌다"
    assert not verifier.requests
    run = next(iter(run_store.runs.values()))
    assert run.generation_params is None
    assert run.model_provider is None
    assert run.model_name is None

    # 대조군 — 실제로 부른 실행은 값을 남긴다.
    called_store, _s, _g, _v = _prepare()
    with TestClient(create_app()) as client:
        assert client.post("/v1/problems", headers=_HEADERS, json=_body()).status_code == 202
    called = next(iter(called_store.runs.values()))
    assert called.generation_params is not None
    assert called.model_provider is not None


def test_post_202_carries_the_phase_so_be_knows_whether_to_wait() -> None:
    """🔴 202가 `status`를 싣는다 — BE가 **통지를 기다릴지 바로 GET할지**를 그 값으로 정한다.

    ⚠ pg는 POST 안에서 워커를 동기 실행하지만 `run_next()`가 **자기 잡을 처리한다는 보장이
    없다**(우선순위·aging 순서). 그래서 202가 종단으로 나가는 경로와 `queued`로 나가는
    경로가 **둘 다 실재한다** — `job_id`만 실어 보내면 BE가 어느 쪽인지 알 수 없다.

    ⚠ counsel 202와 같은 형태다(04 §3.9) — 두 잡 엔드포인트가 다르게 생기면 BE가 규칙을
    두 벌 만든다.
    """
    _prepare()

    with TestClient(create_app()) as client:
        posted = client.post("/v1/problems", headers=_HEADERS, json=_body())
        job_id = posted.json()["data"]["job_id"]
        fetched = client.get(
            f"/v1/problems/{job_id}",
            headers={"X-Tenant-Id": _HEADERS["X-Tenant-Id"]},
        )

    assert posted.status_code == 202
    assert set(posted.json()["data"]) == {"job_id", "status"}
    assert posted.json()["data"]["status"] == "succeeded"
    # 🔴 같은 잡을 두 문으로 읽었을 때 같은 값이어야 한다 — 202의 status가 GET과 갈리면
    #    BE가 어느 쪽을 믿을지 알 수 없다.
    assert posted.json()["data"]["status"] == fetched.json()["data"]["status"]


def test_post_202_says_queued_when_the_runner_took_another_job() -> None:
    """🔴 **202가 `queued`로 나가는 경로가 실재한다** — 그래서 위 단정이 상수 대조가 아니다.

    러너는 우선순위·aging 순으로 **다음 잡 하나**를 처리한다. 앞에 다른 잡이 있으면 내 잡은
    큐에 남고, 그 상태로 202가 나간다. ⚠ 이때 BE가 통지를 기다리면 **다음 요청이 올 때까지
    안 돈다** — 배경 드레인 루프가 없다(v1 인라인 실행 · 09 §2-24).
    """
    _run_store, stores, _gen, _ver = _prepare(calls=2)
    supervisor = Supervisor(
        store=build_agent_job_store(),
        lease_duration=timedelta(minutes=5),
        priority_aging_interval=timedelta(minutes=10),
    )
    _run(
        ProblemGenerationEnqueuer(
            supervisor=supervisor, request_store=stores.requests
        ).enqueue(_problem_request(request_id="ahead", target_ref="student-ahead"))
    )

    with TestClient(create_app()) as client:
        posted = client.post("/v1/problems", headers=_HEADERS, json=_body())

    assert posted.status_code == 202
    assert posted.json()["data"]["status"] == "queued", (
        "앞선 잡이 있는데도 202가 종단으로 나온다 — 러너가 내 잡을 처리했다는 뜻이라 "
        "이 테스트의 전제(다음 잡 하나만 처리)가 깨졌다"
    )


def test_background_drain_finishes_all_jobs_without_another_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """POST 한 번이 알린 테넌트의 기존 큐를 추가 요청 없이 종단까지 비운다."""

    monkeypatch.setenv("PG_DRAIN_ENABLED", "true")
    monkeypatch.setenv("PG_DRAIN_MAX_JOBS_PER_CYCLE", "2")
    monkeypatch.setenv("PG_DRAIN_CYCLE_INTERVAL_SECONDS", "0.001")
    monkeypatch.setenv("PG_DRAIN_IDLE_INTERVAL_SECONDS", "0.001")
    _run_store, stores, _generator, _verifier = _prepare(calls=3)
    supervisor = Supervisor(
        store=build_agent_job_store(),
        lease_duration=timedelta(minutes=5),
        priority_aging_interval=timedelta(minutes=10),
    )

    async def enqueue_ahead_jobs() -> tuple[Any, Any]:
        enqueuer = ProblemGenerationEnqueuer(
            supervisor=supervisor, request_store=stores.requests
        )
        first = await enqueuer.enqueue(
            _problem_request(request_id="drain-ahead-1", target_ref="student-ahead-1")
        )
        second = await enqueuer.enqueue(
            _problem_request(request_id="drain-ahead-2", target_ref="student-ahead-2")
        )
        return first, second

    first, second = _run(enqueue_ahead_jobs())

    async def wait_for_terminal(posted_id: UUID) -> tuple[JobPhase, ...]:
        deadline = asyncio.get_running_loop().time() + 5.0
        job_ids = (first.job_id, second.job_id, posted_id)
        for _ in range(500):
            jobs = [
                await supervisor.get(tenant_id=first.tenant_id, job_id=job_id)
                for job_id in job_ids
            ]
            phases = tuple(job.phase for job in jobs if job is not None)
            if len(phases) == 3 and all(
                phase in {JobPhase.SUCCEEDED, JobPhase.FAILED} for phase in phases
            ):
                return phases
            if asyncio.get_running_loop().time() >= deadline:
                break
            await asyncio.sleep(0.01)
        raise AssertionError("추가 요청 없이 배경 드레인이 모든 잡을 종단시키지 못했다")

    with TestClient(create_app()) as client:
        posted = client.post("/v1/problems", headers=_HEADERS, json=_body())
        assert posted.status_code == 202
        phases = _run(wait_for_terminal(UUID(posted.json()["data"]["job_id"])))

    assert phases == (JobPhase.SUCCEEDED,) * 3
    assert not problem_router.problem_drain_running()


def test_each_app_lifecycle_cleans_up_only_its_own_drain_task() -> None:
    """서로 다른 이벤트 루프의 앱이 상대 드레인 태스크를 취소하지 않는다."""

    _prepare()

    with TestClient(create_app()):
        assert problem_router.problem_drain_running()
        with TestClient(create_app()):
            assert problem_router.problem_drain_running()
        assert problem_router.problem_drain_running()

    assert not problem_router.problem_drain_running()


def test_response_versions_and_ledger_versions_are_the_same_row() -> None:
    """🔴 응답 `meta.versions` == `AI_RUN`의 버전 열. **재현 키가 둘이면 안 된다**(불변식 8).

    A가 counsel에서 이 축이 갈린 것을 찾았다(8/9 · `#20`) — 라우터가 `"0.1.0"`을, 워커가
    `"0.1"`을 실어 **같은 실행인데 응답과 원장이 다른 값을 말한다.** 그때 아무 테스트도
    안 걸렸다.

    🔴 **정본을 어디 두느냐보다 이 단정이 먼저다.** capability마다 상수를 어디 두든,
    응답과 원장이 **같은 자리를 참조**하면 갈릴 수 없다 — pg는 `assembly.problem_versions()`
    하나를 두 문이 함께 쓴다. 이 테스트는 그 구조를 잠근다.

    ⚠ 키 이름을 손으로 옮기지 않는다 — `envelope.versions_dict()`가 쓰는 것과 같은 변환
    (`_version` 접미사 제거)을 `RunMetadata`에 적용해 만든다. 손으로 적으면 **두 번째 사본**이
    되고, 계약이 열 개에서 열한 개가 되는 날 이 테스트만 조용히 낡는다(99 #07 부류).
    """
    run_store, _stores, _gen, _ver = _prepare()

    with TestClient(create_app()) as client:
        posted = client.post("/v1/problems", headers=_HEADERS, json=_body())

    assert posted.status_code == 202
    meta = posted.json()["meta"]
    run = next(iter(run_store.runs.values()))
    ledger = {
        name.removesuffix("_version"): getattr(run, name)
        for name in type(run).model_fields
        if name.endswith("_version")
    }

    assert ledger, (
        "AI_RUN에서 버전 열을 하나도 못 찾았다 — 필드 이름이 바뀌었다면 검사가 끊긴 것이다"
    )
    assert meta["versions"] == ledger, (
        "응답과 원장이 같은 실행에 다른 버전을 말한다 — 과거 실행을 어느 값으로 재현할지가 "
        "갈린다(불변식 8). 두 자리가 같은 상수를 참조하는지 확인하라"
    )
    # 🔴 그 원장 행을 가리키는지까지 본다 — 값이 같아도 다른 행을 가리키면 재현이 안 된다.
    assert meta["execution_id"] == str(run.execution_id)
