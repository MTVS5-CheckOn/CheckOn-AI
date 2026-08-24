"""리포트 목록·상세·강사 수정·AI 원문 복귀 HTTP 수직 슬라이스."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
from fake_provider import FakeProvider
from fastapi.testclient import TestClient
from httpx import Response

from ai.api.app import create_app
from ai.api.routers.report import (
    reset_report_clock,
    reset_report_narrator,
    reset_report_store,
    set_report_clock,
    set_report_narrator,
    set_report_store,
)
from ai.composition.briefing_gate import check_brief_gate
from ai.contracts.diagnosis import (
    CellVerdict,
    DiagnosisEvent,
    MisconceptionReport,
    NodeVerdict,
    Period,
    PropagatedNode,
    WeaknessCell,
    WeaknessMap,
    WeaknessNode,
)
from ai.contracts.execution import ExecutionContext
from ai.contracts.llm import ModelRole
from ai.contracts.problem_generation import DifficultyBand, ItemResult, ProblemItemStatus
from ai.contracts.report import (
    ReportAudience,
    ReportBlock,
    ReportBlockKind,
    ReportBlockRevision,
    ReportEvidenceRef,
    ReportMetricInput,
    ReportRevisionKind,
    ReportStudioData,
    ReportUnproducedMetric,
)
from ai.contracts.report_narration import ReportNarrationDraft
from ai.contracts.taxonomy import AreaTag, TypeTag
from ai.llm.gateway import LlmGateway
from ai.report.memory_store import InMemoryReportStore
from ai.report.narration import (
    ReportNarrationResult,
    ReportNarrationSection,
    ReportNarrationStatus,
    ReportNarrator,
)
from ai.report.store import ReportSourceSnapshot, ReportTimeSeriesSnapshot, StoredReport
from ai.report.vocabulary import (
    RootCauseMetricKind,
    TimeSeriesMetricKind,
    default_report_root_cause_vocabulary,
    default_report_time_series_vocabulary,
)

REPORT_ID = UUID("00000000-0000-4000-8000-000000000501")
BLOCK_ID = UUID("00000000-0000-4000-8000-000000000502")
ABSENT_ID = UUID("00000000-0000-4000-8000-000000000599")
NOW = datetime(2026, 8, 24, 10, 0, tzinfo=UTC)
LATER = datetime(2026, 8, 24, 10, 5, tzinfo=UTC)
HEADERS = {"X-Tenant-Id": "tenant-a", "X-Request-Id": "request-report-1"}


def _evidence(record_id: str = "row-1") -> tuple[ReportEvidenceRef, ...]:
    return (
        ReportEvidenceRef(
            source_table="feature_week",
            record_id=record_id,
            summary="리포트 근거",
        ),
    )


def _time_series_snapshot() -> ReportTimeSeriesSnapshot:
    return ReportTimeSeriesSnapshot(
        period=Period(from_date=date(2026, 8, 1), to_date=date(2026, 8, 31)),
        events=tuple(
            DiagnosisEvent(
                event_id=f"time-series-{index}",
                area_tag=AreaTag.LANGUAGE,
                type_tag=TypeTag.CONCEPT,
                correct=index % 2 == 0,
                occurred_at=NOW + timedelta(hours=index),
                tag_confirmed=True,
            )
            for index in range(10)
        ),
    )


def _stored_report(
    *,
    tenant_id: str = "tenant-a",
    gate_passed: bool = True,
    include_time_series: bool = False,
) -> StoredReport:
    block = ReportBlock(
        block_id=BLOCK_ID,
        seq=0,
        block_type=ReportBlockKind.FACT,
        revisions=(
            ReportBlockRevision(
                revision_no=0,
                revision_kind=ReportRevisionKind.AI_DRAFT,
                ai_original="최초 AI 원문",
                evidence=_evidence(),
                numbers_used=(),
                gate_passed=gate_passed,
            ),
        ),
        active_revision_no=0,
    )
    return StoredReport(
        report_id=REPORT_ID,
        tenant_id=tenant_id,
        guardian_ref="guardian-1",
        source=ReportSourceSnapshot(
            weakness_map=WeaknessMap(
                graph_version="graph-v1",
                taxonomy_version="v1",
                config_version="config-v1",
                snapshot_hash="sha256:report-router",
                cells={
                    "language×concept": WeaknessCell(
                        acc=0.75,
                        n=4,
                        verdict=CellVerdict.OK,
                    ),
                    "reading×fact": WeaknessCell(
                        acc=0.25,
                        n=4,
                        verdict=CellVerdict.WEAK,
                        severity=0.75,
                    ),
                },
                nodes={
                    "language.grammar.fortition": WeaknessNode(
                        verdict=NodeVerdict.WEAK_CONFIRMED,
                        basis=("event:basis-1",),
                    )
                },
                propagated={
                    "language.grammar.phonological_change": PropagatedNode(
                        score=0.75,
                        from_nodes=("language.grammar.fortition",),
                    )
                },
            ),
            misconceptions=MisconceptionReport(
                by_area={"reading": {"scope_confusion": 2}},
                by_node={"read.root": {"scope_confusion": 1}},
            ),
            item_results=(
                ItemResult(
                    item_id=UUID("00000000-0000-4000-8000-000000000503"),
                    status=ProblemItemStatus.VERIFIED,
                    attempt_no=1,
                    difficulty_band=DifficultyBand.LOW,
                ),
            ),
            metrics=(
                ReportMetricInput(
                    metric_key="home_practice_rate",
                    value=70,
                    audience=ReportAudience.GUARDIAN,
                    evidence=_evidence("guardian-metric"),
                ),
                ReportMetricInput(
                    metric_key="class_average",
                    value=63,
                    audience=ReportAudience.TEACHER_ONLY,
                    evidence=_evidence("teacher-metric"),
                ),
            ),
            time_series=_time_series_snapshot() if include_time_series else None,
            cell_min_items=1,
        ),
        blocks=(block,),
        created_at=NOW,
        updated_at=NOW,
    )


@pytest.fixture(autouse=True)
def _isolate() -> Iterator[None]:
    reset_report_store()
    reset_report_clock()
    reset_report_narrator()
    yield
    reset_report_store()
    reset_report_clock()
    reset_report_narrator()


def _client_with(
    report: StoredReport | None = None,
    *,
    narrator: ReportNarrator | _RejectedNarrator | None = None,
) -> TestClient:
    store = InMemoryReportStore()
    if report is not None:
        asyncio.run(store.put(report))
    set_report_store(store)
    set_report_clock(lambda: LATER)
    if narrator is not None:
        set_report_narrator(narrator)
    return TestClient(create_app(), raise_server_exceptions=False)


def _narrator(steps: tuple[str, ...]) -> tuple[ReportNarrator, FakeProvider]:
    provider = FakeProvider(steps, name="fake-report-http")
    gateway = LlmGateway(
        {ModelRole.REPORTER: provider},
        transport_retry={ModelRole.REPORTER: 0},
    )
    return (
        ReportNarrator(
            gateway,
            text_gate=check_brief_gate,
            clock=lambda: NOW,
        ),
        provider,
    )


def _draft(content: str) -> str:
    return ReportNarrationDraft(content=content, numbers_used=()).model_dump_json()


class _RejectedNarrator:
    async def generate(
        self,
        studio_data: ReportStudioData,
        *,
        execution_context: ExecutionContext,
        teacher_instruction: str | None = None,
    ) -> ReportNarrationResult:
        del studio_data, teacher_instruction
        return ReportNarrationResult(
            sections=tuple(
                ReportNarrationSection(
                    block_type=kind,
                    status=ReportNarrationStatus.REJECTED_INSUFFICIENT,
                    prompt_id=f"report.{kind.value}.v1",
                    prompt_version="v1",
                    attempts=0,
                    reason="evidence_missing",
                )
                for kind in ReportBlockKind
            ),
            generated_at=NOW,
            versions=execution_context.versions,
        )


def _create_body() -> dict[str, Any]:
    report = _stored_report()
    return {
        "report_id": str(report.report_id),
        "guardian_ref": report.guardian_ref,
        "source": report.source.model_dump(mode="json"),
    }


def _data(response: Response) -> dict[str, Any]:  # noqa: ANN401 — HTTP JSON envelope
    body = response.json()
    data: dict[str, Any] = body["data"]
    return data


# 생성: 정상·게이트 경계·근거 부재·tenant·헤더 실패
def test_create_generates_five_blocks_and_get_serves_them() -> None:
    narrator, provider = _narrator(
        (
            _draft("학습 기록을 함께 살펴보겠습니다."),
            _draft("확인된 학습 사실을 정리했습니다."),
            _draft("차트에서 확인된 흐름을 정리했습니다."),
            _draft("취약한 내용을 차근차근 복습해 보세요."),
            _draft("다음 기록에서도 변화를 살펴보겠습니다."),
        )
    )
    with _client_with(narrator=narrator) as client:
        created = client.post("/v1/reports", headers=HEADERS, json=_create_body())
        fetched = client.get(f"/v1/reports/{REPORT_ID}", headers=HEADERS)

    assert created.status_code == 200
    assert _data(created)["status"] == "ready"
    assert [item["status"] for item in _data(created)["sections"]] == ["ready"] * 5
    assert fetched.status_code == 200
    assert [block["block_type"] for block in _data(fetched)["blocks"]] == [
        kind.value for kind in ReportBlockKind
    ]
    assert len(provider.requests) == 5
    assert all("class_average" not in request.prompt for request in provider.requests)
    assert all("teacher-metric" not in request.prompt for request in provider.requests)


def test_create_returns_template_only_as_200_without_persisting_blocks() -> None:
    rejected = _draft("이 내용은 문제아를 위한 기록입니다.")
    narrator, provider = _narrator((rejected,) * 15)
    with _client_with(narrator=narrator) as client:
        created = client.post("/v1/reports", headers=HEADERS, json=_create_body())
        fetched = client.get(f"/v1/reports/{REPORT_ID}", headers=HEADERS)

    assert created.status_code == 200
    assert _data(created)["status"] == "template_only"
    assert _data(created)["blocks"] == []
    assert {item["reason"] for item in _data(created)["sections"]} == {
        "forbidden:문제아"
    }
    assert len(provider.requests) == 15
    assert fetched.status_code == 404


def test_create_returns_evidence_missing_as_200_without_calling_a_provider() -> None:
    with _client_with(narrator=_RejectedNarrator()) as client:
        response = client.post("/v1/reports", headers=HEADERS, json=_create_body())

    assert response.status_code == 200
    assert _data(response)["status"] == "rejected_insufficient"
    assert {item["reason"] for item in _data(response)["sections"]} == {
        "evidence_missing"
    }
    assert _data(response)["blocks"] == []


def test_created_report_is_hidden_from_another_tenant() -> None:
    narrator, _provider = _narrator((_draft("학습 기록을 정리했습니다."),) * 5)
    with _client_with(narrator=narrator) as client:
        created = client.post("/v1/reports", headers=HEADERS, json=_create_body())
        hidden = client.get(
            f"/v1/reports/{REPORT_ID}",
            headers={"X-Tenant-Id": "tenant-b", "X-Request-Id": "request-b"},
        )

    assert created.status_code == 200
    assert hidden.status_code == 404


def test_create_rejects_missing_request_id_before_generation() -> None:
    narrator, provider = _narrator((_draft("학습 기록을 정리했습니다."),) * 5)
    with _client_with(narrator=narrator) as client:
        response = client.post(
            "/v1/reports",
            headers={"X-Tenant-Id": "tenant-a"},
            json=_create_body(),
        )

    assert response.status_code == 400
    assert response.json()["error"]["detail"] == {
        "missing_headers": ["X-Request-Id"]
    }
    assert not provider.requests


# 목록: 정상·경계·실패
def test_list_returns_only_current_tenant_reports() -> None:
    with _client_with(_stored_report()) as client:
        response = client.get("/v1/reports", headers=HEADERS)

    assert response.status_code == 200
    assert [item["report_id"] for item in _data(response)["reports"]] == [str(REPORT_ID)]


def test_list_is_empty_at_tenant_boundary() -> None:
    with _client_with(_stored_report(tenant_id="tenant-b")) as client:
        response = client.get("/v1/reports", headers=HEADERS)

    assert response.status_code == 200
    assert _data(response)["reports"] == []


def test_list_rejects_missing_request_id() -> None:
    with _client_with() as client:
        response = client.get("/v1/reports", headers={"X-Tenant-Id": "tenant-a"})

    assert response.status_code == 400
    assert response.json()["error"]["detail"] == {"missing_headers": ["X-Request-Id"]}


# 상세: 정상·게이트 경계·tenant 실패
def test_detail_assembles_guardian_data_with_consistent_omissions() -> None:
    with _client_with(_stored_report()) as client:
        response = client.get(f"/v1/reports/{REPORT_ID}", headers=HEADERS)

    assert response.status_code == 200
    data = _data(response)
    studio = data["studio_data"]
    assert data["audience"] == "guardian"
    assert len(studio["blocks"]) == 6
    assert "misconception_frequency" in [block["kind"] for block in studio["blocks"]]
    vocabulary = default_report_root_cause_vocabulary()
    assert [metric["metric_key"] for metric in studio["metrics"]] == [
        "home_practice_rate",
        vocabulary.metric_key_for(RootCauseMetricKind.CONFIRMED),
        vocabulary.metric_key_for(RootCauseMetricKind.PROPAGATED),
    ]
    assert all(metric["audience"] != "teacher_only" for metric in studio["metrics"])
    direct, propagated = studio["metrics"][1:]
    assert direct["evidence"][0]["record_id"] == "event:basis-1"
    assert propagated["evidence"][0]["source_table"] == "weakness_propagation"
    assert propagated["evidence"][0]["record_id"].startswith("graph-node-")
    assert "언어(문법) ×" in propagated["evidence"][0]["summary"]
    assert "language.grammar.fortition" not in response.text
    assert "language.grammar.phonological_change" not in response.text
    section_keys = [item["key"] for item in data["unproduced_sections"]]
    assert section_keys == studio["unproduced"]
    assert section_keys == [metric.value for metric in ReportUnproducedMetric]
    assert all(item["reason"] for item in data["unproduced_sections"])


def test_detail_serves_time_series_metrics_through_the_http_assembly_path() -> None:
    with _client_with(_stored_report(include_time_series=True)) as client:
        response = client.get(f"/v1/reports/{REPORT_ID}", headers=HEADERS)

    assert response.status_code == 200
    metrics = _data(response)["studio_data"]["metrics"]
    keys = [metric["metric_key"] for metric in metrics]
    vocabulary = default_report_time_series_vocabulary()
    assert vocabulary.metric_key_for(
        TimeSeriesMetricKind.MONTHLY_GRADED_ITEMS,
        bucket="2026-08",
    ) in keys
    assert vocabulary.metric_key_for(
        TimeSeriesMetricKind.WEEKLY_ACCURACY,
        bucket="2026-08-24",
    ) in keys
    assert vocabulary.metric_key_for(TimeSeriesMetricKind.BASELINE_ACCURACY) in keys
    assert all(metric["audience"] == "guardian" for metric in metrics)
    assert "teacher-metric" not in response.text


def test_detail_without_time_series_snapshot_returns_normally_without_time_series() -> None:
    with _client_with(_stored_report()) as client:
        response = client.get(f"/v1/reports/{REPORT_ID}", headers=HEADERS)

    assert response.status_code == 200
    metrics = _data(response)["studio_data"]["metrics"]
    assert all(not metric["metric_key"].startswith("time_series.") for metric in metrics)


def test_detail_returns_gate_rejection_as_200_status() -> None:
    with _client_with(_stored_report(gate_passed=False)) as client:
        response = client.get(f"/v1/reports/{REPORT_ID}", headers=HEADERS)

    assert response.status_code == 200
    assert _data(response)["status"] == "gate_rejected"


def test_detail_hides_other_tenant_report_as_not_found() -> None:
    with _client_with(_stored_report(tenant_id="tenant-b")) as client:
        response = client.get(f"/v1/reports/{REPORT_ID}", headers=HEADERS)

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


# 강사 수정: 정상·낙관적 잠금 경계·블록 실패
def test_teacher_edit_appends_revision_and_preserves_ai_original() -> None:
    with _client_with(_stored_report()) as client:
        response = client.patch(
            f"/v1/reports/{REPORT_ID}/blocks/{BLOCK_ID}",
            headers=HEADERS,
            json={"base_revision_no": 0, "content": "강사 수정본"},
        )

    assert response.status_code == 200
    revisions = _data(response)["blocks"][0]["revisions"]
    assert [revision["revision_kind"] for revision in revisions] == ["ai_draft", "teacher_edit"]
    assert revisions[0]["ai_original"] == "최초 AI 원문"
    assert revisions[1]["ai_original"] == "최초 AI 원문"
    assert revisions[1]["teacher_edit"] == "강사 수정본"


def test_teacher_edit_rejects_stale_base_revision() -> None:
    with _client_with(_stored_report()) as client:
        first = client.patch(
            f"/v1/reports/{REPORT_ID}/blocks/{BLOCK_ID}",
            headers=HEADERS,
            json={"base_revision_no": 0, "content": "첫 수정"},
        )
        stale = client.patch(
            f"/v1/reports/{REPORT_ID}/blocks/{BLOCK_ID}",
            headers=HEADERS,
            json={"base_revision_no": 0, "content": "뒤늦은 수정"},
        )

    assert first.status_code == 200
    assert stale.status_code == 409
    assert stale.json()["error"]["detail"]["current_revision_no"] == 1


def test_teacher_edit_returns_not_found_for_absent_block() -> None:
    with _client_with(_stored_report()) as client:
        response = client.patch(
            f"/v1/reports/{REPORT_ID}/blocks/{ABSENT_ID}",
            headers=HEADERS,
            json={"base_revision_no": 0, "content": "강사 수정본"},
        )

    assert response.status_code == 404


# 복귀: 정상·대상 경계·리포트 실패
def test_restore_appends_rollback_and_keeps_complete_http_history() -> None:
    with _client_with(_stored_report()) as client:
        edit = client.patch(
            f"/v1/reports/{REPORT_ID}/blocks/{BLOCK_ID}",
            headers=HEADERS,
            json={"base_revision_no": 0, "content": "강사 수정본"},
        )
        restore = client.post(
            f"/v1/reports/{REPORT_ID}/blocks/{BLOCK_ID}/restore",
            headers=HEADERS,
            json={
                "base_revision_no": 1,
                "revert_to_revision_no": 0,
                "teacher_ref": "teacher-1",
            },
        )

    assert edit.status_code == 200
    assert restore.status_code == 200
    revisions = _data(restore)["blocks"][0]["revisions"]
    assert [revision["revision_kind"] for revision in revisions] == [
        "ai_draft",
        "teacher_edit",
        "rollback",
    ]
    assert revisions[0]["ai_original"] == "최초 AI 원문"
    assert revisions[1]["teacher_edit"] == "강사 수정본"
    assert revisions[2]["ai_original"] == "최초 AI 원문"
    assert revisions[2]["revert_to_revision_no"] == 0
    assert revisions[2]["restored_by"] == "teacher-1"
    assert revisions[2]["restored_at"] == LATER.isoformat().replace("+00:00", "Z")


def test_restore_rejects_revision_outside_history() -> None:
    with _client_with(_stored_report()) as client:
        response = client.post(
            f"/v1/reports/{REPORT_ID}/blocks/{BLOCK_ID}/restore",
            headers=HEADERS,
            json={
                "base_revision_no": 0,
                "revert_to_revision_no": 1,
                "teacher_ref": "teacher-1",
            },
        )

    assert response.status_code == 400
    assert response.json()["error"]["detail"] == {"revert_to_revision_no": 1}


def test_restore_returns_not_found_for_absent_report() -> None:
    with _client_with(_stored_report()) as client:
        response = client.post(
            f"/v1/reports/{ABSENT_ID}/blocks/{BLOCK_ID}/restore",
            headers=HEADERS,
            json={
                "base_revision_no": 0,
                "revert_to_revision_no": 0,
                "teacher_ref": "teacher-1",
            },
        )

    assert response.status_code == 404


def test_report_router_does_not_expose_generation_rewrite_or_send_routes() -> None:
    with _client_with(_stored_report()) as client:
        responses = [
            client.post(f"/v1/reports/{REPORT_ID}/generate", headers=HEADERS),
            client.post(f"/v1/reports/{REPORT_ID}/rewrite", headers=HEADERS),
            client.post(f"/v1/reports/{REPORT_ID}/send", headers=HEADERS),
        ]

    assert [response.status_code for response in responses] == [404, 404, 404]
