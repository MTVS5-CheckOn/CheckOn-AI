"""`topic=schedule` → `template_only` 경로 — 진리표 고정 (03 §C 상황 2 · error_codes §2.1).

🔴 **이 파일의 핵심은 진리표 2행이다** — `(schedule, 근거 0건)`이 `template_only`로
가는지. `rejected_insufficient`가 나오면 **topic 분기가 근거 선검사 뒤로 밀린 것**이다.

종전에는 `inquiry.topic`이 파이프라인 어디에서도 소비되지 않아 시간표 문의에 **학습 근거
기반 성적 초안**이 생성됐다.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

# ⚠ 픽스처를 복제하지 않고 재사용한다 — 같은 계약 §4-① 예시를 두 곳에 두면 갈린다.
#   `tests/`에 `__init__.py`가 없어 pytest는 테스트 모듈을 **최상위 이름**으로 올린다.
from test_counsel_router import _HEADERS, _REQUEST

from ai.api.app import create_app
from ai.api.routers.counsel import (
    reset_counsel_stores,
    set_counsel_provider,
)


class _SpyProvider:
    """plan·write 호출 수를 센다 — template_only가 **LLM 0회**인지 보려면 필요하다."""

    def __init__(self) -> None:
        self.calls = 0

    async def plan(self, **kwargs: Any) -> dict[str, list[str]]:  # noqa: ANN401
        del kwargs
        self.calls += 1
        return {}

    async def write(self, **kwargs: Any) -> str:  # noqa: ANN401
        del kwargs
        self.calls += 1
        return "생성된 문장입니다."


@pytest.fixture(autouse=True)
def _isolate() -> Iterator[None]:
    reset_counsel_stores()
    yield
    reset_counsel_stores()


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(create_app()) as test_client:
        yield test_client


def _post(client: TestClient, *, topic: str, facts: bool) -> dict[str, Any]:
    body = json.loads(json.dumps(_REQUEST))
    body["inquiry"]["topic"] = topic
    if not facts:
        body["context"]["facts"] = []
    headers = dict(_HEADERS)
    headers["Idempotency-Key"] = f"t1:counsel:{topic}-{facts}"
    response = client.post("/v1/counsel/drafts", json=body, headers=headers)
    assert response.status_code == 202, response.text
    job_id = response.json()["data"]["job_id"]

    got = client.get(f"/v1/counsel/drafts/{job_id}", headers=headers)
    assert got.status_code == 200, got.text
    result: dict[str, Any] = got.json()["data"]["result"]
    result["_job_id"] = job_id
    return result


# ── 🔴 진리표 4행 ────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("topic", "facts", "status", "reason"),
    [
        ("schedule", True, "template_only", "no_data_topic"),
        # 🔴 이 행이 이 작업의 핵심이다 — 실패하면 topic 분기가 근거 선검사 뒤로 밀린 것.
        ("schedule", False, "template_only", "no_data_topic"),
        ("grade", False, "rejected_insufficient", "no_citable_evidence"),
        ("grade", True, "generated", None),
    ],
)
def test_topic_and_evidence_truth_table(
    client: TestClient, topic: str, facts: bool, status: str, reason: str | None
) -> None:
    """`topic` × 근거 유무의 4조합이 어디로 수렴하는지 통째로 고정한다.

    🔴 **`(schedule, 근거 0건)` 행이 순서를 고정한다.** 시간표 문의 + 신규생일 때
    `rejected_insufficient`("아직 데이터를 모으는 중이에요")가 나가면 **조용히 틀린
    응답**이다 — 시간표 답변에 학습 데이터는 애초에 필요 없다(03 §C 상황 2).
    """
    result = _post(client, topic=topic, facts=facts)

    assert result["draft_status"] == status
    assert result["status_reason"] == reason


# ── template_only의 형태 ────────────────────────────────────────


def test_template_only_carries_no_text_or_citations(client: TestClient) -> None:
    """🔴 **안내 문구는 AI가 만들지 않는다**(error_codes §2.7 규칙 ③).

    LLM이 문장을 지어내면 근거 0건 산출물이라 불변식 2 위반이고, 통과시킬 근거가 없어
    게이트를 세울 수 없다. 문구의 원본은 §2.1 "백엔드 표시 문구" 열이며 BE 소유다.
    """
    result = _post(client, topic="schedule", facts=True)

    assert result["text"] is None
    assert result["citations"] == []
    assert "message" not in result, "AI가 표시 문구를 실어 보내고 있다"


def test_template_only_still_reports_labels(client: TestClient) -> None:
    """라벨 반영은 그대로다 — 기존 조기 반환 경로(`rejected_insufficient`)와 같은 형태."""
    result = _post(client, topic="schedule", facts=True)
    assert result["labels_applied"]


def test_template_only_calls_no_llm(client: TestClient) -> None:
    """🔴 provider 호출 수 **0** — enqueue·runner를 타지 않는다."""
    spy = _SpyProvider()
    set_counsel_provider(spy)

    _post(client, topic="schedule", facts=True)

    assert spy.calls == 0, "template_only인데 LLM을 불렀다"


def test_generated_path_still_calls_llm(client: TestClient) -> None:
    """대조군 — 데이터 유관 문의는 여전히 LLM을 탄다(분기가 과잉이 아님)."""
    spy = _SpyProvider()
    set_counsel_provider(spy)

    _post(client, topic="grade", facts=True)

    assert spy.calls >= 1


# ── refine 미등록 ───────────────────────────────────────────────


def test_template_only_cannot_be_refined(client: TestClient) -> None:
    """🔴 **다듬기로는 되돌릴 수 없다**(04 §3.9) — 다듬을 원본이 없다.

    `_drafts`에 등록하지 않으므로 그 `job_id`로 refine을 부르면 404다. 오분류였다면
    정정 경로(confirmations + 새 키 재요청)를 탄다.
    """
    result = _post(client, topic="schedule", facts=True)

    response: httpx.Response = client.post(
        f"/v1/counsel/drafts/{result['_job_id']}/refine",
        json={"instruction": "더 짧게"},
        headers=_HEADERS,
    )
    assert response.status_code == 404
