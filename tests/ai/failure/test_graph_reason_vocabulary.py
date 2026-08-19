"""`graph.py`가 내는 사유가 **와이어 어휘 안에 있다** — 사유가 태어나는 마지막 자리.

🔴 **PR-03 이 절반만 잠갔다.** 라우터(`REASON_*`)와 워커(`ERROR_*`)는 상수를 지나고
소스 검사가 리터럴 복귀를 막는데, **`graph.py`는 맨 리터럴 그대로**였다. 그러면:

    graph.py 에 `fail_reason="provider_down"` 을 넣는다
      → `wire_status_for` 가 `_FAILURE_WIRE.get(…, GATE_EXHAUSTED)` 기본값으로 **조용히** 떨어진다
      → `status_reason="provider_down"` 이 **그대로 와이어로 나간다**
      → `WIRE_STATUS_REASONS` 에 없다 · `error_codes.md §2.1` 에 없다 ⇒ **BE 화면 빈칸**
      → 🔴 **red 가 되는 검사가 하나도 없다**

⇒ `WIRE_STATUS_REASONS` docstring 의 약속(«집합을 늘리면 문서도 같이»)이 **절반만 참**이었다.
그리고 그건 8/19 정정 통보에 BE 에 적어 보낸 약속이기도 하다 —
«앞으로 이 문서가 코드와 갈리면 저희 CI 가 먼저 red 가 됩니다».

⚠ **소스 검사만으로는 부족하다** — 「리터럴을 안 썼다」는 보지만 「상수를 썼는데 그 상수가
어휘에 없다」는 못 본다. 그래서 아래 행동 검사가 **실제 응답**에서 잰다.
"""

from __future__ import annotations

import ast
import importlib.util
import re
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any, Final

import httpx
import pytest
from counsel_text import draft
from fastapi.testclient import TestClient

from ai.api.app import create_app
from ai.api.routers.counsel import reset_counsel_stores, set_counsel_provider
from ai.composition.counsel.provider import FakeCounselProvider
from ai.contracts.counsel import WIRE_STATUS_REASONS
from ai.contracts.llm import LlmTimeout
from ai.db.store_factory import reset_shared_agent_runtime

_HEADERS: Final = {
    "X-Tenant-Id": "t_graph_vocab",
    "X-Request-Id": "rq-graph-vocab",
    "Idempotency-Key": "iq_graph_vocab",
}
_GRAPH_SOURCE: Final = (
    Path(__file__).parents[3] / "src" / "ai" / "composition" / "counsel" / "graph.py"
)


def _request_body() -> dict[str, Any]:
    """🔴 계약 §4-① 예시를 복제하지 않는다 — 정본을 **파일로** 읽는다.

    ⚠ 평면 `import test_counsel_router`는 여기서 **안 된다** — `tests/ai/integration`이
    같은 디렉터리가 아니라 `sys.path`에 없다(실측: `ModuleNotFoundError`). 같은 폴더의
    `test_counsel_draft_body_missing._request_body`와 같은 방식이다.
    """
    target = (
        Path(__file__).resolve().parents[1] / "integration" / "test_counsel_router.py"
    )
    spec = importlib.util.spec_from_file_location("_counsel_router_contract", target)
    assert spec is not None and spec.loader is not None, target
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    body: dict[str, Any] = module._REQUEST
    return dict(body)


@pytest.fixture(autouse=True)
def _isolate() -> Iterator[None]:
    reset_shared_agent_runtime()
    reset_counsel_stores()
    yield
    reset_shared_agent_runtime()
    reset_counsel_stores()


def _post_and_fetch(drafts: Sequence[str] | None = None, *, key: str) -> dict[str, Any]:
    """POST → GET. 초안 실물은 GET 에만 실린다(202 는 2키다)."""
    if drafts is not None:
        set_counsel_provider(FakeCounselProvider(drafts=list(drafts)))
    headers = {**_HEADERS, "Idempotency-Key": key}
    with TestClient(create_app()) as client:
        posted: httpx.Response = client.post(
            "/v1/counsel/drafts", json=_request_body(), headers=headers
        )
        assert posted.status_code == 202, posted.text
        fetched: httpx.Response = client.get(
            f"/v1/counsel/drafts/{posted.json()['data']['job_id']}", headers=headers
        )
    assert fetched.status_code == 200, fetched.text
    result: dict[str, Any] = fetched.json()["data"]["result"]
    return result


