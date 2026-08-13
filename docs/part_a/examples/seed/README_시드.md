# CheckOn 위험신호 검증 시드 — 학생 17명 × 12주

작성 2026-08-13 · AI 파트 → 백엔드
🔴 **CSV 3개 + 검증 SELECT.** Roster(계정·프로필·관계·반·alias)는 백엔드가 만들고,
아래 CSV를 그 UUID에 맞춰 적재한다.

---

## 0. 기준 날짜 — `week_offset` 을 실제 날짜로 바꾸는 법

CSV는 날짜를 고정하지 않고 **이번 주 월요일에서 역산하는 오프셋**으로 준다.
재실행할 때 기준만 바꾸면 그대로 재사용된다.

```
기준 = 이번 주 월요일 (KST 00:00)          예) 2026-08-10
occurred_at(KST) = 기준 − (week_offset × 7일) + (dow 일) + time_kst
occurred_at(DB)  = 위 값을 UTC 로 변환해 timestamptz 에 저장
```

| 값 | 뜻 |
|---|---|
| `week_offset = 0` | **이번 주** (진행 중) |
| `week_offset = 1` | 직전 완료 주 |
| `week_offset = 11` | 11주 전 |
| `dow` | 0=월 … 6=일 |

⚠ **KST 월요일 00:00 = UTC 일요일 15:00.** 경계 전후 기록이 UTC 날짜가 같아도
다른 KST 주에 들어간다 — 변환을 KST 기준으로 먼저 하고 UTC 로 저장할 것.

🔴 **`analysisDate` 는 기준 월요일이 속한 주로 잡는다.** 시드와 분석 창이 어긋나면
R1 의 "2주 연속"도 R3 의 "이번 주 대 평소"도 전부 밀린다.

---

## 1. 값 집합 — 🔴 백엔드 실제값에 맞췄습니다

2026-08-13 대조 결과, **AI 계약이 백엔드보다 넓거나 같습니다. 더 좁은 축이 하나도 없습니다.**
그래서 시드는 **백엔드 실제값만** 씁니다.

| 컬럼 | 시드가 쓰는 값 | 백엔드 | AI 계약 |
|---|---|---|---|
| `record_type` | `SOLVE` · `SUBMIT` | 동일 | + `attend`·`consult` (미사용) |
| `source_type` | **`MANUAL`** | 운영 API 고정 | + `trackA`·`trackB`·`studentHome` |
| `area_tag` | reading · literature · language · speech_writing · media | 동일 5종 | **정확히 일치** |
| `subject_track` | common · elective | 동일 | **정확히 일치** |
| `type_tag` | fact · infer · critic · concept · apply | 동일 5종 | **정확히 일치** |
| `item_format` | **`mcq` 만** | mcq 만 | + `short`·`essay` (미사용) |

**조합 제약도 동일합니다.**

```
reading · literature          → common
speech_writing · language · media → elective
```

AI 쪽 정본: `taxonomy.py` — `COMMON_AREAS = {READING, LITERATURE}` · `derive_subject_track()`

