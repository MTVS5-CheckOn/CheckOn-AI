"""위험신호 감지 계약 — /detect 요청·응답 타입.

사양 원본: docs/part_a/09_detect_spec.md (AI 확정 · 백엔드 전달본)
소유: 박진희(member-A) 단독 — 양자 승인 대상 아님 (docs/02_ownership.md §3)

불변식 1(CLAUDE.md): 감지는 결정론 · LLM 금지. 신호 발화·lifecycle 판정은 전부
결정론 코드가 하며, brief 한 줄 문장만 LLM이 생성하되 왜곡 게이트를 거친다.
불변식 2: evidence 없는 신호는 스키마상 생성 불가 (Signal.evidence min_length=1).
불변식 3: 입력은 alias만 — 실명·연락처 필드는 어디에도 없다. extra="forbid"로
경계 밖 필드(실명 등)를 구조적으로 차단한다.
"""

from datetime import date, datetime, timedelta
from enum import StrEnum
from typing import Annotated, Literal

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

    MANUAL = "MANUAL"
    """🔴 **백엔드 서버 소유 라벨의 한시 호환값**(2026-08-13) — 강사 수기 입력.

    출처: 백엔드 `LearningRecordController :: LearningRecordRequest.toCommand`가
    `LearningRecordSource.MANUAL`을 고정으로 넣는다. `learning_records.source_type`은
    enum이 아니라 자유 문자열이고, v1의 유일한 등록 경로가 이 값을 쓴다.
    의미는 `trackB`(강사 채점 입력)와 같다.

    ⚠ **값을 `trackB`로 정규화하지 않는다.** 정규화하면 `canonical_snapshot_payload`가
      백엔드 원문과 갈려 `snapshot_hash` 대조가 **영구히** 불가능해진다.
    ⚠ **유입 경로 판정에 이 값을 쓰지 마라** — 어느 경로인지는 백엔드만 안다.
      AI는 현재 `source`를 어떤 판정에도 읽지 않는다(전수 실측 2026-08-13: 0건).
    # TODO(Open-N): 백엔드가 source_type → source 화이트리스트 매핑을 배포하면
    #   이 멤버와 BACKEND_EMITTED_SOURCE_VALUES의 "MANUAL"을 **같이** 제거한다.
    """


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
    #: ⚠ **빈 배열을 허용한다**(2026-08-13). 백엔드는 반 미배정 학생의 `cl_unassigned`를
    #:   classes에서 거르므로(`LearningRecordSnapshotService :: build`), 전원 미배정인
    #:   강사는 빈 배열을 보낸다. AI 판정은 classes를 읽지 않는다 — 여기서 400을 내면
    #:   잃는 것만 있다.
    #: 🔴 **기본값을 주지 마라** — 키 자체는 필수다. 「안 보냈다」와 「비었다」를 섞지 않는다.
    classes: tuple[ClassRef, ...]

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


# ─────────────── 부재형 신호의 정본 근거 (R2·R3·R5 · 99 #43) ───────────────


class EvidenceKind(StrEnum):
    """정본 근거의 종류 — 🔴 **discriminator다**(09 §2-보강).

    ⚠ **한 모델에 nullable 필드를 몰아넣지 않는다** — 그러면 `kind=weekly_activity`인데
    `expected_count`가 실린 **거짓 조합**이 문법상 가능해지고, 검증이 모델 밖으로 샌다.
    """

    ASSIGNMENT_WINDOW = "assignment_window"
    """R2 — 그 주 예정 과제와 제출 결과 집계."""

    WEEKLY_ACTIVITY = "weekly_activity"
    """R3 — 그 주 전체 학습량 집계. **0건도 실존하는 레코드**다."""

    ENROLLMENT_TRANSITION = "enrollment_transition"
    """R5 — 재원 상태 전환 이력 1건."""


