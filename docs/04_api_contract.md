# 체크온 AI 서비스 — REST API · 스냅숏 데이터 계약 v0.1 (제안)

| | |
| --- | --- |
| **상태** | 🟢 **7/15 리뷰 완료 — v1.0 승격 대기.** 잔여 2건: Open-9 백분위 출처 · Kafka 토픽/이벤트 스키마 부록. 채워지면 v1.0 커밋 |
| **당사자** | AI 서비스(Python·PostgreSQL, member-A) ↔ 백엔드(Java·Spring·PostgreSQL) |
| **읽는 법** | `[제안]` = 이견 없으면 그대로 확정 · `[Open-n]` = **결정 필요 → §1만 논의하면 30분에 끝남** |
| **확정 후** | A는 FakeSnapshot으로, 백엔드는 AI 스텁으로 **서로 안 기다리고 동시 개발** 시작 |
| **참조** | `part_a/01_pipeline` `part_a/02_design` `06_erd` · 안건 추적 `99_open_items` · 요청 JSON만 모은 실무용: `05_request_json` |

**TL;DR (5줄)**

1. AI는 **신호·초안·매핑을 만들 뿐, 발송·확정·반영은 전부 백엔드**(HITL) — 이 API에 발송 개념이 없다.
2. 어떤 페이로드에도 **실명·연락처 필드 자체가 없다** — 백엔드 DTO 차원에서 보장을 요청한다.
3. '정상적 미생성'(데이터 부족 거부, 템플릿 전용, 문장화 폴백)은 **에러가 아니라 200 + status**.
4. 빠른 것(분류·태그)은 동기, LLM 생성(초안·Import·에이전트)은 **202 + 작업 조회**.
5. 결정 필요한 건 **Open-1 ~ Open-12 열두 개**뿐이다 (4a~4d 포함, 30분 미팅 분량).

**목차** — §1 리뷰 안건 · §2 공통 규약 · §3 엔드포인트(퀵 레퍼런스 + 상세) · §4 스냅숏 데이터 계약 · §5 비기능 · §6 확정 절차 · 부록 A 해시 규칙

---

## §1. 리뷰 안건 — Open-1 ~ 12 (이것만 정하면 됩니다)

| # | 안건 | 선택지 | 💙 A의 제안과 이유 | 결정 |
| --- | --- | --- | --- | --- |
| **Open-1** | 감지 스냅숏 전달 방향 | 백엔드 push / AI pull | **push** — 배치 오케스트레이션이 백엔드(Spring Batch)에 있음 | ✅ 7/15 |
| **Open-2** | 비동기 완료 통지 | 폴링 / 웹훅 / Kafka | ✅ **(7/15 확정) Kafka** — 백엔드 메시징 표준. 토픽·이벤트 스키마는 v1.0 부록 | ✅ |
| **Open-3** | Import 파일 전달·산출물 반환 | multipart / 스토리지 URL | **둘 다 스토리지 URL** — 대용량·재시도 유리. 산출물 반영은 백엔드가 기존 F1 경로로(우회 금지) | ✅ 7/15 |
| **Open-4a** | 초기 도입 시 과거 데이터 백필 | 일괄 1회 / 주차 분할 | 주차 분할(해시·재현성 유지) | ✅ 7/15 |
| **Open-4b** | **과제명 텍스트 제공 가능?** | 가능 / 불가 | ✅ **(7/15) 제공 가능 확정** — 태깅 제안 기능 생존 | ✅ |
| **Open-4c** | 학생 요약 집계 주체 | 백엔드 / AI | **AI** — 자기 feature_week 사용, 중복 계산 방지. 백엔드는 개입·소통 이력만 | ✅ 7/15 |
| **Open-4d** | 소통 원문 전달 범위·마스킹 | — | 최근 10건·90일 · **백엔드 1차 마스킹 + AI redaction 2차** | ✅ 7/15 |
| **Open-5** | 인증·네트워크 | 인프라 표준 | 표준 따름 — A의 조건은 둘: 내부 전용 노출 + AI PG 국내 리전 | ✅ 7/15 |
| **Open-6** | JSON 네이밍 | snake / camel | 백엔드 표준 따름(snake_case) — 전 엔드포인트 일괄 | ✅ 7/15 |
| **Open-7** | /feedback·/confirmations 통합 | 통합 / 분리 | **분리** — 경보 평가(1차 라벨)와 제안 확정(품질 평가셋)은 의미가 다름 | ✅ 7/15 |
| **Open-8** | 야간 배치 시각·순서 | — | 스냅숏 02:00 → /detect 02:10 → 브리핑 조립 03:30 | ✅ 7/15 |
| **Open-9** | 전국 백분위 데이터 출처·모수 | 자체 사용자 풀 / 외부 기준 | 🟠 **부분 합의(7/15): 모수 미달 시 "해당 차트 생략" 확정** — 출처·갱신 주기는 백엔드 확인 잔여 | 🟠 |
| **Open-10** | 리포트 대상 선정(15일 규칙) vs DataSufficiency 게이트 | — | **게이트 우선** — 15일 이전 등원생이어도 데이터 2주 미만이면 그 달 생략. 대상 선정은 백엔드 소유 | ✅ 7/15 |
| **Open-11** | 영역 enum 수능 기준 개정 **[A+B 합의]** | 3갈래 유지 / 수능 6영역 | ✅ **(7/15 확정) 수능 6영역 채택 + item_format은 v1에서 `mcq`만 사용**(수능 국어 전 문항 객관식 — short·essay는 enum 예약, 내신·자체 시험 후순위) | ✅ |
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

아래 JSON은 `problem_generation` 실행에서 `VersionSet`의 정식 10키 집합을 모두 표시한 예시다.

```json
{
  "data": { },
  "error": null,
  "meta": {
    "execution_id": "uuid",
    "versions": {
      "pipeline": "1.0.0",
      "engine": "detection-rules-0.2",
      "threshold": null,
      "prompt": "v0.1",
      "schema": "0.1",
      "contract": "0.1",
      "graph": "curriculum-0.1",
      "taxonomy": "taxonomy-0.1",
      "verify_config": "verify-0.1",
      "difficulty_calib": "difficulty-0.1"
    }
  }
}
```

실패 시 `data: null`, `error: {"code", "message", "detail"}`. **meta.versions는 항상 실린다** — 재현성·디버깅의 기준.

**(7/15) 공통 버전 세트는 6종으로 통일** — 이 §2.2와 ERD의 `AI_RUN`이 각각 4종씩 서로 다르게 적고 있어(§2.2=threshold·contract / ERD=prompt·schema) 합집합으로 맞췄다.

**[PART_A+PART_B] 승인 확장:** 키 집합의 정본은 `contracts/execution.py`의 `VersionSet`이며, `AI_RUN` 컬럼·`meta.versions`와 1:1이다. 현재 정식 키 집합은 공통 6종(`pipeline`·`engine`·`threshold`·`prompt`·`schema`·`contract`) + [PART_B] 실행 전용 nullable 4종(`graph`·`taxonomy`·`verify_config`·`difficulty_calib`)인 **총 10종**이다. 위 JSON은 특정 capability의 예시이며, nullable 값은 실행 종류에 따라 달라진다.

nullable 키는 실행 종류에 따라 **null이 될 수 있다**: `threshold`는 감지 임계값 시트 버전이라 detection 외에는 null · `prompt`는 LLM 미사용 실행(감지·진단)에서 null · `graph`·`taxonomy`·`verify_config`는 관련 [PART_B] 실행 외에는 null · `difficulty_calib`은 `problem_generation` 외에는 null.

