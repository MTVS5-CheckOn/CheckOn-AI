# 체크온 AI 서비스 — REST API · 스냅숏 데이터 계약 v0.1 (제안)

| | |
| --- | --- |
| **상태** | 🟠 제안 초안 — member-A 작성, 리뷰 미팅 후 v1.0 승격 |
| **당사자** | AI 서비스(Python·PostgreSQL, member-A) ↔ 백엔드(Java·Spring·MySQL) |
| **읽는 법** | `[제안]` = 이견 없으면 그대로 확정 · `[Open-n]` = **결정 필요 → §1만 논의하면 30분에 끝남** |
| **확정 후** | A는 FakeSnapshot으로, 백엔드는 AI 스텁으로 **서로 안 기다리고 동시 개발** 시작 |
| **참조** | A파트 설계 v2(파이프라인·ERD) · 사전설계 체크리스트 v1 · AI 아키텍처 지시서 |

**TL;DR (5줄)**

1. AI는 **신호·초안·매핑을 만들 뿐, 발송·확정·반영은 전부 백엔드**(HITL) — 이 API에 발송 개념이 없다.
2. 어떤 페이로드에도 **실명·연락처 필드 자체가 없다** — 백엔드 DTO 차원에서 보장을 요청한다.
3. '정상적 미생성'(데이터 부족 거부, 템플릿 전용, 문장화 폴백)은 **에러가 아니라 200 + status**.
4. 빠른 것(분류·태그)은 동기, LLM 생성(초안·Import·에이전트)은 **202 + 작업 조회**.
5. 결정 필요한 건 **Open-1 ~ Open-10 열 개**뿐이다.

**목차** — §1 리뷰 안건 · §2 공통 규약 · §3 엔드포인트(퀵 레퍼런스 + 상세) · §4 스냅숏 데이터 계약 · §5 비기능 · §6 확정 절차 · 부록 A 해시 규칙

---

## §1. 리뷰 안건 — Open-1 ~ 8 (이것만 정하면 됩니다)

| # | 안건 | 선택지 | 💙 A의 제안과 이유 | 결정 |
| --- | --- | --- | --- | --- |
| **Open-1** | 감지 스냅숏 전달 방향 | 백엔드 push / AI pull | **push** — 배치 오케스트레이션이 백엔드(Spring Batch)에 있음 | ☐ |
| **Open-2** | 비동기 완료 통지 | 폴링 / 웹훅 | **폴링**(단순) — 트래픽 증가 시 웹훅 전환 | ☐ |
| **Open-3** | Import 파일 전달·산출물 반환 | multipart / 스토리지 URL | **둘 다 스토리지 URL** — 대용량·재시도 유리. 산출물 반영은 백엔드가 기존 F1 경로로(우회 금지) | ☐ |
| **Open-4a** | 초기 도입 시 과거 데이터 백필 | 일괄 1회 / 주차 분할 | 주차 분할(해시·재현성 유지) | ☐ |
| **Open-4b** | **과제명 텍스트 제공 가능?** | 가능 / 불가 | **필수 요청** — 태깅 제안 기능의 전제. 불가면 해당 기능 자체가 죽음 ⚠ | ☐ |
| **Open-4c** | 학생 요약 집계 주체 | 백엔드 / AI | **AI** — 자기 feature_week 사용, 중복 계산 방지. 백엔드는 개입·소통 이력만 | ☐ |
| **Open-4d** | 소통 원문 전달 범위·마스킹 | — | 최근 10건·90일 · **백엔드 1차 마스킹 + AI redaction 2차** | ☐ |
| **Open-5** | 인증·네트워크 | 인프라 표준 | 표준 따름 — A의 조건은 둘: 내부 전용 노출 + AI PG 국내 리전 | ☐ |
| **Open-6** | JSON 네이밍 | snake / camel | 백엔드 표준 따름 — 전 엔드포인트 일괄이기만 하면 됨 | ☐ |
| **Open-7** | /feedback·/confirmations 통합 | 통합 / 분리 | **분리** — 경보 평가(1차 라벨)와 제안 확정(품질 평가셋)은 의미가 다름 | ☐ |
| **Open-8** | 야간 배치 시각·순서 | — | 스냅숏 02:00 → /detect 02:10 → 브리핑 조립 03:30 | ☐ |
| **Open-9** | 전국 백분위 데이터 출처·모수 | 자체 사용자 풀 / 외부 기준 | 학부모에게 공개되는 수치 — 콜드스타트 시 모수 부족. **모수 미달 시 "해당 차트 생략"** 규칙 제안(어설픈 백분위로 신뢰를 깎지 않음) | ☐ |
| **Open-10** | 리포트 대상 선정(15일 규칙) vs DataSufficiency 게이트 | — | **게이트 우선** 제안 — 15일 이전 등원생이어도 데이터 2주 미만이면 그 달 월별 리포트 생략. 대상 선정 자체는 백엔드 소유(AI는 받은 목록대로 생성) | ☐ |
| **Open-11** | 영역 enum 수능 기준 개정 **[A+B 합의]** | 3갈래 유지 / 수능 6영역 | 독서·문학·화법·작문·언어·매체 + 공통/선택 메타 + `item_format(mcq·short·essay)` — 감지 R6·약점 지도·태깅·진단 그래프가 공유하는 어휘라 동시 개정 필요 | ☐ |
| **Open-12** | 오프라인 시험 답안지 OCR의 실명 처리 **[백엔드]** | — | 답안지에는 학생 실명이 쓰여 있음 — OCR 결과의 이름→alias 매칭은 **vault 보유한 백엔드가 수행**, AI에는 매칭 완료된 alias 답안만 진입(Phase 2 오프라인 시험 루프의 전제) | ☐ |

