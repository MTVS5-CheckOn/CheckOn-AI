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

import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from functools import lru_cache
from uuid import UUID, uuid4

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver

from ai.agents.checkpointer import open_checkpointer
from ai.agents.supervisor import Supervisor
from ai.composition.counsel.provider import (
    CompositeCounselProvider,
    CounselPlanner,
    DraftWriter,
    FakeCounselLlmProvider,
    GatewayDraftWriter,
    GatewayPlanner,
)
from ai.composition.counsel.settings import CounselSettings, get_counsel_settings
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
from ai.db.repositories.llm_payload import capture_payloads
from ai.db.repositories.run_store import RunStore, default_llm_call_collector
from ai.db.settings import DbSettings, get_db_settings
from ai.llm.gateway import LlmCallRecorder, LlmGateway
from ai.runtime.trace_masking import RedactionTripwireTraceHook
from ai.runtime.tracing import require_tracing_disabled

logger = logging.getLogger(__name__)

_PG = "pg"
_OPENAI_COMPAT = "openai_compat"

#: 상담팩 전송 재시도 = 0 — LLM 실패 시 재시도 없이 정직하게 기록한다(브리핑 narrator 동일).
COUNSELOR_TRANSPORT_RETRY = 0

#: 게이트 실패 재생성 상한 — ERD DRAFT_BLOCK "≤3"(불변식 6). 조립부가 주입한다.
DEFAULT_REGEN_MAX = 3


def build_counsel_gateway(
    provider: LLMProvider, *, recorder: LlmCallRecorder | None = None
) -> LlmGateway:
    """counselor role provider를 등록한 게이트웨이 — **머지 조건 ②**.

    등록이 빠지면 `gateway.complete`가 role 조회에 실패한다. 전송 재시도와
    `trace_masking_hook`은 여기서 주입한다(09 §2-16 P1′ 기동 가드 — 미주입이면
    `LANGSMITH_TRACING=true`에서 생성이 실패한다).

    `recorder`는 기본이 공용 수집기다(99 ㊻ⓐ) — `build_brief_gateway`와 같은 규약이다.
    수집분은 워커가 `record_run`으로 영속한다(`composition/counsel/worker.py` ④′).
    """
    return LlmGateway(
        # 전송 본문 포착(99 ㉝) — `build_brief_gateway`와 같은 규약이다.
        {ModelRole.COUNSELOR: capture_payloads(provider)},
        recorder=recorder or default_llm_call_collector(),
        transport_retry={ModelRole.COUNSELOR: COUNSELOR_TRANSPORT_RETRY},
        trace_masking_hook=RedactionTripwireTraceHook(),
    )


@lru_cache
def _default_memory_checkpointer() -> InMemorySaver:
    """프로세스 공용 인메모리 체크포인터 — `_default_agent_job_store()`와 짝이다.

    🔴 **컨텍스트마다 새로 만들면 재개할 state가 없다.** `_resume_input`의 `aget_state`가
    항상 빈 스냅숏을 받아 **매번 init(cursor=0)** 을 투입하므로, 재개 분기도 그 안의
    불변식 ④ `context_hash` 대조도 서비스 경로에서 한 번도 돌지 않았다(99 ㉦).
    """
    return InMemorySaver()


def reset_default_memory_checkpointer() -> None:
    """공용 체크포인터를 버린다 — **테스트 격리 전용**.

    ⚠ 잡 원장 리셋과 **반드시 함께** 부른다 — 한쪽만 지우면 죽은 잡의 체크포인트가 다음
    테스트의 같은 `thread_id`(=`job_id`)로 되살아난다.
    """
    _default_memory_checkpointer.cache_clear()


