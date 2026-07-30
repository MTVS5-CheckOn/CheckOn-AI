# [체크온] AI member-B 파트 설계 v1 — 시퀀스 · B 증분 ERD · 데이터 경계

> **지위:** member-B(염준영) 공식 설계 v1. 전체 파이프라인은 [`01_pipeline.md`](01_pipeline.md), 본 문서는 시퀀스·ERD·데이터 경계. 우선순위: 공용 계약 > 02_ownership > 99_open_items > part_a > 본 문서.
>
> **변경 이력**
> - v1.2 (2026-07-30): `PROBLEM_ITEM.type_tag`를 값 복제 대신 `contracts/taxonomy.py`의 공용 `TypeTag` 참조로 정합. 어휘 변경 시 감지 R6·약점 지도·태깅ⓒ·출제를 같은 변경 단위로 재산정한다.
> - v1.1 (2026-07-27): B-M2-01 확정 반영 — `PROBLEM_ITEM`의 문항 자체 난이도(`difficulty_est`)와 학생 적합도(`difficulty_fit`)를 분리하고, v1의 `difficulty_fit`은 항상 null로 고정. **계약↔ERD 간극 2건 해소**: `WEAKNESS_MAP.overall_low` 컬럼 신설(`contracts/diagnosis.py`의 `WeaknessMap.overall_low`가 산출물에 존재하나 ERD에 컬럼이 없던 문제), `PROBLEM_SET.request`의 "난이도" 표기를 신설 필드 `requested_difficulty`([`05`](05_problem_generation.md) §4.1 C6)에 정합. 공용 ERD 편입 스펙은 [`09`](09_integration_proposals.md) §2-4.3.
> - v1 (2026-07-15): 구 `01_design.md`에서 파이프라인 절을 [`01_pipeline.md`](01_pipeline.md)로 분리하고 본 문서로 개편. 7/15·대화 결정 반영 — Kafka 완료 통지, meta.quota 폐기, v1 mcq만, B-4 폐기(서술형 v1 제외), 핑퐁 수정 MVP 승격, 수동 목표 출제(`target_source`), 낙관적 잠금. 입력 초안: `CODEXPROMPT/(염준영)_파트_설계_v0.md` · `CODEXPROMPT/(염준영)_출제스튜디오_Step3_검증라벨_핑퐁수정_요구사항_v0.md`.

---

## 1. 시퀀스 다이어그램

### 1-A. 맞춤 문항 세트 생성 — 진단→생성→3단계 품질 게이트→검수

API 경로는 정본 계약에 아직 없다 — 아래 경로는 `[제안]`([`09_integration_proposals.md`](09_integration_proposals.md) §2-1). 공통 규약(공용 envelope·`X-Tenant-Id`/`X-Request-Id`/`Idempotency-Key`·202)은 준수하고, **완료 통지는 Kafka**(7/15 — GET은 디버그·복구 보조).

```mermaid
sequenceDiagram
  autonumber
  participant T as 강사 (백엔드 화면)
  participant BE as 백엔드
  participant API as AI 앱 계층
  participant SUP as 결정론 슈퍼바이저
  participant PRB as 워커③ problem_generation
  participant DIA as ai/diagnosis
  participant GW as ai/llm 게이트웨이
  participant PG as AI PostgreSQL
  participant K as Kafka (job-events)
  T->>BE: 출제 요청 (Step 2 조건)
  BE->>API: POST /problem-sets [제안] + Idempotency-Key
  API->>SUP: problem_set.generate Job enqueue
  API-->>BE: 202 { job_id } — queued
  API->>PG: AI_RUN 기록 — 공통 VersionSet 6종 + B nullable 4종 + snapshot_hash<br/>(B 버전은 WEAKNESS_MAP·PROBLEM_ITEM에도 저장 · 09 §2-9 승인 완료)
  API->>DIA: 약점 지도 산출/조회 (target_source=weakness_auto)
  Note over DIA: 데이터 부족 → rejected_insufficient(정상 상태)<br/>수동 목표(teacher_manual) 요청은 진단 생략
  DIA->>PG: WEAKNESS_MAP upsert
  SUP->>PRB: lease + request_ref + checkpoint_ref — generating
  loop 문항별 (item_attempt ≤ 3 · 조기 중단 판정 포함)
    PRB->>GW: 생성 (generator · 구조화 출력)
    GW-->>PRB: GeneratedItem (rationale+근거)
    PRB->>PRB: 게이트 ① RuleValidation (R-1~R-7)
    PRB->>GW: 게이트 ② BlindCrossSolve (verifier · 다른 모델 패밀리 · blind)
    GW-->>PRB: SolveResult (+정렬 판정) → 코드 대조
    PRB->>PRB: 난이도 추정 (difficulty_est 계산 · difficulty_fit=v1 null)
    PRB->>PRB: 게이트 ③ ReleaseDecision (난이도 불일치 포함)
    PRB->>PG: item + VERIFICATION_RESULT 저장 (체크포인트)
  end
  PRB->>PG: PROBLEM_SET 저장 — generated | partial_success | failed
  PRB->>SUP: result_ref + lease_generation
  SUP->>K: worker_job.succeeded { job_id, operation, result_ref }
  K->>BE: consume → Step 3 화면 갱신 (검증 라벨 표시)
  T->>T: 선별·수정·승인 ✋ (백엔드 HITL — 승인 전 학생 미노출)
```

