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

## 3. 제안 — 안 B (기준선 동봉)

§2가 「발화만」이므로 **안 B**를 택한다.

```
evidence 항목에 필드를 추가하고 기준선 기록도 함께 싣습니다.

  role         "trigger" | "baseline"
  metric       "accuracy" | "submit_rate" | "activity_count" | …
  observed     그 레코드에서 관측된 값
  baseline     비교 기준값 (role="trigger" 일 때만)
  sample_size  분모 (예: 채점된 문항 수)
  occurred_on  그 기록의 날짜

⚠ evidence 개수가 늘어납니다. 백엔드에서 role 로 갈라 주셔야 합니다.
```

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

## 5. 이 조사에서 갈린 것 (등재로 이어진 사실)

| 질문 | 실측 결과 |
| --- | --- |
| R1이 결측을 0%로 보나 | 🔴 **아니다** — `accuracy = … if graded else None`, `_r1`은 `None`이면 미발화 |
| R2·R3 스킵이 미래 `analysisDate` 탓인가 | 🔴 **아니다** — R2 skip은 집계가 **통째로 빌 때만** |
| R1 evidence에 기준선이 있나 | **없다** (§2) |
| evidence에 서술 필드가 있나 | `summary` 자유 문자열 하나뿐 (§1) |

자세한 근거는 `99_open_items.md` #59·#60·#61·#62 및 #48 보강 참조.
