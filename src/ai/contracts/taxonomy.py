"""수능 국어 영역·문항 형식 공용 어휘.

사양 원본: docs/policies/taxonomy.md (어휘집 v1 — 2026-08-09 B 확정: 출제 5영역 · v1=mcq만)
소유: [A+B] 양자 승인 — 변경 시 두 명 승인 필수 (docs/02_ownership.md §4)

소비처가 4곳이라 동시 개정이 필요하다 (어휘집 §5):
감지 R6(area×type 정답률) · 약점 지도(B) · 태깅 제안ⓒ(A) · 출제/진단 그래프(B).
문자열 리터럴을 산재시키지 말고 항상 이 enum을 참조한다 (03_coding_rules.md §1).
"""

from enum import StrEnum
from typing import Final

from pydantic import BaseModel, ConfigDict


class AreaTag(StrEnum):
    """수능 국어 **출제 5영역** — 어휘집 §1 (2026-08-09 B 확정 개정).

    태깅 원칙(어휘집 §2): ① 세트가 아닌 문항 단위 ② 지문 소재가 아니라
    측정 대상(무엇을 묻나) 기준 ③ 애매하면 confidence를 낮춰 강사 확정에 맡긴다.

    🔴 **축이 「과목」이 아니라 「출제 단위」다.** 종전 6영역은 과목 편제를 따라
    `speech`·`writing`을 갈라 두고 `language`·`media`도 갈라 뒀는데, **그 두 쌍의
    사정이 정반대다.**

    · **언어와 매체** — `language`·`media`를 **그대로 둘로 둔다.** 문항 형태가 완전히
      달라서다: 언어는 규칙 적용, 매체는 매체 자료의 수용·생산이라 **같은 출제 규격으로
      만들 수 없다.**
    · **화법과 작문** — `speech`·`writing`을 **`speech_writing` 하나로 합친다.** 담화
      상황과 글쓰기 상황이 *"토론 후 글쓰기"* 처럼 **한 세트로 출제**되고, 문항 단위로
      갈라도 발문·선지 규격이 같다.

    ⚠ **「같은 선택과목이니 묶는다」가 아니다** — 그 기준이면 언어·매체도 묶어야 한다.
    기준은 **"같은 출제 규격으로 문항을 만들 수 있는가"** 하나다(`data/area_specs.yaml`이
    그 규격의 정본이고 영역마다 한 항목이다).

    ⚠ **이 순서가 표시 순서다** — 강사 화면·요청 파라미터의 기본 나열이 이 순서를 따른다.
    """

    LANGUAGE = "language"
    """언어(문법) — 음운·단어·문장·담화·국어사. 선택 「언어와 매체」."""

    MEDIA = "media"
    """매체 — 매체 자료의 수용·생산(인터넷·방송 등). 선택 「언어와 매체」."""

    LITERATURE = "literature"
    """문학 — 현대시·현대소설·고전시가·고전산문·극/수필. 공통."""

    READING = "reading"
    """독서 — 비문학 지문 독해(인문·사회·과학·기술·예술·융합). 공통."""

    SPEECH_WRITING = "speech_writing"
    """화법과작문 — 담화(발표·토론·협상) + 글쓰기(계획·자료 활용·고쳐쓰기). 선택.

    🔴 **종전 `speech`·`writing` 둘을 대체한다**(2026-08-09). 실제 시험이 *"토론 후
    글쓰기"* 처럼 **한 세트로 출제**하고, 문항 단위로 갈라도 발문·선지 규격이 같아
    두 값을 유지할 근거가 출제 축에 없었다.
    ⚠ **과거 데이터의 `speech`·`writing`은 이 값으로 읽지 않는다** — 값 매핑을 코드에
    두지 않았다. 학습 이벤트에 그 값이 흘러오면 검증에서 걸린다(조용히 접지 않는다).
    """


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
    """인지 유형 — 어휘집 §3. area와 직교하며 모든 area와 조합 가능.

    🔴 **평가원 5축을 계약에 담되 v1 산출은 4종이다** — `APPLY`는 **어휘 예약**(99 ㊣).

    ⚠ `item_format`의 `short`·`essay`와 같은 예약이지만 **처방이 다르다.**
    `item_format`은 요청 파라미터라 우리가 만들지 않지만 `type_tag`는 **여러 문으로
    들어온다.** 문마다 답이 다르다(8/8 전수 실측):

      · **산출**(우리가 만든다) — **안 낸다.** 골든셋에 `apply` 표본이 없어 검증 불가(㊛).
        ⚠ 실측상 **지금은 산출하는 자리 자체가 없다** — `composition/classify/`에 `TypeTag`
        참조가 **0건**이고, 태그 제안(`kind: tag`)은 400 `kind_not_implemented`다
      · **출제 요청**(BE → `ProblemRequest.type_tags`) — **400 `type_tag_not_supported`.**
        주체 3분할상 호출자가 요청을 고쳐야 한다(`source_procurement_not_implemented` 선례).
        ✅ **구현됨(8/9 · B #147)** — `enqueue.py::reject_unsupported_type_tags()`가
        요청 레코드·`WorkerJob` 생성보다 **앞**에서 끊어 **잡을 만들지 않는다**
        (실측: 400 · 잡 0건 · AI_RUN 0건). ⚠ **언제 참이 됐는지를 남긴다** — 지우기만
        하면 다음 사람이 *"원래 이랬나"* 를 다시 판단한다.
      · **학습 이벤트**(BE → `LearningEvent.type_tag`) — 🔴 **받는다.** 강사가 매긴 태그가
        흘러온 것이고 **우리 v1 범위와 무관한 사실**이다. 막으면 구현 범위 때문에 데이터를
        왜곡하고(㉡·㊢ 부류) ㊛의 재료를 만들 경로를 닫는다
      · **완전성 검사**(설정 · `difficulty_weights`) — 예약 태그는 **가중치를 안 갖는다**
      · **표시 라벨**(`_TYPE_KO`) — 🔴 **갖는다.** 학습 이벤트를 받는 이상 R6 브리핑까지
        흘러가고(`LearningEvent` → `features.cells` → `_r6_facts`), 라벨이 없으면
        `.get()` 폴백이 **enum 값(영문)** 이라 *"문학·apply"* 가 학부모 문장에 섞인다

    🔴 **문마다 답이 다른 것이 이 예약의 전부다** — 하나로 뭉치면 어느 하나를 잘못 막는다.
    **입력은 받고 출력은 안 하되, 받은 것은 사람이 읽을 수 있게 표시한다.**

    **여는 조건:** ㊛ 태깅 골든셋에 `apply` 표본이 쌓이고 baseline이 정해질 때.
    """

    FACT = "fact"
    """사실 확인."""

    INFER = "infer"
    """추론 — 예: literature × infer(화자 정서 추론)."""

    CRITIC = "critic"
    """비판·평가."""

    CONCEPT = "concept"
    """개념·지식 — 예: language × concept(음운 규칙 지식)."""

    APPLY = "apply"
    """적용·창의 — 🔴 **v1 미산출 · 어휘 예약.** 표시 라벨은 갖는다.

    🔴 **출제 요청 미수용** — ✅ **구현됨**(8/9 · B #147 ·
    `enqueue.py::reject_unsupported_type_tags()`). 400 `type_tag_not_supported`이고
    **잡을 만들지 않는다**(거절이 요청 레코드 생성보다 앞이다).

    ⚠ **(8/7~8/9 이력) 이 자리가 한동안 단정형이었다** — 구현 전인데 *"미수용"* 이라고만
    적어 **구현된 것으로 읽혔고**, ㊩ 규칙을 만든 PR이 그 규칙을 어긴 자리였다.
    8/7에 「약속 vs 현재」를 병기했고 8/9에 구현이 들어와 병기를 걷었다. **판단을 남기는
    이유는 이 자리에 또 단정형을 쓰지 않게 하기 위해서다** — *미구현 계약은 약속한 동작과
    현재 동작을 함께 적는다*(99 ㊩).

    ⚠ **맨 뒤에 둔다** — 앞 넷의 값·순서는 DB·골든셋·설정 YAML에 문자열로 살아 있다.
    """


#: 🔴 **예약을 명시하고 v1을 뺄셈으로 유도한다.** 반대로 4종을 나열하면 새 값이 생길 때
#: **조용히 지원 목록에 안 들어가고**(fail-open) 아무도 모른다 — 이 저장소가 목록형으로
#: 아홉 번 당한 그 형태다(로그 67). 뺄셈이면 새 값은 기본이 **「지원」** 이고, 예약하려면
#: **여기 명시적으로 적어야** 한다.
RESERVED_TYPE_TAGS: Final = frozenset({TypeTag.APPLY})

#: v1이 실제로 산출·수용하는 축. 🔴 **소비처가 이것 하나를 참조한다** — 4종 목록을 여러
#: 곳에 복제하면 한 곳만 고쳐진다(`test_type_tag_reservation`의 AST 가드가 잡는다).
#: ⚠ **표시 라벨은 이 집합이 아니라 `TypeTag` 전체를 덮어야 한다** — 예약도 표시된다.
V1_TYPE_TAGS: Final = frozenset(TypeTag) - RESERVED_TYPE_TAGS


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