---

## §2. 공통 규약

### 2.1 기본 `[제안]`

| 항목 | 값 |
| --- | --- |
| Base URL | `http://checkon-ai.internal/v1` — 내부 전용, 외부 미노출 `[Open-5]` |
| 포맷 | JSON (UTF-8) · 네이밍 `snake_case` `[Open-6]` |
| 필수 헤더 | `X-Tenant-Id`(강사 alias) · `X-Request-Id`(상호 추적) · 쓰기 요청은 `Idempotency-Key` |
| 시간 | ISO-8601 + 오프셋 (`2026-07-14T02:00:00+09:00`) |

### 2.2 응답 envelope `[제안]`

```json
{
  "data": { },
  "error": null,
  "meta": {
    "execution_id": "uuid",
    "versions": { "pipeline": "1.0.0", "engine": "rule-1.3", "threshold": "v4", "contract": "0.1" }
  }
}
```

실패 시 `data: null`, `error: {"code", "message", "detail"}`. **meta.versions는 항상 실린다** — 재현성·디버깅의 기준.

### 2.3 에러 코드 `[제안]`

| HTTP | code | 의미 |
| --- | --- | --- |
| 400 | `VALIDATION_FAILED` | 스키마 위반 (detail에 필드별 사유) |
| 401 | `UNAUTHORIZED` | 토큰 무효 |
| 403 | `TENANT_MISMATCH` | 헤더 tenant ≠ 페이로드 tenant |
| 404 | `NOT_FOUND` | 리소스 없음 — 동의 없는 학생 참조 포함(존재 자체를 숨김) |
| 409 | `IDEMPOTENT_REPLAY` | 동일 Idempotency-Key → 기존 결과 반환 |
| 429 | `QUOTA_EXCEEDED` | 할당 소진 — detail: `{ "limit_kind": "draft | problem | report_single", "scope": "monthly | daily", "limit": 30, "used": 30, "resets_at": "매월 1일 / 자정(KST)" }` — limit_kind로 어떤 할당인지, scope로 월 할당/일일 상한 구분(UI 안내 문구 분기용) |
| 503 | `LLM_UNAVAILABLE` | LLM 장애(재시도 소진) → 백엔드는 재시도 예약 표시 |
| 500 | `INTERNAL` | 그 외 |

