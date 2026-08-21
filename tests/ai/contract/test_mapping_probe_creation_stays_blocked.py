"""🔴 `mapping_probe` 를 **새로 만들 수 있는 경로가 없다** (99 #187·#188).

⚠ 🔴 **이 검사의 이름에 「만료」를 쓰지 않았다.** 생성 경로 0건은 **만료의 전제**이지
만료가 아니다 — 배포 DB 에 기존 행이 남아 있으면 enum 을 못 지운다. 이름이 「만료」면
**삭제 직후부터 green** 이라 «이미 만료됐다» 고 말하게 된다(99 #105 가 그 형태였다).
🔴 green 이 뜻하는 것은 **«아직 막혀 있다»** 이지 «지워도 된다» 가 아니다.

🔴 **만료 조건(배포 DB 의 `mapping_probe` 행이 0)은 저장소 밖이라 검사로 못 잰다.**
**99 #188** 이 그 운영 점검 항목이다(소유 A · 계기 셋 · 읽기 전용 SQL).

⚠ **enum 자체는 남아 있는 것이 정상이다** — `mapping_probe` 행은 **실행 기록**이고
`WorkerKind(row.agent_kind)` 가 그 값을 필요로 한다. 지우면 «그 실행이 없었다» 가 되고
**불변식 8(재현성)·원장 완전성**에 걸린다.
"""

from __future__ import annotations

import ast
import pathlib
from typing import Final

from ai.contracts.agents import OperationKind, WorkerKind

_SRC: Final = pathlib.Path("src/ai")
#: 🔴 이 넷은 **정의·매핑 자리**라 값이 나오는 것이 정상이다 — 생성 경로가 아니다.
_DEFINITION_SITES: Final = frozenset(
    {
        "contracts/agents.py",  # enum 정의 + 두 매핑
        "db/models.py",  # CHECK 제약 + 값 목록 주석
        "db/ledger_completeness.py",  # WORKER_CAPABILITY 표
        "contracts/execution.py",  # Capability.IMPORT_MAPPING
    }
)



#: 🔴 **주석·docstring 은 「언급」이지 「생성 경로」가 아니다.** 텍스트로 훑으면 «왜 둘이었나»
#: 를 적어 둔 기록 일곱 자리가 걸린다(실측 8/22) — 그건 **남의 사실을 red 로 만드는 것**이다.
#: ⇒ **AST 로 실행되는 코드만** 본다: enum 멤버 참조와 **docstring 이 아닌** 문자열 상수.
#: ⚠ `db/migrations/` 는 제외한다 — 마이그레이션은 **과거를 재생**하는 파일이라 그 값이
#: 나오는 것이 정상이고, 고치면 이미 적용된 이력과 갈린다.
_MEMBERS: Final = frozenset({"MAPPING_PROBE", "MAPPING_PROBE_RESOLVE", "IMPORT_MAPPING"})
_VALUES: Final = frozenset({"mapping_probe", "mapping_probe.resolve", "import_mapping"})


def _docstring_nodes(tree: ast.Module) -> set[int]:
    """docstring Constant 의 id 집합 — 그것만 문자열 검사에서 뺀다."""
    found: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            first = node.body[0] if node.body else None
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                found.add(id(first.value))
    return found


def _code_references(path: pathlib.Path) -> list[int]:
    """실행되는 코드가 그 값을 쓰는 줄 번호."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    skip = _docstring_nodes(tree)
    lines: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in _MEMBERS:
            lines.append(node.lineno)
        elif isinstance(node, ast.Name) and node.id in _MEMBERS:
            lines.append(node.lineno)
        elif (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and node.value in _VALUES
            and id(node) not in skip
        ):
            lines.append(node.lineno)
    return sorted(set(lines))


def test_the_enum_values_are_still_readable() -> None:
    """① 원장 행을 읽는 경로가 산다 — 🔴 **이게 남긴 이유다.**"""
    assert WorkerKind("mapping_probe") is WorkerKind.MAPPING_PROBE
    assert OperationKind("mapping_probe.resolve") is OperationKind.MAPPING_PROBE_RESOLVE


def test_no_source_file_can_create_a_mapping_probe_job() -> None:
    """② 🔴 **생성 경로가 0건이다** — 정의·매핑 자리 밖에서 그 값을 쓰는 코드가 없다.

    ⚠ 문자열과 enum 멤버를 **둘 다** 센다 — 한쪽만 세면 다른 쪽으로 되살아난다.
    🔴 새 파일이 그 값을 쓰기 시작하면 여기가 red 다. 그때 물을 것은
    «import 축을 되살리는가» 이고, 되살린다면 이 검사와 99 #187 을 같이 고친다.
    """
    offenders: list[str] = []
    for path in sorted(_SRC.rglob("*.py")):
        relative = path.relative_to(_SRC).as_posix()
        if relative in _DEFINITION_SITES or relative.startswith("db/migrations/"):
            continue
        offenders.extend(f"{relative}:{line}" for line in _code_references(path))
    assert not offenders, (
        "`mapping_probe` 를 정의 자리 밖에서 쓴다 — import 축이 되살아났거나 새 생성 경로가 "
        f"생겼다(99 #187): {offenders}"
    )


def test_the_import_mapping_package_is_gone() -> None:
    """③ 패키지 자체가 없다 — ②가 「문자열만」 보는 것을 보완한다."""
    assert not (_SRC / "import_mapping").exists(), (
        "`src/ai/import_mapping/` 가 되살아났다 — 되살리는 것이 판정이면 99 #187 을 먼저 고쳐라"
    )
    assert not (_SRC / "api" / "routers" / "imports.py").exists()
