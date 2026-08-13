# [체크온] AI PostgreSQL 전체 ERD v4 — [PART_A+PART_B] 문제생성 저장 계층 반영

원칙(변경 없음): AI PG는 **산출물·실행 메타·캐시**만. 도메인 원본(학생·학습 기록·Alert 상태·문의 원문·Draft 승인)은 백엔드 DB 소유 — `…_ref`는 전부 백엔드 DB를 가리키는 **논리 참조**(물리 FK 아님). 전 테이블 `tenant_id` 필수 + **애플리케이션 계층 격리**(RLS 미도입 — 실도입 여부는 99 BE-11).

v2 추가분: `AGENT_RUN` `AGENT_STEP` · `SIGNAL_BRIEF`(ⓐ) · `INQUIRY_CLASS`(ⓑ) · `TAG_SUGGESTION`(ⓒ) · `LABEL_SUGGESTION`(ⓓ)

v3 확장: `AGENT_RUN`을 워커 3종의 영속 `WorkerJob` 실행 원장으로 확장했다. `AGENT_RUN.id`=`job_id`, `run_id`=`execution_id`, `status`=`JobPhase`이며 operation·우선순위·lease·fencing·복구 횟수·불투명한 payload/checkpoint/result 참조를 보존한다. 워커 state의 정본은 공식 LangGraph PostgresSaver가 관리하는 내부 테이블이고, 아래 26개는 애플리케이션 소유 테이블만 센다. `state_checkpoint`는 기존 호환을 위한 **마스킹된 관측 캐시**일 뿐 재개 원본으로 사용하지 않는다.

v4 확장: A-1 승인에 따라 [PART_B] 문제생성·진단 8테이블(`WEAKNESS_MAP`·`PASSAGE`·`PROBLEM_SET`·`PROBLEM_ITEM`·`VERIFICATION_RESULT`·`ITEM_REVISION`·`DIFFICULTY_CALIB`·`ITEM_CANDIDATE`)을 편입했다. 애플리케이션 소유 테이블은 **총 34개**다. `EVIDENCE_ITEM.owner_kind`에는 A-2 승인값 `problem_item`을 추가했다.

v5 확장: 기대치 입력 2테이블과 `COUNSEL_PACK_RESULT`에 이어 `COUNSEL_DRAFT_VIEW`를 편입했다. 애플리케이션 소유 테이블은 **총 38개**다. 읽기 모델 테이블은 `_CachedView`·`_DraftState` 배선 전의 자리이며 두 스냅숏을 독립 nullable 정본으로 둔다.

v6 확장(2026-08-12 · ㉻): `COUNSEL_CONTEXT_BUNDLE`을 편입하고 `DRAFT.content`를 추가했다. 애플리케이션 소유 테이블은 **총 39개**다. 🔴 **입력 묶음은 읽기 모델이 아니다** — `COUNSEL_DRAFT_VIEW`는 조회 캐시고 이 테이블은 실행 **전에** 만들어져 워커가 소비하는 입력이라 생애주기가 다르다. 한 테이블에 합치면 조회 캐시를 비우는 정리 배치가 **재개할 잡의 입력을 지운다.**

**`AI_RUN` 버전 세트:** 키 집합의 정본은 `contracts/execution.py`의 `VersionSet`이다. 공통 6종(`pipeline` · `engine` · `threshold` · `prompt` · `schema` · `contract`)과 [PART_B] 실행 전용 nullable 4종(`graph` · `taxonomy` · `verify_config` · `difficulty_calib`)으로 구성되며, **버전 컬럼은 총 10개**다. 실행 식별자·모델 정보·재현성 키·생성 시각까지 포함한 `AI_RUN` 전체 컬럼은 **총 18개**다.

`threshold_version`은 감지 임계값 시트(`threshold_config`) 버전으로 `detection` 실행에만 의미가 있고, 그 외 실행에서는 null이다. 이 값이 없으면 과거 경보를 재현할 수 없다(CLAUDE.md 불변식 8).

**공용 enum 소유 구분:** `AI_RUN.capability`의 `detection`·`composition`·`import_mapping`은 [PART_A], `diagnosis`·`problem_generation`은 [PART_B]다. `GATE_RESULT`의 `problem_set`과 `RuleValidation`·`BlindCrossSolve`·`ReleaseDecision`은 [PART_B] 출제 검증에서 사용한다.

