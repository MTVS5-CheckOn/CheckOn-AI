# evidence 필드 설계 — 백엔드 회신용 (2026-08-13)

> 소유: 박진희 (detection) · 상태: **제안** — 백엔드 합의 전. 계약 코드는 아직 안 바꿨다.
> 🔴 이 문서는 **조사 결과 + 제안**이다. `contracts/detection.py` 수정은 합의 후 별도 PR.

## 0. 왜 이 문서가 생겼나

8/13 리허설 3자 대조에서 승우님이 **AI 원문 = DB = Alert API 완전 일치**를 확인해 주셨다.
`record_id`·`brief` 무손실, `score`·`advisory`·`lifecycle`·`rules_skipped` 저장까지 확인됐다.
**저장이 안 된다는 걱정은 전부 기우였다.**

남은 것은 **evidence 항목이 사람이 읽을 재료를 안 갖고 있다**는 것이다.

## 1. 실측 — 지금 evidence에 무엇이 들어 있나

`EvidenceItem`([contracts/detection.py:522](../../src/ai/contracts/detection.py#L522))의 필드는 셋뿐이다:

| 필드 | 값 |
| --- | --- |
| `source_table` | 논리 테이블명 |
| `record_id` | 백엔드 DB 원본 PK |
| `summary` | **자유 문자열 하나** |

그리고 `summary`가 규칙마다 밀도가 다르다([detection/evidence.py](../../src/ai/detection/evidence.py)):

| 규칙 | resolver | summary 실제 문면 |
| --- | --- | --- |
| **R1·R4·R6** | `_from_learning_events` | `f"{label} 근거 기록"` — 🔴 **레코드가 몇 건이든 전부 같은 문자열** |
| R2 | `resolve_r2_evidence` | `f"예정 과제 {expected}건 중 제출 {submitted}건"` |
| R3 | `resolve_r3_evidence` | `f"해당 주 학습 활동 {count}건"` |
| R5 | `resolve_r5_evidence` | `"휴원 후 복귀 상태 전환 기록"` |

🔴 **승우님이 보신 「같은 라벨 3건」이 정확히 R1 경로다.** `"정답률 하락 근거 기록"` ×3 —
세 레코드가 무엇이 다른지 문면에 없다. 계약 구조(3필드)는 규칙마다 동일하고,
**다른 것은 내용 밀도**다.

## 2. 🔴 R1 evidence는 **발화 주만** 담는다 — 기준선이 없다

`_finding`([detection/rules.py:417](../../src/ai/detection/rules.py#L417))이
`evidence_weeks = assess`(판정 창)로만 만들고, `_from_learning_events`가 그 주의
`learning_event` record_id만 모은다. **baseline 주의 레코드는 근거에 안 실린다.**

⇒ 백엔드가 *"평소 대비 얼마나 떨어졌나"* 를 보여주려면 **비교 기준이 payload에 없다.**
brief 문장에만 녹아 있고, 그 문장은 LLM 산출이라 재현이 보장되지 않는다.

## 3. 구현 — 안 D (비교값은 신호 레벨 · 기준선 행은 R3만)

> 🔴 **초안의 안 B는 폐기했다.** 구현하다 갈렸다 — 아래 §3-1이 그 이유다.

### 3-1. 🔴 왜 안 B가 안 되는가 — `learning_event`는 문항 단위다

`LearningEvent`의 `record_id` 하나 = **solve 한 건**(`correct: bool` · `duration_sec`).
그런데 R1이 비교하는 값은 **주 단위 집계 정답률**이고, R4는 **주 단위 평균 정규화 시간**이다.
⇒ **그 값에 해당하는 백엔드 레코드가 존재하지 않는다.**

문항 레코드에 주 단위 값을 붙이면 *"그 기록 자신의 값"* 이 거짓이 되고, 저장소 규율
(*"판정에 쓴 값과 응답 evidence가 갈리면 BE가 원본을 열었을 때 숫자가 안 맞는다"*)에 어긋난다.

R3만 되는 이유: `WeeklyActivityEvidence`가 **주 단위 백엔드 레코드**라
`record_id`와 `activity_count`가 1:1로 붙는다.

### 3-2. 채택 — 비교는 신호가, 출처는 evidence가 든다

```
Signal      metric · observed · baseline · sample_size     ← "무엇을 무엇과 비교했나"
Evidence    role · observed · sample_size · occurred_on    ← "어느 기록이 근거인가"
```

`Signal.baseline`이 *"평소 대비"* 를 든다. evidence 행에는 **`baseline` 필드를 두지 않았다** —
둘 곳이 없어서가 아니라 **거짓이 되기 때문**이다.

### 3-3. 🔴 규칙별 baseline 행 여부 (2026-08-13 확정 · §4-B 산출물)

| 규칙 | 신호 | `Signal.baseline` | evidence `baseline` 행 | 기준선의 성격 |
| --- | --- | --- | --- | --- |
| **R1** | acc_drop | ✅ 있음 | ❌ **없음** | 직전 주들의 **평균 정답률** — 집계값이라 가리킬 레코드가 없다 |
| **R2** | submit_drop | ❌ 없음 | ❌ 없음 | 기준선 비교를 **안 한다** — 연속 미제출 *횟수*를 임계와 비교 |
| **R3** | volume_gap | ✅ 있음 | ✅ **있음** | 직전 8주 `weekly_activity` — **주 단위 레코드가 실존** |
| **R4** | hidden_risk | ✅ 있음 | ❌ **없음** | 직전 주들의 평균 정규화 시간 — 집계값 |
| **R5** | return_care | ❌ 없음 | ❌ 없음 | **비교 대상 개념이 없다** — 복귀는 사건이다 |
| **R6** | type_bias | ❌ 없음 | ❌ 없음 | 같은 주 안의 셀 점유율을 **임계**와 비교 |

> 🔴 **「없음」은 누락이 아니다.** 기준이 집계값이거나(R1·R4) 비교 자체를 안 하는 규칙이라
> (R2·R5·R6) **가리킬 기록이 존재하지 않는다.** 없는 기록을 지어내지 않기 위한 것이다.
> ⚠ **백엔드가 *"baseline 행은 항상 온다"* 로 가정하면 R3 외 전 규칙에서 화면이 빈다.**
> R1의 *"평소 대비"* 는 `Signal.baseline`에서 읽어야 한다.

### 3-4. 개수 상한 — 역할별로 가른다

```
trigger  최대 3건   (종전과 동일 — 기존 응답이 안 흔들린다)
baseline 최대 3건   (R3의 기준창은 8주라 그대로 두면 8건이 된다)
```

🔴 **전체 상한 하나로 두면 안 된다** — 3→6으로만 올리면 R1이 trigger를 3건에서 **6건으로**
내기 시작한다. 이 PR은 *"기준선을 추가한다"* 지 *"기존 근거를 바꾼다"* 가 아니다.
⚠ 기준선은 **최근 주부터** 남긴다(오래된 주가 먼저 잘린다).

### 3-5. (참고) 폐기한 초안 — 안 B

```
evidence 항목마다 role/metric/observed/baseline/sample_size/occurred_on 을 싣는다
```

⇒ **§3-1의 이유로 폐기.** `baseline`을 evidence 행에 두면 R1·R4에서 거짓이 된다.

### 🔴 공통 꼬리말 (회신에 반드시 포함)

```
🔴 백엔드 쪽 작업은 LLM 생성이 아니라 String.format 수준입니다.
   brief 만 LLM이고 evidence 는 재현 가능해야 해서 의도적으로 LLM을
   안 태웁니다 — 같은 기록인데 조회할 때마다 근거 문구가 달라지면
   안 되니까요. (2026-08-13 실측: 동일 seed 8회 → 유일 문장 3종)

예) "8/12 과제 · 정답률 0% (0/5문항)"
    = 저희가 보낸 role/metric/observed/sample_size
    + 백엔드가 가진 과제명·날짜
```

## 4. ⚠ 계약 변경 범위

`contracts/detection.py`는 **양자 승인 13파일이 아니다**(CLAUDE.md §2 대조 — 비해당) —
소유는 박진희(`02_ownership.md`). 다만 **백엔드가 소비하는 계약**이므로 합의 없이 바꾸지 않는다.
필드 추가는 optional로 넣어 기존 소비자를 안 깨는 방향(계약 축은 `_CONTRACT_VERSION`).

## 5. 🔴 `summary`는 「전부 동어반복」이 아니었다

초안이 *"summary는 정보가 0이니 deprecated"* 라고 적었는데 **리허설에서 본 것이 R1이라
그렇게 읽혔을 뿐**이다. 실측::

    R1·R4·R6   "acc_drop 근거 기록"          🔴 정보 0 (필드 이름의 반복)
    R2         "예정 과제 3건 중 제출 0건"    ✅ 숫자가 있었다
    R3         "해당 주 학습 활동 2건"        ✅ 숫자가 있었다
    R5         "휴원 후 복귀 상태 전환 기록"  ✅ 문구가 있었다

🔴 **그래서 「읽지 마라」를 먼저 보내면 R2·R3 신호가 화면에서 숫자를 통째로 잃는다.**
⇒ **구조화 필드로 먼저 옮겼다**: R2는 `observed`(제출)+`sample_size`(예정), R3는
`observed`(활동 수). R5는 옮길 숫자가 없다(문구뿐).
**이제 `summary`의 모든 숫자가 구조화 필드에도 있다** — 전환해도 잃는 것이 없다.

## 6. 이 조사에서 갈린 것 (등재로 이어진 사실)

| 질문 | 실측 결과 |
| --- | --- |
| R1이 결측을 0%로 보나 | 🔴 **아니다** — `accuracy = … if graded else None`, `_r1`은 `None`이면 미발화 |
| R2·R3 스킵이 미래 `analysisDate` 탓인가 | 🔴 **아니다** — R2 skip은 집계가 **통째로 빌 때만** |
| R1 evidence에 기준선이 있나 | **없다** (§2) |
| evidence에 서술 필드가 있나 | `summary` 자유 문자열 하나뿐 (§1) |

자세한 근거는 `99_open_items.md` #59·#60·#61·#62 및 #48 보강 참조.
