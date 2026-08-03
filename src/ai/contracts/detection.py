"""위험신호 감지 계약 — /detect 요청·응답 타입.

사양 원본: docs/part_a/09_detect_spec.md (AI 확정 · 백엔드 전달본)
소유: 박진희(member-A) 단독 — 양자 승인 대상 아님 (docs/02_ownership.md §3)

불변식 1(CLAUDE.md): 감지는 결정론 · LLM 금지. 신호 발화·lifecycle 판정은 전부
결정론 코드가 하며, brief 한 줄 문장만 LLM이 생성하되 왜곡 게이트를 거친다.
불변식 2: evidence 없는 신호는 스키마상 생성 불가 (Signal.evidence min_length=1).
불변식 3: 입력은 alias만 — 실명·연락처 필드는 어디에도 없다. extra="forbid"로
경계 밖 필드(실명 등)를 구조적으로 차단한다.
"""

from datetime import date, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ai.contracts.taxonomy import AreaTag, ItemFormat, SubjectTrack, TypeTag


class RuleId(StrEnum):
    """내부 규칙 번호 — 명세 §1 표. [로그] 통계·디버깅용, 화면 미노출."""

    R1 = "R1"
    R2 = "R2"
    R3 = "R3"
    R4 = "R4"
    R5 = "R5"
    R6 = "R6"


class SignalType(StrEnum):
    """신호 종류 6종 — 명세 §1 (AI 확정값). 코드 분기·통계용."""

    ACC_DROP = "acc_drop"
    """정답률 하락 (R1) — 평소보다 크게 떨어진 상태가 2주 연속."""

    SUBMIT_DROP = "submit_drop"
    """제출 저조 (R2) — 제출률 하락 또는 연속 미제출."""

    VOLUME_GAP = "volume_gap"
    """학습 공백 (R3) — 주간 학습량이 평소의 40% 미만."""

    HIDDEN_RISK = "hidden_risk"
    """숨은 위기 (R4) — 점수는 멀쩡한데 풀이 시간 급증. 제품 핵심 신호."""

    RETURN_CARE = "return_care"
    """복귀 케어 (R5) — 휴원 후 복귀 첫 주. TOP 상한과 별도."""

    TYPE_BIAS = "type_bias"
    """유형 편중 (R6) — 특정 영역×유형에 오답이 몰림."""


class Lifecycle(StrEnum):
    """경보 생애 판정 — 명세 §4. AI가 alert_context를 입력으로 결정론 판정한다.

    억제(해소 후 2주 이내 + 팔로업 이미 나감)는 응답에서 제외되므로 값이 없다 —
    "안 보내는 것"은 enum 값이 아니라 signals 배열에서의 부재로 표현한다.
    """

    NEW = "new"
    """새 Alert 생성 — 이력 없음·다른 유형·해소 후 2주 경과 재발."""

    ONGOING = "ongoing"
    """같은 유형 open 경보가 이미 있음 — 기존 Alert의 brief·evidence만 교체."""

    FOLLOW_UP = "follow_up"
    """해소 후 2주 이내 재발 + 팔로업 미발송 — 팔로업 카드 1회."""


#: signal_type → 화면 표시 한글 문구 — 명세 §1 표 (AI 확정).
#: 응답 Signal.display_label에 이 값을 그대로 실어 백엔드·프론트의 문구 매핑을 없앤다.
DISPLAY_LABELS: dict[SignalType, str] = {
    SignalType.ACC_DROP: "정답률 하락",
    SignalType.SUBMIT_DROP: "제출 저조",
    SignalType.VOLUME_GAP: "학습 공백",
    SignalType.HIDDEN_RISK: "숨은 위기",
    SignalType.RETURN_CARE: "복귀 케어",
    SignalType.TYPE_BIAS: "유형 편중",
}

#: rule_id → signal_type — 명세 §1 표의 1:1 대응.
RULE_SIGNAL_MAP: dict[RuleId, SignalType] = {
    RuleId.R1: SignalType.ACC_DROP,
    RuleId.R2: SignalType.SUBMIT_DROP,
    RuleId.R3: SignalType.VOLUME_GAP,
    RuleId.R4: SignalType.HIDDEN_RISK,
    RuleId.R5: SignalType.RETURN_CARE,
    RuleId.R6: SignalType.TYPE_BIAS,
}

#: 관찰 중(신규생) 기준 — 명세 §3. 재원 14일(2주) 미만은 판정에서 제외.
#: AI는 목록을 돌려주지 않고 stats.excluded_under_2w 숫자만 낸다.
OBSERVED_ONLY_MIN_WEEKS = 2