@asynccontextmanager
async def _open_saver(settings: DbSettings) -> AsyncIterator[BaseCheckpointSaver]:  # type: ignore[type-arg]
    """store_backend에 맞춘 체크포인터(probe/assembly와 동일 규약).

    🔴 **두 분기의 수명이 의도적으로 다르다 — 여기가 가장 틀리기 쉬운 자리다.**

      `pg`     요청 스코프. `AsyncPostgresSaver`는 **커넥션**이라 컨텍스트를 벗어날 때
               닫혀야 한다. 싱글턴으로 만들면 커넥션 수명·풀·트랜잭션 경계가 요청과
               어긋난다. 저장소가 외부라 **공유할 이유도 없다** — 이미 산다.
      memory   **프로세스 공용.** 저장소가 이 객체 자체라 요청과 함께 죽으면 재개가
               통째로 사라진다(㉦).

    ⚠ **memory는 닫지 않는다.** `finally`에 정리를 넣거나 `async with`로 감싸면 첫 요청이
    끝날 때 공용 인스턴스가 닫혀 **다음 요청이 죽는다.** 아래 `else` 분기에 정리 코드를
    추가하지 마라 — `test_the_memory_checkpointer_is_not_closed_on_exit`가 지킨다.
    """
    if settings.store_backend == _PG:
        async with open_checkpointer(settings) as saver:
            yield saver
    else:
        # ⚠ 여기서 yield만 한다 — 닫지 않는 것이 계약이다(위 docstring).
        yield _default_memory_checkpointer()


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
    run_store: RunStore | None = None,
) -> AsyncIterator[CounselPackRunner]:
    """설정에 맞춘 체크포인터와 LLM 접점을 주입한 러너를 연다.

    ⚠ `planner`·`writer`는 **명시 주입 필수**다. 이전에는 미주입 시 `FakeCounselProvider`로
    조용히 폴백했는데, 운영 배선 실수가 곧 **날조 산출 저장**이었다(조용한 Fake가 최악).
    테스트·개발 조립부는 Fake를 명시적으로 꽂고, 프로덕션 미배선은 기동 시점에 터진다.

    `run_store`는 미지정이면 러너가 기본 팩토리로 만든다 — 주입은 **호출자와 같은 인스턴스**를
    쓰기 위한 seam이다(라우터·테스트가 적재를 관측하려면 같은 저장소여야 한다).
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
            run_store=run_store,
        )


def build_gateway_writer(
    provider: LLMProvider, *, recorder: LlmCallRecorder | None = None
) -> GatewayDraftWriter:
    """실 LLM 경로 조립 — provider 등록 + 재시도·recorder 주입을 한 곳에서 끝낸다."""
    return GatewayDraftWriter(build_counsel_gateway(provider, recorder=recorder))


# ── 조립 루트 ─────────────────────────────────────────────────────
#
# 🔴 **두 층으로 나눈다.** 선례가 둘인데 성격이 다르고, counsel은 둘 다 필요하다.
#
#   `problem_generation/bootstrap.py`  순수 조립 — env를 안 읽고 호출자가 의존성을 준다
#   `classify.build_classify_provider` env(`LLM_PROVIDER`)로 fake↔실 구현을 고른다
#
# 가르는 기준은 **테스트·러너가 무엇을 주입하는가**다. 평가 러너는 자기 관측 래퍼
# (`_CountingProvider(build_openai_compat_provider())`)를 감싼 provider를 이미 들고 있으므로
# env 선택이 끼면 안 된다 — 순수 조립(`build_counsel_provider`)을 부른다. 프로덕션 기동은
# 반대로 아무것도 안 들고 있으므로 env 층(`build_counsel_llm_provider`)이 필요하다.
# 한 함수로 합치면 러너가 env를 우회하려고 내부를 다시 뜯게 된다.


def build_counsel_provider(
    provider: LLMProvider, *, recorder: LlmCallRecorder | None = None
) -> CompositeCounselProvider:
    """**순수 조립** — 주어진 LLM provider로 plan+write 한 객체를 만든다(env 미참조).

    라우터·워커는 planner·writer를 **한 객체**로 받는데 실 경로는 둘이라 어댑터가 필요하다.
    """
    gateway = build_counsel_gateway(provider, recorder=recorder)
    return CompositeCounselProvider(GatewayPlanner(gateway), GatewayDraftWriter(gateway))


def build_counsel_llm_provider(settings: CounselSettings | None = None) -> LLMProvider:
    """**env 선택** — `LLM_PROVIDER`로 fake↔실 구현을 고른다(`classify`와 같은 규약).

    🔴 **fake를 고르는 것과 배선을 잊는 것은 다르다.** 잊으면 기동이 막히고
    (`CounselProviderNotWired`), 고르면 여기서 **경고 로그**가 남고 산출물의
    `LLM_CALL.provider`·`AI_RUN.model_provider`에 `fake-counsel`이 적힌다 — 사후에
    "이 초안이 진짜였나"를 가릴 수 있다. 종전 기본값(`FakeCounselProvider`)은 게이트웨이를
    아예 안 거쳐서 **원장에 아무것도 안 남았다.**

    벤더 독립: `openai` import는 어댑터 안에만 있고 여기선 구현을 **선택만** 한다.
    """
    settings = settings or get_counsel_settings()
    if settings.llm_provider == _OPENAI_COMPAT:
        from ai.llm.providers.openai_compat import (  # noqa: PLC0415
            build_openai_compat_provider,
        )

        return build_openai_compat_provider()
    logger.warning(
        "counsel provider=fake — LLM_PROVIDER=%r이라 결정론 Fake로 조립한다. "
        "실 LLM 호출은 0건이고 산출물에는 provider=%r가 남는다(사후 구분용). "
        "실 경로는 LLM_PROVIDER=%s.",
        settings.llm_provider,
        FakeCounselLlmProvider().name,
        _OPENAI_COMPAT,
    )
    return FakeCounselLlmProvider()


__all__ = [
    "COUNSELOR_TRANSPORT_RETRY",
    "DEFAULT_REGEN_MAX",
    "reset_default_memory_checkpointer",
    "build_counsel_gateway",
    "build_counsel_llm_provider",
    "build_counsel_provider",
    "build_gateway_writer",
    "open_counsel_pack_runner",
]