### 1-B. 문항별 핑퐁 수정(refine — **MVP**)

정책 전문은 [`07_refine_policy.md`](07_refine_policy.md) — 여기는 골격만. **강사 지시가 게이트를 이기지 못한다.** 쿼터는 백엔드 소유(AI 무관 — 7/15).

```mermaid
sequenceDiagram
  autonumber
  participant T as 강사
  participant API as AI 앱 계층
  participant PRB as ai/problem_generation
  participant GW as ai/llm 게이트웨이
  participant PG as AI PostgreSQL
  loop 다듬기 턴
    T->>API: POST /problem-sets/{id}/items/{id}/refine [제안]<br/>(instruction + base_revision_no)
    API->>API: 낙관적 잠금 — base_revision_no ≠ 최신이면 409 (07 §6)
    API->>PRB: 정적 검사 (redaction·정책 — 차단 시 LLM 미호출)
    PRB->>GW: 수정안 생성 (generator)
    PRB->>PRB: 게이트 ①②③ 전체 재실행 — 부분 수정도 예외 없음
    PRB->>PG: ITEM_REVISION 저장 (diff·검증 결과·redaction된 지시문)
    API-->>T: 리비전 + 갱신 라벨 (재검증 완료 전 승인 버튼 비활성)
  end
```

### 1-C. 승인·발행 이후 (참고 — 전부 백엔드 소유)

강사 승인(Step 4) → 발행(PDF/학생 홈) → 학생 풀이 → `learning_event` 유입 → 다음 진단에 반영. AI는 이 경로에 개입하지 않는다(발송 코드 없음). 승인된 문항의 재수정 시 승인 상태 철회 절차는 `OPEN`(백엔드 소유 — 09 §3).

> 서술형 채점 분담(구 B-4)은 **7/15 폐기** — v1은 mcq만이라 서술형 자체가 없다. F17 지면 시험·OCR 소유는 P2 시점에 Open-12와 함께 재론.

## 2. ERD 증보 — AI PostgreSQL (B 소유 테이블 **7종**)

**원칙(`docs/06_erd.md`와 동일):** 산출물·실행 메타·캐시만. `…_ref`는 논리 참조. 전 테이블 `tenant_id`+RLS. 공용 테이블(AI_RUN·LLM_CALL·GATE_RESULT·EVIDENCE_ITEM)은 현재 애플리케이션 정본 26테이블을 재사용한다. 아래 B 전용 7테이블의 공용 ERD 편입은 별도 승인 요청(09 §2-4)이다.

> **공용 enum 확장(09 §2-3):** `GATE_RESULT.owner_kind` += `problem_set` · `GATE_RESULT.gate_name` += `RuleValidation|BlindCrossSolve|ReleaseDecision` — ✅ **A+B 승인·`d5283d0` 구현·공용 ERD 문서 동기화 완료**. 잔여: `EVIDENCE_ITEM.owner_kind` += `problem_item`만 `evidence/models.py` 구현 시 양자 승인.

