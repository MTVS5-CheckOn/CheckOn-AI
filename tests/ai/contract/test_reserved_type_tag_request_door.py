"""🔴 예약 태그(`apply`)가 **출제 요청 문**에서 거절된다 (99 ㊨ · #01).

#134가 `TypeTag.APPLY`를 계약에 넣으면서 **다섯 문 중 하나가 안 닫혔다.** 04·`error_codes`·
`policies/taxonomy.md`·`contracts/taxonomy.py`가 전부 *"출제 요청에 보내면 400
`type_tag_not_supported`"* 라고 적었는데 **400이 안 났다.** A가 이 파일을 `xfail(strict=True)`로
세워 두었고(#136), B 구현으로 마커를 지웠다.

## 그때 무엇이 났는가 (8/9 실측 — 이 파일이 세워진 이유)

    ProblemRequest.model_validate   통과 (enum이 유효하다 · 어디에도 v1 allowlist가 없다)
    workflow.py 소스 게이트          통과 (area_tag·passage만 본다)
    generator                        `"type_tag": "apply"` 로 생성
    rules.py 요청 일치 검사          통과 (요청도 apply다)
    difficulty.py:22                 weights.type_tag[item.type_tag] → 🔴 KeyError
    assembly.py  except Exception   → 잡을 failed로 적고 **재던진다**
    api/app.py   _unhandled         → **HTTP 500** · 잡은 남는데 job_id가 응답에 없다(고아 잡)

## 지금 무엇이 나는가

    enqueue.py::reject_unsupported_type_tags → 400 INVALID_SCHEMA
      detail.reason = "type_tag_not_supported" · 요청 태그와 v1 지원 목록 동봉
    🔴 **잡도 요청 레코드도 만들어지지 않는다** — 거절이 `put()`보다 앞이다.

🔴 **거절 자리가 「잡을 만들기 전」인 것이 이 파일의 두 번째 주장이다.** 주체 3분할상
호출자가 고칠 요청이라 4xx이고, 잡을 만들면 **실패 원장만 늘어난다**. 자료 조달 미구현도
같은 문 앞으로 이동해 99 #01의 남은 자리가 해소됐다(2026-08-09).
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Final

import httpx
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
from ai.problem_generation.enqueue import ProblemRequestStore
from ai.problem_generation.infrastructure.config import load_verify_config
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


async def _unused_diagnosis(_: ProblemRequest) -> DiagnosisResult:
    raise AssertionError("teacher_manual 요청은 진단을 호출하지 않아야 한다")


def _body(*, type_tags: tuple[str, ...]) -> dict[str, Any]:
    """⚠ `area_tag="language"` · `passage` 없음은 **의도적이다.**

    이 조합은 자료 조달 문을 통과하므로 예약 태그 거절만 격리해 본다. 둘 다 위반할 때도
    관측 호환성을 위해 예약 태그가 먼저지만, 그 순서만으로 소스 가드의 정상 배선을 증명할
    수는 없다. 조용한 통과가 이 저장소의 반복 사고라 전제까지 고정한다.
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
                misconception_tag=None if no == 1 else "application_target_substitution",
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


class _RecordingRequestStore:
    """`put()` 호출을 세는 대역 — *"잡을 만들기 전에 끊겼는가"* 의 관측 지점.

    ⚠ 잡보다 **요청 저장이 먼저**다(`enqueue.py`) — `put`이 0회면 그 뒤의 `WorkerJob`도
    만들어지지 않았다. 저장소 내부 키 형식에 기대지 않으려고 스파이로 본다.
    """

    def __init__(self, inner: ProblemRequestStore) -> None:
        self._inner = inner
        self.puts: list[ProblemRequest] = []

    async def put(self, request: ProblemRequest) -> str:
        self.puts.append(request)
        return await self._inner.put(request)

    async def get(
        self, request_ref: str, *, tenant_id: str
    ) -> ProblemRequest | None:
        return await self._inner.get(request_ref, tenant_id=tenant_id)


def _prepare(item_type: TypeTag) -> _RecordingRequestStore:
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
    requests = _RecordingRequestStore(problem_runtime_stores().requests)
    problem_router.set_problem_stores(problem_runtime_stores(request_store=requests))
    problem_router.set_problem_run_store(InMemoryRunStore())
    return requests


