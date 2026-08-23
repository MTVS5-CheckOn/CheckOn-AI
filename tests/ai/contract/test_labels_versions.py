"""🔴 라벨 응답의 `meta.versions` 가 **라벨 것**이다 (99 #191 · 04 §2.2).

⚠ 🔴 **왜 이 검사가 있나** — #382 가 라우터에 `counsel_versions()` 를 꽂았다. 그러면
`meta.versions.prompt` 가 **counsel 프롬프트 버전**을 말하고, BE 가 그 값을 보므로
**거짓말이 나간다**(불변식 8 — 재현성).

🔴 **두 축을 함께 잰다.** 「라벨 값과 같다」만 재면 **두 값이 우연히 같을 때** 아무것도 안
재고 통과한다(앵커 폭 — №52 §4-2 가 세운 형태). ⇒ 「counsel 것과 **다르다**」도 잰다.
⚠ 언젠가 두 프롬프트 버전이 같은 문자열이 될 수 있다 — 그때도 `engine_version` 이 갈리므로
이 검사는 계속 뜻이 있다.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any, Final

import pytest
from fastapi.testclient import TestClient

from ai.api.app import create_app
from ai.api.routers import labels as labels_router
from ai.composition.counsel.versions import counsel_versions
from ai.composition.labels.prompt import PROMPT_VERSION
from ai.composition.labels.versions import labels_versions

_HEADERS: Final = {"X-Tenant-Id": "t1", "X-Request-Id": "r1"}
_BODY: Final[dict[str, Any]] = {
    "guardian_ref": "gd_11b0",
    "history": [
        {
            "record_id": f"cm_{index}",
            "direction": "inbound",
            "text": text,
            "at": "2026-06-12T10:11:00+09:00",
        }
        for index, text in enumerate(
            (
                "숫자로 정리해 주세요",
                "점수 추이 표로 부탁드려요",
                "지난주 결과가 궁금합니다",
                "표로 보여 주시면 좋겠어요",
                "이번 달 통계도 알려 주세요",
            ),
            start=88,
        )
    ],
}


@pytest.fixture(autouse=True)
def _reset() -> Iterator[None]:
    labels_router.reset_label_suggest_provider()
    yield
    labels_router.reset_label_suggest_provider()


def test_the_declared_prompt_version_is_the_label_one() -> None:
    """① `labels_versions()` 가 **라벨 프롬프트 버전**을 싣는다."""
    assert labels_versions().prompt_version == PROMPT_VERSION


def test_the_label_versions_differ_from_counsel() -> None:
    """② 🔴 **counsel 것과 다르다** — 이게 앵커 폭이다.

    ⚠ ①만 있으면 라우터가 `counsel_versions()` 를 써도 **두 값이 같기만 하면** 통과한다.
    """
    labels, counsel = labels_versions(), counsel_versions()
    assert labels.engine_version != counsel.engine_version, (
        f"엔진 이름이 같다({labels.engine_version}) — 원장에서 두 경로를 못 가른다"
    )


def test_the_response_carries_the_label_versions() -> None:
    """③ 🔴 **응답이 실제로 그 값을 싣는다** — 함수만 재면 배선을 못 잡는다.

    ⚠ 그게 #382 의 결함이었다: 함수는 멀쩡한데 라우터가 **다른 함수를 불렀다.**
    """
    with TestClient(create_app()) as client:
        response = client.post("/v1/labels/suggest", headers=_HEADERS, json=_BODY)

    assert response.status_code == 200, response.text
    versions = response.json()["meta"]["versions"]
    assert versions["prompt"] == PROMPT_VERSION, (
        f"라벨 응답이 프롬프트 버전 {versions['prompt']!r} 을 말한다 — "
        f"라벨은 {PROMPT_VERSION!r} 이다(counsel 것을 빌려 쓰고 있는지 보라)"
    )
    assert versions["engine"] == labels_versions().engine_version
    assert versions["engine"] != counsel_versions().engine_version, (
        "응답이 counsel 엔진 이름을 말한다 — 라우터가 counsel_versions() 를 부른다"
    )