```mermaid
erDiagram
  %% ───────── 실행 메타 (플랫폼) ─────────
  AI_RUN ||--o{ SIGNAL : "감지 실행"
  AI_RUN ||--o{ DRAFT : "생성 실행"
  AI_RUN ||--o{ LLM_CALL : "호출 기록"
  AI_RUN ||--o{ AGENT_RUN : "워커 Job 실행 원장(v3)"
  AGENT_RUN ||--o{ AGENT_STEP : "노드·도구 이력"
  AGENT_STEP |o--o| LLM_CALL : "LLM 노드인 경우"
  LLM_CALL ||--o| LLM_PAYLOAD : "본문(마스킹 전제)"

  %% ───────── detection ─────────
  SIGNAL ||--o{ EVIDENCE_ITEM : "근거 1..*"
  SIGNAL ||--o| SIGNAL_BRIEF : "문장화(v2·ⓐ)"

  %% ───────── composition ─────────
  DRAFT ||--|{ DRAFT_BLOCK : "블록 1..*"
  DRAFT_BLOCK ||--o{ EVIDENCE_ITEM : "블록별 인용"
  DRAFT ||--o{ GATE_RESULT : "게이트 이력"
  DRAFT ||--o{ DRAFT_REVISION : "핑퐁 턴 이력(v2.1)"
  AGENT_RUN ||--o{ DRAFT : "상담팩이 학생별 생성(v2)"

  %% ───────── import_mapping ─────────
  SOURCE_PROFILE ||--o{ MAPPING_SPEC : "양식당 버전들"
  MAPPING_SPEC ||--o{ IMPORT_JOB : "spec으로 N회 수입"
  IMPORT_JOB ||--o{ IMPORT_ROW_ERROR : "오류 행"
  IMPORT_JOB ||--o{ GATE_RESULT : "게이트 이력"
  MAPPING_SPEC |o--o| AGENT_RUN : "조사 에이전트가 산출(v2)"
  MAPPING_SPEC |o--o| LLM_CALL : "1-shot 추론(재사용 시 null)"

  %% ───────── diagnosis · problem_generation ([PART_B]) ─────────
  AI_RUN ||--o{ WEAKNESS_MAP : "진단 실행"
  AI_RUN ||--o{ PROBLEM_SET : "출제 실행"
  WEAKNESS_MAP ||--o{ PROBLEM_SET : "출제 입력(수동 목표는 null)"
  PROBLEM_SET ||--|{ PROBLEM_ITEM : "문항 1..*"
  PROBLEM_SET ||--o{ ITEM_CANDIDATE : "슬롯별 생성 후보"
  PASSAGE ||--o{ PROBLEM_ITEM : "지문 공유(null 가능)"
  PROBLEM_ITEM ||--o{ VERIFICATION_RESULT : "게이트 이력"
  PROBLEM_ITEM ||--o{ ITEM_REVISION : "refine 턴"
  PROBLEM_ITEM ||--o{ EVIDENCE_ITEM : "rationale 근거"
  PROBLEM_SET ||--o{ GATE_RESULT : "게이트 요약"
  PASSAGE |o--o| LLM_CALL : "생성 호출(풀 선택 시 null)"
  VERIFICATION_RESULT |o--o| LLM_CALL : "교차 풀이(규칙 검증 시 null)"
  ITEM_REVISION |o--o| LLM_CALL : "AI 수정 호출(null 가능)"

  AI_RUN {
    uuid execution_id PK
    varchar tenant_id "teacher alias · 격리 키"
    varchar capability "detection|composition|import_mapping|diagnosis|problem_generation"
    varchar pipeline_version
    varchar engine_version
    varchar threshold_version "감지 임계값 시트 버전 — detection 외에는 null"
    varchar prompt_version "프롬프트를 쓰지 않는 capability에서만 null — 선언 축 · counsel·classify·detect·imports·pg는 호출 0건이어도 채운다(04 §2.2)"
    varchar schema_version
    varchar contract_version "API 계약 버전 — meta.versions.contract"
    varchar graph_version "교육과정 그래프 버전 — diagnosis·problem_generation 외에는 null"
    varchar taxonomy_version "영역·유형 공용 어휘 버전 — 관련 [PART_B] 실행 외에는 null"
    varchar verify_config_version "진단·품질 게이트 설정 버전 — 관련 [PART_B] 실행 외에는 null"
    varchar difficulty_calib_version "난이도 보정 버전 — problem_generation 외에는 null"
    varchar model_provider
    varchar model_name
    jsonb generation_params "그 실행이 실제로 쓴 샘플링 파라미터 — 사용 축 · LLM 호출이 0건이면 null"
    varchar input_snapshot_hash "재현성 키"
    timestamptz created_at
  }
  IDEMPOTENCY_RECORD {
    uuid id PK
    varchar tenant_id "격리 키"
    varchar endpoint "예: POST /v1/detect — 키 스코프"
    varchar idempotency_key "= tenant + analysis_date 등 (요청 헤더 — 09 §2)"
    varchar snapshot_hash "바디 동일성 판정 — 04 부록 A canonical 해시"
    jsonb response_body "저장된 응답 envelope (같은 키+같은 hash면 재반환)"
    timestamptz created_at "TTL 30일 — alert_context 창과 정합(D-② 확정). 초과분 정리 배치"
  }
  AGENT_RUN {
    uuid id PK "WorkerJob.job_id"
    uuid run_id "WorkerJob.execution_id — 잡 실행 신원, AI_RUN이 존재하면 같은 ID로 논리 결합"
    varchar tenant_id "격리 키·lease 조회 범위"
    varchar agent_kind "counsel_pack|mapping_probe|problem_generation"
    varchar operation "고정 라우팅 5종"
    varchar payload_ref "불변 command 논리 참조"
    varchar payload_hash "sha256:64hex"
    varchar priority_class "batch|standard|interactive"
    int dispatch_attempt "lease 획득 누적 횟수"
    int lease_generation "fencing token"
    int recovery_count "lease 만료 회수 횟수"
    int max_recovery_attempts "기본 3"
    varchar lease_owner "leased|running에서만"
    timestamptz lease_acquired_at
    timestamptz lease_expires_at
    varchar checkpoint_ref "PostgresSaver checkpoint 불투명 참조"
    varchar result_ref "워커 도메인 결과 불투명 참조"
    varchar error_code "failed|cancelled 사유"
    timestamptz queued_at
    timestamptz started_at
    timestamptz finished_at
    jsonb state_checkpoint "마스킹된 관측 캐시 — 재개 정본 아님"
    varchar progress "조회용 투영, 예: 19/22"
    varchar status "queued|leased|running|paused|succeeded|failed|cancelled"
    timestamptz updated_at
  }
  AGENT_STEP {
    uuid id PK
    uuid agent_run_id FK
    int seq
    varchar node_name "plan|generate|gate|tool_call…"
    varchar tool_called "get_unique_values… (null 가능)"
    jsonb tool_args_masked "마스킹 통과분만"
    uuid llm_call_id "null 가능"
    varchar outcome
  }
  ENGINE_REGISTRY {
    varchar engine_id PK
    varchar kind "rule|ml(향후)"
    varchar version
    jsonb feature_schema "호환성 검사 메타"
    boolean is_active
  }
  FEATURE_WEEK {
    uuid id PK
    varchar tenant_id
    varchar student_ref "alias"
    date week_start
    varchar segment "normal|readapt|vacation|new_term — 정본은 detection/segments.Segment, resolve_segment가 TermContext에서 판정"
    jsonb metrics "acc·time_ratio_norm·submit_rate·type_acc"
    varchar feature_version
    timestamptz created_at "UNIQUE(tenant·student·week·ver)"
  }
  PASSAGE_TYPE_STAT {
    uuid id PK
    varchar tenant_id
    varchar passage_ref "지문/자료 묶음 — 05 learning_events"
    varchar type_tag "contracts/taxonomy.py TypeTag"
    int responses "누적 응답 수"
    int corrects "누적 정답 수"
    timestamptz updated_at "UNIQUE(tenant·passage·type)"
  }
  EXPECTATION_INGEST {
    uuid id PK
    varchar tenant_id
    varchar snapshot_hash "반영 완료 표시 — 이중 집계 방지"
    int events_applied
    timestamptz created_at "UNIQUE(tenant·snapshot_hash)"
  }
  BASELINE {
    uuid id PK
    varchar tenant_id
    varchar student_ref
    varchar segment
    jsonb metrics "이동 기준선"
    int window_weeks
    varchar version
    timestamptz updated_at
  }
  THRESHOLD_CONFIG {
    uuid id PK
    varchar tenant_id
    varchar rule_id "R1..R6"
    jsonb params
    int version "이전 버전 보존"
    varchar source "default|calibrated"
    varchar approved_by "운영자 승인"
    timestamptz created_at
  }
  SIGNAL {
    uuid id PK
    uuid run_id FK
    varchar tenant_id
    varchar student_ref
    varchar rule_id
    varchar signal_type
    varchar display_label "화면 표시 한글 문구(AI 확정) — 09 §1"
    varchar lifecycle "new|ongoing|follow_up — AI 경보 생애 판정, 09 §4"
    numeric score
    int rank "반 내 최종 표시 순번 — new·follow_up 통과분 뒤 ongoing·R5, 5 초과 가능"
    varchar metric "무엇을 잰 값인가 — accuracy·consecutive_missing_weeks 등"
    numeric observed "판정 창의 관측값 — 단위가 규칙마다 다르다(part_a/14 §3-2′)"
    numeric baseline "비교 기준값 — 임계 비교형(submit_drop·type_bias)·사건형(return_care)은 null"
    int sample_size "분모 — 문항 수 또는 기준선 주 수"
    timestamptz created_at
  }
  %% (2026-08-14) 비교값 4컬럼은 99 #60 으로 열렸다 — 준영님 양자 승인. evidence_item 은 아직이다.
  SIGNAL_BRIEF {
    uuid id PK
    varchar tenant_id
    uuid signal_ref FK
    text brief_text "LLM 한 줄 문장화"
    boolean gate_passed "왜곡 게이트: 수치·방향·라벨 일치"
    boolean fallback_used "실패 시 템플릿 폴백"
    uuid llm_call_id FK
  }
  RULE_FEEDBACK {
    uuid id PK "예약(7/16 보류) — /feedback 보류로 미적재, 캘리브레이션 재개 시 활성"
    varchar tenant_id
    varchar rule_id
    varchar alert_ref "백엔드 Alert ID(논리)"
    varchar verdict "useful|not_applicable"
    timestamptz created_at
  }
  EVIDENCE_ITEM {
    uuid id PK
    varchar tenant_id
    varchar owner_kind "signal|draft_block|problem_item"
    uuid owner_id
    varchar source_table "논리 참조 — 백엔드 DB 테이블명 또는 내부 참조 종류(evidence_pack_anchor)"
    varchar record_id "원본 PK 또는 앵커 좌표(pack_id|anchor_id)"
    varchar summary "표시용 한 줄 — ⚠ (8/14) 응답에서는 deprecated, 저장은 유지"
  }
  %% 🔴 (2026-08-14) **응답 계약에는 있는데 이 표에는 없는 필드가 넷 있다** — 의도된 상태다.
  %%    signals[].evidence[] 에 `role`·`observed`·`sample_size`·`occurred_on` 이 나가지만
  %%    (04 §2 [A 확정 통보 2026-08-14] · part_a/09) **AI PG 에는 저장하지 않는다.**
  %%    🔴 **B 도 안 쓴다**(2026-08-14 준영님 전수: import 0건 · INSERT 0건).
  %%    B 의 문항 근거는 problem_item.snapshot(JSONB) 안에 있다(part_b/09 §2-20).
  %%    ⇒ **쓰는 코드가 0건인 테이블에 컬럼만 늘리는 것은 값이 없어** 열지 않았다.
  %%    여는 조건: 이 테이블에 **실제로 적재하는 경로**가 생길 때.
  %%    ⇒ 이 ERD 는 **DB 현실**을 그린다. 없는 컬럼을 그리면 ERD 가 거짓이 되고
  %%      `test_erd_model_parity` 가 red 가 된다(컬럼 단위로 대조한다).
  %%    **여는 조건: 원장에도 남길지 결정 → 승인 → 모델·마이그레이션·ERD 동시 갱신** (99 #60)
  DRAFT {
    uuid id PK
    uuid run_id FK
    uuid agent_run_id "상담팩 산출 시(v2·null 가능)"
    varchar tenant_id
    varchar kind "reply|report|counsel_pack"
    varchar student_ref
    varchar guardian_ref
    jsonb label_snapshot "4축 열거형 · 생성 시 동결"
    varchar status "generated|template_only|rejected_insufficient|failed"
    varchar fail_reason
    timestamptz created_at
    text content "🔴 게이트를 통과한 초안 본문 전문 — 없던 것이 의도가 아니라 결손이었다(㉻). NOT NULL · 0010 시점 행 0건이라 무손실. DRAFT_BLOCK.content(블록 분해)·DRAFT_REVISION(핑퐁 이력)과 다른 축"
  }
  DRAFT_BLOCK {
    uuid id PK
    uuid draft_id FK
    int seq
    varchar block_type "greeting|fact|suggestion|closing"
    text content "비었으면 재시도 소진 섹션"
    int regen_count "≤3"
  }
  DRAFT_REVISION {
    uuid id PK
    uuid draft_id FK
    int turn_no "상한 없음 — 턴당 상담 초안 할당 1 소모(월 할당이 자연 상한)"
    varchar scope "whole|block"
    int block_seq "scope=block일 때"
    text instruction "강사 자유 지시 (문체 프로필 재료)"
    varchar preset "softer|conclusion_first|shorter|null"
    jsonb result_blocks "이 턴의 블록 스냅숏 (롤백용)"
    boolean gates_passed "매 턴 게이트 재통과"
    varchar blocked_reason "지시가 게이트에 막힌 경우 사유"
    uuid llm_call_id FK
    timestamptz created_at
  }
  GATE_RESULT {
    uuid id PK
    varchar tenant_id
    varchar owner_kind "draft|import_job|problem_set"
    uuid owner_id
    varchar gate_name "Consent|DataSufficiency|Evidence|SourceGrounding|ToneSafety|RequiredField|MappingConfidence|TeacherConfirm|RuleValidation|BlindCrossSolve|ReleaseDecision"
    int seq
    boolean passed
    varchar reason
  }
  COUNSEL_PACK_RESULT {
    uuid id PK "pack://<id> 의 id — AGENT_RUN.result_ref가 가리키는 키(AGENT_RUN.id와 다르다 · 다형 참조라 물리 FK 없음)"
    varchar tenant_id "격리 술어 — 전 테이블 필수"
    varchar class_ref "파기 술어 — 반 단위 삭제의 유일한 컬럼 축(조회 축 아님)"
    varchar plan_outcome "ok|llm_failed|redaction_blocked|unparsed|all_dropped — 강조점 0건의 사유를 세는 집계 축"
    timestamptz created_at "보존기간·정리 배치의 축 — INDEX(tenant_id, created_at)"
    jsonb snapshot "🔴 정본 — CounselPackResultRecord 전문. 위 넷은 여기서 유도한 파생이다"
  }
  COUNSEL_CONTEXT_BUNDLE {
    uuid id PK "context://<id> 의 id — 워커 payload_ref가 가리키는 키(AI_RUN보다 먼저 생기므로 물리 FK 없음)"
    varchar tenant_id "격리 술어 — 전 테이블 필수 · 조회는 id + tenant_id"
    varchar class_ref "파기 술어 — 반 단위 삭제 축"
    jsonb contexts "🔴 정본 — ContextBundleRecord.contexts 전문(student_ref → DraftContext). 별도 손사본을 만들지 않는다"
    varchar content_hash "계약값 — 저장 시 재계산해 대체하지 않는다(재개의 불변식 ④ 대조 대상)"
    timestamptz created_at "보존기간·정리 배치의 축 — INDEX(tenant_id, created_at)"
  }
  COUNSEL_DRAFT_VIEW {
    uuid id PK
    varchar tenant_id "격리 술어 — 전 테이블 필수 · UNIQUE(tenant_id, job_id)"
    varchar job_id "GET·refine 조회 키 — AGENT_RUN 물리 FK가 아닌 varchar 논리 참조"
    varchar status "queued|leased|running|paused|succeeded|failed|cancelled — CounselDraftJobView의 JobPhase 조회 투영"
    uuid execution_id "AI_RUN 조인 축 · template_only·근거 0건처럼 원장 행이 없으면 null"
    timestamptz updated_at "정리 배치 축 — 두 캐시에 없는 저장 시각"
    jsonb view_snapshot "🔴 정본 ① — _CachedView 전문 · 독립 축출되므로 null 가능"
    jsonb draft_snapshot "🔴 정본 ② — _DraftState 전문 · 독립 축출되므로 null 가능"
  }
  WEAKNESS_MAP {
    uuid id PK
    uuid run_id FK
    varchar tenant_id
    varchar student_ref "alias"
    date week_start "주차 경계 — 스냅숏 계약과 동일"
    varchar graph_version
    varchar taxonomy_version
    varchar config_version "B 기본값 시트 버전"
    varchar snapshot_hash "재현 키"
    jsonb cells "area×type: acc·n·verdict·severity"
    jsonb nodes "노드 verdict"
    jsonb propagated "역전파 — root_candidate"
    boolean overall_low "전면 부진 플래그"
    timestamptz computed_at "UNIQUE(tenant·student·graph_ver·week_start)"
  }
  PASSAGE {
    uuid id PK
    varchar tenant_id
    varchar source_kind "generated|licensed_pool"
    varchar source_ref "풀 작품 ID 논리 참조 · generated는 null"
    varchar license_ref "라이선스·작품 버전 · T3 필수"
    varchar area_tag "reading|literature"
    varchar topic
    int word_count "어절 실측"
    jsonb complexity
    text content "generated만 · licensed_pool은 null"
    uuid llm_call_id FK "풀 선택 시 null"
  }
  PROBLEM_SET {
    uuid id PK
    uuid run_id FK
    varchar tenant_id
    varchar target_kind "student|class"
    varchar target_ref "alias 논리 참조"
    varchar target_source "weakness_auto|teacher_manual"
    uuid weakness_map_id FK "자동 목표만 · 수동 목표는 null"
    jsonb request "area·type·수량·requested_difficulty"
    varchar status "queued|generating|generated|partial_success|failed"
    varchar summary
    varchar stop_reason "조기 중단 사유 · null 가능"
    boolean diagnostic_purpose
    timestamptz created_at
  }
  PROBLEM_ITEM {
    uuid id PK
    uuid set_id FK
    int slot_index "조회 키 — UNIQUE(set_id, slot_index) · ProblemItemStore.get이 요구한다(09 §2-20 결정 ①)"
    jsonb snapshot "🔴 정본 — StoredProblemItem 전문. 아래 투영은 전부 여기서 유도한다(결정 ② B안)"
    uuid passage_id FK "T1은 null"
    varchar area_tag "투영 · 본문 없는 슬롯은 null"
    varchar type_tag "fact|infer|critic|concept — apply는 v1 미산출 예약이라 이 컬럼에 안 들어온다(99 ㊣ · 요청 문에서 400) · 투영 · 본문 없는 슬롯은 null"
    varchar item_format "mcq — v1 · 투영 · 본문 없는 슬롯은 null"
    varchar skill_node_id "null 가능"
    text stem "투영 · 본문 없는 슬롯은 null"
    jsonb choices "mcq 선지 5 · 투영 · 본문 없는 슬롯은 null"
    jsonb answer "투영 · 본문 없는 슬롯은 null"
    text rationale "근거 인용 필수 · 투영 · 본문 없는 슬롯은 null"
    numeric difficulty_est "투영 · null 가능(ItemResult가 옵셔널)"
    numeric difficulty_fit "null 가능 · v1 항상 null"
    varchar difficulty_calib_ver "null 가능 — 저장 시점에 대응 값이 없다(09 §2-20 #5)"
    boolean review_badge "투영 — 사유(review_reason)는 스냅숏 안이다"
    int current_revision_no "낙관적 잠금 기준 — 투영이 아니라 저장소 소유 상태"
    varchar status "verified|needs_review|dropped|verification_unavailable — 투영"
    varchar drop_reason "null 가능 — 폐기 사유 전용. 최종본 저장소 경로에서는 항상 null(09 §2-20 #12)"
  }
  VERIFICATION_RESULT {
    uuid id PK
    uuid item_id FK
    varchar stage "rule_validation|blind_cross_solve|release_decision"
    boolean passed
    jsonb detail "실패 규칙·풀이·confidence·정렬 판정"
    uuid llm_call_id FK "규칙 검증은 null"
    int attempt_no "item_attempt 회차"
  }
  ITEM_REVISION {
    uuid id PK
    uuid item_id FK
    int turn_no "= revision_no"
    varchar revision_kind "ai_refine|teacher_direct|rollback"
    text instruction "redaction 후 저장 · rollback은 null"
    jsonb result_snapshot "전체 스냅숏 · 차단 시 null"
    jsonb diff "변경 전후 diff"
    boolean verifications_passed
    varchar blocked_reason "null 가능"
    uuid llm_call_id FK "teacher_direct·rollback은 null"
  }
  DIFFICULTY_CALIB {
    uuid id PK
    varchar tenant_id
    jsonb params "가중치"
    int version "이전 버전 보존"
    varchar source "default|calibrated"
    varchar approved_by_ref "alias 논리 참조"
    timestamptz created_at
  }
  ITEM_CANDIDATE {
    uuid id PK
    uuid set_id FK
    varchar tenant_id
    int slot_index
    int attempt_no "1..3"
    jsonb snapshot "GeneratedItem 전문 · 불변"
    jsonb gate_summary "게이트 ①② 판정"
    numeric difficulty_est
    timestamptz created_at "UNIQUE(tenant·set·slot·attempt)"
  }
  INQUIRY_CLASS {
    uuid id PK
    varchar tenant_id
    varchar inquiry_ref "백엔드 문의 ID(논리)"
    varchar topic "AI 예측(고정) grade|schedule|counsel_request|etc"
    varchar sentiment "AI 예측(고정) normal|complaint"
    varchar urgency "AI 예측(고정) immediate|normal"
    numeric confidence_topic
    numeric confidence_sentiment
    numeric confidence_urgency
    varchar corrected_topic "NULL=그 축 안 바꿈"
    varchar corrected_sentiment "NULL=그 축 안 바꿈"
    varchar corrected_urgency "NULL=그 축 안 바꿈"
    timestamptz reviewed_at "NULL=평가셋 분모 제외(미검토)"
    uuid llm_call_id FK
  }
  LABEL_SUGGESTION {
    uuid id PK
    varchar tenant_id
    varchar guardian_ref
    varchar axis "comm|interest|sensitivity|frequency"
    varchar value "축별 열거형만"
    jsonb evidence_quotes "마스킹 인용문 — 실존 게이트 통과분"
    numeric confidence
    varchar status "suggested|confirmed|rejected"
  }
  LLM_CALL {
    uuid id PK
    uuid run_id FK
    varchar role "generator|verifier|mapper|classifier(v2)|narrator(v2.1)|counselor(v2.2)"
    varchar provider
    varchar model
    varchar prompt_id
    varchar prompt_version
    int tokens_in
    int tokens_out
    numeric cost_usd
    int latency_ms
    varchar outcome "ok|parse_fail|field_missing|bad_ref|timeout|provider_error|redaction_blocked — 정본은 contracts/llm.CallOutcome, 재시도 정책은 error_codes §3"
    timestamptz created_at
  }
  LLM_PAYLOAD {
    uuid call_id PK "FK llm_call"
    text request_masked "redaction 통과본만 · 전송분 그대로(변형 금지)"
    text response_raw "이름은 raw지만 마스킹 통과본 — TTL 30일"
  }
  STYLE_PROFILE {
    varchar tenant_id PK
    jsonb features "선호 어휘·톤 (수정 diff 누적)"
    timestamptz updated_at
  }
  SOURCE_PROFILE {
    uuid id PK
    varchar tenant_id
    varchar file_hash "멱등키"
    varchar filename
    jsonb sheets "시트·헤더·타입·샘플 통계 · 양식 시그니처"
    timestamptz created_at
  }
  MAPPING_SPEC {
    uuid id PK
    varchar tenant_id
    uuid source_profile_id FK
    int version
    jsonb spec "컬럼매핑·변환규칙·신뢰도·미매핑사유"
    varchar status "inferred|confirmed|rejected"
    uuid inferred_by_call "1-shot 시 · 재사용 시 null"
    uuid probe_agent_run "조사 에이전트 산출 시(v2·null 가능)"
    timestamptz confirmed_at
  }
  IMPORT_JOB {
    uuid id PK
    varchar tenant_id
    uuid spec_id FK
    varchar file_hash
    varchar status "profiled|preview|confirmed|transformed"
    int row_total
    int row_ok
    int row_fail
    timestamptz created_at
  }
  IMPORT_ROW_ERROR {
    uuid id PK
    uuid job_id FK
    int row_no
    varchar column_name
    varchar reason
  }
  TAG_SUGGESTION {
    uuid id PK
    varchar tenant_id
    varchar source_text_hash "과제명 해시 = 캐시 키(재호출 방지)"
    varchar source_kind "trackA_upload|trackB_grading"
    varchar area_tag "수능 6영역: reading|literature|speech|writing|language|media (Open-11 확정 7/15)"
    varchar type_tag "fact|infer|critic|concept — item_format(mcq|short|essay)와 병행 표기 · 정본은 V1_TYPE_TAGS(99 ㊣ 산출 축)"
    numeric confidence
    varchar status "suggested|confirmed|rejected"
    uuid llm_call_id FK
  }
```

