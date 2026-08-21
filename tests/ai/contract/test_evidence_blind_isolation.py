"""blind 무오염 — 해소된 근거가 blind 페이로드로 흘러들지 않는다 (09 §2-12-② 조건 5).

**범위 축소 근거(7/30 — B 지적 수용, 09 §2-15 제안 예정):**
1. `06_quality_gates.md` §0·§1은 게이트 ①을 **`RuleValidation`(결정론 코드)**, ②를
   **`BlindCrossSolve`**(blind LLM)로 나눈다. **R-1은 게이트 ①이지 verifier가 아니다.**
2. 그 R-1은 `problem_generation/verification.py`에 있고, 06 §1의 GraphRAG 확장이
   `source_content_hash` 대조·`quote_hash` 일치·`license_ref` 유효·`rights_status=approved`를
   요구한다 — **근거를 봐야 하므로 evidence import가 정당하다.** 패키지 전체 금지는 과잉이었다.
3. blind 계약(`05_problem_generation.md` §4.3 — 정답·해설·근거 비공개)의 경계는 패키지가
   아니라 **blind 페이로드를 조립하는 지점**이다. 실측상 `cross_solver.py`의
   `blind_item = {...}` 리터럴 한 곳뿐이라 거기로 좁힌다.

축소의 대가로 **화이트리스트**를 얻는다 — 금칙 키 목록(블랙리스트)만으로는 새 키가 추가돼도
안 걸리므로, `blind_item`의 키 집합 자체를 고정한다. 둘은 목적이 달라 함께 유지한다.

선례: `test_evaluation_isolation.py`(평가 격리 AST) · `test_contracts_isolation.py`.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[3] / "src" / "ai"
_CROSS_SOLVER = _SRC / "problem_generation" / "application" / "cross_solver.py"

#: blind 페이로드를 **조립하는** 모듈만 — 여기서 ai.evidence가 보이면 blind가 깨진다.
#: 게이트 ①(`RuleValidation` — `verification.py`의 R-1)은 **대상이 아니다.** R-1은 근거를
#: 대조해야 하므로 evidence import가 정당하다(06 §1 GraphRAG 확장). 실측(7/30): 패키지
#: 전체에서 `blind_item` 조립·cross-solve 프롬프트 렌더는 `cross_solver.py` 한 곳뿐이다.
_BLIND_PAYLOAD_MODULES = ["problem_generation/application/cross_solver.py"]

#: `blind_item`에 실려도 되는 키 **전부**(화이트리스트) — `cross_solver.py`에서 손으로 읽었다.
#: 05 §4.3: blind = 정답·해설·근거 비공개. 목표 메타는 정렬 판정용으로 별도 전달된다
#: (`target_metadata` — 이 화이트리스트 대상 아님).
_EXPECTED_BLIND_KEYS = frozenset(
    {"area_tag", "type_tag", "item_format", "skill_node_id", "stem", "choices"}
)

#: 이름 자체가 금지인 키(블랙리스트) — 화이트리스트와 목적이 다르다. 화이트리스트는
#: "새 키 감지", 이쪽은 "정답·해설·근거 계열 이름이 절대 등장하지 않음"을 고정한다.
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

_FMT6_HINT = (
    "FMT-6로 학생 가시 자료 블록을 추가한다면 이 화이트리스트를 함께 갱신하고 "
    "part_b/08 §7 프롬프트 스냅숏 회귀를 확인하라."
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


@pytest.mark.parametrize("module_path", _BLIND_PAYLOAD_MODULES)
def test_blind_payload_module_does_not_import_evidence(module_path: str) -> None:
    """blind 페이로드 조립 모듈이 ai.evidence를 import하지 않는다.

    게이트 ①(RuleValidation)은 이 검사 대상이 아니다 — R-1은 근거를 대조해야 한다(06 §1).
    """
    path = _SRC / module_path
    if not path.is_file():
        pytest.skip(f"{module_path} 아직 없음")
    assert not _imports_evidence(path.read_text(encoding="utf-8")), (
        f"{module_path}가 ai.evidence를 import한다 — 해소된 근거가 blind 페이로드로 샐 수 있다"
    )


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


def test_blind_payload_keys_match_whitelist_exactly() -> None:
    """`blind_item` 키 집합이 화이트리스트와 **정확히 일치**한다 — 새 키가 늘면 실패한다.

    블랙리스트만으로는 위험한 새 키(이름이 금칙어가 아닌 것)를 못 잡는다.
    """
    assert _BLIND_PAYLOAD_MODULES, "blind 페이로드 조립 모듈 목록이 비었다 — 검사 대상이 사라졌다"
    if not _CROSS_SOLVER.is_file():
        pytest.skip("cross_solver.py 아직 없음")
    keys = _blind_payload_keys(_CROSS_SOLVER.read_text(encoding="utf-8"))
    assert keys, "blind_item 딕셔너리 리터럴을 찾지 못했다 — 검사 경로가 끊겼다"
    added = keys - _EXPECTED_BLIND_KEYS
    removed = _EXPECTED_BLIND_KEYS - keys
    assert keys == _EXPECTED_BLIND_KEYS, (
        f"blind 페이로드 키가 바뀌었다 (추가={sorted(added)} 제거={sorted(removed)}). {_FMT6_HINT}"
    )


def test_blind_payload_carries_no_evidence_keys() -> None:
    """교차 풀이 페이로드에 정답·해설·근거 계열 **이름**이 없다 — quote 유출 회귀 방지."""
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