> **[PART_B 크로스체킹 요청 · 미확정 — capability별 version 조건]** 위 null 조건은 문서에는 있으나 현재 공용 모델이 capability별 필수·금지 조합을 강제하지 않아 B 실행의 B 버전 누락이나 A 실행의 B 버전 혼입이 통과할 수 있다. **제안 해결안:** `ExecutionContext` 조립 경계에서 capability별 VersionSet 불변식을 validator와 음수 테스트로 고정한다. A·B가 공용 계약의 강제 수준을 확인해 달라.
>
> ✅ **A 판정(7/22):** 타당한 지적이나 `execution.py`(양자 승인 파일) 변경이라 A 단독으로 확정하지 않는다 — **양자 협의 안건으로 등록**(99 등록). capability별 VersionSet validator(A 실행에 B 키 혼입 금지·B 실행에 B 키 필수)는 B와 강제 수준을 합의한 뒤 execution.py에 반영한다.

> **[PART_B 크로스체킹 요청 · 미확정 — 실패 envelope]** 현재 공용 `api/envelope.py`는 실패 응답의 `meta`를 null로 만들 수 있어 위 “meta.versions는 항상” 규약과 어긋난다. **제안 해결안:** 실행 전 오류까지 포함해 version meta를 조립하는 단일 규칙을 두고 성공·실패 HTTP 테스트에서 10키 집합을 검증한다. 계약을 유지할지 구현 동작을 정본으로 삼을지 A·B 확인을 요청한다.
>
> ✅ **A 판정(7/22):** **계약이 정본** — 실패 응답에도 `meta.versions`를 싣는다(구현을 계약에 맞춰 수정 · ⚠ 양자 파일 `api/envelope.py`). 실행 전 오류(헤더 누락 등 config 확정 전)의 조립 규칙: 엔드포인트가 아는 **정적 앱 버전 + 기본 config의 threshold**로 채운다(실행 여부와 무관하게 그 엔드포인트의 버전 정보). 성공·실패 HTTP 테스트에서 10키 집합을 검증한다.

### 2.3 에러 코드

