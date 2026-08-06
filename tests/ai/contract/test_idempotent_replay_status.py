"""🔴 멱등 재반환 상태코드 — **최초 요청과 같은 코드**(동기 200 · 비동기 202).

04 §2.3의 종전 표기는 *"같은 바디 = 기존 결과를 200으로 반환"* 이었다. 그 문장은 **동기
엔드포인트만 있던 시절**에 쓰였고, 비동기 202가 생기면서 뒤처졌다.

🔴 **202 자리에 200을 주면 거짓말이 된다.** 202는 *"접수했고 아직 안 끝났을 수 있다"* 이고
200은 *"결과가 준비됐다"* 인데, 멱등 재요청 시점에 잡이 `running`일 수 있다. BE는 200을 보고
결과를 읽으러 가고, 없으면 "다시 시도"를 그린다 — 강사가 누르면 산출물이 2개 생긴다.

⚠ **틀린 것은 문서였다** — 구현 세 축은 이미 일관됐다(실측 8/7). 그래서 이 파일이 잡는 것은
*"구현을 고쳐라"* 가 아니라 **"다음에 또 갈리지 않게"** 다. 문서만 고치면 같은 일이 반복된다.

⚠ 엔드포인트별 라운드트립은 각 통합 테스트가 이미 본다. 여기서는 **세 축을 한 자리에
모아** 상태코드 규약만 대조한다 — 규약이 한 곳에서 보이지 않으면 다음 엔드포인트가
자기 마음대로 정한다.

⚠ **`POST /v1/classify`는 여기 없다 — 빠뜨린 게 아니다.** 그 라우터는 `Idempotency-Key`를
**아예 받지 않는다**(`classify.py` 모듈 docstring: *"부작용 없는 동기 호출이고 멱등 저장이
없다"*). 실측하면 같은 바디를 두 번 보내도 `meta.execution_id`가 다르다 — **재반환이 아니라
재실행**이라 이 규약의 대상이 아니다.
"""

from __future__ import annotations

from collections.abc import Iterator
from io import BytesIO
from typing import Any

import pandas as pd  # type: ignore[import-untyped]
import pytest
from fastapi.testclient import TestClient

from ai.api.app import create_app
from ai.api.routers.classify import reset_inquiry_class_store
from ai.api.routers.counsel import reset_counsel_stores
from ai.api.routers.detect import reset_detection_store, reset_idempotency_store
from ai.api.routers.imports import reset_import_stores
from ai.db.store_factory import reset_shared_agent_runtime


def _headers(key: str) -> dict[str, str]:
    return {
        "X-Tenant-Id": "t1",
        "X-Request-Id": f"rq-{key}",
        "Idempotency-Key": key,
    }