> ⭐ **가장 중요한 원칙:** '정상적 미생성'은 에러가 아니다.
> `rejected_insufficient`(데이터 부족) · `template_only`(데이터 무관 문의) · `fallback_used`(문장화 폴백)는 **200 + `data.status`**로 온다. 화면 문구 번역은 백엔드/프론트 몫.

### 2.4 동기 / 비동기 `[제안]`

| 방식 | 대상 | 규칙 |
| --- | --- | --- |
| 동기 (≤2s) | `/classify` `/tags/suggest` `/detect`(야간이라 지연 무관) `/feedback` `/confirmations` | 타임아웃 10s |
| 비동기 (202) | `/drafts` `/imports` `/agents/*` `/labels/suggest` | 202 + `job_id` → `GET` 폴링 `[Open-2]` · 작업 총 5분 초과 시 failed |

### 2.5 사용량 한도 — 기능별 할당 + 일일 상한 `[제안]`

**할당 수치는 기획서 5.3 요금표를 따른다**(Free 20명·문항 100·상담 100·단건 10 / Standard 50명·300·300·30 / Pro 100명·500·500·50 — 기간 단위는 월 권장, 기획 확정 대상). 본 계약은 **소진 메커니즘**을 확정한다: 클로드식 단순 모델 — 일일 상한 병행 + 매일 자정(KST) 리셋, 이월 없음, "오늘 8/10 남음" 상시 표시.

| 구분 | 카운트 | 규칙 |
| --- | --- | --- |
| **상담 답변 초안** | 초안 생성 1 · refine 1턴 1 (같은 풀) | 플랜 할당 소모 · 일일 상한 병행 |
| **시험 문제 초안** | 문항 1개 = 1 (B 소유 기능이지만 미터링은 공통) | 〃 |
| **단건 리포트(수시)** | 1건 = 1 | 플랜 할당(10/30/50) + **일일 상한**(예: Standard 하루 10) — 반 전체를 단건으로 뽑아 일괄을 우회하는 것 방지 + 고토큰 원가 방어 |
| **일괄 작업** (야간·비동기) | **월별 리포트 일괄 생성**(매월 1일 전월분·재원생 전원 — 생성까지만, 승인·전달은 강사/HITL) · 상담팩 | 할당과 **별도**, 전 유료 티어 월 1회 기본(상담팩은 Pro 월 2회) |
| 카운트 제외 | 분류·태깅(캐시) · 감지(LLM 무관) · 라벨 제안(주간 배치) | 무제한 |

- 소진 시 429 `QUOTA_EXCEEDED`(§2.3, `limit_kind`로 어떤 할당인지 구분: `draft | problem | report_single`) — 인터랙티브만 차단, 일괄·감지는 무관.
- 잔여량은 모든 생성 응답의 `meta.quota`로 동봉: `{ "draft": {"limit":300,"used":41,"daily_used":7}, "report_single": {"limit":30,"used":12,"daily_limit":10,"daily_used":2} }` — 백엔드 UI 상시 표시용.
- **월별 리포트 대상 규칙(15일 규칙)은 백엔드 소유** — 15일 이전 등원생 포함 / 이후 등원생 그 달 생략. AI의 DataSufficiency 게이트(2주 미만 거부)가 우선(`[Open-10]`).

---

## §3. 엔드포인트

### 3.0 퀵 레퍼런스

