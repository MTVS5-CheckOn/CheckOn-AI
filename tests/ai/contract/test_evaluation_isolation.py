"""평가 격리 경계 — 프로덕션 capability는 ai.evaluation을 import하지 않는다.

02_ownership.md §5 "평가 (프로덕션 격리)"를 문서 규칙에서 회귀 테스트로 승격한다
(02_ownership PART_B 크로스체킹 A판정 7/22). FakeSnapshot·골든셋·데모는 evaluation/에
있고, detection 등 프로덕션 코드가 이를 역참조하면 격리가 깨진다.

선례: test_contracts_isolation.py(contracts 의존 방향 AST 검사).
"""

import ast
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[3] / "src" / "ai"

#: 프로덕션 capability — 이들은 ai.evaluation을 import해선 안 된다.
_PRODUCTION_PACKAGES = [
    "detection",
    "composition",
    "diagnosis",
    "problem_generation",
    #: ⚠ 🔴 **`import_mapping` 을 뺐다(2026-08-24 · 99 #226 ⓑ)** — skip 사유가
    #: «`import_mapping/` **아직 없음**» 이었는데 🔴 **거짓이었다**: import 축은 №61 에서
    #: **v1 범위 밖으로 삭제**됐다(«아직 없다» 가 아니라 «없앴다»). 그 디렉터리는 **영영
    #: 안 생기므로** 그 칸은 영영 skip 이고, «22 skip 중 하나» 로 계속 세어졌다.
    #: 🔴 나머지 넷은 그대로 돈다 — 이 검사의 대상이 0이 되는 것이 아니다.
    #: 🔴 **되살아나는 조건**: import 축이 v1 로 돌아오면 이 줄을 되돌린다(#187).
]


def _imports_evaluation(source: str) -> bool:
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom) and node.module:
            if node.module == "ai.evaluation" or node.module.startswith("ai.evaluation."):
                return True
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "ai.evaluation" or alias.name.startswith("ai.evaluation."):
                    return True
    return False


@pytest.mark.parametrize("package", _PRODUCTION_PACKAGES)
def test_production_does_not_import_evaluation(package: str) -> None:
    package_dir = _SRC / package
    if not package_dir.is_dir():
        pytest.skip(f"{package}/ 아직 없음")
    offenders = [
        str(path.relative_to(_SRC))
        for path in sorted(package_dir.rglob("*.py"))
        if _imports_evaluation(path.read_text(encoding="utf-8"))
    ]
    assert not offenders, f"{package}가 ai.evaluation을 import한다(격리 위반): {offenders}"


def test_at_least_one_package_scanned() -> None:
    """경로 계산이 틀려 전부 skip되는 것을 막는다 — 최소 하나는 실제 스캔돼야."""
    scanned = [p for p in _PRODUCTION_PACKAGES if (_SRC / p).is_dir()]
    assert scanned, "프로덕션 capability 디렉터리를 하나도 찾지 못했다"
