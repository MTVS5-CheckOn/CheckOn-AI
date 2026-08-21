# 백엔드 → AI 요청 JSON 모음 v0.1.1

모든 요청 공통 헤더: `X-Tenant-Id`(강사 alias) · `X-Request-Id` · 쓰기는 `Idempotency-Key`. 실명·연락처 필드는 어디에도 없음(alias만). 상세 규약·응답 스키마는 `체크온_AI_API·데이터계약_v0.1.md` 참조.

> ****7/15 회의 확정 반영:** 비동기 완료 통지 Kafka(Open-2) · 월별 리포트 일괄 1회(BE-5) · item_format v1=mcq만(Open-11) · 쿼터는 AI 무관(BE-4) — 상세는 04_api_contract·99_open_items.

v0.1.1 동기화:** ① `area_tag` 수능 6영역 enum + `item_format` 추가(`[Open-11]` 확정 7/15 · v1=mcq만) ② `/drafts` kind에 리포트 2종 구분(월별 일괄 / 단건 수시) ③ 리포트 요청에 `benchmarks` 추가(노출 3층 — teacher_only는 학부모向 컨텍스트에서 구조적 제외)

---

## 1. `POST /v1/detect` — 야간 감지 (일 1회)

```json
{
  "snapshot_meta": {
    "week_start": "2026-07-06",
    "snapshot_hash": "sha256:9f2c...",
    "term_context": "normal",              // normal | new_term | vacation
    "classes": [
      { "class_ref": "cl_a1" },
      { "class_ref": "cl_b2" }
    ]
  },
  "students": [
    {
      "student_ref": "st_8f2a",            // alias
      "class_ref": "cl_a1",
      "enrolled_weeks": 14,                 // 2 미만이면 '관찰 중' 처리됨
      "status": "enrolled",                 // enrolled | paused | returned(복귀 첫 주)
      "consent": "granted"                  // granted 외에는 이벤트가 와도 폐기
    }
  ],
  "learning_events": [
    {
      "record_id": "le_1029",               // 백엔드 DB PK — 근거 역추적 키, 불변 필수
      "student_ref": "st_8f2a",
      "type": "solve",                      // solve | submit | attend | consult
      "occurred_at": "2026-07-08T19:20:00+09:00",
      "correct": false,                     // solve만
      "duration_sec": 183,                  // solve만, 없으면 null (R4 미적용)
      "passage_word_count": 812,            // 지문형 문항만 — 어절 정규화용
      "passage_ref": "ps_4471",             // ★(8/3 신설) 지문/자료 묶음 참조 — 아래 [A 확정 통보]. 없으면 null
      "area_tag": "reading",                // 있으면 — 수능 기준: reading(독서) | literature(문학) | speech(화법) | writing(작문) | language(언어/문법) | media(매체) — Open-11 확정(7/15)
      "subject_track": "common",            // 있으면: common(공통) | elective(선택과목) — 수능 공통/선택 메타
      "type_tag": "infer",                  // 있으면: fact | infer | critic | concept
      "item_format": "mcq",                 // 있으면: mcq(객관식) | short(단답) | essay(서술형) — R6·약점 지도가 형식별로 분리 집계
      "assignment_title_text": "6월 모의고사 비문학 대비 #3",   // ⚠ Open-4b: 태깅 제안 입력
      "source": "trackB"                    // trackA | trackB | studentHome
    }
  ],
  "alert_context": [                        // ★(7/16 신설) 최근 30일 경보 이력 — lifecycle 판정 입력 (명세 09 §2·§4)
    {
      "student_ref": "st_8f2a",
      "signal_type": "hidden_risk",         // 명세 09 §1의 6값
      "status": "open",                     // open | resolved
      "resolved_at": null,                  // resolved일 때 해소 일시 — "해소 후 2주" 쿨다운 기준
      "followed_up": false                  // 해소 후 팔로업 카드가 이미 나갔는지 (중복 방지)
    }
  ],
  "detection_evidence": [                   // ★(8/12 신설 · **optional**) R2·R3·R5의 정본 근거 (99 #43)
    {
      "kind": "assignment_window",          // R2 — 그 주 예정 과제와 제출 결과
      "source_table": "assignment_week_summary",
      "record_id": "aws_20260810_st_8f2a",  // 백엔드 원본 PK — 응답 evidence에 그대로 실린다
      "student_ref": "st_8f2a",
      "week_start": "2026-08-10",
      "expected_count": 3,                  // 0이면 「과제가 없던 주」 — 미제출 연속에서 제외
      "submitted_count": 0                  // expected 이하. 1 이상이면 연속 종료
    },
    {
      "kind": "weekly_activity",            // R3 — 그 주 전체 학습량 집계. **0건도 실존 레코드**
      "source_table": "student_week_activity",
      "record_id": "swa_20260810_st_8f2a",
      "student_ref": "st_8f2a",
      "week_start": "2026-08-10",
      "activity_count": 0
    },
    {
      "kind": "enrollment_transition",      // R5 — 휴원→복귀 상태 전환 이력
      "source_table": "student_status_history",
      "record_id": "ssh_st_8f2a_20260810",
      "student_ref": "st_8f2a",
      "occurred_at": "2026-08-10T09:00:00+09:00",  // timezone-aware 필수
      "from_status": "paused",
      "to_status": "returned"               // students[].status도 returned여야 한다(불일치 시 400)
    }
  ]
}
```

