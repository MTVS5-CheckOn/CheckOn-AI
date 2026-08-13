"""진단 설정·커리큘럼 그래프 로딩 — 이 capability의 유일한 파일 I/O 경계.

소유: member-B(염준영) (docs/02_ownership.md §1)

`diagnoser.diagnose()`는 순수 함수라 임계값을 **주입받기만** 한다. 종전에는 그 값을
만드는 자리가 **테스트에만** 있어서(`test_problem_generation_smoke.py`가
`DiagnosisConfig(...)`를 직접 조립) 프로덕션 호출자가 값을 새로 적어야 했다 —
"값이 바뀌면 코드 diff가 생기면 위치가 틀린 것"(03_coding_rules §1)의 그 위치다.

⚠ `problem_generation/infrastructure/config.py`와 같은 형태로 맞췄다 — 로더가 둘로
갈리면 fail-closed 규약도 갈린다.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml  # type: ignore[import-untyped]
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ai.diagnosis.diagnoser import DiagnosisConfig
from ai.diagnosis.skill_graph import SkillGraph, load_skill_graph

_DATA_ROOT = Path(__file__).resolve().parent / "data"
_DEFAULT_CONFIG_PATH = _DATA_ROOT / "diagnosis_config.yaml"
_DEFAULT_GRAPH_PATH = _DATA_ROOT / "curriculum_graph.yaml"


class DiagnosisConfigError(ValueError):
    """진단 설정 파일을 읽거나 검증할 수 없음."""


class DiagnosisConfigDocument(BaseModel):
    """버전 + 판정 파라미터 묶음.

    🔴 `version`을 `DiagnosisConfig`에 **넣지 않았다.** 그쪽은 순수 함수의 계산 입력이고
    버전은 산출물 라벨이다 — 섞으면 "설정이 달라졌는가"와 "계산이 달라졌는가"를 같은
    필드로 판정하게 된다(`diagnose()`가 `config_version`을 별도 인자로 받는 이유).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    version: str = Field(min_length=1)
    expected_taxonomy_version: str = Field(min_length=1)
    params: DiagnosisConfig


def load_diagnosis_config(path: Path = _DEFAULT_CONFIG_PATH) -> DiagnosisConfigDocument:
    """버전 관리된 진단 설정을 엄격히 로드한다 — 스키마 위반은 즉시 실패."""

    try:
        raw: object = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise DiagnosisConfigError(f"진단 설정 파일을 읽을 수 없다: {path}") from error
    except yaml.YAMLError as error:
        raise DiagnosisConfigError(f"진단 설정 YAML 오류: {error}") from error
    try:
        return DiagnosisConfigDocument.model_validate(raw)
    except ValidationError as error:
        raise DiagnosisConfigError(f"진단 설정 스키마 오류: {error}") from error


def load_curriculum_graph(
    path: Path = _DEFAULT_GRAPH_PATH,
    *,
    expected_taxonomy_version: str | None = None,
) -> SkillGraph:
    """패키지 동봉 커리큘럼 그래프를 로드한다.

    기대 어휘 버전을 안 주면 설정 파일의 값을 쓴다 — 호출부마다 `"v1"`을 다시 적는
    자리를 만들지 않는다(그 리터럴이 테스트 6곳에 복제돼 있었다).
    """

    version = expected_taxonomy_version or load_diagnosis_config().expected_taxonomy_version
    return load_skill_graph(path, expected_taxonomy_version=version)


@lru_cache
def default_diagnosis_runtime() -> tuple[SkillGraph, DiagnosisConfigDocument]:
    """기동 1회 로드한 그래프·설정 — 요청마다 YAML을 다시 읽지 않는다.

    ⚠ 캐시라 **파일을 고쳐도 프로세스가 살아 있으면 안 바뀐다.** 설정 개정은 배포
    단위이고(버전을 올린다), 테스트는 로더를 직접 부른다.
    """

    document = load_diagnosis_config()
    graph = load_curriculum_graph(
        expected_taxonomy_version=document.expected_taxonomy_version
    )
    return graph, document


__all__ = [
    "DiagnosisConfigDocument",
    "DiagnosisConfigError",
    "default_diagnosis_runtime",
    "load_curriculum_graph",
    "load_diagnosis_config",
]
