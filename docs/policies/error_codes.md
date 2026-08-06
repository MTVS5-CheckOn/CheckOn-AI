# [체크온] 에러·상태 코드 사전 v0 — API 계약 부록 C **[확정 7/21 — 코드 사전 정본, 99 #12]**

> **핵심 구분:** "AI가 못 한 것"(에러)과 "AI가 안 하기로 판단한 것"(**정상 상태**)을 코드 레벨에서 분리한다. 후자는 200으로 내려간다 — 게이트 거부는 제품이 약속대로 동작한 것이지 장애가 아니다. 백엔드 표시 문구까지 이 문서로 고정해 UI 재협의를 없앤다.

---

## 1. HTTP 레이어 (전 엔드포인트 공통)

| HTTP | 코드 | 뜻 | 백엔드 처리 |
| --- | --- | --- | --- |
| 400 | `INVALID_SCHEMA` | **요청 계약 위반** — 바디 스키마 위반 + 필수 헤더 누락 포함(detail로 필드 경로·헤더명 구분) | 개발 오류 — 로그·알럿, 강사 노출 없음 |
| 401/403 | `UNAUTHORIZED` / `TENANT_MISMATCH` | 내부 토큰 불량 / tenant 불일치 | 보안 알럿 |
| 404 | `NOT_FOUND` | draft_id·job_id 등 부재 · **조회·불투명 참조에서 동의 없는 학생**(존재 자체를 숨김) `[백엔드 확인 대기]` | |
| 409 | `IDEMPOTENCY_CONFLICT` | 같은 Idempotency-Key로 다른 바디 | 기존 결과 반환 안 함 — 개발 오류 |
| 409 | `REVISION_CONFLICT` | 문항 refine의 `base_revision_no` 불일치·진행 중 — detail.reason: `stale_base_revision` \| `revision_in_progress` (내부 API라 reason 노출 허용) | B 규칙 원본 `part_b/07_refine_policy.md` §6 |
| 422 | `CONSENT_ABSENT` | **존재가 확인된 생성 명령의 동의 선조건 위반**(백엔드 선차단이 1차) `[백엔드 확인 대기]` | "동의가 필요한 기능이에요" |
| 429 | `RATE_LIMITED` | 순간 요청 폭주(할당과 무관한 기술적 제한) | 백엔드 재시도(backoff) |
| 500 | `INTERNAL` | 미분류 서버 오류 | 재시도 1회 후 폴백 |
| 503 | `LLM_UPSTREAM_DOWN` | LLM 벤더 장애 | **폴백 규약**: 감지=전일 브리핑 유지+배지 · 초안="잠시 후 다시" |
| 504 | `TIMEOUT` | 동기 10s / 비동기 총 5분 초과 | 비동기는 복구 불가 시 `WorkerJob.phase=failed`로 수렴 |

> **[PART_B 편입 요청 · B 규약 확정/정본 미편입] HTTP 충돌 코드 1종 추가:** `409 REVISION_CONFLICT` — 문항 refine의 새 멱등키 요청에서 `base_revision_no`가 최신과 다르거나 같은 문항의 refine이 이미 진행 중인 경우다. 같은 키+다른 바디의 정본 코드 `409 IDEMPOTENCY_CONFLICT`와 의미가 다르므로 별도 코드가 필요하다. B 상세 사유는 `stale_base_revision`과 `revision_in_progress`이며, 멱등 조회를 먼저 수행해 같은 키+같은 바디는 기존 상태·결과를 200으로 재반환한다. **정본 표 편입과 detail 공개 범위를 A가 확인해 달라.** B 규칙 원본은 `part_b/07_refine_policy.md` §6이다.
>
> ✅ **A 판정(7/22):** **정본 편입 완료** — §1 표에 `409 REVISION_CONFLICT` 추가. detail 공개 범위는 이 API가 내부 백엔드 전용(§2.1)이므로 `detail.reason`(`stale_base_revision` \| `revision_in_progress`) **노출 허용**. 멱등 선조회 규약(같은 키+같은 바디=200 재반환)도 IDEMPOTENCY_CONFLICT와 동일하게 적용.

