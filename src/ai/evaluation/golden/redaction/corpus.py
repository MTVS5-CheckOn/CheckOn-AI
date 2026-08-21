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

🔴 **(8/6) 같은 사각지대가 한 번 더 났다 — 이번엔 "조사 없음"에 편중돼 있었다.**
44·45는 `⟪이름1⟫ 학생 어머니`(조사 없음)만 봤고, 그래서 호칭어 필터가 **정확 일치**라
`학생의`·`학생을`이 우회하는 것이 통과했다. "○○ 학생**의** 어머니입니다"는 학부모 문의의
**전형 문면**이라 실제로 터졌다(2차 redact에서 새 finding → 트립와이어 차단 → 불필요한
폴백). ⇒ 형태 축은 **조사 유무까지** 갈라 적는다. 그리고 개별 형태를 쫓는 대신
**멱등성이라는 성질**을 코퍼스 전건에 테스트로 걸었다(`test_redaction_idempotence.py`) —
다음 형태에서도 자동으로 물린다.

🔴 **형태 커버리지 표 — 이 표가 없어서 미탐이 났다(8/5).**
종전 코퍼스는 **별명형(`~이`+조사)에 편중**돼 있었고, 그래서 "미탐 0" 게이트가 통과하는
동안 **성이 붙은 정식 성명형이 통째로 새고** 있었다(`김민준이`·`박서연은`·`이도윤의` 등
실측 7건). 게이트가 통과한 방식 자체가 구멍이 안 보인 이유였다. 새 형태를 추가할 때는
**이 표를 먼저 갱신**한다 — 어떤 형태를 덮고 있는지 보이지 않으면 같은 사각지대가 또 생긴다.

