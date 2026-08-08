"""counsel 초안 라우터 — `/v1/counsel/drafts` (인박스 계약 v1 §4 · 04 §3.9).

소유: 박진희 (composition 라우터). 계약(`contracts/counsel`)과 기존 counsel_pack 워커를
HTTP로 노출한다. **요청 단위 = 문의 1건**이며 워커는 새로 만들지 않는다 — 학생 묶음 워커를
**N=1 축퇴 사례로 재사용**한다(99 D ㉛). 저장(`pack://`·`draft://`)·재개·수렴·서킷 경로는
그대로다.

**동기 실행(선례: `imports` 라우터).** enqueue → 같은 요청 안에서 러너를 1회 돌려 결과를
확정하고 202를 낸다. 실 비동기 워커 루프·Kafka 완료 통지는 후속이며, 그때 이 라우터는
enqueue까지만 하고 GET이 잡 상태를 읽는 형태로 좁아진다(계약은 그대로다 — BE는 이미
202 → Kafka → GET 순서로 쓴다).

🔴 **provider에 기본값이 없다** — 조립 루트가 `set_counsel_provider()`를 부르지 않으면
**기동이 실패한다**. 종전 기본값은 `FakeCounselProvider()`였고, 그건 배선 실수를 조용한
날조 산출로 바꿨다(Fake의 `fallback_text`는 게이트를 통과하고 원장에도 안 남는다).
CI·테스트는 Fake를 **명시적으로** 꽂고(`reset_counsel_stores`), 실 경로는
`GatewayPlanner`+`GatewayDraftWriter`를 만족하는 한 객체를 꽂는다.

**러너는 `assembly.open_counsel_pack_runner`로만 만든다** — 직접 생성하면
`require_tracing_disabled`(불변식 3)와 체크포인터 선택이 서비스 경로에서만 빠진다.

멱등: 감지·Import 라우터 선례 재사용 — `(tenant_id, endpoint, idempotency_key)` 스코프,
바디 동일성은 canonical 해시. 캐시 fail-open.
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from collections import OrderedDict
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Final

from fastapi import APIRouter, Request, Response
from pydantic import ValidationError

from ai.agents.supervisor import Supervisor, system_utc_now
from ai.api.envelope import success_envelope
from ai.api.version_scope import RouterScope
from ai.composition.counsel.assembly import (
    build_counsel_llm_provider,
    build_counsel_provider,
    open_counsel_pack_runner,
    reset_default_memory_checkpointer,
)
from ai.composition.counsel.enqueue import CounselPackEnqueuer
from ai.composition.counsel.labels import LabelVocabularyError, snapshot_from_labels
from ai.composition.counsel.prompt import PROMPT_VERSION
from ai.composition.counsel.provider import (
    COUNSEL_GEN_PARAMS,
    CounselPlanner,
    DraftWriter,
    FakeCounselProvider,
)
from ai.composition.counsel.refine import refine_draft
from ai.composition.counsel.settings import get_counsel_settings
from ai.composition.counsel.stores import (
    DRAFT_SCHEME,
    AgentStepSink,
    ContextStore,
    DraftResultStore,
    InMemoryAgentStepSink,
    InMemoryContextStore,
    InMemoryDraftResultStore,
    InMemoryPackResultStore,
    PackResultStore,
    make_ref,
)
from ai.composition.counsel.worker import CounselPackRunner
from ai.contracts.agents import JobPhase, WorkerJob
from ai.contracts.composition import DraftContext, EvidenceFact
from ai.contracts.counsel import (
    Citation,
    CounselDraftJobView,
    CounselDraftRequest,
    CounselDraftResult,
    InquiryTopic,
    RefineRequest,
    RefineResponse,
    WireDraftStatus,
    wire_status_for,
)
from ai.contracts.execution import Capability, ExecutionContext, VersionSet
from ai.contracts.gates import BlockedReason
from ai.contracts.llm import LlmError
from ai.db.repositories.idempotency import IdempotencyStore
from ai.db.repositories.run_store import (
    RunStore,
    default_llm_call_collector,
)
from ai.db.store_factory import (
    build_agent_job_store,
    build_idempotency_store,
    build_run_store,
)
from ai.runtime.errors import (
    IdempotencyConflict,
    NotFound,
    SnapshotInvalid,
    domain_error_for,
)

logger = logging.getLogger(__name__)

router = APIRouter()

_PIPELINE_VERSION = "0.1.0"
_ENGINE_VERSION = "counsel-pack-0.1"
_SCHEMA_VERSION = "0.1"
_CONTRACT_VERSION = "0.1"

_REQUIRED_HEADERS = ("X-Tenant-Id", "X-Request-Id", "Idempotency-Key")
_POST_ENDPOINT = "POST /v1/counsel/drafts"

#: 학습 데이터가 필요 없는 문의 유형 — `template_only` 경로(error_codes §2.1 · 03 §C 상황 2).
#: ⚠ **`schedule` 한 종만이다.** `etc`를 넣지 않는 이유는 **오분류의 비대칭**이다 —
#: 데이터 유관 문의를 무관으로 잘못 보면 근거가 있는데도 일반 안내만 나가 **기능이 사라지고**,
#: 반대로 무관 문의를 유관으로 보면 근거 부족 → `rejected_insufficient`(정직한 거부)로
#: **안전하게 수렴**한다. 넓히려면 이 상수만 고치면 된다.
_NO_DATA_TOPICS: Final[frozenset[InquiryTopic]] = frozenset({InquiryTopic.SCHEDULE})

#: 게이트 실패 재생성 상한 — ERD DRAFT_BLOCK "≤3"(불변식 6).
_REGEN_MAX = 3
_LEASE_OWNER = "counsel-router"

#: `result`를 실을 수 있는 phase — **결과 계약이 확정된 상태**만이다(error_codes §2.5).
#: 나머지(queued·leased·running·paused·cancelled)는 결과가 **아직/영영 없는** 것이고,
#: 🔴 그걸 `llm_failed`로 보고하면 `status="queued"` + `draft_status="llm_failed"`라는
#: **모순 조합**이 나간다 — 잡은 살아 있는데 BE는 "다시 시도"를 그리고, 강사가 누르면
#: 초안이 2개 생긴다. 계약의 `result`가 옵셔널인 이유가 이것이다.
_REPORTABLE_PHASES: Final[frozenset[JobPhase]] = frozenset(
    {JobPhase.SUCCEEDED, JobPhase.FAILED}
)


def _can_report_result(phase: JobPhase) -> bool:
    """`result`를 실을 수 있는 phase인가 — **POST·GET이 같은 함수를 쓴다**.

    🔴 이 판정을 두 곳에 복제하면 한쪽만 고쳐진다. 이 시리즈가 다섯 번 겪은 형태다
    (#108·#111·#114·#116·#119) — 그래서 상수 참조가 아니라 **함수 하나**로 못 박는다.
    """
    return phase in _REPORTABLE_PHASES

#: 시계 주입점 — `datetime.now()` 직접 호출 금지(03 §3).
_clock = system_utc_now

_idempotency_store: IdempotencyStore = build_idempotency_store()
#: 실행 원장(AI_RUN·LLM_CALL) — refine 턴이 직접 쓴다. POST 경로는 워커가 쓴다
#: (LLM 호출이 `job.execution_id` 아래에서 일어나므로 그 실행의 주인이 워커다).
_run_store: RunStore = build_run_store()
_context_store: ContextStore = InMemoryContextStore()
_draft_store: DraftResultStore = InMemoryDraftResultStore()
_pack_store: PackResultStore = InMemoryPackResultStore()
_step_sink: AgentStepSink = InMemoryAgentStepSink()

#: 인메모리 캐시 1개의 항목 상한 — `LlmCallCollector.MAX_PENDING_RUNS`와 **같은 계열**로
#: 둔다(불변식 6 "모든 루프에 상한"). ⚠ **추측값이다** — 실사용 부하 데이터가 없다. 근거는
#: "같은 프로세스가 동시에 들고 있을 법한 잡 수"이며 그쪽 상수의 판단을 그대로 따랐다.
#: 실측이 생기면 바꾼다.
_MAX_CACHED_JOBS: Final = 256


class _JobCache[V]:
    """`(tenant_id, job_id) → V` 인메모리 캐시 — **LRU 상한 + 밀려난 수 카운터**.

    🔴 **#121이 연 표면이다.** 전에는 잡 원장이 요청마다 새로 만들어져 짝이 되는 캐시
    항목도 사실상 죽은 값이었다. 이제 잡이 **프로세스 수명 내내 살아** 캐시도 계속 유효한
    참조가 되므로, 무한히 쌓이는 것이 처음으로 실제 문제가 됐다.

    선례를 그대로 복사했다 — `LlmCallCollector`가 `MAX_PENDING_RUNS` LRU + `evicted_runs`
    카운터 + 경고 로그를 이미 갖고 있다(`run_store.py`). 설계 결정을 새로 하지 않는다.

    ⚠ **밀려난 항목은 404가 된다.** 캐시라서 허용하는 것이다 — 잡 **원장**에는 같은
    처방을 쓰면 안 된다(밀려난 잡 = 없어진 잡 · 99 ㊐).
    ⚠ 카운터를 읽는 자리를 같이 만든다 — 로그 59의 교훈("관측 장치를 만들 때 읽는 자리를
    같이 만들지 않으면 없는 것과 같다"). `evicted`는 경고 로그로 나간다.
    """

    def __init__(self, label: str, *, max_items: int = _MAX_CACHED_JOBS) -> None:
        self._label = label
        self._max_items = max_items
        self._rows: OrderedDict[tuple[str, str], V] = OrderedDict()
        self.evicted = 0
        """상한 초과로 밀려난 항목 수 — 0이 아니면 캐시가 부하를 못 담고 있다."""

    def get(self, key: tuple[str, str]) -> V | None:
        row = self._rows.get(key)
        if row is not None:
            self._rows.move_to_end(key)  # LRU — 읽힌 항목을 뒤로
        return row

    def put(self, key: tuple[str, str], value: V) -> None:
        self._rows[key] = value
        self._rows.move_to_end(key)
        while len(self._rows) > self._max_items:
            stale_key, _ = self._rows.popitem(last=False)
            self.evicted += 1
            logger.warning(
                "%s 캐시 상한 초과 — 가장 오래된 항목을 버린다 tenant=%s job_id=%s "
                "limit=%d evicted=%d. 이후 그 job_id의 GET·refine은 404다.",
                self._label,
                stale_key[0],
                stale_key[1],
                self._max_items,
                self.evicted,
            )

    def clear(self) -> None:
        self._rows.clear()
        self.evicted = 0

    def __len__(self) -> int:
        return len(self._rows)


#: 와이어 읽기 모델 — `(tenant_id, job_id) → 계약 뷰`. 🔴 **캐시다, 정본이 아니다.**
#:
#: 정본은 **잡 원장**(`WorkerJob`)이고 이건 그 투영을 담아 두는 자리다. 종전에는 쓰기가
#: POST 1곳뿐이라 **갱신 경로가 0개**였고, 그래서 잡이 나중에 끝나도 GET은 영원히 POST
#: 당시의 phase를 돌려줬다(㉩) — BE는 스피너를 계속 그리고 강사는 다시 눌러 초안을 2개
#: 만든다. 지금은 GET이 원장에서 현재 phase를 **다시 읽어** 이 캐시를 갱신한다.
#:
#: ⚠ **영속은 이 PR이 아니다**(`_DraftState` docstring과 같은 판정) — 프로세스 공용
#: 캐시까지가 v1이고, PG 이관 시 자리를 넘긴다.
@dataclass(frozen=True)
class _CachedView:
    """GET이 돌려줄 뷰 + **그 잡의 실행 원장 키**(99 ㊮).

    🔴 `execution_id`를 뷰와 **함께** 캐시한다 — GET이 그 자리에서 `uuid.uuid4()`를
    만들면 같은 잡을 두 번 GET할 때 값이 달라지고, 셋 중 어느 것도 `AI_RUN`의 실행이
    아니다(실측 8/7: POST·GET1·GET2·AI_RUN이 **4종**). `meta.execution_id`는
    **그 응답이 말하는 실행의 원장 키**이지 응답마다 만드는 값이 아니다.

    ⚠ **`None`이 정직한 경우가 있다** — `template_only`·근거 0건은 워커도 LLM도 안 타서
    **원장에 행이 없다**. 그때는 없는 실행을 가리키는 값을 지어내는 대신 POST 시점에
    만든 값 하나를 **재사용**한다(아래 `_envelope_execution_id`) — 최소한 **두 번 GET이
    같아진다**. ⚠ 그 값은 상관 ID이지 원장 키가 아니고, 그 사실을 여기 적어 둔다.
    ⚠ 선례는 `routers/problem.py::_CachedView`(view + versions)다.
    """

    view: CounselDraftJobView
    execution_id: uuid.UUID | None
    #: 원장 키가 없을 때 응답들을 묶는 상관 ID — POST가 한 번 만들고 GET이 재사용한다.
    correlation_id: uuid.UUID

    def envelope_execution_id(self) -> str:
        """`meta.execution_id`에 실을 값 — **원장 키가 있으면 그것**, 없으면 상관 ID.

        ⚠ `success_envelope(execution_id: str)`는 `str`이라 `None`을 못 싣는다
        (`api/envelope.py`는 **양자**라 이 PR이 안 건드린다). `error_envelope`는 계약상
        *"실행 전 오류라 execution_id가 없으면 null"* 을 이미 허용하는데 성공 응답에는
        그 자리가 없다 — **그 비대칭은 99 ㊮에 등재만** 하고 여기서는 안정성(두 번 GET이
        같다)을 먼저 확보한다.
        """
        return str(self.execution_id or self.correlation_id)


_view_cache: _JobCache[_CachedView] = _JobCache("counsel_view")


@dataclass
class _DraftState:
    """refine 대상 초안의 현재 상태 — `(tenant_id, job_id)`로 찾는다.

    `context`는 게이트 재통과에 필요하고(허용 숫자·금칙·길이 상한이 전부 여기서 나온다),
    `citations`는 반영 턴 응답에 다시 실린다. **영속은 후속**이다 — v1은 `_view_cache`와 같은
    인메모리 읽기 모델이며 PG 이관 시 DRAFT_REVISION(ERD)이 자리를 받는다(06 §7).
    """

    context: DraftContext
    citations: tuple[Citation, ...]
    text: str

    #: 🔴 최초 요청의 입력 스냅숏 해시(99 ㉭). refine 원장(`AI_RUN.input_snapshot_hash`)이
    #: 이 값을 쓴다 — 종전에는 `"sha256:refine"` 리터럴이라 **아무 입력도 특정하지 못했다.**
    #: 출처는 여기뿐이다 — `RefineRequest`는 `instruction`·`turn_no` 둘뿐이고
    #: `DraftContext`에도 `snapshot_hash`가 없다.
    #:
    #: 🔴 **기본값이 없다.** 종전 `= ""`는 **계약을 위반하는 값**이었다 —
    #: `contracts/execution.py`의 `input_snapshot_hash`는 `Field(min_length=1)`이라
    #: 빈 문자열이 무효다. 그리고 `_refine_execution_context(...)` 호출이 **`try` 블록
    #: 밖**에 있어(`failed = True` 앞) `ValidationError`가 `_record_refine_run`도 안 지나고
    #: `_unhandled`로 간다 — **500 + 원장 0건**이다(㊝과 같은 형태:
    #: `Brief(text="")` → `min_length=1` → 핸들러 밖).
    #: ⚠ 기본값을 없애면 빠뜨렸을 때 **생성 시점 `TypeError`** 로 즉시 죽는다 — 조용하지 않다.
    #: ⚠ 지금은 도달 불가다(생성 지점이 `:700` 한 곳이고 항상 채운다) — **잠재 결함**을
    #:   막는 것이고, 규칙은 *"기본값이 계약을 위반하는 값이면 빠뜨림이 조용한 게 아니라
    #:   500이 된다"* 이다.
    snapshot_hash: str

    #: 🔴 최초 생성이 고른 **검증 통과 강조점**(99 ㉮). 없으면 refine이 매 턴 강조점 없이
    #: 다시 써서 *"1턴에 강조한 것이 2턴에 사라지는"* 상태가 된다 — 강사가 다듬기를 한 번만
    #: 눌러도 **매번** 그렇다.
    #: ⚠ **빈 값이 「강조점 없음」인지 「안 쟀음」인지는 여기서 안 갈린다** — 사유는
    #: `CounselPackResultRecord.plan_outcome`이 든다(㉲). 이 필드만 보고 판단하지 마라.
    #: ⚠ **기본값 `()`는 그대로 둔다** — 위 `snapshot_hash`와 성격이 다르다. `()`는
    #:   **계약상 유효한 값**(강조점 없음)이고 `""`는 무효였다. 「유효한 기본값」과
    #:   「계약을 위반하는 기본값」을 같이 취급하지 않는다.
    emphasis: tuple[str, ...] = ()


#: refine 읽기 모델 — `(tenant_id, job_id) → 초안 상태`. 키가 job_id인 이유는
#: 문의 1건 = 잡 1개 = 초안 1개(pack N=1 · 99 D ㉛)라 별도 draft_id를 노출할 필요가
#: 없고, BE가 Kafka 완료 통지로 이미 받은 값을 그대로 쓸 수 있어서다(04 §3.9).
_drafts: _JobCache[_DraftState] = _JobCache("counsel_draft")

#: LLM 접점 — 🔴 **기본값이 없다.** 조립부가 주입하지 않으면 서비스는 뜨지 않는다.
#:
#: ⚠ 종전 기본값은 `FakeCounselProvider()`였다. Fake는 시나리오가 없으면
#: `context.fallback_text`("이번 기간 학습 상황을 정리해 보내드립니다.")를 돌려주는데, 그
#: 문장은 숫자·금칙어가 없어 **게이트를 그대로 통과**한다 — `draft_status=generated` +
#: `citations`가 붙은 정상 초안으로 학부모에게 나가고, LLM 호출이 0건이라
#: **AI_RUN·LLM_CALL에도 남지 않아 사후 추적으로도 구분되지 않는다.**
#: `assembly.open_counsel_pack_runner`가 같은 이유로 이미 폴백을 제거했고
#: ("조용한 Fake가 최악"), 이 라우터에만 남아 있었다.
_provider: Any = None


class CounselProviderNotWired(RuntimeError):
    """counsel provider 미배선·부분 배선 — **기동 시점**에 터뜨린다.

    첫 요청까지 미루면 Fake 산출이 나가거나(구 기본값) `worker_internal_error`가 되는데,
    둘 다 배포 후에야 드러난다.
    """


#: provider가 만족해야 할 Protocol — 워커가 **plan·write 둘 다** 호출한다.
#: ⚠ `runtime_checkable`은 **메서드 존재만** 본다(시그니처는 안 본다). 그래도
#: "planner 없는 writer만 꽂혔다"는 이 PR이 잡으려는 실수는 전부 걸린다.
_PROVIDER_PROTOCOLS: Final = (("plan", CounselPlanner), ("write", DraftWriter))


def set_counsel_provider(provider: Any) -> None:  # noqa: ANN401 — Planner+Writer 이중 Protocol
    """plan·write provider 주입 — 테스트 시나리오·실 LLM 배선의 seam.

    🔴 한 객체가 `CounselPlanner`·`DraftWriter` **양쪽**을 만족해야 한다. 종전에는 검사가
    없어 `write`만 있는 객체를 꽂아도 기동이 통과하고 **첫 요청에서** `worker_internal_error`가
    났다 — 배선 실수는 배선 시점에 터지는 게 맞다.
    """
    missing = [
        name for name, protocol in _PROVIDER_PROTOCOLS
        if not isinstance(provider, protocol)
    ]
    if missing:
        raise CounselProviderNotWired(
            f"counsel provider가 {', '.join(missing)}을(를) 구현하지 않는다 "
            f"({type(provider).__name__}) — 워커가 plan·write 둘 다 호출한다"
        )
    global _provider
    _provider = provider


def require_counsel_provider() -> Any:  # noqa: ANN401 — Planner+Writer 이중 Protocol
    """배선 확인 — 미배선이면 **기동을 막는다**(아래 startup 훅이 부른다).

    ⚠ `api/app.py`는 양자 승인 파일이라 라우터가 자기 startup 훅을 들고 간다
    (`include_router`가 앱으로 옮겨 준다). 앱 조립부를 고치지 않고도 같은 시점에 터진다.
    """
    if _provider is None:
        raise CounselProviderNotWired(
            "counsel provider가 배선되지 않았다 — `set_counsel_provider()`를 부르지 않으면 "
            "초안 경로를 띄우지 않는다. 조용한 Fake 폴백은 제거됐다(날조 산출 저장 방지). "
            "테스트·CI는 `reset_counsel_stores()`가 Fake를 명시적으로 꽂는다."
        )
    return _provider


def bootstrap_counsel_provider() -> None:
    """조립 루트 — 기동 시 env를 보고 provider를 만들어 꽂는다(`LLM_PROVIDER`).

    🔴 **이미 배선돼 있으면 덮지 않는다.** 테스트·평가 러너는 자기 시나리오 provider를
    명시 주입하는데, 조립 루트가 그걸 갈아치우면 주입 seam이 무의미해진다.

    ⚠ fake가 선택되는 것은 **사고가 아니라 선택**이다 — `build_counsel_llm_provider`가
    경고 로그를 남기고 산출물에도 `fake-counsel`이 적힌다. 반면 이 함수가 아예 안 돌면
    아래 `require_counsel_provider`가 기동을 막는다. 둘은 다른 사건이다.
    """
    if _provider is not None:
        return
    set_counsel_provider(build_counsel_provider(build_counsel_llm_provider()))


def _startup() -> None:
    """기동 순서 = **조립 → 확인**. 확인이 뒤라 조립 루트가 없거나 실패하면 걸린다.

    ⚠ 조립 루트를 **전역 이름으로** 부른다 — 테스트가 `bootstrap_counsel_provider`를
    비워 "조립 루트 부재"를 재현할 수 있어야 하기 때문이다(핸들러가 함수 객체를 잡아
    두면 monkeypatch가 안 먹는다).
    """
    bootstrap_counsel_provider()
    require_counsel_provider()


router.add_event_handler("startup", _startup)


def set_counsel_stores(
    *,
    idempotency_store: IdempotencyStore | None = None,
    context_store: ContextStore | None = None,
    draft_store: DraftResultStore | None = None,
    pack_store: PackResultStore | None = None,
    step_sink: AgentStepSink | None = None,
) -> None:
    """저장소 주입 — 합성 루트·테스트에서 특정 인스턴스를 꽂는다."""
    global _idempotency_store, _context_store, _draft_store, _pack_store, _step_sink
    if idempotency_store is not None:
        _idempotency_store = idempotency_store
    if context_store is not None:
        _context_store = context_store
    if draft_store is not None:
        _draft_store = draft_store
    if pack_store is not None:
        _pack_store = pack_store
    if step_sink is not None:
        _step_sink = step_sink


def set_counsel_run_store(store: RunStore) -> None:
    """실행 원장 주입 — 테스트가 AI_RUN·LLM_CALL 적재를 관측하는 seam."""
    global _run_store
    _run_store = store


def reset_counsel_stores() -> None:
    """테스트 격리용 — 저장소·읽기 모델·provider를 기본값으로 되돌린다.

    🔴 **체크포인터도 함께 버린다**(8/7 · 99 ㉦) — 프로세스 공용 싱글턴이라 비우지 않으면
    같은 `thread_id`의 죽은 체크포인트가 다음 테스트의 재개로 되살아난다.

    🔴 **잡 원장은 여기서 안 지운다(8/7 · 99 ㊒ 해소).** 그건 **A·B 공용**이라
    (B가 pg 워커에서 같은 팩토리를 쓴다) counsel 이름을 단 함수가 지우면 counsel 밖을
    지우는 것이 된다. 잡을 적재하는 테스트는 `reset_shared_agent_runtime()`을 **명시적으로**
    부른다 — 이름이 범위를 말하게 하는 것이 요점이다.
    ⚠ **둘을 함께 불러야 하는 자리가 있다** — 잡과 체크포인트는 `thread_id`(=`job_id`)로
    엮여 있어 한쪽만 지우면 짝 없는 것이 남는다. 그 자리에서는 두 함수를 나란히 부른다.
    """
    global _idempotency_store, _context_store, _draft_store, _pack_store, _step_sink
    global _run_store
    reset_default_memory_checkpointer()
    _run_store = build_run_store()
    default_llm_call_collector().reset()
    _idempotency_store = build_idempotency_store()
    _context_store = InMemoryContextStore()
    _draft_store = InMemoryDraftResultStore()
    _pack_store = InMemoryPackResultStore()
    _step_sink = InMemoryAgentStepSink()
    _view_cache.clear()
    _drafts.clear()
    set_counsel_provider(FakeCounselProvider())


def counsel_versions() -> VersionSet:
    """이 엔드포인트의 버전 세트 — 실패 응답에도 실린다(04 §2.2 A판정).

    🔴 **`prompt_version`을 싣는다(8/7 · 99 ㊔).** 종전에는 넷만 채워 응답의
    `meta.versions.prompt`가 `null`이었는데, **같은 실행의 `AI_RUN`에는 `"0.2"`가 있었다** —
    응답과 원장이 다른 답을 했다. 04 §2.2는 `prompt`를 **프롬프트를 쓰지 않는 capability
    에서만 null**로 규정하고(선언 축) counsel은 프롬프트를 쓴다. `imports`는 이미 싣고
    있어 **counsel만 빠져 있었다.**

    ⚠ **(8/11) 인용문을 04의 현행 문면으로 고쳤다.** 종전에는 *"`prompt`는 **LLM 미사용
    실행**(감지·진단)에서 null"* 로 인용했는데, 04 §2.2가 8/10에 **그 표현을 폐기**했다
    (ⓐ*"LLM을 안 쓰는 capability"* / ⓑ*"호출이 0인 실행"* 둘로 읽혀 오독을 낳았다 ·
    99 ㊧). **인용은 원문이 바뀌면 같이 바뀌어야 한다** — 안 고치면 여기가 폐기된 표현의
    마지막 서식지가 된다(99 #12).

    ⚠ **counsel은 프롬프트가 둘인데 이 필드는 하나다.** `VersionSet.prompt_version`은
    단일 `str | None`이고 `contracts/execution.py`는 양자 승인 파일이라 늘릴 수 없다.

        prompt.PROMPT_VERSION      = "0.2"   counsel_pack(초안)   ← 이 값을 싣는다
        provider.PLAN_PROMPT_VERSION = "0.1"  counsel_plan(강조점)

    **초안 쪽을 고른 근거:** ⓐ `AI_RUN`이 이미 그 값을 쓴다(`worker.py`의
    `_execution_context` — *"prompt_version은 프롬프트 모듈이 소유한다"*). ㊔가 말하는 결함이
    *"응답과 원장이 다른 답을 한다"* 이므로 **원장에 맞추는 것**이 그걸 닫는 최소 변경이다.
    ⓑ 산출물은 초안이다 — plan은 그 입력을 고르는 보조 단계다.

    ⚠ **plan 버전은 유실되지 않는다**(실측 8/7). `LLM_CALL` 행이 호출별
    `prompt_version`을 들어 `['0.1', '0.2']`가 그대로 남는다. 축이 다르다 —
    **`AI_RUN`은 실행의 대표 프롬프트, `LLM_CALL`은 호출별 프롬프트**다.
    """
    return VersionSet(
        pipeline_version=_PIPELINE_VERSION,
        engine_version=_ENGINE_VERSION,
        schema_version=_SCHEMA_VERSION,
        contract_version=_CONTRACT_VERSION,
        prompt_version=PROMPT_VERSION,
    )


def _format_validation_error(exc: ValidationError) -> list[dict[str, str]]:
    """필드 경로만 — 값은 싣지 않는다(04 §2.3 · 개인정보가 detail로 새지 않게)."""
    return [
        {"field": ".".join(str(part) for part in error["loc"]), "type": error["type"]}
        for error in exc.errors()
    ]


def _canonical_hash(body: dict[str, Any]) -> str:
    """바디 동일성 판정용 canonical 해시 — Import 라우터와 같은 방식."""
    canonical = json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _citations_of(request: CounselDraftRequest) -> tuple[Citation, ...]:
    """인용 목록 — **근거 우주 파생**(계약 §4-③ `citations` ≥1).

    `record_id`가 있는 fact 전수다. `DraftContext.cited_record_ids()`(⑱)와 같은 개념의
    요청측 대칭이며 새 파생을 발명하지 않는다. LLM을 쓰지 않는 결정론이다.

    ⚠ **v1은 본문 인라인 앵커(`#L1`)를 지원하지 않는다**(99 D ㊳) — `cite_id`는 목록 안의
    순서 키다. 계약 §4-③ 예시의 `#L1` 문면은 v1.1에서 유효해진다(BE·FE 통보 완료).
    """
    return tuple(
        Citation(cite_id=f"L{index}", record_id=fact.record_id, summary=fact.summary)
        for index, fact in enumerate(request.context.citable_facts(), start=1)
        if fact.record_id
    )


def _draft_context(request: CounselDraftRequest) -> DraftContext:
    """계약 요청 → 워커 입력. 문의 1건이므로 학생 1명짜리 컨텍스트 하나다.

    `guardian_ref`에는 `parent_ref`가 들어간다 — 둘 다 가명이며 이름만 다르다.
    """
    snapshot, _applied = snapshot_from_labels(request.labels)
    facts = tuple(
        EvidenceFact(label="근거", value=fact.summary, record_id=fact.record_id)
        for fact in request.context.facts
    )
    return DraftContext(
        student_ref=request.student_ref,
        guardian_ref=request.parent_ref,
        label_snapshot=snapshot,
        facts=facts,
        evidence_summaries=(),
        period_label=request.context.period_label,
        # ⚠ **정본이 없는 문자열이다**(8/9 전수 — `docs/`에 이 문장이 없다).
        #    counsel은 이 값을 **되돌려 쓰지 않으므로**(소비처가 Fake 하나 · 99 #08)
        #    지금은 계약의 `NonEmptyStr`을 채우는 자리표시자다. 🔴 **실제 폴백 경로가
        #    생기면 여기 하드코딩이 아니라 05 톤 규칙의 정본을 참조해야 한다** —
        #    그때 이 문장이 학부모에게 나가고, 문면 소유는 BE다(`error_codes` §2.1).
        fallback_text="이번 기간 학습 상황을 정리해 보내드립니다.",
        # 🔴 학부모가 실제로 물은 것 — 종전엔 아무도 안 읽어서 같은 학생·같은 라벨이면
        # 어떤 문의든 바이트 동일한 프롬프트가 나왔다. BE 1차 마스킹분이고, 전송 직전
        # `redact()`를 한 번 더 탄다(fail-closed · 불변식 3).
        inquiry_text=request.inquiry.text_masked,
    )


#: 차단 사유별 강사 문구 — 원본은 `part_a/06_refine_policy.md` §4 표다(여기서 새로 만들지
#: 않는다). 🔴 **응답에는 싣지 않는다**(8/5) — BE가 `blocked_reason`으로 이 표를 조회해
#: 문구를 붙인다. 여기 남겨 둔 것은 골든 테스트가 그 매핑의 기대값으로 쓰기 때문이다.
REFINE_BLOCK_MESSAGES: dict[BlockedReason, str] = {
    BlockedReason.EVIDENCE_MISSING: "요청하신 내용은 기록에서 확인되지 않아 반영하지 못했어요",
    BlockedReason.COMPARISON_EXPOSURE: "반 평균·석차는 학부모 문서에 포함할 수 없어요(내부 지표)",
    BlockedReason.TONE_VIOLATION: "해당 표현은 안전 기준에 걸려 완곡한 표현으로 제안했어요",
    BlockedReason.PII_EXPOSURE: "개인정보는 초안에 넣을 수 없어요",
    BlockedReason.OUT_OF_SCOPE: "이 초안의 다듬기와 무관한 요청이에요",
}


def _refine_execution_context(
    execution_id: uuid.UUID, tenant_id: str, input_snapshot_hash: str
) -> ExecutionContext:
    """refine 턴의 실행 컨텍스트 — LLM 호출 기록이 함께 받는다(불변식 8)."""
    return ExecutionContext(
        execution_id=execution_id,
        tenant_id=tenant_id,
        capability=Capability.COMPOSITION,
        input_snapshot_hash=input_snapshot_hash,
        versions=counsel_versions(),
    )


def _build_supervisor() -> Supervisor:
    settings = get_counsel_settings()
    return Supervisor(
        store=build_agent_job_store(),
        lease_duration=timedelta(seconds=settings.counsel_lease_seconds),
        priority_aging_interval=timedelta(
            seconds=settings.counsel_priority_aging_seconds
        ),
        clock=_clock,
    )


async def _generate(
    request: CounselDraftRequest, *, tenant_id: str
) -> tuple[CounselDraftJobView, uuid.UUID | None]:
    """문의 1건 → 초안 1건. 워커는 기존 counsel_pack을 N=1로 재사용한다(99 D ㉛).

    🔴 **잡의 `execution_id`를 함께 돌려준다**(99 ㊮) — 응답의 `meta.execution_id`는
    **그 응답이 말하는 실행의 원장 키**여야 하는데, 종전에는 라우터가 `uuid.uuid4()`를
    그 자리에서 만들어 넣어 **AI_RUN의 어느 행도 가리키지 않았다.** 정본은 이미
    `WorkerJob.execution_id`에 있고 `worker.py`가 그 값으로 AI_RUN을 쓴다.
    ⚠ 선례는 `routers/problem.py::_generate`의 `tuple[ProblemJobView, WorkerJob]`이다 —
      같은 저장소에서 한쪽만 view만 돌려주던 것이 이 갈림의 형태다.

    ⚠ **`None`이 정직한 경우가 있다** — 아래 ①②(`template_only`·근거 0건)는 워커도 LLM도
    안 타므로 **원장에 행 자체가 없다.** 없는 실행을 가리키는 값을 지어내지 않는다.

    🔴 **근거 선검사가 LLM 호출보다 앞이다.** 인용 가능한 근거(`record_id` 있는 fact)가
    0건이면 게이트를 통과한 초안을 만들어 놓고 `citations`가 비어 버리는 낭비가 되므로,
    **호출 전에** `rejected_insufficient`로 끊는다(04 §3.9 규약 · 불변식 2).
    """
    _snapshot, applied = snapshot_from_labels(request.labels)

    # ① 🔴 데이터 무관 문의 — **근거 선검사보다 앞**이다(순서가 계약이다).
    #    시간표 문의 + 신규생(근거 0건)이면 뒤에 두었을 때 "아직 데이터를 모으는
    #    중이에요"가 나가는데, **시간표 답변에 학습 데이터는 애초에 필요 없다.**
    #    근거 유무와 무관하게 template_only여야 한다(03 §C 상황 2).
    #
    #    ⚠ `text`를 비운다 — **안내 문구는 AI가 만들지 않는다.** 근거 3겹:
    #      ⓐ LLM이 문장을 지어내면 **근거 0건 산출물**이라 불변식 2 위반이고, 통과시킬
    #        근거가 없어 게이트를 세울 수 없다
    #      ⓑ `error_codes` §2.1의 **"백엔드 표시 문구"** 열이 원본이라 BE 소유다
    #      ⓒ §2.7 **규칙 ③ "표시 문구는 AI가 주지 않는다"** — 8/5에 `RefineResponse.message`를
    #        제거해 세운 전 엔드포인트 규약이다. 여기 문구를 실으면 그걸 되돌리게 된다
    #
    #    ⚠ **`confidence`를 보지 않는다** — 강등 판단의 주체는 **BE**다(04 §3.5).
    #    요청의 `inquiry`에 `confidence` 필드 자체가 없고, AI는 받은 `topic`을
    #    **확정값으로 신뢰**한다. 오분류였다면 정정 경로(§3.9)가 되돌린다.
    #
    #    LLM 0회 · 잡 적재 없음 · `_drafts` 미등록(초안이 없으니 refine 대상이 아니다).
    if request.inquiry.topic in _NO_DATA_TOPICS:
        view = CounselDraftJobView(
            job_id=str(uuid.uuid4()),
            status=JobPhase.SUCCEEDED.value,
            result=CounselDraftResult(
                draft_status=WireDraftStatus.TEMPLATE_ONLY,
                status_reason="no_data_topic",  # error_codes §2.1 정본
                labels_applied=applied,
                generated_at=_clock(),
            ),
        )
        return view, None  # 워커·LLM 미실행 — 원장에 행이 없다

    # ② 근거 0건 — 여기부터는 **데이터 유관 문의**다.
    if not request.context.citable_facts():
        view = CounselDraftJobView(
            job_id=str(uuid.uuid4()),
            status=JobPhase.SUCCEEDED.value,
            result=CounselDraftResult(
                draft_status=WireDraftStatus.REJECTED_INSUFFICIENT,
                status_reason="no_citable_evidence",
                labels_applied=applied,
                generated_at=_clock(),
            ),
        )
        return view, None  # 워커·LLM 미실행 — 원장에 행이 없다

    supervisor = _build_supervisor()
    context = _draft_context(request)
    job = await CounselPackEnqueuer(
        supervisor=supervisor, context_store=_context_store, now=_clock
    ).enqueue(
        tenant_id=tenant_id,
        class_ref=request.class_ref,
        contexts={request.student_ref: context},
    )
    provider = require_counsel_provider()
    # 🔴 조립부를 경유한다 — 러너를 여기서 직접 만들면 `require_tracing_disabled`와
    # 체크포인터 선택(`_open_saver`)이 **서비스 경로에서만 빠진다**. 실제로 그랬다:
    # `LANGSMITH_TRACING=true`여도 이 엔드포인트는 그냥 돌았다.
    async with open_counsel_pack_runner(
        supervisor=supervisor,
        context_store=_context_store,
        step_sink=_step_sink,
        draft_store=_draft_store,
        pack_store=_pack_store,
        planner=provider,
        writer=provider,
        regen_max=_REGEN_MAX,
        lease_owner=_LEASE_OWNER,
        # 실행 원장은 라우터와 **같은 인스턴스**를 쓴다 — 워커가 기본 팩토리로 따로 만들면
        # 테스트가 주입한 저장소를 우회해 적재를 관측할 수 없다.
        run_store=_run_store,
    ) as runner:
        # 🔴 `run_next`는 `worker_kind + tenant_id`로만 lease한다 — **job_id를 지정해 집을
        # 수 없다.** 큐에 남의 잡이 남아 있으면 이게 집어오는 건 내 잡이 아니다. 그래도
        # 호출은 유지한다(큐를 비우는 역할이 있다) — 바꾼 것은 **결과를 어디서 읽는가**다.
        ran = await runner.run_next(tenant_id=tenant_id)
        if ran is not None and ran.job_id != job.job_id:
            logger.info(
                "counsel 러너가 다른 잡을 실행했다 mine=%s ran=%s — 결과는 내 잡에서 읽는다",
                job.job_id,
                ran.job_id,
            )
        # 🔴 **이 요청은 자기 잡의 결과만 읽는다.** 종전에는 `ran`(남의 잡일 수 있다)의
        # `result_ref`에서 본문을 꺼내 내 `job_id`·`citations`와 함께 반환했다 —
        # 다른 학생의 초안이 나가고 `_drafts`에도 등록돼 refine까지 오염됐다.
        mine = await supervisor.get(tenant_id=tenant_id, job_id=job.job_id) or job
        view_job_id = str(job.job_id)
        if not _can_report_result(mine.phase):
            # 아직 안 돌았다(queued·leased·running·paused) 또는 취소됐다. **결과가 없다는
            # 것과 실패는 다르다** — `result=None` + 잡 phase가 정직한 표현이다
            # (error_codes §2.1 "queued/generating → 스피너", 계약 `result`가 옵셔널).
            return (
                CounselDraftJobView(
                    job_id=view_job_id, status=mine.phase.value, result=None
                ),
                job.execution_id,
            )
        result = await _wire_result(
            runner, mine, request, applied, job_id=view_job_id, tenant_id=tenant_id
        )
        return (
            CounselDraftJobView(
                job_id=view_job_id, status=mine.phase.value, result=result
            ),
            job.execution_id,
        )


async def _wire_result(
    runner: CounselPackRunner,
    job: WorkerJob,
    request: CounselDraftRequest,
    applied: tuple[str, ...],
    *,
    job_id: str,
    tenant_id: str,
) -> CounselDraftResult:
    """잡 결과 → 계약 `result`. 판정 파생은 `wire_status_for` 한 곳이 한다.

    **잡 성공 ≠ 초안 존재**(불변식 4) — 결과 계약이 저장됐어도 학생 판정은 거부일 수 있다.
    """
    pack = await runner.result_of(job.result_ref) if job.result_ref else None
    if pack is None:
        # 결과 계약 **자체가 없다** = 잡 장애(error_codes §2.5의 failed 정의).
        return CounselDraftResult(
            draft_status=WireDraftStatus.LLM_FAILED,
            status_reason=job.error_code or "job_no_result",
            labels_applied=applied,
            generated_at=_clock(),
        )
    student = pack.results[0] if pack.results else None
    if student is None:
        # 🔴 결과 계약은 있는데 **학생 결과가 0건** — 묶음에 그 학생이 없었다는 뜻이다
        # (`student_refs = sorted(bundle.contexts)`). N=1에서 이건 장애가 아니라
        # **컨텍스트 부재**다. 실패로 내면 화면이 "아직 데이터를 모으는 중이에요"가 아니라
        # "다시 시도"를 그린다 — 인박스 계약 §4 매핑 표가 깨진다(점검 B-4).
        return CounselDraftResult(
            draft_status=WireDraftStatus.REJECTED_INSUFFICIENT,
            status_reason="context_missing",
            labels_applied=applied,
            generated_at=_clock(),
        )
    wire, reason = wire_status_for(student.status, student.fail_reason)
    if wire is not WireDraftStatus.GENERATED:
        return CounselDraftResult(
            draft_status=wire,
            status_reason=reason,
            labels_applied=applied,
            generated_at=_clock(),
        )
    record = (
        await _draft_store.get(
            make_ref(DRAFT_SCHEME, student.draft_id), tenant_id=tenant_id
        )
        if student.draft_id
        else None
    )
    citations = _citations_of(request)
    if record is not None:
        # refine 대상 등록 — 키는 **응답이 싣는 job_id**다(04 §3.9). 종전에는 내부
        # `record.id`로 등록했는데 그 값은 어떤 응답에도 실리지 않아, BE가 refine 대상
        # 키를 얻을 계약 경로가 없었다(호출하면 404 확정 · 99 D).
        _drafts.put(
            (tenant_id, job_id),
            _DraftState(
                context=_draft_context(request),
                citations=citations,
                text=record.content,
                # 🔴 최초 생성이 고른 강조점을 refine이 이어받는다(99 ㉮). 값이 state 밖으로
                #    나오는 경로는 결과 계약뿐이다 — `pack.emphasis_points`(㉲와 같은 자리).
                emphasis=tuple(pack.emphasis_points.get(student.student_ref, ())),
                # 🔴 refine 원장이 쓸 입력 스냅숏(99 ㉭). **워커가 쓴 값과 같은 값**이다
                #    (`worker.py`의 `input_snapshot_hash=job.payload_hash` ·
                #    `payload_hash = content_hash(contexts)`) — 그래서 POST와 refine의
                #    `AI_RUN.input_snapshot_hash`가 **일치**한다. 같은 스냅숏 위의 다음 턴이다.
                snapshot_hash=job.payload_hash,
            ),
        )
    return CounselDraftResult(
        draft_status=WireDraftStatus.GENERATED,
        text=record.content if record else None,
        citations=citations,
        labels_applied=applied,
        generated_at=_clock(),
    )


@router.post("/v1/counsel/drafts", status_code=202)
async def post_counsel_draft(request: Request, response: Response) -> dict[str, Any]:
    """초안 생성 — 헤더·바디 검증 → 멱등 → 근거 선검사 → 워커(N=1) → 계약 뷰 저장."""
    missing = [name for name in _REQUIRED_HEADERS if not request.headers.get(name)]
    if missing:
        raise SnapshotInvalid("필수 헤더 누락", {"missing_headers": missing})

    tenant_id = request.headers["X-Tenant-Id"]
    idempotency_key = request.headers["Idempotency-Key"]

    try:
        raw_body = await request.json()
    except ValueError as exc:
        raise SnapshotInvalid("요청 바디가 유효한 JSON이 아님", str(exc)) from exc

    try:
        draft_request = CounselDraftRequest.model_validate(raw_body)
    except ValidationError as exc:
        raise SnapshotInvalid(
            "요청 바디 스키마 위반", _format_validation_error(exc)
        ) from exc

    try:
        snapshot_from_labels(draft_request.labels)
    except LabelVocabularyError as exc:
        raise SnapshotInvalid(str(exc), {"labels": list(exc.offending)}) from exc

    body_hash = _canonical_hash(raw_body)
    hit = await _idempotency_store.get(
        tenant_id=tenant_id, endpoint=_POST_ENDPOINT, idempotency_key=idempotency_key
    )
    if hit is not None:
        if hit.snapshot_hash == body_hash:
            response.status_code = 202
            return hit.response_body
        raise IdempotencyConflict(
            "같은 Idempotency-Key에 다른 바디", {"idempotency_key": idempotency_key}
        )

    view, execution_id = await _generate(draft_request, tenant_id=tenant_id)
    cached = _CachedView(
        view=view, execution_id=execution_id, correlation_id=uuid.uuid4()
    )
    _view_cache.put((tenant_id, view.job_id), cached)

    envelope = success_envelope(
        data={"job_id": view.job_id, "status": view.status},
        # 🔴 잡의 실행 원장 키다(99 ㊮) — 종전에는 이 자리에서 만든 `uuid.uuid4()`라
        #    AI_RUN의 어느 행도 가리키지 않았다.
        execution_id=cached.envelope_execution_id(),
        versions=counsel_versions(),
    )
    await _idempotency_store.put(
        tenant_id=tenant_id,
        endpoint=_POST_ENDPOINT,
        idempotency_key=idempotency_key,
        snapshot_hash=body_hash,
        response_body=envelope,
    )
    return envelope


async def _refresh_view(
    cached: CounselDraftJobView, *, tenant_id: str
) -> CounselDraftJobView:
    """캐시된 뷰를 **잡 원장의 현재 phase**로 갱신한다 — 정본은 원장이다(㉩).

    🔴 조회에 `tenant_id`를 **반드시** 싣는다 — 안 실으면 남의 잡 phase가 새어 "그 job_id는
    존재한다"가 응답으로 드러난다(존재 은닉이 캐시 키에만 걸려 있으면 안 된다).

    ⚠ **잡이 없을 수도 있다.** `template_only`·근거 0건 경로는 LLM도 워커도 안 타고 뷰만
    만들어 돌려주므로(`_generate` ①②) 원장에 행이 없다 — 그건 장애가 아니라 **잡이 없는
    확정**이라 캐시를 그대로 쓴다.

    ⚠ **아직 못 하는 것:** phase가 뒤늦게 `succeeded`가 됐는데 캐시에 `result`가 없으면
    `status="succeeded"` + `result=None`이 나간다. 결과 본문을 다시 조립하려면 원 요청
    (`citations`·`labels_applied`의 출처)이 필요한데 그건 영속 설계 몫이다 — 계약의
    `result`가 옵셔널이라 형태는 정직하고, 안건은 99에 등재했다.
    """
    try:
        job_uuid = uuid.UUID(cached.job_id)
    except ValueError:  # 캐시 키가 UUID가 아니다 — 갱신 대상이 아니다
        return cached
    job = await _build_supervisor().get(tenant_id=tenant_id, job_id=job_uuid)
    if job is None or job.phase.value == cached.status:
        return cached
    if not _can_report_result(job.phase):
        # 아직 안 끝났거나 취소됐다 — **결과 없음이 정직한 표현**이다(POST와 같은 판정).
        return CounselDraftJobView(
            job_id=cached.job_id, status=job.phase.value, result=None
        )
    if cached.result is None:
        logger.info(
            "counsel 잡이 뒤늦게 %s로 확정됐는데 캐시에 result가 없다 job_id=%s — "
            "본문 재조립은 영속 설계 후속이다(99)",
            job.phase.value,
            cached.job_id,
        )
    return cached.model_copy(update={"status": job.phase.value})


@router.get("/v1/counsel/drafts/{job_id}")
async def get_counsel_draft(job_id: str, request: Request) -> dict[str, Any]:
    """결과 회수 — 잡 성공 ≠ 초안 존재(불변식 4).

    🔴 **캐시를 정본으로 삼지 않는다**(㉩) — 매번 잡 원장에서 현재 phase를 다시 읽는다.
    """
    tenant_id = request.headers.get("X-Tenant-Id")
    if not tenant_id:
        raise SnapshotInvalid("필수 헤더 누락", {"missing_headers": ["X-Tenant-Id"]})
    cached = _view_cache.get((tenant_id, job_id))
    if cached is None:  # 존재 은닉 — 다른 테넌트의 job_id도 여기로 떨어진다
        raise NotFound("job_id 부재", {"job_id": job_id})
    view = await _refresh_view(cached.view, tenant_id=tenant_id)
    # 🔴 실행 키·상관 ID는 **POST가 정한 값을 그대로 들고 간다** — 갱신 대상은 phase뿐이다.
    #    여기서 새로 만들면 같은 잡을 두 번 GET할 때 값이 달라진다(99 ㊮ 실측).
    refreshed = _CachedView(
        view=view,
        execution_id=cached.execution_id,
        correlation_id=cached.correlation_id,
    )
    _view_cache.put((tenant_id, job_id), refreshed)
    return success_envelope(
        data=view.model_dump(mode="json"),
        execution_id=refreshed.envelope_execution_id(),
        versions=counsel_versions(),
    )


async def _record_refine_run(
    refine_context: ExecutionContext,
    execution_id: uuid.UUID,
    *,
    swallow_errors: bool = False,
) -> None:
    """refine 턴 1회를 원장에 남긴다 — 성공·차단·**장애** 전부.

    ⚠ `take(execution_id)`는 **어느 경로에서도 반드시** 불려야 한다. 안 부르면 수집기에
    레코드가 남아 다음 실행에 섞인다(누수).

    `swallow_errors`는 장애 경로 전용이다 — 적재 실패가 원인 예외를 덮으면 진단이
    뒤집힌다("LLM이 죽었다" → "원장이 죽었다"). 정상 경로에서는 fail-closed 그대로 올린다.
    """
    calls = default_llm_call_collector().take(execution_id)
    last = calls[-1].record if calls else None
    try:
        await _run_store.record_run(
            refine_context.to_run_metadata(
                created_at=_clock(),
                model_provider=last.provider if last is not None else None,
                model_name=last.model if last is not None else None,
                # 🔴 **사용 축이다**(#144 · 04 §2.2 8/10 확정) — 이 턴이 **실제로 쓴**
                #    샘플링 파라미터다. 상수로 두면 LLM을 안 부른 턴(생성 전 차단·장애)에
                #    *"그 값으로 돌렸다"* 는 **거짓**이 남는다.
                #    ⚠ 종전엔 이 한 줄만 무조건이라 **같은 행 안에서 축이 갈렸다** —
                #      바로 위 두 줄은 이미 조건부였다. `worker.py`가 셋 다 조건부로
                #      두고 *"한 행 안에서 축이 갈리면 읽는 쪽이 어느 쪽으로도 읽는다"* 고
                #      경고까지 적어 뒀는데 **refine만 안 따랐다**(99 #11 ⓒ · 8/07 해소).
                generation_params=(
                    COUNSEL_GEN_PARAMS if last is not None else None
                ),
            ),
            calls,
        )
    except Exception:  # noqa: BLE001 — 원인 예외를 덮지 않는다(위 docstring)
        if not swallow_errors:
            raise
        logger.exception("refine 장애 턴의 원장 적재 실패 — 원인 예외를 유지한다")


@router.post("/v1/counsel/drafts/{job_id}/refine")
async def post_counsel_refine(job_id: str, request: Request) -> dict[str, Any]:
    """다듬기 1턴 — 동기 · **매 턴 게이트 전체 재통과**(06 §1).

    🔴 **차단도 200이다**(`applied:false` + `blocked_reason`) — 게이트 거부는 에러가 아니다
    (불변식 4 · error_codes §4 "GateRejected를 5xx로 올리는 코드는 리뷰 반려").

    ⚠ **(8/8) 종전 문면은 *"사유 + 문구"* 였고 8/5부터 거짓이었다** — `RefineResponse.message`가
    그때 제거됐다(§2.7 규칙 ③ *"표시 문구는 AI가 주지 않는다"*). **같은 함수 100줄 아래가
    *"문구(`REFINE_BLOCK_MESSAGES`)는 응답에 싣지 않는다"* 라고 적고 있어 한 함수가 자기
    자신과 모순됐다.** 🔴 **8/5에 세 곳을 고치고 넷째를 안 봤다** — 04 §3.9·
    `RefineResponse` docstring·`error_codes`는 전부 맞다(전수 확인 8/8 · 99 #19).

    대상 키는 `job_id`다 — POST 202 응답·Kafka 완료 통지가 싣는 그 값이다.
    FE 계약 §3-③의 `inquiry_id`는 BE가 중계 매핑한다(04 §3.9).
    턴 상한을 판정하지 않는다: `turn_no`는 받아서 로그로만 쓴다(쿼터는 전부 백엔드).
    """
    tenant_id = request.headers.get("X-Tenant-Id")
    if not tenant_id:
        raise SnapshotInvalid("필수 헤더 누락", {"missing_headers": ["X-Tenant-Id"]})

    try:
        raw_body = await request.json()
    except ValueError as exc:
        raise SnapshotInvalid("요청 바디가 유효한 JSON이 아님", str(exc)) from exc
    try:
        refine_request = RefineRequest.model_validate(raw_body)
    except ValidationError as exc:
        raise SnapshotInvalid(
            "요청 바디 스키마 위반", _format_validation_error(exc)
        ) from exc

    state = _drafts.get((tenant_id, job_id))
    if state is None:  # 존재 은닉 — 다른 테넌트의 job_id도 여기로 떨어진다
        raise NotFound("job_id 부재", {"job_id": job_id})

    execution_id = uuid.uuid4()
    refine_context = _refine_execution_context(
        execution_id, tenant_id, state.snapshot_hash
    )
    #: 🔴 `finally`가 성공·차단·**모든 종류의 실패**를 지나게 하려고 둔 플래그다.
    #:  실패 경로에서만 적재 오류를 삼킨다(성공·차단은 fail-closed 그대로).
    failed = True
    try:
        outcome = await refine_draft(
            context=state.context,
            instruction=refine_request.instruction,
            # 🔴 최초 생성이 고른 강조점을 이어받는다(99 ㉮) — 없으면 턴마다 사라진다.
            emphasis=state.emphasis,
            # 🔴 전역 `_provider`를 직접 읽지 않는다. POST 경로는 이미 이걸 쓰는데
            # **refine만 우회**하고 있었다 — #108이 "조용한 Fake 금지"를 세웠는데 이 한 줄이
            # 빠져 CI는 초록이었다(같은 패턴 5번째). 가드는 `test_provider_access_guard`.
            writer=require_counsel_provider(),
            execution_context=refine_context,
            regen_max=_REGEN_MAX,
            # 🔴 **누적의 배선.** `state.text`는 종전에 write-only였다(읽는 코드 0곳) —
            # 매 턴 원본에서 새로 써서 턴1의 반영이 턴2에서 되살아났다. 팀 공유본
            # (와이어프레임 v3.5·프로토타입·데이터계약)이 전부 누적을 전제로 만들어져 있다.
            previous_text=state.text,
        )
        failed = False
    except LlmError as exc:
        # 변환은 **LLM 예외만의 일**이다 — `RedactionUncertain`은 이미 `DomainException`이라
        # 변환 없이 그대로 500으로 나간다(그래서 여기 절이 필요 없다).
        raise domain_error_for(exc) from exc
    finally:
        # 🔴 **성공·차단·모든 종류의 실패가 여기를 지난다.** 예외 종류가 늘어도 원장은
        #   안 갈린다. 종전에는 이 부수효과가 `except LlmError` 절 **안에** 복제돼 있어서,
        #   그 절이 못 잡는 예외(`RedactionUncertain` — `DomainException`)만 원장에서
        #   사라졌다(8/7 실측: AI_RUN 0 · 수집기 잔존 1).
        #   ⚠ `except DomainException`을 **추가**해서 때우지 않았다 — 그건 같은 복제를 한 번
        #   더 하는 것이고 다음 예외에서 또 빠진다. 절이 아니라 구조를 고친다.
        # ⚠ `finally`는 예외가 위로 전파되기 **전에** 돈다 — 적재가 raise보다 먼저다.
        # 실행 원장 — refine 턴도 하나의 실행이다(불변식 8). 차단 턴도 남긴다: 차단은 에러가
        # 아니고(불변식 4) 어떤 호출이 무엇을 냈길래 게이트가 걸렸는지가 정확히 추적 대상이다.
        # ⚠ `quota_consumed`(pack state)에는 들어가지 않는다 — 이 경로는 그래프 밖이다.
        # ⚠ 실패 경로에서만 적재 오류를 삼킨다 — `LedgerWriteFailed`가 원인 예외를 덮으면
        #   "LLM이 죽었다"가 "원장이 죽었다"로 바뀌어 진단이 뒤집힌다.
        await _record_refine_run(
            refine_context, execution_id, swallow_errors=failed
        )
    if outcome.applied and outcome.text:
        state.text = outcome.text  # 반영분만 승격 — 차단 턴은 직전 버전 유지(계약 §6)
        response = RefineResponse(
            applied=True, text=outcome.text, citations=state.citations
        )
    else:
        logger.info(
            "refine 차단 turn=%d reason=%s",
            refine_request.turn_no,
            outcome.blocked_reason.value if outcome.blocked_reason else "unknown",
        )
        # ⚠ 문구(`REFINE_BLOCK_MESSAGES`)는 **응답에 싣지 않는다**(8/5) — 표시 문구는
        # BE 소유다(error_codes §2.6 규칙 3). 표 자체는 `part_a/06` §4의 투영이라
        # 남겨 둔다(골든 테스트가 BE 매핑의 기대값으로 참조한다).
        response = RefineResponse(
            applied=False, blocked_reason=outcome.blocked_reason
        )
    return success_envelope(
        data=response.model_dump(mode="json"),
        execution_id=str(execution_id),
        versions=counsel_versions(),
    )



#: 이 라우터가 응답하는 경로 접두와 그 버전 세트 — `api/app.py`가 **실패 응답**에 쓴다(99 ㊓).
#: 🔴 접두를 여기 두는 이유: **경로를 바꾸는 사람과 접두를 고치는 사람이 같아야 한다.**
#:  `app.py`에 박으면 다른 파일이라 조용히 갈린다.
#: `/v1/counsel/drafts`·`/{job_id}`·`/refine` 셋을 한 접두가 덮는다.
VERSION_SCOPE: Final = RouterScope("/v1/counsel", counsel_versions)

__all__ = [
    "CounselProviderNotWired",
    "bootstrap_counsel_provider",
    "counsel_versions",
    "require_counsel_provider",
    "set_counsel_run_store",
    "reset_counsel_stores",
    "router",
    "set_counsel_provider",
    "set_counsel_stores",
]