> **[PART_B 크로스체킹 요청 · 미확정] 동의 오류 경계:** 현재 404 `NOT_FOUND`의 “동의 없는 학생 참조”와 422 `CONSENT_ABSENT`의 적용 경계가 겹친다. **제안 해결안:** 존재 여부를 숨겨야 하는 조회·불투명 참조는 404, 존재가 이미 확인된 생성 명령의 동의 선조건 위반은 422로 구분한다. A·백엔드가 보안 의도와 맞는지 확인해 달라.
>
> ✅ **A 판정(7/22):** B 구분안 **채택** — 조회·불투명 참조에서 동의 없는 학생 = `404`(존재 은닉), 존재가 확인된 생성 명령의 동의 선조건 위반 = `422`. §1 표에 반영. 단 보안 의도 최종 확인은 **`[백엔드 확인 대기]`**(vault·존재 은닉 정책 소유가 백엔드).

> **[PART_B 크로스체킹 요청 · 미확정] 요청 계약 위반 범위:** `INVALID_SCHEMA` 설명은 요청 바디로 한정돼 있지만 현재 HTTP 경계는 필수 헤더 누락에도 같은 코드를 사용한다. **제안 해결안:** 헤더·JSON·body를 포함한 “요청 계약 위반”으로 정본 의미를 넓히거나 헤더용 코드를 분리하고, 선택한 범위를 공통 검증 핸들러 테스트로 고정한다. A·B가 확인해 달라.
>
> ✅ **A 판정(7/22):** 의미를 **"요청 계약 위반"으로 확장**(헤더 누락·JSON 파싱 실패·바디 스키마 모두 포함, `detail`로 구분) — 현 구현과 일치. 헤더용 코드 분리는 하지 않는다(하나의 400으로 수렴, detail이 어느 헤더/필드인지 명시). §1 표 반영.

> **쿼터 소진(`QUOTA_EXCEEDED`류)은 백엔드 선차단이라 이 사전에 없다(7/15 · BE-4).** 할당 차단·카운트·잔여 표시는 전부 백엔드 Billing 소유라 AI 레이어에 도달하지 않는다 — `RATE_LIMITED`(순간 폭주)는 할당과 무관한 기술적 제한이라 유지한다.

## 2. 산출물 상태 코드 — 200 안의 `status` (에러 아님!)

### 2.1 Draft (`GET /v1/drafts/{id}`)

| status | 뜻 | `status_reason` 예 | 백엔드 표시 문구 |
| --- | --- | --- | --- |
| `queued` / `generating` | 대기/생성 중 | — | 스피너 |
| `generated` | 정상 — 게이트 전체 통과 | — | 초안 표시 |
| `template_only` | **정상** — 데이터 무관 문의(시간표 등)라 일반 템플릿만 | `no_data_topic` | "일반 안내 초안이에요 — 학습 데이터는 사용하지 않았어요" |
| `rejected_insufficient` | **정상** — 데이터 부족으로 생성 안 함(재원 2주 미만 등) | `data_lt_2weeks` | "○○ 학생은 아직 데이터를 모으는 중이에요(다음 달부터 가능)" |
| `failed` | 진짜 실패 | `llm_failed` `gate_exhausted` `llm_timeout` `parse_fail_exhausted` | "생성에 실패했어요 — 다시 시도" |

**`failed`의 `status_reason` — counsel 와이어 5종과의 대응(7/31 · 인박스 계약 v1 §4-③ · 8/x `template_only` 승격).** 계약은 초안 판정을 `generated · template_only · rejected_insufficient · llm_failed · gate_exhausted` 5종으로 싣는다. 뒤의 둘은 **판정이 아니라 사유**라서 이 표의 `status`가 아니라 `failed`의 `status_reason`에 둔다 — 계약 자신이 둘 다 화면 `failed`("다시 시도")로 매핑하므로 화면이 구분하지 않는 것을 판정 축에 섞지 않는다.

| 계약 `draft_status` | AI 내부(`DraftStatus`) | `status_reason` |
| --- | --- | --- |
| `generated` | `generated` | — |
| `template_only` | `template_only` | `no_data_topic` |
| `rejected_insufficient` | `rejected_insufficient` | 부족 사유(`context_missing` 등) |
| `llm_failed` | `failed` | `llm_failed` |
| `gate_exhausted` | `failed` | `gate_exhausted` |

