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

🔴 **(2026-08-14 재설계) 지키는 것은 「`.env`를 안 읽는다」가 아니라
「테스트가 실수로 실 LLM을 부르지 않는다」다.**

종전 처방은 `real_llm_optin()`이 `.env`를 아예 안 읽는 것이었다. 그게 **운영도 같이
막았다** — 8/14 윈도우에서 브리핑 11건이 전건 `provider_error`(`.env`에 스위치가 있는데
코드가 안 봤다). 03 §1 「`os.environ` 직접 접근 금지」 위반이기도 했다.

    설정은 `.env`를 읽는다                       ✅ 운영에서 먹는다
    막는 자리는 **테스트 진입점**으로 옮겼다        ✅ 99 #32 사고는 여기서 막는다
      tests/ai/fakes/real_llm_optin_pin.py       pytest 세션 전체
      src/ai/evaluation/pre_pr_verify.py         PR 전 검증

⚠ **막아야 할 것은 테스트지 설정이었던 적이 없다** — 99 #32는 `pytest -m integration`이
실 API를 부른 사고다. 이 파일은 그 **세 축**을 다 잠근다(§3 계열 검사).

⚠ **이 파일은 실 API를 부르지 않는다** — 순수 함수와 소스 텍스트만 본다.
"""

from __future__ import annotations

import ast
import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Final

import pytest

from ai.runtime.real_llm import (
    REAL_LLM_OPTIN_ENV,
    RealLlmSettings,
    build_real_openai_client,
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


def test_the_optin_is_off_unless_explicitly_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """🔴 **fail-closed** — 명시적으로 켠 값이 아니면 전부 꺼짐이다.

    ⚠ **이 검사는 종전 `test_the_optin_reads_process_env_not_dotenv`의 후신이다.**
    종전 이름이 지키던 것은 *"`.env`를 안 읽는다"* 라는 **수단**이었고, 진짜 목적은
    **"테스트가 실수로 실 LLM을 부르지 않는다"** 였다(모듈 docstring). 수단은 운영을
    같이 막아 버려서 폐기했고(8/14), 목적은 여기와 아래 세 축이 이어받는다.

    🔴 **오타로 열리지 않는다** — `"0"`·`"false"`·`"yep"`·빈 값 전부 꺼짐이다.
    ⚠ `"yep"`이 **예외가 아니라 꺼짐**인 것도 중요하다. `ValidationError`가 나면
    오타 하나가 브리핑을 500으로 만든다.
    """
    monkeypatch.delenv(REAL_LLM_OPTIN_ENV, raising=False)
    assert real_llm_optin() is False
    monkeypatch.setenv(REAL_LLM_OPTIN_ENV, "1")
    assert real_llm_optin() is True
    for typo in ("0", "false", "yep", "", "  "):
        monkeypatch.setenv(REAL_LLM_OPTIN_ENV, typo)
        assert real_llm_optin() is False, f"오타 {typo!r}로 열렸다 — fail-closed가 아니다"


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


def test_real_client_factory_blocks_before_constructing_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """opt-in이 없으면 SDK 생성자 대신 호출 차단 client를 반환한다."""
    monkeypatch.delenv(REAL_LLM_OPTIN_ENV, raising=False)
    called = False

    def _factory(**_kwargs: object) -> object:
        nonlocal called
        called = True
        return object()

    denied = object()
    client = build_real_openai_client(
        _factory,
        denied_client_factory=lambda _reason: denied,
        base_url="https://api.openai.com/v1",
        api_key="test-key",
        timeout_s=15.0,
    )
    assert called is False
    assert client is denied


def test_detect_import_does_not_assemble_real_provider() -> None:
    """라우터 import는 설정과 실 provider 조립을 실행하지 않는다."""
    env = os.environ.copy()
    env["LLM_PROVIDER"] = "openai_compat"
    env.pop(REAL_LLM_OPTIN_ENV, None)
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from ai.api.routers import detect; "
                "assert detect._brief_provider is None; "
                "assert detect._brief_gateway is None"
            ),
        ],
        cwd=_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr


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


def test_real_client_creation_has_one_production_gate() -> None:
    """실 client 생성 관문과 provider 생성 책임이 새 진입점으로 복제되지 않는다."""
    source_root = _ROOT / "src" / "ai"
    adapter = source_root / "llm" / "providers" / "openai_compat.py"
    gate_calls: list[Path] = []
    direct_provider_calls: list[Path] = []
    for path in source_root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
                continue
            if node.func.id == "build_real_openai_client":
                gate_calls.append(path)
            if node.func.id == "OpenAICompatProvider" and path != adapter:
                direct_provider_calls.append(path)

    assert gate_calls == [adapter], gate_calls
    assert not direct_provider_calls, direct_provider_calls


def test_no_production_code_constructs_async_openai_directly() -> None:
    """새 진입점도 ``AsyncOpenAI``를 중앙 opt-in 관문 밖에서 직접 만들지 않는다."""
    source_root = _ROOT / "src" / "ai"
    direct_calls: list[str] = []
    for path in source_root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported_names: set[str] = set()
        imported_modules: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "openai":
                imported_names.update(
                    alias.asname or alias.name
                    for alias in node.names
                    if alias.name == "AsyncOpenAI"
                )
            elif isinstance(node, ast.Import):
                imported_modules.update(
                    alias.asname or alias.name
                    for alias in node.names
                    if alias.name == "openai"
                )

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            direct_name_call = (
                isinstance(node.func, ast.Name) and node.func.id in imported_names
            )
            module_attribute_call = (
                isinstance(node.func, ast.Attribute)
                and node.func.attr == "AsyncOpenAI"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id in imported_modules
            )
            if direct_name_call or module_attribute_call:
                direct_calls.append(f"{path.relative_to(_ROOT)}:{node.lineno}")

    assert not direct_calls, (
        "AsyncOpenAI 직접 생성은 중앙 opt-in 관문을 우회한다: "
        f"{direct_calls} — 생성자를 build_real_openai_client에 전달하라"
    )


# ══ 🔴 세 축 — 「운영에서 먹는다」·「셸이 이긴다」·「테스트는 무시한다」 (2026-08-14) ══
#
# ⚠ 셋이 한 벌이다. 하나만 빼면 나머지가 거짓말이 된다:
#   첫째만 있으면  → 테스트가 실 API를 부른다 (99 #32 사고 재발)
#   셋째만 있으면  → 운영에서 안 먹는다        (8/14 브리핑 11건 사고 재발)


def _write_dotenv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    """`tmp_path`에 임시 `.env`를 쓰고 설정이 **그쪽을** 보게 한다.

    🔴 **저장소의 진짜 `.env`는 건드리지 않는다** — 개인 설정이고, 읽기만 해도 검사가
    개발자 기기에 따라 흔들린다(99 #57·#63이 그 병이었다).

    ⚠ `real_llm_optin_pin`이 세션 내내 `env_file`을 `None`으로 끊어 뒀으므로(그게 셋째
    축이다) 여기서 **명시로 되돌린다.** `monkeypatch`라 이 검사가 끝나면 다시 끊긴다.
    """
    monkeypatch.setitem(RealLlmSettings.model_config, "env_file", ".env")
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(f"{REAL_LLM_OPTIN_ENV}={value}\n", encoding="utf-8")


def test_dotenv_opts_in_for_the_server(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """🔴 `.env`의 값이 **읽힌다** — 2026-08-14 사고의 반대 방향.

    윈도우 AI 서버는 `.env`로 설정한다(셸 export는 IDE·서비스 기동에서 죽는다).
    종전 구현은 `os.environ`만 봐서 **그 `.env`가 안 먹었고** 브리핑 11건이 전건
    `provider_error`로 나갔다. 이 검사가 그 회귀를 막는다.
    """
    monkeypatch.delenv(REAL_LLM_OPTIN_ENV, raising=False)
    _write_dotenv(tmp_path, monkeypatch, "1")
    assert real_llm_optin() is True, (
        "`.env`의 opt-in이 안 읽힌다 — 이 상태가 2026-08-14 브리핑 11건 실패였다"
    )


def test_process_env_beats_dotenv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """셸에서 켜는 기존 사용법이 안 깨진다 (프로세스 env > `.env`).

    ⚠ **양방향으로 본다.** 한쪽만 보면 `.env`를 아예 안 읽는 구현도 통과한다.
    """
    _write_dotenv(tmp_path, monkeypatch, "1")
    monkeypatch.setenv(REAL_LLM_OPTIN_ENV, "0")
    assert real_llm_optin() is False, "프로세스 env의 `0`이 `.env`의 `1`을 못 이긴다"
    #: 대조군 — `.env`는 살아 있었다(위가 「`.env`를 안 읽음」으로 통과한 게 아니다).
    monkeypatch.delenv(REAL_LLM_OPTIN_ENV)
    assert real_llm_optin() is True

    _write_dotenv(tmp_path, monkeypatch, "0")
    monkeypatch.setenv(REAL_LLM_OPTIN_ENV, "1")
    assert real_llm_optin() is True, "셸에서 켜는 기존 사용법이 깨졌다"


def test_the_session_pin_is_registered() -> None:
    """🔴 핀이 `addopts`에 실려 있다 — **없으면 이 PR이 게이트를 약화시킨 것**이다.

    ⚠ **`pyproject.toml`을 직접 읽는다.** 핀 모듈을 import해서 확인하면 그 import가
    핀을 실행해 **검사가 스스로 통과한다**(99 #57에서 그 자충수를 한 번 썼다).
    """
    config = tomllib.loads((_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    addopts = config["tool"]["pytest"]["ini_options"]["addopts"]
    assert "-p real_llm_optin_pin" in addopts, (
        f"세션 핀이 addopts에 없다: {addopts!r} — `.env`가 테스트의 판정을 쥔다 (99 #32)"
    )


def test_pytest_session_ignores_dotenv_optin(tmp_path: Path) -> None:
    """🔴 `.env`에만 있으면 **테스트에서는 안 켜진다** (99 #32).

    ⚠ 이 검사가 없으면 이 PR은 게이트를 약화시킨다 — `pre_pr_verify`의
    `env.pop(REAL_LLM_OPTIN_ENV)`는 **프로세스 env만** 지워서, `.env`를 읽는 지금은
    그 직후 `.env`가 다시 올라오기 때문이다.

    🔴 **진짜 pytest 세션을 하나 띄워서 본다** — 핀이 「이 세션에 이미 적용돼 있다」를
    확인하는 형태로 쓰면 자기 자신을 근거로 삼게 된다. 하위 세션은 `.env`에 `=1`을 두고
    프로세스 env에서는 키를 **지운 채**(= `pre_pr_verify`가 만드는 상태) 시작한다.

    ⚠ **import 경로는 ini가 아니라 `PYTHONPATH`로 넘긴다 — 윈도우 함정이다.**
    pytest는 ini의 `paths` 타입을 `shlex.split`(posix 모드)으로 자르는데, 거기서
    **윈도우 역슬래시가 이스케이프 문자로 먹힌다**(2026-08-14 실측 ·
    `_pytest/config/__init__.py`). 구분자가 통째로 사라져 `C:CheckOn-AIsrc` 같은 값이 되고
    ⇒ 핀 import 실패 ⇒ 하위 세션 `exit=1` ⇒ **이 검사만 윈도우에서 상시 red**였다.
    🔴 그러면 `pre_pr_verify`가 **offline에서 멈춰 integration 단계가 아예 안 돈다**
    (99 #63과 같은 모양 · 준영님 기기 실측). **경로를 ini로 되돌리지 마라.**
    ⚠ 저장소 `pyproject.toml`은 안 걸린다 — 거기 `pythonpath`는 TOML **리스트**라
    `shlex`를 안 탄다. 이렇게 **생성하는 ini**만 해당한다(공백 있는 경로도 같이 산다).
    """
    (tmp_path / ".env").write_text(f"{REAL_LLM_OPTIN_ENV}=1\n", encoding="utf-8")
    (tmp_path / "pytest.ini").write_text(
        "[pytest]\naddopts = -p real_llm_optin_pin\n", encoding="utf-8"
    )
    (tmp_path / "test_optin_leak.py").write_text(
        "from ai.runtime.real_llm import real_llm_optin\n\n\n"
        "def test_session_is_off() -> None:\n"
        "    assert real_llm_optin() is False\n",
        encoding="utf-8",
    )
    env = os.environ.copy()
    env.pop(REAL_LLM_OPTIN_ENV, None)  # ⚠ `pre_pr_verify`가 하는 것과 같은 상태
    #: 핀은 `tests/ai/fakes`의 평면 import다(pyproject `pythonpath` 규약) — 하위 세션에도
    #: 같은 두 뿌리를 준다. ⚠ 물려받은 값이 있으면 **뒤에 붙인다**(덮지 않는다).
    import_roots = [str(_ROOT / "src"), str(_ROOT / "tests" / "ai" / "fakes")]
    inherited = env.get("PYTHONPATH")
    if inherited:
        import_roots.append(inherited)
    env["PYTHONPATH"] = os.pathsep.join(import_roots)
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, (
        "pytest 세션이 `.env`의 opt-in을 켰다 — 99 #32 사고 경로가 열려 있다\n"
        f"{result.stdout}\n{result.stderr}"
    )
