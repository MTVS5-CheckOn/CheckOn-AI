"""버전 축이 **실제 응답과 원장에** 반영되는가 (지시서 70-R2).

🔴 **상수 소스를 grep하는 것으로는 증명되지 않는다** — 응답 조립과 원장 적재가 그 상수를
실제로 읽는지는 **응답과 `AI_RUN` 행**을 봐야 안다(#34에서 같은 형태를 실측했다:
*"메시지는 「리포트에」인데 보는 것은 「소스에」"*).

**이번에 올린 축과 안 올린 축을 갈라 고정한다** — 무엇이 왜 안 올랐는지가 다음 사람에게
필요하다.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any, Final

import pytest
from fastapi.testclient import TestClient

from ai.api.app import create_app
from ai.api.routers.detect import (
    reset_detection_store,
    reset_idempotency_store,
    set_detection_store,
)
from ai.db.repositories.detection_store import InMemoryDetectionStore
from ai.evaluation.fake_snapshot import fixture_composite_risk, to_payload

_HEADERS: Final = {
    "X-Tenant-Id": "teacher_alias_001",
    "X-Request-Id": "req-ver",
    "Idempotency-Key": "teacher_alias_001:version-axes",
}

#: 🔴 **8/12에 올린 축** — 판정과 요청 계약이 바뀌었다(99 #43·#44).
_BUMPED: Final = {"engine": "detection-rules-0.3", "contract": "0.2"}
#: ⚠ **안 올린 축** — DB 스키마·피처 산식·파이프라인 구조가 그대로다.
_UNCHANGED: Final = {"pipeline": "0.1.0", "schema": "0.1"}
#: FEATURE_WEEK 적재 피처 버전 — `_week_metrics`의 키·산식 무변경.
_FEATURE_VERSION: Final = "0.1"


@pytest.fixture
def runs() -> Iterator[InMemoryDetectionStore]:
    """🔴 **`AI_RUN`은 감지 원장 저장소로 간다** — `_run_store`가 아니다(실측).

    ⚠ `_run_store`에 대역을 꽂고 0건을 보고 *"원장을 안 쓴다"* 로 읽을 뻔했다 —
    detect는 `LedgerWrite`(AI_RUN + SIGNAL + FEATURE_WEEK)를 **한 묶음**으로 적재한다.
    """
    reset_idempotency_store()
    reset_detection_store()
    store = InMemoryDetectionStore()
    set_detection_store(store)
    yield store
    reset_idempotency_store()
    reset_detection_store()


def _post(client: TestClient) -> dict[str, Any]:
    response = client.post(
        "/v1/detect", json=to_payload(fixture_composite_risk()), headers=_HEADERS
    )
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def test_the_response_carries_the_bumped_versions(runs: InMemoryDetectionStore) -> None:
    """🔴 **응답 `meta.versions`** — 소스가 아니라 나간 값을 본다."""
    del runs
    with TestClient(create_app()) as client:
        versions = _post(client)["meta"]["versions"]
    for key, expected in _BUMPED.items():
        assert versions[key] == expected, f"{key}가 {versions[key]}다"
    for key, expected in _UNCHANGED.items():
        assert versions[key] == expected, f"안 올린 축 {key}가 {versions[key]}로 바뀌었다"


def test_the_ledger_row_carries_the_bumped_versions(runs: InMemoryDetectionStore) -> None:
    """🔴 **`AI_RUN` 행** — 응답만 맞고 원장이 낡으면 재현이 갈린다(불변식 8)."""
    with TestClient(create_app()) as client:
        versions = _post(client)["meta"]["versions"]

    assert len(runs.runs) == 1, f"AI_RUN이 {len(runs.runs)}건이다"
    row = runs.runs[0]
    assert row.engine_version == _BUMPED["engine"]
    assert row.contract_version == _BUMPED["contract"]
    assert row.pipeline_version == _UNCHANGED["pipeline"]
    assert row.schema_version == _UNCHANGED["schema"]
    #: 🔴 **응답과 원장이 같은 값을 말한다** — 두 자리가 갈리면 어느 쪽이 정본인지 모른다.
    assert row.threshold_version == versions["threshold"], (
        "응답과 원장의 threshold 버전이 다르다"
    )


def test_the_threshold_version_is_the_injected_config(runs: InMemoryDetectionStore) -> None:
    """threshold는 **주입된 설정값**이다 — 상수가 아니라 실행 설정에서 온다."""
    from ai.api.routers.detect import detection_versions  # noqa: PLC0415
    from ai.detection.thresholds import default_threshold_config  # noqa: PLC0415

    #: ⚠ `ThresholdConfig.version`은 **정수**(3)이고 응답에는 `default-v3` 문자열이 실린다 —
    #:   변환은 `detection_versions()`가 한다. **그 함수를 거쳐서** 비교한다(형태를 손으로 짜면
    #:   변환 규칙이 두 곳에 산다).
    expected = detection_versions(default_threshold_config()).threshold_version
    assert expected is not None and str(default_threshold_config().version) in expected

    with TestClient(create_app()) as client:
        versions = _post(client)["meta"]["versions"]
    assert versions["threshold"] == expected
    assert runs.runs[0].threshold_version == expected


def test_the_feature_version_did_not_move() -> None:
    """🔴 **`FEATURE_WEEK` 피처 버전은 안 올렸다** — 산식이 그대로이기 때문이다.

    ⚠ 올리면 축적분이 **통째로 다른 버전**이 되어 baseline read-path가 과거를 못 읽는다.
    부재형 셋의 판정 입력은 요청의 `detection_evidence`이지 이 피처가 아니다.
    """
    from ai.api.routers import detect as detect_router  # noqa: PLC0415

    assert detect_router._FEATURE_VERSION == _FEATURE_VERSION
    rows = detect_router._build_feature_weeks(fixture_composite_risk())
    assert rows, "피처 행이 하나도 없다 — 이 검사가 아무것도 안 본다"
    assert {row.feature_version for row in rows} == {_FEATURE_VERSION}


def test_the_spec_example_matches_the_real_versions() -> None:
    """🔴 **문서의 detect 응답 예시가 실제 실행값과 같은가** (지시서 70-R2).

    ⚠ 종전 `09` 예시는 `pipeline 1.0.0`·`threshold v4`·`contract 1.0`이었다 —
    **한 번도 실제였던 적이 없는 자리표시자**인데 「현재 실행값」처럼 읽혔다.
    ⚠ `04`의 10키 블록은 **problem_generation 형태 예시**라 여기서 안 본다(그쪽엔
    `engine`이 detect 값으로 들어가 있던 문서 오류가 있어 8/12에 정정했다).
    """
    from pathlib import Path  # noqa: PLC0415

    from ai.api.routers.detect import detection_versions  # noqa: PLC0415

    spec = (
        Path(__file__).resolve().parents[3] / "docs" / "part_a" / "09_detect_spec.md"
    ).read_text(encoding="utf-8")
    versions = detection_versions().model_dump()
    for key in ("pipeline", "engine", "schema", "contract", "threshold"):
        value = versions[f"{key}_version"]
        assert f'"{key}": "{value}"' in spec, (
            f"09 응답 예시의 {key}가 실제({value})와 다르다"
        )


def test_the_demo_artifact_matches_the_real_versions() -> None:
    """자동 재현 산출물도 같은 값이어야 한다 — 재생성을 빠뜨리면 red."""
    import json  # noqa: PLC0415
    from pathlib import Path  # noqa: PLC0415

    from ai.api.routers.detect import detection_versions  # noqa: PLC0415

    demo = json.loads(
        (
            Path(__file__).resolve().parents[3]
            / "docs"
            / "part_a"
            / "examples"
            / "detect_demo_response.json"
        ).read_text(encoding="utf-8")
    )
    versions = detection_versions().model_dump()
    for key in ("pipeline", "engine", "schema", "contract"):
        assert demo["meta"]["versions"][key] == versions[f"{key}_version"], key