# ───────────────────────── Request (백엔드 → AI) ─────────────────────────


class TermContext(StrEnum):
    """학사 상황 — 명세 §2."""

    NORMAL = "normal"
    NEW_TERM = "new_term"
    """신학기 — 반 재편성 직후라 발화 기준을 느슨하게."""

    VACATION = "vacation"
    """방학 — 과제 관련 신호 R2·R3를 아예 보지 않음."""


class StudentStatus(StrEnum):
    """재원 상태 — 명세 §2."""

    ENROLLED = "enrolled"
    PAUSED = "paused"
    """휴원 중 — 판정 제외."""

    RETURNED = "returned"
    """복귀 첫 주 — R5 복귀 케어 발동."""


class EventType(StrEnum):
    """학습 기록 종류 — 명세 §2 learning_events.type."""

    SOLVE = "solve"
    SUBMIT = "submit"
    ATTEND = "attend"
    CONSULT = "consult"


class EventSource(StrEnum):
    """기록 유입 경로 — 명세 §2 learning_events.source."""

    TRACK_A = "trackA"
    """자료 업로드."""

    TRACK_B = "trackB"
    """강사 채점 입력."""

    STUDENT_HOME = "studentHome"
    """학생 숙제 앱."""


class AlertStatus(StrEnum):
    """경보 이력 상태 — 명세 §2 alert_context.status."""

    OPEN = "open"
    RESOLVED = "resolved"


#: consent 동의 상태 — 명세 §2는 "granted"만 명시하고 다른 값을 열거하지 않는다.
#: 명세에 없는 상태값을 지어내지 않기 위해 enum이 아니라 str로 받고 이 상수와 대조한다.
CONSENT_GRANTED = "granted"


class ClassRef(BaseModel):
    """반 참조 — 명세 §2 snapshot_meta.classes[]."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    class_ref: str = Field(min_length=1)


class SnapshotMeta(BaseModel):
    """스냅숏 메타 — 명세 §2 snapshot_meta."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    week_start: str = Field(min_length=1)
    """이번 주 월요일 — "이번 주 vs 평소" 비교의 기준 키. ISO date 형식 (09 §2)."""

    snapshot_hash: str = Field(min_length=1)
    """요청 본문 전체(alert_context 포함)의 해시 — 재현·감사 키 (04 부록 A)."""

    term_context: TermContext
    classes: tuple[ClassRef, ...] = Field(min_length=1)

    @field_validator("week_start")
    @classmethod
    def validate_week_start_is_iso_date(cls, value: str) -> str:
        """week_start를 ISO date로 엄격 검증 — 오타는 넘기지 않고 거부 (09 §2 A판정 7/22)."""
        try:
            date.fromisoformat(value)
        except ValueError as exc:
            raise ValueError(f"week_start는 ISO date(YYYY-MM-DD)여야 한다: {value!r}") from exc
        return value


class StudentInput(BaseModel):
    """재원생 1명 — 명세 §2 students[]. 실명 필드 없음 (불변식 3)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    student_ref: str = Field(min_length=1)
    """alias — 실명↔가명 매핑은 백엔드 vault 전유."""

    class_ref: str = Field(min_length=1)
    enrolled_weeks: int = Field(ge=0)
    """등원 주차 — OBSERVED_ONLY_MIN_WEEKS 미만은 조용히 판정 제외."""

    status: StudentStatus
    consent: str = Field(min_length=1)
    """CONSENT_GRANTED가 아니면 이 학생의 learning_events를 전부 버린다."""


class LearningEvent(BaseModel):
    """학습 기록 1건 — 명세 §2 learning_events[]. 증분만 전송된다.

    area_tag·type_tag·item_format·subject_track은 taxonomy.py 공용 어휘를 재사용한다
    (어휘 분기 금지 — 03_coding_rules.md §1).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    record_id: str = Field(min_length=1)
    """백엔드 DB 원본 PK — 근거 역추적 키, 불변 필수."""

    student_ref: str = Field(min_length=1)
    type: EventType
    occurred_at: datetime
    correct: bool | None = None
    """solve일 때만 — 정답률 계산 재료 (R1·R6)."""

    duration_sec: int | None = Field(default=None, ge=0)
    """solve일 때만, 풀이 시간(초). None이면 R4(숨은 위기) 판정 불가."""

    passage_word_count: int | None = Field(default=None, ge=0)
    """지문형 문항만 — R4가 "시간 ÷ 지문 길이"로 정규화할 때 씀."""

    passage_ref: str | None = Field(default=None, min_length=1)
    """지문/자료 묶음 참조 — 기대치 입력 층의 조합 키(04 §1 · 05 [A 확정 통보 8/3]).

    같은 지문·도표·〈보기〉 자료를 공유하는 문항들이 같은 값을 갖고, **재출제 시에도
    유지**된다. 비지문 자료도 포함하므로 "지문"에만 국한되지 않는다.

    **AI는 이 값을 역참조하지 않는다** — `record_id`(근거 조회용 DB PK)와 달리 조합
    통계의 그룹 키로만 쓰는 불투명 참조다. `None`이면 그 문항은 전체 평균 폴백으로
    떨어지며, 이는 **보정 없음과 동치**라 안전하다.
    """

    area_tag: AreaTag | None = None
    subject_track: SubjectTrack | None = None
    type_tag: TypeTag | None = None
    item_format: ItemFormat | None = None
    assignment_title_text: str | None = None
    """과제/시험 이름 원문 — 태그 자동 제안 입력 (Open-4b)."""

    source: EventSource