와이어 변환은 **라우터의 결정론 함수 한 곳**이 한다(내부 도메인 ≠ 와이어 표현). 내부 `DraftStatus` 전수가 그 파생표에 등재됐는지 CI가 대조하며, 미등재 값은 크래시가 아니라 `failed` + `status_reason="unmapped:{값}"`으로 **정직하게** 나간다. 어휘가 계약·코드·문서 3갈래인 사실은 99 D ㊱에 등록돼 있다.

🔴 **`template_only` 승격(8/x).** 종전에는 계약에 이 값이 없어 내부 `TEMPLATE_ONLY`를 **`generated`로 강등**했는데, 그 조합은 **불변식 2 검증**(`CounselDraftResult`의 `citations` ≥1)에 걸린다 — 데이터 무관 문의는 근거가 0건이기 때문이다. **그 값을 만드는 경로가 없어서**(죽은 값) 지금까지 안 터졌을 뿐이고, 라우터 분기를 넣는 순간 500이 났을 잠복 결함이었다. 와이어 5종 승격으로 해소했다.

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

> **상태명 정본:** 작업 상태(status)는 `contracts/imports.ImportStatus`(= `10_import_spec §2` 상태기계 · `04 §3.8`)를 투영한다 — 진행형(`profiling`·`inferring`·`probing`)·종단(`done`)을 쓴다. `transforming`은 없다 — 전체 행 변환이 백엔드 소유가 됐다(2026-07-30 · 10 §4). **`blocked`도 없다** — 필수 여부 판단·확정 차단이 백엔드 소유가 됐다(2026-07-30 백엔드 확정 · 10 §1.3·§3.4). `needs_review`·`unmapped`은 작업 상태가 아니라 **컬럼(매핑) 플래그**로 `mapping_preview`에 실린다(10 §1.2·§3.4).

| status | 뜻 | 문구 |
| --- | --- | --- |
| `profiling` → `preview_ready` | 매핑 추론 완료 — 강사 확인 대기 | 미리보기 화면 |
| `needs_review` (컬럼 플래그) | confidence 낮은 매핑 — **숨기지 않고 노출** | "이 열은 확신이 낮아요 — 확인해 주세요" |
| `unmapped` (컬럼 플래그) | 매핑 불가 — '모름' 정직 표기 | "무엇인지 알 수 없어 건너뛰어요" |
| `done` | 확정 완료 — 강사가 확정한 매핑 spec 저장(전체 행 변환·행 리포트는 백엔드 소유 — 10 §4) | 확정 요약 |
| `failed` | 파일 손상·형식 불가 | "파일을 읽지 못했어요" |

### 2.5 에이전트 (`GET /v1/agents/{run_id}`)

공통 실행 상태 필드는 `phase`이며 정본은 `WorkerJob` 계약을 투영한 영속 `AGENT_RUN`이다. `done`은 사용하지 않고 성공 종료를 `succeeded`로 통일한다.

| phase | 의미 |
| --- | --- |
| `queued` | 영속 큐에서 실행 대기 |
| `leased` | 실행자가 제한 시간 소유권을 획득한 내부 상태 |
| `running` | 워커 실행 중 — `progress`·`checkpoint_ref` 조회 가능 |
| `paused` | 체크포인트에서 안전하게 중단되어 resume 가능 |
| `succeeded` | 계약에 맞는 도메인 결과와 `result_ref` 저장 완료 |
| `failed` | 복구 불가능한 실행 장애로 유효 결과를 확정하지 못함 |
| `cancelled` | 취소 요청이 안전한 체크포인트 경계에서 반영됨 |

`succeeded`·`failed`·`cancelled`는 terminal이다. `paused`는 실패가 아니다. 부분 수렴과 실행 실패를 다음처럼 구분한다.

- 상담팩에서 일부 학생이 `skipped_insufficient`·도메인 `failed`여도 완료 draft와 요약을 저장했다면 Job은 `succeeded`.
- 매핑 조사에서 상한 5회 뒤 일부 컬럼이 `unresolved`·`needs_review`여도 명시적 결과를 저장했다면 Job은 `succeeded`.
- 문제생성의 `rejected_insufficient`, 세트 `partial_success`·도메인 `failed`, 문항 `verification_unavailable`·`dropped`도 유효한 결과 계약이면 Job은 `succeeded`.
- 워커 장애·체크포인트 손상·결과 저장 실패처럼 결과 계약 자체를 확정할 수 없을 때만 Job은 `failed`. 완료된 하위 산출물은 보존한다.