| 엔드포인트 | 방식 | 언제 호출 | 돌려주는 것 |
| --- | --- | --- | --- |
| `POST /detect` | 동기 | 야간 배치 02:10 | 신호 TOP 3~5 + 근거 + 브리핑 문장 |
| `POST /feedback` | 동기 | 강사가 경보 평가할 때마다 | ack (캘리브레이션 재료) |
| `POST /confirmations` | 동기 | 태그·라벨·분류·초안수정 확정 시 | ack (품질 평가셋 재료) |
| `POST /drafts` → `GET /drafts/{id}` | 202 | 문의 도착 즉시 · 리포트 주기 | 블록별 초안 + 근거 + 게이트 + status |
| `POST /drafts/{id}/tone` | 202 | 톤 조절 버튼 | 해당 블록만 재생성 |
| `POST /classify` | 동기 | 문의 도착 즉시 | topic + urgency (표시·정렬 전용) |
| `POST /tags/suggest` | 동기 | 과제 입력 화면 | 영역·유형 제안 + 신뢰도 + 캐시 여부 |
| `POST /labels/suggest` | 202 | 주간 배치 | 라벨 제안 + 근거 인용(실존 검증 통과분) |
| `POST /imports` → `GET` → `/confirm` | 202 | 타사 엑셀 업로드 | 매핑 미리보기 → 확정 후 표준 스키마 산출 |
| `POST /agents/counsel-pack` → `GET` → `/resume` | 202 | 상담 주간 | 진행률 · 완료분 즉시 조회 · 재개 |
| `GET /health` · `GET /meta/versions` | 동기 | 상시 | 헬스 · 버전 |

---

### 3.1 `POST /v1/detect` — 야간 감지 `[Open-1: push 가정]`

| 방식 | 호출자 | 멱등 |
| --- | --- | --- |
| 동기 | 백엔드 배치 | `Idempotency-Key = tenant + week_start` |

**Request** — 스키마 상세는 §4.1:

```json
{
  "snapshot_meta": { "week_start": "2026-07-06", "snapshot_hash": "sha256:...", "term_context": "normal",
                     "classes": [{ "class_ref": "cl_a1" }] },
  "students":        [ { "...": "§4.1 students" } ],
  "learning_events": [ { "...": "§4.1 learning_events" } ]
}
```

**Response 200:**

```json
{
  "data": {
    "signals": [{
      "signal_id": "uuid", "student_ref": "st_8f2a", "rule_id": "R4",
      "signal_type": "hidden_risk", "score": 0.78, "rank": 2,
      "brief": { "text": "비문학 지문을 붙잡는 시간이 3주째 늘고 있어요 — 정답률은 아직 버티는 중이에요.",
                 "gate_passed": true, "fallback_used": false },
      "evidence": [{ "source_table": "learning_event", "record_id": "le_1029", "summary": "7/3 숙제 지연 제출" }]
    }],
    "observed_only": ["st_c3d1"],
    "stats": { "students_evaluated": 58, "signals_raised": 3, "capped_out": 2 }
  }
}
```

**규약:** evidence 빈 신호는 스키마상 불가 · `observed_only` = 데이터 2주 미만(경보 제외) · **Alert 생성·상태 관리는 백엔드 소유** — AI는 신호 산출까지.

---

### 3.2 `POST /v1/feedback` — 경보 평가 회신 `[제안]`

```json
// Request
{ "alert_ref": "al_5521", "signal_id": "uuid", "verdict": "not_applicable" }
// Response 200
{ "data": { "accepted": true } }
```

'해당 없음' 비율 30% 초과 시 임계값 보수화 제안이 A(운영자)에게 감 — 자동 변경 없음.

### 3.3 `POST /v1/confirmations` — 제안 확정 회신 `[Open-7: 분리 제안]`

```json
// Request — 태그·라벨·분류·초안 수정의 확정/거절/수정
{ "kind": "tag", "suggestion_id": "uuid", "action": "corrected",
  "corrected_value": { "area_tag": "grammar", "type_tag": "concept" } }
// Response 200
{ "data": { "accepted": true } }
```

kind: `tag | label | classification | draft_edit`(강사 수정 diff → 문체 프로필 재료).

---

### 3.4 `POST /v1/drafts` — 초안 생성 (202)

| 방식 | 호출자 | 멱등 |
| --- | --- | --- |
| 202 → `GET /v1/drafts/{draft_id}` | 백엔드 (문의 도착 즉시 — 사전 생성 / 월간 리포트 주기) | `Idempotency-Key = inquiry_ref` 등 |

