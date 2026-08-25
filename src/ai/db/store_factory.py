"""저장소 팩토리 — settings.store_backend로 InMemory↔PG를 고른다 (D-② 커밋⑤).

소유: 박진희 (db). database_url에 기본값이 있어 URL 유무로는 못 고르므로 명시 플래그로
고른다. 기본 "memory"라 CI·데모는 DB 없이 돌고, 실 DB 연동 시 .env에서 "pg"로 전환한다.
PG 저장소 생성은 엔진을 lazy로 만들 뿐 접속하지 않는다(session.py) — import 시 DB 불필요.
"""

from __future__ import annotations

from functools import lru_cache

from ai.agents.job_store import InMemoryJobStore, JobStore

#: 🔴 **두 축의 동명 클래스를 alias로 갈라 받는다**(99 #37) — 이름이 같아서 한 파일에서
#: 그냥 import하면 **뒤에 온 것이 앞을 덮는다.** 의미가 드러나는 별칭을 쓴다.
from ai.composition.counsel.stores import (
    AgentStepSink as CounselAgentStepSink,
)
from ai.composition.counsel.stores import (
    ContextStore,
    DraftResultStore,
    InMemoryContextStore,
    InMemoryDraftResultStore,
    InMemoryPackResultStore,
    PackResultStore,
)
from ai.composition.counsel.stores import (
    InMemoryAgentStepSink as InMemoryCounselAgentStepSink,
)
from ai.db.counsel_read_model import (
    CounselDraftViewStore,
    NullCounselDraftViewStore,
    PgCounselDraftViewStore,
)
from ai.db.repositories.agent_job import PgJobStore
from ai.db.repositories.counsel_context_store import PgContextStore
from ai.db.repositories.counsel_draft_store import PgDraftResultStore
from ai.db.repositories.counsel_step_store import PgCounselAgentStepSink
from ai.db.repositories.detection_store import (
    DetectionStore,
    InMemoryDetectionStore,
    PgDetectionStore,
)
from ai.db.repositories.idempotency import (
    IdempotencyStore,
    InMemoryIdempotencyStore,
    PgIdempotencyStore,
)
from ai.db.repositories.inquiry_class_store import (
    InMemoryInquiryClassStore,
    InquiryClassStore,
    PgInquiryClassStore,
)
from ai.db.repositories.label_confirmation_store import (
    LabelConfirmationStore,
    PgLabelConfirmationStore,
    default_label_confirmation_store,
)
from ai.db.repositories.pack_store import PgPackResultStore
from ai.db.repositories.run_store import (
    InMemoryRunStore,
    PgRunStore,
    RunStore,
)
from ai.db.session import get_sessionmaker
from ai.db.settings import DbSettings, get_db_settings

_PG = "pg"


@lru_cache
def _default_inquiry_class_store() -> InMemoryInquiryClassStore:
    """프로세스 공용 인메모리 저장소 — `default_llm_call_collector()`와 같은 규약.

    🔴 **호출마다 새 인스턴스를 주면 정정 루프가 통째로 끊긴다.** `/v1/classify`가 A에
    적재하고 `/v1/confirmations`가 B를 읽어 `apply_confirmation`이 행을 못 찾고, 라우터가
    404를 낸다 — 그 404가 *"폴백이라 적재되지 않았다"*(error_codes §2.7)와 **같은 응답**이라
    강사 정정이 전부 유실되는데 로그·응답 어디에도 이상 신호가 없다.
    """
    return InMemoryInquiryClassStore()


def reset_default_inquiry_class_store() -> None:
    """공용 인메모리 저장소를 비운다 — **테스트 격리 전용**.

    ⚠ 공유로 바꾸면 라우터의 `_store = build_inquiry_class_store()` 재바인딩이 같은 객체를
    다시 가리켜 **아무것도 리셋하지 않는다**(테스트 간 행 누수). 캐시를 버려 다음 호출이
    새 인스턴스를 받게 한다 — 두 라우터가 각자 재바인딩할 필요가 없다.
    PG 백엔드에는 영향이 없다(세션메이커가 원본이라 이미 공유).
    """
    _default_inquiry_class_store.cache_clear()


