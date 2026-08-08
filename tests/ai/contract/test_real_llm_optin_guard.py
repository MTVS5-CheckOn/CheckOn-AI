"""실 LLM 호출은 **명시적 허용 없이는 하지 않는다** (99 #32 · 사고 2026-08-09).

🔴 **사고:** `pytest -m integration`이 사용자 키로 실 OpenAI API를 호출했다(추정 20~25콜).
skip 조건이 `"localhost" in openai_base_url`이었고 `.env`의 `OPENAI_BASE_URL`이 **기본값을
덮어** 조건이 거짓이 됐다.

⚠ `openai_compat.py`가 *"기본을 localhost로 둬서 미설정을 안전하게 만든다(fail-safe)"* 로
의도를 적어 뒀고 **그 판단은 옳다.** 🔴 **다만 기본값이 안전한 것은 기본값이 쓰일 때뿐이고**
`env_file=".env"`가 그 전제를 깬다. ⇒ **안전은 기본값이 아니라 「명시적 허용」에 걸어야 한다.**

**방향을 뒤집는다:**

    지금:  localhost가 아니면      → 부른다    🔴 fail-open
    바꿔:  명시적 opt-in이 없으면  → 안 부른다  ✅ fail-closed

🔴 **opt-in은 `.env`를 읽지 않는다 — 프로세스 env만 본다.** `.env`는 한 번 넣으면 남고
**이 사고가 정확히 그 형태**였다. 셸 env는 그 명령에만 붙는다(선례: `runtime/tracing.py`가
`os.environ`을 직접 본다).

⚠ **이 파일은 실 API를 부르지 않는다** — 순수 함수와 소스 텍스트만 본다.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Final

import pytest

from ai.runtime.real_llm import (
    REAL_LLM_OPTIN_ENV,
    real_llm_optin,
    real_llm_skip_reason,
)

_ROOT: Final = Path(__file__).resolve().parents[3]

#: 🔴 실 LLM을 부를 수 있는 자리 전수(2026-08-09 실측 · **넷**).
#: ⚠ 종전 등재는 셋이었다 — `test_briefing_smoke.py`는 **`localhost` 판정조차 없어**
#:   세어지지 않았다(가장 나빴다).
_CALL_SITES: Final = (
    "src/ai/evaluation/counsel_llm_smoke.py",
    "tests/ai/integration/test_llm_smoke.py",
    "tests/ai/integration/test_pg_real_llm_smoke.py",
    "tests/ai/integration/test_briefing_smoke.py",
)

#: 종전 판정 — 이 문자열이 **실행되는 줄에** 남아 있으면 그 자리가 아직 fail-open이다.
#: 🔴 **주석·docstring은 대상이 아니다** — 첫 판이 *"종전 조건은 이러했다"* 를 적은 **내 주석을
#: 잡았다.** 「검사가 판정과 설명을 못 가른다」이고, 그러면 **정정을 적을 수 없다**
#: (적는 순간 red · 로그 103의 `오기`와 같은 ⓓ 논리). ⇒ `ast`로 **코드만** 본다.
_OLD_GATE: Final = re.compile(r'"localhost"\s+in\s+\w*\.?openai_base_url')


def _code_without_comments(path: Path) -> str:
    """주석을 뺀 소스 — `ast.unparse`가 주석을 버린다(선례: `test_counsel_runtime_lifetime`).

    ⚠ docstring은 `ast`가 노드로 들고 있어 남는다 — 그것까지 빼려면 아래에서 지운다.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Module | ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            continue
        body = node.body
        if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
            if isinstance(body[0].value.value, str):
                node.body = body[1:] or [ast.Pass()]
    return ast.unparse(tree)


def test_the_site_census_is_not_empty() -> None:
    """🔴 검사 경로가 끊기면 통과가 아니라 실패다."""
    missing = [site for site in _CALL_SITES if not (_ROOT / site).is_file()]
    assert not missing, f"실 LLM 호출 자리를 못 찾았다: {missing}"
    assert len(_CALL_SITES) >= 4, _CALL_SITES


def test_the_optin_reads_process_env_not_dotenv(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """🔴 **`.env`가 아니라 프로세스 env만 본다** — `.env`는 한 번 넣으면 남는다."""
    monkeypatch.delenv(REAL_LLM_OPTIN_ENV, raising=False)
    assert real_llm_optin() is False
    monkeypatch.setenv(REAL_LLM_OPTIN_ENV, "1")
    assert real_llm_optin() is True
    #: ⚠ 아무 값이나 켜지지 않는다 — 오타로 열리면 fail-closed가 아니다.
    monkeypatch.setenv(REAL_LLM_OPTIN_ENV, "0")
    assert real_llm_optin() is False


def test_a_real_server_url_is_still_skipped_without_optin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """🔴 **사고 재현 — `.env`가 실서버를 가리켜도 opt-in 없이는 안 부른다.**

    ⚠ **실제 호출은 하지 않는다** — 순수 함수에 URL을 넣어 **사유가 나오는지**만 본다.
    사고 당시 이 자리가 `None`(=부른다)이었다.
    """
    monkeypatch.delenv(REAL_LLM_OPTIN_ENV, raising=False)
    reason = real_llm_skip_reason("https://api.openai.com/v1")
    assert reason is not None, (
        "`.env`가 실서버를 가리키는데 skip 사유가 없다 — 이 상태가 사고였다"
    )
    assert REAL_LLM_OPTIN_ENV in reason, (
        f"사유가 조건을 안 말한다: {reason!r} — 「미설정」이 아니라 「허용이 없다」가 조건이다"
    )


def test_the_optin_lets_it_through(monkeypatch: pytest.MonkeyPatch) -> None:
    """⚠ 대조군 — opt-in이 있으면 통과한다. 전부 막으면 스모크 축이 죽는다."""
    monkeypatch.setenv(REAL_LLM_OPTIN_ENV, "1")
    assert real_llm_skip_reason("https://api.openai.com/v1") is None


def test_no_site_keeps_the_old_default_based_gate() -> None:
    """🔴 **판정이 한 자리다** — 종전 조건이 복제돼 있던 셋을 걷었는가.

    ⚠ `openai_compat.py`의 **주석**은 대상이 아니다(설명이고 판정이 아니다 · B 소유).
    """
    stale = [
        site
        for site in _CALL_SITES
        if _OLD_GATE.search(_code_without_comments(_ROOT / site))
    ]
    assert not stale, (
        f"기본값 전제 판정이 남아 있다: {stale} — `.env`가 덮으면 그 자리는 fail-open이다"
    )


def test_every_site_uses_the_shared_gate() -> None:
    """🔴 **넷 다 공용 게이트를 쓴다** — 하나라도 빠지면 그 자리로 나간다.

    ⚠ `test_briefing_smoke.py`는 종전에 **게이트가 아예 없었다**(`llm_provider="openai_compat"`
    를 강제해 바로 호출했다) — 그래서 사고 집계에서 빠졌다.
    """
    missing = [
        site
        for site in _CALL_SITES
        if "real_llm_skip_reason" not in (_ROOT / site).read_text(encoding="utf-8")
    ]
    assert not missing, f"공용 게이트를 안 쓰는 자리: {missing}"
