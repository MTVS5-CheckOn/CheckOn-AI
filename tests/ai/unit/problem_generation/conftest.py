"""문제출제 단위 테스트에서 공용 테스트 대역 경로를 등록한다."""

import sys
from pathlib import Path

import pytest

from ai.runtime.tracing import TRACING_ENV_SYNONYMS

_FAKES_DIR = Path(__file__).parents[2] / "fakes"
sys.path.insert(0, str(_FAKES_DIR))


@pytest.fixture(autouse=True)
def _disable_langsmith_tracing(monkeypatch: pytest.MonkeyPatch) -> None:
    """기동 가드가 보는 추적 env에서 문제생성 게이트웨이 테스트를 격리한다.

    판정 정본이 ``external_tracing_active()``로 단일화돼(09 §2-16 후속 1)
    ``.env``·``LlmSettings``는 가드에 관여하지 않는다 — env만 끄면 된다.
    """
    for name in TRACING_ENV_SYNONYMS:
        monkeypatch.delenv(name, raising=False)