def build_inquiry_class_store(
    settings: DbSettings | None = None,
) -> InquiryClassStore:
    """분류 평가셋 저장소 — 적재 실패는 **fail-closed**(inquiry_class_store.py 참조).

    🔴 인메모리도 **프로세스 공용 1개**다. `/v1/classify`와 `/v1/confirmations`가 같은 행을
    봐야 정정 루프가 닫힌다(`counsel.py`가 실행 원장에 같은 판단을 적어 뒀다 — *"워커가 기본
    팩토리로 따로 만들면 테스트가 주입한 저장소를 우회한다"*).
    """
    settings = settings or get_db_settings()
    if settings.store_backend == _PG:
        return PgInquiryClassStore(sessionmaker=get_sessionmaker())
    return _default_inquiry_class_store()


def build_label_confirmation_store(
    settings: DbSettings | None = None,
) -> LabelConfirmationStore:
    """라벨 확정 **집계** 저장소 — 99 #239(승인: 염준영 · 2026-08-25).

    🔴 인메모리도 **프로세스 공용 1개**다(`build_inquiry_class_store` 와 같은 규약) —
    라우터와 검사가 같은 수를 봐야 한다.
    ⚠ 🔴 **PG 는 매번 새로 만든다** — 상태가 DB 에 있어 인스턴스를 공유할 이유가 없다.
    🔴 **분기가 여기 있는 이유**: `test_store_backend_default_assembly.py` 가
    «`store_backend` 를 보는데 **아무도 안 보는 자리**» 를 잡는다 — 저장소 모듈 안에
    두면 조립 루트가 그것을 안 부른다(실측 2026-08-25 · 그 가드가 이 회차에 red 를 냈다).
    """
    settings = settings or get_db_settings()
    if settings.store_backend == _PG:
        return PgLabelConfirmationStore(sessionmaker=get_sessionmaker())
    return default_label_confirmation_store()


def build_run_store(settings: DbSettings | None = None) -> RunStore:
    """실행 원장(AI_RUN·LLM_CALL) 저장소 — 적재 실패는 **fail-open**(run_store.py 참조).

    감지 원장(`build_detection_store`, fail-closed)과 정책이 반대인 이유는 소비자가 다르기
    때문이다 — 이쪽은 회계·추적 관측이고, 실패로 요청을 되돌리면 관측이 기능을 이긴다.
    """
    settings = settings or get_db_settings()
    if settings.store_backend == _PG:
        return PgRunStore(sessionmaker=get_sessionmaker())
    return InMemoryRunStore()


@lru_cache
def _default_agent_job_store() -> InMemoryJobStore:
    """프로세스 공용 잡 원장 — `_default_inquiry_class_store()`와 같은 규약.

    🔴 **호출마다 새 인스턴스를 주면 잡이 요청과 함께 죽는다.** POST가 적재한 잡을 다음
    요청이 못 보므로 ⓐ `paused` 잡을 재개할 주체가 없고 ⓑ `lease` 만료 recovery가 회수할
    대상을 잃고 ⓒ GET이 현재 phase를 다시 읽을 원본이 없다(㉩). 실제로 counsel의 재개
    경로는 **한 번도 실행된 적이 없었다** — 그 위에 지은 ⑰의 종단 신뢰성이 인메모리
    백엔드에서는 서 있지 않았다(99 ㉦).

    ⚠ **멀티 워커에서는 여전히 갈린다** — "프로세스 공용"은 한 프로세스 안에서만 참이다
    (99 ㉬와 같은 한계). 진짜 답은 PG 백엔드를 기본으로 올리는 것이고 그건 배포 축이다.
    """
    return InMemoryJobStore()


def reset_default_agent_job_store() -> None:
    """공용 잡 원장을 비운다 — **테스트 격리 전용**(`reset_counsel_stores`가 부른다).

    캐시를 버려 다음 호출이 새 인스턴스를 받게 한다 — `reset_default_inquiry_class_store`와
    같은 방식이다(공유 인스턴스를 비우는 대신 캐시를 버린다 · 그쪽 docstring 참조).
    ⚠ 체크포인터도 함께 버려야 짝이 맞는다(`reset_default_memory_checkpointer`) — 잡만
    지우면 죽은 잡의 체크포인트가 다음 테스트의 같은 thread_id로 되살아난다.
    """
    _default_agent_job_store.cache_clear()