| 형태 | 예 | 케이스 |
| --- | --- | --- |
| 성 생략 + 유음이 + 조사 | 서연이가 | 1 |
| 성 생략 + 유음이 + 관계어 | 서연이 엄마 | 28 |
| 영문명 + 조사 | Sarah가 | 3 |
| **성+이름 3자 + 주격 이/가** | 김민준이 | **35·36** |
| **성+이름 3자 + 보조사 은/는** | 박서연은 | **37·38** |
| **성+이름 3자 + 목적격 을/를** | 이도윤을 | **39·40** |
| **성+이름 3자 + 관형격 의** | 최지우의 | **41** |
| **성+이름 3자 + 공동격 와/과** | 정하윤과 | **42** |
| **마스킹 후 잔여 재검출** — 조사 없음 | ⟪이름1⟫ 학생 어머니 | **44·45** |
| **마스킹 산출물 재입력** — 호칭어 + 조사 | ⟪이름1⟫ 학생**의** 어머니 | **50~53** |
| **마스킹 산출물 재입력** — 유형 다른 토큰 + 실명 | ⟪연락처1⟫ 서연 어머니 | **54** |
| **원문 → 1차 → 2차 비멱등** | 김민준 학생**의** 어머니 | **55** |
| ⚠ 성+이름 **2자** | 김철이 결석했어요 | **미커버**(99 등재) |
| 호칭 결합(P1ⓑ) | 민준이 어머니 | 2 |
| 함정 — 성씨 겹침 한자어 | 성적이·문의를 | **46~49** |
| 함정 — 동 단어 | 아동 심리 | 34 |
| 함정 — 작가명 | 김유정 | 24 |
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
    # ── 35~45 성명형(성+이름) — 🔴 8/5 미탐 실측분을 그대로 등재 ────
    # 종전 코퍼스가 별명형에 편중돼 이 형태 전체가 사각지대였다. 배경 실측 7건이
    # 그대로 케이스가 된다 — "게이트가 통과했는데 새고 있었다"의 재발 방지다.
    RedactionCase(
        id=35,
        category="성명형·주격이",
        text="김민준이 요즘 힘들어합니다",
        expected="⟪확인필요⟫이 요즘 힘들어합니다",
        must_absent=("김민준",),
        reason="성+이름 3자 + 주격 '이' — 종전 패턴은 '~이'로 끝나는 것만 잡아 미탐",
    ),
    RedactionCase(
        id=36,
        category="성명형·주격가",
        text="박서연이 숙제를 안 해요",
        expected="⟪확인필요⟫이 숙제를 안 해요",
        must_absent=("박서연",),
        reason="성+이름 3자 + 주격 — 성씨 유한 집합 패턴으로 검출",
    ),
    RedactionCase(
        id=37,
        category="성명형·보조사은",
        text="김민준은 요즘 힘들어합니다",
        expected="⟪확인필요⟫은 요즘 힘들어합니다",
        must_absent=("김민준",),
        reason="보조사 '은'이 종전 조사 목록에 없었다(구현 갭)",
    ),
    RedactionCase(
        id=38,
        category="성명형·보조사는",
        text="정하윤는 발표를 잘합니다",
        expected="⟪확인필요⟫는 발표를 잘합니다",
        must_absent=("정하윤",),
        reason="보조사 '는' — 성씨 패턴 경로",
    ),
    RedactionCase(
        id=39,
        category="성명형·목적격을",
        text="김민준을 잘 부탁드립니다",
        expected="⟪확인필요⟫을 잘 부탁드립니다",
        must_absent=("김민준",),
        reason="목적격 '을'이 종전 조사 목록에 없었다",
    ),
    RedactionCase(
        id=40,
        category="성명형·목적격를",
        text="이도윤를 챙겨주세요",
        expected="⟪확인필요⟫를 챙겨주세요",
        must_absent=("이도윤",),
        reason="목적격 '를' — 성씨 패턴 경로",
    ),
    RedactionCase(
        id=41,
        category="성명형·관형격의",
        text="최지우의 발표가 좋았어요",
        expected="⟪확인필요⟫의 발표가 좋았어요",
        must_absent=("최지우",),
        reason="관형격 '의'가 종전 조사 목록에 없었다",
    ),
    RedactionCase(
        id=42,
        category="성명형·공동격",
        text="정하윤과 상담하고 싶어요",
        expected="⟪확인필요⟫과 상담하고 싶어요",
        must_absent=("정하윤",),
        reason="공동격 '과'가 종전 조사 목록에 없었다",
    ),
    RedactionCase(
        id=44,
        category="잔여·재검출",
        text="김민준 학생 어머니입니다",
        expected="⟪이름1⟫ 학생 어머니입니다",
        must_absent=("김민준",),
        reason=(
            "🔴 호칭 결합으로 이름이 마스킹된 뒤 **남은 '학생'이 다시 인명 후보가 되던** "
            "버그(B). 호칭어 자기검출 스킵 + 토큰 인접 스킵으로 막는다 — 안 막으면 "
            "트립와이어가 전송을 차단해 불필요한 폴백이 난다"
        ),
    ),
    RedactionCase(
        id=45,
        category="잔여·재검출",
        text="박서연 학생 아버님께 전해주세요",
        expected="⟪이름1⟫ 학생 아버님께 전해주세요",
        must_absent=("박서연",),
        reason="호칭 2개(학생·아버님) 연속 — 잔여 어절이 후보로 재검출되지 않는다",
    ),
    # ── 46~49 함정: 성씨와 첫 글자가 겹치는 학원 도메인 어휘 ──
    # 성씨를 유한 집합으로 좁혀도 '성'적·'문'의·'정'도·'강'의는 겹친다.
    # 오탐 ≤2 허용치를 쓰되, 넘으면 name_exclude에 **실측된 것만** 넣는다.
    RedactionCase(
        id=46,
        category="함정·성씨겹침",
        text="성적이 떨어졌어요",
        expected="성적이 떨어졌어요",
        must_present=("성적",),
        context_trap=True,
        reason="'성'이 성씨라 '성적이'가 성명+주격으로 보인다 — 도메인 최빈 어휘",
    ),
    RedactionCase(
        id=47,
        category="함정·성씨겹침",
        text="문의를 드립니다",
        expected="문의를 드립니다",
        must_present=("문의",),
        context_trap=True,
        reason="'문'이 성씨 — 상담 문맥 최빈 어휘",
    ),
    RedactionCase(
        id=48,
        category="함정·성씨겹침",
        text="정도는 이해합니다",
        expected="정도는 이해합니다",
        must_present=("정도",),
        context_trap=True,
        reason="'정'이 성씨 + 보조사 '는'",
    ),
    RedactionCase(
        id=49,
        category="함정·성씨겹침",
        text="강의를 듣고 있어요",
        expected="강의를 듣고 있어요",
        must_present=("강의",),
        context_trap=True,
        reason="'강'이 성씨 + 목적격 '를'",
    ),
    # ── 50~54 마스킹 산출물 재입력 (8/6 신설) ────────────────────
    # 🔴 **멱등성 축**이다 — 트립와이어와 저장 훅이 마스킹 통과본을 **다시** 검사하므로
    # 비멱등이면 정상 문면이 차단된다. 44·45가 조사 없는 형태만 봐서 새던 자리다.
    RedactionCase(
        id=50,
        category="산출물 재입력",
        text="⟪이름1⟫ 학생의 어머니입니다",
        expected="⟪이름1⟫ 학생의 어머니입니다",
        must_present=("학생의",),
        context_trap=True,
        reason=(
            "🔴 **이 PR의 핵심 케이스.** 호칭어 필터가 정확 일치라 `학생의`가 목록에 없어 "
            "우회했고, 호칭 `학생의`가 이름으로 마스킹돼 새 finding이 났다 → 트립와이어가 "
            "전송을 막았다. \"○○ 학생의 어머니입니다\"는 학부모 문의의 전형 문면이다. "
            "조사를 벗기고 비교하도록 고쳤다(8/6)"
        ),
    ),
    RedactionCase(
        id=51,
        category="산출물 재입력",
        text="⟪이름1⟫ 학생을 부탁드립니다",
        expected="⟪이름1⟫ 학생을 부탁드립니다",
        must_present=("학생을",),
        context_trap=True,
        reason="목적격 '을' 결합형 — 50과 같은 우회 경로(조사만 다르다)",
    ),
    RedactionCase(
        id=52,
        category="산출물 재입력",
        text="⟪이름1⟫ 학생은 성실합니다",
        expected="⟪이름1⟫ 학생은 성실합니다",
        must_present=("학생은",),
        context_trap=True,
        reason=(
            "보조사 '은' — 후속 호칭어가 없어 종전에도 통과했다. **회귀 방지용**이다"
            "(호칭 패턴을 넓히면 여기가 먼저 깨진다)"
        ),
    ),
    RedactionCase(
        id=53,
        category="산출물 재입력",
        text="학생의 어머니입니다",
        expected="학생의 어머니입니다",
        must_present=("학생의",),
        context_trap=True,
        reason=(
            "🔴 **앞에 토큰이 없어도** `학생의`는 이름이 아니다. 토큰 인접 스킵만으로는 "
            "이 형태가 남는다 — 호칭어 필터를 고쳐야 잡힌다(변경 B의 독립 근거). "
            "종전에는 이 문장 하나만으로 `⟪이름1⟫ 어머니입니다`가 됐다"
        ),
    ),
    RedactionCase(
        id=54,
        category="산출물 재입력",
        text="010-1234-5678 서연 어머니",
        expected="⟪연락처1⟫ ⟪이름1⟫ 어머니",
        must_absent=("서연",),
        reason=(
            "🔴 **호칭 단계에 토큰 인접 스킵을 걸면 안 되는 이유**를 고정한다(8/6 실측). "
            "호칭 단계는 `_mask_batch`(연락처·학교·주소) **직후**라 직전 토큰이 남의 "
            "유형이고, 그 뒤 어절은 **아직 마스킹되지 않은 실제 이름**일 수 있다. "
            "스킵을 걸면 `서연`이 그대로 남아 **미탐**이다 — 스코어링 단계에서 같은 스킵이 "
            "맞는 것은 그쪽이 파이프라인 마지막이라 전제가 다르기 때문이다. "
            "엑셀 비고란 유입형(18~22)의 실제 형태다"
        ),
    ),
    RedactionCase(
        id=55,
        category="산출물 재입력",
        text="김민준 학생의 어머니입니다",
        expected="⟪이름1⟫ 학생의 어머니입니다",
        must_absent=("김민준",),
        must_present=("학생의",),
        reason=(
            "🔴 **원문 형태**로 넣어야 코퍼스 전건 멱등성 테스트(C-1)가 물린다. 50~53은 "
            "이미 마스킹된 문면을 입력으로 두므로 `redact(redact(x))`의 2차가 깨끗해 "
            "성질 테스트를 통과해 버렸다 — 44(조사 없음)의 조사 결합 짝이며, 이 케이스가 "
            "1차→2차 사이에서 새 finding이 나던 실제 경로다(8/6 실측)"
        ),
    ),
    # ── 56~64 (8/6) 정식 성명 + 관계어 · 양·군 오탐 함정 ────────
    # 🔴 종전 코퍼스에 **관계어 앞 정식 성명이 한 건도 없었다.** 관계어는 별명형
    #    (`서연이 엄마`, 28)에만 배선돼 있었고 그래서 `박서연 엄마입니다`가 실명 그대로
    #    나갔다 — 실측 14종 전건 미탐. `어머니`·`어머님`·`아버님`은 있는데 **`아버지`가
    #    없었던 것**이 목록이 체계 없이 만들어졌다는 증거다(99 ㉯).
    RedactionCase(
        id=56,
        category="인명·관계어",
        text="박서연 엄마입니다",
        expected="⟪이름1⟫ 엄마입니다",
        must_absent=("박서연",),
        must_present=("엄마",),
        reason="🔴 정식 성명 + 관계어 — 학부모 자기소개의 전형 문면. 14종 대표",
    ),
    RedactionCase(
        id=57,
        category="인명·관계어",
        text="김민준 아버지인데 상담 요청드립니다",
        expected="⟪이름1⟫ 아버지인데 상담 요청드립니다",
        must_absent=("김민준",),
        reason="⚠ `아버님`은 잡히는데 `아버지`가 없었다 — 목록의 체계 부재를 고정한다",
    ),
    RedactionCase(
        id=58,
        category="인명·관계어",
        text="이도윤 할머니가 데리러 오세요",
        expected="⟪이름1⟫ 할머니가 데리러 오세요",
        must_absent=("이도윤",),
        reason="조부모 — 실제 하원 문의에 나오는 관계어",
    ),
    RedactionCase(
        id=59,
        category="인명·관계어",
        text="최지우 보호자입니다",
        expected="⟪이름1⟫ 보호자입니다",
        must_absent=("최지우",),
        reason="`보호자`·`학부모`는 서식 문면이라 특히 흔하다",
    ),
    RedactionCase(
        id=60,
        category="인명·관계어",
        text="정하윤 형입니다",
        expected="⟪이름1⟫ 형입니다",
        must_absent=("정하윤",),
        reason="🔴 1자 관계어 — 가장 오탐 위험이 큰 자리(형태·형식). 성씨 결합이 그걸 막는다",
    ),
    RedactionCase(
        id=61,
        category="인명·호칭양군",
        text="박서연 양이 오늘 결석했습니다",
        expected="⟪이름1⟫ 양이 오늘 결석했습니다",
        must_absent=("박서연",),
        must_present=("양이",),
        reason="양·군을 성씨 결합으로 좁힌 뒤에도 **실제 호칭은 잡혀야** 한다",
    ),
    RedactionCase(
        id=62,
        category="문맥함정·분량",
        text="숙제 양이 많다는 문의가 있었어요",
        expected="숙제 양이 많다는 문의가 있었어요",
        must_present=("숙제 양이",),
        context_trap=True,
        reason=(
            "🔴 `양`이 분량을 뜻하는 학원 빈출 문면 — 종전엔 `⟪이름1⟫ 양이`로 오탐."
            "실측 오탐 9종 중 6종이 이 형태였다"
        ),
    ),
    RedactionCase(
        id=63,
        category="문맥함정·평가용어",
        text="대조군 결과와 실험군 결과를 비교했습니다",
        expected="대조군 결과와 실험군 결과를 비교했습니다",
        must_present=("대조군", "실험군"),
        context_trap=True,
        reason=(
            "🔴 `군`이 집단을 뜻하는 경우 — **우리 평가 문서**(part_a/13 §4-1)에 실제로 "
            "나오는 말이다. 종전엔 둘 다 `⟪이름1⟫군`으로 오탐"
        ),
    ),
    RedactionCase(
        id=64,
        category="문맥함정·지시대명사",
        text="우리 엄마가 저희 아빠는 그 형태로 하자고 하셨어요",
        expected="우리 엄마가 저희 아빠는 그 형태로 하자고 하셨어요",
        must_present=("우리 엄마", "저희 아빠", "그 형태"),
        context_trap=True,
        reason=(
            "🔴 관계어를 넣으면 새로 생기는 오탐 축 — 앞 어절이 지시대명사다. "
            "성씨 결합(`[$surnames][가-힣]{2}`)이 이걸 통째로 막는다"
        ),
    ),
    # ── 65~72 상담 fact·완충 사전 빈출어(오탐 검증 — 유지돼야 함) ──────
    # 🔴 №53 실측 8문면. `whitelists.name_exclude` 5어간이 이 여덟을 막는다.
    #   ⚠ 어간을 뺐을 때 **여기가 red** 여야 그 목록이 지켜진다 — yaml:332 의 «코퍼스와 함께 누적».
    RedactionCase(
        id=65,
        category="함정·상담 fact(이번에)",
        text="이번에는 과제를 모두 제출했습니다",
        expected="이번에는 과제를 모두 제출했습니다",
        must_present=("이번에는",),
        context_trap=True,
        reason="`이`(성씨)+`번에`+`는` — 3음절 한자어 + 조사. name_exclude `이번에`",
    ),
    RedactionCase(
        id=66,
        category="함정·상담 fact(이번에)",
        text="이번에도 제출 완료",
        expected="이번에도 제출 완료",
        must_present=("이번에도",),
        context_trap=True,
        reason="같은 어간에 조사만 다른 형태 — 조사 15개가 전부 같은 구멍이다",
    ),
    RedactionCase(
        id=67,
        category="함정·상담 fact(정리하)",
        text="정리하는 습관이 자리 잡고 있습니다",
        expected="정리하는 습관이 자리 잡고 있습니다",
        must_present=("정리하는",),
        context_trap=True,
        reason="`정`(성씨)+`리하`+`는`. name_exclude `정리하`",
    ),
    RedactionCase(
        id=68,
        category="함정·상담 fact(이해력)",
        text="이해력이 부족한 편입니다",
        expected="이해력이 부족한 편입니다",
        must_present=("이해력이",),
        context_trap=True,
        reason=(
            "`이`+`해력`+주격 `이`. 🔴 **완충 사전 B군 치환 쌍의 `from`** 이라 "
            "이 오탐이 프롬프트 축 방어를 통째로 끄고 있었다(prompt.py `_renderable_pair`)"
        ),
    ),
    RedactionCase(
        id=69,
        category="함정·상담 fact(정에서)",
        text="가정에서도 지도해 주시면 좋겠습니다",
        expected="가정에서도 지도해 주시면 좋겠습니다",
        must_present=("가정에서도",),
        context_trap=True,
        reason=(
            "🔴 **어절 안쪽 절단** — `가`를 버리고 `정에서`+`도`를 문다. "
            "name_exclude 는 그 성질을 안 고치고 이 조각만 뺀다(99 #178)"
        ),
    ),
    RedactionCase(
        id=70,
        category="함정·상담 fact(정에서)",
        text="가정에서도 함께 봐 주세요",
        expected="가정에서도 함께 봐 주세요",
        must_present=("가정에서도",),
        context_trap=True,
        reason="같은 절단이 다른 문장에서도 나는지 — 어간이 아니라 조각이라 문맥과 무관하다",
    ),
    RedactionCase(
        id=71,
        category="함정·완충어(다른)",
        text="다른 학생에 비해 시간이 더 걸립니다",
        expected="다른 학생에 비해 시간이 더 걸립니다",
        must_present=("다른", "학생"),
        context_trap=True,
        reason=(
            "🔴 이쪽은 `name_candidates`가 아니라 **`name_honorific`** 경로다 — "
            "`[가-힣]{2,3}` + 호칭 `학생` 이라 `⟪확인필요⟫`가 아니라 **`⟪이름1⟫` 확정**이 났다. "
            "`name_exclude`가 두 경로 모두에 걸린다(redaction.py:311·398)"
        ),
    ),
    RedactionCase(
        id=72,
        category="함정·상담 fact(복합)",
        text="이번에는 정리하는 습관이 보이고 가정에서도 지도해 주셨습니다",
        expected="이번에는 정리하는 습관이 보이고 가정에서도 지도해 주셨습니다",
        must_present=("이번에는", "정리하는", "가정에서도"),
        context_trap=True,
        reason=(
            "🔴 **후보 2개 이상이면 문장이 통째로 `⟪확인필요⟫`가 된다**(밀도 임계) — "
            "한 문장에 셋을 넣어 그 층까지 막혔는지 잰다. 오탐 1건이면 트립와이어가 "
            "이미 전송을 막는다(`trace_masking.py:83` — `findings or uncertain`)"
        ),
    ),
)