> ### `[A 확정 통보 — 2026-08-03 · 승우 합의]` `passage_ref` 신설
>
> **필드:** `learning_events[].passage_ref` (string, **nullable**)
>
> **의미:** **지문/자료 묶음 참조** — 같은 지문·도표·〈보기〉 자료를 공유하는 문항들이 **같은 값**을 갖는다. **재출제 시에도 유지**되어야 한다(백엔드 DB 기준). 비지문 자료(도표·보기)도 포함하므로 "지문"에만 국한되지 않는다.
>
> **null 허용:** 지문이 없는 문항(문법 단문·어휘 등)과 **묶음 개념이 없는 학원**은 비운다. **비워도 안전하다** — 그 문항은 기대치 산출에서 전체 평균 폴백으로 떨어지며, 이는 **보정 없음과 동치**다(회귀 위험 0).
>
> **AI는 이 값을 역참조하지 않는다** — `record_id`(근거 조회용 DB PK)와 달리 **불투명 키**이며 조합 통계의 그룹 키로만 쓴다. 그래서 `_ref` 관례를 따른다.
>
> **왜 필요한가:** 기대치 입력 층이 "어려운 지문이 걸린 주라 떨어진 것"과 "진짜 무너진 것"을 구분한다. 실측상 **지문 × `type_tag`가 문항 단위 효과의 66%를 회수**하고, 영역×유형 같은 속성 단위는 **3%뿐**이다(`part_a/13` §4-3). `type_tag`는 이미 오므로 **추가로 받을 값은 이 하나**다.
>
> **언제부터:** 기대치 층이 켜지는 시점부터. **그전에 보내도 무해**하고(무시됨), 안 보내도 전량 폴백으로 동작한다.

→ 응답: 신호 목록(evidence·브리핑 문장 + display_label·lifecycle 포함) + stats. **observed_only 목록은 제거(7/16)** — `stats.excluded_under_2w` 숫자만. 상세는 명세 `docs/part_a/09_detect_spec.md` §3.

---

## 2. `POST /v1/feedback` — 경보 평가 회신 🕓 보류(7/16)

🕓 **보류(7/16) — 임계 캘리브레이션 재개 시 활성.** API·화면 버튼 모두 이번 범위에서 뺀다. `/detect` 응답의 `signal_id`는 향후 회신 대비 백엔드가 계속 저장한다. 상세는 `04_api_contract.md` §3.2.

---

## 3. `POST /v1/confirmations` — 제안 확정·수정 회신

```json
{
  "kind": "tag",                            // tag | label | classification | draft_edit
  "suggestion_id": "0a1b...",
  "action": "corrected",                    // confirmed | rejected | corrected
  "corrected_value": {                      // corrected일 때만
    "area_tag": "language",                 // 수능 enum (Open-11)
    "type_tag": "concept",
    "item_format": "mcq"
  }
}
```

```json
// kind=draft_edit — 강사 수정 diff 회신 (문체 프로필 재료)
{
  "kind": "draft_edit",
  "suggestion_id": null,
  "draft_id": "d_7788",
  "action": "corrected",
  "corrected_value": {
    "block_seq": 2,
    "original_text": "다만 추론 유형에 들어가며...",
    "edited_text": "요즘 추론 문제를 새로 시작해서..."
  }
}
```

---

## 4. `POST /v1/drafts` — 초안 생성 (202, 문의 도착 즉시 / 리포트)