**잡 단위 `error_code` 어휘(내부 — 화면 미노출).** 학생·문항 단위 `fail_reason`과 **문자열이 겹치지 않게** 접두를 붙인다 — 대시보드가 문자열로 집계하면 잡 장애와 도메인 정상 스킵이 섞여 장애 오판이 된다.

| error_code | 워커 | 의미 |
| --- | --- | --- |
| `worker_recovery_exhausted` | 공통 | lease 만료 회수가 `max_recovery_attempts`에 도달 |
| `context_bundle_missing` | counsel_pack | `payload_ref`가 해소되지 않아 결과 계약을 확정할 수 없다 |
| `context_hash_mismatch` | counsel_pack | 재개 시 재역참조 해시 ≠ 체크포인트 `context_hash`(손상 — 불변식 ④) |
| `tenant_mismatch` | counsel_pack | 묶음의 tenant가 잡의 tenant와 다르다(격리 위반) |
| `worker_internal_error` | counsel_pack | 미분류 예외 — running 방치 대신 즉시 수렴 |


**counsel_pack 강조점 드롭 사유(내부 관측 — `phase`와 무관).** plan(LLM) 불량 출력은 **게이트 거부**이지 잡 실패가 아니다(불변식 4). 두 사유를 구분하는 이유는 대응이 다르기 때문이다 — 전자는 plan 프롬프트 문제, 후자는 LLM 날조다.

| 사유 | 의미 |
| --- | --- |
| `missing_record_id` | 강조점에 근거 `record_id` 표기가 없다 |
| `unknown_record_id` | 인용한 `record_id`가 컨텍스트에 실존하지 않는다(날조·오타) |

plan LLM 실패·전량 드롭이면 **강조점 없이 초안 생성을 계속**하고 Job은 정상 수렴한다. 이 실패는 `paused` 서킷 카운터에 넣지 않는다 — 서킷은 **학생 단위** write 실패 기준이다(§1.3).



lease 만료 때만 `recovery_count`를 증가시키며, 설정된 `max_recovery_attempts`(기본 3)에 도달하면 `phase=failed`, 내부 `error_code=worker_recovery_exhausted`로 수렴한다. 정상 수동 pause/resume은 이 장애 복구 예산을 소모하지 않는다. 내부 코드는 사용자 화면에 직접 노출하지 않는다.

### 2.6 문항 생성·refine 결과 (B — `POST /v1/problems` 등, 성공 200 안의 필드)

B 계약 기준. 모두 HTTP 에러나 `WorkerJob.phase`가 아니라 성공 응답 `data` 안의 도메인 값이며, **필드별로 enum이 다르다**(A 판정 7/22 — 범주를 나눠 기록).

| 범주 | 필드 | 값 | 뜻 |
| --- | --- | --- | --- |
| 생성 결과 | `RejectedInsufficientOutcome.status` | `rejected_insufficient` | 자동 개인화 데이터 부족으로 생성 전 정상 종료 |
| 세트 | `ProblemSetStatus.status` | `queued` · `generating` | 문제생성 도메인의 진행 투영 — 실행 phase 정본이 아님 |
| 세트 | `ProblemSetStatus.status` | `generated` | 요청 문항을 모두 검증 완료 |
| 세트 | `ProblemSetStatus.status` | `partial_success` | 일부 문항만 검증 통과 — 세트는 유효, 실패·미처리 수량 명시 |
| 세트 | `ProblemSetStatus.status` | `failed` | 성공 문항이 없는 도메인 결과 — 결과가 저장되었으면 Job `succeeded` |
| 문항 | `items[].status` | `verified` | 검증 통과 |
| 문항 | `items[].status` | `needs_review` | 검증 신뢰 낮음 — 강사 확인 필요(숨기지 않고 노출) |
| 문항 | `items[].status` | `verification_unavailable` | 검증기(교차 풀이 등) 일시 불가 — 재시도 대기 |
| 문항 | `items[].status` | `dropped` | 문항 처리 실패를 사유와 함께 명시 |
| 실패 사유 | `items[].failure_reason` · `dropped_reasons[]` | `generation_exhausted` | 문항당 생성 시도 소진(≤3회) |
| 실패 사유 | 〃 | `source_unverified` | 근거 원천 대조 실패 — 발행 차단 |
| 실패 사유 | 〃 | `banned_topic` | 금지 소재 판정으로 문항 폐기 |
| 검토 사유 | `items[].review_reason` | `low_confidence` · `area_mismatch` · `t3_literature` · `diagnostic_purpose` · `manual_target_first` · `difficulty_band_mismatch` | `needs_review` 배지 사유 — 폐기 사유인 `failure_reason`과 분리 |
| 난이도 밴드 | `items[].difficulty_band` | `low` · `medium` · `high` | 응답의 표시·필터용 밴드. 한글 라벨(하·중·상)은 FE 소유 |