**Request:**

```json
{
  "kind": "reply",
  "student_ref": "st_8f2a", "guardian_ref": "gd_11b0",
  "label_snapshot": { "comm": "narrative", "interest": "attitude", "sensitivity": "anxious", "frequency": "frequent" },
  "inquiry": { "inquiry_ref": "iq_204", "body_text": "요즘 아이가 힘들어하는 것 같은데...", "received_at": "2026-07-10T21:04:00+09:00" },
  "context": { "...": "§4.2 초안 컨텍스트" }
}
```

**Response (GET) 200:**

```json
{
  "data": {
    "draft_id": "uuid",
    "status": "generated",
    "status_reason": null,
    "classification": { "topic": "complaint", "urgency": "immediate" },
    "blocks": [{
      "seq": 1, "block_type": "fact",
      "content": "서연이는 6월 한 달 지문 42개·312문항을 성실히 풀었고...",
      "evidence": [{ "source_table": "learning_event_agg", "record_id": "agg_w27" }],
      "empty_reason": null
    }],
    "gates": [{ "gate": "SourceGrounding", "passed": true }, { "gate": "ToneSafety", "passed": true }]
  }
}
```

**status 값:** `generated` 정상 · `template_only` 데이터 무관 문의(일반 템플릿만) · `rejected_insufficient` 데이터 부족(정상 상태!) · `failed` LLM 장애 등(사유 포함). 블록 `content`가 비고 `empty_reason`이 있으면 = 재시도 3회 소진 섹션.

**리포트(kind=report) 전용 규약 — 차트별 해설 블록 `[제안]`:** 리포트는 차트(영역별 약점 현황 · 보강 후 개선 추이 · 전국 백분위) 단위로 구성되며, 블록에 `block_type: "chart_analysis"` + `chart_ref`가 붙는다. **차트 수치·개선율·백분위는 전부 코드/백엔드가 확정하고 LLM은 해설 문장만** 생성(SourceGrounding이 문장 속 수치를 benchmarks·집계와 대조). **노출 정책 3층:** 학생 = 비교 일절 비노출 / 학부모 리포트 = 전국 백분위만(반 평균·석차 비노출 — teacher_only 데이터는 컨텍스트에서 구조적 제외) / 반 평균 = 강사 내부 화면 전용(성적 지표 관리 도구). 하위 백분위는 수치는 사실대로 + 문장은 성장 추이 중심(라벨 anxious 시 완곡 강화).

**`POST /v1/drafts/{id}/refine`** — **채팅형 다듬기 (핑퐁)** `[제안]`:

```json
// Request
{ "scope": "block", "block_seq": 2,
  "instruction": "마지막에 다음 상담 일정을 제안하는 문장 하나 넣어주세요. 전체적으로 조금 더 짧게.",
  "preset": null }        // 톤 버튼(softer|conclusion_first|shorter)은 preset으로 흡수
```

- 강사가 자유 문장으로 지시하며 초안을 반복 다듬는 대화형 루프. 202 → GET 시 `revision_no` 증가 + `revisions[]` 턴 이력(롤백: `{"revert_to": n}` — 할당 미소모).
- **핑퐁의 가드레일:** ① 매 턴 결과도 게이트 전체(Evidence·SourceGrounding·ToneSafety) 재통과 — **강사 지시가 게이트를 이기지 못한다**("정답률 95%라고 써줘"는 근거 부재로 차단+사유 반환) ② **다듬기 1턴 = 상담 초안 할당 1 소모** — 플랜 월 할당이 자연 상한(별도 세션 턴 제한 없음), UI는 "이번 달 상담 초안 n/300 남음"을 상시 표시(§2.5 meta.quota) ③ 지시문·수정 이력은 문체 프로필 재료로 축적.

**규약:** 승인·발송은 백엔드(HITL) — 다듬기는 초안까지, 이 API에 발송 개념 없음. `label_snapshot`은 **백엔드 확정 라벨만**(ai_suggested 미포함).

