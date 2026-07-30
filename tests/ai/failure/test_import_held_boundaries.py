"""AI 범위 경계 — 변환기 부재(§4)·스토리지 fetch 보류(§6.3)를 테스트로 못박는다.

**변환기는 '후속 보류'가 아니라 '범위 밖'이 됐다**(2026-07-30) — 전체 행 변환·행별 검증·
집계·산출물 저장은 백엔드 소유다. 그래서 `run_transform` 스텁을 되살리지 않았는지를
모듈 부재로 검사한다(NotImplementedError 스텁 → 부재 회귀로 전환).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from ai.import_mapping.job_store import StubSourceLoader

_SRC = Path(__file__).resolve().parents[3] / "src" / "ai"


def test_transformer_module_is_absent() -> None:
    """import_mapping/transform.py가 없다 — 변환기는 AI 범위 밖(10 §4)."""
    assert importlib.util.find_spec("ai.import_mapping.transform") is None
    assert not (_SRC / "import_mapping" / "transform.py").exists()


def test_no_row_transform_entrypoint_in_src() -> None:
    """전체 행 변환 진입점이 되살아나면 실패 — 소유 분계 회귀(검사 경로는 파일 수로 보장)."""
    files = sorted(p for p in _SRC.rglob("*.py") if "__pycache__" not in p.parts)
    assert files, "src/ai에서 .py를 하나도 찾지 못했다 — 검사 경로가 끊겼다"
    offenders = [
        str(p.relative_to(_SRC)) for p in files if "run_transform" in p.read_text(encoding="utf-8")
    ]
    assert not offenders, f"행 변환 진입점이 되살아났다: {offenders}"


def test_storage_fetch_is_held() -> None:
    with pytest.raises(NotImplementedError):
        StubSourceLoader().load("s3://x")