def reset_shared_agent_runtime() -> None:
    """**A·B 공용** 잡 원장을 비운다 — 테스트 격리 전용(99 ㊒).

    🔴 **이 저장소는 counsel 것이 아니다.** B가 pg 워커에서 `build_agent_job_store()`를
    그대로 쓰기로 확정하면서(8/7) `store_backend=memory`에서 **counsel 잡과
    problem_generation 잡이 같은 싱글턴에 산다.** lease는 `worker_kind`로 격리되지만
    (`job_store.py` · `test_counsel_runtime_lifetime`가 잠근다) **리셋에는 격리가 없다** —
    비우면 둘 다 사라진다.

    ⚠ 그래서 `reset_counsel_stores()`에서 **떼어 냈다.** 종전에는 counsel 이름을 단 함수가
    counsel 밖을 지웠고, 다음 사람이 *"counsel 것만 지우겠지"* 로 읽으면 틀린다.
    잡을 적재하는 테스트는 이 함수를 **명시적으로** 부른다.

    ⚠ 체크포인터는 여기 없다 — `_default_memory_checkpointer`는 **counsel 소유**이고
    (`composition/counsel/assembly.py`), probe는 자기 `InMemorySaver()`를 따로 만든다.
    """
    _default_agent_job_store.cache_clear()


def build_agent_job_store(settings: DbSettings | None = None) -> JobStore:
    """슈퍼바이저 실행 원장 — PG 선택 시 재시작·멀티워커 안전 저장소.

    🔴 인메모리도 **프로세스 공용 1개**다(㉫ `build_inquiry_class_store`와 같은 판단) —
    잡의 수명이 요청보다 길어야 재개·recovery·GET 갱신이 성립한다. 위 docstring 참조.
    """
    settings = settings or get_db_settings()
    if settings.store_backend == _PG:
        return PgJobStore(sessionmaker=get_sessionmaker())
    return _default_agent_job_store()


def build_idempotency_store(settings: DbSettings | None = None) -> IdempotencyStore:
    """멱등 캐시 저장소 — 실패는 fail-open(idempotency.py 참조)."""
    settings = settings or get_db_settings()
    if settings.store_backend == _PG:
        return PgIdempotencyStore(sessionmaker=get_sessionmaker())
    return InMemoryIdempotencyStore()


def build_detection_store(settings: DbSettings | None = None) -> DetectionStore:
    """감지 원장 저장소 — 실패는 fail-closed(detection_store.py 참조)."""
    settings = settings or get_db_settings()
    if settings.store_backend == _PG:
        return PgDetectionStore(sessionmaker=get_sessionmaker())
    return InMemoryDetectionStore()




def build_pack_result_store(settings: DbSettings | None = None) -> PackResultStore:
    """counsel 팩 결과(`pack://`) 저장소 — `result_ref`가 가리키는 대상 (99 ㉕).

    🔴 **기본값은 그대로 memory다** — `store_backend` 플립은 배포 결정이고 이 함수의
    축이 아니다. 여기가 하는 일은 **PG를 도달 가능하게** 만드는 것이다: 팩토리 분기가
    없으면 `PgPackResultStore`는 정의만 있고 **프로덕션 소비가 0**이 되고, 그건 99 #22가
    등재한 형태(`ItemCandidate` — 정의·마이그레이션까지 있는데 아무도 안 쓴다)다.
    """
    settings = settings or get_db_settings()
    if settings.store_backend == _PG:
        return PgPackResultStore(sessionmaker=get_sessionmaker())
    return InMemoryPackResultStore()