class AlertContextItem(BaseModel):
    """최근 30일 경보 이력 1건 — 명세 §2 alert_context[].

    AI가 이 이력을 입력으로 lifecycle을 판정한다 (§4 규칙표).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    student_ref: str = Field(min_length=1)
    signal_type: SignalType
    status: AlertStatus
    resolved_at: datetime | None = None
    """resolved일 때 해소 일시 — "해소 후 2주" 쿨다운 계산 기준."""

    followed_up: bool
    """해소 후 팔로업 카드가 이미 나갔는지 — 중복 방지."""

    @model_validator(mode="after")
    def validate_status_resolved_at(self) -> "AlertContextItem":
        """상태 조합 검증 (09 §2 A 판정 7/22): resolved면 resolved_at 필수, open이면 부재.

        미래 해소 시각 금지는 week_start와의 비교라 모델 단독으론 판정할 수 없다 —
        엔진 입력 검증(week_start를 아는 곳)이 적절하다. 이번 v0는 모델 단독 상태 조합만
        강제하고, 미래 시각 검증은 엔진 편입을 후속 제안으로 남긴다.
        """
        if self.status is AlertStatus.RESOLVED and self.resolved_at is None:
            raise ValueError("status=resolved면 resolved_at이 필수다")
        if self.status is AlertStatus.OPEN and self.resolved_at is not None:
            raise ValueError("status=open이면 resolved_at이 없어야 한다")
        return self


class DetectRequest(BaseModel):
    """POST /v1/detect 요청 바디 — 명세 §2 (백엔드 → AI).

    tenant_id는 바디가 아니라 X-Tenant-Id 헤더로 온다 — 여기 담지 않는다.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    snapshot_meta: SnapshotMeta
    students: tuple[StudentInput, ...] = ()
    learning_events: tuple[LearningEvent, ...] = ()
    alert_context: tuple[AlertContextItem, ...] = ()
    """최근 30일 경보 이력 — 없으면 전 신호가 lifecycle=new로 판정된다."""


# ───────────────────────── Response (AI → 백엔드) ─────────────────────────


class Brief(BaseModel):
    """브리핑 한 줄 — 명세 §3 signals[].brief. LLM 생성 + 왜곡 게이트 통과분."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    text: str = Field(min_length=1)
    gate_passed: bool
    fallback_used: bool
    """true면 게이트 실패로 안전 템플릿 문장으로 대체됨 — 표시는 동일."""


class EvidenceItem(BaseModel):
    """신호 근거 1건 — 명세 §3 signals[].evidence[].

    evidence 없는 신호는 생성 불가이므로(불변식 2), Signal.evidence가 이 항목을
    최소 1개 강제한다.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_table: str = Field(min_length=1)
    record_id: str = Field(min_length=1)
    """백엔드 DB 원본 PK — 강사 [근거 보기]가 이 ID로 원본 조회."""

    summary: str = Field(min_length=1)


