"""㉒-a 재현 — 동의어 env로 추적을 켜면 가드가 침묵한다(불변식 3).

현행 기동 가드(#48 · `llm/gateway.py:120-128`)는 `LANGSMITH_TRACING` **하나만** 본다.
그런데 `langsmith.utils.tracing_is_enabled()`는 `get_env_var("TRACING_V2", default=
get_env_var("TRACING"))`이고 `get_env_var`가 `("LANGSMITH","LANGCHAIN")` 두 네임스페이스를
보므로 **동의어가 4개**다. 관례적 이름(`LANGCHAIN_TRACING_V2`)으로 설정하면 추적은 활성인데
가드는 조용하고, 마스킹 전 state(⑱부터 `emphasis_points`에 근거 라벨·수치·record_id 문면)가
외부 SaaS로 나가면서 앱은 에러 0이다.

실측(2026-07-31 · langsmith 0.10.2): 4개 중 **3개가 가드를 통과**했고, 워커 그래프 1회 실행에
`POST /runs/multipart`(Content-Length 15606) 시도가 관찰됐다 — `part_a/11` §7 참조.

⚠ 이 테스트는 env만 조작한다 — 실키·실엔드포인트·실전송 0.
"""

from __future__ import annotations

import pytest

#: `tracing_is_enabled()`가 실제로 읽는 env 전수 — 소스가 정본이다.
#: `langsmith/utils.py:141` `get_env_var("TRACING_V2", default=get_env_var("TRACING"))`
#: × `get_env_var(..., namespaces=("LANGSMITH","LANGCHAIN"))`(`:423`) = 2 × 2.
TRACING_ENV_SYNONYMS = (
    "LANGSMITH_TRACING_V2",
    "LANGCHAIN_TRACING_V2",
    "LANGSMITH_TRACING",
    "LANGCHAIN_TRACING",
)


@pytest.fixture(autouse=True)
def _isolate_tracing_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """추적 관련 env를 전부 지운 상태에서 시작한다(개발자 로컬 설정 무관)."""
    for name in TRACING_ENV_SYNONYMS:
        monkeypatch.delenv(name, raising=False)
    # langsmith는 env를 lru_cache로 기억한다 — 케이스마다 비운다.
    from langsmith.utils import get_env_var

    get_env_var.cache_clear()  # type: ignore[attr-defined]


def _tracing_enabled() -> bool:
    from langsmith.utils import get_env_var, tracing_is_enabled

    get_env_var.cache_clear()  # type: ignore[attr-defined]
    return tracing_is_enabled() is not False


# ── 동의어 전수 — 라이브러리 판정 ─────────────────────────────────


