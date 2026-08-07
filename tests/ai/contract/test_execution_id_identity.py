"""🔴 `meta.execution_id`는 **응답마다 만드는 값이 아니다** (99 ㊮).

그 응답이 말하는 **실행의 원장 키**다. 라우터가 `success_envelope(...)`에
`str(uuid.uuid4())`를 그 자리에서 넣으면 세 가지가 동시에 깨진다:

  ① `AI_RUN`의 어느 행도 가리키지 않는다 — 재현 추적(불변식 8)의 키가 아무것도 못 찾는다
  ② 같은 잡을 두 번 GET하면 **값이 다르다** — 로그 상관조차 안 된다
  ③ BE가 *"이 응답이 어느 실행이었나"* 를 물으면 답이 없다

실측(8/7 · counsel): POST·GET1회·GET2회·`AI_RUN`이 **4종**이었다. 정본은 이미
`WorkerJob.execution_id`에 있고 `worker.py`가 그 값으로 `AI_RUN`을 쓴다.

## 가드가 **못 보는 것** (한계를 여기 적는다)

🔴 **이 검사는 「그 자리에서 만들었나」만 본다.** 변수에 담아 넘기면 **통과한다**::

    x = uuid.uuid4()
    return success_envelope(..., execution_id=str(x))   # ← AST로는 안 걸린다

실제로 `counsel.py`의 POST가 정확히 그 형태였다(`:762`에서 만들어 `:768`에서 넘김).
**AST는 형태를 보고 행동은 못 본다.**

⇒ 완전한 검사는 **종단**이다 — *"같은 잡을 두 번 GET하면 같은 값"*. 그건 라우터마다
픽스처가 달라 여기 두지 않고 각 라우터의 통합 테스트에 둔다(counsel:
`tests/ai/integration/test_counsel_router.py`). **두 장치가 다른 것을 본다.**

⚠ 또 하나 못 보는 것: `execution_id=job.job_id`처럼 **다른 식별자**를 넣는 경우
(`imports.py:168`이 그 형태다 · 99 ㊯). 인라인 생성이 아니라 AST로는 정상이다.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Final

import pytest

_ROUTERS: Final = Path(__file__).resolve().parents[3] / "src" / "ai" / "api" / "routers"

#: 🔴 **B 수정·계약 판정 대기분** — 파일별로 **갈라서** 건다(#140 판단).
#:
#: 한 xfail에 셋을 담으면 하나가 고쳐져도 나머지 둘 때문에 계속 red이고 **어느 것이
#: 고쳐졌는지 자동 검출이 안 된다.** ⑮의 `_PENDING_B_FIX`는 대상이 하나여서 집합
#: 하나로 됐지만 여기는 **다른 사람·다른 시점**이다.
#:
#: ⚠ **화이트리스트가 아니다** — `strict=True`라 고쳐지는 순간 **XPASS로 red**가 나고
#: 그때 그 파일만 이 dict에서 지운다. 화이트리스트는 영원히 조용하고 이건 시끄럽다.
#: 목록에 없는 파일이 걸리면 **그대로 red**다.
_PENDING: Final[dict[str, str]] = {
    "problem.py": (
        "GET(`:435`)이 `str(uuid.uuid4())`를 쓴다 — counsel GET과 **같은 한 줄 결함**이다. "
        "⚠ **B 소유 파일**이고 B가 같은 파일 `:397`(POST)에서 이미 "
        "`str(job.execution_id)`로 **올바른 형태**를 쓰고 있다 — 기계적 파급에 가깝지만 "
        "소유자 리뷰가 필요해 #140에서 고치지 않았다(99 ㊮)."
    ),
    "confirmations.py": (
        "`:185`가 `str(uuid.uuid4())`를 쓴다. ⚠ **성격이 다르다** — 이 엔드포인트는 "
        "LLM을 안 부르고 **원장을 아예 안 쓴다.** *「가리킬 실행이 없다」* 가 정답일 수 "
        "있어 **04 계약 판정이 선행**이다(생략 vs 요청 상관 ID). 코드 문제로 단정하고 "
        "고치면 없는 계약을 만든다(99 ㊮)."
    ),
}


class _EnvelopeCall(ast.NodeVisitor):
    """`success_envelope(...)` 호출의 `execution_id=` 인자를 모은다."""

    def __init__(self) -> None:
        self.calls = 0
        self.inline: list[int] = []
        """`execution_id`가 **그 자리에서 만든** uuid인 호출의 줄 번호."""

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
        if name == "success_envelope":
            self.calls += 1
            for keyword in node.keywords:
                if keyword.arg == "execution_id" and _mints_uuid(keyword.value):
                    self.inline.append(node.lineno)
        self.generic_visit(node)


def _mints_uuid(node: ast.AST) -> bool:
    """이 표현식이 **그 자리에서** uuid를 만드는가 — `str(uuid.uuid4())`·`uuid4()` 등."""
    for child in ast.walk(node):
        if not isinstance(child, ast.Call):
            continue
        func = child.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
        if name in {"uuid4", "uuid1"}:
            return True
    return False


def _scan(path: Path) -> _EnvelopeCall:
    visitor = _EnvelopeCall()
    visitor.visit(ast.parse(path.read_text(encoding="utf-8")))
    return visitor


def _router_files() -> list[Path]:
    return sorted(p for p in _ROUTERS.glob("*.py") if p.name != "__init__.py")


def test_the_scan_finds_envelope_calls() -> None:
    """🔴 **검사 경로가 끊기면 통과가 아니라 실패다.**

    `success_envelope` 호출을 하나도 못 찾으면 *"위반이 없다"* 가 아니라 *"안 봤다"* 다
    (이름 개명·경로 변경 확인).
    """
    files = _router_files()
    assert files, f"{_ROUTERS}에서 라우터 파일을 못 찾았다"
    total = sum(_scan(path).calls for path in files)
    assert total, (
        "라우터에서 success_envelope 호출을 하나도 못 찾았다 — 위반이 없는 게 아니라 "
        "검사가 끊긴 것이다"
    )


def _params() -> list[object]:
    return [
        pytest.param(
            path,
            id=path.name,
            marks=(
                [pytest.mark.xfail(strict=True, reason=_PENDING[path.name])]
                if path.name in _PENDING
                else []
            ),
        )
        for path in _router_files()
    ]


@pytest.mark.parametrize("path", _params())
def test_meta_execution_id_is_not_minted_per_response(path: Path) -> None:
    """🔴 라우터가 `success_envelope(execution_id=…)`에 **그 자리에서 만든 uuid**를
    넘기지 않는다.

    고치는 법: 그 응답이 말하는 실행의 키를 쓴다 — 잡 기반이면
    **`job.execution_id`**(`routers/problem.py:397`이 그 형태다), 동기 실행이면 그 요청이
    만들어 `ExecutionContext`에 실은 값(`detect.py`·`classify.py`가 그 형태다).
    """
    found = _scan(path).inline
    assert not found, (
        f"{path.name}의 {found}행이 success_envelope에 그 자리에서 만든 uuid를 넘긴다.\n"
        "🔴 meta.execution_id는 **그 응답이 말하는 실행의 원장 키**다 — 응답마다 만들면 "
        "AI_RUN의 어느 행도 가리키지 않고, 같은 잡을 두 번 조회하면 값이 달라진다.\n"
        "고치는 법: 잡 기반이면 `job.execution_id`(problem.py:397 선례), 동기 실행이면 "
        "`ExecutionContext`에 실은 그 값."
    )


def test_the_pending_list_names_only_unfixed_files() -> None:
    """⚠ 대기 목록의 파일이 **실재**하는지 — 개명·삭제되면 xfail이 조용히 남는다."""
    names = {path.name for path in _router_files()}
    missing = sorted(set(_PENDING) - names)
    assert not missing, f"대기 목록에 없는 파일이 있다: {missing} — 개명·삭제 확인"


def test_the_guard_would_catch_a_violation(tmp_path: Path) -> None:
    """⚠ **가드가 실제로 잡는지**를 이 파일 안에서 확인한다 — 뒤집기의 상시화(로그 70).

    ⚠ `tempfile.NamedTemporaryFile`을 쓰지 마라 — win32에서 열려 있는 동안 그 경로를 다시
    열 수 없다(#134 실측). `tmp_path`는 pytest가 주는 실제 디렉터리다.
    """
    sample = tmp_path / "violating_router.py"
    sample.write_text(
        "import uuid\n"
        "def handler():\n"
        "    return success_envelope(data={}, execution_id=str(uuid.uuid4()),"
        " versions=None)\n",
        encoding="utf-8",
    )
    scanned = _scan(sample)
    assert scanned.calls == 1, scanned.calls
    assert scanned.inline == [3], scanned.inline

    clean = tmp_path / "clean_router.py"
    clean.write_text(
        "def handler(job):\n"
        "    return success_envelope(data={}, execution_id=str(job.execution_id),"
        " versions=None)\n",
        encoding="utf-8",
    )
    assert _scan(clean).inline == [], "변수 참조를 위반으로 잡으면 안 된다"
