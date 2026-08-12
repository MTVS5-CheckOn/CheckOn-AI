"""실 PG 미가용 안내는 **한 곳에서만** 만든다.

🔴 **이 안내는 두 번 낡았다.**

  1차 — 문면이 `docker compose -f compose.dev.yml up`이었는데 **그 파일이 저장소에 없었다.**
  2차 — #221이 9곳을 `docker compose up -d`로 고쳤는데 ⓐ **자리가 23곳**이라 나머지는
        그대로였고 ⓑ 고친 문면도 여전히 틀렸다 — `docker-compose.yml`**도** 저장소에
        없다(`.gitignore`). clone한 새 개발자에게는 **여전히 막다른 길**이다.

⇒ 값이 23곳에 살면 한쪽만 고쳐진다(99 #02). 정본을 `tests/ai/fakes/pg_hint.py` 하나로 두고
**여기서 복제를 막는다.**

⚠ **명령을 문면에 넣지 않는다** — 접속값·기동 절차의 정본은 README 한 곳이다. 명령을 다시
넣으면 그날부터 정본이 둘이 되고 같은 결함이 재발한다.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Final

from pg_hint import PG_UNAVAILABLE, pg_unavailable

_TESTS: Final = Path(__file__).resolve().parents[2]
_SRC: Final = _TESTS.parents[0] / "src" / "ai"
#: 정본 자신 — 여기에는 「저장소에 없다」는 사실이 적혀 있어야 한다.
_CANON: Final = _TESTS / "ai" / "fakes" / "pg_hint.py"

#: 🔴 **안내에 다시 들어오면 안 되는 명령** — 저장소에 없는 파일을 가리킨다.
_FORBIDDEN_IN_MESSAGES: Final = ("docker compose", "compose.dev.yml", "docker-compose")


def _skip_messages(path: Path) -> list[str]:
    """그 파일의 `pytest.skip(...)` 첫 인자 중 **리터럴 문자열**만."""
    found: list[str] = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr == "skip"):
            continue
        if node.args and isinstance(node.args[0], ast.Constant):
            value = node.args[0].value
            if isinstance(value, str):
                found.append(value)
    return found


def test_no_test_file_spells_the_command_itself() -> None:
    """🔴 **skip 문면에 기동 명령을 직접 적지 않는다** — 적으면 정본이 둘이 된다."""
    offenders: dict[str, list[str]] = {}
    for path in sorted(_TESTS.rglob("*.py")):
        if path == _CANON:
            continue
        bad = [
            message
            for message in _skip_messages(path)
            if any(token in message for token in _FORBIDDEN_IN_MESSAGES)
        ]
        if bad:
            offenders[str(path.relative_to(_TESTS))] = bad
    assert not offenders, (
        f"기동 명령이 skip 문면에 복제됐다: {offenders} — "
        f"`pg_hint.PG_UNAVAILABLE`을 쓰고 절차는 README에 둔다"
    )


def test_the_canonical_message_says_the_file_is_not_in_the_repo() -> None:
    """🔴 **「저장소에 없다」를 말한다** — 그게 두 번 낡은 이유였다.

    ⚠ 명령을 적지 않는 것만으로는 부족하다. *"어디에 있지"* 를 다시 찾게 하면
    안내가 아니라 수수께끼다.
    """
    assert "저장소에 없다" in PG_UNAVAILABLE, PG_UNAVAILABLE
    assert "README" in PG_UNAVAILABLE, PG_UNAVAILABLE
    #: ⚠ 정본 자신도 명령을 품지 않는다.
    assert not any(token in PG_UNAVAILABLE for token in _FORBIDDEN_IN_MESSAGES)


def test_the_note_is_appended_not_replaced() -> None:
    """`pg_unavailable("(99 #26)")`가 **정본 + 맥락**이어야 한다 — 덮어쓰면 안내가 사라진다."""
    assert pg_unavailable("(99 #26)") == f"{PG_UNAVAILABLE} (99 #26)"
    assert pg_unavailable() == PG_UNAVAILABLE


def test_the_verifier_points_at_the_readme_too() -> None:
    """🔴 **검증기 오류 문면도 같은 자리를 가리킨다** — 거기만 낡으면 또 막다른 길이다.

    ⚠ `pre_pr_verify`는 테스트가 아니라 **명령**이라 위 AST 검사에 안 걸린다 — 그래서
    따로 본다(검사의 이름이 보는 것보다 넓으면 안 된다).
    """
    source = (_SRC / "evaluation" / "pre_pr_verify.py").read_text(encoding="utf-8")
    assert "docker compose up -d" not in source, (
        "검증기가 저장소에 없는 compose 파일을 안내한다"
    )
    assert "README" in source, "검증기 오류 문면이 기동 절차를 안 가리킨다"