---

### 3.5 `POST /v1/classify` — 문의 분류 (동기)

```json
// Request
{ "inquiry_ref": "iq_204", "body_text": "여름방학 특강 시간표가 궁금합니다" }
// Response 200
{ "data": { "topic": "schedule", "urgency": "normal", "confidence": 0.95 } }
```

topic: `grade | schedule | complaint | counsel_request | etc` (enum 강제). **표시·정렬·완충 강도에만 사용** — 차단·자동응답 금지는 백엔드도 준수.

### 3.6 `POST /v1/tags/suggest` — 태그 제안 (동기·캐시)

```json
// Request
{ "source_kind": "trackB_grading", "title_text": "6월 모의고사 비문학 대비 #3" }
// Response 200
{ "data": { "suggestion_id": "uuid", "area_tag": "reading", "type_tag": "infer",
            "confidence": 0.92, "cached": true } }
```

**강사 확정(→ /confirmations) 전 learning_event 반영 금지**는 백엔드 책임. 동일 명명 패턴은 캐시 히트(LLM 0회).

### 3.7 `POST /v1/labels/suggest` — 라벨 제안 (주간 배치·202)

```json
// Request
{ "guardians": [{ "guardian_ref": "gd_11b0",
    "history": [{ "record_id": "cm_88", "direction": "inbound", "text": "숫자로 정리해 주세요", "at": "..." }] }] }
// Response (GET) 200
{ "data": { "suggestions": [{
    "suggestion_id": "uuid", "guardian_ref": "gd_11b0",
    "axis": "comm", "value": "data", "confidence": 0.86,
    "evidence_quotes": [{ "record_id": "cm_88", "quote": "숫자로 정리해 주세요" }] }] } }
```

**인용 실존 게이트 통과분만 반환** — 인용이 이력에 없으면 제안 자체가 폐기됨. 확정 전 초안 생성에 미사용.

---

### 3.8 `/v1/imports` — 스마트 데이터 이전 (202) `[Open-3]`

**흐름:** ① `POST /imports` (파일 — 스토리지 URL 제안) → 202 `job_id` ② `GET /imports/{job_id}` — 상태·미리보기 ③ `POST /imports/{job_id}/confirm` — 강사 확정 → 변환.

**GET Response (preview_ready):**

```json
{
  "data": {
    "job_id": "uuid", "status": "preview_ready",
    "mapping_preview": {
      "spec_version": 1, "reused": false,
      "columns": [
        { "source": "원생명",   "target": "student_alias", "confidence": 0.97 },
        { "source": "점수A",   "target": "weekly_score",  "confidence": 0.41, "needs_review": true,
          "probe_note": "유니크 값 8,7,10,9… → 10점 만점 추정" },
        { "source": "連락처",  "target": null, "unmapped_reason": "개인정보 필드 — 자동 이전 대상 아님" }
      ],
      "sample_rows": [ { "...": "변환 예시 5행" } ],
      "blocked": false, "blocked_reason": null
    }
  }
}
```

**confirm Request:** `{ "spec_overrides": [{ "source_column": "점수B", "target_field": "weekly_score_2" }] }` → 변환 실행 → `status: done` + `result: { "output_url": "...", "row_total": 1240, "row_ok": 1228, "row_errors": [{ "row_no": 88, "column": "제출일", "reason": "날짜 형식" }] }`.

**status:** `profiling → inferring → probing(조사 에이전트) → preview_ready → transforming → done` / `blocked`(필수 필드 미매핑 — 확정 차단). **규약:** 산출물 반영은 백엔드가 기존 F1 업로드 경로로 · 동의 미보유 행 '보류'는 백엔드 · 같은 양식 재수입은 `reused: true`(LLM 0회).

---

### 3.9 `/v1/agents/counsel-pack` — 상담팩 에이전트 (202)

