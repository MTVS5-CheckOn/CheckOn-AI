"""상담팩(counsel_pack) 계약 — 학생별 입력·결과·초안 블록.

사양 원본: `docs/06_erd.md` DRAFT·DRAFT_BLOCK · `docs/policies/error_codes.md` §2.1 ·
`docs/part_a/05_tone_mapping.md`(4축 라벨).
소유: **박진희 단독**(02_ownership §3 — `detection.py`·`composition.py`). 양자 아님.

**경계 — `DraftContext`는 워커 state에 들어가지 않는다**(`langgraph_state.md` §1.2 ⑨ 개정).
state에는 `context_ref`·`context_hash`만 싣고, 본문은 저장소가 보관한다. 이 타입은
**저장소·조립부가 다루는 입력**이며 체크포인트·트레이스로 나가지 않는다.

불변식 3(CLAUDE.md): 실명·연락처 필드가 없다 — `student_ref`·`guardian_ref`는 alias다.
"""

from enum import StrEnum
from re import compile as _re_compile
from typing import Annotated, Final
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

type NonEmptyStr = Annotated[str, Field(min_length=1)]

#: 표기값에서 수치를 뽑는 정규식 — 게이트 EXACT 대조의 허용집합 산출용.
_NUMBER_RE: Final = _re_compile(r"\d+")


# ───────────────────────── 4축 라벨 (05 §1) ─────────────────────────
#
# 값 어휘의 사양 원본은 05 §1이고 런타임 조회는 `composition/tone_map.yaml`이 한다.
# 아래 enum과 yaml `axis_rules`의 어휘가 어긋나면 조회마다 변환이 끼므로
# `tests/ai/unit/composition/test_counsel_contracts.py`가 값 대조로 고정한다.


class CommStyle(StrEnum):
    """소통 스타일 — tone_map 조합 키의 1번째 축."""

    DATA = "data"
    NARRATIVE = "narrative"


class Sensitivity(StrEnum):
    """민감도 — 조합 키의 2번째 축."""

    ANXIOUS = "anxious"
    DIRECT = "direct"


class Interest(StrEnum):
    """관심사 — 조합 키의 3번째 축."""

    GRADE = "grade"
    ATTITUDE = "attitude"
    ADMISSION = "admission"


class Frequency(StrEnum):
    """소통 빈도 — 조합 키의 4번째 축."""

    FREQUENT = "frequent"
    MONTHLY = "monthly"


class LabelSnapshot(BaseModel):
    """DRAFT.label_snapshot — 생성 시 동결되는 4축 열거형(ERD jsonb).

    필드 이름은 `tone_map.yaml`의 `axis_rules` 축 이름과 같다 —
    `combination_key(**snapshot.as_axes())`로 바로 조회된다.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    comm: CommStyle
    sensitivity: Sensitivity
    interest: Interest
    frequency: Frequency

    def as_axes(self) -> dict[str, str]:
        """tone_map 조회용 축 dict — 05 §5 조합 키 `comm.sens.interest.freq`."""
        return {
            "comm": self.comm.value,
            "sensitivity": self.sensitivity.value,
            "interest": self.interest.value,
            "frequency": self.frequency.value,
        }


# ───────────────────────── 초안 입력 (DraftContext) ─────────────────────────


class EvidenceFact(BaseModel):
    """근거 한 줄 — 라벨과 표기값. 표기값 안의 숫자만 게이트 허용집합에 들어간다.

    `composition/briefing_context.EvidenceFact`와 같은 결이며, 상담팩은 계약 타입으로 둔다
    (저장소를 거쳐 워커 밖으로 오가기 때문).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    label: NonEmptyStr
    value: NonEmptyStr

    record_id: str | None = None
    """MySQL 논리 참조(04 §4.2 `interventions[]`·`comm_history[]`가 주는 그것).

    **집계·기준선 파생 fact는 `None`이다** — "평소 정답률(개인 기준선)"·"하락폭"처럼 여러
    기록에서 파생된 값에는 단일 record_id가 없다. 가짜 ID를 지어내지 않는다(억지 매핑 금지).
    ⚠ 그 대가로 **`record_id`가 없는 fact는 강조점 근거로 인용될 수 없다** — 불변식 ①은
    "강조점이 인용하는 근거는 실존해야 한다"이고, 검증은 `cited_record_ids()` 대조로 한다.
    """