def build_context_store(settings: DbSettings | None = None) -> ContextStore:
    """counsel 워커의 **입력 묶음**(`context://`) 저장소 (㉻ · 지시서 73 §5).

    🔴 **이 분기가 없어서 ㉻ ⓐ가 있었다.** `counsel.py`의 `_context_store`는 초기값도
    reset도 `InMemoryContextStore()` 리터럴이라 `STORE_BACKEND=pg`가 **아무 영향을 못 줬고**,
    다른 프로세스의 워커는 `payload_ref`를 해소하지 못해 `context_bundle_missing`으로 죽었다
    (#37의 스텝 싱크와 같은 형태).

    ⚠ **pg에서 인메모리로 강등하지 않는다** — 접속 실패는 실패로 올라온다. 폴백이 있으면
    *"DB가 죽은 것"* 과 *"정상"* 이 같은 응답이 되고 그 사이 입력이 휘발한다.
    """
    settings = settings or get_db_settings()
    if settings.store_backend == _PG:
        return PgContextStore(sessionmaker=get_sessionmaker())
    return InMemoryContextStore()


def build_draft_result_store(
    settings: DbSettings | None = None,
) -> DraftResultStore:
    """counsel 초안 **본문**(`draft://`) 저장소 (㉻ · 지시서 73 §5).

    🔴 **이 분기가 없어서 ㉻ ⓑ가 있었다.** 늦게 성공한 잡이 `phase=succeeded`인데 GET은
    `result=None`이었다 — 본문이 워커 프로세스의 dict에만 있었기 때문이다.

    ⚠ **`build_context_store`와 짝이다** — 한쪽만 배선하면 입력은 복원되는데 본문이
    사라지거나(또는 그 반대) **결손이 절반만 닫힌다.**
    """
    settings = settings or get_db_settings()
    if settings.store_backend == _PG:
        return PgDraftResultStore(sessionmaker=get_sessionmaker())
    return InMemoryDraftResultStore()


def build_counsel_draft_view_store(
    settings: DbSettings | None = None,
) -> CounselDraftViewStore:
    """상담 읽기 모델(`COUNSEL_DRAFT_VIEW`) 저장소 — `_view_cache`·`_drafts`의 뒷면 (99 ㉿).

    🔴 **memory에서는 `Null…`이다** — 인메모리 dict를 하나 더 두면 라우터 캐시와 **정본이
    둘**이 되고, 축출·재시작 증상이 어느 쪽 때문인지 못 가린다. memory의 v1 정본은 캐시다.
    ⚠ 이 분기가 없으면 `PgCounselDraftViewStore`는 **프로덕션 소비가 0**이 된다(99 #22).
    """
    settings = settings or get_db_settings()
    if settings.store_backend == _PG:
        return PgCounselDraftViewStore(get_sessionmaker())
    return NullCounselDraftViewStore()


def build_counsel_agent_step_sink(
    settings: DbSettings | None = None,
) -> CounselAgentStepSink:
    """counsel 스텝(agent_step) 싱크 — 🔴 **counsel 계약 타입**을 돌려준다 (99 #37).

    ⚠ 🔴 **(8/22) 종전에는 `build_agent_step_sink()` 와 「별개다」를 적어 뒀다** — 그쪽은
    `mapping_probe` 전용이었고 **probe 의 `AgentStepRecord`** 를 받았다(필드가 같아도 별개
    타입이라 서로 못 썼다). import 축 개발 중단으로 **그 빌더는 사라졌고 이제 하나뿐**이다
    (99 #187). 🔴 **그래도 이름을 안 줄였다** — `counsel_` 접두가 «이 타입은 counsel 계약»
    이라는 사실을 계속 말한다. 종전 문면은 **왜 둘이었는지의 기록**이다.
    종전에는 counsel 조립부가 **어떤 빌더도 안 타서** `STORE_BACKEND=pg`를 켜도
    스텝이 **PG에 안 앉았다**(실측 8/11 · #37).

    ⚠ **생성만으로 접속하지 않는다** — DB 없는 환경에서 import·조립이 깨지면 안 된다.
    """
    settings = settings or get_db_settings()
    if settings.store_backend == _PG:
        return PgCounselAgentStepSink(sessionmaker=get_sessionmaker())
    #: ⚠ 여기 오는 값은 **`memory` 하나뿐**이다 — 미등록 값은 `DbSettings`가 **기동 시
    #: 거부**한다(99 #38). 팩토리에 판정을 복제하지 않는다.
    return InMemoryCounselAgentStepSink()

