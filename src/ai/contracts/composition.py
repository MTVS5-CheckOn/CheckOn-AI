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

#: 표기값에서 수치를 뽑는 정규식 — 게이트 EXACT 대조용.
_NUMBER_RE: Final = _re_compile(r"\d+")

#: 자릿수 구분 기호 — `1,240`의 쉼표만 지운다(**숫자 사이**에 낀 것만).
#: ⚠ 모든 쉼표를 지우면 `"62%, 71%"`가 `6271`로 붙어 **없던 수치가 생긴다.**
_DIGIT_SEPARATOR_RE: Final = _re_compile(r"(?<=\d),(?=\d)")


def extract_numbers(text: str) -> set[str]:
    """표기에서 수치를 뽑는다 — **게이트와 허용집합이 같은 함수를 쓴다.**

    🔴 정규화를 한쪽에만 걸면 방향이 반대일 때 그대로 뚫린다. `\\d+`는 쉼표에서 끊기므로
    종전에는 같은 값이 표기만 달라도 막혔다:

    | 근거 | 본문 | 종전 |
    | --- | --- | --- |
    | `출석 1,240회` → `{1, 240}` | `1240회` → `{1240}` | ❌ `ungrounded_number:1240` |
    | `1240` → `{1240}` | `1,240` → `{1, 240}` | ❌ `ungrounded_number:1` |

    허용집합 쪽만 넓히면 두 번째 방향을 못 고치고(본문 조각을 허용해야 해서 **창작 수치가
    새는** 방향으로 느슨해진다), 그래서 **추출 자체**를 양쪽에서 같게 만든다.

    ⚠ 소수점(`3.5`)은 그대로 두 조각으로 남는다 — 지금 동작과 같고, 양쪽이 같은 규칙이라
    비대칭이 생기지 않는다.
    """
    return set(_NUMBER_RE.findall(_DIGIT_SEPARATOR_RE.sub("", text)))


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
    """백엔드 DB 논리 참조(04 §4.2 `interventions[]`·`comm_history[]`가 주는 그것).

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

    inquiry_text: str = ""
    """🔴 **학부모가 실제로 물은 것**(BE 1차 마스킹분 `inquiry.text_masked`).

    종전에는 이 값을 아무도 읽지 않아, 같은 학생·같은 라벨이면 *"성적이 왜 떨어졌나요"* 와
    *"숙제 줄여주세요"* 가 **바이트 동일한 프롬프트**를 만들었다 — 답장이 질문과 무관해도
    구조상 알 수 없었다.

    ⚠ **BE의 1차 마스킹을 믿지 않는다** — 프롬프트 조립 뒤 `redact()`를 한 번 더 거치고
    `uncertain`이면 전송하지 않는다(fail-closed · 불변식 3 · `GatewayDraftWriter`).
    ⚠ 기본값이 빈 문자열이라 **안 넘기면 프롬프트가 종전과 같은 자리에 머문다.**
    """

    def cited_record_ids(self) -> frozenset[str]:
        """강조점이 **인용할 수 있는** record_id 집합 — record_id가 있는 fact들만.

        plan(LLM)이 낸 강조점의 record_id를 이 집합과 대조해 실존을 검증한다(불변식 ①).
        문자열 패턴 존재 검사로는 날조(`record_id=fake_1`)를 걸러낼 수 없다.
        """
        return frozenset(fact.record_id for fact in self.facts if fact.record_id)

    def allowed_numbers(self) -> frozenset[str]:
        """이 컨텍스트가 실제로 제공한 수치 집합(EXACT) — 05 §6-1.

        출처는 셋이다: `facts` · `evidence_summaries` · **`period_label`**.
        기간 표기를 넣는 이유는 프롬프트가 그것을 **쓰라고 지시하기 때문**이다
        (`counsel_pack.txt`의 "{period_label} 상담 초안을 작성하세요"). 지시대로
        "2026년 7월"을 되뇐 문장이 `ungrounded_number:2026`으로 거부되면 게이트가
        정상 문장을 막는다(99 D ㉘). `period_label`은 스냅숏 대상 기간의 결정론
        파생이라 `facts`의 수치와 같은 지위이지 LLM이 만든 숫자가 아니다.

        ⚠ **`fact.label`도 본다.** 프롬프트가 `- {label}: {value}` 형태로 label을 그대로
        싣기 때문이다(`prompt.render_evidence_block`) — `period_label`을 넣은 것과 **같은
        근거**다. label이 `"7월 3주차 정답률"`이면 지시대로 쓴 문장이
        `ungrounded_number:3`으로 막힌다. label은 백엔드 스냅숏의 결정론 파생이지 LLM이
        만든 숫자가 아니다.

        🔴 **`inquiry_text`는 넣지 않는다.** 학부모 문의는 **근거가 아니다** — *"지난번
        80점이라고 하셨는데"* 의 80은 우리 기록에 없는 수치이고, 허용하면 LLM이 그걸
        근거인 것처럼 되받아 쓴다(불변식 2). 프롬프트도 "문의에 있는 숫자를 그대로 쓰지
        마라"고 지시한다 — 지시와 게이트가 같은 방향이어야 한다.

        어느 출처에도 없는 숫자는 그대로 거부된다 — 환산·창작 수치 차단(불변식 1·2)은
        불변이다.
        """
        nums: set[str] = set()
        for fact in self.facts:
            nums |= extract_numbers(fact.label)
            nums |= extract_numbers(fact.value)
        for summary in self.evidence_summaries:
            nums |= extract_numbers(summary)
        nums |= extract_numbers(self.period_label)
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
    "extract_numbers",
    "Frequency",
    "Interest",
    "LabelSnapshot",
    "Sensitivity",
    "StudentResult",
]
