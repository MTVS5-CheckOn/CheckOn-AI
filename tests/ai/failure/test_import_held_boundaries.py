"""명시적 보류 경계 — 변환기·스토리지 fetch는 NotImplementedError로 경계 고정 (§6.3).

이번 단계에서 안 만드는 것을 테스트로 못박는다 — 조용히 반쯤 구현하지 않았음을 증명.
"""

from __future__ import annotations

import pytest

from ai.contracts.imports import MappingColumn, MappingPreview
from ai.import_mapping.job_store import StubSourceLoader
from ai.import_mapping.profiling import SourceProfile
from ai.import_mapping.transform import run_transform


def test_transformer_is_held() -> None:
    profile = SourceProfile(filename="f.xlsx", sheets=())
    preview = MappingPreview(
        spec_version=1, reused=False, columns=(MappingColumn(source="x", target="score"),)
    )
    with pytest.raises(NotImplementedError):
        run_transform(profile, preview)


def test_storage_fetch_is_held() -> None:
    with pytest.raises(NotImplementedError):
        StubSourceLoader().load("s3://x")