```mermaid
erDiagram
  AI_RUN ||--o{ WEAKNESS_MAP : "진단 실행"
  AI_RUN ||--o{ PROBLEM_SET : "출제 실행"
  WEAKNESS_MAP ||--o{ PROBLEM_SET : "출제 입력(동결 · manual은 null)"
  PROBLEM_SET ||--|{ PROBLEM_ITEM : "문항 1..*"
  PASSAGE ||--o{ PROBLEM_ITEM : "지문 공유(null 가능)"
  PROBLEM_ITEM ||--o{ VERIFICATION_RESULT : "게이트 이력"
  PROBLEM_ITEM ||--o{ ITEM_REVISION : "refine 턴(MVP)"
  PROBLEM_ITEM ||--o{ EVIDENCE_ITEM : "rationale 근거"
  PROBLEM_SET ||--o{ GATE_RESULT : "게이트 요약"
  PASSAGE |o--o| LLM_CALL : "생성 호출(풀 선택 시 null)"
  VERIFICATION_RESULT |o--o| LLM_CALL : "교차 풀이(규칙 검증 시 null)"

  WEAKNESS_MAP {
    uuid id PK
    uuid run_id FK
    varchar tenant_id
    varchar student_ref "alias"
    varchar graph_version
    varchar taxonomy_version
    varchar config_version "B 기본값 시트 버전 (06 부록)"
    varchar snapshot_hash "재현 키"
    jsonb cells "area×type: acc·n·verdict·severity"
    jsonb nodes "노드 verdict — 04 §5.4"
    jsonb propagated "역전파 — root_candidate"
    boolean overall_low "전면 부진 플래그 — 04 §4 (계약 WeaknessMap.overall_low 대응)"
    timestamptz computed_at "UNIQUE(tenant·student·graph_ver·주차)"
  }
  PASSAGE {
    uuid id PK
    varchar tenant_id
    varchar source_kind "generated | licensed_pool"
    varchar source_ref "풀 작품 ID(백엔드 논리 참조) · generated: null"
    varchar license_ref "라이선스·작품 버전 (T3 필수)"
    varchar area_tag "reading|literature"
    varchar topic
    int word_count "어절 실측"
    jsonb complexity
    text content "generated만 · licensed_pool은 발췌 오프셋만"
    uuid llm_call_id FK "null 가능"
  }
  PROBLEM_SET {
    uuid id PK
    uuid run_id FK
    varchar tenant_id
    varchar target_kind "student | class"
    varchar target_ref "alias 논리 참조"
    varchar target_source "weakness_auto | teacher_manual — manual은 비개인화 표기 필수"
    uuid weakness_map_id FK "auto만 · manual은 null"
    jsonb request "area·type·수량·난이도 (format은 v1 mcq 고정)"
    varchar status "queued|generating|generated|partial_success|failed"
    varchar summary "8 verified · 1 review · 1 dropped"
    varchar stop_reason "조기 중단 사유 — null 가능 (06 §6)"
    boolean diagnostic_purpose "탐색 출제 — KPI 분리 집계"
    timestamptz created_at
  }
  PROBLEM_ITEM {
    uuid id PK
    uuid set_id FK
    uuid passage_id FK "null 가능(T1)"
    varchar area_tag
    varchar type_tag "contracts/taxonomy.py TypeTag — R6·약점 지도·태깅ⓒ·출제 공용 어휘"
    varchar item_format "mcq — v1 확정 (short·essay enum 예약)"
    varchar skill_node_id "null 가능"
    text stem
    jsonb choices "mcq 선지 5"
    jsonb answer
    text rationale "근거 인용 필수"
    numeric difficulty_est
    numeric difficulty_fit "null 가능 · v1 항상 null(산출·분기 코드 없음)"
    varchar difficulty_calib_ver
    boolean review_badge
    int current_revision_no "낙관적 잠금 기준 (07 §6)"
    varchar status "verified|needs_review|dropped|verification_unavailable — AI 내부 상태까지만"
    varchar drop_reason "generation_exhausted | banned_topic 등"
  }
  VERIFICATION_RESULT {
    uuid id PK
    uuid item_id FK
    varchar stage "rule_validation|blind_cross_solve|release_decision"
    boolean passed
    jsonb detail "실패 규칙 ID·풀이·confidence·정렬 판정(aligned 등)"
    uuid llm_call_id FK "null 가능"
    int attempt_no "item_attempt 회차"
  }
  ITEM_REVISION {
    uuid id PK
    uuid item_id FK
    int turn_no "= revision_no"
    varchar revision_kind "ai_refine | teacher_direct | rollback"
    text instruction "redaction 후 저장 · rollback은 null"
    jsonb result_snapshot "전체 스냅숏 — 롤백 단순 복원"
    jsonb diff "변경 전후 diff"
    boolean verifications_passed "매 턴 게이트 ①②③ 재통과"
    varchar blocked_reason
    uuid llm_call_id FK "teacher_direct·rollback은 null"
  }
  DIFFICULTY_CALIB {
    uuid id PK
    varchar tenant_id
    jsonb params "가중치 (06 부록)"
    int version "이전 버전 보존"
    varchar source "default | calibrated"
    varchar approved_by_ref "alias — 실명 금지"
    timestamptz created_at
  }
```

