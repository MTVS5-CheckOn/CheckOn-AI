"""ERD 34테이블 ORM — 06_erd.md 정본을 그대로 옮긴다.

소유: 공통 계약 (A+B 확인 완료 — 양자 12곳, 02_ownership §4). ERD가 정본이므로
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


class ProblemItem(Base):
    __tablename__ = "problem_item"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    set_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("problem_set.id"))
    passage_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("passage.id"), nullable=True
    )
    area_tag: Mapped[str] = mapped_column(String)
    type_tag: Mapped[str] = mapped_column(String)
    item_format: Mapped[str] = mapped_column(String)
    skill_node_id: Mapped[str | None] = mapped_column(String, nullable=True)
    stem: Mapped[str] = mapped_column(Text)
    choices: Mapped[dict[str, Any]] = mapped_column(JSONB)
    answer: Mapped[dict[str, Any]] = mapped_column(JSONB)
    rationale: Mapped[str] = mapped_column(Text)
    difficulty_est: Mapped[Decimal] = mapped_column(Numeric)
    difficulty_fit: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    difficulty_calib_ver: Mapped[str] = mapped_column(String)
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
    run_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("ai_run.execution_id"))
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
    # 모델 정보 — LLM 미사용 실행(detection)에서는 null.
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
    role: Mapped[str] = mapped_column(String)  # generator|verifier|mapper|classifier|narrator
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
