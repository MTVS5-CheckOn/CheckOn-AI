"""스모크 러너가 **pytest 밖에서** 골든 케이스를 적재할 수 있는가 (6차 장애 2026-08-10).

🔴 **실측 장애다.** 6차 실 LLM 스모크가 **S1을 마치고(21콜 소비) S2 문 앞에서** 죽었다:

```text
_run_s2 → _s2_cases() → _golden("tests.ai.integration.test_counsel_router")
       → 그 모듈의 `from counsel_text import DEFAULT_DRAFT, draft`
       → ModuleNotFoundError: No module named 'counsel_text'
```

`counsel_text`는 `tests/ai/fakes/`에 있고, 그 **평면 import는 pytest ini의
`pythonpath = ["src", "tests/ai/fakes"]`로만** 해석된다. 러너는 `python -m`으로 도는데
`sys.path`에 **cwd 하나만** 넣었다 ⇒ pytest 안에서는 되고 **실행 경로에서만 죽는다.**

🔴 **이 검사를 pytest 안에서 그냥 부르면 아무것도 안 본다** — 여기선 `tests/ai/fakes`가
**이미** `sys.path`에 있어서 **버그가 있어도 green**이다. ⇒ **자식 프로세스**로 부른다.
⚠ **LLM은 안 부른다** — `_s2_cases()`는 모듈 import와 dict 조립뿐이다.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Final

from ai.evaluation.counsel_llm_smoke import _s2_cases, _test_import_roots

_ROOT: Final = Path(__file__).resolve().parents[4]
#: 자식에서 실행할 것 — **케이스 수를 찍는다**(적재가 끝까지 갔다는 뜻).
_PROBE: Final = (
    "from ai.evaluation.counsel_llm_smoke import _s2_cases; print(len(_s2_cases()))"
)


def _child_env() -> dict[str, str]:
    """🔴 부모의 pytest 경로를 물려주지 않는다 — 물려주면 이 검사가 무의미해진다."""
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    return env


def test_the_child_process_really_lacks_the_pytest_path() -> None:
    """🔴 절단 가드 — 자식이 `tests/ai/fakes`를 **안 가진 채** 시작해야 이 파일이 무언가를 본다."""
    result = subprocess.run(
        [sys.executable, "-c", "import sys; print('\\n'.join(sys.path))"],
        cwd=_ROOT,
        env=_child_env(),
        capture_output=True,
        text=True,
        check=True,
    )
    fakes = str(_ROOT / "tests" / "ai" / "fakes")
    assert fakes not in result.stdout.splitlines(), (
        "자식이 이미 fakes 경로를 갖고 시작한다 — 이 검사는 버그를 못 본다"
    )


def test_the_case_loader_works_outside_pytest() -> None:
    """🔴 **실행 경로에서 적재가 끝까지 가는가** — 6차가 여기서 죽었다."""
    result = subprocess.run(
        [sys.executable, "-c", _PROBE],
        cwd=_ROOT,
        env=_child_env(),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        "pytest 밖에서 S2 케이스 적재가 실패한다 — 실 LLM 회차가 S1 소비 후 여기서 죽는다:\n"
        f"{result.stderr[-1500:]}"
    )
    assert result.stdout.strip().splitlines()[-1] == "4", (
        f"케이스 수가 4가 아니다: {result.stdout!r}"
    )


def test_the_case_loader_still_works_inside_pytest() -> None:
    """뒤집기 — 원래 되던 경로(pytest 안)도 그대로여야 한다."""
    cases = _s2_cases()
    assert len(cases) == 4
    assert all(isinstance(body, dict) and body for _, _, body in cases)


def test_the_runner_import_roots_come_from_the_pytest_config() -> None:
    """🔴 **경로 목록을 손으로 베끼지 않는다** — 베끼면 갈린다(99 #02).

    ⚠ pytest ini에 세 번째 항목이 생기면 **러너만 조용히 낡는다.** 정본은 `pyproject.toml`
    하나이고, 러너는 그것을 읽는다. 이 검사는 **그 파생이 실제로 성립하는지**를 묻는다.
    """
    config = tomllib.loads((_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    declared = config["tool"]["pytest"]["ini_options"]["pythonpath"]
    assert declared, "pytest ini의 pythonpath가 비었다 — 정본을 못 읽었다"

    roots = _test_import_roots()
    assert _ROOT in roots, "저장소 루트가 빠지면 `tests.ai...` 패키지 경로를 못 연다"
    for entry in declared:
        assert _ROOT / entry in roots, f"pytest ini의 `{entry}`가 러너 경로에 없다"