class _EvidenceBase(BaseModel):
    """세 근거의 공통 축 — 백엔드 정본을 **참조만** 한다(복제 저장 금지).

    🔴 **`source_table`을 AI가 SQL 식별자로 쓰지 않는다** — 응답 evidence에 그대로 실어
    **BE가 자기 원본을 조회**하게 하는 논리명일 뿐이다.
    ⚠ 실명·연락처·자유 원문이 들어올 자리가 **없다**(`extra="forbid"` · 불변식 3).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    record_id: str = Field(min_length=1)
    """백엔드 원본 PK — 응답 evidence에 그대로 실린다."""

    student_ref: str = Field(min_length=1)
    """학생 alias — `students[]`에 실재해야 한다(요청 단위 검증)."""


class AssignmentWindowEvidence(_EvidenceBase):
    """R2 — 그 주 **예정 과제 수와 제출 수**(09 §2-보강).

    🔴 **`expected_count == 0`은 미제출이 아니다** — 과제가 없던 주다(방학·휴강).
    연속 미제출은 `expected_count > 0 AND submitted_count == 0`일 때만 센다.
    ⚠ **일부 제출은 v1의 `consecutive_missing`에 넣지 않는다** — 제출률 하락 경로는
    분모 계약이 서기 전까지 열지 않는다(04 §1 R2 · BE-10).
    """

    kind: Literal[EvidenceKind.ASSIGNMENT_WINDOW]
    source_table: Literal["assignment_week_summary"]
    """🔴 **kind마다 정본 테이블이 하나다**(99 #43) — 자유 문자열이면
    `kind=assignment_window`에 `student_status_history`를 넣어도 통과한다.
    ⚠ JSON 타입은 그대로 문자열이다 — **허용값만 닫는다**(BE DTO 무변경)."""

    week_start: date
    expected_count: int = Field(ge=0)
    submitted_count: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_submitted_within_expected(self) -> "AssignmentWindowEvidence":
        if self.submitted_count > self.expected_count:
            raise ValueError(
                "submitted_count는 expected_count를 넘을 수 없다"
                f"({self.submitted_count} > {self.expected_count})"
            )
        return self


class WeeklyActivityEvidence(_EvidenceBase):
    """R3 — 그 주 **전체 학습량 집계**(09 §2-보강).

    🔴 **0건도 실존하는 집계 레코드다** — 「기록이 없다」와 「집계가 0이다」는 다른 사실이고
    앞은 증명할 수 없다. R3는 이 값을 **판정과 evidence에 함께** 쓴다.
    ⚠ `learning_events` 개수로 다시 센 값을 정본처럼 섞지 않는다.
    """

    kind: Literal[EvidenceKind.WEEKLY_ACTIVITY]
    source_table: Literal["student_week_activity"]
    week_start: date
    activity_count: int = Field(ge=0)


class EnrollmentTransitionEvidence(_EvidenceBase):
    """R5 — 재원 **상태 전환 이력** 1건(09 §2-보강).

    🔴 `students[].status`는 **현재 값**이라 *"언제 바뀌었는가"* 를 말하지 않는다.
    복귀 케어는 **전환 사실**로 발화하므로 그 이력이 정본이다.
    """

    kind: Literal[EvidenceKind.ENROLLMENT_TRANSITION]
    source_table: Literal["student_status_history"]
    occurred_at: datetime
    from_status: StudentStatus
    to_status: StudentStatus

    @field_validator("occurred_at")
    @classmethod
    def validate_timezone_aware(cls, value: datetime) -> datetime:
        """🔴 **naive datetime 금지** — 주차 귀속이 기기 시간대에 좌우되면 안 된다."""
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("occurred_at은 timezone-aware여야 한다")
        return value


#: 🔴 **discriminated union** — `kind`로만 갈린다. 다른 kind의 필드를 섞으면 거부된다.
type DetectionEvidence = Annotated[
    AssignmentWindowEvidence | WeeklyActivityEvidence | EnrollmentTransitionEvidence,
    Field(discriminator="kind"),
]


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

    detection_evidence: tuple[DetectionEvidence, ...] = ()
    """R2·R3·R5의 **정본 근거**(99 #43). 🔴 **optional이라 기존 요청이 안 깨진다.**

    ⚠ **없으면 세 규칙은 발화하지 않고 `authoritative_evidence_missing`으로 skip**된다 —
    조용히 다른 기록을 근거로 삼지 않는다(fail-closed). R1·R4·R6는 영향이 없다.
    ⚠ **판정 입력이므로 `snapshot_hash` 대상**이다(04 부록 A · `canonical_snapshot_payload`).
    """

    @model_validator(mode="after")
    def validate_detection_evidence(self) -> "DetectRequest":
        """🔴 새 배열의 **요청 단위 경계**만 본다(기존 필드 관계 무결성은 후속 P1).

        전부 **400 `INVALID_SCHEMA`** 로 수렴한다 — error_codes §1의 7/22 A판정이
        *"바디 스키마 위반은 헤더 누락·JSON 파싱과 함께 하나의 400"* 으로 확정했다.
        ⚠ **`submitted > expected`만 422로 가르지 않았다** — 그러면 같은 배열의 위반이
        상태 코드 둘로 갈리고, 그 구분은 04·error_codes 계약 변경 없이는 세울 수 없다.
        """
        known = {student.student_ref for student in self.students}
        by_record: dict[tuple[str, str], DetectionEvidence] = {}
        window_keys: set[tuple[str, str, date]] = set()
        activity_keys: set[tuple[str, date]] = set()
        for item in self.detection_evidence:
            if item.student_ref not in known:
                raise ValueError(
                    f"detection_evidence의 student_ref가 students[]에 없다: {item.student_ref!r}"
                )
            #: 같은 원본 PK가 **다른 내용**으로 두 번 오면 어느 쪽이 정본인지 못 정한다.
            record_key = (item.source_table, item.record_id)
            seen = by_record.get(record_key)
            if seen is not None and seen != item:
                raise ValueError(
                    f"같은 (source_table, record_id)에 다른 내용이 왔다: {record_key}"
                )
            by_record[record_key] = item

            if isinstance(item, AssignmentWindowEvidence):
                #: ⚠ 종류·학생·주차가 같은 집계가 둘이면 **값이 같아도** 거부한다 —
                #:   집계 정본은 하나여야 한다(둘을 합치는 규칙을 발명하지 않는다).
                key = (item.kind.value, item.student_ref, item.week_start)
                if key in window_keys:
                    raise ValueError(f"과제 주차 집계가 중복됐다: {key}")
                window_keys.add(key)
            elif isinstance(item, WeeklyActivityEvidence):
                activity_key = (item.student_ref, item.week_start)
                if activity_key in activity_keys:
                    raise ValueError(f"주간 학습량 집계가 중복됐다: {activity_key}")
                activity_keys.add(activity_key)
        self._validate_evidence_against_snapshot()
        return self

    def _validate_evidence_against_snapshot(self) -> None:
        """분석 기준 주차·학생 상태와의 정합 — 🔴 **미래와 불일치를 거부한다.**"""
        week_monday = date.fromisoformat(self.snapshot_meta.week_start)
        status_of = {s.student_ref: s.status for s in self.students}
        returned_transition: set[str] = set()
        for item in self.detection_evidence:
            if isinstance(item, AssignmentWindowEvidence | WeeklyActivityEvidence):
                if item.week_start > week_monday:
                    raise ValueError(
                        f"분석 주차보다 미래인 집계다: {item.week_start} > {week_monday}"
                    )
                continue
            occurred_monday = item.occurred_at.date()
            occurred_monday -= timedelta(days=occurred_monday.weekday())
            if occurred_monday > week_monday:
                raise ValueError(
                    f"분석 주차보다 미래인 상태 전환이다: {occurred_monday} > {week_monday}"
                )
            if item.to_status is StudentStatus.RETURNED:
                returned_transition.add(item.student_ref)
        #: 🔴 **상태와 전환이 갈리면 조용히 한쪽을 고르지 않는다** — 어느 쪽이 사실인지
        #:   AI가 정할 수 없다. 요청을 거부해 BE가 맞춰 보내게 한다.
        for student_ref in returned_transition:
            if status_of.get(student_ref) is not StudentStatus.RETURNED:
                raise ValueError(
                    "복귀 전환 이력이 있는데 students[].status가 returned가 아니다: "
                    f"{student_ref!r}"
                )


# ───────────────────────── Response (AI → 백엔드) ─────────────────────────


class Brief(BaseModel):
    """브리핑 한 줄 — 명세 §3 signals[].brief. LLM 생성 + 왜곡 게이트 통과분."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    text: str = Field(min_length=1)
    gate_passed: bool
    fallback_used: bool
    """true면 게이트 실패로 안전 템플릿 문장으로 대체됨 — 표시는 동일."""


class EvidenceRole(StrEnum):
    """근거 1건의 역할 — 표시 층이 「근거」와 「비교 기준」을 갈라 보여줄 수 있게 한다.

    🔴 **닫힌 집합이다.** 자유 문자열이면 백엔드 표시 분기가 조용히 갈린다.
    """

    TRIGGER = "trigger"
    """판정을 성립시킨 기록 — *"이번 주 정답률 0%"*."""

    BASELINE = "baseline"
    """그 판정이 비교 대상으로 삼은 기록 — *"직전 8주"*. 없는 규칙이 있다(아래)."""


class EvidenceItem(BaseModel):
    """신호 근거 1건 — 명세 §3 signals[].evidence[].

    evidence 없는 신호는 생성 불가이므로(불변식 2), Signal.evidence가 이 항목을
    최소 1개 강제한다.

    ━━ 구조화 필드 (2026-08-13 · 99 #59·#60) ━━

    🔴 **`summary` 하나로는 표시 라벨을 만들 수 없다.** AI는 과제명·학생명·반명을 **아예
    받지 않으므로**(불변식 3) *"8/12 과제 · 정답률 0% (0/5문항)"* 같은 문면을 만들 수 없다.
    ⇒ **재료를 구조화해서 보내고 문면은 백엔드가 조립한다.**

    🔴 **비교값(`metric`·`observed`·`baseline`·`sample_size`)은 여기가 아니라 `Signal`에 있다.**
    *"평소 100%에서 0%로 하락"* 은 **신호 하나의 속성**이지 개별 레코드의 속성이 아니다 —
    R1의 기준선은 직전 주들의 **평균 정답률**이고 그 값에 해당하는 **백엔드 레코드가 없다**
    (`learning_event`는 문항 단위다). 레코드마다 주 단위 값을 붙이면 *"그 기록의 값"* 이
    거짓이 된다. ⇒ **비교는 신호가, 출처는 evidence가 든다**(99 #60 · 안 D).

    🔴 **evidence는 LLM으로 만들지 않는다** — 근거는 주장을 증명하는 물건이라 환각이 섞이면
    안 되고 재현되어야 한다(brief만 LLM이고 동일 seed 8회에 유일 문장 3종 · 99 ㊼·#56).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_table: str = Field(min_length=1)
    record_id: str = Field(min_length=1)
    """백엔드 DB 원본 PK — 강사 [근거 보기]가 이 ID로 원본 조회."""

    summary: str = Field(min_length=1)
    """🔴 **deprecated (2026-08-13 · 99 #59).** 🔴 **다만 규칙마다 성격이 달랐다.**

    ⚠ **"전부 동어반복"이 아니었다** — 리허설에서 본 것이 R1이라 그렇게 읽혔을 뿐이다::

        R1·R4·R6  f"{signal_type} 근거 기록"          🔴 정보 0 (필드 이름의 반복)
        R2        "예정 과제 3건 중 제출 0건"          ✅ 숫자가 있었다
        R3        "해당 주 학습 활동 2건"              ✅ 숫자가 있었다
        R5        "휴원 후 복귀 상태 전환 기록"        ✅ 문구가 있었다

    🔴 **그래서 「읽지 마라」를 먼저 보내면 R2·R3 신호가 화면에서 숫자를 통째로 잃는다** —
    그 숫자가 다른 어디에도 없었기 때문이다. ⇒ **구조화 필드로 먼저 옮겼다**(99 #60):
    R2는 `observed`(제출)+`sample_size`(예정), R3는 `observed`(활동 수). R5는 숫자가 없다.

    ⇒ **이제 `summary`의 모든 숫자가 구조화 필드에도 있다.** 표시 라벨은 백엔드가
    `role`·`observed`·`sample_size`와 자기가 가진 과제명·날짜로 포맷한다.
    ⚠ **지우지 않는다** — 지금 백엔드가 읽고 있어 삭제는 파괴적 변경이다.
    만료: 백엔드가 구조화 필드로 전환하면 별건으로 제거한다.
    """

    role: EvidenceRole
    """🔴 **필수** — 기본값을 두지 않는다.

    기본값을 `trigger`로 두면 **채우는 것을 잊어도 조용히 통과한다.** 근거의 성격을
    안 밝히면 표시 층이 비교 기준을 근거로 섞어 보여준다 — fail-closed로 둔다.
    """

    observed: float | None = None
    """🔴 **그 기록 자신의 값** — 다른 기록의 값이나 집계값을 여기 넣지 않는다.

    🔴 **`Signal.observed`와 다른 값이다.** 그쪽은 **신호 전체**의 관측값이고 이쪽은
    **개별 레코드**의 값이다. `submit_drop`에서 그쪽은 «연속 3주», 이쪽은
    «그 주 제출 0건»이다 — 같은 응답에서 **다른 것을 가리킨다.**


    ⚠ **대부분의 규칙에서 `None`이다.** `learning_event`는 **문항 단위**라(`correct`·
    `duration_sec`) 주 단위 지표(정답률·정규화 시간)에 해당하는 값이 **그 레코드에 없다.**
    주 단위 백엔드 집계(`weekly_activity_summary` 등)일 때만 채워진다.
    🔴 **없는 값을 지어내지 않는다** — 주 정답률을 그 주의 문항 레코드마다 반복해 실으면
    *"그 기록의 값"* 이 거짓이 되고, BE가 원본을 열었을 때 숫자가 안 맞는다.
    """

    sample_size: int | None = None
    """그 기록의 **분모** — 있을 때만.

    🔴 **이 필드가 없으면 `summary`를 못 뗀다.** R2의 `"예정 과제 3건 중 제출 0건"` 에서
    `제출 0`은 `observed`가 받는데 **`예정 3`이 갈 곳이 없었다** — 그 숫자가 응답 어디에도
    없어져 백엔드가 `summary` 문자열을 계속 파싱해야 한다(2026-08-13 실측).
    """

    occurred_on: date | None = None
    """**그 기록의 날짜** — 주간 집계 레코드만 주 시작일이다.

    🔴 **문항 단위 기록(`learning_event`)은 그 문항을 푼 날이다**(99 #60 보강). 종전에는
    주 월요일을 실어서, 8/13에 푼 문항이 `2026-08-10`으로 나갔다 — **BE가 원본을 열면
    날짜가 안 맞았다.** `LearningEvent.occurred_at`은 **필수 필드**인데 **있는 값을 버리고
    없는 값을 만들어 넣고 있었다**(바로 위 `observed`가 금지하는 바로 그것).

    ⚠ **주간 집계는 그대로 주 시작일이다** — `weekly_activity_summary`·
    `assignment_week_summary`는 **진짜 주 단위 레코드**라 주 시작일이 **정답**이다.

    🔴 **날짜를 모르면 `None`이다 — 주 월요일로 대신하지 않는다.** 지금은 그런 기록이
    안 생기지만(`occurred_at` 필수), 증분 복원분(`FEATURE_WEEK`)이 근거를 만들게 되면
    그 자리가 열린다(99 #65). 그때 월요일로 메우면 같은 사고가 되풀이된다.
    """


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

    #: ━━ 비교값 (2026-08-13 · 99 #60 · 안 D) ━━
    #:
    #: 🔴 **여기 있는 이유** — *"평소 100%에서 0%로 하락"* 은 **신호 하나의 속성**이지
    #: 개별 레코드의 속성이 아니다. R1의 기준선은 직전 주들의 **평균 정답률**이고 그 값에
    #: 해당하는 **백엔드 레코드가 없다**(`learning_event`는 문항 단위). evidence 행에 실으면
    #: 레코드마다 주 단위 값을 반복하게 되고 *"그 기록의 값"* 이 거짓이 된다.
    #:
    #: ⚠ **표시 문면은 백엔드가 만든다** — AI는 과제명·학생명·반명을 안 받는다(불변식 3).
    #: 🔴 **판정에 쓴 그 값이다** — 여기서 다시 계산하지 않는다. 갈리면 BE가 원본을 열었을
    #:   때 숫자가 안 맞는다(`detection/evidence.py` 모듈 docstring과 같은 규율).
    #: ⚠ **채울 수 없으면 `None`** — 0으로 적지 않는다. 「기준이 없다」와 「기준이 0이다」는
    #:   다른 사실이고(99 #43 계열), 규칙마다 채울 수 있는 것이 다르다(`04` 규칙표).

    metric: str | None = None
    """무엇을 잰 값인가 — `accuracy` · `activity_count` · `norm_time` · `error_share` 등.

    ⚠ **닫힌 enum으로 두지 않았다** — 규칙이 늘 때마다 계약을 열어야 하고, 소비 측은
    이 문자열을 그대로 라벨에 쓰지 않는다(자기 표를 본다).
    """

    observed: float | None = None
    """판정 창에서 관측된 값 — 이 신호가 *"지금 이렇다"* 고 말하는 수치.

    🔴 **`EvidenceItem.observed`와 다른 값이다.** 이쪽은 **신호 전체**의 관측값이고
    그쪽은 **개별 레코드**의 값이다. `submit_drop`에서 이쪽은 «연속 3주», 그쪽은
    «그 주 제출 0건»이다 — 같은 응답에서 **다른 것을 가리킨다.**
    ⚠ 이름을 바꾸지 않는다(백엔드 계약·컬럼이 이 이름이다). **문서가 가른다.**
    """

    baseline: float | None = None
    """비교 기준값 — 🔴 **기준선 비교를 하지 않는 규칙에서는 `None`이다.**

    R2(연속 미제출 **횟수**)·R6(같은 주 셀 점유율)·R5(복귀 사건)는 **임계값과 비교**하지
    기준선과 비교하지 않는다. 임계값을 여기 적으면 *"평소 대비"* 로 읽혀 거짓이 된다.
    """

    sample_size: int | None = None
    """분모 — 정답률이면 채점 문항 수, 기준선 평균이면 그 창의 주 수."""

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
