# [체크온] 에러·상태 코드 사전 v0 — API 계약 부록 C **[확정 7/21 — 코드 사전 정본, 99 #12]**

> **핵심 구분:** "AI가 못 한 것"(에러)과 "AI가 안 하기로 판단한 것"(**정상 상태**)을 코드 레벨에서 분리한다. 후자는 200으로 내려간다 — 게이트 거부는 제품이 약속대로 동작한 것이지 장애가 아니다. 백엔드 표시 문구까지 이 문서로 고정해 UI 재협의를 없앤다.

---

## 1. HTTP 레이어 (전 엔드포인트 공통)

| HTTP | 코드 | 뜻 | 백엔드 처리 |
| --- | --- | --- | --- |
| 400 | `INVALID_SCHEMA` | 요청 바디 스키마 위반(detail에 필드 경로) | 개발 오류 — 로그·알럿, 강사 노출 없음 |
| 401/403 | `UNAUTHORIZED` / `TENANT_MISMATCH` | 내부 토큰 불량 / tenant 불일치 | 보안 알럿 |
| 404 | `NOT_FOUND` | draft_id·job_id 등 부재 · 동의 없는 학생 참조 포함(존재 자체를 숨김) | |
| 409 | `IDEMPOTENCY_CONFLICT` | 같은 Idempotency-Key로 다른 바디 | 기존 결과 반환 안 함 — 개발 오류 |
| 422 | `CONSENT_ABSENT` | 대상 학생 동의 없음(생성류 요청 자체가 오면 안 됨 — 백엔드 선차단이 1차) | "동의가 필요한 기능이에요" |
| 429 | `RATE_LIMITED` | 순간 요청 폭주(할당과 무관한 기술적 제한) | 백엔드 재시도(backoff) |
| 500 | `INTERNAL` | 미분류 서버 오류 | 재시도 1회 후 폴백 |
| 503 | `LLM_UPSTREAM_DOWN` | LLM 벤더 장애 | **폴백 규약**: 감지=전일 브리핑 유지+배지 · 초안="잠시 후 다시" |
| 504 | `TIMEOUT` | 동기 10s / 비동기 총 5분 초과 | 비동기는 status=failed로 수렴 |

> **[PART_B 편입 요청 · B 규약 확정/정본 미편입] HTTP 충돌 코드 1종 추가:** `409 REVISION_CONFLICT` — 문항 refine의 새 멱등키 요청에서 `base_revision_no`가 최신과 다르거나 같은 문항의 refine이 이미 진행 중인 경우다. 같은 키+다른 바디의 정본 코드 `409 IDEMPOTENCY_CONFLICT`와 의미가 다르므로 별도 코드가 필요하다. B 상세 사유는 `stale_base_revision`과 `revision_in_progress`이며, 멱등 조회를 먼저 수행해 같은 키+같은 바디는 기존 상태·결과를 200으로 재반환한다. **정본 표 편입과 detail 공개 범위를 A가 확인해 달라.** B 규칙 원본은 `part_b/07_refine_policy.md` §6이다.

> **[PART_B 크로스체킹 요청 · 미확정] 동의 오류 경계:** 현재 404 `NOT_FOUND`의 “동의 없는 학생 참조”와 422 `CONSENT_ABSENT`의 적용 경계가 겹친다. **제안 해결안:** 존재 여부를 숨겨야 하는 조회·불투명 참조는 404, 존재가 이미 확인된 생성 명령의 동의 선조건 위반은 422로 구분한다. A·백엔드가 보안 의도와 맞는지 확인해 달라.

> **[PART_B 크로스체킹 요청 · 미확정] 요청 계약 위반 범위:** `INVALID_SCHEMA` 설명은 요청 바디로 한정돼 있지만 현재 HTTP 경계는 필수 헤더 누락에도 같은 코드를 사용한다. **제안 해결안:** 헤더·JSON·body를 포함한 “요청 계약 위반”으로 정본 의미를 넓히거나 헤더용 코드를 분리하고, 선택한 범위를 공통 검증 핸들러 테스트로 고정한다. A·B가 확인해 달라.

