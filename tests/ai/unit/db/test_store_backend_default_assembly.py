"""**기본 설정 하나로 조립부 전체가 PG로 서는가** (99 #39 · 기본값 플립).

🔴 **한 축만 PG로 주입해 놓고 「플립됐다」고 말하면 안 된다.** 이 시리즈가 실제로 그랬다 —
`test_counsel_read_model_router_pg`는 읽기 모델 **하나만** 주입하고 나머지는 memory로 뒀고
(그 파일 docstring이 이유를 적는다), `#37`은 counsel 스텝 싱크가 **어떤 빌더도 안 타서**
`STORE_BACKEND=pg`를 켜도 PG에 안 앉는 상태를 8/11에 실측했다. **팩토리가 있다는 것과
기본 조립이 그것을 탄다는 것은 다른 사실이다.**

⇒ 여기서는 **환경 변수를 지운 진짜 기본 설정**으로 팩토리 전수를 부른다. 그리고
**명시적 `STORE_BACKEND=memory`** 에서는 종전 구현이 그대로 나오는지도 같이 본다
(플립이 memory를 없앤 것이 아니다).

🔴 **새 팩토리가 표에서 빠지면 red다.** 표를 손으로 들면 다음에 생기는 팩토리는 조용히
빠지고, 그 저장소만 memory로 남는다 — `#37`이 정확히 그 형태였다. 그래서 표의 이름 집합을
**모듈에서 파생한 이름 집합과 대조**한다.

🔴 **`store_backend`를 보는 자리는 팩토리만이 아니다.** 체크포인터 셋(`_open_saver`)도 같은
플래그로 갈린다. 그것들은 **접속을 여는 async 컨텍스트**라 DB 없이 못 부르므로, 여기서는
**「안 본다」를 사유와 함께 명시**한다 — 목록에 없는 새 분기가 생기면 red다.

⚠ **DB에 접속하지 않는다** — PG 저장소 생성은 엔진을 lazy로 만들 뿐이다(`db/session.py`).
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from pathlib import Path
from typing import Any, Final

import pytest

from ai.agents.job_store import InMemoryJobStore
from ai.composition.counsel.stores import (
    InMemoryAgentStepSink as InMemoryCounselAgentStepSink,
)
from ai.composition.counsel.stores import InMemoryPackResultStore
from ai.db import store_factory
from ai.db.counsel_read_model import (
    NullCounselDraftViewStore,
    PgCounselDraftViewStore,
)
from ai.db.repositories.agent_job import PgJobStore
from ai.db.repositories.counsel_step_store import PgCounselAgentStepSink
from ai.db.repositories.detection_store import InMemoryDetectionStore, PgDetectionStore
from ai.db.repositories.idempotency import (
    InMemoryIdempotencyStore,
    PgIdempotencyStore,
)
from ai.db.repositories.inquiry_class_store import (
    InMemoryInquiryClassStore,
    PgInquiryClassStore,
)
from ai.db.repositories.pack_store import PgPackResultStore
from ai.db.repositories.probe_stores import (
    PgAgentStepSink,
    PgProfileStore,
    PgSpecResultStore,
)
from ai.db.repositories.problem_store import PgProblemItemStore
from ai.db.repositories.run_store import InMemoryRunStore, PgRunStore
from ai.db.session import get_engine
from ai.db.settings import get_db_settings
from ai.import_mapping.probe.stores import (
    InMemoryAgentStepSink,
    InMemoryProfileStore,
    InMemorySpecResultStore,
)
from ai.problem_generation.assembly import build_tenant_scoped_item_store

_SRC: Final = Path(__file__).resolve().parents[4] / "src" / "ai"

#: (팩토리 이름, 기본=pg에서 나와야 하는 타입, 명시 memory에서 나와야 하는 타입).
#: ⚠ 읽기 모델의 memory 짝은 `Null…`이다 — 인메모리 dict를 하나 더 두면 정본이 둘이 된다
#:   (`build_counsel_draft_view_store` docstring).
_FACTORIES: Final[tuple[tuple[str, type, type], ...]] = (
    ("build_inquiry_class_store", PgInquiryClassStore, InMemoryInquiryClassStore),
    ("build_run_store", PgRunStore, InMemoryRunStore),
    ("build_agent_job_store", PgJobStore, InMemoryJobStore),
    ("build_idempotency_store", PgIdempotencyStore, InMemoryIdempotencyStore),
    ("build_detection_store", PgDetectionStore, InMemoryDetectionStore),
    ("build_profile_store", PgProfileStore, InMemoryProfileStore),
    ("build_spec_result_store", PgSpecResultStore, InMemorySpecResultStore),
    ("build_pack_result_store", PgPackResultStore, InMemoryPackResultStore),
    (
        "build_counsel_draft_view_store",
        PgCounselDraftViewStore,
        NullCounselDraftViewStore,
    ),
    (
        "build_counsel_agent_step_sink",
        PgCounselAgentStepSink,
        InMemoryCounselAgentStepSink,
    ),
    ("build_agent_step_sink", PgAgentStepSink, InMemoryAgentStepSink),
)

#: `store_backend`를 보지만 **이 파일이 안 부르는** 자리 — 사유를 함께 적는다.
#: 🔴 위반을 세지 않고 **위반이 아닌 것을 사유와 함께 열거**한다(fail-closed). 새 분기가
#: 생기면 여기에도 표에도 없으므로 red다.
_UNCALLED_BRANCHES: Final[dict[str, str]] = {
    "ai.composition.counsel.assembly::_open_saver": (
        "pg 분기가 AsyncPostgresSaver **커넥션**을 여는 async 컨텍스트다 — DB 없이 못 부른다. "
        "수명 규약(pg=요청 스코프·memory=프로세스 공용)은 그 파일의 전용 검사가 든다"
    ),
    "ai.import_mapping.probe.assembly::_open_saver": (
        "위와 같은 형태(probe 축) — 같은 플래그로 체크포인터를 고른다"
    ),
    "ai.problem_generation.assembly::_open_saver": (
        "위와 같은 형태(B 축) — B 소유 파일이라 이 회차가 실행 경로를 넓히지 않는다"
    ),
    "ai.problem_generation.assembly::build_tenant_scoped_item_store": (
        "**아래 전용 검사가 직접 부른다** — `tenant_id`가 필수 인자이고 memory에서 "
        "`None`(=교체할 것이 없다)을 돌려주므로 위 표의 (pg타입, memory타입) 모양에 안 맞는다"
    ),
}


@pytest.fixture
def default_settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """🔴 **환경 변수를 지우고 진짜 기본값으로 돌린다.**

    ⚠ `tests/conftest.py`가 오프라인 회귀용으로 `STORE_BACKEND=memory`를 걸어 둔다 —
    **그걸 지우지 않으면 이 파일은 자기가 재려는 것을 자기가 가린다.**
    ⚠ `lru_cache` 둘을 함께 비운다 — 설정과 엔진이 굳어 있으면 옛 값으로 조립된다.
    """
    monkeypatch.delenv("STORE_BACKEND", raising=False)
    get_db_settings.cache_clear()
    get_engine.cache_clear()
    try:
        yield
    finally:
        get_engine.cache_clear()
        get_db_settings.cache_clear()


@pytest.fixture
def memory_settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("STORE_BACKEND", "memory")
    get_db_settings.cache_clear()
    try:
        yield
    finally:
        get_db_settings.cache_clear()


def test_the_pin_is_actually_removed(default_settings: None) -> None:
    """🔴 **절단 가드** — 핀이 남아 있으면 아래 검사들은 memory를 재고 통과한다.

    ⚠ 이 파일이 재려는 것은 **「환경 변수가 없을 때」**다. 핀이 살아 있으면 `default_settings`
    아래에서도 `memory`가 나오고, 그러면 pg 단정은 전부 red가 되어 **결함처럼 보인다** —
    그 혼동을 여기서 먼저 끊는다.
    """
    del default_settings
    import os  # noqa: PLC0415 — 환경 자체를 보는 검사라 여기서만 쓴다

    assert "STORE_BACKEND" not in os.environ, "핀이 안 지워졌다 — 이 파일은 memory를 재고 있다"
    assert get_db_settings().store_backend == "pg", "기본 설정이 pg가 아니다"


@pytest.mark.parametrize(("name", "pg_type", "_memory_type"), _FACTORIES)
def test_the_default_assembly_returns_the_pg_store(
    default_settings: None, name: str, pg_type: type, _memory_type: type
) -> None:
    """🔴 **환경 변수를 하나도 안 준 기본 조립**이 PG 구현을 내야 한다."""
    del default_settings, _memory_type
    built = getattr(store_factory, name)()
    assert isinstance(built, pg_type), (
        f"{name}()가 기본 설정에서 {type(built).__name__}를 냈다 — "
        f"이 저장소만 조용히 휘발성으로 남는다"
    )


@pytest.mark.parametrize(("name", "_pg_type", "memory_type"), _FACTORIES)
def test_explicit_memory_still_returns_the_in_memory_store(
    memory_settings: None, name: str, _pg_type: type, memory_type: type
) -> None:
    """플립이 memory를 없앤 것이 아니다 — **명시 선택**은 그대로 산다."""
    del memory_settings, _pg_type
    built = getattr(store_factory, name)()
    assert isinstance(built, memory_type), (
        f"{name}()가 명시 memory에서 {type(built).__name__}를 냈다"
    )


def test_the_tenant_scoped_item_store_follows_the_same_flag(
    default_settings: None,
) -> None:
    """B 축 저장소 — 기본에서 PG, 명시 memory에서 **`None`**(교체할 것이 없다)."""
    del default_settings
    assert isinstance(
        build_tenant_scoped_item_store(tenant_id="t_flip"), PgProblemItemStore
    )


def test_the_tenant_scoped_item_store_is_none_on_memory(memory_settings: None) -> None:
    del memory_settings
    assert build_tenant_scoped_item_store(tenant_id="t_flip") is None


# ───────────────────────── 표가 낡지 않게 하는 가드 ─────────────────────────


def _builder_names() -> set[str]:
    """`store_factory`가 실제로 내보내는 `build_*` 전수 — 표가 아니라 **모듈**에서 온다."""
    return {name for name in dir(store_factory) if name.startswith("build_")}


def test_every_factory_in_the_module_is_covered() -> None:
    """🔴 **새 팩토리가 조용히 빠지지 못한다.**

    ⚠ 표를 손으로만 들면 다음에 생기는 팩토리는 **아무 검사도 안 받고** memory로 남는다 —
    `#37`(counsel 스텝 싱크)이 정확히 그 형태였고 **PR 넷을 지나 8/11에 발견**됐다.
    """
    covered = {name for name, _pg, _mem in _FACTORIES}
    missing = sorted(_builder_names() - covered)
    assert not missing, f"표에 없는 팩토리가 있다: {missing} — 기본 조립을 아무도 안 본다"
    stale = sorted(covered - _builder_names())
    assert not stale, f"표에 없어진 팩토리가 남아 있다: {stale}"


def _pg_branch_sites() -> dict[str, str]:
    """`store_backend`를 비교하는 자리 전수 → `모듈::함수`.

    🔴 **행 번호로 세지 않는다** — 한 줄만 밀려도 목록이 낡는다. 감싸는 함수 이름으로 센다.
    """
    sites: dict[str, str] = {}
    for path in sorted(_SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        module = "ai." + str(path.relative_to(_SRC.parent).with_suffix("")).replace(
            "/", "."
        ).removeprefix("ai.")
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            for inner in ast.walk(node):
                if isinstance(inner, ast.Compare) and _reads_backend(inner.left):
                    sites[f"{module}::{node.name}"] = module
    return sites


def _reads_backend(expr: ast.expr) -> bool:
    return isinstance(expr, ast.Attribute) and expr.attr == "store_backend"


def test_every_backend_branch_is_either_called_or_excused() -> None:
    """🔴 **분기가 늘면 red다** — 「안 본다」는 사유와 함께만 허용한다.

    ⚠ 위반을 세는 검사가 아니라 **위반이 아닌 것을 열거**하는 검사다. 새 분기는
    표에도 사유 목록에도 없으므로 자동으로 잡힌다(fail-closed).
    """
    called = {f"ai.db.store_factory::{name}" for name, _pg, _mem in _FACTORIES}
    sites = set(_pg_branch_sites())
    assert sites, "분기를 하나도 못 찾았다 — 스캐너가 끊겼다(0건은 「없다」가 아니다)"
    unaccounted = sorted(sites - called - set(_UNCALLED_BRANCHES))
    assert not unaccounted, (
        f"`store_backend`를 보는데 아무도 안 보는 자리가 있다: {unaccounted} — "
        f"표에 넣거나 사유를 적어라"
    )
    stale = sorted(set(_UNCALLED_BRANCHES) - sites)
    assert not stale, f"사유 목록이 낡았다(분기가 사라졌다): {stale}"


def test_the_excuses_are_not_blank() -> None:
    """사유 칸을 비워 두면 목록이 **면제 도장**이 된다."""
    for site, reason in _UNCALLED_BRANCHES.items():
        assert len(reason) >= 20, f"{site}의 사유가 비었다"


# ───────────────────────── conftest 복구망이 실제로 무는가 ─────────────────────────


def test_a_leaked_pin_is_repaired_before_the_next_test(request: Any) -> None:  # noqa: ANN401
    """🔴 **복구망 자체를 검사한다** — 안 물면 이 저장소의 회귀 2 640건이 pg로 돈다.

    ⚠ 실측한 형태 그대로 재현한다: 환경 변수를 지우고 **캐시를 채운 뒤** 끝낸다.
    종전 초안은 환경 변수만 봤는데, `monkeypatch`가 변수는 되돌려도 **`lru_cache`에 pg
    설정이 남아** 다음 검사가 pg로 조립됐다(전량 실행에서만 red · 단독은 통과).
    """
    del request
    import os  # noqa: PLC0415

    os.environ.pop("STORE_BACKEND", None)
    get_db_settings.cache_clear()
    assert get_db_settings().store_backend == "pg", "누출 상태를 못 만들었다"
    #: ⚠ 여기서 끝난다 — 복구는 **다음 검사 전에** conftest의 autouse가 한다.


def test_the_pin_is_back(request: Any) -> None:  # noqa: ANN401
    """위 검사가 남긴 누출이 **여기 오기 전에** 복구돼 있어야 한다."""
    del request
    import os  # noqa: PLC0415

    assert os.environ.get("STORE_BACKEND") == "memory", "핀이 복구되지 않았다"
    assert get_db_settings().store_backend == "memory", (
        "환경은 돌아왔는데 설정 캐시가 pg다 — 복구망이 캐시를 안 본다"
    )


# ────────────── 플립이 새로 요구하는 배포 선행 단계 ──────────────


_CHECKPOINTER: Final = _SRC / "agents" / "checkpointer.py"
_SETUP: Final = "setup_checkpointer_schema"
_ENTRY: Final = "main"


def _checkpointer_tree() -> ast.Module:
    return ast.parse(_CHECKPOINTER.read_text(encoding="utf-8"))


def _called_name(call: ast.Call) -> str:
    """`f(...)`는 `f` · `mod.f(...)`는 `f` — 호출 대상의 **마지막 이름**."""
    func = call.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def _calls_inside(function_name: str, *, called: str) -> int:
    """`function_name` 본문에서 `called`를 **호출하는** 횟수 — 이름 언급이 아니라 호출이다.

    🔴 **docstring·주석·import는 안 센다** — `ast.Call`의 `func`만 본다.
    ⚠ `asyncio.run`처럼 **점 있는 호출**도 센다(처음엔 `ast.Name`만 봐서 놓쳤다).
    """
    for node in ast.walk(_checkpointer_tree()):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name == function_name:
            return sum(
                1
                for inner in ast.walk(node)
                if isinstance(inner, ast.Call) and _called_name(inner) == called
            )
    return 0


def test_the_deployment_entry_point_runs_the_setup_function() -> None:
    """🔴 **배포 명령이 정해진 초기화 함수를 실제로 실행하는가** (99 #39).

    ⚠ **종전 가드는 저장소 전체 문자열 검색이었고 거짓 green이었다.** 통합 테스트의
    실제 호출을 **지워도 통과**했다 — **자기 파일의 docstring**에 함수 이름이 있었기
    때문이다(실측: 실제 호출 **0건**인데 **1 passed**). *"호출처가 하나 이상"* 은
    **테스트와 문서까지 운영 호출처로 계산**한다.

    ⇒ 묻는 것을 바꾼다: **`python -m ai.agents.checkpointer`가 그 함수를 실행하는가.**
    ⚠ **정확히 한 번**이다 — 두 번 부르면 배포 로그가 무엇을 말하는지 흐려진다.
    """
    assert _calls_inside(_ENTRY, called=_SETUP) == 1, (
        f"`{_ENTRY}()`가 `{_SETUP}()`를 정확히 한 번 부르지 않는다 — "
        f"배포 명령이 스키마를 준비하지 않는다(99 #39)"
    )


def test_the_entry_point_actually_runs_it_rather_than_building_a_coroutine() -> None:
    """🔴 `asyncio.run(...)`으로 **실행**해야 한다 — 코루틴만 만들면 아무 일도 안 난다.

    ⚠ 그 실수는 **경고 하나 없이 exit 0**이다(`RuntimeWarning: never awaited`는 stderr에만).
    """
    assert _calls_inside(_ENTRY, called="run") >= 1, (
        f"`{_ENTRY}()`가 `asyncio.run(...)`을 안 쓴다 — 코루틴을 만들고 버릴 수 있다"
    )


def test_module_execution_reaches_the_entry_point() -> None:
    """`if __name__ == "__main__":`가 **`main()`을 실행**하는가.

    ⚠ 없으면 `python -m ai.agents.checkpointer`가 **아무 일도 안 하고 exit 0**이다.
    """
    guarded = [
        node
        for node in _checkpointer_tree().body
        if isinstance(node, ast.If)
        and isinstance(node.test, ast.Compare)
        and any(
            isinstance(cmp_, ast.Constant) and cmp_.value == "__main__"
            for cmp_ in node.test.comparators
        )
    ]
    assert guarded, "`if __name__ == \"__main__\":` 블록이 없다"
    called = {
        _called_name(inner)
        for node in guarded
        for inner in ast.walk(node)
        if isinstance(inner, ast.Call)
    }
    assert _ENTRY in called, f"모듈 실행 경로가 `{_ENTRY}()`를 안 부른다"


def test_a_test_calling_the_setup_is_not_a_deployment_entry_point() -> None:
    """🔴 **이 가드가 무엇을 안 세는지** 못 박는다 — 통합 테스트도, 이 파일도 아니다.

    ⚠ 종전 가드를 만족시키려면 **함수 이름을 아무 데나 적으면** 됐다. 그러면 규율이
    아니라 **의식**이 된다(로그 계열). 여기서는 **`checkpointer.py`의 `main()` 안**만 본다.
    """
    tests_root = _SRC.parent.parent / "tests"
    mentioning = [
        path
        for path in sorted(tests_root.rglob("*.py"))
        if _SETUP in path.read_text(encoding="utf-8")
    ]
    assert mentioning, "이 파일이 그 이름을 적고 있으므로 최소 1건이어야 한다(스캐너 절단 가드)"
    #: 🔴 **그 수는 판정에 안 쓰인다** — 위 세 검사 중 어디에도 `mentioning`이 안 들어간다.
    assert _calls_inside(_ENTRY, called=_SETUP) == 1


def test_the_entry_point_does_not_print_connection_settings() -> None:
    """🔴 실패 문면에 **DSN·비밀번호**를 싣지 않는다 — 예외 타입만 내보낸다."""
    source = _CHECKPOINTER.read_text(encoding="utf-8")
    entry = source[source.index(f"def {_ENTRY}(") :]
    for leak in ("database_url", "connection_string", "checkpoint_connection_string"):
        assert leak not in entry, f"배포 명령이 접속정보를 문면에 싣는다: {leak}"