def _literal_fail_reasons(path: Path) -> list[str]:
    """`fail_reason=` 에 **글자를 가진 문자열 상수**가 박힌 자리를 모은다.

    ⚠ `None` 과 `":"`(접두·상세 구분자)는 어휘가 아니라 **형태**라 통과시킨다.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: list[str] = []

    def constants(node: ast.expr) -> list[str]:
        if isinstance(node, ast.Constant):
            return [node.value] if isinstance(node.value, str) else []
        if isinstance(node, ast.JoinedStr):
            return [
                part.value
                for part in node.values
                if isinstance(part, ast.Constant) and isinstance(part.value, str)
            ]
        return []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        for keyword in node.keywords:
            if keyword.arg != "fail_reason":
                continue
            found.extend(
                text for text in constants(keyword.value) if re.search(r"[a-z]", text)
            )
    return found


class _AlwaysFailingWriter:
    """**생성 턴부터** 정해진 예외를 던지는 writer 대역.

    ⚠ 선례는 `tests/ai/integration/test_refine_failure_http.py::_FailingWriter` 인데
    그쪽은 `refine_instruction` 이 있을 때만 터진다(다듬기 축을 재려고). 여기는
    **초안 생성 경로**의 `graph.py` 를 재야 하므로 첫 호출부터 던진다.
    ⚠ 러너를 통째로 갈지 않는다 — provider 만 바꾸고 `graph.py` 는 실제 코드가 돈다.
    """

    def __init__(self, exc: Exception) -> None:
        self._exc = exc
        self.calls = 0

    async def plan(self, **_kwargs: object) -> dict[str, list[str]]:
        return {}

    async def write(self, **_kwargs: object) -> str:
        self.calls += 1
        raise self._exc


# ── 행동 축 — graph.py 가 실제로 내는 값이 어휘의 멤버다 ───────────


def test_a_gate_exhausted_draft_reports_a_known_reason() -> None:
    """게이트 소진 경로(`gate_exhausted:{게이트사유}`)의 **접두가 어휘에 있다.**

    ⚠ 근거가 없는 초안만 계속 주면 재생성 상한까지 가고 소진된다. 상세(`:too_short…`)는
    **와이어에 안 실린다** — `wire_status_for` 가 접두만 싣는다.
    """
    ungrounded = draft("정답률이 88%까지 올랐습니다.")
    result = _post_and_fetch([ungrounded] * 6, key="iq_graph_gate")

    assert result["draft_status"] == "gate_exhausted", result
    reason = result["status_reason"]
    assert reason in WIRE_STATUS_REASONS, (
        f"graph.py 가 어휘 밖의 사유를 냈다: {reason!r} — "
        "BE 는 이 값으로 화면 문구를 고른다(없으면 빈칸)"
    )
    assert ":" not in reason, f"상세가 와이어로 샜다: {reason!r}"


def test_an_llm_failure_reports_a_known_reason() -> None:
    """LLM 장애 경로(`llm_failed:{예외클래스}`)의 **접두가 어휘에 있다.**"""
    set_counsel_provider(_AlwaysFailingWriter(LlmTimeout("대역 — 모델에 못 닿았다")))
    result = _post_and_fetch(None, key="iq_graph_llm")

    assert result["draft_status"] == "llm_failed", result
    reason = result["status_reason"]
    assert reason in WIRE_STATUS_REASONS, (
        f"graph.py 가 어휘 밖의 사유를 냈다: {reason!r}"
    )
    assert ":" not in reason, f"예외 클래스명이 와이어로 샜다: {reason!r}"


# ── 소스 축 — 리터럴 복귀를 막는다 ────────────────────────────────


def test_the_graph_reason_literals_are_gone() -> None:
    """🔴 `graph.py` 가 `fail_reason` 에 **맨 리터럴을 안 쓴다.**

    ⚠ 행동으로는 못 잰다 — 리터럴이든 상수든 **같은 문자열**이 나간다. 값이 같은 두 형태를
    가르는 것은 소스뿐이다(PR-01 에서 배운 자리 · 결정 로그 121).

    🔴 **정규식이 아니라 AST 로 잰다** — 처음엔 정규식으로 짰다가 **주석 안의 문자열**에
    걸렸다(`graph.py:189` 가 *"값 문자열을 student의 `fail_reason="redaction_blocked"`와
    같게 뒀다"* 라고 적는다). 소스를 텍스트로 훑으면 **설명하는 글이 코드로 세어진다.**

    🔴 **이 검사가 못 보는 것**(PR-03 이 추출기 사각지대를 적어 둔 것과 같은 규율):
      ⓐ **`fail_reason` 이 아닌 이름으로 사유를 만들면 안 보인다** — 헬퍼를 하나 두고
        그 안에서 만들면 이 키워드에 안 걸린다. 위 행동 검사가 그 짝이다.
      ⓑ **상수를 썼는데 그 상수가 어휘에 없는 경우**도 안 보인다 — 이건 형태가 아니라
        값의 문제라 행동 검사(어휘 멤버 단언)가 잡는다.
    ⚠ f-string 안의 접두는 **본다** — `f"llm_failed:{x}"` 는 `JoinedStr` 안의 `Constant`라
      아래 수집기가 글자를 가진 상수를 전부 집는다. `":"` 하나는 형태라 통과시킨다.
    """
    literals = _literal_fail_reasons(_GRAPH_SOURCE)

    assert not literals, (
        f"graph.py 가 fail_reason 에 리터럴을 박고 있다: {literals} — "
        "어휘 상수를 쓰면 집합에 없는 값을 넣을 때 이름부터 없다"
    )


def test_the_failure_wire_table_is_keyed_by_the_constants() -> None:
    """🔴 `_FAILURE_WIRE` 의 키가 **상수**다 — 리터럴로 되돌리면 사본이 둘이 된다.

    PR-03 이 `ERROR_*` 의 집을 옮긴 것과 같은 이유다: 같은 값이 두 곳에 적혀 있으면
    한쪽만 고쳐지고, 그게 이 어휘가 애초에 고치려던 병이다.

    ⚠ 여기도 소스로 잰다 — 리터럴이든 상수든 dict 은 같은 키를 갖는다.
    """
    source = (
        Path(__file__).parents[3] / "src" / "ai" / "contracts" / "counsel.py"
    ).read_text(encoding="utf-8")
    table = source[source.index("_FAILURE_WIRE: Final") :]
    table = table[: table.index("}") + 1]

    literals = re.findall(r'^\s*"([a-z_]+)":', table, flags=re.MULTILINE)
    assert not literals, (
        f"_FAILURE_WIRE 의 키가 리터럴이다: {literals} — 상수를 키로 써야 사본이 안 는다"
    )


def test_the_vocabulary_still_derives_from_the_failure_table() -> None:
    """🔴 `WIRE_STATUS_REASONS` 가 `_FAILURE_WIRE` 를 **파생**한다 — 나열하지 않는다.

    나열로 바꾸면 `_FAILURE_WIRE` 에 키가 늘 때 **집합이 안 따라온다** — 새 접두가
    어휘 밖으로 조용히 새는 그 구멍이 다시 열린다.
    """
    source = (
        Path(__file__).parents[3] / "src" / "ai" / "contracts" / "counsel.py"
    ).read_text(encoding="utf-8")
    block = source[source.index("WIRE_STATUS_REASONS: Final") :]
    block = block[: block.index('\n"""')]

    assert "set(_FAILURE_WIRE)" in block, (
        "어휘 집합이 _FAILURE_WIRE 를 파생하지 않는다 — 상수를 나열하면 그 표에 키가 "
        "늘 때 집합이 안 따라온다"
    )