```json
{
  "kind": "reply",                          // reply | report(월별 일괄 — 매월 1일 전월분, 할당 미소모) | report_single(단건 수시 — limit_kind: report_single) | counsel_pack_single
  "student_ref": "st_8f2a",
  "guardian_ref": "gd_11b0",
  "label_snapshot": {                       // 백엔드 확정 라벨만 (ai_suggested 금지)
    "comm": "narrative",                    // data | narrative
    "interest": "attitude",                 // grade | attitude | admission
    "sensitivity": "anxious",               // anxious | direct
    "frequency": "frequent"                 // frequent | monthly
  },
  "inquiry": {                              // kind=reply일 때만
    "inquiry_ref": "iq_204",
    "body_text": "요즘 아이가 힘들어하는 것 같은데 학원에서는 뭘 하고 있는 건가요?",
    "received_at": "2026-07-10T21:04:00+09:00"
  },
  "context": {
    "interventions": [
      { "record_id": "iv_31", "type": "counsel", "memo_text": "학교 시험 불안 언급", "at": "2026-05-16T20:00:00+09:00" }
    ],
    "comm_history": [                       // 최근 10건·90일, 백엔드 1차 마스킹 (Open-4d)
      { "record_id": "cm_88", "direction": "inbound", "text": "숫자로 정리해 주세요", "at": "2026-06-12T10:11:00+09:00" }
    ],
    "benchmarks": {                         // kind=report·report_single일 때만 — 백엔드 집계 API 산출, AI는 수치 생성 불가
      "national_percentile": { "value": 68, "as_of": "2026-06-30", "source": "모의고사연계 (Open-9)", "audience": "guardian" },
      "class_avg":           { "value": 74, "audience": "teacher_only" }   // 학부모向 draft의 LLM 컨텍스트에서 구조적 제외(프롬프트 미포함 = 유출 불가)
    }
  }
}
```

→ 202 `{ "draft_id": "..." }` → `GET /v1/drafts/{draft_id}`로 조회
**리포트 규약:** 대상 선정(15일 규칙)은 백엔드 소유 — AI는 받은 목록대로 생성하되 DataSufficiency 게이트(2주 미만 거부)가 우선(Open-10). 응답 블록에 `block_type: "chart_analysis"` + `chart_ref`(영역별 약점 · 개선 추이 · 전국 백분위) — 수치는 코드/백엔드 확정, LLM은 해설 문장만.

## 4b. `POST /v1/drafts/{draft_id}/refine` — 채팅형 다듬기 (202, 핑퐁)

```json
{
  "scope": "block",                         // whole | block
  "block_seq": 2,                           // scope=block일 때만
  "instruction": "마지막에 다음 상담 일정을 제안하는 문장 하나 넣어주세요. 전체적으로 조금 더 짧게.",
  "preset": null                            // 또는 softer | conclusion_first | shorter (버튼 = 프리셋)
}
```

→ 202 → `GET /v1/drafts/{draft_id}` 조회 시 `revision_no` 증가, `revisions[]`에 턴 이력.
**규약:** 다듬기 1턴 = 상담 초안 할당 1 소모 — **과금·차단은 백엔드 집행(7/15 확정 BE-4), AI는 쿼터 무관·무제한 처리(세션 턴 제한 없음).** 매 턴 결과도 게이트 전체(Evidence·SourceGrounding·ToneSafety) 재통과 — 지시에 없는 수치·근거는 생성 불가, 강사 지시가 게이트를 이기지 못함(차단 시 사유 반환). 이전 리비전으로 롤백은 `{ "revert_to": 1 }`(할당 미소모).

---

## 5. `POST /v1/classify` — 문의 분류 (동기, 문의 도착 즉시)

```json
{
  "inquiry_ref": "iq_205",
  "body_text": "여름방학 특강 시간표가 궁금합니다"
}
```

→ `{ "topic": "schedule", "urgency": "normal", "confidence": 0.95 }`

---

## 6. `POST /v1/tags/suggest` — 태그 제안 (동기, 과제 입력 화면)

```json
{
  "source_kind": "trackB_grading",          // trackA_upload | trackB_grading
  "title_text": "6월 모의고사 비문학 대비 #3"
}
```

→ `{ "suggestion_id": "...", "area_tag": "reading", "type_tag": "infer", "item_format": "mcq", "confidence": 0.92, "cached": true }` — area는 수능 6영역 enum(Open-11)

---

## 7. `POST /v1/labels/suggest` — 라벨 제안 (202, 강사 요청)

```json
{
  "guardians": [
    {
      "guardian_ref": "gd_11b0",
      "history": [                          // 이력 5건 이상인 학부모만
        { "record_id": "cm_88", "direction": "inbound", "text": "숫자로 정리해 주세요", "at": "2026-06-12T10:11:00+09:00" },
        { "record_id": "cm_91", "direction": "inbound", "text": "점수 추이 표로 부탁드려요", "at": "2026-07-01T09:30:00+09:00" }
      ]
    }
  ]
}
```

---

## 8. 스마트 데이터 이전 `/v1/imports` 🔴 **(2026-08-22 삭제 — v1 범위 밖)**