> B 규칙 원본은 `part_b/05_problem_generation.md` §6 · `part_b/06_quality_gates.md` · `07_refine_policy.md`. 이 표는 정본 편입만이며 값 정의는 B 소유.

### 2.7 상태 표기 규약 — "판정 불리언 + 사유 코드" (8/5 정립)

AI가 **안 하기로 판단한 것**은 200으로 내려간다(§2 서두). 그 표기는 전 엔드포인트가 **같은 모양**을 쓴다.

| 엔드포인트 | 판정 | 사유 |
| --- | --- | --- |
| `POST /v1/counsel/drafts/{job_id}/refine` | `applied: bool` | `blocked_reason`(`BlockedReason` 8종 enum) |
| `POST /v1/classify` | `classified: bool` | `fallback_reason`(`ClassifyFallbackReason` **2종** enum) |
| `GET /v1/counsel/drafts/{job_id}` | `draft_status`(5종 enum) | `status_reason` |

⚠ 이 표의 "5종"은 8/5(P3) 시점에 **실제로는 4종이던 것을 잘못 적은 값**이었고, `template_only` 승격(8/x)으로 **사실이 됐다** — 다음 사람이 "왜 5종이지?"에서 멈추지 않게 남긴다.

**규칙 3가지**

1. **사유는 닫힌 집합(enum)이다.** 자유 문자열은 오타·미등재 값을 조용히 흘린다. `fallback_reason`이 `str`이었던 것을 8/5에 enum으로 봉쇄했다.
2. **판정과 사유는 짝이다.** 정상 판정에 사유가 실리거나 그 반대는 **타입 차원에서 막는다**(`model_validator`). 어긋나면 BE가 두 필드로 상태를 추측하게 된다.
3. **표시 문구는 AI가 주지 않는다.** 문구는 이 문서의 "백엔드 표시 문구" 열과 `part_a/06` §4 표가 원본이고 **BE가 매핑**한다 — 문구 수정이 AI 배포에 묶이지 않게 한다. 8/5에 `RefineResponse.message`를 제거해 이 규칙을 전 엔드포인트에 맞췄다(종전에 refine만 예외였던 것은 역사적 우연이다).

⚠ 이름이 엔드포인트마다 다른 것(`applied` / `classified`)은 **의도적으로 통일하지 않았다** — 통일하면 계약·apidog·BE를 동시에 고쳐야 하는데 얻는 게 가독성뿐이다. **축이 같다는 사실은 이 표가 보증한다.**

⚠ `data.code`/`data.message` 형태로 바꾸지 않은 이유도 같은 결이다 — 실질이 이미 이 구조이고, `data.code`는 HTTP 층 `error.code`(§1)와 **이름이 겹쳐 축 혼선**을 만든다.

## 3. LLM_CALL.outcome (내부 관측 — API 미노출)

`ok | parse_fail | field_missing | bad_ref(근거 ID 실존 실패) | timeout | provider_error | redaction_blocked(전송 전 차단 — 마스킹 정의서 §3 fail-closed)`. 재시도 정책: parse_fail·field_missing은 블록 단위 ≤3회, bad_ref는 즉시 해당 문장 폐기(재시도 무의미 — 환각), redaction_blocked는 재시도 금지+알럿.

## 4. 도메인 예외 ↔ HTTP 매핑 (canonical adapter = `runtime/errors.py`)

