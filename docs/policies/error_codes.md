# [체크온] 에러·상태 코드 사전 v0 — API 계약 부록 C `[제안]`

> **핵심 구분:** "AI가 못 한 것"(에러)과 "AI가 안 하기로 판단한 것"(**정상 상태**)을 코드 레벨에서 분리한다. 후자는 200으로 내려간다 — 게이트 거부는 제품이 약속대로 동작한 것이지 장애가 아니다. 백엔드 표시 문구까지 이 문서로 고정해 UI 재협의를 없앤다.

---

## 1. HTTP 레이어 (전 엔드포인트 공통)

| HTTP | 코드 | 뜻 | 백엔드 처리 |
| --- | --- | --- | --- |
| 400 | `INVALID_SCHEMA` | 요청 바디 스키마 위반(detail에 필드 경로) | 개발 오류 — 로그·알럿, 강사 노출 없음 |
| 401/403 | `UNAUTHORIZED` / `TENANT_MISMATCH` | 내부 토큰 불량 / tenant 불일치 | 보안 알럿 |
| 404 | `NOT_FOUND` | draft_id·job_id 등 부재 | |
| 409 | `IDEMPOTENCY_CONFLICT` | 같은 Idempotency-Key로 다른 바디 | 기존 결과 반환 안 함 — 개발 오류 |
| 422 | `CONSENT_ABSENT` | 대상 학생 동의 없음(생성류 요청 자체가 오면 안 됨 — 백엔드 선차단이 1차) | "동의가 필요한 기능이에요" |
| 429 | `QUOTA_EXCEEDED` | 할당 소진 — detail `{limit_kind, scope, limit, used, resets_at}` (쿼터 명세 §4) | limit_kind×scope로 문구 분기 |
| 429 | `RATE_LIMITED` | 순간 요청 폭주(할당과 무관한 기술적 제한) | 백엔드 재시도(backoff) |
| 500 | `INTERNAL` | 미분류 서버 오류 | 재시도 1회 후 폴백 |
| 503 | `LLM_UPSTREAM_DOWN` | LLM 벤더 장애 | **폴백 규약**: 감지=전일 브리핑 유지+배지 · 초안="잠시 후 다시" |
| 504 | `TIMEOUT` | 동기 10s / 비동기 총 5분 초과 | 비동기는 status=failed로 수렴 |

## 2. 산출물 상태 코드 — 200 안의 `status` (에러 아님!)

### 2.1 Draft (`GET /v1/drafts/{id}`)

| status | 뜻 | `status_reason` 예 | 백엔드 표시 문구 |
| --- | --- | --- | --- |
| `queued` / `generating` | 대기/생성 중 | — | 스피너 |
| `generated` | 정상 — 게이트 전체 통과 | — | 초안 표시 |
| `template_only` | **정상** — 데이터 무관 문의(시간표 등)라 일반 템플릿만 | `no_data_topic` | "일반 안내 초안이에요 — 학습 데이터는 사용하지 않았어요" |
| `rejected_insufficient` | **정상** — 데이터 부족으로 생성 안 함(재원 2주 미만 등) | `data_lt_2weeks` | "○○ 학생은 아직 데이터를 모으는 중이에요(다음 달부터 가능)" |
| `failed` | 진짜 실패 | `llm_timeout` `parse_fail_exhausted` | "생성에 실패했어요 — 다시 시도" |

블록 레벨: `content` 비었고 `empty_reason` 있음 = 해당 섹션만 재시도 3회 소진 — 초안 전체는 유효. 문구: "이 부분은 자동 작성하지 못했어요 — 직접 채워주세요".

### 2.2 refine 리비전 (`revisions[]` — 핑퐁 정책서 §4와 동일 enum)

`blocked_reason: evidence_missing | comparison_exposure | tone_violation | pii_exposure | out_of_scope` — 문구는 핑퐁 정책서 §4 표가 원본(여기 중복 정의하지 않음).

### 2.3 감지 (`/v1/detect` 응답 내)

| 필드 | 뜻 | 문구 |
| --- | --- | --- |
| `observed_only[]` | 데이터 2주 미만 — 판단 보류 학생 목록 | "관찰 중" 뱃지(경보 아님) |
| `rule_skipped[]` | 규칙별 미적용 사유 — `{rule: "R4", reason: "duration_missing"}` `{rule: "R6", reason: "tagging_below_60pct"}` | 강사 노출 없음 — 운영 대시보드만 |
| `shadow: true` | 섀도 모드 실행분 | 브리핑 미발송 |

### 2.4 Import (`GET /v1/imports/{job_id}`)

| status | 뜻 | 문구 |
| --- | --- | --- |
| `profiled` → `preview_ready` | 매핑 추론 완료 — 강사 확인 대기 | 미리보기 화면 |
| `needs_review` (컬럼 플래그) | confidence 낮은 매핑 — **숨기지 않고 노출** | "이 열은 확신이 낮아요 — 확인해 주세요" |
| `unmapped` (컬럼 플래그) | 매핑 불가 — '모름' 정직 표기 | "무엇인지 알 수 없어 건너뛰어요" |
| `transformed` | 변환 완료 — `row_ok/row_fail` | 결과 요약 |
| `failed` | 파일 손상·형식 불가 | "파일을 읽지 못했어요" |

### 2.5 에이전트 (`GET /v1/agents/{run_id}`)

`running(progress "19/22") | paused(체크포인트 — resume 가능) | done | failed(완료분은 보존 — 학생 19명분 draft는 유효)`.

## 3. LLM_CALL.outcome (내부 관측 — API 미노출)

`ok | parse_fail | field_missing | bad_ref(근거 ID 실존 실패) | timeout | provider_error | redaction_blocked(전송 전 차단 — 마스킹 정의서 §3 fail-closed)`. 재시도 정책: parse_fail·field_missing은 블록 단위 ≤3회, bad_ref는 즉시 해당 문장 폐기(재시도 무의미 — 환각), redaction_blocked는 재시도 금지+알럿.

## 4. 도메인 예외 ↔ HTTP 매핑 (runtime/errors 구현 지침)

```
DomainException (base)
├─ ConsentAbsent          → 422 CONSENT_ABSENT
├─ QuotaExceeded          → 429 QUOTA_EXCEEDED      (Billing 헤더 기반 — BE-4)
├─ SnapshotInvalid        → 400 INVALID_SCHEMA
├─ GateRejected           → 200 + status (에러로 승격 금지!)
├─ EvidenceUnresolvable   → 200 + 블록 empty_reason=bad_ref
├─ LlmUnavailable         → 503 LLM_UPSTREAM_DOWN
└─ RedactionUncertain     → 500 INTERNAL (원문 노출 위험 — 상세 사유 응답에 미포함)
```

**불변식:** `GateRejected`를 5xx로 올리는 코드는 리뷰 반려 — "게이트 거부 = 정상"이 이 사전의 존재 이유.

## 5. 문구 스타일 가이드 (백엔드/프론트 전달용)

- 강사向 문구는 실패 귀책을 학생·강사에게 돌리지 않는다("데이터가 부족해요" ○ / "학생이 안 풀었어요" ×).
- '정상 상태' 문구에는 **다음 행동**을 항상 포함("다음 달부터 가능" · "직접 채워주세요").
- 쿼터 문구는 리셋 시점 명시("내일 자정 이후" / "8월 1일 초기화") + 상위 플랜 안내는 monthly scope에만.