> 🔴 **이 요청 예시들은 더 이상 계약이 아니다.** import 축은 v1 에서 개발하지 않기로 했고
> 엔드포인트 셋(`POST /v1/imports` · `GET /v1/imports/{job_id}` · `POST .../confirm`)이 **삭제됐다**
> — `docs/04_api_contract.md` §3.8 · 99 #187.
>
> ⚠ 종전 예시(`file_url`·`file_hash`·`spec_overrides`)는 **git 이력과 `docs/handoff/`** 에 남는다.
> 🔴 **여기 남겨 두면 이 문서가 「있다」고 말한다** — 04 §3.8 은 「없다」고 말한다(같은 사실을 두 문서가
> 반대로 말하는 형태 · #02).
>
> ⚠ **절 번호는 안 당겼다** — 뒤 절(§9·§10…)을 가리키는 자리가 있어 번호를 밀면 그것들이 갈린다.

---

## 9. 상담팩 에이전트 `/v1/agents/counsel-pack`

```json
// ① POST — 기동 (202)
{
  "class_ref": "cl_a1",
  "student_refs": ["st_8f2a", "st_3c1d", "st_77aa"],
  "contexts": {                             // 학생별 — 4번 context와 동일 구조
    "st_8f2a": {
      "guardian_ref": "gd_11b0",
      "label_snapshot": { "comm": "narrative", "interest": "attitude", "sensitivity": "anxious", "frequency": "frequent" },
      "interventions": [ { "record_id": "iv_31", "type": "counsel", "memo_text": "...", "at": "..." } ],
      "comm_history":  [ { "record_id": "cm_88", "direction": "inbound", "text": "...", "at": "..." } ]
    }
  }
}
```

```json
// ② GET /v1/agents/{agent_run_id} — 진행 조회 (body 없음)
// ③ POST /v1/agents/{agent_run_id}/resume — paused 재개 (body 없음)
{}
```

---

## `detection_evidence` — 없으면 어떻게 되나 (8/12 · 99 #43)

**optional이라 기존 요청은 그대로 파싱된다.** 다만 세 규칙의 동작이 달라진다.

| 규칙 | 배열 없음 |
| --- | --- |
| R1 · R4 · R6 | **기존대로 판정** |
| **R2 · R3 · R5** | **미판정** + `stats.rules_skipped`에 `authoritative_evidence_missing` |

🔴 **다른 기록을 근거로 대신 삼지 않는다**(fail-closed). 종전에는 근거가 비면 **그 학생의
가장 최근 아무 `learning_event`** 를 인용해서, R2가 과거 `solve`를, R5가 복귀 전 `solve`를
근거로 실었다.

### 🔴 전송 계약 — 원시 기록은 증분, 주간 집계는 **rolling 10주 동봉** (8/12 확정)

| 배열 | 전송 범위 |
| --- | --- |
| `learning_events` | **기존 계약대로 증분** |
| `detection_evidence.assignment_window` | **학생별 최근 10주 전량** |
| `detection_evidence.weekly_activity` | **학생별 최근 10주 전량** |
| `detection_evidence.enrollment_transition` | 분석 주에 해당하는 전환 이력 |

⚠ **집계는 작다** — 실측(데모 학생 10명): `detection_evidence` **201행 · 38.9KB**로
전체 요청의 **9.7%**다(학생당 **20.1행 ≈ 3.9KB**). 학생 수에 **선형**이다.

🔴 **자르면 판정이 달라진다**(실측): 이번 주 집계만 보내면 R2·R3가
`authoritative_evidence_missing`으로 **skip**된다 — 분자(이번 주)와 분모(평소)를 **같은 자로**
재야 하고, 두 측정을 섞느니 판정하지 않기 때문이다. **빠진 주는 0이 아니라 「측정 부재」**다.

⚠ **AI PG에 집계 전문을 복제 저장하지 않는다** — 그래서 이 축을 저장이 아니라 **요청 계약**으로
닫는다(`db/models.py`·마이그레이션 무접촉). 전량 스냅숏과 증분 요청이 **같은 판정**을 내는지
회귀 검사가 값으로 지킨다(발화·score·evidence·`signal_id`·`rules_skipped` 전부).

⚠ **AI는 이 레코드를 복제 저장하지 않는다** — 응답 evidence의 `source_table` + `record_id`로
**백엔드가 자기 원본을 조회**한다. `source_table`은 논리명이며 AI가 SQL 식별자로 쓰지 않는다.

⚠ **R2의 「제출률 하락」 경로는 아직 열지 않았다** — 일부 제출은 연속을 끊을 뿐이고,
분모 계약이 설 때 별도로 연다(04 §1 R2 · BE-10).