> 🔴 **`INQUIRY_CLASS`는 평가셋 테이블이다**(`part_a/03` §C7 · 99 ⓑ · B 양자 승인). 3축은 **AI 예측 고정**이고 덮어쓰지 않는다 — 강사 정정은 `corrected_*`에 따로 쌓는다. 예측을 정정값으로 덮으면 (입력·예측·정답) 3요소가 깨져 평가셋 목적이 사라진다.
>
> `corrected_by_teacher`는 **컬럼이 아니라 파생값**이다: `corrected_topic IS NOT NULL OR corrected_sentiment IS NOT NULL OR corrected_urgency IS NOT NULL`.
>
> 판정 해석 — ① `reviewed_at IS NULL` → **평가셋 미편입**(분모 제외) ② `reviewed_at NOT NULL` + `corrected_topic IS NULL` → topic 축 **AI 정답** ③ `corrected_topic IS NOT NULL` → topic 축 **AI 오답**(정답 = `corrected_topic`). `reviewed_at`이 없으면 "안 고쳤다"와 "안 봤다"가 뭉개져 **정확도가 과대평가된다**.
>
> CHECK `ck_inquiry_class_corrected_requires_review` — 정정이 있으면 `reviewed_at`도 있어야 한다. ✅ **적재·정정 수신 구현 완료(P2-c)** — `POST /v1/classify`가 예측을 적재하고(같은 문의 재호출은 캐시 히트 · LLM 0회) `POST /v1/confirmations`가 정정을 받는다. 자연키 `uq_inquiry_class_scope`(0006)가 그 전제다. ⚠ `llm_call_id`는 아직 NULL이다(99 ⓕ·㊻ 선행 의존).


