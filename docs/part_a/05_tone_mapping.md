# [체크온] 라벨→톤 매핑 규칙표 + 완충 사전 v0

> **지시서 2.5 이행:** "규칙을 프롬프트에 묻지 말고 데이터 파일로." 이 md가 사양 원본이고, 런타임 산출물은 `composition/tone_map.yaml` + `composition/buffer_lexicon.yaml` 두 데이터 파일이다. 프롬프트 템플릿은 이 파일들을 **조회**만 한다 — 값 하드코딩 금지.
> 라벨은 4축 enum 확정본(`label_snapshot` 동결값)만 입력 — ai_suggested 미사용.

---

## 1. 축별 1차 규칙 (조합 이전의 단독 효과)

| 축 | 값 | 톤·문장 효과 |
| --- | --- | --- |
| `comm` | `data` | 수치·표 우선, 문장 짧게, 형용사 최소. "정답률 62%→71%" 식 |
| | `narrative` | 서사 우선, 수치는 근거로 1~2개만. "추론 문제를 새로 시작하면서…" |
| `sensitivity` | `anxious` | 완충 +1단계(§4 사전 적용 강화), 부정 정보는 반드시 대응 계획과 한 문장 안에, 하락 서술 후치 |
| | `direct` | 결론 선행, 완곡 표현 최소(단 금칙어는 동일 적용 — direct ≠ 무례) |
| `interest` | `grade` | 성적·정답률·백분위 중심 구성 |
| | `attitude` | 성실도·제출·수업 태도 중심, 점수는 보조 |
| | `admission` | 목표(수능·내신) 대비 현재 위치와 로드맵 프레임 |
| `frequency` | `frequent` | 짧게(핵심 2~3문장/블록), 지난 소통 이후 **변경분만** |
| | `monthly` | 충실하게(블록당 3~5문장), 기간 전체 요약 포함 |

## 2. 24조합 매핑표 (comm×sensitivity = 4 기본 프로파일 × interest 3 × frequency 2)

표기: 구성 = 블록 순서 / 길이 = 블록당 문장 수 / 완충 = §4 사전 적용 단계(0~2).

| # | comm | sens | interest | freq | 구성 | 길이 | 완충 | 비고 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | data | anxious | grade | frequent | 인사→수치 요약→안심 포인트→제안 | 2 | 2 | 수치 뒤 즉시 맥락 문장 의무 |
| 2 | data | anxious | grade | monthly | 인사→기간 수치표→추이 해설→제안 | 4 | 2 | |
| 3 | data | anxious | attitude | frequent | 인사→제출·참여 수치→제안 | 2 | 2 | |
| 4 | data | anxious | attitude | monthly | 인사→성실도 지표→변화 해설→제안 | 4 | 2 | |
| 5 | data | anxious | admission | frequent | 인사→목표 대비 지표→제안 | 2 | 2 | 백분위 후치(리포트 사양서 §5) |
| 6 | data | anxious | admission | monthly | 인사→목표 로드맵 수치→제안 | 4 | 2 | |
| 7 | data | direct | grade | frequent | **결론(수치)→근거→제안** | 2 | 1 | |
| 8 | data | direct | grade | monthly | 결론→기간 수치표→제안 | 3 | 1 | |
| 9 | data | direct | attitude | frequent | 결론(성실도)→근거→제안 | 2 | 1 | |
| 10 | data | direct | attitude | monthly | 결론→지표→제안 | 3 | 1 | |
| 11 | data | direct | admission | frequent | 결론(위치)→갭 수치→제안 | 2 | 1 | |
| 12 | data | direct | admission | monthly | 결론→로드맵→제안 | 3 | 1 | |
| 13 | narrative | anxious | grade | frequent | 인사→긍정 서사→성적 맥락→제안 | 3 | 2 | 점수 언급 최대 1회 |
| 14 | narrative | anxious | grade | monthly | 인사→한 달 서사→성장 해설→제안 | 5 | 2 | |
| 15 | narrative | anxious | attitude | frequent | 인사→태도 일화→제안 | 3 | 2 | evidence 일화는 기록 실존분만 |
| 16 | narrative | anxious | attitude | monthly | 인사→태도 서사→변화→제안 | 5 | 2 | |
| 17 | narrative | anxious | admission | frequent | 인사→방향 서사→제안 | 3 | 2 | |
| 18 | narrative | anxious | admission | monthly | 인사→목표 서사→현재 위치(완곡)→제안 | 5 | 2 | |
| 19 | narrative | direct | grade | frequent | 핵심 서사→성적 근거→제안 | 3 | 1 | |
| 20 | narrative | direct | grade | monthly | 핵심→한 달 흐름→제안 | 4 | 1 | |
| 21 | narrative | direct | attitude | frequent | 핵심(태도)→일화→제안 | 3 | 1 | |
| 22 | narrative | direct | attitude | monthly | 핵심→서사→제안 | 4 | 1 | |
| 23 | narrative | direct | admission | frequent | 핵심(위치)→서사→제안 | 3 | 1 | |
| 24 | narrative | direct | admission | monthly | 핵심→로드맵 서사→제안 | 4 | 1 | |

공통 불변: 모든 조합에서 ① 금칙어(§4 A군)는 완충 단계와 무관하게 항상 차단 ② 제안 블록은 강사가 실행 가능한 행동 1개 이상 ③ evidence 없는 문장 금지(게이트가 최종 방어).