> **쿼터 소진(`QUOTA_EXCEEDED`류)은 백엔드 선차단이라 이 사전에 없다(7/15 · BE-4).** 할당 차단·카운트·잔여 표시는 전부 백엔드 Billing 소유라 AI 레이어에 도달하지 않는다 — `RATE_LIMITED`(순간 폭주)는 할당과 무관한 기술적 제한이라 유지한다.

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

공통·[PART_A] 차단 사유는 `blocked_reason: evidence_missing | comparison_exposure | tone_violation | pii_exposure | out_of_scope`다. 문구는 `part_a/06_refine_policy.md` §4 표가 원본이다(여기 중복 정의하지 않음).

[PART_B] 문항 refine은 `answer_integrity | banned_topic | prompt_injection`을 추가로 사용한다. 규칙 원본은 `part_b/07_refine_policy.md` §3이다.

| [PART_B] blocked_reason | 의미 |
| --- | --- |
| `answer_integrity` | 정답 유일성·정답 구조를 깨뜨리는 수정 지시 |
| `banned_topic` | 금지 소재를 사용하라는 수정 지시 |
| `prompt_injection` | 기존 규칙이나 검증 절차를 무시·우회하라는 공격 지시 |

### 2.3 감지 (`/v1/detect` 응답 내)

| 필드 | 뜻 | 문구 |
| --- | --- | --- |
| `rules_skipped[]` | 규칙별 미적용 사유 — `{rule_id: "R4", reason: "duration_missing", students: 5}` (형태는 09 §3 · `contracts/detection.py` `RuleSkipped`와 동일) | 강사 노출 없음 — 운영 대시보드만 |
| `shadow: true` | 섀도 모드 실행분 | 브리핑 미발송 |

> **(7/16) `observed_only[]` 제거.** "관찰 중"(재원 14일 미만) 표시는 백엔드가 `enrolled_at`으로 자체 계산한다(명세 `part_a/09_detect_spec.md` §3 관찰 중 처리) — AI 응답은 학생 목록을 돌려주지 않고 `stats.excluded_under_2w` 숫자만 낸다.

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

> **[PART_B 편입 요청 · B 분류 확정/정본 미편입] 문항 생성 결과 어휘는 모두 같은 `status` 필드가 아니다.** B 계약 기준으로 `ProblemSetStatus.status`의 `partial_success`, `ProblemItemStatus.items[].status`의 `needs_review`·`verification_unavailable`, `ProblemFailureReason.items[].failure_reason` 및 `dropped_reasons[]`의 `generation_exhausted`·`source_unverified`로 분류된다. 모두 HTTP 에러가 아니라 성공 응답의 `data` 안에 있지만 필드별 enum이 다르므로, 정본 편입 시 이 세 범주를 나눠 기록해 달라.

## 3. LLM_CALL.outcome (내부 관측 — API 미노출)

`ok | parse_fail | field_missing | bad_ref(근거 ID 실존 실패) | timeout | provider_error | redaction_blocked(전송 전 차단 — 마스킹 정의서 §3 fail-closed)`. 재시도 정책: parse_fail·field_missing은 블록 단위 ≤3회, bad_ref는 즉시 해당 문장 폐기(재시도 무의미 — 환각), redaction_blocked는 재시도 금지+알럿.

## 4. 도메인 예외 ↔ HTTP 매핑 (runtime/errors 구현 지침)

```
DomainException (base)
├─ ConsentAbsent          → 422 CONSENT_ABSENT
├─ SnapshotInvalid        → 400 INVALID_SCHEMA
├─ GateRejected           → 200 + status (에러로 승격 금지!)
├─ EvidenceUnresolvable   → 200 + 블록 empty_reason=bad_ref
├─ LlmUnavailable         → 503 LLM_UPSTREAM_DOWN
└─ RedactionUncertain     → 500 INTERNAL (원문 노출 위험 — 상세 사유 응답에 미포함)
```