```json
// POST — 기동
{ "class_ref": "cl_a1", "student_refs": ["st_8f2a", "..."], "contexts": { "...": "§4.2 × 학생별" } }
// GET /v1/agents/{agent_run_id}
{ "data": { "status": "paused", "progress": "13/22",
    "completed": [{ "student_ref": "st_8f2a", "draft_id": "uuid" }],
    "skipped":   [{ "student_ref": "st_c3d1", "reason": "insufficient_data" }],
    "failed":    [{ "student_ref": "st_77aa", "reason": "llm_unavailable" }] } }
// POST /v1/agents/{agent_run_id}/resume — paused에서 체크포인트 복원 재개
```

**규약:** 완료 draft는 전체 종료 전에도 개별 조회 가능(강사가 먼저 검토 시작 가능) · 장애 시 완료분 보존·재개는 실패 지점부터.

### 3.10 운영

`GET /v1/health` — liveness/readiness · `GET /v1/meta/versions` — 엔진·임계값·프롬프트·계약 버전(백엔드가 브리핑 메타에 표시 가능).

---

## §4. 스냅숏 데이터 계약 (백엔드 → AI)

> **3대 원칙 `[제안]`** ① 실명·연락처·주소는 **필드가 스키마에 없다**(백엔드 직렬화 DTO에서 보장) ② 모든 식별자는 alias/불투명 ID ③ `snapshot_hash`로 재현성 보장(부록 A).

### 4.1 감지 스냅숏 (`/detect`)

**snapshot_meta**

| 필드 | 타입 | 필수 | 쓰는 곳 | 비고 |
| --- | --- | --- | --- | --- |
| `week_start` | date | ✅ | 피처 주차 키 | 월요일 기준 `[제안]` |
| `snapshot_hash` | string | ✅ | 재현성 | 부록 A |
| `term_context` | enum `normal·new_term·vacation` | ✅ | 세그먼트(재적응·방학 오경보 방지) | 원천은 백엔드 term_config |
| `classes[]` | array | ✅ | 랭킹 상한(per_class) | `{class_ref}` |

**students[]**

| 필드 | 타입 | 필수 | 쓰는 곳 | 비고 |
| --- | --- | --- | --- | --- |
| `student_ref` | string | ✅ | 전체 | alias |
| `class_ref` | string | ✅ | 랭킹 상한 | |
| `enrolled_weeks` | int | ✅ | 관찰 중 판정(2주 미만) | |
| `status` | enum `enrolled·paused·returned` | ✅ | R5(복귀 케어)·재적응 | returned = 복귀 첫 주 |
| `consent` | enum `granted·pending·revoked` | ✅ | 동의 게이트 | granted 외 이벤트는 폐기 |

**learning_events[]** — 지난 주차 증분 `[Open-4a: 백필]`

| 필드 | 타입 | 필수 | 쓰는 곳 | 비고 |
| --- | --- | --- | --- | --- |
| `record_id` | string | ✅ | **evidence 역추적 키** | MySQL PK — **불변 필수** |
| `student_ref` | string | ✅ | | |
| `type` | enum `solve·submit·attend·consult` | ✅ | 규칙별 | |
| `occurred_at` | datetime | ✅ | 시계열 | |
| `correct` | bool | solve만 | 정답률(R1·R6) | |
| `duration_sec` | int | solve만 | 풀이시간(R4) | 없으면 R4 미적용(대체 신호) |
| `passage_word_count` | int | 지문형만 | **어절 정규화** | 국어 특화의 핵심 필드 |
| `area_tag` / `type_tag` | enum | 있으면 | 유형별 정답률(R6)·약점 지도 | 미태깅 허용 — 태깅 제안이 채움. **area 값(수능 기준 개정 제안): `reading(독서)·literature(문학)·speech(화법)·writing(작문)·language(언어/문법)·media(매체)`** + 수능공통/선택 메타 · `item_format: mcq|short|essay` 병행 — 최종 enum은 `[Open-11]` [A+B] 합의 |
| `assignment_title_text` | string | 있으면 | **태깅 제안(ⓒ) 입력** | ⚠ `[Open-4b]` 제공 불가 시 기능 자체 불가 |
| `source` | enum `trackA·trackB·studentHome` | ✅ | 품질 가중 | |

