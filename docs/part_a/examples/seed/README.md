# 백엔드 검증 시드 — 학생 17명 × 12주 (2026-08-13 승우님 전달분)

🔴 **이 파일들이 `../detect_demo_request.json` 의 시나리오 정본이다.**
`src/ai/evaluation/demo_snapshot.py` 를 고칠 때 **여기와 맞는지 확인한다.**

⚠ **반입 이유(2026-08-14):** 종전에는 이 시드가 **저장소에 없었다.** 그래서
`tests/ai/contract/test_demo_matches_backend_seed.py` 가 이름과 달리 **시드를 못 보고**
검사 파일 안에 손으로 적은 숫자(`_SEED_CLASS_SIZES`)와만 대조했다. ⇒ **데모가 시드와
갈렸는데 아무도 몰랐다**(아래 §3). 지금은 검사가 `students.csv` 를 읽는다. (99 #68)

## 1. 파일

| 파일 | 무엇 |
|---|---|
| `students.csv` | 17명 · 반·재원 주수·status·consent·**겨냥한 규칙** |
| `detection_assignment_week_summaries.csv` | 주차별 과제 배정/제출 (193행) |
| `learning_records.csv` | 학습 기록 원본 (2123행) |
| `checkon_seed.sql` | 승우님께 넘긴 적재 스크립트 |

🔴 **`checkon_seed.sql` 의 `\set teacher_id` · `\set monday` 는 예시값 그대로다** —
승우님 환경 값을 넣지 마라. 그대로 두면 각자 자기 값으로 바꿔 쓴다.

⚠ **생성기(`gen.py`)는 이 반입에 없다** — 전달분에 포함되지 않았다(2026-08-14 실측).
CSV 세 개가 정본이고, 재생성이 필요하면 그때 생성기를 함께 반입한다.

## 2. 축 — 실측 (`students.csv` 를 직접 센 값)

```
17명 = cl_a1 9 + cl_b2 7 + (미배정) 1
status   ENROLLED 16 · RETURNED 1 (st_10)
consent  granted 16 · denied 1 (st_12)
재원 2주 미만  st_09 (1주)
```

🔴 **PAUSED 학생이 없다** — **휴원 제외 경로는 이 시드로 검증되지 않는다**(2026-08-14 발견).
데모에는 PAUSED 가 1명 있어서 두 쪽의 평가 대상 수가 갈린다(§3).

### 기대 평가 학생 수 = **15**

```
17 − st_09 (재원 1주 · excluded_under_2w) − st_12 (consent=denied) = 15
```

## 3. ⚠ 지금 갈려 있는 것 (2026-08-14 실측)

| | 시드 기대 | 데모 (`demo_snapshot.py`) | 승우님 E2E |
|---|---|---|---|
| 학생 수 | 17 | 17 ✅ | 17 ✅ |
| 반 배치 | 9 / 7 / 1 | 9 / 7 / 1 ✅ | ✅ |
| 주차 수 | 12 | 12 ✅ | ✅ |
| **평가 학생** | **15** | 🔴 **14** | 🔴 **16** (동의 제외 미구현) |
| **status** | ENROLLED 16 · RETURNED 1 | 🔴 enrolled 15 · returned 1 · **paused 1** | — |
| **`type_bias`** | 1 + **R6 skip 1** | 🔴 **2** (skip 미생성) | 1 + skip 1 ✅ |

**왜 갈리나:**

- **평가 14 vs 15** — 데모에만 PAUSED 학생이 있어 하나 더 빠진다
  (`17 − 재원1주 − 미동의 − 휴원 = 14`).
- **`type_bias` 2 vs 1** — 시드는 st_15 를 *"R6 skip — tagging_below_60pct"* 로 겨냥했는데,
  데모 생성기 `fake_snapshot.py` 의 `_cell_for()` 가 **모든 문항에 `type_tag` 를 채워서**
  태그 누락 상태를 만들 수 없다. ⇒ 그 학생이 skip 대신 **정상 발화**한다. (99 #67)

⚠ **이 표가 오래되면 `test_demo_matches_backend_seed.py` 의 `xfail` 사유와 어긋난다** —
고칠 때 **양쪽을 같이** 고쳐라.
