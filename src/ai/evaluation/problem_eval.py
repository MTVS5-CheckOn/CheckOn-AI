"""M2 문제출제 골든 코퍼스의 구조·개수·blind 불변식 평가."""

from pathlib import Path
from typing import Literal, Self

import yaml  # type: ignore[import-untyped]  # PyYAML은 공식 타입 정보를 제공하지 않는다.
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

_DEFAULT_GOLDEN_ROOT = Path(__file__).resolve().parent / "golden" / "problems"
_EXPECTED_CASE_IDS: dict[str, tuple[str, ...]] = {
    "rule_violations": tuple(f"P{index}" for index in range(1, 19)),
    "ambiguity": tuple(f"AM{index}" for index in range(1, 12)),
    "normal_pass": tuple(f"N{index}" for index in range(1, 11)),
    "refine": tuple(f"RF{index}" for index in range(1, 12)),
    "graphrag": tuple(f"GR{index}" for index in range(1, 12)),
}


class ProblemGoldenError(ValueError):
    """문제출제 골든 코퍼스가 정본 구조를 위반함."""


class EducationalDraft(BaseModel):
    """교사 검수 전 문항 기준 초안."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    stem: str = Field(min_length=1)
    choices: tuple[str, ...] = Field(min_length=5, max_length=5)
    answer_draft: int = Field(ge=1, le=5)
    rationale_draft: str = Field(min_length=1)
    evidence_draft: tuple[str, ...] = Field(min_length=1)
    difficulty_draft: float | None = None


class GoldenMutation(BaseModel):
    """기준 초안에 적용할 결정론 fixture 변형."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    code: str = Field(min_length=1)
    detail: str = Field(min_length=1)


class GoldenExpectation(BaseModel):
    """구현이 재현해야 하는 판정·호출 기대값."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    decision: str = Field(min_length=1)
    rule_ids: tuple[str, ...] = ()
    llm_calls: int = Field(ge=0)
    notes: str = Field(min_length=1)


class ProblemGoldenCase(BaseModel):
    """문제출제 골든 사례 1건."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    case_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    track: Literal["T1", "T2", "T3"]
    item_format: Literal["mcq"] = "mcq"
    review_status: Literal["expert_review_pending"]
    educational_draft: EducationalDraft
    mutation: GoldenMutation
    expected: GoldenExpectation


class ProblemGoldenSuite(BaseModel):
    """한 디렉터리의 골든 사례 묶음."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["problem-golden.v1"] = "problem-golden.v1"
    suite: str = Field(min_length=1)
    cases: tuple[ProblemGoldenCase, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_unique_case_ids(self) -> Self:
        case_ids = [case.case_id for case in self.cases]
        duplicates = sorted(
            case_id for case_id in set(case_ids) if case_ids.count(case_id) > 1
        )
        if duplicates:
            raise ValueError(f"중복 case_id: {', '.join(duplicates)}")
        return self


class PromptSnapshotEntry(BaseModel):
    """프롬프트 조립 스냅숏 manifest 한 행."""

    model_config = ConfigDict(frozen=True, extra="forbid", protected_namespaces=())

    prompt_id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    file: str = Field(min_length=1)
    review_status: Literal["expert_review_pending"]


class PromptSnapshotManifest(BaseModel):
    """문제출제 프롬프트 3종 스냅숏 목록."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["prompt-snapshots.v1"] = "prompt-snapshots.v1"
    snapshots: tuple[PromptSnapshotEntry, ...] = Field(min_length=1)


