"""수능 국어 영역·문항 형식 공용 어휘.

사양 원본: docs/policies/taxonomy.md (어휘집 v0 — 7/15 확정: 6영역 · v1=mcq만)
소유: [A+B] 양자 승인 — 변경 시 두 명 승인 필수 (docs/02_ownership.md §4)

소비처가 4곳이라 동시 개정이 필요하다 (어휘집 §5):
감지 R6(area×type 정답률) · 약점 지도(B) · 태깅 제안ⓒ(A) · 출제/진단 그래프(B).
문자열 리터럴을 산재시키지 말고 항상 이 enum을 참조한다 (03_coding_rules.md §1).
"""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class AreaTag(StrEnum):
    """수능 국어 6영역 — 어휘집 §1.

    태깅 원칙(어휘집 §2): ① 세트가 아닌 문항 단위 ② 지문 소재가 아니라
    측정 대상(무엇을 묻나) 기준 ③ 애매하면 confidence를 낮춰 강사 확정에 맡긴다.
    """

    READING = "reading"
    """독서 — 비문학 지문 독해(인문·사회·과학·기술·예술·융합). 공통."""

    LITERATURE = "literature"
    """문학 — 현대시·현대소설·고전시가·고전산문·극/수필. 공통."""

    SPEECH = "speech"
    """화법 — 발표·토론·협상 등 담화 상황. 선택 「화법과 작문」."""

    WRITING = "writing"
    """작문 — 글쓰기 계획·초고 수정·자료 활용. 선택 「화법과 작문」."""

    LANGUAGE = "language"
    """언어(문법) — 음운·단어·문장·담화·국어사. 선택 「언어와 매체」."""

    MEDIA = "media"
    """매체 — 매체 자료의 수용·생산(인터넷·방송 등). 선택 「언어와 매체」."""


class SubjectTrack(StrEnum):
    """수능 체제 구분 — 어휘집 §1.

    영역값에서 유도 가능하나(독서·문학=공통), 선택 체제가 아닌 학생의 데이터를
    구분하기 위해 별도 필드로 유지한다.
    """

    COMMON = "common"
    """공통 — 독서·문학."""

    ELECTIVE = "elective"
    """선택과목 — 화법과 작문 / 언어와 매체."""


class TypeTag(StrEnum):
    """인지 유형 — 어휘집 §3. area와 직교하며 모든 area와 조합 가능."""

    FACT = "fact"
    """사실 확인."""

    INFER = "infer"
    """추론 — 예: literature × infer(화자 정서 추론)."""

    CRITIC = "critic"
    """비판·평가."""

    CONCEPT = "concept"
    """개념·지식 — 예: language × concept(음운 규칙 지식)."""


class ItemFormat(StrEnum):
    """문항 형식 — 어휘집 §4.

    v1은 MCQ만 사용한다 (7/15 확정 — 수능 국어는 전 문항 객관식).
    SHORT·ESSAY는 enum 자리만 예약한 값이다. 내신·자체 시험 대비는 타겟 고도화
    단계의 사양이므로, **이 두 값에 대한 처리 분기 코드를 만들면 안 된다**
    (CLAUDE.md §3 — 채점·루브릭·유사 답안 로직 일체 금지).
    예약값을 소비하려면 어휘집 §4와 F17 재론이 선행되어야 한다.
    """

    MCQ = "mcq"
    """객관식(5지선다) — 자동 채점(정오). AI 관여 없음, 정오만 피처로."""

    SHORT = "short"
    """단답형 — **예약값. v1 미사용 · 처리 분기 금지.**"""

    ESSAY = "essay"
    """서술형 — **예약값. v1 미사용 · 처리 분기 금지.**"""


#: v1에서 실제로 사용하는 문항 형식 (어휘집 §4 — 7/15 확정).
#: 예약값을 걸러내는 검증용. 분기 로직이 아니라 "쓰지 않는다"의 표현이다.
SUPPORTED_ITEM_FORMATS: frozenset[ItemFormat] = frozenset({ItemFormat.MCQ})

#: 공통 과목에 속하는 영역 (어휘집 §1) — subject_track 유도의 기준.
COMMON_AREAS: frozenset[AreaTag] = frozenset({AreaTag.READING, AreaTag.LITERATURE})


def derive_subject_track(area: AreaTag) -> SubjectTrack:
    """영역에서 수능 체제를 유도한다 (어휘집 §1).

    스냅숏에 subject_track이 없을 때의 기본값 산출용. 선택 체제가 아닌 학생은
    이 유도값이 맞지 않을 수 있으므로, 원본 값이 있으면 그쪽이 우선한다.
    """
    return SubjectTrack.COMMON if area in COMMON_AREAS else SubjectTrack.ELECTIVE


class ItemTags(BaseModel):
    """문항 1개의 태그 묶음 — 태깅 제안ⓒ·출제·R6 집계의 공용 단위.

    R6 집계 축은 area × type 기본이며 item_format은 분리 리포팅만 한다
    (어휘집 §4 — 6×4×3=72셀로 쪼개면 학생당 표본 부족).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    area: AreaTag
    type: TypeTag
    item_format: ItemFormat = ItemFormat.MCQ
    subject_track: SubjectTrack | None = None
    """None이면 area에서 유도한다 (derive_subject_track)."""