## 3. 하위 백분위 완곡 규칙 (리포트 chart_analysis 전용 — 신규)

- 수치는 사실대로(변조·은폐 금지). 문장 프레임만 제어:
- 백분위 ≥ 50: 제한 없음 / 30~49: 반드시 개선 추이 또는 강점 영역과 병기 / < 30: 병기 의무 + **단독 문장 금지** + 다음 계획 문장 직결.
- `sensitivity=anxious`이면 각 구간 한 단계씩 엄격 적용(50~69도 병기 권장) + 백분위 해설 블록을 제안 블록 뒤로 후치.
- 어떤 조합에서도 반 평균·석차는 입력에 없음(audience 필터가 선차단) — 이 규칙표는 표현만 다룬다.

## 4. buffer_lexicon.yaml 초안 (금칙·치환 50항)

```yaml
# A군 — 금칙(치환 불가, 문장 재생성): 낙인·평가·진단
forbidden:
  - 게으르
  - 불성실
  - 산만하
  - 머리가 나쁘
  - 노력을 안
  - 의지가 없
  - 문제아
  - 뒤처졌
  - 꼴찌
  - 최하위
  - 포기한 것 같
  - 집중력이 없           # '오늘 집중이 어려웠다'(상태)는 허용 — 기질 단정만 금지
  - ADHD                  # 진단성 표현 일절 금지
  - 우울증
  - 심리적 문제
  - 다른 아이들은          # 비교 프레임
  - 반에서 유일하게
  - 이대로면 큰일          # 공포 유발
  - 가망이 없
  - 손을 놓

# B군 — 치환(단정 → 관찰·상태 서술)
replace:
  - { from: "못합니다",          to: "아직 익숙하지 않습니다" }
  - { from: "틀렸습니다",        to: "정답에 이르지 못했습니다" }
  - { from: "실패했습니다",      to: "이번에는 결과가 나오지 않았습니다" }
  - { from: "떨어졌습니다",      to: "다소 주춤했습니다" }          # anxious 2단계에서만, data 라벨은 수치 병기
  - { from: "심각합니다",        to: "주의 깊게 보고 있습니다" }
  - { from: "약점입니다",        to: "보완하면 좋을 부분입니다" }
  - { from: "느립니다",          to: "시간을 들여 풀고 있습니다" }
  - { from: "안 했습니다",       to: "이번에는 하지 못했습니다" }
  - { from: "거부합니다",        to: "부담을 느끼는 듯합니다" }
  - { from: "최악",              to: "가장 어려웠던" }
  - { from: "항상 틀립니다",     to: "반복적으로 어려워하는 유형입니다" }
  - { from: "전혀 이해하지 못",   to: "아직 개념이 자리 잡히지 않" }
  - { from: "성적이 나쁩니다",   to: "점수가 기대에 미치지 못했습니다" }
  - { from: "불안정합니다",      to: "편차가 있는 편입니다" }
  - { from: "문제가 있습니다",   to: "함께 살펴볼 부분이 있습니다" }
  - { from: "제출을 안 합니다",  to: "제출이 어려운 주가 있었습니다" }
  - { from: "수업을 안 듣",      to: "수업 참여가 줄어든" }
  - { from: "게을리",            to: "우선순위가 밀린 듯" }
  - { from: "포기",              to: "잠시 멈춘" }
  - { from: "형편없",            to: "아쉬운" }
  - { from: "뒤떨어",            to: "따라잡는 중" }
  - { from: "못 따라",           to: "적응하는 중" }
  - { from: "낙제",              to: "기준 미달" }                  # 사실 서술 필요시 수치로 대체 권장
  - { from: "걱정입니다",        to: "관심 있게 지켜보고 있습니다" }
  - { from: "이해력이 부족",     to: "추론 단계에서 시간이 걸리는" }
  - { from: "기초가 없",         to: "기초를 다지는 단계" }
  - { from: "태도가 나쁘",       to: "컨디션 기복이 보이" }
  - { from: "혼자만",            to: "" }                          # 비교 함의 삭제
  - { from: "반 평균보다",       to: "" }                          # 학부모向 원천 금지(입력에도 없음 — 이중 방어)
  - { from: "다른 학생에 비해",  to: "지난달과 비교해" }            # 비교축을 타인→본인 과거로 전환
```

- 적용 단계: 완충 0 = A군만 / 1 = A군 + B군 기본 / 2(anxious) = A군 + B군 전체 + 부정문 후치 규칙.
- 치환은 생성 후 후처리가 아니라 **프롬프트 규칙 + ToneSafety 게이트 검출** 병행 — 게이트가 A군 검출 시 해당 블록 재생성(≤3회).
- 사전 갱신: 강사 수정 diff에서 반복 등장하는 순화 패턴을 후보로 자동 수집 → 사람 승인 후 추가(자동 반영 금지).

## 5. 산출 파일·검증

- `tone_map.yaml`: §1·§2를 기계가독으로(조합 키 `comm.sens.interest.freq`) — 24键 전부 존재하는지 로드 시 검증.
- `buffer_lexicon.yaml`: §4 — A군 검출은 형태소 접두 매칭(활용형 대응).
- 골든셋: 24조합 × 동일 입력 스냅숏 → 프롬프트 스냅숏 테스트(평가 계획서 §3) — 조합 바꿨는데 출력이 같으면 매핑 미적용 버그.