> ✅ **A+BE 확인 완료(2026-07-30) — SIGNAL.rank.** `rank`는 **반 내 최종 표시 순번**이다 — `new`·`follow_up` 통과분(1..N) 뒤에 `ongoing`·R5가 이어붙어 **`cap_max`(5)를 초과할 수 있다**. **백엔드 DTO에 `rank` ≤ `cap_max` 제약이 없음을 2026-07-30 확인**했다. `capped_out`은 lifecycle 억제 후 `new`·`follow_up` 후보의 탈락 수만 센다. 정본: 04 §3 · 09 §4 · 99 #14.

## 보존 순서 규약 (2026-08-12 · ㉻ · 지시서 73 §8)

🔴 **아래 넷은 멱등 레코드보다 먼저 삭제되면 안 된다.**

    COUNSEL_CONTEXT_BUNDLE · DRAFT · COUNSEL_PACK_RESULT · COUNSEL_DRAFT_VIEW
      ⩾ IDEMPOTENCY_RECORD

⚠ 순서가 뒤집히면 **멱등 202 뒤 GET이 404**이거나 **재개가 입력을 못 찾는다**: 멱등 레코드가
살아 있으면 POST가 이전 `job_id`로 202를 주는데 그 잡의 산출물이 이미 지워졌기 때문이다.
🔴 **이건 가정이 아니라 실측이다** — 통합 검사가 `counsel_draft_view`만 지웠더니 두 번째
회차부터 404였다(99 #39 · ㉿ ⓓ가 «멱등은 남고 본문은 사라진 비대칭»으로 재현된 자리).

⚠ **입력 묶음은 조회 캐시보다 오래 살아야 한다** — `COUNSEL_DRAFT_VIEW`를 비우는 정리
배치가 `COUNSEL_CONTEXT_BUNDLE`을 같이 지우면 **재개할 잡의 입력이 사라진다.** 두 테이블을
합치지 않은 이유가 이것이다.

🔴 **실제 삭제 스케줄러·TTL 숫자는 이 문서가 정하지 않는다** — 별도 운영 안건이다.
**「상시 보존이 구현됐다」고 읽지 마라**: 지금 있는 것은 **순서 규약과 그 축이 될 인덱스**
(`ix_counsel_context_bundle_tenant_created` 등)뿐이고, 지우는 코드는 없다.

**증분 반영 메모:** ① `DRAFT.agent_run_id` · `MAPPING_SPEC.probe_agent_run` 컬럼 추가(에이전트 산출 연결, 기존 경로는 null) ② `LLM_CALL.role`에 `classifier` 추가(ⓑⓒⓓ) · **(v2.1) `narrator` 추가**(브리핑 문장화 전용 — 09 §1-10 · varchar라 마이그레이션 없음) · **(v2.2) `counselor` 추가**(상담 초안 문장화 전용 — B 동의 7/30 · varchar라 마이그레이션 없음) ③ `SOURCE_PROFILE.sheets`에 양식 시그니처 포함(재수입 매칭 키) ④ 양자 승인 대상은 기존과 동일(EVIDENCE_ITEM 구조·LLM_CALL 지표 필드) + `TAG_SUGGESTION`의 area/type enum은 B의 약점 지도와 공용 어휘이므로 **[A+B]** ⑤ **(v2.1) `DRAFT_REVISION` 추가**(핑퐁 턴 이력) · 사용량 미터링은 **일일 턴제**로 확정 — `llm_usage`를 `(tenant_id, date)` 그레인으로 변경: `usage_daily(tenant_id, date PK, interactive_turns int, batch_jobs jsonb)`. 인터랙티브 턴만 일일 한도 대상, 일괄 작업(상담팩·리포트)은 월 단위 작업 카운트(게이팅 소유는 백엔드 Billing — AI는 미터링 리포트만).

**(D-② 확정 통보 · 7/22)** `IDEMPOTENCY_RECORD` 신설 — 멱등 저장소의 프로세스 인메모리(재시작 소실·멀티워커 비공유, 99 ⑨)를 영속화한다. **유니크 제약 `(tenant_id, endpoint, idempotency_key)`** — 동시 삽입 경합은 이 제약으로 원자성 보장(B 크로스체킹 스코프 제안 수용). 같은 키 + 같은 `snapshot_hash` = 저장된 `response_body` 재반환 · 다른 hash = 409. **TTL 30일**(`alert_context` 창과 정합 — 새 숫자 발명 없이 기존 시간 창 재사용). 재현·감사는 `AI_RUN`이 담당하므로 응답 본문을 무기한 보관하지 않는다.

**(㉝ 해소 통보 · 8/6)** `LLM_PAYLOAD` 적재 배선 — 종전에는 정의·DDL만 있고 **기록 주체가 `src/`에 0곳**이었다(불변식 8 공회전: 같은 `prompt_id@version`이 여러 다른 문면을 보내도 사후 구분 불가). ㊻(#92)의 **수집 후 영속** 경로에 얹었고 **스키마 변경·마이그레이션 0**이다.

- **기록 시점** — `LLMProvider`를 감싸는 조립부 래퍼(`capture_payloads`)가 전송된 요청·받은 응답을 포착한다. `llm/gateway.py` 무접촉.
- **삽입 순서가 계약이다** — `llm_payload.call_id`가 `llm_call.id`를 **PK 겸 FK**로 참조하므로 `LLM_CALL` → `LLM_PAYLOAD` 순서다. 한 트랜잭션에서 넣는다.
- **`request_masked`는 전송분 그대로** 넣고 저장 직전 검사는 검증으로만 쓴다 — `redact()`가 멱등이 아니어서(2차가 `학생의`를 인명으로 잡는다) 변형하면 보낸 적 없는 문면이 남는다. **`response_raw`는 이름과 달리 마스킹 통과본**이다(환각 실명 대비 · `masking_redaction` §3 신설 행). 컬럼명은 양자 파일이라 바꾸지 않았다.
- **본문 길이 상한** — `llm_payload_max_chars`(기본 32,768자, Settings). 초과분은 **버린다(절단 금지)** — 잘린 프롬프트는 "재현 가능해 보이지만 아닌" 기록이라 조용히 틀린다. 본문만 버리고 호출 메타는 남긴다.
- 🔴 **TTL 30일** — `IDEMPOTENCY_RECORD`·`alert_context` 창을 그대로 쓴다(**새 숫자 발명 없음**). `LLM_PAYLOAD`에 시각 컬럼이 없으므로 **`llm_call.created_at` 조인**으로 판정한다(컬럼 추가 없음 — 양자). 본문은 재현·감사 보조이고 원장 정본은 `AI_RUN`·`LLM_CALL`이라 무기한 보관하지 않는다.
- ⚠ **정리 배치는 미구현이다** — 규약만 섰다. 운영 축이며 99에 등재했다.

**(B-1 실행 계약 확정 · 7/27)** `AGENT_RUN`은 `contracts/agents.py`의 `WorkerJob`을 영속 투영한다. 큐 선택 인덱스는 tenant·worker·phase·priority·queued_at·id, lease 회수 인덱스는 tenant·worker·phase·lease_expires_at 순이다. `dispatch_attempt`·`lease_generation`은 lease마다 증가하고, 장애 예산은 별도 `recovery_count`만 소비하므로 정상 수동 pause/resume가 복구 상한을 깎지 않는다.