canonical 예외→HTTP adapter는 **`runtime/errors.py` 한 곳**에 둔다(공용). 아래 트리가 정본.

```
DomainException (base)
├─ ConsentAbsent          → 422 CONSENT_ABSENT
├─ SnapshotInvalid        → 400 INVALID_SCHEMA (헤더 누락·JSON·바디 포함)
├─ IdempotencyConflict    → 409 IDEMPOTENCY_CONFLICT
├─ GateRejected           → 200 + status (에러로 승격 금지!)
├─ EvidenceUnresolvable   → 200 + 블록 empty_reason=bad_ref
├─ LlmUpstreamDown        → 503 LLM_UPSTREAM_DOWN
├─ LlmUpstreamTimeout     → 504 TIMEOUT
└─ RedactionUncertain     → 500 INTERNAL (원문 노출 위험 — 상세 사유 응답에 미포함)
```

🔴 **(8/6) 트리의 이름은 runtime 매핑 예외다.** `contracts.llm`의 `LlmUnavailable`·
`LlmTimeout`은 이 경계가 **받아 변환하는 입력**이며(아래 7/22 A 판정), **이름을 갈라 둔
이유가 그것이다.** 종전에는 트리 쪽도 `LlmUnavailable`이라 `contracts.llm`과 동명이었고 —
받는 쪽과 받히는 쪽이 같은 이름이면 그건 경계가 아니다. 변환은 순수 함수
`runtime.errors.domain_error_for(exc)` 한 곳이 한다.

| 받는 예외(`contracts.llm`) | 내는 예외(runtime) | HTTP |
| --- | --- | --- |
| `LlmTimeout` | `LlmUpstreamTimeout` | 504 TIMEOUT |
| `LlmUnavailable` | `LlmUpstreamDown` | 503 LLM_UPSTREAM_DOWN |
| `ParseFailed`·`FieldMissing` | `DomainException` | 500 INTERNAL |
| 그 밖의 `LlmError`(4xx) | `DomainException` | 500 INTERNAL |

⚠ **plain `LlmError`(4xx)를 503으로 뭉개지 않는다** — 벤더가 살아 있는데 우리 요청이 틀린
것이라, 503의 폴백 문구("잠시 후 다시")가 거짓이 된다. `openai_compat`이 **429만**
`LlmUnavailable`로 승격하고 나머지 4xx를 plain으로 두는 것이 이 구분이다.
⚠ 이 경계가 받는 시점에는 **재시도 예산이 이미 소진**돼 있다 — 게이트웨이가
`reraise=True`로 재시도를 소진한 뒤 원 예외를 올린다. "아무도 못 바꾸고 기다릴 뿐"의
조건이 구조적으로 충족되는 근거다.

> **[PART_B 크로스체킹 요청 · 미확정 — 예외 트리]** 실제 공통 계약의 `LlmTimeout`과 runtime의 `IdempotencyConflict`가 위 트리에 없고, `contracts.llm.LlmUnavailable`과 runtime 예외도 서로 다른 계층이다. **제안 해결안:** canonical 예외→HTTP adapter 한 곳을 정한 뒤 §4 트리에 `IdempotencyConflict`·`LlmTimeout`을 포함하고 HTTP 통합 테스트로 409·503·504를 고정한다. A·B가 확인해 달라.
>
> ✅ **A 판정(7/22):** 수용 — adapter 위치 `runtime/errors.py` 확정, 트리에 `IdempotencyConflict`→409·`LlmTimeout`→504 편입(위 반영). `contracts.llm` 예외(LlmUnavailable·LlmTimeout)는 runtime adapter가 받아 HTTP로 변환하는 단일 경계로 정리. 감지 경로엔 LLM 예외가 없으므로(결정론) 503·504는 다른 capability 편입 시 통합 테스트로 고정.

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
| ~~Import 일부 행 실패~~ **백엔드 소유로 이관(2026-07-30)** | 결과 요약(백엔드 화면) | **최종 문구는 기획·프론트와 백엔드가 확정한다.** A 초안(참고자료로 전달): "1,240행 중 1,228행을 옮겼어요 — 실패한 12행은 목록에서 확인하고 직접 수정할 수 있어요." |
| 태그 제안 실패 | 과제 입력 폼 | (문구 없음 — 제안 없이 수동 선택 폼만 표시, 기능 실패를 광고하지 않는다) |
| 분류 실패 | 인박스 | (문구 없음 — 정렬 없이 시간순 표시) |