## 3. 데이터 경계 — 뭐가 어느 DB에 있나

| 데이터 | 백엔드 MySQL (원본) | AI PostgreSQL (B 소유분) |
| --- | --- | --- |
| 문항 승인·배포·학생 노출 상태 | ✅ HITL 전부 | ❌ — status는 AI 내부 검증 상태까지만 |
| 쿼터(차단·카운트·잔여 표시) | ✅ Billing 전부 (7/15) | ❌ — **AI는 쿼터를 알지 못한다** |
| 학생 제출·채점 결과(learning_event) | ✅ 원본 | ❌ — 스냅숏 페이로드로만, 저장 안 함 |
| 약점 지도 | 표시용 사본 ✅ | WEAKNESS_MAP ✅ (산출 원본) |
| 지문·문항·검증·리비전 | 승인 확정본 ✅ | PASSAGE·PROBLEM_SET·PROBLEM_ITEM·VERIFICATION_RESULT·ITEM_REVISION ✅ |
| 난이도 파라미터 | ❌ | DIFFICULTY_CALIB ✅ |
| 공유 저작물 풀(문학) | 라이선스·원문 ✅ | 풀 ID·발췌 오프셋 참조만 |
| LLM 호출·원가 | ❌ | LLM_CALL·LLM_PAYLOAD ✅ — **원가 관측용**(쿼터 아님, quota_metering §5) |

**suggested/verified 상태의 모든 문항은 강사 승인 전까지 학생에게 어떤 경로로도 노출되지 않는다.** `verification_unavailable` 문항은 재검증 성공 전까지 승인 대상에도 오르지 않으며 **수동 예외 승인 불허**(확정 — [`06_quality_gates.md`](06_quality_gates.md) §3).

## 4. OPEN 항목 (본 문서 관련분 — 총괄은 09 §3)

| 번호 | 항목 | 담당 |
| --- | --- | --- |
| B-3 잔여 | taxonomy 경계 사례 7건 판정 (enum 자체는 7/15 확정) | A+B |
| Open-12 | F17 OCR 실명→alias·OCR 소유 (P2) | BE(+A·B) |
| — | 승인된 문항 재수정 시 승인 철회 절차 | BE |
| — | 리비전 보존 기간(개수는 무제한 `[잠정]`) | BE+B |
| D-10 | B API 계약 편입 + Kafka 이벤트 증분 | BE+B |

## 5. 다음 작업 후보

- ✅ 완료: `contracts/diagnosis.py`·`contracts/problem_generation.py` 계약 및 테스트 구현(`d5283d0`)
1. **문법 DAG `curriculum_graph.yaml` 25~40노드 실작성**
2. `golden/problems/`·`golden/diagnosis/` 실파일화(08 §1) — CI 게이트가 구현보다 먼저
3. `pg_banned_topics.yaml` + B 기본값 시트(`verify_config` v1) 실파일화
4. ✅ 슈퍼바이저 실행 계약(langgraph_state §5) B 리뷰 완료 — [`01_pipeline.md`](01_pipeline.md) §6
5. B API 증분 + 공통 `worker_job.*`의 `result_ref` 조회 방식 BE 리뷰(D-10) — 09 §2-1
6. T1 기준 자료 후보 조사(D-03) — 데이터 자산 검증 우선
