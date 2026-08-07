"""🔴 예약 태그(`apply`)가 **출제 요청 문**에서 거절돼야 한다 (99 ㊨).

#134가 `TypeTag.APPLY`를 계약에 넣으면서 **다섯 문 중 하나가 안 닫혔다.** 04·`error_codes`·
`policies/taxonomy.md`·`contracts/taxonomy.py`가 전부 *"출제 요청에 보내면 400
`type_tag_not_supported`"* 라고 적었는데 **400이 안 난다.** 구현은 B 몫(소유·새 동작)이고
이 파일은 **구멍을 red로 세워 두는 것**이 전부다.

## 도달 체인 (8/9 실측 — 추론이 아니다)

    ProblemRequest.model_validate   통과 (enum이 유효하다 · 라우터에 v1 allowlist가 없다)
    workflow.py:536  소스 게이트     통과 (area_tag·passage만 본다)
    generator                        `"type_tag": "apply"` 로 생성
    rules.py 요청 일치 검사          통과 (요청도 apply다)
    difficulty.py:22                 weights.type_tag[item.type_tag] → 🔴 KeyError
    workflow.py 어느 except도 안 잡음 (LookupError 절은 _item_store.get 전용 · 다른 스코프)
    assembly.py:238 except Exception → 잡을 failed로 표시하고 **재던진다**
    routers/problem.py:336           run_next()를 POST 안에서 **동기 실행**하고 감싸지 않는다
    api/app.py _unhandled            → **HTTP 500**

🔴 **우리가 열었다.** `policy.py`의 완전성 검사를 `set(V1_TYPE_TAGS)`로 좁히면서 **설정
로딩은 통과시키고 런타임 조회(`difficulty.py:22`의 dict 인덱싱)는 안 막았다.** 원래 검사가
지키던 것은 설정 파일 하나가 아니라 **그 설정을 인덱싱하는 모든 자리**였다.

## 왜 `xfail(strict=True)`인가

선례는 `test_version_scope_purity.py`(#133)다. **화이트리스트가 아니다** — strict라
**B 구현이 머지되는 순간 XPASS로 red**가 나고, 그때 마커를 지우면 초록이다. 화이트리스트는
영원히 조용하지만 이건 **고쳐지면 시끄럽다.** `skip`으로 바꾸지 마라 — skip은 조용하다.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Final

import httpx
import pytest
from fastapi.testclient import TestClient

from ai.api.app import create_app
from ai.api.routers import problem as problem_router
from ai.contracts.diagnosis import DiagnosisResult
from ai.contracts.problem_generation import (
    Answer,
    Choice,
    EvidenceAnchor,
    EvidenceKind,
    GeneratedItem,
    ProblemRequest,
)
from ai.contracts.taxonomy import (
    RESERVED_TYPE_TAGS,
    V1_TYPE_TAGS,
    AreaTag,
    ItemFormat,
    TypeTag,
)
from ai.db.repositories.run_store import InMemoryRunStore
from ai.db.store_factory import reset_shared_agent_runtime
from ai.problem_generation.assembly import problem_runtime_stores
from ai.problem_generation.provider import ProblemProviders

_FAKES_DIR = Path(__file__).parents[1] / "fakes"
sys.path.insert(0, str(_FAKES_DIR))

from fake_graph_context import FakeGraphContextService  # noqa: E402
from fake_provider import FakeProvider  # noqa: E402

_SKILL_NODE_ID: Final = "grammar.sentence-structure"
_HEADERS: Final = {
    "X-Tenant-Id": "tenant-reserved-tag",
    "X-Request-Id": "request-reserved-tag",
}

#: 🔴 **작업 1 ③④의 실측**(8/9 · `f99bec0`). 추론이 아니라 관측이다 — B가 이걸 보고
#: 고치고, 고친 뒤 무엇이 바뀌었는지 이 문장과 대조한다.
_PENDING_B_FIX_REASON: Final = (
    "예약 태그 거절이 아직 없다 — 라우터(`routers/problem.py::_problem_request`)가 "
    "`ProblemRequest.model_validate()`의 enum 검증만 쓰므로 `apply`가 그대로 통과한다. "
    "🔴 실측(8/9 · f99bec0 · 대역 provider): POST /v1/problems 가 **HTTP 500 "
    "`INTERNAL`**(envelope·meta.versions는 정상, `data=null`, `error.detail=null`)을 내고, "
    "잡은 **만들어져 `phase=failed` / `error_code='problem_worker_internal'`로 수렴하지만 "
    "`job_id`가 응답에 없어 BE가 조회할 수 없다**(고아 잡). 원인은 "
    "`difficulty.py:22`의 `weights.type_tag[item.type_tag]` KeyError이고 "
    "`assembly.py:238`이 잡을 실패로 적은 뒤 재던져 `_unhandled`까지 간다. "
    "대조군 `type_tags=[\"infer\"]`는 같은 배선에서 202 → succeeded로 정상 통과한다. "
    "🔴 strict=True다 — B 구현이 머지되면 이 xfail이 XPASS로 red가 되고, 그때 마커를 지운다."
)


async def _unused_diagnosis(_: ProblemRequest) -> DiagnosisResult:
    raise AssertionError("teacher_manual 요청은 진단을 호출하지 않아야 한다")


def _body(*, type_tags: tuple[str, ...]) -> dict[str, Any]:
    """⚠ `area_tag="language"` · `passage` 없음은 **의도적이다.**

    🔴 다른 조합이면 `workflow.py:536`의 소스 게이트가 **먼저**
    `ProblemSourceUnsupported`를 내서, 이 테스트가 *예약 태그와 무관한 이유로* 통과한다.
    조용한 통과가 이 저장소의 반복 사고다(#122·#126·#133 · 로그 70) — 그래서 여기에
    적어 둔다. 이 조합만이 `difficulty.py`까지 실제로 도달한다.
    """
    return {
        "target_kind": "student",
        "target_ref": "student-reserved-tag",
        "target_source": "teacher_manual",
        "manual_targets": [_SKILL_NODE_ID],
        "snapshot_hash": "snapshot-reserved-tag",
        "taxonomy_version": "v1",
        "area_tag": "language",
        "type_tags": list(type_tags),
        "item_format": "mcq",
        "count": 1,
    }


def _generated_item_json(type_tag: TypeTag) -> str:
    """⚠ 요청 태그와 **같은** 태그로 생성한다 — `rules.py`의 일치 검사를 통과해야
    `difficulty.py`까지 간다. 어긋나면 규칙 실패로 갈려 도달 체인이 끊긴다."""
    return GeneratedItem(
        area_tag=AreaTag.LANGUAGE,
        type_tag=type_tag,
        item_format=ItemFormat.MCQ,
        skill_node_id=_SKILL_NODE_ID,
        stem="밑줄 친 절이 문장에서 담당하는 기능으로 옳은 것을 고르시오.",
        choices=tuple(
            Choice(
                no=no,
                text=f"문장 구조 선택지 {no}",
                why_wrong=None if no == 1 else f"{no}번은 문법 근거와 다르다.",
            )
            for no in range(1, 6)
        ),
        answer=Answer(correct_no=1),
        rationale="승인된 문법 근거에 따르면 1번이 옳다.",
        evidence=(EvidenceAnchor(kind=EvidenceKind.GRAMMAR_RULE, ref="grammar:rule-1"),),
    ).model_dump_json()


def _solve_result_json() -> str:
    from ai.contracts.problem_generation import SolveResult

    return SolveResult(
        chosen=1,
        reasoning="문법 근거를 독립적으로 확인했다.",
        confidence=0.95,
        target_skill_node_id=_SKILL_NODE_ID,
        measured_skill_node_id=_SKILL_NODE_ID,
        aligned=True,
        alignment_confidence=0.95,
        alignment_reason="목표 문법 노드와 일치한다.",
    ).model_dump_json()


def _prepare(item_type: TypeTag) -> None:
    reset_shared_agent_runtime()
    problem_router.reset_problem_router()
    problem_router.set_problem_providers(
        ProblemProviders(
            generator=FakeProvider(
                (_generated_item_json(item_type),), name="reserved-tag-generator"
            ),
            verifier=FakeProvider(
                (_solve_result_json(),), name="reserved-tag-verifier"
            ),
            has_dedicated_verifier=False,
        )
    )
    problem_router.set_problem_services(
        graph_context=FakeGraphContextService(), diagnosis=_unused_diagnosis
    )
    problem_router.set_problem_stores(problem_runtime_stores())
    problem_router.set_problem_run_store(InMemoryRunStore())


def _post(type_tag: str, item_type: TypeTag) -> httpx.Response:
    _prepare(item_type)
    # ⚠ `raise_server_exceptions=False` — 서버 예외를 다시 던지지 않고 **실제 응답**을
    #   보게 한다. BE가 받는 것이 무엇인지가 이 테스트의 주장이다.
    with TestClient(create_app(), raise_server_exceptions=False) as client:
        response: httpx.Response = client.post(
            "/v1/problems",
            headers={**_HEADERS, "Idempotency-Key": f"idem-{type_tag}"},
            json=_body(type_tags=(type_tag,)),
        )
    return response


def test_the_reservation_premise_holds() -> None:
    """🔴 **검사 경로가 끊기면 통과가 아니라 실패다.**

    예약 태그가 사라지거나 v1으로 승격되면 이 파일의 전제가 없어진다 — 그때는 *"구멍이
    닫혔다"* 가 아니라 **이 파일을 다시 판단해야 한다**는 뜻이다.
    """
    assert RESERVED_TYPE_TAGS, "예약 태그가 없다 — 이 파일의 전제가 사라졌다"
    assert TypeTag.APPLY in RESERVED_TYPE_TAGS
    assert TypeTag.APPLY not in V1_TYPE_TAGS


def test_a_v1_type_tag_is_accepted_end_to_end() -> None:
    """⚠ **대조군** — v1 지원 태그는 같은 배선에서 정상 경로로 간다.

    🔴 이게 없으면 *"400이 안 나는 게 요청이 잘못돼서"* 인지 구분이 안 된다. 요청 형태
    (`area_tag`·`manual_targets`·헤더·대역 배선)가 유효하다는 것을 여기서 못 박는다.
    """
    response = _post("infer", TypeTag.INFER)
    assert response.status_code == 202, (
        f"대조군이 202가 아니다({response.status_code}) — 요청 형태나 대역 배선이 깨졌다. "
        "본건 테스트의 결과를 믿을 수 없다"
    )
    assert response.json()["data"]["job_id"]


@pytest.mark.xfail(strict=True, reason=_PENDING_B_FIX_REASON)
def test_a_reserved_type_tag_is_rejected_at_the_request_door() -> None:
    """🔴 예약 태그가 든 출제 요청은 **400**이어야 한다.

    🔴 **거절은 잡을 만들기 전에 나야 한다.** 주체 3분할상 이건 **호출자가 고칠 요청**이라
    4xx이고, 잡을 만들면 **실패 원장만 늘어난다.** 지금은 실제로 그렇게 되고 있다 —
    잡이 `failed`로 남는데 `job_id`가 응답에 없어 **BE는 그 잡의 존재조차 모른다.**

    ⚠ **상태 코드만 단정한다.** 사유 코드 `type_tag_not_supported`는 아직 **`[제안]`**
    (`error_codes` §6)이고 B가 다른 코드를 고를 수 있다 — 계약이 코드보다 앞서 나가면
    B의 선택을 이 테스트가 막는다. 사유 코드까지 잠그는 것은 그 코드가 확정된 뒤다.
    """
    response = _post("apply", TypeTag.APPLY)
    assert response.status_code == 400, (
        f"예약 태그가 출제 요청 문을 통과한다 — HTTP {response.status_code}가 나왔다.\n"
        f"  body = {response.text[:400]}\n"
        "🔴 400이어야 한다(주체 3분할 — 호출자가 고칠 요청이다). 고치는 위치는 라우터 "
        "요청 경계(`routers/problem.py::_problem_request`)이고, 지금은 "
        "`ProblemRequest.model_validate()`의 enum 검증뿐이라 예약 태그가 그대로 통과한다. "
        "사유 코드는 `type_tag_not_supported` [제안 · error_codes §6]."
    )