class ProblemGoldenSuiteSummary(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    suite: str
    case_count: int = Field(ge=1)


class ProblemGoldenReport(BaseModel):
    """구조 검증을 통과한 오프라인 골든셋 요약."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    passed: Literal[True] = True
    total_cases: int = Field(ge=1)
    suites: tuple[ProblemGoldenSuiteSummary, ...]
    prompt_snapshot_count: int = Field(ge=1)
    educational_review_status: Literal["expert_review_pending"] = (
        "expert_review_pending"
    )
    real_model_evaluated: Literal[False] = False


def _load_yaml(path: Path) -> object:
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise ProblemGoldenError(f"골든 파일을 읽을 수 없다: {path}") from error
    except yaml.YAMLError as error:
        raise ProblemGoldenError(f"골든 YAML 구문 오류: {path}: {error}") from error


def _load_suite(root: Path, suite_name: str) -> ProblemGoldenSuite:
    path = root / suite_name / "cases.yaml"
    try:
        suite = ProblemGoldenSuite.model_validate(_load_yaml(path))
    except ValidationError as error:
        raise ProblemGoldenError(f"골든 스키마 오류: {path}: {error}") from error

    if suite.suite != suite_name:
        raise ProblemGoldenError(
            f"골든 suite 이름 불일치: path={suite_name}, value={suite.suite}"
        )
    expected_ids = _EXPECTED_CASE_IDS[suite_name]
    actual_ids = tuple(case.case_id for case in suite.cases)
    if actual_ids != expected_ids:
        raise ProblemGoldenError(
            f"{suite_name} case_id 불일치: actual={actual_ids}, expected={expected_ids}"
        )
    return suite


def _load_prompt_snapshots(root: Path) -> PromptSnapshotManifest:
    snapshots_root = (root / "prompt_snapshots").resolve()
    manifest_path = snapshots_root / "manifest.yaml"
    try:
        manifest = PromptSnapshotManifest.model_validate(_load_yaml(manifest_path))
    except ValidationError as error:
        raise ProblemGoldenError(
            f"프롬프트 스냅숏 manifest 오류: {error}"
        ) from error

    expected_prompt_ids = {
        "pg.passage.v1",
        "pg.items.v1",
        "pg.cross_solve.v1",
    }
    actual_prompt_ids = {snapshot.prompt_id for snapshot in manifest.snapshots}
    if actual_prompt_ids != expected_prompt_ids or len(manifest.snapshots) != 3:
        raise ProblemGoldenError(
            f"프롬프트 스냅숏 ID 불일치: {sorted(actual_prompt_ids)}"
        )

    for snapshot in manifest.snapshots:
        path = (snapshots_root / snapshot.file).resolve()
        if not path.is_relative_to(snapshots_root):
            raise ProblemGoldenError("프롬프트 스냅숏 경로가 디렉터리 밖을 가리킨다")
        try:
            content = path.read_text(encoding="utf-8").strip()
        except OSError as error:
            raise ProblemGoldenError(
                f"프롬프트 스냅숏을 읽을 수 없다: {snapshot.file}"
            ) from error
        if not content:
            raise ProblemGoldenError(
                f"프롬프트 스냅숏이 비어 있다: {snapshot.file}"
            )
        if snapshot.prompt_id == "pg.cross_solve.v1":
            forbidden_keys = {'"answer"', '"rationale"', '"evidence"'}
            leaked = sorted(key for key in forbidden_keys if key in content)
            if leaked:
                raise ProblemGoldenError(
                    f"cross_solve blind 누출: {', '.join(leaked)}"
                )
            if '"target_metadata"' not in content:
                raise ProblemGoldenError(
                    "cross_solve 스냅숏에 target_metadata가 없다"
                )
    return manifest


class ProblemGoldenEvaluator:
    """실 LLM 없이 골든 코퍼스 구조와 보안 불변식을 검사한다."""

    def __init__(self, root: Path = _DEFAULT_GOLDEN_ROOT) -> None:
        self._root = root

    def evaluate(self) -> ProblemGoldenReport:
        suites = tuple(
            _load_suite(self._root, suite_name)
            for suite_name in _EXPECTED_CASE_IDS
        )
        manifest = _load_prompt_snapshots(self._root)
        summaries = tuple(
            ProblemGoldenSuiteSummary(suite=suite.suite, case_count=len(suite.cases))
            for suite in suites
        )
        return ProblemGoldenReport(
            total_cases=sum(summary.case_count for summary in summaries),
            suites=summaries,
            prompt_snapshot_count=len(manifest.snapshots),
        )