**`POST /v1/classify` — `classified: false`의 두 사유(8/5 신설).** 분류 실패는 **500이 아니라 200**이다(서두 원칙: "AI가 안 하기로 판단한 것은 정상 상태"). `etc`를 확신 있는 판정처럼 내보내지 않고 `classified: false` + `confidence` 0.0으로 정직하게 표기한다.

| `fallback_reason` | 언제 | BE 처리 |
| --- | --- | --- |
| `tripwire_blocked` | 전송 직전 트립와이어가 프롬프트에서 **잔여 흔적**을 발견했다 — "안 가려진 게 남았다"는 신호다 | **정렬 미적용 · 시간순 표시**(위 행과 동일) |
| `parse_exhausted` | LLM 출력이 enum 강제 스키마를 못 채워 재시도 상한(2회)을 소진했다 | 〃 |

**`POST /v1/confirmations` — 확정 회신(8/5 · P2-c).**

| 상황 | 코드 | 사유 | 뜻 |
| --- | --- | --- | --- |
| `kind`가 `tag`·`label`·`draft_edit` | **400** | `kind_not_implemented` | 제안 **생성기가 없다** — 확정할 대상이 존재하지 않는다. 받아서 조용히 버리면 BE가 "저장됐다"고 오해하므로 정직하게 거절한다 |
| `kind=classification` + `action=rejected` | **400** | `action_not_supported` | 3축은 값이 반드시 있어야 하는 축이라 "거절"이 정의되지 않는다 |
| `action=corrected`인데 정정할 축이 0건 | **400** | `corrected_value_missing` | `corrected_value`가 없거나 3축이 전부 null이다. 🔴 **종전에는 200 `accepted:true`로 나갔다** — `reviewed_at`만 찍히고 그 행은 규약 ①에 의해 **이후 재예측이 영구 차단**된다("검토함"으로 굳는다). BE는 정정이 저장됐다고 믿는다 |
| `action=confirmed`인데 `corrected_value`가 실림 | **400** | `corrected_value_not_allowed` | 값이 통째로 버려지던 조합이다. 버릴 거면 받지 않는다 — 위 두 행과 같은 원칙 |
| 대상 분류 없음 | **404** | — | 그 `inquiry_ref`로 분류한 적이 없거나, **폴백이라 적재되지 않았다**(`classified=false`는 행을 만들지 않는다) |

🔴 **정정 되돌리기(8/6).** 이미 정정된 축에 **예측값이 다시 오면 취소**로 보고 `corrected_*`를 NULL로 되돌린다. 종전에는 예측하고만 비교해서 이 회신이 "같은 값 정정"으로 무시됐고, 강사가 실수를 알아채고 원래 값으로 회신해도 정정이 **영구히 남았다** — 재분류율이 과대계상된다. ⚠ 규약 "같은 값 정정은 정정이 아니다"는 그대로다(이전 정정이 없으면 여전히 무시). 두 규칙은 같은 방향이다 — 분자에서 가짜 정정을 뺀다. **응답은 바뀌지 않는다**(200 `accepted:true`).

⚠ **`redact()`의 `uncertain`은 폴백 사유가 아니다**(8/5 정정) — `⟪확인필요⟫`가 남아도 **가려진 텍스트로 분류를 계속한다**. `masking_redaction`:40이 "**소비자가** fail-closed 판단"이라 규정했고, 분류는 산출물을 만들지 않고 판정만 하므로 이름이 필요 없다. ⚠ **초안 생성 경로는 그대로 중단한다** — 거긴 학부모에게 나갈 문장을 만든다.

⚠ **LLM 장애는 폴백이 아니다** — `LlmUnavailable`·`LlmTimeout`은 **503**으로 올라간다(§4). "안 하기로 판단한 것"과 "못 한 것"을 같은 상태로 뭉개지 않는다.

**노출 금지 표현:** "서버 오류", "AI가 다운", "LLM", 에러 코드 원문 — 내부 용어는 로그에만.
