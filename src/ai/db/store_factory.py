"""저장소 팩토리 — settings.store_backend로 InMemory↔PG를 고른다 (D-② 커밋⑤).

소유: 박진희 (db). database_url에 기본값이 있어 URL 유무로는 못 고르므로 명시 플래그로
고른다. 기본 "memory"라 CI·데모는 DB 없이 돌고, 실 DB 연동 시 .env에서 "pg"로 전환한다.
PG 저장소 생성은 엔진을 lazy로 만들 뿐 접속하지 않는다(session.py) — import 시 DB 불필요.
"""

from __future__ import annotations

from functools import lru_cache

from ai.agents.job_store import InMemoryJobStore, JobStore
from ai.db.repositories.agent_job import PgJobStore
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
from ai.db.repositories.probe_stores import (
    PgAgentStepSink,
    PgProfileStore,
    PgSpecResultStore,
)
from ai.db.repositories.run_store import (
    InMemoryRunStore,
    PgRunStore,
    RunStore,
)
from ai.db.session import get_sessionmaker
from ai.db.settings import DbSettings, get_db_settings
from ai.import_mapping.probe.stores import (
    AgentStepSink,
    InMemoryAgentStepSink,
    InMemoryProfileStore,
    InMemorySpecResultStore,
    ProfileStore,
    SpecResultStore,
)

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


def build_profile_store(settings: DbSettings | None = None) -> ProfileStore:
    """mapping_probe 입력(source_profile) 저장소 — PG 선택 시 워커·enqueue가 공유."""
    settings = settings or get_db_settings()
    if settings.store_backend == _PG:
        return PgProfileStore(sessionmaker=get_sessionmaker())
    return InMemoryProfileStore()


def build_spec_result_store(settings: DbSettings | None = None) -> SpecResultStore:
    """mapping_probe 산출(mapping_spec) 저장소 — result_ref로 참조되는 조사 결과."""
    settings = settings or get_db_settings()
    if settings.store_backend == _PG:
        return PgSpecResultStore(sessionmaker=get_sessionmaker())
    return InMemorySpecResultStore()


def build_agent_step_sink(settings: DbSettings | None = None) -> AgentStepSink:
    """mapping_probe 스텝(agent_step) 싱크 — 도구 호출 이력(마스킹 통과분)."""
    settings = settings or get_db_settings()
    if settings.store_backend == _PG:
        return PgAgentStepSink(sessionmaker=get_sessionmaker())
    return InMemoryAgentStepSink()