### 4.2 초안 컨텍스트 (`/drafts` · `/agents/counsel-pack`)

| 필드 | 타입 | 필수 | 쓰는 곳 | 비고 |
| --- | --- | --- | --- | --- |
| `interventions[]` | array | ✅ | 초안 맥락 | `{record_id, type, memo_text, at}` — 강사 본인의 기록 |
| `comm_history[]` | array | ✅ | 톤·이력·라벨 제안 | `{record_id, direction, text, at}` — `[Open-4d]` 최근 10건·90일 · 백엔드 1차 마스킹 제안 |
| `guardian.label_snapshot` | object | ✅ | 톤 매핑 | 4축 enum — **확정본만** |
| `inquiry` | object | reply만 | 분류·초안 | `{inquiry_ref, body_text, received_at}` |
| `student_summary` | object | ❌ | — | `[Open-4c]` A 제안: 불필요(AI가 자기 피처 사용) |
| `benchmarks` | object | report만 | 리포트 차트·해설 | 백엔드 집계 API 산출. **공개 등급 분리**: `{ "national_percentile": { "value": 68, "as_of": "...", "source": "...", "audience": "guardian" }, "class_avg": { "value": 74, "audience": "teacher_only" } }` — **teacher_only 항목은 학부모向 draft의 LLM 컨텍스트 조립에서 구조적으로 제외**(프롬프트에 미포함 = 유출 불가). 전국 백분위 출처·모수는 `[Open-9]` 백엔드 확인 필요(콜드스타트) |

### 4.3 크기·빈도 `[제안]`

| 페이로드 | 크기 상한 | 빈도 |
| --- | --- | --- |
| 감지 스냅숏 | 5MB/요청 (초과 시 분할) | 일 1회 (야간) |
| 초안 컨텍스트 | 512KB | 문의당 · 리포트 주기 |
| Import 파일 | 20MB | 비정기 `[Open-3]` |

---

## §5. 비기능 규약 `[제안]`

- **타임아웃:** 동기 10s · 비동기 작업 총 5분(초과 시 `failed` + 사유). 백엔드 서킷브레이커 임계는 v1.0에서 함께 확정.
- **AI 장애 시:** 백엔드는 전일 브리핑 유지 + 배지(기존 NFR). `/detect` 실패 시 다음 배치까지 대기 — 멱등키(tenant+week)로 중복 방지.
- **감사:** 모든 요청·응답은 `X-Request-Id`로 양쪽에서 상호 추적 가능.
- **하위호환:** 필드 **추가** = 마이너(무통보 가능) · 필드 **삭제·의미 변경** = 메이저(협의 필수). `meta.versions.contract`로 상호 확인.

## §6. 확정 절차

1. 리뷰 미팅(30분) — §1의 Open-1~8만 논의 → 체크박스 채움
2. 반영 후 **v1.0 승격** → 저장소 커밋(이후 변경은 PR)
3. 양측 병행 개발 시작: A = FakeSnapshot · 백엔드 = AI 스텁
4. 통합 시점: 부록 A 해시 테스트 벡터 3건으로 상호 검증

---

## 부록 A. snapshot_hash 산정 `[제안]`

```
sha256( canonical_json({
  snapshot_meta: { week_start, term_context },
  students:        sorted by student_ref,
  learning_events: sorted by record_id
}) )
```

canonical_json = 키 정렬 · 공백 제거 · UTF-8. **동일 구현 검증용 테스트 벡터 3건을 v1.0에 첨부한다** (백엔드 Java와 AI Python이 같은 해시를 내는지 통합 전 확인).

> 이 문서의 요약·리뷰용 시각 버전: **체크온_AI_API계약_팀공유본.html** — 충돌 시 본 md가 정답.