def _golden(path: str) -> Any:  # noqa: ANN401 — 테스트 모듈을 픽스처 출처로 재사용
    """기존 통합 테스트의 요청 픽스처를 **파일 경로로** 불러 그대로 쓴다.

    ⚠ 바디를 새로 짓지 않는다 — 두 곳이 갈리면 "같은 요청"이라는 말이 깨진다.
    ⚠ `tests`는 패키지가 아니라 `import tests.…`가 안 된다(`pythonpath = ["src"]`) —
      경로 로딩이 이 저장소의 방식이다(`evaluation/counsel_llm_smoke.py`의 `_golden`과 같다).
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location(path.replace("/", "."), path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _xlsx() -> bytes:
    buffer = BytesIO()
    pd.DataFrame(
        {
            "원생명": ["김철수"],
            "반": ["A1"],
            "등원일": ["2026-03-02"],
            "상태": ["재원"],
            "동의": ["동의"],
        }
    ).to_excel(buffer, index=False)
    return buffer.getvalue()


@pytest.fixture(autouse=True)
def _isolate() -> Iterator[None]:
    _reset_all()
    yield
    _reset_all()


def _reset_all() -> None:
    reset_idempotency_store()  # 🔴 네 축이 **같은 멱등 저장소**를 쓴다 — 키가 새면 재반환이 섞인다
    reset_detection_store()
    reset_inquiry_class_store()
    reset_shared_agent_runtime()  # A·B 공용 잡 원장(99 ㊒)
    reset_counsel_stores()
    reset_import_stores()


# ── 동기 = 200 ────────────────────────────────────────────────────


def test_detect_replays_with_200() -> None:
    """동기 엔드포인트는 최초도 재반환도 **200**이다."""
    source = _golden("tests/ai/integration/test_idempotency_restart.py")
    body = source._payload()
    headers = _headers("t1:replay:detect")

    with TestClient(create_app()) as client:
        first = client.post("/v1/detect", json=body, headers=headers)
        second = client.post("/v1/detect", json=body, headers=headers)

    assert first.status_code == 200, first.text
    assert second.status_code == 200, (
        f"detect 멱등 재반환이 {second.status_code}다 — 최초와 같은 200이어야 한다(04 §2.3)"
    )
    assert second.json() == first.json(), "재반환 바디가 최초와 다르다"


# ── 비동기 = 202 ──────────────────────────────────────────────────


def test_counsel_drafts_replays_with_202() -> None:
    """🔴 `POST /v1/counsel/drafts`는 최초도 재반환도 **202**다 — 200이면 거짓말이다."""
    router_test = _golden("tests/ai/integration/test_counsel_router.py")
    headers = _headers("t1:replay:counsel")

    with TestClient(create_app()) as client:
        first = client.post("/v1/counsel/drafts", json=router_test._REQUEST, headers=headers)
        second = client.post("/v1/counsel/drafts", json=router_test._REQUEST, headers=headers)

    assert first.status_code == 202, first.text
    assert second.status_code == 202, (
        f"counsel 멱등 재반환이 {second.status_code}다 — 202여야 한다. 200은 '결과가 "
        "준비됐다'는 뜻인데 그 시점에 잡이 running일 수 있다(04 §2.3)"
    )
    assert second.json() == first.json()


def test_imports_replays_with_202() -> None:
    """🔴 `POST /v1/imports`도 **202**다 — 데코레이터 `status_code=202`가 재반환에 걸린다."""
    from ai.api.routers import imports as imports_router

    class _FakeLoader:
        """`test_imports_router.FakeSourceLoader`와 같은 계약 — `load(url) -> bytes`."""

        def load(self, source_url: str) -> bytes:
            del source_url
            return _xlsx()

    imports_router.set_import_stores(source_loader=_FakeLoader())
    headers = _headers("t1:replay:imports")
    body = {"source_url": "s3://replay.xlsx", "filename": "replay.xlsx"}

    with TestClient(create_app()) as client:
        first = client.post("/v1/imports", json=body, headers=headers)
        second = client.post("/v1/imports", json=body, headers=headers)

    assert first.status_code == 202, first.text
    assert second.status_code == 202, (
        f"imports 멱등 재반환이 {second.status_code}다 — 202여야 한다(04 §2.3)"
    )
    assert second.json() == first.json()


# ── 규약이 한 곳에서 보이는가 ─────────────────────────────────────


def test_the_contract_states_the_rule_in_one_place() -> None:
    """⚠ 04 §2.3이 **표로** 규약을 들고 있다 — 문장이 사라지면 다음 사람이 다시 정한다.

    이 단정이 red가 되면 문서가 지워졌거나 표기가 바뀐 것이다. 그때는 이 파일의 기대값이
    아니라 **왜 바뀌었는지**를 먼저 봐야 한다.
    """
    from pathlib import Path

    contract = Path("docs/04_api_contract.md").read_text(encoding="utf-8")
    assert "최초 요청과 같은 상태코드" in contract, (
        "04 §2.3의 멱등 재반환 규약 문장이 사라졌다 — 문서와 구현이 다시 갈릴 자리다"
    )