🔴 **`source_type = MANUAL` 은 이미 AI 계약에 있습니다**(PR #236, 2026-08-13).
400 재현되지 않습니다. 다만 한시 호환값이라 백엔드가 `source_type → source`
화이트리스트 매핑을 배포하면 같이 정리합니다.

---

## 2. 파일

### `students.csv` — Roster 명세 (16명)

백엔드가 이 표대로 계정·프로필·관계·반·alias 를 만든다.

| 컬럼 | 뜻 |
|---|---|
| `student_alias` | 🔴 **문서상 이름**. 실제 `ai_student_aliases.alias`(`st_`+32hex)와 매핑표로 이어 주세요 |
| `class_alias` | `cl_a1` · `cl_b2` · `(미배정)` |
| `relationship_started_weeks_ago` | 🔴 `teacher_student_relationships.started_at` 을 이만큼 과거로. **`class_enrollments.enrolled_at` 이 아닙니다** |
| `status` | `ENROLLED` · `RETURNED` |
| `consent` | `granted` · `denied` |

### `learning_records.csv` — 2,123행

`teacher_id` = **`teacher_profile_id`** (account_id 아님) · `student_id`·`class_group_id` 는 매핑표로.
`id`(uuid)는 백엔드가 생성. AI 요청의 `record_id` = `le_` + uuid(하이픈 제거)는 백엔드가 붙입니다.

빈 칸(`""`)은 **NULL** 입니다.

### `detection_assignment_week_summaries.csv` — 193행

⚠ **이번 라운드에서는 안 쓰입니다.** 요청 생성 로직이 이 테이블을 아직 안 읽어서
R2 의 `assignment_window` 가 요청에 안 실립니다(2026-08-13 확인).
**미리 만들어 둔 것**이니, 그 작업이 끝나면 그대로 넣으시면 R2 대조가 됩니다.

---

## 3. 학생 구성 — 무엇을 보려고 이렇게 짰나

### `cl_a1` (9명) — 🔴 **반별 TOP 3~5 상한을 처음으로 넘긴다**

| 별칭 | 겨냥 | 왜 |
|---|---|---|
| st_01 | R1 급락 (86% → 58/56%) | |
| st_02 | R1 완만 (83% → 66/64%) | 같은 규칙 다른 강도 → **랭킹 순서 대조** |
| st_03 | R3 학습 공백 (10건 → 2건) | 그 주 과제 미제출도 함께 |
| st_04 | R6 유형 편중 (문학×추론 20%) | |
| st_05 | R4 숨은 위기 **단독** | 🔴 `advisory=true` · 상한 밖 합류 |
| st_06 | 🔴 **복합 R1 + R3** | 실제로 가장 흔한 모양. 지금까지 한 번도 안 밟힘 |
| st_07 | R1 경계 (80% → 62/61%) | 임계 근처 |
| st_08 | 정상 | 대조군 — **안 뜨는 게 맞다** |
| st_09 | 신규생 (재원 1주) | 🔴 판정 제외 · `excluded_under_2w` 확인 |

⇒ 발화 6명(01·02·03·04·06·07) + advisory 1명 → **`capped_out` 이 0이 아니게 됩니다.**

### `cl_b2` (7명) — 상태·skip 축

| 별칭 | 겨냥 |
|---|---|
| st_10 | R5 복귀 관리 (`status=RETURNED` · 3주 공백 후 복귀) |
| st_11 | R1 하락 — 🔴 **2회차에 `ongoing` 확인용** |
| st_12 | 🔴 **미동의**(`consent=denied`) — 판정·evidence 어디에도 안 나와야 함 |
| st_13 | R4 skip — `duration_sec` 전부 NULL → `duration_missing` |
| st_14 | R6 skip — 태그 70% 누락 → `tagging_below_60pct` |
| st_15 | 정상 대조군 |
| st_17 | ⚠ **R2 제출 저하** — 3주 연속 미제출. 백엔드가 assignment_window 를 실어주면 발화 |

### 반 미배정 (1명)

| st_16 | 🔴 `class_group_id = NULL` → 요청에 `cl_unassigned` 로 나가는 경로 |

⚠ 오늘 어댑터가 `cl_unassigned` 를 `^cl_[0-9a-f]{32}$` 로 막아 DLT 로 보냈습니다.
**이 학생 하나가 그 경로를 실증합니다.**

### ⚠ lifecycle 은 2회차부터입니다

`ongoing`·`follow_up` 은 과거 Alert 이력이 있어야 나옵니다. 그 이력은 detection_run 이
한 번 돌아야 생깁니다. **첫 실행은 전부 `new` 가 정상입니다.**

---

## 4. 🔴 검증 SELECT — 배치 돌리기 전에 시드가 맞는지

`:teacher` = `teacher_profile_id` · `:monday` = 기준 월요일(KST)

```sql
-- ① 학생 수와 반 배치
SELECT COALESCE(cg.name,'(미배정)') AS class, COUNT(DISTINCT lr.student_id) AS students
FROM learning_records lr LEFT JOIN class_groups cg ON cg.id = lr.class_group_id
WHERE lr.teacher_id = :teacher GROUP BY 1 ORDER BY 1;
-- 기대: cl_a1=9 · cl_b2=7 · (미배정)=1

-- ② 학생×주 행 수 — 주가 12개씩 있는지 (KST 주 경계)
SELECT sa.alias,
       date_trunc('week', (lr.occurred_at AT TIME ZONE 'Asia/Seoul'))::date AS wk,
       COUNT(*) AS rows
FROM learning_records lr
JOIN ai_student_aliases sa ON sa.student_id = lr.student_id AND sa.teacher_id = lr.teacher_id
WHERE lr.teacher_id = :teacher
GROUP BY 1,2 ORDER BY 1,2 DESC;
-- 🔴 기대: 신규생(재원1주) 외 전원 12주 · 주마다 solve N + submit 1

-- ③ R3 학습 공백 학생의 이번 주 급감 확인
SELECT sa.alias,
       date_trunc('week',(lr.occurred_at AT TIME ZONE 'Asia/Seoul'))::date AS wk,
       COUNT(*) AS activity_count
FROM learning_records lr
JOIN ai_student_aliases sa ON sa.student_id = lr.student_id AND sa.teacher_id = lr.teacher_id
WHERE lr.teacher_id = :teacher
GROUP BY 1,2 HAVING date_trunc('week',(lr.occurred_at AT TIME ZONE 'Asia/Seoul'))::date >= :monday
ORDER BY 3 ASC;
-- 🔴 기대: 이번 주 최소값이 R3·복합 학생 (2~4건), 나머지는 8건 이상

-- ④ R1 하락 학생의 주간 정답률
SELECT sa.alias,
       date_trunc('week',(lr.occurred_at AT TIME ZONE 'Asia/Seoul'))::date AS wk,
       ROUND(AVG(CASE WHEN lr.correct THEN 1 ELSE 0 END)::numeric, 2) AS acc
FROM learning_records lr
JOIN ai_student_aliases sa ON sa.student_id = lr.student_id AND sa.teacher_id = lr.teacher_id
WHERE lr.teacher_id = :teacher AND lr.record_type = 'SOLVE' AND lr.correct IS NOT NULL
GROUP BY 1,2 ORDER BY 1,2 DESC;
-- 🔴 기대: 최근 2주만 낮고 그 앞 10주는 평탄

-- ⑤ 값 집합이 계약 밖으로 새지 않았는지
SELECT DISTINCT record_type FROM learning_records WHERE teacher_id = :teacher;  -- SOLVE, SUBMIT
SELECT DISTINCT source_type FROM learning_records WHERE teacher_id = :teacher;  -- MANUAL
SELECT DISTINCT area_tag, subject_track FROM learning_records WHERE teacher_id = :teacher ORDER BY 1,2;
-- 🔴 기대 조합: reading/literature→common · language/speech_writing/media→elective · (NULL,NULL)

-- ⑥ R4 skip 학생은 duration 이 전부 NULL 인가
SELECT sa.alias, COUNT(*) FILTER (WHERE lr.duration_sec IS NULL) AS null_dur, COUNT(*) AS total
FROM learning_records lr
JOIN ai_student_aliases sa ON sa.student_id = lr.student_id AND sa.teacher_id = lr.teacher_id
WHERE lr.teacher_id = :teacher AND lr.record_type='SOLVE'
GROUP BY 1 ORDER BY 2 DESC;
-- 🔴 기대: R4 skip 학생만 null_dur = total

-- ⑦ 재원 주수 (판정 제외 학생 확인)
SELECT sa.alias, r.started_at,
       floor(EXTRACT(EPOCH FROM ((:monday::date + 7) - r.started_at))/604800) AS enrolled_weeks
FROM teacher_student_relationships r
JOIN ai_student_aliases sa ON sa.student_id = r.student_id AND sa.teacher_id = r.teacher_id
WHERE r.teacher_id = :teacher ORDER BY 3;
-- 🔴 기대: 신규생 1명만 < 2, 나머지 14 이상
```

---

## 5. 배치 실행 후 대조할 것

```
POST /api/v1/detection-runs  {"analysisDate": "<기준 주 안의 날짜>"}
GET  /api/v1/detection-runs/{runId}     → SUCCEEDED 대기
```

| 확인 | 기대 |
|---|---|
| 🔴 `capped_out` | **0이 아니다** (cl_a1 발화 6명) |
| 발화 규칙 | R1 · R3 · R4 · R5 · R6 — 🔴 **R2 는 이번 라운드 제외** |
| `advisory=true` | st_05 1건 |
| `excluded_under_2w` | 1 (신규생) |
| 🔴 미동의 학생 | **응답 어디에도 없음** |
| `rules_skipped` | `duration_missing` · `tagging_below_60pct` · (R2 는 `authoritative_evidence_missing`) |
| lifecycle | 🔴 **1회차는 전부 `new`** · 2회차에 `ongoing` |
| 반 미배정 학생 | `class_ref = cl_unassigned` 로 나가는지 |

⚠ 반복 검증 시 **`analysisDate` 를 하루씩 밀어서** 돌리세요 —
동일 강사·동일 analysisDate 는 같은 실행을 재사용합니다.