class Signal(BaseModel):
    """위험신호 1건 — 명세 §3 signals[]. 선별·정렬 완료 상태.

    반별 TOP 3~5 상한은 `new`·`follow_up`에만 적용하며 `ongoing`·`return_care`(R5)는
    상한 밖으로 추가된다 — `signals` 길이와 `rank`가 5를 초과할 수 있다(04 §3 · 99 #14).
    `rank`는 반 내 최종 표시 순번이다(통과분 뒤 ongoing·R5).

    display_label은 signal_type에 대응하는 DISPLAY_LABELS 값과 일치해야 한다 —
    AI가 만드는 신호는 항상 정합하며(validate_display_label), "모르는 signal_type"
    관용은 수신 측(백엔드) 몫이다.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    signal_id: str = Field(min_length=1)
    student_ref: str = Field(min_length=1)
    class_ref: str = Field(min_length=1)
    rule_id: RuleId
    signal_type: SignalType
    display_label: str = Field(min_length=1)
    score: float = Field(ge=0.0, le=1.0)
    """[로그] 심각도 — 정렬 근거용, 화면 미노출(낙인 방지)."""

    rank: int = Field(ge=1)
    """반 내 우선순위 — 1이 최상위."""

    advisory: bool = False
    """참고 표시 전용 신호 — 알림·카드에서 빼고 학생 상세에만 보인다(04 §1 R4 재정의).

    v1에서 `True`가 되는 것은 **R4 단독 경보**뿐이다(13 §3 — 조기 경보 주장 보류,
    동시 진단 보조로 재정의). **판정·evidence는 정식 신호와 똑같이 산출되며**, 바뀌는
    것은 소비 방식이다: TOP N 랭킹 비참여 · 상한 슬롯 미소비(`capped_out` 미산입) ·
    상한 밖 합류(`ongoing`·R5와 같은 축).

    ⚠ **R4가 다른 규칙과 병합되면 `False`다** — 그 경보는 R1 등의 근거로 정식 발화한다.
    """

    lifecycle: Lifecycle
    brief: Brief
    evidence: tuple[EvidenceItem, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_display_label(self) -> "Signal":
        """display_label이 signal_type의 확정 문구와 일치하는지 (명세 §1)."""
        expected = DISPLAY_LABELS[self.signal_type]
        if self.display_label != expected:
            raise ValueError(
                f"display_label '{self.display_label}'이 signal_type "
                f"{self.signal_type}의 확정 문구 '{expected}'와 다르다"
            )
        return self

    @model_validator(mode="after")
    def validate_rule_signal_match(self) -> "Signal":
        """rule_id와 signal_type의 1:1 대응이 명세 §1 표와 맞는지."""
        expected = RULE_SIGNAL_MAP[self.rule_id]
        if self.signal_type is not expected:
            raise ValueError(
                f"rule_id {self.rule_id}는 signal_type {expected}에 대응하는데 "
                f"{self.signal_type}가 왔다"
            )
        return self


class RuleSkipped(BaseModel):
    """판정 못 한 규칙 — 명세 §3 stats.rules_skipped[]. 데이터 품질 모니터링용."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    rule_id: RuleId
    reason: str = Field(min_length=1)
    """예: duration_missing."""

    students: int = Field(ge=0)


class DetectStats(BaseModel):
    """운영 지표 — 명세 §3 stats. [로그] 화면 미노출.

    excluded_under_2w는 관찰 중(재원 2주 미만) 제외 수 — 백엔드의 '관찰 중' 계산과
    대조용 숫자다. AI는 학생 목록(구 observed_only)을 돌려주지 않는다 (명세 §3).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    students_evaluated: int = Field(ge=0)
    signals_raised: int = Field(ge=0)
    excluded_under_2w: int = Field(ge=0)
    capped_out: int = Field(ge=0)
    """lifecycle 억제 후 `new`·`follow_up` 후보의 탈락 수만 (04 §3 · 99 #14).

    상한 밖으로 합류하는 `ongoing`·R5는 세지 않는다.
    """

    r1_threshold_pp: float | None = None
    """이번 실행에 실제로 쓴 R1 하락폭 임계(%p) — 04 §1 발동률 목표 방식.

    분위 임계는 스냅숏마다 값이 달라지므로 **쓴 값을 기록한다**(불변식 8 — 기록 = 실제
    사용분). 강사의 "왜 오늘은 안 떴냐"에 답할 근거이기도 하다.
    """

    r1_threshold_source: str | None = None
    """`quantile` | `fallback` — 표본 부족 시 고정 `drop_pp`로 폴백한 것."""

    r1_pool_n: int | None = Field(default=None, ge=0)
    """분위 산출에 쓴 표본 수(베이스라인 창 × 전 학생의 주간 하락폭)."""

    rules_skipped: tuple[RuleSkipped, ...] = ()


class DetectResponse(BaseModel):
    """POST /v1/detect 응답의 data — 명세 §3 (AI → 백엔드).

    API envelope(error·meta)는 응답 계층이 감싼다 — 여기는 data 내용만.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    signals: tuple[Signal, ...] = ()
    stats: DetectStats
