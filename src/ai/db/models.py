"""ERD 41테이블 ORM — 06_erd.md 정본을 그대로 옮긴다.

소유: 공통 계약 (A+B 확인 완료 — 양자 목록은 02_ownership.md §4가 정본).
⚠ **여기 수를 다시 적지 않는다** — 「12곳」이 v5(`graphrag.py` 편입)로 13이 된 뒤에도
안 따라왔다(#02). 목록을 지키는 테스트가 없어 재진술이 안전하지 않다. ERD가 정본이므로
ERD에 없는 테이블은 만들지 않는다. A-1 승인으로 B 전용 문항·진단 8테이블을
편입했으며, 대조는 tests/ai/db/test_erd_model_parity.py 가 강제한다.

**실명·연락처 컬럼 절대 없음**(불변식 3) — alias(student_ref·guardian_ref 등)만.
`…_ref`는 백엔드 DB 원본을 가리키는 **논리 참조**(varchar, 물리 FK 아님).
enum성 컬럼(signal_type·lifecycle·status류)은 PG enum 대신 **varchar + 값 검증은 앱 계층**
(enum 확장 시 마이그레이션 부담 회피 — D-② 확정).

타입 매핑: uuid=Uuid · varchar=String · text=Text · jsonb=JSONB · int=Integer ·
numeric=Numeric · timestamptz=DateTime(timezone=True) · date=Date · boolean=Boolean.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.schema import UniqueConstraint

from ai.db.base import Base

#: timestamptz 공통 표기 — ERD의 timestamptz 는 전부 tz-aware.
_TZ = DateTime(timezone=True)


# ─────────────────────────── 묶음① 감지 계열 ───────────────────────────


class EngineRegistry(Base):
    __tablename__ = "engine_registry"

    engine_id: Mapped[str] = mapped_column(String, primary_key=True)
    kind: Mapped[str] = mapped_column(String)
    version: Mapped[str] = mapped_column(String)
    feature_schema: Mapped[dict[str, Any]] = mapped_column(JSONB)
    is_active: Mapped[bool] = mapped_column(Boolean)


class FeatureWeek(Base):
    __tablename__ = "feature_week"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "student_ref",
            "week_start",
            "feature_version",
            name="uq_feature_week_scope",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String)
    student_ref: Mapped[str] = mapped_column(String)
    week_start: Mapped[date] = mapped_column(Date)
    segment: Mapped[str] = mapped_column(String)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSONB)
    feature_version: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(_TZ)


class PassageTypeStat(Base):
    """지문×유형 조합의 실측 누적 — 기대치 입력 층의 원본(04 §1 · 13 §4-3-4).

    ⚠ **학생 식별자를 저장하지 않는다.** 이 행은 **조합의 속성**이지 학생 데이터가
    아니다(개인정보 최소 수집). 누가 풀었는지는 `FEATURE_WEEK`가 갖는다.

    비율이 아니라 **원시 카운트**를 든다 — 표본 수 판정(`expectation_min_n`)과 누적
    갱신이 같은 값에서 나와야 한다.
    """

    __tablename__ = "passage_type_stat"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "passage_ref", "type_tag", name="uq_passage_type_stat_scope"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String)
    passage_ref: Mapped[str] = mapped_column(String)  # 05 learning_events의 불투명 참조
    type_tag: Mapped[str] = mapped_column(String)  # contracts/taxonomy.py TypeTag
    responses: Mapped[int] = mapped_column(Integer)
    corrects: Mapped[int] = mapped_column(Integer)
    updated_at: Mapped[datetime] = mapped_column(_TZ)


class ExpectationIngest(Base):
    """기대치 통계에 반영 완료된 스냅숏 원장 — **이중 집계 방지**(결정 로그 33).

    같은 스냅숏이 다른 `Idempotency-Key`로 재전송돼도(백필·정정) 통계를 두 번 더하지
    않는다. 라우터 멱등은 같은 키에서만 막아 주므로 집계 층에 별도 원장이 필요하다 —
    Import의 `source_fingerprint`와 같은 결이다.

    재계산 근사를 쓰지 않는 이유: 기대치의 가치가 **여러 반·기수에 걸친 누적**이라
    최근 스냅숏만 반영하면 존재 이유가 깎이고, "조용히 틀어지는" 계통 오류가 된다.

    ⚠ **학생 식별자를 저장하지 않는다** — 스냅숏 단위 사실만 남긴다.
    """

    __tablename__ = "expectation_ingest"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "snapshot_hash", name="uq_expectation_ingest_scope"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String)
    snapshot_hash: Mapped[str] = mapped_column(String)
    events_applied: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(_TZ)


class Baseline(Base):
    __tablename__ = "baseline"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String)
    student_ref: Mapped[str] = mapped_column(String)
    segment: Mapped[str] = mapped_column(String)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSONB)
    window_weeks: Mapped[int] = mapped_column(Integer)
    version: Mapped[str] = mapped_column(String)
    updated_at: Mapped[datetime] = mapped_column(_TZ)


class ThresholdConfig(Base):
    __tablename__ = "threshold_config"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String)
    rule_id: Mapped[str] = mapped_column(String)
    params: Mapped[dict[str, Any]] = mapped_column(JSONB)
    version: Mapped[int] = mapped_column(Integer)
    source: Mapped[str] = mapped_column(String)
    approved_by: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(_TZ)


class Signal(Base):
    __tablename__ = "signal"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    run_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("ai_run.execution_id"))
    tenant_id: Mapped[str] = mapped_column(String)
    student_ref: Mapped[str] = mapped_column(String)
    rule_id: Mapped[str] = mapped_column(String)
    signal_type: Mapped[str] = mapped_column(String)
    display_label: Mapped[str] = mapped_column(String)
    lifecycle: Mapped[str] = mapped_column(String)
    score: Mapped[Decimal] = mapped_column(Numeric)
    rank: Mapped[int] = mapped_column(Integer)

    #: ━━ 비교값 (99 #59·#60 · 안 D) — 2026-08-14 준영님 양자 승인 ━━
    #:
    #: 🔴 **응답에는 나가는데 원장에 없어서**, 강사가 이의를 제기해도 *"그때 평소값이
    #: 얼마였나"* 를 우리 원장으로 답할 수 없었다. 재현이 반쪽이었다.
    #:
    #: 🔴 **전부 nullable 이다** — 규칙마다 채울 수 있는 것이 다르고 **값의 부재가 정상**이다:
    #:   `return_care` 는 넷 다 `None`(사건형 — 잴 지표도 비교 대상도 없다) ·
    #:   `submit_drop`·`type_bias` 는 `baseline` 이 `None`(**평소가 아니라 임계값**과
    #:   비교한다 — 임계를 baseline 에 적으면 *"평소 대비"* 로 읽혀 거짓이 된다).
    #: ⚠ **`observed` 의 단위가 규칙마다 다르다** — `submit_drop` 은 «주 수»다.
    #:   정본 표는 `docs/part_a/14_evidence_fields.md` §3-2′ (여기 복제하지 않는다).
    #: ⚠ **`evidence_item` 은 같이 안 열었다** — 그 테이블은 **쓰는 코드가 0건**이다
    #:   (아래 `EvidenceItem` docstring).
    metric: Mapped[str | None] = mapped_column(String, nullable=True)
    observed: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    baseline: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    sample_size: Mapped[int | None] = mapped_column(Integer, nullable=True)

    created_at: Mapped[datetime] = mapped_column(_TZ)


class SignalBrief(Base):
    __tablename__ = "signal_brief"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String)
    signal_ref: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("signal.id"))
    brief_text: Mapped[str] = mapped_column(Text)
    gate_passed: Mapped[bool] = mapped_column(Boolean)
    fallback_used: Mapped[bool] = mapped_column(Boolean)
    llm_call_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("llm_call.id"), nullable=True
    )


class RuleFeedback(Base):
    __tablename__ = "rule_feedback"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String)
    rule_id: Mapped[str] = mapped_column(String)
    alert_ref: Mapped[str] = mapped_column(String)
    verdict: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(_TZ)


class EvidenceItem(Base):
    """🔴 **쓰는 코드가 0건이다** (2026-08-14 · A·B 각자 전수 확인).

    ⚠ **«미사용» 이 아니다** — 그 표현은 *"지금은 안 쓰지만 배선돼 있다"* 로 읽힌다.
    실제로는 **INSERT 경로 자체가 없다**: 이 클래스를 import 하는 코드 0건, 이 테이블에
    쓰는 코드 0건(정의 + `0001` 초기 마이그레이션이 전부).

    ⚠ `detection/evidence.py` 의 `EvidenceItem` 은 **동명이인**이다 — 응답 계약의 값 객체
    (`role`·`occurred_on` 을 갖는다)이지 이 ORM 행이 아니다.
    ⚠ B 의 문항 근거는 `problem_item.snapshot`(JSONB) 안에 앵커로 들어간다
    (`part_b/09` §2-20 「스냅숏 정본 + 파생 투영」). 그래서 SQL 로 질의할 수 없다 —
    *"이 조항을 근거로 쓴 문항 전부"* 같은 질의가 필요해지면 이 테이블을 다시 볼 자리다.

    ⇒ 응답 evidence 의 `role`·`observed`·`sample_size`·`occurred_on` 을 **여기 안 넣었다.**
    **쓰는 코드가 0건인 테이블에 컬럼만 늘리는 것은 값이 없다**(99 #60 · 준영님 판단 일치).
    여는 조건: 이 테이블에 **실제로 적재하는 경로**가 생길 때.
    """

    __tablename__ = "evidence_item"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String)
    owner_kind: Mapped[str] = mapped_column(String)
    owner_id: Mapped[uuid.UUID] = mapped_column(Uuid)  # 다형 소유 — 물리 FK 없음
    source_table: Mapped[str] = mapped_column(String)
    record_id: Mapped[str] = mapped_column(String)
    summary: Mapped[str] = mapped_column(String)


# ────────────────────────── 묶음② 상담·소통 계열 ──────────────────────────


class Draft(Base):
    __tablename__ = "draft"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    run_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("ai_run.execution_id"))
    # 상담팩 산출 시(v2)에만 연결 — 기존 경로는 null.
    agent_run_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    tenant_id: Mapped[str] = mapped_column(String)
    kind: Mapped[str] = mapped_column(String)
    student_ref: Mapped[str] = mapped_column(String)
    guardian_ref: Mapped[str] = mapped_column(String)
    label_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String)
    fail_reason: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(_TZ)
    #: 🔴 **게이트를 통과한 초안 본문**(㉻ · 지시서 73 §2-2 · 준영님 확정 2026-08-12).
    #:
    #: ⚠ **없던 것이 의도가 아니라 결손이었다**(`part_b/09` §1763). 본문이 테이블 밖에만
    #: 있어서 늦게 성공한 잡의 GET이 `result=None`이었다 — 워커 프로세스가 죽으면
    #: `InMemoryDraftResultStore`와 함께 사라졌다.
    #:
    #: 🔴 **`NOT NULL`이다.** 0010 시점에 **검사한 배포 대상**에서 무손실로 세울 수 있다.
    #: 실측 둘: ⓐ **0010 이전 프로덕션 저장소 호출 경로 0건**(`Draft` ORM 참조가 정의 한
    #: 곳뿐 · 2026-08-12 전수) ⓑ **검사한 실 PG의 `draft` 0행**.
    #: ⚠ **「어떤 환경에도 행이 없다」가 아니다** — 애플리케이션 경로가 0건인 것은
    #: 수동 SQL·과거 실험 DB까지 배제하지 못한다. 행이 있는 DB에서는 이 마이그레이션이
    #: **실패해 배포를 멈춘다** — 거짓 backfill보다 그쪽이 안전하다(적용 전
    #: `SELECT count(*) FROM draft;` 확인).
    #: ⚠ 빈 문자열 backfill·server default를 두지 않았다 — **빈 본문은 「초안이 있다」는
    #: 거짓**이고, 그러면 게이트 거부와 정상 초안이 같은 값을 갖는다.
    #:
    #: ⚠ **`DRAFT_BLOCK.content`와 다른 축이다** — 그쪽은 블록 분해(§05 §4)이고 여기는
    #: 최초 본문 전문이다. refine 이력은 계속 `DRAFT_REVISION`이 축이다.
    content: Mapped[str] = mapped_column(Text)


class DraftBlock(Base):
    __tablename__ = "draft_block"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    draft_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("draft.id"))
    seq: Mapped[int] = mapped_column(Integer)
    block_type: Mapped[str] = mapped_column(String)
    content: Mapped[str] = mapped_column(Text)  # 비었으면 재시도 소진 섹션
    regen_count: Mapped[int] = mapped_column(Integer)  # ≤3


class DraftRevision(Base):
    __tablename__ = "draft_revision"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    draft_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("draft.id"))
    turn_no: Mapped[int] = mapped_column(Integer)  # 상한 없음 — 월 할당이 자연 상한
    scope: Mapped[str] = mapped_column(String)
    block_seq: Mapped[int | None] = mapped_column(Integer, nullable=True)  # scope=block일 때
    instruction: Mapped[str] = mapped_column(Text)
    preset: Mapped[str | None] = mapped_column(String, nullable=True)
    result_blocks: Mapped[dict[str, Any]] = mapped_column(JSONB)  # 롤백용 스냅숏
    gates_passed: Mapped[bool] = mapped_column(Boolean)  # 매 턴 게이트 재통과
    blocked_reason: Mapped[str | None] = mapped_column(String, nullable=True)
    llm_call_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("llm_call.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(_TZ)


class GateResult(Base):
    __tablename__ = "gate_result"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String)
    owner_kind: Mapped[str] = mapped_column(String)  # draft|import_job|problem_set
    owner_id: Mapped[uuid.UUID] = mapped_column(Uuid)  # 다형 소유 — 물리 FK 없음
    gate_name: Mapped[str] = mapped_column(String)
    seq: Mapped[int] = mapped_column(Integer)
    passed: Mapped[bool] = mapped_column(Boolean)
    reason: Mapped[str | None] = mapped_column(String, nullable=True)


class CounselPackResult(Base):
    """상담 팩 결과 — 🔴 **`snapshot`이 정본이고 위 넷은 조회용 파생 투영이다.**

    (99 ㉕ · A 본문 설계 2026-08-08 §3 · 조건 ⓒ) `AGENT_RUN.result_ref`(`pack://{id}`)가
    가리키던 대상이 여태 없었다 — `AGENT_RUN`은 포인터만 들고 `DRAFT`는 학생별 1행이라
    **팩 단위 요약이 앉을 자리가 없었다.** `AGENT_RUN`의 jsonb로 흡수하지 않은 이유는
    ⓐ 공통 원장이 capability별로 갈리고 ⓑ 그러면 `pack://` ref의 대상이 자기 자신이 되며
    ⓒ 대상 테이블 하나면 끝나서다.

    🔴 **PK는 `AGENT_RUN.id`가 아니다** — `worker.py`가 결과 레코드에 새 UUID를 만든다
    (`AGENT_RUN.id`는 `WorkerJob.job_id`). ⇒ `result_ref = 'pack://' || COUNSEL_PACK_RESULT.id`.
    `AGENT_RUN`과 **FK로 잇지 않는다** — `result_ref`는 capability마다 대상이 다른 불투명
    참조라(`06_erd.md`) 다형 FK가 된다.

    ⚠ **투영 넷의 이유가 같지 않다** — `tenant_id`는 격리 술어, `created_at`은 보존기간·정리
    배치 축, `plan_outcome`은 *"강조점 0건, 왜"* 집계 축이고, **`class_ref`만 조회가 아니라
    파기 술어**다(반 단위 삭제에서 컬럼으로 낼 수 있는 유일한 축 — `student_ref`는 스냅숏
    안이다). 유도는 저장소의 한 함수가 한다(조건 ⓐ).

    ⚠ **`results`·`emphasis_points`는 투영하지 않는다** — 학생별 조회 축은 `DRAFT`가 이미
    갖는다(학생당 1행). 투영하면 같은 사실이 두 테이블에 앉고 그 둘이 갈린다.
    ✅ **쓰기 경로가 섰다**(PR-κ · 2026-08-09) — 조건 ⓓ(`emphasis_points`의 출력측 마스킹)가
    PR-ι에서 **포착 시점 마스킹**으로 닫히면서 `PgPackResultStore`가 붙었다.
    ⚠ **잔여 위험은 99 #28이다** — `redact()`가 조사·호칭 같은 문맥 신호로 인명을 확정하는데
    강조점은 짧은 명사구라 **「잡히는 형태를 늘린 것」이지 「실명이 못 들어온다」가 아니다.**
    ⚠ **(8/8~8/9 이력) 이 자리가 「자리만 만든다」였다** — 테이블만 서고 저장소가 없던 구간의
    기록이다. 지우기만 하면 다음 사람이 *"원래 있었나"* 를 다시 판단한다(99 ㊩).
    """

    __tablename__ = "counsel_pack_result"
    __table_args__ = (
        # 보존기간·정리 배치가 테넌트별로 훑는 축. `class_ref` 인덱스는 파기 경로가 실제로
        # 생길 때 — 지금 넣으면 쓰는 곳 없는 인덱스다.
        Index("ix_counsel_pack_result_tenant_created", "tenant_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String)
    class_ref: Mapped[str] = mapped_column(String)
    plan_outcome: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(_TZ)
    #: 🔴 정본 — CounselPackResultRecord 전문. 위 넷은 여기서 유도한 파생이다.
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB)


class CounselContextBundle(Base):
    """상담 워커의 **입력 묶음 정본** — `context://{id}`가 가리키는 대상 (㉻ · 지시서 73 §2-1).

    🔴 **읽기 모델이 아니다.** `COUNSEL_DRAFT_VIEW`(`_CachedView`·`_DraftState`)는 조회
    캐시고, 이 테이블은 **실행 전에 만들어져 워커가 소비하는 입력**이다 — 생애주기가 다르다.
    한 테이블에 합치면 조회 캐시를 비우는 정리 배치가 **재개할 잡의 입력을 지운다.**

    🔴 **`AI_RUN`과 FK로 잇지 않는다.** 이 행은 `begin_run()`보다 **먼저** 생긴다
    (POST → enqueue → 워커 lease → `begin_run`). FK를 걸면 enqueue가 실행 원장을
    선행 요구하게 되고, 그건 *"실행이 없는데 실행 기록을 만든다"* 는 99 #46의 반대편이다.

    ⚠ **`contexts`가 무손실 정본이다** — `ContextBundleRecord.contexts`(학생 alias →
    `DraftContext`)를 그대로 담는다. 여기서 파생해 다시 구성할 수 없는 **별도 손사본을
    만들지 않는다**(선례: `PROBLEM_ITEM`이 컬럼 재조립으로 12건을 깎았다).

    ⚠ **`content_hash`는 계약값이다** — 저장 시 재계산해 대체하지 않는다. 워커가 재개할 때
    이 값을 state의 `context_hash`와 대조하는 것이 불변식 ④인데, 저장소가 자기 방식으로
    다시 계산하면 **대조가 자기 자신과의 비교**가 되어 손상을 못 잡는다.
    """

    __tablename__ = "counsel_context_bundle"
    __table_args__ = (
        #: 보존기간·정리 배치가 테넌트별로 훑는 축 — `counsel_pack_result`와 같은 이유다.
        #: ⚠ 조회는 `id + tenant_id`(PK 포함)라 별도 인덱스를 더 만들지 않는다.
        Index("ix_counsel_context_bundle_tenant_created", "tenant_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String)
    class_ref: Mapped[str] = mapped_column(String)
    #: 🔴 정본 — `{student_ref: DraftContext}` 전문.
    contexts: Mapped[dict[str, Any]] = mapped_column(JSONB)
    content_hash: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(_TZ)


class CounselDraftView(Base):
    """상담 읽기 모델 — 두 독립 캐시의 스냅숏이 정본이다.

    `view_snapshot`은 `_CachedView` 전문이고 `draft_snapshot`은 `_DraftState` 전문이다.
    두 캐시는 서로 다른 시점에 채워지고 독립적으로 축출되므로 둘 다 nullable이어야 한다.
    그래야 "GET은 404인데 refine은 200"인 비대칭도 한 행으로 표현할 수 있다. 다음 사람이
    컬럼 하나로 합치면 그 상태가 사라지므로 두 정본을 의도적으로 분리한다.

    🔴 스냅숏이 정본이고 `tenant_id`·`job_id`·`status`·`execution_id` 넷은 조회용 파생이다.
    유도는 `db/counsel_draft_view.py::counsel_draft_view_projection()` 한 함수만 담당한다.
    `updated_at`은 두 캐시에 없는 값을 지어내지 않고 향후 저장소의 쓰기 시각으로 남기는
    정리 배치 축이다. 이 PR은 자리만 만들며 `_view_cache`·`_drafts` 배선은 A의 축이다.

    `execution_id`는 `template_only`·근거 0건처럼 AI_RUN 행이 없는 경우 null이 정직하다.
    `job_id`는 `AGENT_RUN`과 물리 FK로 잇지 않는 varchar 논리 참조다.
    """

    __tablename__ = "counsel_draft_view"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "job_id", name="uq_counsel_draft_view_job"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String)
    job_id: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String)
    execution_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(_TZ)
    view_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    draft_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)


# ───────────────── 진단·문제생성 계열 ([PART_B]) ─────────────────


class WeaknessMap(Base):
    __tablename__ = "weakness_map"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "student_ref",
            "graph_version",
            "week_start",
            name="uq_weakness_map_scope",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    run_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("ai_run.execution_id"))
    tenant_id: Mapped[str] = mapped_column(String)
    student_ref: Mapped[str] = mapped_column(String)
    week_start: Mapped[date] = mapped_column(Date)
    graph_version: Mapped[str] = mapped_column(String)
    taxonomy_version: Mapped[str] = mapped_column(String)
    config_version: Mapped[str] = mapped_column(String)
    snapshot_hash: Mapped[str] = mapped_column(String)
    cells: Mapped[dict[str, Any]] = mapped_column(JSONB)
    nodes: Mapped[dict[str, Any]] = mapped_column(JSONB)
    propagated: Mapped[dict[str, Any]] = mapped_column(JSONB)
    overall_low: Mapped[bool] = mapped_column(Boolean)
    computed_at: Mapped[datetime] = mapped_column(_TZ)


class Passage(Base):
    __tablename__ = "passage"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String)
    source_kind: Mapped[str] = mapped_column(String)
    source_ref: Mapped[str | None] = mapped_column(String, nullable=True)
    license_ref: Mapped[str | None] = mapped_column(String, nullable=True)
    area_tag: Mapped[str] = mapped_column(String)
    topic: Mapped[str] = mapped_column(String)
    word_count: Mapped[int] = mapped_column(Integer)
    complexity: Mapped[dict[str, Any]] = mapped_column(JSONB)
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    llm_call_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("llm_call.id"), nullable=True
    )


class ProblemSet(Base):
    __tablename__ = "problem_set"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    run_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("ai_run.execution_id"))
    tenant_id: Mapped[str] = mapped_column(String)
    target_kind: Mapped[str] = mapped_column(String)
    target_ref: Mapped[str] = mapped_column(String)
    target_source: Mapped[str] = mapped_column(String)
    weakness_map_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("weakness_map.id"), nullable=True
    )
    request: Mapped[dict[str, Any]] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String)
    summary: Mapped[str | None] = mapped_column(String, nullable=True)
    stop_reason: Mapped[str | None] = mapped_column(String, nullable=True)
    diagnostic_purpose: Mapped[bool] = mapped_column(Boolean)
    created_at: Mapped[datetime] = mapped_column(_TZ)


class ProblemGenerationRequest(Base):
    """PG 워커 입력 정본 — 프로세스 재시작 뒤 `payload_ref`를 해소한다."""

    __tablename__ = "problem_generation_request"
    __table_args__ = (
        Index("ix_problem_generation_request_tenant_created", "tenant_id", "created_at"),
    )

    ref: Mapped[str] = mapped_column(String, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(_TZ, server_default=func.now())


class ProblemGenerationResult(Base):
    """PG 워커 결과 정본 — 다른 API 프로세스가 `result_ref`를 역참조한다."""

    __tablename__ = "problem_generation_result"
    __table_args__ = (
        Index("ix_problem_generation_result_tenant_created", "tenant_id", "created_at"),
    )

    ref: Mapped[str] = mapped_column(String, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String)
    job_id: Mapped[uuid.UUID] = mapped_column(Uuid, unique=True)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(_TZ, server_default=func.now())


class ProblemItem(Base):
    """슬롯 최종본 — 🔴 **`snapshot`이 정본이고 아래 컬럼은 조회용 파생 투영이다.**

    (09 §2-20 결정 ②·③ · A 판정 2026-08-08 조건 ⓒ) 계약 값 객체
    `StoredProblemItem`이 무손실로 왕복해야 하는데(`application/ports.py`
    `ProblemItemStore.save/get`), 컬럼 전개로는 계약이 늘 때마다 **양자 파일이 열린다** —
    `contracts/problem_generation.py`는 B 소유인데 이 파일은 양자라 B가 자기 계약을 늘릴
    때마다 A를 기다린다. 선례는 `ItemCandidate`(`snapshot` + `gate_summary` 정본 +
    `difficulty_est` 파생).

    🔴 **투영을 직접 쓰지 마라 — 읽을 값은 `snapshot`에서 온다.** 투영은 목록·정렬·필터가
    JSONB 연산 없이 돌기 위한 사본이고, 유도는 `db/repositories/problem_store.py`의
    `problem_item_projection()` **한 함수**가 한다(조건 ⓐ). 갈리면
    `tests/ai/db/test_problem_store_projection.py`가 red다(조건 ⓑ · 99 #04).

    ⚠ **`current_revision_no`는 파생이 아니다** — 스냅숏에 대응 값이 없는 **저장소 소유
    상태**(낙관적 잠금 축)라 최초 저장이 0을 넣고 `item_revision`이 올린다.
    ⚠ **`drop_reason`도 파생이 아니다** — `StoredProblemItem`은 `dropped`를 저장하지 않으므로
    (`domain/models.py` `validate_body`) 이 저장소 경로에서는 **항상 null**이다.
    `result.failure_reason`을 여기 실으면 폐기 사유 컬럼 오버로딩이 된다(§2-20 #12).
    """

    __tablename__ = "problem_item"
    __table_args__ = (
        # 🔴 `ProblemItemStore.get(set_id, slot_index)`가 Protocol 시그니처로 요구하는 조회 키.
        # `tenant_id`가 빠진 것은 누락이 아니다 — `set_id`가 `problem_set.tenant_id`에
        # 종속이라 둘로 전역 유일하다(A 판정 §1). `item_candidate`가 넷인 이유는 거긴
        # `tenant_id`가 직접 컬럼이라 복합 인덱스가 그 컬럼을 태우는 편이 나아서다.
        UniqueConstraint("set_id", "slot_index", name="uq_problem_item_slot"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    set_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("problem_set.id"))
    slot_index: Mapped[int] = mapped_column(Integer)
    #: 🔴 정본 — StoredProblemItem 전문. 아래 투영은 전부 여기서 유도한다.
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB)
    passage_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("passage.id"), nullable=True
    )
    # ── 아래부터 파생 투영 — 본문 없는 슬롯(`item=None`)에서는 전부 null이다(결정 ③ ⓒ).
    area_tag: Mapped[str | None] = mapped_column(String, nullable=True)
    type_tag: Mapped[str | None] = mapped_column(String, nullable=True)
    item_format: Mapped[str | None] = mapped_column(String, nullable=True)
    skill_node_id: Mapped[str | None] = mapped_column(String, nullable=True)
    stem: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: ⚠ jsonb 배열이다 — `GeneratedItem.choices`가 선지 5개 목록이라 dict로 감싸지 않는다.
    choices: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB, nullable=True)
    answer: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    difficulty_est: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    difficulty_fit: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    #: 저장 시점에 대응 값이 없다(§2-20 #5) — 보정 버전이 붙기 전에는 null이다.
    difficulty_calib_ver: Mapped[str | None] = mapped_column(String, nullable=True)
    review_badge: Mapped[bool] = mapped_column(Boolean)
    current_revision_no: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String)
    drop_reason: Mapped[str | None] = mapped_column(String, nullable=True)


class VerificationResult(Base):
    __tablename__ = "verification_result"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    item_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("problem_item.id"))
    stage: Mapped[str] = mapped_column(String)
    passed: Mapped[bool] = mapped_column(Boolean)
    detail: Mapped[dict[str, Any]] = mapped_column(JSONB)
    llm_call_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("llm_call.id"), nullable=True
    )
    attempt_no: Mapped[int] = mapped_column(Integer)


class ItemRevision(Base):
    __tablename__ = "item_revision"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    item_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("problem_item.id"))
    turn_no: Mapped[int] = mapped_column(Integer)
    revision_kind: Mapped[str] = mapped_column(String)
    instruction: Mapped[str | None] = mapped_column(Text, nullable=True)
    result_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    diff: Mapped[dict[str, Any]] = mapped_column(JSONB)
    verifications_passed: Mapped[bool] = mapped_column(Boolean)
    blocked_reason: Mapped[str | None] = mapped_column(String, nullable=True)
    llm_call_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("llm_call.id"), nullable=True
    )


class DifficultyCalib(Base):
    __tablename__ = "difficulty_calib"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String)
    params: Mapped[dict[str, Any]] = mapped_column(JSONB)
    version: Mapped[int] = mapped_column(Integer)
    source: Mapped[str] = mapped_column(String)
    approved_by_ref: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(_TZ)


class ItemCandidate(Base):
    __tablename__ = "item_candidate"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "set_id",
            "slot_index",
            "attempt_no",
            name="uq_item_candidate_scope",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    set_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("problem_set.id"))
    tenant_id: Mapped[str] = mapped_column(String)
    slot_index: Mapped[int] = mapped_column(Integer)
    attempt_no: Mapped[int] = mapped_column(Integer)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB)
    gate_summary: Mapped[dict[str, Any]] = mapped_column(JSONB)
    difficulty_est: Mapped[Decimal] = mapped_column(Numeric)
    created_at: Mapped[datetime] = mapped_column(_TZ)


# ────────────────────────── 묶음② 상담·소통 계열 계속 ──────────────────────────


class InquiryClass(Base):
    """ⓑ 문의 분류 결과 — **평가셋 테이블**이다(`part_a/03` §C7 · `08` §7).

    3축 컬럼은 **AI 예측 고정**이며 절대 덮어쓰지 않는다. 강사 정정은 `corrected_*`에
    별도 보관한다 — 예측이 소실되면 (입력·예측·정답) 3요소가 깨져 평가셋 목적이
    사라진다(B 승인 조건 1).

    `corrected_by_teacher`는 **컬럼이 아니라 파생값**이다(조건 2)::

        corrected_by_teacher := (corrected_topic IS NOT NULL
                              OR corrected_sentiment IS NOT NULL
                              OR corrected_urgency IS NOT NULL)

    판정 해석::

        reviewed_at IS NULL                            → 평가셋 미편입(분모 제외)
        reviewed_at NOT NULL, corrected_topic IS NULL  → topic 축 AI 정답
        corrected_topic IS NOT NULL                    → topic 축 AI 오답(정답=corrected_topic)

    🔴 **기록 규약 2건 — 적재 코드가 반드시 지킨다(조건 4).**

    ① **예측과 같은 값으로 "정정"된 경우 `corrected_*`는 NULL을 유지한다.** 강사가
       드롭다운을 열어 같은 값을 다시 골라도 값을 쓰지 않는다 — 쓰면 그게 오답으로
       집계돼 재분류율이 부풀려지고, `reviewed_at`으로 분리한 의미가 무너진다.
    ② **재검토 시 `reviewed_at`은 마지막 검토 시각으로 갱신하고 이력은 남기지 않는다.**
       검토 이력이 필요해지면 별도 테이블 안건으로 연다.

    ⚠ **폴백 건(`classified=False`)은 적재하지 않는다**(조건 4). 판정이 없는 건에 enum
    값을 채우면 불변식 2 위반이고 평가셋이 오염된다. **폴백률은 이 테이블에서 세지
    말 것** — 관측은 구조화 로그로 하고, `runtime/metrics` 이벤트는 모듈 자체가 아직
    없어 별도 양자 승인 대상이다(99 등재).

    값 어휘 정본은 `contracts/counsel.py`의 `InquiryTopic`·`InquirySentiment`·
    `InquiryUrgency`다. DB enum 타입을 만들지 않고 String + 주석으로 두는 것은 기존
    `topic`·`urgency`와의 대칭이다(조건 3).
    """

    __tablename__ = "inquiry_class"
    __table_args__ = (
        CheckConstraint(
            "(corrected_topic IS NULL AND corrected_sentiment IS NULL "
            "AND corrected_urgency IS NULL) OR reviewed_at IS NOT NULL",
            name="corrected_requires_review",
        ),
        # 🔴 자연키 — 문의 1건 = 분류 1건(P2-c). **캐시와 검토 보호의 전제**다:
        # ① 같은 `(tenant_id, inquiry_ref)` 재호출은 저장분을 돌려주고 LLM을 안 부른다
        # ② `reviewed_at`이 선 행을 나중 예측이 덮지 않는다(평가셋 예측·정답 쌍 보존)
        # 새 UUID를 응답에 노출하지 않고 BE가 이미 아는 값을 참조 키로 쓴다.
        UniqueConstraint("tenant_id", "inquiry_ref", name="uq_inquiry_class_scope"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String)
    inquiry_ref: Mapped[str] = mapped_column(String)  # 백엔드 문의 ID(논리)

    # ── AI 예측 (고정 — 덮어쓰기 금지) ──────────────────────
    topic: Mapped[str] = mapped_column(String)  # grade|schedule|counsel_request|etc
    sentiment: Mapped[str] = mapped_column(String)  # normal|complaint
    urgency: Mapped[str] = mapped_column(String)  # immediate|normal
    confidence_topic: Mapped[Decimal] = mapped_column(Numeric)
    confidence_sentiment: Mapped[Decimal] = mapped_column(Numeric)
    confidence_urgency: Mapped[Decimal] = mapped_column(Numeric)

    # ── 강사 정정 (NULL = 그 축은 안 바꿈) ──────────────────
    corrected_topic: Mapped[str | None] = mapped_column(String, nullable=True)
    corrected_sentiment: Mapped[str | None] = mapped_column(String, nullable=True)
    corrected_urgency: Mapped[str | None] = mapped_column(String, nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(_TZ, nullable=True)
    """강사가 이 건을 검토한 시각. NULL이면 **평가셋 분모에서 제외**한다 — 정정 안 함
    (AI 정답)과 미검토를 구분하지 못하면 정확도가 과대평가된다. 시각으로 두는 이유는
    검토 지연(분류 시점 → 검토 시점) 자체가 파일럿 관측치이기 때문이다."""

    llm_call_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("llm_call.id"), nullable=True
    )


class LabelSuggestion(Base):
    __tablename__ = "label_suggestion"
    #: 🔴 **v1 미사용 (2026-08-22 · 99 #190)** — 라벨 제안은 `POST /v1/labels/suggest` 가
    #:   **동기로 계산해 바로 돌려주고 아무것도 저장하지 않는다**(04 §3.7 저장 정책).
    #: ⚠ **지우지 마라** — 스키마를 지우려면 마이그레이션이고, **안 쓰면 비용이 0**이다.
    #:   (import 축에서 배운 그대로다 — 지우는 것과 안 쓰는 것은 다르다 · 99 #187)
    #: 🔴 **왜 안 쓰나:** 이 테이블은 「주간 배치 + 영속」 시절의 것이다.
    #:   ⓐ `created_at` 이 없어 **축출할 근거가 없고**
    #:   ⓑ `guardian_ref` 를 가져 「새로 쌓이는 개인 데이터 0」 이라는 정책과 **갈린다.**
    #: 🔴 **언제 지울 수 있나:** 배포 DB 에 행이 0 이고 스키마를 바꾸는 회차.
    #:   ⚠ 그 조건은 **검사로 못 잰다**(저장소 밖).   · 소유: **A(박진희)**

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String)
    guardian_ref: Mapped[str] = mapped_column(String)
    axis: Mapped[str] = mapped_column(String)  # comm|interest|sensitivity|frequency
    value: Mapped[str] = mapped_column(String)  # 축별 열거형만
    evidence_quotes: Mapped[dict[str, Any]] = mapped_column(JSONB)  # 마스킹 인용문
    confidence: Mapped[Decimal] = mapped_column(Numeric)
    status: Mapped[str] = mapped_column(String)


class StyleProfile(Base):
    __tablename__ = "style_profile"

    tenant_id: Mapped[str] = mapped_column(String, primary_key=True)
    features: Mapped[dict[str, Any]] = mapped_column(JSONB)  # 선호 어휘·톤(수정 diff 누적)
    updated_at: Mapped[datetime] = mapped_column(_TZ)


# ─────────────────── 묶음③ import·에이전트·운영 계열 ───────────────────


class SourceProfile(Base):
    __tablename__ = "source_profile"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String)
    file_hash: Mapped[str] = mapped_column(String)  # 멱등키
    filename: Mapped[str] = mapped_column(String)
    sheets: Mapped[dict[str, Any]] = mapped_column(JSONB)  # 시트·헤더·타입·양식 시그니처
    created_at: Mapped[datetime] = mapped_column(_TZ)


class MappingSpec(Base):
    __tablename__ = "mapping_spec"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String)
    source_profile_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("source_profile.id")
    )
    version: Mapped[int] = mapped_column(Integer)
    spec: Mapped[dict[str, Any]] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String)
    # 1-shot 추론 호출 · 조사 에이전트 산출 — 재사용/미사용 시 null. ERD FK 마커 없음.
    inferred_by_call: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    probe_agent_run: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(_TZ, nullable=True)


class ImportJob(Base):
    __tablename__ = "import_job"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String)
    spec_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("mapping_spec.id"))
    file_hash: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String)
    row_total: Mapped[int] = mapped_column(Integer)
    row_ok: Mapped[int] = mapped_column(Integer)
    row_fail: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(_TZ)


class ImportRowError(Base):
    __tablename__ = "import_row_error"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    job_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("import_job.id"))
    row_no: Mapped[int] = mapped_column(Integer)
    column_name: Mapped[str] = mapped_column(String)  # 엑셀 컬럼명 — 실명 아님
    reason: Mapped[str] = mapped_column(String)


class TagSuggestion(Base):
    __tablename__ = "tag_suggestion"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String)
    source_text_hash: Mapped[str] = mapped_column(String)  # 과제명 해시 = 캐시 키
    source_kind: Mapped[str] = mapped_column(String)
    area_tag: Mapped[str] = mapped_column(String)  # 수능 6영역 (A+B 공용 어휘)
    type_tag: Mapped[str] = mapped_column(String)
    confidence: Mapped[Decimal] = mapped_column(Numeric)
    status: Mapped[str] = mapped_column(String)
    llm_call_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("llm_call.id"), nullable=True
    )


class AgentRun(Base):
    __tablename__ = "agent_run"
    __table_args__ = (
        CheckConstraint(
            "agent_kind IN ('counsel_pack', 'mapping_probe', 'problem_generation')",
            name="worker_kind",
        ),
        CheckConstraint(
            "status IN "
            "('queued', 'leased', 'running', 'paused', 'succeeded', 'failed', 'cancelled')",
            name="phase",
        ),
        CheckConstraint(
            "priority_class IN ('batch', 'standard', 'interactive')",
            name="priority",
        ),
        CheckConstraint(
            "(agent_kind = 'counsel_pack' AND operation = 'counsel_pack.generate') OR "
            "(agent_kind = 'mapping_probe' AND operation = 'mapping_probe.resolve') OR "
            "(agent_kind = 'problem_generation' AND operation IN "
            "('problem_set.generate', 'problem_item.refine', 'problem_item.reverify'))",
            name="operation_route",
        ),
        CheckConstraint(
            "payload_hash ~ '^sha256:[0-9a-f]{64}$'",
            name="payload_hash",
        ),
        CheckConstraint(
            "dispatch_attempt >= 0 "
            "AND dispatch_attempt = lease_generation "
            "AND recovery_count >= 0 "
            "AND max_recovery_attempts >= 1 "
            "AND recovery_count <= max_recovery_attempts",
            name="attempts",
        ),
        CheckConstraint(
            "(status IN ('leased', 'running') "
            "AND lease_owner IS NOT NULL "
            "AND lease_acquired_at IS NOT NULL "
            "AND lease_expires_at IS NOT NULL "
            "AND lease_expires_at > lease_acquired_at) "
            "OR (status NOT IN ('leased', 'running') "
            "AND lease_owner IS NULL "
            "AND lease_acquired_at IS NULL "
            "AND lease_expires_at IS NULL)",
            name="lease",
        ),
        CheckConstraint(
            "(status IN ('succeeded', 'failed', 'cancelled') AND finished_at IS NOT NULL) "
            "OR (status NOT IN ('succeeded', 'failed', 'cancelled') AND finished_at IS NULL)",
            name="finished",
        ),
        CheckConstraint(
            "(status = 'succeeded' AND result_ref IS NOT NULL) OR status <> 'succeeded'",
            name="success_result",
        ),
        CheckConstraint(
            "status IN ('succeeded', 'failed', 'cancelled') OR result_ref IS NULL",
            name="result_phase",
        ),
        CheckConstraint(
            "(status = 'failed' AND error_code IS NOT NULL) OR status <> 'failed'",
            name="failure_error",
        ),
        CheckConstraint(
            "status IN ('failed', 'cancelled') OR error_code IS NULL",
            name="error_phase",
        ),
        CheckConstraint(
            "(status IN ('running', 'paused') "
            "AND started_at IS NOT NULL "
            "AND checkpoint_ref IS NOT NULL) "
            "OR status NOT IN ('running', 'paused')",
            name="resume_fields",
        ),
        CheckConstraint(
            "(status <> 'succeeded' OR started_at IS NOT NULL) "
            "AND (status <> 'queued' OR dispatch_attempt <> 0 "
            "OR (started_at IS NULL AND checkpoint_ref IS NULL))",
            name="start_fields",
        ),
        CheckConstraint(
            "(started_at IS NULL OR started_at >= queued_at) "
            "AND (finished_at IS NULL OR finished_at >= queued_at) "
            "AND (started_at IS NULL OR finished_at IS NULL OR finished_at >= started_at) "
            "AND (lease_acquired_at IS NULL OR lease_acquired_at >= queued_at)",
            name="time_order",
        ),
        Index(
            "ix_agent_run_dispatch_queue",
            "tenant_id",
            "agent_kind",
            "status",
            "priority_class",
            "queued_at",
            "id",
        ),
        Index(
            "ix_agent_run_lease_expiry",
            "tenant_id",
            "agent_kind",
            "status",
            "lease_expires_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    # 잡 생성 시 확정되는 실행 신원이며 AI_RUN 포인터가 아니다. AI_RUN이 생기면 같은 ID로
    # 논리 결합한다(판정 ③ · docs/handoff/2026-08-10_pg_flip_agent_run_fk_order.md §0-1).
    run_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    tenant_id: Mapped[str] = mapped_column(String)
    agent_kind: Mapped[str] = mapped_column(String)  # counsel_pack|mapping_probe|problem_generation
    operation: Mapped[str] = mapped_column(String)
    payload_ref: Mapped[str] = mapped_column(String)
    payload_hash: Mapped[str] = mapped_column(String)
    priority_class: Mapped[str] = mapped_column(String)
    dispatch_attempt: Mapped[int] = mapped_column(Integer)
    lease_generation: Mapped[int] = mapped_column(Integer)
    recovery_count: Mapped[int] = mapped_column(Integer)
    max_recovery_attempts: Mapped[int] = mapped_column(Integer)
    lease_owner: Mapped[str | None] = mapped_column(String, nullable=True)
    lease_acquired_at: Mapped[datetime | None] = mapped_column(_TZ, nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(_TZ, nullable=True)
    checkpoint_ref: Mapped[str | None] = mapped_column(String, nullable=True)
    result_ref: Mapped[str | None] = mapped_column(String, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String, nullable=True)
    queued_at: Mapped[datetime] = mapped_column(_TZ)
    started_at: Mapped[datetime | None] = mapped_column(_TZ, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(_TZ, nullable=True)
    state_checkpoint: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        comment=(
            "마스킹된 관측 캐시. LangGraph state 정본은 별도 PostgresSaver 테이블이며 "
            "이 컬럼으로 재개하지 않는다."
        ),
    )
    progress: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String)
    updated_at: Mapped[datetime] = mapped_column(_TZ)


class AgentStep(Base):
    __tablename__ = "agent_step"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    agent_run_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("agent_run.id"))
    seq: Mapped[int] = mapped_column(Integer)
    node_name: Mapped[str] = mapped_column(String)  # plan|generate|gate|tool_call…
    tool_called: Mapped[str | None] = mapped_column(String, nullable=True)
    tool_args_masked: Mapped[dict[str, Any]] = mapped_column(JSONB)  # 마스킹 통과분만
    # LLM 노드인 경우만 — ERD FK 마커 없음(null 가능).
    llm_call_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    outcome: Mapped[str] = mapped_column(String)


# ───────────────── 묶음④ 실행 메타 (플랫폼) 계열 ─────────────────


class AiRun(Base):
    __tablename__ = "ai_run"

    execution_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String)  # teacher alias · RLS 키
    capability: Mapped[str] = mapped_column(String)
    # VersionSet 공통 6종 — pipeline·engine·schema·contract 는 항상 존재.
    pipeline_version: Mapped[str] = mapped_column(String)
    engine_version: Mapped[str] = mapped_column(String)
    threshold_version: Mapped[str | None] = mapped_column(String, nullable=True)
    prompt_version: Mapped[str | None] = mapped_column(String, nullable=True)
    schema_version: Mapped[str] = mapped_column(String)
    contract_version: Mapped[str] = mapped_column(String)
    # [PART_B] 실행 전용 nullable 4종.
    graph_version: Mapped[str | None] = mapped_column(String, nullable=True)
    taxonomy_version: Mapped[str | None] = mapped_column(String, nullable=True)
    verify_config_version: Mapped[str | None] = mapped_column(String, nullable=True)
    difficulty_calib_version: Mapped[str | None] = mapped_column(String, nullable=True)
    # 🔴 모델 정보 — **사용 축**: 그 실행의 LLM 호출이 0건이면 null(99 #12 · 04 §2.2).
    #    ⚠ (8/8 정정) 종전 「LLM 미사용 실행(detection)에서는 null」은 **「(detection)」이
    #      틀렸다** — detect도 브리핑 프롬프트를 쓰고, 이 셋이 갈리는 축은 capability가
    #      아니라 **호출 유무**다(위 버전 키는 선언 축이라 조건이 다르다).
    model_provider: Mapped[str | None] = mapped_column(String, nullable=True)
    model_name: Mapped[str | None] = mapped_column(String, nullable=True)
    generation_params: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    input_snapshot_hash: Mapped[str] = mapped_column(String)  # 재현성 키
    created_at: Mapped[datetime] = mapped_column(_TZ)


class IdempotencyRecord(Base):
    __tablename__ = "idempotency_record"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "endpoint",
            "idempotency_key",
            name="uq_idempotency_scope",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String)  # RLS 키
    endpoint: Mapped[str] = mapped_column(String)  # 키 스코프
    idempotency_key: Mapped[str] = mapped_column(String)
    snapshot_hash: Mapped[str] = mapped_column(String)  # 바디 동일성 판정
    response_body: Mapped[dict[str, Any]] = mapped_column(JSONB)  # 저장된 응답 envelope
    created_at: Mapped[datetime] = mapped_column(_TZ)  # TTL 30일


class LlmCall(Base):
    __tablename__ = "llm_call"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    run_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("ai_run.execution_id"))
    # role 값 정본: ai.contracts.llm.ModelRole — 🔴 목록을 여기 옮겨 적지 않는다
    role: Mapped[str] = mapped_column(String)
    provider: Mapped[str] = mapped_column(String)
    model: Mapped[str] = mapped_column(String)
    prompt_id: Mapped[str] = mapped_column(String)
    prompt_version: Mapped[str] = mapped_column(String)
    tokens_in: Mapped[int] = mapped_column(Integer)
    tokens_out: Mapped[int] = mapped_column(Integer)
    cost_usd: Mapped[Decimal] = mapped_column(Numeric)
    latency_ms: Mapped[int] = mapped_column(Integer)
    outcome: Mapped[str] = mapped_column(String)  # ok|parse_fail|field_missing|bad_ref|timeout
    created_at: Mapped[datetime] = mapped_column(_TZ)


class LlmPayload(Base):
    __tablename__ = "llm_payload"

    # PK 이자 llm_call 로의 FK — 1:1 본문(마스킹 전제).
    call_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("llm_call.id"), primary_key=True
    )
    request_masked: Mapped[str] = mapped_column(Text)  # redaction 통과본만
    # ⚠ **이름은 `raw`지만 저장 규약은 마스킹 통과본이다**(8/6 · 99 ㉝ · masking_redaction §3).
    #   응답은 어떤 게이트도 안 거치므로 환각으로 실명을 만들 수 있어 저장 전 redact한다.
    #   컬럼명을 바꾸지 않은 이유: 이 파일은 양자 승인 대상이고 개명은 마이그레이션을 부른다.
    #   TTL 30일 — `llm_call.created_at` 조인으로 판정한다(이 테이블엔 시각 컬럼이 없다).
    response_raw: Mapped[str] = mapped_column(Text)


__all__ = ["Base"]