class DraftContext(BaseModel):
    """학생 1명의 상담 초안 근거 패키지 — LLM 입력·게이트 허용집합·폴백의 단일 출처.

    ⚠ **워커 state에 넣지 않는다**(§1.2 ⑨) — `context_ref`로 역참조한다.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    student_ref: NonEmptyStr
    """학생 alias — 실명 아님(불변식 3)."""

    guardian_ref: NonEmptyStr
    """보호자 alias — 실명·연락처 아님(불변식 3)."""

    label_snapshot: LabelSnapshot
    """생성 시 동결된 4축(ERD DRAFT.label_snapshot)."""

    facts: tuple[EvidenceFact, ...] = ()
    evidence_summaries: tuple[NonEmptyStr, ...] = ()
    period_label: NonEmptyStr
    """"2026년 7월" 같은 대상 기간 표기 — 프롬프트 문구용."""

    fallback_text: NonEmptyStr
    """게이트 소진·LLM 실패 시 되돌아갈 결정론 템플릿(briefing_context와 동일 역할)."""

    def cited_record_ids(self) -> frozenset[str]:
        """강조점이 **인용할 수 있는** record_id 집합 — record_id가 있는 fact들만.

        plan(LLM)이 낸 강조점의 record_id를 이 집합과 대조해 실존을 검증한다(불변식 ①).
        문자열 패턴 존재 검사로는 날조(`record_id=fake_1`)를 걸러낼 수 없다.
        """
        return frozenset(fact.record_id for fact in self.facts if fact.record_id)

    def allowed_numbers(self) -> frozenset[str]:
        """이 컨텍스트가 실제로 제공한 수치 집합(EXACT). 파생 표기의 숫자만 허용된다."""
        nums: set[str] = set()
        for fact in self.facts:
            nums.update(_NUMBER_RE.findall(fact.value))
        for summary in self.evidence_summaries:
            nums.update(_NUMBER_RE.findall(summary))
        return frozenset(nums)


# ───────────────────────── 초안 산출 (DRAFT·DRAFT_BLOCK) ─────────────────────────


class DraftKind(StrEnum):
    """DRAFT.kind — ERD 값 집합."""

    REPLY = "reply"
    REPORT = "report"
    COUNSEL_PACK = "counsel_pack"


class DraftStatus(StrEnum):
    """DRAFT.status — error_codes §2.1. `template_only`·`rejected_insufficient`는 **정상**이다."""

    GENERATED = "generated"
    TEMPLATE_ONLY = "template_only"
    REJECTED_INSUFFICIENT = "rejected_insufficient"
    FAILED = "failed"


class BlockType(StrEnum):
    """DRAFT_BLOCK.block_type — ERD 값 집합."""

    GREETING = "greeting"
    FACT = "fact"
    SUGGESTION = "suggestion"
    CLOSING = "closing"


class DraftBlock(BaseModel):
    """DRAFT_BLOCK 한 행 — 초안의 블록 1개."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    seq: int = Field(ge=0)
    block_type: BlockType
    content: str
    """비었으면 재시도 소진 섹션 — 초안 전체는 유효(error_codes §2.1 블록 레벨)."""

    regen_count: int = Field(ge=0, le=3)
    """게이트 실패 재생성 횟수 — ERD 주석 "≤3"(불변식 6)."""


class StudentResult(BaseModel):
    """학생 1명의 처리 결과 — `langgraph_state` §1.2 state에 실린다(본문 아님·포인터)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    student_ref: NonEmptyStr
    draft_id: UUID | None = None
    """성공 시에만. 본문은 DRAFT 행에 있고 state는 이 포인터만 든다."""

    status: DraftStatus
    fail_reason: str | None = None


__all__ = [
    "BlockType",
    "CommStyle",
    "DraftBlock",
    "DraftContext",
    "DraftKind",
    "DraftStatus",
    "EvidenceFact",
    "Frequency",
    "Interest",
    "LabelSnapshot",
    "Sensitivity",
    "StudentResult",
]
