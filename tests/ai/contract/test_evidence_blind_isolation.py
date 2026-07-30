"""blind 무오염 — 해소된 근거가 verifier 페이로드로 흘러들지 않는다 (09 §2-12-② 조건 5).

blind 계약은 **정답·해설·근거(evidence) 비공개**다(`part_b/05_problem_generation.md` §4.3).
`ResolvedAnchor.quote`는 근거 인용문이라 사실상 정답 힌트이므로, verifier 경로에 닿으면
게이트 ②가 무의미해진다(`part_b/11` §6-4).

두 축으로 고정한다:
1. verifier 경로 모듈이 `ai.evidence`를 import하지 않는다(AST).
2. 교차 풀이 blind 페이로드의 키에 근거·정답·해설 계열이 없다(AST — 딕셔너리 리터럴 검사).

선례: `test_evaluation_isolation.py`(평가 격리 AST) · `test_contracts_isolation.py`.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[3] / "src" / "ai"
_CROSS_SOLVER = _SRC / "problem_generation" / "cross_solver.py"

#: verifier 페이로드를 만드는 경로 — 여기서 ai.evidence가 보이면 blind가 깨진다.
_VERIFIER_PACKAGES = ["problem_generation"]

#: blind 페이로드에 절대 실리면 안 되는 키(정답·해설·근거 계열).
_FORBIDDEN_BLIND_KEYS = frozenset(
    {
        "answer",
        "rationale",
        "evidence",
        "quote",
        "anchors",
        "evidence_pack",
        "evidence_pack_id",
        "resolved",
    }
)


def _imports_evidence(source: str) -> bool:
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom) and node.module:
            if node.module == "ai.evidence" or node.module.startswith("ai.evidence."):
                return True
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "ai.evidence" or alias.name.startswith("ai.evidence."):
                    return True
    return False


@pytest.mark.parametrize("package", _VERIFIER_PACKAGES)
def test_verifier_path_does_not_import_evidence(package: str) -> None:
    """resolver 산출물이 verifier 쪽으로 흘러들 import 경로 자체를 막는다."""
    package_dir = _SRC / package
    if not package_dir.is_dir():
        pytest.skip(f"{package}/ 아직 없음")
    offenders = [
        str(path.relative_to(_SRC))
        for path in sorted(package_dir.rglob("*.py"))
        if _imports_evidence(path.read_text(encoding="utf-8"))
    ]
    assert not offenders, f"{package}가 ai.evidence를 import한다(blind 오염 위험): {offenders}"


def _blind_payload_keys(source: str) -> set[str]:
    """cross_solver의 `blind_item = {...}` 리터럴 키를 뽑는다."""
    keys: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Assign):
            continue
        targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
        if "blind_item" not in targets or not isinstance(node.value, ast.Dict):
            continue
        keys |= {
            key.value
            for key in node.value.keys
            if isinstance(key, ast.Constant) and isinstance(key.value, str)
        }
    return keys


def test_blind_payload_carries_no_evidence_keys() -> None:
    """교차 풀이 페이로드에 정답·해설·근거 키가 없다 — quote 유출 회귀 방지."""
    if not _CROSS_SOLVER.is_file():
        pytest.skip("cross_solver.py 아직 없음")
    keys = _blind_payload_keys(_CROSS_SOLVER.read_text(encoding="utf-8"))
    assert keys, "blind_item 딕셔너리 리터럴을 찾지 못했다 — 검사 경로가 끊겼다"
    leaked = keys & _FORBIDDEN_BLIND_KEYS
    assert not leaked, f"blind 페이로드에 근거·정답 계열 키가 있다: {sorted(leaked)}"


def test_resolved_evidence_is_not_reachable_from_verifier_contract() -> None:
    """SolveResult(verifier 출력)에 근거 필드가 없다 — 반대 방향 유출도 막는다."""
    from ai.contracts.problem_generation import SolveResult

    assert not _FORBIDDEN_BLIND_KEYS & set(SolveResult.model_fields)