**에러 코드의 정본은 [`docs/policies/error_codes.md`](policies/error_codes.md) §1이다 — 여기 중복 정의하지 않는다.** (과거 이 표의 일부 코드가 정본과 코드명·409 의미가 어긋나 있어 표를 제거 — 7/21 통일, 상세는 99_open_items #12. 404의 "동의 없는 학생 참조 포함"·429 쿼터 주석은 정본 §1로 이관.)

- **멱등(409):** 같은 `Idempotency-Key` + **같은 바디** = 기존 결과를 200으로 반환 · 같은 키 + **다른 바디** = `409 IDEMPOTENCY_CONFLICT`로 거부(기존 결과 반환 안 함).

> **[PART_B 크로스체킹 요청 · 미확정 — 공통 HTTP 경계]** 현재 감지 v0 캐시는 전역 Idempotency-Key와 클라이언트 `snapshot_hash`를 신뢰한다. **제안 해결안:** `(tenant_id, method/path, idempotency_key)`로 스코프하고 서버가 canonical body digest를 계산해 원자 저장·TTL·영속화를 보장한다. 아울러 `RequestValidationError`를 공통 envelope의 `400 INVALID_SCHEMA`로 매핑하고, 실제 `contracts.llm` 예외를 503/504로 변환하는 단일 adapter, `RedactionUncertain` 상세 강제 제거, `X-Request-Id` 응답 echo·로그 correlation을 공통 계층에 두는 안을 A·B·백엔드가 확인해 달라.
>
> ✅ **A 판정(7/22):**
> - **멱등 고도화(스코프·TTL·서버 digest·영속화):** D-② 안건으로 이관(99 ⑨ 확장). 이 API는 내부 백엔드 전용(§2.1 Base URL — 외부 미노출)이라 v0는 클라이언트 `snapshot_hash` 신뢰 + 인메모리로 충분하고, 서버 digest·스코프·TTL은 DB 교체 시 함께 구현한다.
> - **X-Request-Id 응답 echo·correlation:** 수용 — 공통 계층에 편입(커밋 api 공통부, ⚠ 양자).
> - **예외→HTTP adapter:** `error_codes.md` §4 트리를 정본으로, canonical adapter 위치는 `runtime/errors.py`로 확정. `IdempotencyConflict`→409·`LlmTimeout`→504 편입(error_codes §4 갱신). `RequestValidationError`→400 INVALID_SCHEMA는 현 구현과 일치.

> ⭐ **가장 중요한 원칙:** '정상적 미생성'은 에러가 아니다.
> `rejected_insufficient`(데이터 부족) · `template_only`(데이터 무관 문의) · `fallback_used`(문장화 폴백)는 **200 + `data.status`**로 온다. 화면 문구 번역은 백엔드/프론트 몫.

### 2.4 동기 / 비동기 `[제안]`

| 방식 | 대상 | 규칙 |
| --- | --- | --- |
| 동기 (≤2s) | `/classify` `/tags/suggest` `/confirmations` | 타임아웃 10s (`/feedback`은 7/16 보류 — §3.2) |
| 동기 (`/detect`) | `/detect`(야간 배치라 지연 무관) | **타임아웃 60s [A 확정 7/23 · 백엔드 통보 필요]** — 브리핑 문장화(ⓐ) 포함으로 상향. 문장화 총 예산 45s(LLM 호출당 15s 상한 — [A 확정], 어댑터 전체 타임아웃 정합·10s→15s), 예산 소진 신호는 템플릿 폴백. **백엔드 클라이언트 read timeout ≥60s 필요.** 다른 동기 API는 10s 유지 |
| 비동기 (202) | `/drafts` `/imports` `/agents/*` `/labels/suggest` | 202 + `job_id` → **완료 통지는 Kafka 이벤트 (7/15 확정)** · `GET`은 상태 보조 조회로 유지 · 작업 총 5분 초과 시 failed |

### 2.5 사용량 한도 — 기능별 할당 + 일일 상한 `[제안]`

**할당 수치는 기획서 5.3 요금표를 따른다**(Free 20명·문항 100·상담 100·단건 10 / Standard 50명·300·300·30 / Pro 100명·500·500·50 — 기간 단위는 월 권장, 기획 확정 대상). 본 계약은 **소진 메커니즘**을 확정한다: 클로드식 단순 모델 — 일일 상한 병행 + 매일 자정(KST) 리셋, 이월 없음, "오늘 8/10 남음" 상시 표시.

| 구분 | 카운트 | 규칙 |
| --- | --- | --- |
| **상담 답변 초안** | 초안 생성 1 · refine 1턴 1 (같은 풀) | 플랜 할당 소모 · 일일 상한 병행 |
| **시험 문제 초안** | 문항 1개 = 1 (B 소유 기능이지만 미터링은 공통) | 〃 |
| **단건 리포트(수시)** | 1건 = 1 | 플랜 할당(10/30/50) + **일일 상한**(예: Standard 하루 10) — 반 전체를 단건으로 뽑아 일괄을 우회하는 것 방지 + 고토큰 원가 방어 |
| **일괄 작업** (야간·비동기) | **월별 리포트 일괄 생성**(매월 1일 전월분·재원생 전원 — 생성까지만, 승인·전달은 강사/HITL) · 상담팩 | 할당과 **별도**, 전 유료 티어 월 1회 기본(상담팩은 Pro 월 2회) |
| 카운트 제외 | 분류·태깅(캐시) · 감지(LLM 무관) · 라벨 제안(주간 배치) | 무제한 |

- **(7/15 확정 — BE-4)** 차단·카운트·잔여 표시는 **전부 백엔드 Billing 소유 — AI는 쿼터를 알지 못한다.** 한도 소진 시 백엔드가 AI 호출 자체를 하지 않으며, `meta.quota` 동봉은 폐기. 위 요금표·소진 규칙 표는 백엔드 집행 참고용으로 유지.
- **월별 리포트 대상 규칙(15일 규칙)은 백엔드 소유** — 15일 이전 등원생 포함 / 이후 등원생 그 달 생략. AI의 DataSufficiency 게이트(2주 미만 거부)가 우선(`[Open-10]`).

---

## §3. 엔드포인트

### 3.0 퀵 레퍼런스

| 엔드포인트 | 방식 | 언제 호출 | 돌려주는 것 |
| --- | --- | --- | --- |
| `POST /detect` | 동기 | 야간 배치 02:10 | 신호(`new`·`follow_up` 반별 TOP 3~5 + 상한 밖 `ongoing`·`return_care`) + display_label + lifecycle + 근거 + 브리핑 문장 |
| `POST /confirmations` | 동기 | 태그·라벨·분류·초안수정 확정 시 | ack (품질 평가셋 재료) |
| `POST /drafts` → `GET /drafts/{id}` | 202 | 문의 도착 즉시 · 리포트 주기 | 블록별 초안 + 근거 + 게이트 + status |
| `POST /drafts/{id}/refine` | 202 | 채팅형 다듬기(자유 지시 · 핑퐁) | 지시 반영 리비전 — 매 턴 게이트 재통과, 1턴 = 초안 할당 1 |
| `POST /classify` | 동기 | 문의 도착 즉시 | topic + sentiment + urgency 3축 + 축별 confidence |
| `POST /tags/suggest` | 동기 | 과제 입력 화면 | 영역·유형 제안 + 신뢰도 + 캐시 여부 |
| `POST /labels/suggest` | 202 | 주간 배치 | 라벨 제안 + 근거 인용(실존 검증 통과분) |
| `POST /imports` → `GET` → `/confirm` | 202 | 타사 엑셀 업로드 | 매핑 미리보기 → 확정 후 표준 스키마 산출 |
| `POST /counsel/drafts` → `GET` → `/refine` | 202 | **문의 도착 즉시** | 초안 1건 자동 생성 · 근거 인용 ≥1 · 다듬기(동기) — §3.9 |
| `GET /health` · `GET /meta/versions` | 동기 | 상시 | 헬스 · 버전 |

---

### 3.1 `POST /v1/detect` — 야간 감지 `[Open-1: push 가정]`

> **사양 원본: `docs/part_a/09_detect_spec.md`(AI 확정 · 백엔드 전달본).** 여기는 요약이며 필드 주석·저장 규칙·lifecycle 판정표(§4)는 그 문서가 원본이다. 아래 예시와 09가 어긋나면 09가 정답.

| 방식 | 호출자 | 멱등 |
| --- | --- | --- |
| 동기 | 백엔드 배치 | `Idempotency-Key = tenant + analysis_date`(배치 실행 기준일 — 09 §2 [A 확정 7/28]) |

**Request** — 필드별 타입·필수 여부는 §4.1 표 · 09 §2 참조 (주석은 JSON5 스타일 — 실제 전송 시 제거):

```json
{
  "snapshot_meta": {
    "week_start": "2026-07-13",          // 이 주차의 월요일 — 피처 계산 기준 키
    "snapshot_hash": "sha256:...",       // 부록 A 규칙으로 백엔드가 산정 — alert_context 포함해 해시
    "term_context": "normal",            // normal | new_term | vacation — 신학기·방학 오경보 방지용
    "classes": [{ "class_ref": "cl_a1" }]  // 반 목록 — 경보 상한(반별 TOP 3~5, new·follow_up만 대상) 계산에 필요
  },
  "students":        [ { "...": "§4.1 · 09 §2 students 표 참조" } ],        // 재원생 전체 (consent 포함)
  "learning_events": [ { "...": "§4.1 · 09 §2 learning_events 표 참조" } ], // 지난 주차 증분만
  "alert_context":   [ {                 // ★(7/16 신설) 최근 30일 경보 이력 — lifecycle 판정 입력 (09 §2·§4)
    "student_ref": "st_8f2a",
    "signal_type": "hidden_risk",        // 09 §1의 6값 중 하나
    "status": "open",                    // open | resolved
    "resolved_at": null,                 // resolved일 때 해소 일시 — "해소 후 2주" 쿨다운 기준
    "followed_up": false                 // 해소 후 팔로업 카드가 이미 나갔는지 (중복 방지)
  } ]
}
```

**Response 200:**

```json
{
  "data": {
    "signals": [{                        // TOP 3~5 상한은 new·follow_up에만 적용하며,
                                         // ongoing·return_care(R5)는 상한 밖으로 추가되어
                                         // signals 길이와 rank가 5를 초과할 수 있다
      "signal_id": "uuid",               // AI 신호 ID — Alert와 함께 저장(향후 강사 평가 회신 대비)
      "student_ref": "st_8f2a",
      "class_ref": "cl_a1",
      "rule_id": "R4",                   // R1~R6 — 내부 규칙 번호(로그용, 화면 미노출)
      "signal_type": "hidden_risk",      // 09 §1의 6값
      "display_label": "숨은 위기",        // ★(7/16 신설) 화면에 그대로 쓸 한글 문구(AI 확정) — 09 §1 표
      "score": 0.78, "rank": 2,          // score는 로그용(화면 미노출) · rank = 반 내 우선순위
      "advisory": false,                 // ★(8/3 신설) true면 알림·카드에서 빼고 학생 상세의 참고 표시로 — 아래 [A 확정 통보]
      "lifecycle": "new",                // ★(7/16 신설) new | ongoing | follow_up — AI 경보 생애 판정(09 §4)
      "brief": {                         // 브리핑에 바로 실을 한 줄 문장 (LLM 생성 + 왜곡 게이트 통과분)
        "text": "비문학 지문을 붙잡는 시간이 3주째 늘고 있어요 — 정답률은 아직 버티는 중이에요.",
        "gate_passed": true,
        "fallback_used": false           // true면 게이트 실패 → 템플릿 문장으로 대체된 것 (그래도 표시 가능)
      },
      "evidence": [{                     // 근거 — 항상 1건 이상 (없으면 신호 자체가 생성 안 됨)
        "source_table": "learning_event",
        "record_id": "le_1029",          // 백엔드 DB PK — 강사가 "근거 보기" 누르면 이걸로 원본 조회
        "summary": "7/3 숙제 지연 제출"
      }]
    }],
    "stats": {                           // 운영 지표(로그용, 화면 미노출)
      "students_evaluated": 58,
      "signals_raised": 3,
      "excluded_under_2w": 4,            // ★(7/16) 재원 2주 미만 제외 수 — 구 observed_only 목록을 숫자로 대체
      "capped_out": 2,                   // lifecycle 억제 후 new·follow_up 후보의 탈락 수만 (advisory 제외)
      "r1_threshold_pp": 17.4,           // ★(8/3 신설) 이번 실행에 실제로 쓴 R1 임계 — 아래 [A 확정 통보]
      "r1_threshold_source": "quantile", // quantile | fallback — 표본 부족 시 고정 15%p로 폴백
      "r1_pool_n": 384,                  // 분위 산출에 쓴 표본 수(8주 × 전 학생의 주간 하락폭)
      "rules_skipped": [{ "rule_id": "R4", "reason": "duration_missing", "students": 5 }]
    }
  }
}
```

> ### `[A 확정 통보 — 2026-08-03]` 감지 v1.5 ①② 반영 (BE·FE 대상)
>
> 외부 대규모 로그 검증([`part_a/13_threshold_validation.md`](part_a/13_threshold_validation.md)) 결과에 따른 확정분 둘이다. **이의는 회신으로.**
>
> **① R4(숨은 위기)를 advisory로 강등한다 — 판정은 그대로다.**
> R4는 지금처럼 평가·기록되고 **응답에도 그대로 실린다**(evidence 포함). 바뀌는 것은 소비 방식뿐이다 — **TOP N 랭킹 비참여 · 상한 슬롯 미소비 · `capped_out` 미산입**. 응답의 **`advisory: true`** 로 구분되니 **알림·신호 카드에서 빼고 학생 상세의 참고 표시로** 보내면 된다.
> ⚠ **`signal_type`에서 `hidden_risk`가 사라지는 게 아니다** — 값 삭제가 아니라 **주장 강도**를 내린 것이다. 그리고 **R4가 다른 규칙과 함께 발화하면 그 경보는 정식**이다(`advisory`는 병합 규칙이 전부 R4일 때만 `true`).
> 근거: 성과 하락 선행성은 근거를 찾지 못했고, 활동 종료 연관은 방향성이 있으나 검열 분리 불가로 확정할 수 없다(13 §3). 파일럿에서 우리 데이터로 재검증한다.
>
> **② R1(정답률 하락) 임계를 고정 −15%p에서 발동률 목표 방식으로 바꾼다.**
> 임계 = 테넌트 풀(8주 × 전 학생) 하락폭 분포의 **하위 5% 분위**. 표본 100 미만이면 **고정 15%p로 폴백**한다. **적중률은 고정 임계와 동등**하고, 바뀌는 것은 **알림 수가 설계값으로 고정**된다는 성질이다(13 §4-1).
> ⚠ **BE 작업은 없다** — 요청 계약 무변경이고, AI가 스냅숏에서 산출한다. 다만 실행마다 임계가 달라지므로 **`stats.r1_threshold_pp`·`r1_threshold_source`·`r1_pool_n`** 을 함께 싣는다. 강사가 "왜 오늘은 안 떴냐"고 물을 때 답할 근거다.
>
> **응답 계약 변경 요약:** `signals[].advisory`(bool) 1필드 · `stats` 3필드 추가. **기존 필드는 무변경**이며 추가분은 전부 기본값이 있다.

**규약:** evidence 빈 신호는 스키마상 불가 · **`observed_only` 목록은 제거(7/16)** — AI는 `stats.excluded_under_2w` 숫자만 내고, "관찰 중"(재원 14일 미만) 표시는 백엔드가 `enrolled_at`으로 직접 계산(09 §3) · **lifecycle 판정은 AI 소유**(09 §4 · 쿨다운 2주) · **Alert 생성·상태 관리는 백엔드 소유** — AI는 신호 산출까지.

> ✅ **A+BE+FE 확인 완료(2026-07-30) — 감지 상한 응답.** TOP 3~5 상한은 `new`·`follow_up`에만 적용하며, `ongoing`과 `return_care`(R5)는 상한 밖으로 추가되어 `signals` 길이와 `rank`가 5를 초과할 수 있다. **백엔드·프론트 확인 결과 양쪽 모두 응답 길이 ≤5 또는 `rank` ≤5를 가정·검증하지 않는다**(백엔드 DTO에 rank 상한 제약 없음 · 프론트 렌더링 개수 제한 없음). 파생 정의: `signals_raised`=상한 밖 합류를 **포함한** 최종 반환 신호 수 · `capped_out`=lifecycle 억제 후 **`new`·`follow_up` 후보의 탈락 수만** · `rank`=반 내 **최종 표시 순번**(통과분 뒤 `ongoing`·R5, 5 초과 가능). 정본은 `part_a/04_threshold_config.md` §3 · `09_detect_spec.md` §4 · 99 #14.

---

### 3.2 `POST /v1/feedback` — 경보 평가 회신 `🕓 보류(7/16)`

🕓 **보류(7/16) — 임계 캘리브레이션 재개 시 활성.** API·화면 버튼 모두 이번 범위에서 뺀다. 단 `/detect` 응답의 `signal_id`는 향후 회신 대비해 백엔드가 계속 저장한다(09 §3). 보류 기간의 오경보 보정은 threshold 시트 §5 섀도 모드 수동 리뷰가 대신한다.

### 3.3 `POST /v1/confirmations` — 제안 확정 회신 `[Open-7: 분리 제안]`

```json
// Request — 태그·라벨·분류 제안을 강사가 확정/거절/수정한 결과 회신
{
  "kind": "tag",                        // tag | label | classification | draft_edit
  "suggestion_id": "uuid",              // 제안 API가 반환했던 ID
  "action": "corrected",                // confirmed | rejected | corrected
  "corrected_value": {                  // corrected일 때만 — 강사가 고친 최종값
    "area_tag": "language",             // 수능 6영역 enum (Open-11 확정 7/15)
    "type_tag": "concept",
    "item_format": "mcq"
  }
}
// Response 200
{ "data": { "accepted": true } }
```

kind: `tag | label | classification | draft_edit`(강사 수정 diff → 문체 프로필 재료 — 예시는 `05_request_json.md` §3 참조). **확정 전 제안은 어디에도 반영되지 않는다**(태그는 learning_event에, 라벨은 초안 생성에 미사용) — 이 보장은 백엔드 몫.

#### (8/5 · P2-c) 구현 상태와 규약

🔴 **`suggestion_id`는 kind마다 다른 네임스페이스의 키다** — 단일 UUID 공간이 아니다.

| `kind` | `suggestion_id` | v1 구현 |
| --- | --- | --- |
| `tag` | `TAG_SUGGESTION.id` | ❌ **400** `kind_not_implemented` |
| `label` | `LABEL_SUGGESTION.id` | ❌ **400** `kind_not_implemented` |
| **`classification`** | **`inquiry_ref`**(§3.5 응답이 에코한다) | ✅ **구현** |
| `draft_edit` | `job_id`(§3.9) | ❌ **400** `kind_not_implemented` |

**미지원 3종을 400으로 거절하는 이유** — 제안 **생성기가 없다**(`TAG_SUGGESTION`·`LABEL_SUGGESTION` 적재 0건 · `label_suggestions`는 v1 상수 `[]`). 확정할 대상이 없는데 받아서 조용히 버리면 BE가 "저장됐다"고 오해한다.

**`classification`이 `inquiry_ref`인 이유** — 문의 1건 = 분류 1건이고 **BE가 이미 갖고 있는 값**이라 왕복이 없다. 응답에 새 UUID를 노출하면 BE가 관리할 식별자만 는다(§3.9 refine이 `job_id`로 통일된 것과 같은 판단).

**`corrected_value`(classification)** — 3축 **전부 nullable**이다. 축이 독립이므로 **부분 정정**이 성립한다(topic만 고치고 나머지는 그대로).

```json
{ "kind": "classification", "suggestion_id": "iq_204", "action": "corrected",
  "corrected_value": { "topic": "grade" } }   // sentiment·urgency는 안 바꿈
```

🔴 **BE가 알아야 할 규약 2건**

1. **`rejected`는 `classification`에서 400이다**(`action_not_supported`). 3축은 값이 반드시 있어야 하는 축이라 "거절"이 정의되지 않는다 — tag·label은 "이 제안을 안 쓴다"가 성립하지만 분류는 아니다.
2. **예측과 같은 값을 보내도 정정으로 세지 않는다.** 저장 층이 예측과 대조해 **다른 축만** `corrected_*`에 남긴다(P2-b 기록 규약 ①). 강사가 드롭다운을 열어 같은 값을 다시 골라도 **재분류율이 부풀려지지 않게** 하는 장치다. `reviewed_at`은 어느 경우든 기록된다.

**404** — 대상 분류가 없을 때다(폴백이라 적재 안 됐거나 분류를 부른 적이 없다). **헤더**: `X-Tenant-Id`·`X-Request-Id` 필수, **`Idempotency-Key` 없음**(자연키 갱신이라 재시도가 안전하다).

---

### 3.4 `POST /v1/drafts` — 초안 생성 (202)

| 방식 | 호출자 | 멱등 |
| --- | --- | --- |
| 202 → `GET /v1/drafts/{draft_id}` | 백엔드 (문의 도착 즉시 — 사전 생성 / 월간 리포트 주기) | `Idempotency-Key = inquiry_ref` 등 |

**Request:**

```json
{
  "kind": "reply",                     // reply(문의 답변) | report(월별 일괄 — 매월 1일 전월분, 할당 미소모)
                                       // | report_single(단건 수시 — report_single 할당+일일 상한) | counsel_pack_single
  "student_ref": "st_8f2a", "guardian_ref": "gd_11b0",   // 전부 alias — 실명 매핑은 백엔드 vault만 보유
  "label_snapshot": {                  // 강사가 확정한 학부모 라벨 4축 — 이 시점 값으로 동결(생성 중 라벨 변경 무영향)
    "comm": "narrative",               // data(수치 선호) | narrative(서사 선호)
    "interest": "attitude",            // grade | attitude | admission
    "sensitivity": "anxious",          // anxious(완곡 강화) | direct(결론 선행)
    "frequency": "frequent"            // frequent(짧게·변경분만) | monthly(충실하게)
  },
  "inquiry": {                         // kind=reply일 때만
    "inquiry_ref": "iq_204",
    "body_text": "요즘 아이가 힘들어하는 것 같은데...",   // 백엔드 1차 마스킹 통과본 (Open-4d)
    "received_at": "2026-07-10T21:04:00+09:00"
  },
  "context": { "...": "§4.2 표 참조 — interventions·comm_history·(리포트면) benchmarks" }
}
```

**Response (GET) 200:**

```json
{
  "data": {
    "draft_id": "uuid",
    "status": "generated",             // generated | template_only | rejected_insufficient | failed — 아래 status 값 설명 참조
    "status_reason": null,             // 미생성/실패 시 사유 코드 (화면 문구 번역은 백엔드)
    "classification": { "topic": "grade", "sentiment": "complaint", "urgency": "immediate" },   // 분류ⓑ 3축 동봉 — 인박스 정렬용
    "blocks": [{                       // 초안은 블록 단위 — 강사가 블록별로 수정 가능하게
      "seq": 1,
      "block_type": "fact",            // greeting | fact | suggestion | closing | chart_analysis(리포트)
      "content": "서연이는 6월 한 달 지문 42개·312문항을 성실히 풀었고...",
      "evidence": [{ "source_table": "learning_event_agg", "record_id": "agg_w27" }],  // 블록별 근거 — "근거 보기" UI용
      "empty_reason": null             // 값이 있으면 = 재시도 3회 소진 섹션 → "직접 작성해 주세요" 표시
    }],
    "gates": [{ "gate": "SourceGrounding", "passed": true }, { "gate": "ToneSafety", "passed": true }]
  }
}
```

**status 값:** `generated` 정상 · `template_only` 데이터 무관 문의(일반 템플릿만) · `rejected_insufficient` 데이터 부족(정상 상태!) · `failed` LLM 장애 등(사유 포함). 블록 `content`가 비고 `empty_reason`이 있으면 = 재시도 3회 소진 섹션.

**리포트(kind=report) 전용 규약 — 차트별 해설 블록 `[제안]`:** 리포트는 차트(영역별 약점 현황 · 보강 후 개선 추이 · 전국 백분위) 단위로 구성되며, 블록에 `block_type: "chart_analysis"` + `chart_ref`가 붙는다. **차트 수치·개선율·백분위는 전부 코드/백엔드가 확정하고 LLM은 해설 문장만** 생성(SourceGrounding이 문장 속 수치를 benchmarks·집계와 대조). **노출 정책 3층:** 학생 = 비교 일절 비노출 / 학부모 리포트 = 전국 백분위만(반 평균·석차 비노출 — teacher_only 데이터는 컨텍스트에서 구조적 제외) / 반 평균 = 강사 내부 화면 전용(성적 지표 관리 도구). 하위 백분위는 수치는 사실대로 + 문장은 성장 추이 중심(라벨 anxious 시 완곡 강화). **(7/15 확정 — BE-5)** 월별 일괄 생성은 학생별 N회 호출이 아니라 **일괄 1회(Kafka 배치)** — AI는 내부에서 학생 단위로 처리·부분 실패 격리하고 결과를 학생별 이벤트로 발행. 스트림 스키마는 v1.0 부록.

**`POST /v1/drafts/{id}/refine`** — **채팅형 다듬기 (핑퐁)** `[제안]`:

```json
// Request — 강사가 초안 아래 채팅창에 입력하면 백엔드가 그대로 전달
{
  "scope": "block",                    // whole(전체) | block(특정 블록만)
  "block_seq": 2,                      // scope=block일 때만
  "instruction": "마지막에 다음 상담 일정을 제안하는 문장 하나 넣어주세요. 전체적으로 조금 더 짧게.",
  "preset": null                       // UI 버튼은 preset으로: softer | conclusion_first | shorter (instruction과 병용 가능)
}
// 롤백은 { "revert_to": 2 } — 2턴째 결과로 복원, 할당 미소모
```

- 강사가 자유 문장으로 지시하며 초안을 반복 다듬는 대화형 루프. 202 → GET 시 `revision_no` 증가 + `revisions[]` 턴 이력(롤백: `{"revert_to": n}` — 할당 미소모).
- **핑퐁의 가드레일:** ① 매 턴 결과도 게이트 전체(Evidence·SourceGrounding·ToneSafety) 재통과 — **강사 지시가 게이트를 이기지 못한다**("정답률 95%라고 써줘"는 근거 부재로 차단+사유 반환) ② 다듬기 1턴 = 상담 초안 할당 1 소모라는 과금 규칙은 **백엔드가 집행(7/15 — AI는 무제한 처리·쿼터 무관)**, 잔여 표시도 백엔드 ③ 지시문·수정 이력은 문체 프로필 재료로 축적.

**규약:** 승인·발송은 백엔드(HITL) — 다듬기는 초안까지, 이 API에 발송 개념 없음. `label_snapshot`은 **백엔드 확정 라벨만**(ai_suggested 미포함).

---

### 3.5 `POST /v1/classify` — 문의 분류 (동기)

```json
// Request — 학부모 문의 도착 즉시 (초안 생성과 별도로 먼저 호출해도 됨 — 인박스 정렬용)
{ "inquiry_ref": "iq_204", "body_text": "여름방학 특강 시간표가 궁금합니다" }
// Response 200 (동기 — 수 초 내)
{ "data": { "topic": "schedule", "sentiment": "normal", "urgency": "normal",
            "confidence": { "topic": 0.95, "sentiment": 0.88, "urgency": 0.91 },
            "classified": true, "fallback_reason": null } }
// Response 200 — 분류하지 못한 경우(500이 아니다 · error_codes §2.5)
{ "data": { "topic": "etc", "sentiment": "normal", "urgency": "normal",
            "confidence": { "topic": 0.0, "sentiment": 0.0, "urgency": 0.0 },
            "classified": false, "fallback_reason": "tripwire_blocked" } }
```

**3축 독립 분류다** — 축마다 값 집합도 용도도 다르고, 한 축의 오분류가 다른 축을 오염시키지 않는다(Zendesk intelligent triage와 같은 구조). ⚠ `confidence`는 **축별 객체**다 — 3축이 독립이므로 확신도도 축마다 다르다(Zendesk는 필드마다 별도 confidence를 단다).

| 축 | 값(enum 강제 — 목록 밖 값은 나올 수 없음) | 쓰이는 곳 |
| --- | --- | --- |
| `topic` | `grade` \| `schedule` \| `counsel_request` \| `etc` | 초안 종류(§3.9 `template_only`) · 정렬 |
| `sentiment` | `normal` \| `complaint` | 인박스 최상단 정렬 · 완충 강도 |
| `urgency` | `immediate` \| `normal` | 정렬 |

> 🔴 **(8/5) `complaint`가 `topic`에서 `sentiment`로 이동했다.** 상세는 `part_a/01` §4-ⓑ 참조. **BE는 `inquiry.topic`에 `complaint`를 보내면 400이다.**

**응답에 `inquiry_ref`를 에코한다**(8/5) — 요청값 그대로다. BE가 아는 값이지만 **응답만 보고 다음 호출을 구성할 수 있어야** 계약이 폐쇄 회로가 된다. 확정 회신(§3.3)의 `suggestion_id`가 **이 값**이다.

🔴 **캐시 규약(8/5 · P2-c)** — 같은 `(tenant_id, inquiry_ref)` 재호출은 **저장된 예측을 그대로 돌려주고 LLM을 부르지 않는다**(§1의 "분류·태깅(캐시)" 규정 · 태깅 §3.6 선례와 동형). 부수 효과로 **재시도가 멱등 키 없이 안전**해지고 같은 문의엔 항상 같은 답이 나간다.
⚠ **`classified=false`는 캐시되지 않는다** — 적재 자체를 안 하므로(평가셋 오염 방지) 재호출하면 **다시 시도**한다. redaction·파싱 실패는 일시적일 수 있다.

**헤더 규약** — `X-Tenant-Id` · `X-Request-Id` **필수**, **`Idempotency-Key` 없음**(부작용 없는 동기 호출이고 멱등 저장이 없다 — 같은 본문은 결정론 설정으로 같은 결과다).

🔴 **`body_text`는 원문이다**(counsel의 `text_masked`와 다르다). **AI가 2차 redaction을 적용한 뒤 LLM에 보내며**(masking_redaction §3·§4), ⟪확인필요⟫가 남거나 전송 직전 잔여 흔적이 발견되면 **LLM을 호출하지 않고** `classified: false`로 응답한다. 원문은 로그·에러 detail 어디에도 남지 않는다(§2.3).

**`classified` · `fallback_reason`** — 분류 실패는 **200**이다. `classified: false`면 `etc`를 확신 있는 판정으로 읽지 말고 **정렬을 적용하지 않은 채 시간순으로** 둔다(`error_codes` §2.5). 사유는 **enum 2종**(`tripwire_blocked` | `parse_exhausted`)이며 자유 문자열이 아니다(`error_codes` §2.7 규칙 1). `classified=true`면 `fallback_reason`은 반드시 `null`이고 그 반대도 마찬가지다 — 짝이 어긋나면 스키마가 거부한다(규칙 2). **LLM 장애는 폴백이 아니라 503**이다.

⚠ **`⟪확인필요⟫`가 남아도 분류를 계속한다**(8/5 정정) — 가려진 텍스트는 이미 안전하고 분류에 이름은 필요 없다. 종전 `redaction_uncertain` 사유는 없어졌다. 전송 직전 트립와이어가 **잔여 흔적**을 발견한 경우만 `tripwire_blocked`로 폴백한다(그건 '안 가려진 게 남았다'는 신호라 성격이 다르다).

⚠ **`confidence`는 LLM 자기보고이며 캘리브레이션되지 않았다** — 확률로 읽지 말 것. 임계값을 아직 정하지 않은 이유가 이것이다(아래).

**사용 범위 — 되돌릴 수 있는 것까지다**(`part_a/01` §4-ⓑ). 학부모에게 나가는 자동 응답에는 쓰지 않는다(승인·발송은 전부 HITL — 백엔드도 준수). 되돌리기 경로는 §3.9 재요청 규약이 보장한다.

**confidence에 따른 자동화 강등 — Salesforce Einstein 3단 패턴.** 낮은 확신은 "`etc`로 넣기"가 아니라 **자동화 수준을 한 단계 내리는 것**으로 처리한다.

| `confidence.topic` | 처리 |
| --- | --- |
| 임계값 이상 | `topic`을 확정값으로 사용 — `template_only` 분기 포함 |
| 임계값 미만 | `topic`은 **표시·정렬에만**. 초안은 **일반 경로로 생성**하고 강사에게 "일정 문의로 보입니다" 배지만 표시 |

이 방향이 fail-safe다 — 확신이 낮을 때 정상 초안을 만들어두면 강사가 안 쓰면 그만이지만, 반대(확신 낮은데 `template_only`)는 근거가 있는데도 초안이 사라진다.
⚠ **임계값 숫자는 여기서 정하지 않는다** — 실측 데이터가 없다. `/v1/classify` 구현(P2) 후 재분류율(강사가 예측을 뒤집은 비율)을 관측해 정한다. Salesforce 권고도 "Start in recommendation mode, and monitor accuracy... before setting up auto-triage"다.

### 3.6 `POST /v1/tags/suggest` — 태그 제안 (동기·캐시)

```json
// Request — 강사가 과제/채점 입력 화면에서 제목을 입력하는 순간 호출
{
  "source_kind": "trackB_grading",     // trackA_upload | trackB_grading
  "title_text": "6월 모의고사 비문학 대비 #3"
}
// Response 200 — 입력 폼에 미리 선택된 태그로 표시 → 강사 원탭 확정(→ /confirmations)
{
  "data": {
    "suggestion_id": "uuid",
    "area_tag": "reading",             // 수능 6영역 enum (Open-11 확정 7/15): reading·literature·speech·writing·language·media
    "type_tag": "infer",               // fact | infer | critic | concept
    "item_format": "mcq",              // mcq | short | essay
    "confidence": 0.92,
    "cached": true                     // true = 같은 명명 패턴 캐시 히트 (LLM 호출 0회 — 비용 없음)
  }
}
```

**강사 확정(→ /confirmations) 전 learning_event 반영 금지**는 백엔드 책임. 동일 명명 패턴은 캐시 히트(LLM 0회).

### 3.7 `POST /v1/labels/suggest` — 라벨 제안 (주간 배치·202)

```json
// Request — 주간 배치: 소통 이력 5건 이상 + 라벨 미설정 학부모만 골라서 전달
{
  "guardians": [{
    "guardian_ref": "gd_11b0",
    "history": [                       // 백엔드 1차 마스킹 통과본 (Open-4d)
      { "record_id": "cm_88", "direction": "inbound", "text": "숫자로 정리해 주세요", "at": "..." }
    ]
  }]
}
// Response (GET) 200 — 학부모 360 화면에 '점선 칩'으로 표시 → 강사 확정(→ /confirmations) 전 초안에 미사용
{
  "data": {
    "suggestions": [{
      "suggestion_id": "uuid", "guardian_ref": "gd_11b0",
      "axis": "comm", "value": "data",   // 4축 enum만 가능 — 자유 텍스트 라벨은 스키마상 불가
      "confidence": 0.86,
      "evidence_quotes": [               // 제안 근거 인용 — 실제 이력에 실존하는 문장만 (실존 검증 실패 시 제안 자체 폐기)
        { "record_id": "cm_88", "quote": "숫자로 정리해 주세요" }
      ]
    }]
  }
}
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
      "spec_version": 1,
      "reused": false,                 // true = 같은 양식 재수입 → LLM 호출 0회로 기존 매핑 재사용
      "columns": [                     // 강사 확인 화면에 그대로 렌더링하면 되는 형태
        { "source": "원생명",   "target": "student_alias", "confidence": 0.97 },
        { "source": "점수A",   "target": "weekly_score",  "confidence": 0.41,
          "needs_review": true,        // 확신 낮음 — 강사 확인 필수 표시 (숨기지 않음)
          "probe_note": "유니크 값 8,7,10,9… → 10점 만점 추정" },   // 조사 에이전트의 추론 근거 (툴팁용)
        { "source": "連락처",  "target": null,
          "unmapped_reason": "개인정보 필드 — 자동 이전 대상 아님" }  // '모름/제외'를 정직하게 명시 — 억지 매핑 없음
      ],
      "unmapped_target_fields": ["event_type", "occurred_at"],  // 매핑 후보를 못 찾은 표준 필드 — 필수 판단은 백엔드
      "source_fingerprint": "9f2c...",           // 양식 지문 — 백엔드가 보관·반송(AI만 계산 가능)
      "sample_rows": [ { "...": "변환 예시 5행" } ]
    }
  }
}
```

**confirm Request:** `{ "spec_overrides": [{ "source_column": "점수B", "target_field": "weekly_score_2" }] }` → 확정 spec 저장 → `status: done` + 확정된 `mapping_preview`.

**status:** `profiling → inferring → probing(조사 에이전트) → preview_ready → done(확정 spec)` / `failed`. **규약(2026-07-30 개정):** **AI는 매핑 제안까지** — 전체 행 변환·행별 검증·집계·산출물 저장은 백엔드 소유이며 AI 응답에 `output_url`·행 집계가 없다(`part_a/10_import_spec.md` §4). **필수 여부 판단도 백엔드 소유** — 백엔드가 확정 매핑의 기준 데이터를 보유하고 AI는 양식 재사용을 위해 전달받아 활용한다. AI는 정보만 준다(`unmapped_target_fields`·`columns[].confidence`·`needs_review`)이며 **확정 차단 상태가 없다.** 동의 미보유 행 '보류'는 백엔드 · 같은 양식 재수입은 `reused: true`(LLM 0회 — `source_fingerprint` 반송 기반).

---

### 3.9 `/v1/counsel/drafts` — 상담 초안 (202 · **요청 단위 = 문의 1건**)

> **개정 근거:** 인박스 데이터계약 v1 §4(2026-07-31 **A 확정 통보**) · 99 D ㉛.
> 종전 `/v1/agents/counsel-pack`(학생 묶음 pack)은 **폐기**한다 — 화면 확정으로 선제 상담 자료·반 배치 생성이 사라지고 **문의 도착 시 자동 1회 생성**이 됐다. 재생성 API는 없다(다듬기가 곧 수정).
> 종전 절의 resume·완료분 개별 조회 규약은 **99 D ㉖에 옮겨 적었다**(정보 증발 방지).

```json
// ① POST /v1/counsel/drafts — 기동 (헤더: X-Tenant-Id · X-Request-Id · Idempotency-Key)
{
  "inquiry": {
    "inquiry_ref": "iq_884",             // BE 원본 문의 논리 참조 — AI에겐 불투명 키
    "topic": "grade",                    // grade | schedule | counsel_request | etc — 🔴 8/5 complaint 제거(§3.5)
    "urgency": "immediate",              // immediate | normal — 완충 강화 입력
    "received_at": "2026-07-31T14:20:00+09:00",
    "text_masked": "요즘 아이가 힘들어하는 것 같은데…"   // redaction 통과분 (불변식 3)
  },
  "student_ref": "st_8f2a", "parent_ref": "pa_9c1d", "class_ref": "cl_a1",  // 전부 가명
  "labels": ["narrative", "anxiety_sensitive"],        // 확정 라벨 — 톤 게이트 입력(05 매핑)
  "dismissed_suggestions": [{ "axis": "frequency", "value": "monthly" }],   // 재제안 억제
  "context": {                                         // 인용 가능한 사실의 전체 우주
    "snapshot_hash": "sha256:…",                       // 재현성 축(AI_RUN · 불변식 8)
    "period_label": "2026년 7월",
    "facts": [{ "record_id": "le_2041", "summary": "6월 지문 42개·312문항" }]
  }
}
// → 202 { "job_id": "cj_1029", "status": "queued" }
//   같은 Idempotency-Key + 같은 바디 = 기존 결과 재반환 · 다른 바디 = 409 IDEMPOTENCY_CONFLICT (§2.3)

// ② 완료 통지 — Kafka (job_id, terminal phase) 멱등. 본문 없음 — 본문은 ③으로만 (08)

// ③ GET /v1/counsel/drafts/{job_id} — 결과 회수
{
  "data": {
    "job_id": "cj_1029",
    "status": "succeeded",                  // 공통 phase 7종(error_codes §2.5)
    "result": {
      "draft_status": "generated",          // generated | rejected_insufficient | llm_failed | gate_exhausted
      "text": "어머님, 먼저 세심하게…",       // 게이트 통과본만 — 미통과는 text 없음 + 사유
      "citations": [                        // **항상 1건 이상** — 근거 없는 초안은 존재 불가(불변식 2)
        { "cite_id": "L1", "record_id": "le_2041", "summary": "6월 지문 42개·312문항" }
      ],
      "labels_applied": ["narrative", "anxiety_sensitive"],
      "label_suggestions": [],              // ⚠ v1 상수 [] — 생성기 미구현(99 D ㊲)
      "status_reason": null,                // 거부·실패 사유 코드(error_codes §2.1)
      "generated_at": "2026-07-31T14:24:11+09:00"
    }
  },
  "error": null,
  "meta": { "execution_id": "…", "versions": { "…§2.2…": "…" } }
}

// ④ POST /v1/counsel/drafts/{job_id}/refine — 다듬기 (대상 키 = ①이 돌려준 job_id · 동기 · 매 턴 게이트 전체 재통과)
// 요청  { "instruction": "정답률이 오르고 있다고 강조해서 써줘", "turn_no": 3 }
// 반영  { "applied": true,  "text": "…", "citations": [ … ] }
// 차단  { "applied": false, "blocked_reason": "comparison_exposure", "message": "…" }
```

**규약**

- **잡 성공 ≠ 초안 존재.** `status="succeeded"` + `result.draft_status="rejected_insufficient"`는 **정상 조합**이다(데이터 부족은 에러가 아니다 — 불변식 4). 화면은 "아직 데이터를 모으는 중이에요"를 그린다.
- **`citations[]`는 ≥1이 타입 계약**이다. 인용 가능한 근거(`record_id`가 있는 fact)가 0건이면 **LLM 호출 전에** `rejected_insufficient`로 끊는다 — 게이트를 통과한 초안을 만들어 놓고 근거가 없어 버리는 낭비를 만들지 않는다.
- **`refine` 차단도 200**이다(`applied:false` + `blocked_reason`). `GateRejected`를 5xx로 올리면 리뷰 반려(불변식 4 · error_codes §4).
- 🔴 **차단 문구는 AI가 주지 않는다(8/5).** `blocked_reason` 8종에 대한 표시 문구는 `part_a/06_refine_policy.md` §4 표가 원본이며 **BE가 매핑**한다 — 초안 `draft_status`·classify 폴백과 같은 규약이다(`error_codes` §2.1 "백엔드 표시 문구" 열 · §2.7 규칙 3). ⚠ **종전 응답의 `message` 필드는 제거됐다.**
- **refine 대상 키는 `job_id`다.** 문의 1건 = 잡 1개 = 초안 1개(pack N=1)라 별도 `draft_id`를 노출하지 않는다 — BE는 **Kafka 완료 통지가 싣는 `job_id`를 그대로** 쓰면 되고 별도 조회가 필요 없다. FE 계약 §3-③은 `inquiry_id` 기준이므로 **BE가 `inquiry_id → job_id` 매핑을 중계**한다(AI는 원본 문의에 접근하지 않는다).
  > 🔴 **정정(8/5).** 종전 표기는 `draft_id`였는데 그 값이 **어떤 응답에도 실리지 않아** BE가 refine을 호출할 계약 경로가 없었다(`CounselDraftJobView`는 `job_id`·`status`·`result` 3필드뿐 — 호출하면 404 확정). 스키마에 필드를 추가하는 대신 **키를 `job_id`로 통일**했다. 응답 스키마 무변경.
- **문의 유형(`topic`)을 정정하면 초안을 재요청한다 — 되돌리기가 계약 의무다.** `inquiry.topic`은 분류(ⓑ)의 판정값이고, 그 값이 `template_only`처럼 **초안 종류를 가른다**(§3.5). 분류가 초안을 가르는 것이 허용되는 전제가 **강사가 되돌릴 수 있다**는 것이므로(`part_a/01` §4-ⓑ), 오분류 시 경로를 계약으로 보장한다.
  - 강사가 인박스에서 문의 유형을 정정한다 → BE가 **새 `Idempotency-Key`**로 `POST /v1/counsel/drafts`를 정정된 `topic`으로 다시 호출한다 → 새 초안이 생성된다.
  - **재생성 전용 API는 없다**(이 절 서두). 같은 키 + 다른 바디는 `409 IDEMPOTENCY_CONFLICT`이므로 **반드시 새 키**여야 한다.
  - **다듬기(refine)로는 되돌릴 수 없다.** `template_only`는 `text`가 `null`이고 refine 대상으로 등록되지 않는다 — 다듬을 원본이 없다.
  - 정정 이력은 분류 품질 평가셋으로 `INQUIRY_CLASS`에 축적한다(`part_a/03` §C7). 축별 정정은 `corrected_topic`·`corrected_sentiment`·`corrected_urgency`에 남고 `corrected_by_teacher`는 그 셋의 NULL 여부에서 나오는 **파생값**이다. ⚠ **적재는 아직 없다**(99 ⓑ) — 스키마만 섰고 쓰기 경로는 BE와 정해야 한다(P2-c).
- **턴 상한은 AI가 판정하지 않는다.** `turn_no`는 로그·이력용으로 받기만 한다 — "세션 턴 상한 없음, 월 할당이 자연 상한"(`part_a/06` §1)이고 할당 집행은 전부 백엔드 Billing이다(7/15 BE-4).
- **v1 구현 범위 정정 3건**(계약보다 낮게 구현되는 부분)은 `docs/handoff/2026-07-31_counsel_router_v1_scope_to_BE.md`가 정본이다.

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
| `record_id` | string | ✅ | **evidence 역추적 키** | 백엔드 DB PK — **불변 필수** |
| `student_ref` | string | ✅ | | |
| `type` | enum `solve·submit·attend·consult` | ✅ | 규칙별 | |
| `occurred_at` | datetime | ✅ | 시계열 | |
| `correct` | bool | solve만 | 정답률(R1·R6) | |
| `duration_sec` | int | solve만 | 풀이시간(R4) | 없으면 R4 미적용(대체 신호) |
| `passage_word_count` | int | 지문형만 | **어절 정규화** | 국어 특화의 핵심 필드 |
| `area_tag` / `type_tag` | enum | 있으면 | 유형별 정답률(R6)·약점 지도 | 미태깅 허용 — 태깅 제안이 채움. **area 값(수능 6영역): `reading(독서)·literature(문학)·speech(화법)·writing(작문)·language(언어/문법)·media(매체)`** + `subject_track: common·elective` 메타 · `item_format: v1은 mcq만`(short·essay 예약) — Open-11 확정(7/15) |
| `assignment_title_text` | string | 있으면 | **태깅 제안(ⓒ) 입력** | ⚠ `[Open-4b]` 제공 불가 시 기능 자체 불가 |
| `source` | enum `trackA·trackB·studentHome` | ✅ | 품질 가중 | |

### 4.2 초안 컨텍스트 (`/drafts` · `/counsel/drafts`)

| 필드 | 타입 | 필수 | 쓰는 곳 | 비고 |
| --- | --- | --- | --- | --- |
| `interventions[]` | array | ✅ | 초안 맥락 | `{record_id, type, memo_text, at}` — 강사 본인의 기록 |
| `comm_history[]` | array | ✅ | 톤·이력·라벨 제안 | `{record_id, direction, text, at}` — `[Open-4d]` 최근 10건·90일 · 백엔드 1차 마스킹 제안 |
| `guardian.label_snapshot` | object | ❌ **(7/31 정정)** | 톤 매핑 | 4축 enum — **확정본만**. **선택이다** — 라벨 검토함 계약 §6이 "라벨 0개 = 기본 톤으로 생성"을 확정했고 개통 첫날은 전원이 0개다. **누락 축은 기본값**(`part_a/05` §7-2), 응답 `labels_applied`에 실제 적용된 4축을 싣는다. 4축 enum 밖의 문자열은 400(§7-4 — alias 금지) |
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
- **AI 장애 시:** 백엔드는 전일 브리핑 유지 + 배지(기존 NFR). `/detect` 실패 시 다음 배치까지 대기 — 멱등키(tenant+analysis_date)로 중복 방지.
- **감사:** 모든 요청·응답은 `X-Request-Id`로 양쪽에서 상호 추적 가능.
- **하위호환:** 필드 **추가** = 마이너(무통보 가능) · 필드 **삭제·의미 변경** = 메이저(협의 필수). `meta.versions.contract`로 상호 확인.

## §6. 확정 절차

1. 리뷰 미팅(30분) — §1의 Open-1~12만 논의 → 체크박스 채움 (Open-11은 B 동석 필요, Open-12는 P2라 소유 확인만)
2. 반영 후 **v1.0 승격** → 저장소 커밋(이후 변경은 PR)
3. 양측 병행 개발 시작: A = FakeSnapshot · 백엔드 = AI 스텁
4. 통합 시점: 부록 A 해시 테스트 벡터 3건으로 상호 검증

---

## 부록 A. snapshot_hash 산정 `[제안]`

```
sha256( canonical_json({
  snapshot_meta: { week_start, term_context },
  students:        sorted by student_ref,
  learning_events: sorted by record_id,
  alert_context:   sorted by (student_ref, signal_type)   // (7/16) lifecycle 입력이라 해시 대상에 포함
}) )
```

canonical_json = 키 정렬 · 공백 제거 · UTF-8. **동일 구현 검증용 테스트 벡터 3건을 v1.0에 첨부한다** (백엔드 Java와 AI Python이 같은 해시를 내는지 통합 전 확인).

> 요약·리뷰용 시각 버전(팀공유본 html)은 노션에 있다 — **충돌 시 레포의 본 md가 정답.** JSON 예시의 `//` 주석은 설명용이며 실제 전송 페이로드에는 포함하지 않는다. 요청 바디만 빠르게 볼 때는 `05_request_json.md`.
