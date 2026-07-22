"""AI_RUN ↔ VersionSet 1:1 대조 (D-② 커밋⑤).

AI_RUN의 버전 컬럼 집합과 contracts/execution.VersionSet의 필드 집합은 정확히 같아야
한다 — 재현성(불변식 8)의 정본은 VersionSet이고 AI_RUN은 그걸 그대로 적재하기 때문이다.
한쪽만 버전 키가 늘거나 줄면 과거 실행을 재현할 수 없다. 이 검사가 그 드리프트를 막는다.

RunMetadata(AI_RUN 적재 DTO)도 VersionSet 필드를 전부 품는지 함께 본다 — 적재 매핑
(_ai_run_orm)이 조용히 한 컬럼을 빠뜨리지 못하게.
"""

from __future__ import annotations

from ai.contracts.execution import RunMetadata, VersionSet
from ai.db.models import AiRun

_SUFFIX = "_version"


def _versionset_fields() -> set[str]:
    return set(VersionSet.model_fields)


def _ai_run_version_columns() -> set[str]:
    return {c.name for c in AiRun.__table__.columns if c.name.endswith(_SUFFIX)}


def test_versionset_is_all_version_suffixed() -> None:
    """VersionSet은 버전 키만 담는다 — 접미사 규약이 깨지면 아래 대조가 무의미."""
    assert _versionset_fields()
    assert all(f.endswith(_SUFFIX) for f in _versionset_fields())


def test_ai_run_version_columns_match_versionset() -> None:
    """AI_RUN 버전 컬럼 == VersionSet 필드 (1:1, 양방향)."""
    cols = _ai_run_version_columns()
    fields = _versionset_fields()
    assert cols == fields, (
        f"AI_RUN에만: {sorted(cols - fields)} · VersionSet에만: {sorted(fields - cols)}"
    )


def test_run_metadata_covers_versionset() -> None:
    """RunMetadata(적재 DTO)가 VersionSet 버전 필드를 모두 품는다."""
    run_fields = set(RunMetadata.model_fields)
    assert _versionset_fields() <= run_fields, (
        f"RunMetadata 누락: {sorted(_versionset_fields() - run_fields)}"
    )


def test_ten_version_keys() -> None:
    """공통 6 + [PART_B] 4 = 10 (06_erd 라인 7 · execution.py VersionSet 주석)."""
    assert len(_versionset_fields()) == 10
    assert len(_ai_run_version_columns()) == 10
