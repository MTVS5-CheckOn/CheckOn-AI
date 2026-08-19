"""성취기준 코드 레지스트리의 스키마·fail-closed 검증."""

from __future__ import annotations

from pathlib import Path

import pytest

from ai.diagnosis.curriculum_standards import (
    CURRICULUM_REF_PREFIX,
    DEFAULT_STANDARDS_PATH,
    StandardsLoadError,
    SubjectTrack,
    cited_codes,
    load_curriculum_standards,
)

_VALID = """version: standards-test-v1
source: 테스트 정본
subjects:
  - prefix: 10공국1
    name: 공통국어1
    track: common
    codes:
      - 10공국1-01-01
      - 10공국1-04-02
"""


def _write(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "standards.yaml"
    path.write_text(body, encoding="utf-8")
    return path


def test_bundled_registry_covers_the_high_school_subjects() -> None:
    standards = load_curriculum_standards()

    assert standards.version == "curriculum-standards-2022-high-v1"
    assert len(standards.subjects) == 11
    assert len(standards.codes) == 119
    assert {subject.track for subject in standards.subjects} == set(SubjectTrack)
    #: 🔴 중등·초등 성취기준은 담지 않는다 — v1은 수능 대비 고등만 본다.
    assert all(
        subject.prefix.startswith(("10", "12")) for subject in standards.subjects
    )


def test_bundled_registry_carries_no_standard_text() -> None:
    #: 정본 라이선스가 확인되지 않아 **코드만** 담는다. 문면이 새어 들어오면 방침 위반이다.
    body = DEFAULT_STANDARDS_PATH.read_text(encoding="utf-8")
    payload = "\n".join(
        line for line in body.split("\n") if not line.lstrip().startswith("#")
    )

    assert "한다." not in payload
    assert "이해하고" not in payload


def test_loader_rejects_a_missing_file(tmp_path: Path) -> None:
    with pytest.raises(StandardsLoadError, match="읽을 수 없다"):
        load_curriculum_standards(tmp_path / "없는파일.yaml")


def test_loader_rejects_a_schema_violation(tmp_path: Path) -> None:
    path = _write(tmp_path, "version: only-version\n")

    with pytest.raises(StandardsLoadError, match="스키마를 위반"):
        load_curriculum_standards(path)


def test_loader_rejects_codes_that_do_not_match_their_subject(tmp_path: Path) -> None:
    path = _write(tmp_path, _VALID.replace("10공국1-04-02", "12문학01-06"))

    with pytest.raises(StandardsLoadError, match="스키마를 위반"):
        load_curriculum_standards(path)


def test_loader_rejects_duplicate_codes(tmp_path: Path) -> None:
    path = _write(tmp_path, _VALID.replace("10공국1-04-02", "10공국1-01-01"))

    with pytest.raises(StandardsLoadError, match="스키마를 위반"):
        load_curriculum_standards(path)


def test_cited_codes_reads_only_curriculum_references() -> None:
    refs = (
        "src/ai/problem_generation/data/area_specs.yaml#areas.reading.measures",
        f"{CURRICULUM_REF_PREFIX}12독작01-03",
        f"{CURRICULUM_REF_PREFIX}10공국1-02-01",
    )

    assert cited_codes(refs) == ("12독작01-03", "10공국1-02-01")
    assert cited_codes(()) == ()