@pytest.mark.parametrize("env_name", TRACING_ENV_SYNONYMS)
def test_every_synonym_enables_tracing(
    env_name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """4개 전부 langsmith에겐 '추적 켜짐'이다 — 목록이 새면 여기서 잡힌다."""
    monkeypatch.setenv(env_name, "true")
    assert _tracing_enabled() is True


def test_no_synonym_means_disabled() -> None:
    """전부 미설정이면 꺼짐 — 가드가 평소에 방해하지 않는다는 전제."""
    assert _tracing_enabled() is False


def test_synonym_list_matches_library_source() -> None:
    """라이브러리가 읽는 이름을 우리가 **손으로 복제**하지 않았는지 — 드리프트 방지.

    `get_env_var`의 네임스페이스와 `tracing_is_enabled`의 변수명에서 조합을 재계산해
    상수와 대조한다. langsmith가 이름을 늘리면 red가 된다.
    """
    import inspect

    from langsmith.utils import get_env_var, tracing_is_enabled

    namespaces = inspect.signature(get_env_var).parameters["namespaces"].default
    source = inspect.getsource(tracing_is_enabled)
    names = [n for n in ("TRACING_V2", "TRACING") if f'"{n}"' in source]
    expected = {f"{ns}_{name}" for name in names for ns in namespaces}
    assert set(TRACING_ENV_SYNONYMS) == expected, (
        f"langsmith가 읽는 env가 바뀌었다: {expected}"
    )


# ── A 소유 표면(워커 조립부)이 막는가 ─────────────────────────────


@pytest.mark.parametrize("env_name", TRACING_ENV_SYNONYMS)
def test_counsel_runner_refuses_to_start_when_tracing_active(
    env_name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """추적이 켜져 있고 P2 은닉이 없으면 **기동 거부**(fail-closed).

    `LANGSMITH_TRACING` 단독(기존 경로)도 여전히 거부돼야 한다 — 회귀 방지.
    """
    import asyncio

    monkeypatch.setenv(env_name, "true")
    from ai.composition.counsel.assembly import open_counsel_pack_runner

    async def _open() -> None:
        async with open_counsel_pack_runner(
            supervisor=None,  # type: ignore[arg-type]
            context_store=None,  # type: ignore[arg-type]
            step_sink=None,  # type: ignore[arg-type]
            lease_owner="w",
            planner=None,  # type: ignore[arg-type]
            writer=None,  # type: ignore[arg-type]
        ):
            pass

    with pytest.raises(ValueError, match="외부 트레이싱"):
        asyncio.run(_open())


@pytest.mark.parametrize("env_name", TRACING_ENV_SYNONYMS)
def test_probe_runner_refuses_to_start_when_tracing_active(
    env_name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """mapping_probe도 같은 위험 표면이다(11 §3 — LangGraph 워커 둘)."""
    import asyncio

    monkeypatch.setenv(env_name, "true")
    from ai.import_mapping.probe.assembly import open_mapping_probe_runner

    async def _open() -> None:
        async with open_mapping_probe_runner(
            supervisor=None,  # type: ignore[arg-type]
            lease_owner="w",
        ):
            pass

    with pytest.raises(ValueError, match="외부 트레이싱"):
        asyncio.run(_open())


def test_guard_message_names_detected_env_but_never_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """감지된 env **이름**은 알려주되 **값은 절대 출력하지 않는다**.

    가드가 막으려던 유출을 에러 메시지가 하면 안 된다 — 근처에 `LANGSMITH_API_KEY`가 있다.
    """
    import asyncio

    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "true")
    monkeypatch.setenv("LANGSMITH_API_KEY", "lsv2_secret_value_do_not_leak")
    from ai.composition.counsel.assembly import open_counsel_pack_runner

    async def _open() -> None:
        async with open_counsel_pack_runner(
            supervisor=None,  # type: ignore[arg-type]
            context_store=None,  # type: ignore[arg-type]
            step_sink=None,  # type: ignore[arg-type]
            lease_owner="w",
            planner=None,  # type: ignore[arg-type]
            writer=None,  # type: ignore[arg-type]
        ):
            pass

    with pytest.raises(ValueError) as exc:
        asyncio.run(_open())
    message = str(exc.value)
    assert "LANGCHAIN_TRACING_V2" in message  # 이름은 알려준다
    assert "lsv2_secret_value_do_not_leak" not in message  # 값은 절대
    assert "true" not in message.split("감지된 env")[-1].split("\n")[0]


def test_runner_starts_normally_when_tracing_off() -> None:
    """추적 off(정본)면 가드가 방해하지 않는다 — CI·테스트 경로 무영향."""
    from ai.runtime.tracing import external_tracing_active

    assert external_tracing_active() is False


# ── langsmith import 격리 (AST 계약) ──────────────────────────────


def test_langsmith_import_is_confined_to_runtime_tracing() -> None:
    """`langsmith` import는 **`runtime/tracing.py`에서만**.

    `runtime/`엔 `redaction.py`(개인정보 경로)가 산다 — 03 §1b "개인정보 경로엔 외부 전송
    라이브러리 금지". 판정 술어 하나를 쓰려고 클라이언트를 패키지 전역에 퍼뜨리지 않는다
    (`llm/providers/`에서만 openai SDK를 허용한 선례와 같은 결).
    """
    import ast
    from pathlib import Path

    src = Path(__file__).resolve().parents[3] / "src" / "ai"
    allowed = "runtime/tracing.py"
    files = sorted(p for p in src.rglob("*.py") if "__pycache__" not in p.parts)
    assert files, "src/ai에서 .py를 하나도 찾지 못했다 — 검사 경로가 끊겼다"

    offenders: list[str] = []
    for path in files:
        rel = path.relative_to(src).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            if any(n.split(".")[0] == "langsmith" for n in names) and rel != allowed:
                offenders.append(f"{rel}:{node.lineno}")
    assert not offenders, (
        f"langsmith import가 {allowed} 밖에 있다: {offenders}. "
        "판정이 필요하면 runtime/tracing.py의 함수를 쓰라."
    )
