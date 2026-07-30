"""counsel_pack 조립 seam — provider 등록·전송 재시도 주입·체크포인터 선택.

`probe/assembly.py`와 같은 자리다. 순수 로직(state·graph·gate·prompt)은 infra에 무의존이고,
구체 게이트웨이·체크포인터를 여기서 한 번에 주입한다.

**머지 조건 ② — `ModelRole.COUNSELOR` provider 등록.** `gateway.complete`가
`self._providers.get(request.role)`로 조회하고 없으면 `LookupError`를 올리므로, 등록이
빠지면 첫 호출에서 터진다. 기본은 Fake provider이며 CI·데모는 LLM 없이 돈다.

**`transport_retry`는 여기서 주입한다** — `{COUNSELOR: 0}`. 상담팩은 결정론 폴백(게이트
재생성 ≤3 + 실패 시 정직한 기록)이 있어 전송 재시도를 얹지 않는다(브리핑 narrator와 동일).
01 §5 "게이트웨이는 값을 모르며 조립부가 주입한다" — B의 `verify_config` 시트에는
counselor 행이 없으므로 시트를 참조하지 않는다.

⚠ `llm/gateway.py`는 건드리지 않는다 — **B 단독 소유**(02_ownership §3). B의 트레이스
PR(#48)은 머지됐고, 그 기동 가드가 요구하는 `trace_masking_hook`은 이 조립부가 주입한다
(gateway 자체는 여전히 무변경).
⚠ LangSmith 계측을 이 파일에 추가하지 않는다 — 노드 경계 계측은 `part_b/09` §2-16 P2
범위이며, 실측 결과 (b) 훅으로는 트레이스를 가릴 수 없다(`part_a/11_langsmith_trace_probe.md`).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from uuid import UUID, uuid4

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver

from ai.agents.checkpointer import open_checkpointer
from ai.agents.supervisor import Supervisor
from ai.composition.counsel.provider import (
    CounselPlanner,
    DraftWriter,
    GatewayDraftWriter,
)
from ai.composition.counsel.stores import (
    AgentStepSink,
    ContextStore,
    DraftResultStore,
    InMemoryDraftResultStore,
    InMemoryPackResultStore,
    PackResultStore,
)
from ai.composition.counsel.worker import CounselPackRunner
from ai.contracts.llm import LLMProvider, ModelRole
from ai.db.settings import DbSettings, get_db_settings
from ai.llm.gateway import LlmGateway
from ai.runtime.trace_masking import RedactionTripwireTraceHook
from ai.runtime.tracing import require_tracing_disabled

_PG = "pg"

#: 상담팩 전송 재시도 = 0 — LLM 실패 시 재시도 없이 정직하게 기록한다(브리핑 narrator 동일).
COUNSELOR_TRANSPORT_RETRY = 0

#: 게이트 실패 재생성 상한 — ERD DRAFT_BLOCK "≤3"(불변식 6). 조립부가 주입한다.
DEFAULT_REGEN_MAX = 3


def build_counsel_gateway(provider: LLMProvider) -> LlmGateway:
    """counselor role provider를 등록한 게이트웨이 — **머지 조건 ②**.

    등록이 빠지면 `gateway.complete`가 role 조회에 실패한다. 전송 재시도와
    `trace_masking_hook`은 여기서 주입한다(09 §2-16 P1′ 기동 가드 — 미주입이면
    `LANGSMITH_TRACING=true`에서 생성이 실패한다).
    """
    return LlmGateway(
        {ModelRole.COUNSELOR: provider},
        transport_retry={ModelRole.COUNSELOR: COUNSELOR_TRANSPORT_RETRY},
        trace_masking_hook=RedactionTripwireTraceHook(),
    )


@asynccontextmanager
async def _open_saver(settings: DbSettings) -> AsyncIterator[BaseCheckpointSaver]:  # type: ignore[type-arg]
    """store_backend에 맞춘 체크포인터(probe/assembly와 동일 규약)."""
    if settings.store_backend == _PG:
        async with open_checkpointer(settings) as saver:
            yield saver
    else:
        yield InMemorySaver()


@asynccontextmanager
async def open_counsel_pack_runner(
    *,
    supervisor: Supervisor,
    context_store: ContextStore,
    step_sink: AgentStepSink,
    lease_owner: str,
    planner: CounselPlanner,
    writer: DraftWriter,
    draft_store: DraftResultStore | None = None,
    pack_store: PackResultStore | None = None,  # 미지정이면 인메모리(PG는 후속)
    db_settings: DbSettings | None = None,
    regen_max: int = DEFAULT_REGEN_MAX,
    new_id: Callable[[], UUID] = uuid4,
) -> AsyncIterator[CounselPackRunner]:
    """설정에 맞춘 체크포인터와 LLM 접점을 주입한 러너를 연다.

    ⚠ `planner`·`writer`는 **명시 주입 필수**다. 이전에는 미주입 시 `FakeCounselProvider`로
    조용히 폴백했는데, 운영 배선 실수가 곧 **날조 산출 저장**이었다(조용한 Fake가 최악).
    테스트·개발 조립부는 Fake를 명시적으로 꽂고, 프로덕션 미배선은 기동 시점에 터진다.
    """
    # ㉒-a fail-closed — 추적이 켜져 있으면 아예 돌지 않는다. LangGraph 워커는 마스킹 전
    # state를 노드 경계로 내보내므로(part_a/11 §1.1) P2 은닉 전까지 기동을 막는다.
    # ⚠ briefing 조립부(`composition/provider.py`)에는 걸지 않는다 — 실측상 span 0건이라
    #   위험 표면이 아니다(11 §3 "briefing만 돌리면 프로젝트조차 생성되지 않았다").
    require_tracing_disabled("counsel_pack")

    resolved_db = db_settings or get_db_settings()
    #: 산출물 저장소 기본값 — 인메모리(PG 영속은 후속 · 99 D). 미주입이면 결과가 어디에도
    #: 도착하지 않으므로 None을 허용하지 않는다.
    drafts = draft_store or InMemoryDraftResultStore()
    packs = pack_store or InMemoryPackResultStore()
    async with _open_saver(resolved_db) as checkpointer:
        yield CounselPackRunner(
            supervisor=supervisor,
            context_store=context_store,
            draft_store=drafts,
            pack_store=packs,
            step_sink=step_sink,
            planner=planner,
            writer=writer,
            checkpointer=checkpointer,
            regen_max=regen_max,
            lease_owner=lease_owner,
            new_id=new_id,
        )


def build_gateway_writer(provider: LLMProvider) -> GatewayDraftWriter:
    """실 LLM 경로 조립 — provider 등록 + 재시도 주입을 한 곳에서 끝낸다."""
    return GatewayDraftWriter(build_counsel_gateway(provider))


__all__ = [
    "COUNSELOR_TRANSPORT_RETRY",
    "DEFAULT_REGEN_MAX",
    "build_counsel_gateway",
    "build_gateway_writer",
    "open_counsel_pack_runner",
]