def _post(
    type_tag: str, item_type: TypeTag
) -> tuple[httpx.Response, _RecordingRequestStore]:
    requests = _prepare(item_type)
    # ⚠ `raise_server_exceptions=False` — 서버 예외를 다시 던지지 않고 **실제 응답**을
    #   보게 한다. BE가 받는 것이 무엇인지가 이 테스트의 주장이다.
    with TestClient(create_app(), raise_server_exceptions=False) as client:
        response: httpx.Response = client.post(
            "/v1/problems",
            headers={**_HEADERS, "Idempotency-Key": f"idem-{type_tag}"},
            json=_body(type_tags=(type_tag,)),
        )
    return response, requests


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
    response, requests = _post("infer", TypeTag.INFER)
    assert response.status_code == 202, (
        f"대조군이 202가 아니다({response.status_code}) — 요청 형태나 대역 배선이 깨졌다. "
        "본건 테스트의 결과를 믿을 수 없다"
    )
    assert response.json()["data"]["job_id"]
    assert len(requests.puts) == 1, (
        "대조군에서 요청 저장이 일어나지 않았다 — 스파이가 배선되지 않았다면 아래 "
        "「거절은 잡보다 앞이다」 단정이 **아무것도 안 보고 초록**이 된다"
    )


def test_a_reserved_type_tag_is_rejected_at_the_request_door() -> None:
    """🔴 예약 태그가 든 출제 요청은 **400**이다.

    주체 3분할상 이건 **호출자가 고칠 요청**이라 4xx다. `detail`에 요청 태그와 v1 지원
    목록을 실어 BE가 **무엇으로 바꿀지** 알게 한다(`error_codes` §6).
    """
    response, _requests = _post("apply", TypeTag.APPLY)
    assert response.status_code == 400, (
        f"예약 태그가 출제 요청 문을 통과한다 — HTTP {response.status_code}가 나왔다.\n"
        f"  body = {response.text[:400]}\n"
        "🔴 400이어야 한다(주체 3분할 — 호출자가 고칠 요청이다). 거절 자리는 "
        "`problem_generation/enqueue.py::reject_unsupported_type_tags`다."
    )
    body = response.json()
    assert body["data"] is None
    assert body["error"]["code"] == "INVALID_SCHEMA"
    assert body["error"]["detail"] == {
        "reason": "type_tag_not_supported",
        "type_tags": ["apply"],
        "supported": sorted(tag.value for tag in V1_TYPE_TAGS),
    }, (
        "사유 코드·detail이 계약과 다르다 — 04 §3.11과 `error_codes` §6이 이 형태를 "
        "적고 있다. 바꾸려면 문서를 같이 바꿔라"
    )


def test_the_rejection_lands_before_the_job_exists() -> None:
    """🔴 **거절은 잡을 만들기 전에 난다** — 4xx가 실패 원장을 남기지 않는다(99 #01).

    ⚠ 이게 이 수정의 절반이다. 워크플로에서 거절했다면 400은 나지만 **잡이 만들어졌다가
    실패로 수렴**하고, 원장을 읽는 사람이 *"이 테넌트에 실패가 많다"* 로 오독한다.
    `enqueue()`의 요청 저장(`put`)이 잡 생성보다 앞이므로 `put` 0회가 그 증거다.
    """
    _response, requests = _post("apply", TypeTag.APPLY)
    assert requests.puts == [], (
        "예약 태그 요청이 저장까지 갔다 — 거절이 `enqueue()` 최상단보다 뒤에 있다. "
        "그러면 잡도 만들어지고 실패 잡이 원장에 남는다"
    )


def test_the_door_and_the_difficulty_weights_agree() -> None:
    """🔴 **문이 허용하는 집합 == 가중치가 존재하는 집합.**

    이 둘이 갈리는 순간이 `difficulty.py`의 KeyError가 돌아오는 순간이다(#134 실측).
    한쪽만 넓히는 PR을 여기서 잡는다 — 예약을 여는 날에도 `V1_TYPE_TAGS`가 넓어지면
    `verify_config.yaml`이 red를 내서 가중치를 같이 넣게 된다.
    """
    weights = load_verify_config().difficulty_weights
    assert set(weights.type_tag) == set(V1_TYPE_TAGS), (
        "요청 문이 받는 태그와 난이도 가중치 키가 다르다 — 받은 뒤 조회에서 터진다"
    )
