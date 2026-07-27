"""LLM 단위 테스트에서 공용 테스트 대역 경로를 등록한다."""

import sys
from pathlib import Path

_FAKES_DIR = Path(__file__).parents[2] / "fakes"
sys.path.insert(0, str(_FAKES_DIR))
