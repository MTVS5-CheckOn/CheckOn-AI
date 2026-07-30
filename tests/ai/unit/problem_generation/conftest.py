"""문제출제 단위 테스트에서 공용 테스트 대역 경로를 등록한다."""

import sys
from pathlib import Path

import pytest

from ai.llm import gateway as gateway_module
from ai.llm.settings import LlmSettings

_FAKES_DIR = Path(__file__).parents[2] / "fakes"
sys.path.insert(0, str(_FAKES_DIR))


@pytest.fixture(autouse=True)
def _disable_langsmith_tracing(monkeypatch: pytest.MonkeyPatch) -> None:
    """문제생성 게이트웨이 테스트를 로컬 `.env`의 추적 설정에서 격리한다."""
    settings = LlmSettings(langsmith_tracing=False, _env_file=None)
    monkeypatch.setattr(gateway_module, "get_llm_settings", lambda: settings)