> **[PART_B 크로스체킹 요청 · 미확정 — 예외 트리]** 실제 공통 계약의 `LlmTimeout`과 runtime의 `IdempotencyConflict`가 위 트리에 없고, `contracts.llm.LlmUnavailable`과 runtime 예외도 서로 다른 계층이다. **제안 해결안:** canonical 예외→HTTP adapter 한 곳을 정한 뒤 §4 트리에 `IdempotencyConflict`·`LlmTimeout`을 포함하고 HTTP 통합 테스트로 409·503·504를 고정한다. A·B가 확인해 달라.

> `QuotaExceeded`는 이 트리에 없다 — 쿼터 차단은 백엔드 Billing 선집행이라 AI 도메인 예외로 올라오지 않는다(7/15 · BE-4).

**불변식:** `GateRejected`를 5xx로 올리는 코드는 리뷰 반려 — "게이트 거부 = 정상"이 이 사전의 존재 이유.

## 5-0. (7/15 — BE-6) 이 문서의 §6이 폴백 문구의 원본이다 — A 작성, 프론트는 검수만

## 5. 문구 스타일 가이드 (백엔드/프론트 전달용)

- 강사向 문구는 실패 귀책을 학생·강사에게 돌리지 않는다("데이터가 부족해요" ○ / "학생이 안 풀었어요" ×).
- '정상 상태' 문구에는 **다음 행동**을 항상 포함("다음 달부터 가능" · "직접 채워주세요").
- 쿼터 문구(백엔드 소유 — 참고용)는 리셋 시점 명시("내일 자정 이후" / "8월 1일 초기화") + 상위 플랜 안내는 monthly scope에만.

## 6. AI 장애 폴백 문구표 (BE-6 — A 초안 · 프론트 검수 대상)

원칙: 귀책을 사용자에게 돌리지 않고, 데이터가 사라진 게 아님을 안심시키고, 다음 행동을 알려준다.

| 상황 | 화면 위치 | 문구 초안 |
| --- | --- | --- |
| 야간 감지 실패(AI 다운) | 아침 브리핑 상단 배지 | "어젯밤 분석이 지연됐어요 — 어제 브리핑을 보여드리고 있어요. 오늘 밤 자동으로 다시 분석해요." |
| 초안 생성 실패(failed) | 문의 상세 | "초안 생성에 실패했어요. [다시 시도] — 문의 내용은 안전하게 저장돼 있어요." |
| 블록 일부 비움(empty_reason) | 초안 해당 섹션 | "이 부분은 자동 작성하지 못했어요 — 직접 채워주세요." |
| 데이터 부족(rejected_insufficient) | 문의 상세 | "○○ 학생은 아직 데이터를 모으는 중이에요(등원 2주 후부터 초안을 만들 수 있어요)." |
| refine 지시 차단(blocked_reason) | 다듬기 채팅 말풍선 | §2.2 표(핑퐁 정책서 §4)의 사유별 문구 그대로 |
| 상담팩 중단(paused) | 작업 카드 | "13/22명까지 완료했어요 — 완료된 초안은 지금 바로 볼 수 있어요. [이어서 생성]" |
| Import 판독 실패(failed) | 업로드 화면 | "파일을 읽지 못했어요. 파일이 열리는지 확인 후 다시 올려주세요(xlsx·csv 지원)." |
| Import 일부 행 실패 | 결과 요약 | "1,240행 중 1,228행을 옮겼어요 — 실패한 12행은 목록에서 확인하고 직접 수정할 수 있어요." |
| 태그 제안 실패 | 과제 입력 폼 | (문구 없음 — 제안 없이 수동 선택 폼만 표시, 기능 실패를 광고하지 않는다) |
| 분류 실패 | 인박스 | (문구 없음 — 정렬 없이 시간순 표시) |

**노출 금지 표현:** "서버 오류", "AI가 다운", "LLM", 에러 코드 원문 — 내부 용어는 로그에만.
