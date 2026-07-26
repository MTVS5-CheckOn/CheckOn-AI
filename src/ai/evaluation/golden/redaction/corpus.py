"""redaction failure 코퍼스 — masking_redaction.md §5 (테스트가 곧 산출물).

각 건 = 입력 + 기대 마스킹 결과 + 카테고리 + 사유. 게이트(§5 합격 기준):
- **미탐 0**: `must_absent`(마스킹됐어야 할 원문 조각)가 출력에 남으면 CI 실패.
- **오탐 ≤2**: 문맥 함정(`context_trap`)에서 `must_present`(유지돼야 할 말)가
  마스킹되면 오탐 — 통틀어 ≤2 허용.

**must_absent가 스펙이다 — 손으로 작성·전수 나열한다.** 그 케이스에서 마스킹돼야 할
조각 전부를 나열한다. `expected`는 엔진(runtime/redaction.py) 산출을 고정한 **회귀
고정용**이며 스펙 검증이 아니다(엔진이 바뀌면 expected는 갱신되지만 must_absent는
사람이 정한 불변 안전 기준). 코퍼스는 줄지 않고 누적만(§6).

명부 매칭(P1ⓐ)은 백엔드 1차라 우리(2차)엔 없다 — 성 생략·별명·영문명은 명부 없이
확정 불가하므로 fail-closed로 `⟪확인필요⟫` 처리한다(미탐 0 보장).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class RedactionCase(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: int
    category: str
    text: str
    expected: str
    reason: str
    must_absent: tuple[str, ...] = ()
    """스펙(손 작성): 이 조각들이 출력에 남으면 미탐 — CI 실패."""
    must_present: tuple[str, ...] = ()
    """문맥 함정: 이 말들은 유지돼야 한다 — 마스킹되면 오탐."""
    context_trap: bool = False


CORPUS: tuple[RedactionCase, ...] = (
    # ── 1~6 인명 변형 ──────────────────────────────────────
    RedactionCase(
        id=1,
        category="인명·성생략",
        text="서연이가 요즘 숙제를 자꾸 빼먹어요",
        expected="⟪확인필요⟫가 요즘 숙제를 자꾸 빼먹어요",
        must_absent=("서연",),
        reason="성 없는 이름+유음이+조사 — 명부 없어 fail-closed",
    ),
    RedactionCase(
        id=2,
        category="인명·별명",
        text="막둥이가 학원을 그만두고 싶대요",
        expected="⟪확인필요⟫가 학원을 그만두고 싶대요",
        must_absent=("막둥이",),
        reason="별명(막둥이) 검출 · '그만두고'는 학교 축약 오탐 아님(문맥 게이팅)",
    ),
    RedactionCase(
        id=3,
        category="인명·영문명",
        text="Sarah가 지난주에 결석했어요",
        expected="⟪확인필요⟫가 지난주에 결석했어요",
        must_absent=("Sarah",),
        reason="영문 대문자시작명+조사 — fail-closed",
    ),
    RedactionCase(
        id=4,
        category="인명·형제병기",
        text="서연이랑 동생 서진이도 같이 다녀요",
        expected="⟪확인필요⟫",
        must_absent=("서연", "서진"),
        reason="한 문장 인명 후보 2개 이상 → 밀도 기준으로 문장 통째",
    ),
    RedactionCase(
        id=5,
        category="인명·강사본인",
        text="저는 국어 담당 박민수 쌤이에요",
        expected="저는 국어 담당 ⟪이름1⟫ 쌤이에요",
        must_absent=("박민수",),
        reason="호칭(쌤) 결합 — confident ⟪이름N⟫",
    ),
    RedactionCase(
        id=6,
        category="인명·타학원강사",
        text="옆 학원 김철수 선생님이 스카웃하려 한대요",
        expected="옆 학원 ⟪이름1⟫ 선생님이 스카웃하려 한대요",
        must_absent=("김철수",),
        reason="호칭(선생님) 결합",
    ),
    # ── 7~12 연락처 변형 ───────────────────────────────────
    RedactionCase(
        id=7,
        category="연락처·하이픈없음",
        text="01012345678로 문자 주세요",
        expected="⟪연락처1⟫로 문자 주세요",
        must_absent=("01012345678", "1234", "5678"),
        reason="P2 하이픈 없는 11자리",
    ),
    RedactionCase(
        id=8,
        category="연락처·공백삽입",
        text="0 1 0 - 1 2 3 4 - 5 6 7 8 이 제 번호예요",
        expected="⟪연락처1⟫ 제 번호예요",
        must_absent=("1 2 3 4", "5 6 7 8"),
        reason="P8 공백·특수문자 제거 후 P2 재적용",
    ),
    RedactionCase(
        id=9,
        category="연락처·공일공",
        text="제 번호는 공일공일이삼사오육칠팔이에요",
        expected="제 번호는 ⟪연락처1⟫에요",
        must_absent=("공일공", "이삼사", "오육칠팔"),
        reason="P8 한글숫자 디코딩(공영일이삼사오육칠팔구) 후 P2",
    ),
    RedactionCase(
        id=10,
        category="연락처·국가번호",
        text="+82 10 1234 5678 로 연락 가능해요",
        expected="+⟪연락처1⟫ 로 연락 가능해요",
        must_absent=("1234", "5678"),
        reason="P8 공백 제거 후 P2 — 국가번호 접두는 남아도 번호는 마스킹",
    ),
    RedactionCase(
        id=11,
        category="연락처·지역번호",
        text="학원 대표번호 02-123-4567로 문의주세요",
        expected="학원 대표번호 ⟪연락처1⟫로 문의주세요",
        must_absent=("123-4567", "4567"),
        reason="P2 지역번호(0\\d{1,2}) 형식",
    ),
    RedactionCase(
        id=12,
        category="연락처·카톡ID",
        text="카톡 아이디 sy_mom123으로 연락주세요",
        expected="카톡 아이디 ⟪확인필요⟫으로 연락주세요",
        must_absent=("sy_mom123",),
        reason="P9 카톡 문맥어 인접 영숫자 토큰 → fail-closed(값 확정 아님)",
    ),
    # ── 13~17 소속 노출 ────────────────────────────────────
    RedactionCase(
        id=13,
        category="소속·학교축약",
        text="일산중 2학년이에요",
        expected="⟪학교1⟫ 2학년이에요",
        must_absent=("일산중",),
        reason="P5 축약형 + 학교 문맥(학년) 게이팅",
    ),
    RedactionCase(
        id=14,
        category="소속·학교반번호",
        text="한빛고등학교 2학년 3반 15번이에요",
        expected="⟪학교1⟫ 2학년 3반 15번이에요",
        must_absent=("한빛고등학교", "한빛고"),
        reason="P5 정식 학교명 — 반·번호는 인명 없이 비식별",
    ),
    RedactionCase(
        id=15,
        category="소속·아파트",
        text="서울시 강남구 삼성동 101동 302호에 살아요",
        expected="서울⟪주소1⟫에 살아요",
        must_absent=("강남구", "삼성동", "101동", "302호"),
        reason="P4 시·구·동 + 상세(동·호) — 시 접두 잔여는 비식별",
    ),
    RedactionCase(
        id=16,
        category="소속·지역",
        text="서울시 강남구 대치동에서 다녔어요",
        expected="서울⟪주소1⟫에서 다녔어요",
        must_absent=("강남구", "대치동"),
        reason="P4 시·구·동",
    ),
    RedactionCase(
        id=17,
        category="소속·기관+인명",
        text="새중앙교회 청년부 김지훈 학생이에요",
        expected="⟪확인필요⟫교회 청년부 ⟪이름1⟫ 학생이에요",
        must_absent=("새중앙교회", "김지훈"),
        reason="P9 기관명(교회 직전 고유명사) + 호칭(학생) 인명 동시",
    ),
    # ── 18~22 문서 유입형 ──────────────────────────────────
    RedactionCase(
        id=18,
        category="문서·엑셀셀",
        text="학생명 김서연 학생, 연락처 010-9876-5432",
        expected="학생명 ⟪이름1⟫ 학생, 연락처 ⟪연락처1⟫",
        must_absent=("김서연", "9876", "5432"),
        reason="셀 안 인명(호칭 결합) + 연락처 동시",
    ),
    RedactionCase(
        id=19,
        category="문서·비고란",
        text="비고: 엄마 010-1111-2222",
        expected="비고: 엄마 ⟪연락처1⟫",
        must_absent=("1111", "2222"),
        reason="비고 메모 속 연락처",
    ),
    RedactionCase(
        id=20,
        category="문서·헤더담임",
        text="담임 이영희 선생님, 3반",
        expected="담임 ⟪이름1⟫ 선생님, 3반",
        must_absent=("이영희",),
        reason="헤더 담임 실명(호칭 결합)",
    ),
    RedactionCase(
        id=21,
        category="문서·파일명실명",
        text="김서연_성적표.xlsx 파일 보냈어요",
        expected="⟪확인필요⟫_⟪확인필요⟫.xlsx 파일 보냈어요",
        must_absent=("김서연",),
        reason="P9 파일명 — 확장자 토큰 속 한글 조각 → fail-closed(성적표도 과잉 감수)",
    ),
    RedactionCase(
        id=22,
        category="문서·주소열",
        text="주소 서울시 강남구 대치동 105동 201호",
        expected="주소 서울⟪주소1⟫",
        must_absent=("강남구", "대치동", "105동", "201호"),
        reason="주소 열 — 시·구·동 + 상세",
    ),
    # ── 23~26 문맥 함정(오탐 검증 — 유지돼야 함) ────────────
    RedactionCase(
        id=23,
        category="함정·작가명",
        text="김유정 소설을 이번 주에 다뤘어요",
        expected="김유정 소설을 이번 주에 다뤘어요",
        must_present=("김유정", "소설"),
        context_trap=True,
        reason="교과 작가명 — 화이트리스트/비후보(조사 없음)로 유지",
    ),
    RedactionCase(
        id=24,
        category="함정·작가명",
        text="이상 문학은 학생들이 어려워해요",
        expected="이상 문학은 학생들이 어려워해요",
        must_present=("이상", "문학", "학생들"),
        context_trap=True,
        reason="작가명(이상) 유지 · '학생들'은 호칭 아님(학생(?!들))",
    ),
    RedactionCase(
        id=25,
        category="함정·최고",
        text="우리 반이 이번에 최고 성적이었어요",
        expected="우리 반이 이번에 최고 성적이었어요",
        must_present=("최고",),
        context_trap=True,
        reason="'최고' ≠ 학교 — 축약 화이트리스트",
    ),
    RedactionCase(
        id=26,
        category="함정·작품인물",
        text="봄봄에서 점순이가 데릴사위를 부리는 장면이요",
        expected="봄봄에서 점순이가 데릴사위를 부리는 장면이요",
        must_present=("점순",),
        context_trap=True,
        reason="문학 작품 인명 화이트리스트(점순) — 조사 결합에도 유지",
    ),
    # ── 27~30 복합·우회 ────────────────────────────────────
    RedactionCase(
        id=27,
        category="복합·인명+연락처",
        text="서연이 어머니 연락처는 010-2222-3333이에요",
        expected="⟪이름1⟫ 어머니 연락처는 ⟪연락처1⟫이에요",
        must_absent=("서연", "2222", "3333"),
        reason="호칭(어머니) 인명 + 연락처 한 문장",
    ),
    RedactionCase(
        id=28,
        category="복합·지시문실명",
        text="서연이 엄마한테 보낼 문자인데 정중하게 써주세요",
        expected="⟪확인필요⟫ 엄마한테 보낼 문자인데 정중하게 써주세요",
        must_absent=("서연",),
        reason="지시문 안 실명 — 이름+이+관계어(엄마)로 검출",
    ),
    RedactionCase(
        id=29,
        category="복합·인용중첩",
        text='어머니가 "우리 애가 010-3333-4444로 연락달래요"라고 하셨어요',
        expected='어머니가 "우리 애가 ⟪연락처1⟫로 연락달래요"라고 하셨어요',
        must_absent=("3333", "4444"),
        reason="인용문 안 연락처도 마스킹 · '우리 애'는 인명 아님",
    ),
    RedactionCase(
        id=30,
        category="복합·이모지",
        text="서연이가 😢 어제 010-5555-6666으로 전화했어요",
        expected="⟪확인필요⟫가 😢 어제 ⟪연락처1⟫으로 전화했어요",
        must_absent=("서연", "5555", "6666"),
        reason="이모지 섞여도 인명·연락처 각각 검출",
    ),
    # ── 31~33 누적(§6) — 감사 정정 반영 ────────────────────
    # 31·32는 복원 과정에서 대체된 '잡히는 형태' 변형(삭제 않고 이동). 33은 §5
    # '동아리명' 커버가 코퍼스에 없어 채운 P9 누적(17은 텍스트 변형이 아니라 언더마스킹).
    RedactionCase(
        id=31,
        category="연락처·카톡언급(변형)",
        text="카톡 말고 010-7777-8888로 연락주세요",
        expected="카톡 말고 ⟪연락처1⟫로 연락주세요",
        must_absent=("7777", "8888"),
        reason="구 12번 변형 — 카톡 언급 + 전화(P2). 순수 ID는 12번(P9)이 담당",
    ),
    RedactionCase(
        id=32,
        category="문서·파일내용(변형)",
        text="명단 파일에 서연이랑 민준이가 있어요",
        expected="⟪확인필요⟫",
        must_absent=("서연", "민준"),
        reason="구 21번 변형 — 명단 유입 실명 2개 → 밀도 문장 통째",
    ),
    RedactionCase(
        id=33,
        category="소속·동아리(P9)",
        text="미술동아리 한지민 학생이에요",
        expected="⟪확인필요⟫동아리 ⟪이름1⟫ 학생이에요",
        must_absent=("한지민",),
        reason="P9 기관명(동아리 직전 고유명사) + 호칭 인명 · '동아리'의 동은 주소 오탐 아님",
    ),
    # ── 34 문맥 함정 추가(교육 도메인 동-단어 오탐 검증) ────
    RedactionCase(
        id=34,
        category="함정·동단어",
        text="아동 심리 상담을 권해드려요",
        expected="아동 심리 상담을 권해드려요",
        must_present=("아동",),
        context_trap=True,
        reason="'아동'은 [가-힣]{2,3}동에 비매칭(표준 2음절) + endswith exclude로 복합어도 방어",
    ),
)
