"""baseline read-path 멀티데이 e2e — day1 10주 → day2 1주 증분 (D-②b 핵심 증명).

백엔드가 실운영처럼 "day1=최근 10주 스냅숏, day2부터=지난 1주 증분"을 보내는 시나리오가
실제 API에서 끝까지 도는 것을 증명한다. memory 백엔드(같은 프로세스 축적)로 수행 —
실 PG 재시작 생존은 test_pg_restart(integration).

대조군: 증분 요청(+read-path)의 판정이 같은 시점의 '10주 통짜' 요청 판정과 일치 →
read-path 정확성. read-path 미적용이면 1주 증분은 R1(2주 연속)이 미검출(미탐)됨을 함께 증명.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from ai.api.app import create_app
from ai.api.routers import detect as detect_router
from ai.contracts.detection import DetectRequest, SignalType
from ai.db.repositories.detection_store import InMemoryDetectionStore
from ai.detection.engine import detect
from ai.evaluation.fake_snapshot import (
    StudentPlan,
    alert_open,
    build_detect_request,
    take_single_week,
    take_week_range,
    to_payload,
)

_W0 = "2026-07-06"  # day1 판정 주(월)
_W1 = "2026-07-13"  # day2 판정 주(월) = W0 + 1주


def _declining_plan() -> StudentPlan:
    # 11주 하락 궤적(0.85→0.45) — R1 acc_drop(2주 연속 하락) 재료.
    return StudentPlan(
        student_ref="st_dec",
        class_ref="cl_a1",
        weeks=11,
        accuracy=tuple(round(0.85 - 0.04 * i, 4) for i in range(11)),
    )


def _full() -> DetectRequest:
    return build_detect_request(week_start=_W1, seed=5, students=[_declining_plan()])


def _headers(week: str) -> dict[str, str]:
    return {"X-Tenant-Id": "t1", "X-Request-Id": "r", "Idempotency-Key": f"t1:{week}"}


def _sigset(body: dict[str, Any]) -> set[tuple[str, str, str]]:
    return {
        (s["student_ref"], s["signal_type"], s["lifecycle"])
        for s in body["data"]["signals"]
    }


@pytest.fixture
def client() -> Iterator[TestClient]:
    detect_router.reset_idempotency_store()
    detect_router.reset_detection_store()
    detect_router.reset_brief_provider()
    yield TestClient(create_app())
    detect_router.reset_idempotency_store()
    detect_router.reset_detection_store()
    detect_router.reset_brief_provider()


def test_day1_full_snapshot_accumulates_feature_weeks(client: TestClient) -> None:
    """day1 10주 스냅숏 → acc_drop(new) + FEATURE_WEEK 10주 축적."""
    day1 = take_week_range(_full(), week_start=_W0, weeks_back=10)
    resp = client.post("/v1/detect", json=to_payload(day1), headers=_headers(_W0))
    assert resp.status_code == 200
    assert _sigset(resp.json()) == {("st_dec", "acc_drop", "new")}
    # 모듈 전역 store에 10주분 축적(같은 프로세스에서 day2가 재사용)
    store = detect_router._detection_store
    assert isinstance(store, InMemoryDetectionStore)
    assert len(store.feature_weeks) == 10


def test_increment_readpath_matches_full_snapshot(client: TestClient) -> None:
    """핵심: day2 1주 증분(+read-path) 판정 == 같은 시점 10주 통짜 판정."""
    full = _full()
    day1 = take_week_range(full, week_start=_W0, weeks_back=10)
    client.post("/v1/detect", json=to_payload(day1), headers=_headers(_W0))

    # day2 — day1 경보를 alert_context에 반영(ongoing 유도)
    context = (alert_open("st_dec", SignalType.ACC_DROP),)
    day2_incremental = take_single_week(full, week_start=_W1).model_copy(
        update={"alert_context": context}
    )
    day2_full = take_week_range(full, week_start=_W1, weeks_back=10).model_copy(
        update={"alert_context": context}
    )

    # 증분 → API(read-path가 축적분으로 baseline 구성)
    incremental = _sigset(
        client.post("/v1/detect", json=to_payload(day2_incremental), headers=_headers(_W1)).json()
    )
    # 통짜 → 순수 엔진(요청에 10주 전부 있어 read-path 불필요)
    full_snapshot = {
        (s.student_ref, s.signal_type.value, s.lifecycle.value)
        for s in detect(day2_full).signals
    }
    assert incremental == full_snapshot == {("st_dec", "acc_drop", "ongoing")}


def test_increment_without_readpath_underdetects() -> None:
    """read-path 미적용이면 1주 증분은 R1(2주 연속)이 skip → 미탐(read-path 필요성 증명)."""
    day2_incremental = take_single_week(_full(), week_start=_W1)
    assert detect(day2_incremental).signals == ()  # stored 미주입 → baseline 반쪽 → 미검출


def test_stored_features_not_injected_is_byte_identical() -> None:
    """골든·데모 무변경의 근거 — stored_features 미주입 시 detect는 완전 동일."""
    request = take_week_range(_full(), week_start=_W1, weeks_back=10)
    assert detect(request) == detect(request, stored_features=None)
    assert detect(request) == detect(request, stored_features={})
