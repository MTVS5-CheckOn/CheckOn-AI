# 10. Import(스마트 데이터 이전) 확정 스펙 초안 — `/v1/imports` 상세 계약

> **정본 소스:** `04_api_contract.md §3.8`(3엔드포인트·상태기계·Open-3 스토리지 URL) · `07_standard_schema.md`(변환 목적지 = 표준 스키마) · `01_pipeline.md §3`(매핑 조사 에이전트) · `03_usecases.md I1~I3` · `policies/error_codes.md`(코드 사전 — §1·§2.4·§4·§6) · `policies/masking_redaction.md`(개인정보 이중 방어).
> **이 문서의 위치:** 04 §3.8을 **상세화**한다 — 새 설계를 만들지 않는다. 04와 어긋나는 결정이 필요하면 [백엔드 확인 대기]/[제안]으로 올리고, 표준 필드를 바꾸려면 07을 먼저 고친다(BE-7 3자 일치).
> **불변(CLAUDE.md §1):** LLM은 **매핑을 추론만** 한다 — 확정은 결정론. 억지 매핑 금지(모르면 `unmapped` 정직 표기). **AI의 LLM·저장소·로그는 실명 값을 보지 않는다**(마스킹 통과분만 — §5). 최종 관문은 항상 강사 확정(HITL).
> **범위(2026-07-30 확정):** AI는 **매핑 제안까지**다 — 전체 행 변환·행별 검증·집계·저장은 **백엔드 소유**(§4). AI 서버가 원본 파일을 읽는 것은 유지되므로 §5.2 가드레일은 완화 대상이 아니다.

작성 A · 리뷰 백엔드. 타겟: 수능 대비 고등 국어.

---

## 1. 3엔드포인트 상세 계약

공통: envelope는 `04 §2.2`(`data`/`error`/`meta.versions` — 실패도 `meta.versions` 항상), 에러 코드는 `error_codes §1`이 정본(여기서 중복 정의 안 함). 필수 헤더 `X-Tenant-Id`·`X-Request-Id`, **쓰기(`POST`·`confirm`)는 `Idempotency-Key`**. 비동기 규약: `202 + job_id`, 완료 통지는 **Kafka 이벤트**(7/15), `GET`은 상태 보조 조회, **작업 총 5분 초과 시 `failed`**(04 line 119).

`meta.versions`(Import 실행 관련 키만 non-null): `pipeline` · `prompt`(매핑 추론 프롬프트) · `schema`(표준 스키마 = `std-1`, 07 §5) · `contract` · `taxonomy`(매핑 목적지의 수능 6영역/유형 enum) · `graph`(매핑 조사 에이전트 그래프). `engine`·`threshold`·`verify_config`·`difficulty_calib` = null.

### 1.1 `POST /v1/imports` — 업로드 접수 (202)

| 방향 | 필드 | 타입 | 필수 | 설명 |
| --- | --- | --- | --- | --- |
| Req(body) | `source_url` | string(스토리지 URL) | ✅ | 강사가 올린 원본 파일 위치(Open-3 — 파일 바이트를 바디로 받지 않음). xlsx·csv. |
| Req(body) | `filename` | string | ✅ | 확장자 판별·에러 문구용. |
| Req(body) | `sheet_hint` | string | ⬜ | 강사가 시트를 지정하면 힌트(미지정 시 전 시트 프로파일링). |
| Req(header) | `Idempotency-Key` | string | ✅ | 같은 키+같은 바디 = 기존 `job_id` 200 재반환 · 다른 바디 = `409 IDEMPOTENCY_CONFLICT`. |
| Res(202) | `data.job_id` | uuid | | 이후 조회·확정의 키. |
| Res(202) | `data.status` | enum | | 접수 직후 = `profiling`(§2). |

- **실패:** `source_url` 다운로드 불가·파일 손상·미지원 형식 → 202로 접수 후 상태기계에서 `failed`로 수렴(§2·error_codes §2.4·§6 "파일을 읽지 못했어요"). 요청 계약 위반(헤더 누락·바디 스키마)만 동기 `400 INVALID_SCHEMA`.

### 1.2 `GET /v1/imports/{job_id}` — 상태·미리보기

| status | `data` 핵심 필드 |
| --- | --- |
| `profiling`·`inferring`·`probing` | `{job_id, status}` (+선택 `progress` — probing 루프 회차 `"2/5"`) |
| `preview_ready` | `{job_id, status, mapping_preview{…}}` — 아래 구조(04 §3.8 그대로) |
| `blocked` | `preview_ready`와 동형 + `mapping_preview.blocked=true`·`blocked_reason`(필수 필드 미매핑) — confirm 차단 |
| `done` | `{job_id, status, mapping_preview{…}}` — **강사가 확정한 매핑 spec**. 전체 행 변환 결과는 여기 실리지 않는다(백엔드 소유 — §4) |
| `failed` | `{job_id, status, status_reason}` — `file_unreadable`·`profiling_failed`·`timeout_5min` |

`mapping_preview`(preview_ready) — **04 §3.8과 1:1**:
```json
{
  "spec_version": 1,
  "reused": false,
  "columns": [
    { "source": "원생명", "target": "student_name", "confidence": 0.97 },
    { "source": "점수A", "target": "score", "confidence": 0.41,
      "needs_review": true, "probe_note": "유니크 값 8,7,10,9… → 10점 만점 추정" },
    { "source": "주소", "target": null,
      "unmapped_reason": "개인정보 필드 · 표준 목적지 없음 — 자동 이전 대상 아님" }
  ],
  "structure_notices": [
    { "sheet": "1학기", "kind": "duplicate_header", "column_index": 5, "header": "점수" },
    { "sheet": "1학기", "kind": "empty_header", "column_index": 8, "header": "" }
  ],
  "sample_rows": [ { "…": "변환 예시 5행 — 실명 포함 가능(§5 preview 경계)" } ],
  "blocked": false, "blocked_reason": null
}
```
- `target`은 **07 표준 필드명만**(07 §2·§3) 또는 `null`(unmapped). 자유 필드명 금지. **실명 표준 필드(`student_name`·`guardian_name`·`guardian_phone`)도 매핑 대상**이다 — 매핑 제안에는 실명 컬럼의 **목적지**만 담기고 AI는 값에 접근하지 않는다(§5).
- `needs_review`·`unmapped_reason`은 **숨기지 않고 노출**(error_codes §2.4).
- **`structure_notices[]` — 파일 구조 주의사항(백엔드 요청, 2026-07-30):** AI가 파일 구조를 어떻게 해석했는지 강사가 검토할 **참고정보**다. `kind` = `duplicate_header`(같은 헤더 2회 이상 — 매핑 대상 목록에서는 하나로 합쳐진다) · `empty_header`(이름 없는 컬럼) · `header_without_data`(헤더는 있으나 전량 결측). 항목마다 **`sheet`·`column_index`(1-based 원본 위치)·`header`** 가 실려 강사가 원본에서 바로 찾을 수 있다. **셀 값은 담지 않는다**(§5.2 가드레일 — 헤더 이름만).
- 백엔드가 요청한 미리보기 5종은 이 구조에 모두 있다: 매핑 제안(`columns[].target`) · 미매핑 컬럼(`target=null` + `unmapped_reason`) · 신뢰도(`confidence`) · `needs_review` · 파일 구조 주의사항(`structure_notices`).

### 1.3 `POST /v1/imports/{job_id}/confirm` — 강사 확정 → 변환

| 방향 | 필드 | 타입 | 필수 | 설명 |
| --- | --- | --- | --- | --- |
| Req(body) | `spec_overrides[]` | `{source_column, target_field}` | ⬜ | 강사 수정분. `target_field`는 표준 필드명 또는 `null`(제외 확정). 없으면 미리보기 그대로 확정. |
| Req(header) | `Idempotency-Key` | string | ✅ | 재확정 방지. |
| Res | `data.status` | enum | | `transforming` 진입 → 완료 시 `done`(결과는 GET 또는 Kafka 이벤트로). |

- **확정 재검증:** override 반영 후 **RequiredField 게이트 재통과**(§3). 필수 필드가 여전히 미매핑이면 변환하지 않고 `blocked` 유지(어느 필수 필드인지 응답에 명시). `spec_overrides`의 `target_field`가 표준 필드명이 아니면 `400 INVALID_SCHEMA`.
- **[제안] blocked 상태에서의 confirm:** override로 필수를 채우면 정상 진행. 채우지 못하면 `200 + status=blocked`로 반환(변환 미실행) — 별도 4xx를 만들지 않는다(게이트 거부=정상, error_codes 불변식). *409로 할지 200+status로 할지 §6 열린 질문.*

---

## 2. 상태기계 — 진입·실패·재시도

```
POST → profiling → inferring → [probing] → preview_ready ──confirm(재검증)──▶ done
                        │           │        (강사 확정 대기)                  (확정 spec)
                        └───────────┴────────────┴──▶ blocked (필수 미매핑 — confirm 차단, override로 해제 가능)
  (임의 단계 실패) ─────────────────────────────────▶ failed (파일 불가 · 5분 초과 · 내부 오류)
```

**AI 상태기계는 확정 spec까지다** — 그 뒤의 전체 행 변환·행별 검증·집계·저장은 백엔드 경로이며 이 상태기계에 나타나지 않는다(§4, 2026-07-30). `done` 배선은 confirm 소유 확정(§6.1) 후 정의하고, 그때까지 라우터는 재검증 뒤 `preview_ready`를 유지한다.

| 상태 | 진입 조건 | 담당 | 실패 시 | 재시도 |
| --- | --- | --- | --- | --- |
| `profiling` | POST 접수 직후 | 코드(결정론) | 파일 다운로드 불가·손상·미지원 → `failed(file_unreadable)` | 없음 — 강사 재업로드 |
| `inferring` | 프로파일 완료, 캐시 miss | LLM 1-shot | LLM 불가(LlmUnavailable/timeout) → **전 컬럼 needs_review/unmapped 미리보기로 폴백**(§3, `preview_ready`, `reused=false`) | 1-shot 자체는 재시도 안 함(폴백이 정직) |
| `probing` | inferring 결과에 저신뢰(<0.9) 컬럼 존재 | 에이전트②(LangGraph ReAct) | 도구 루프 상한 5회 소진 → 미해결 컬럼 `unmapped` 명시하고 `preview_ready` 수렴 | 루프 ≤5(01 §3·불변식 6) |
| `preview_ready` | 게이트(RequiredField·Confidence) 산정 완료 | 코드 | — | — |
| `blocked` | 필수 표준 필드가 미매핑 | 코드 | confirm 차단(확정 안 함) | 강사 `spec_overrides`로 해제 |
| `done` | confirm + RequiredField 재통과 → **확정 spec 저장** | 코드 | — | — |
| `failed` | 파일 불가 · **총 5분 초과** · 내부 오류 | 코드 | 종단 | 강사 재시도(재업로드) |

- **캐시 hit 경로:** POST 직후 프로파일 시그니처가 confirmed spec과 일치하면 `profiling → preview_ready`로 직행(`inferring`·`probing` 건너뜀, `reused=true`, LLM 0회 — §3.4).
- **재시도 원칙:** 블록/문장 단위 재시도는 여기 없다(Import는 매핑 추론 1회 + 도구 루프 ≤5). `parse_fail`류는 에이전트 스텝 내부에서 흡수하고, 소진 시 '모름'으로 정직하게 수렴한다(억지 매핑 금지).

---

## 3. 매핑 추론 파이프라인 설계

원칙(01 §3·03 I1~I3): **결정론 프로파일링 → LLM 매핑 추론 → (저신뢰 시) 조사 에이전트 → 게이트 → HITL 확정.** 여기까지가 AI다 — 확정 spec으로 전체 행을 옮기는 일은 백엔드 소유(§4).

### 3.1 결정론 프로파일링 (LLM 0)
- 입력: `source_url` 파일. 산출: `source_profile` = 시트 목록 · 컬럼별 통계(헤더 텍스트, 유니크 수, 타입 추정, 결측률, 값 길이 분포, 샘플 ≤20행). **모든 샘플은 마스킹 통과분**(masking §3, 실명·연락처 치환).
- 프로파일 시그니처(§3.4) 산출 → spec 캐시 조회.

### 3.2 LLM 1-shot 매핑 추론
- 입력: 마스킹된 프로파일(헤더 + 통계 + 샘플). 출력: `MappingSpec` = `columns[]{source, target, confidence, unmapped_reason?}`. **target은 표준 필드명만**(07 — enum으로 강제, 자유 텍스트 금지). 억지 매핑 금지 → 모르면 `target=null` + `unmapped_reason`.
- LLM은 **추론만** — 값 정규화·행 변환은 하지 않는다(AI 범위 밖 — 백엔드 소유, §4).
- **LLM 실패 폴백(HANDOFF §6·CLAUDE.md §1):** LlmUnavailable/timeout이면 프로파일링(결정론)은 이미 끝났으므로, **전 컬럼을 `confidence=0`·`needs_review=true`(식별 가능 표준 필드 후보가 없으면 `unmapped`)로 채운 `preview_ready`**를 낸다 — 강사가 수동 매핑. 작업을 `failed`로 떨구지 않는다(데이터는 안전, 다음 행동은 수동 확정). 빈 매핑을 지어내지 않는다.

### 3.3 조사 에이전트(probing) — 저신뢰 해소 (01 §3)
- **기동 조건:** 1-shot 결과에 `confidence < 0.9` 컬럼이 하나라도 있으면 `agent_run(mapping_probe)` 생성.
- **도구(전부 결정론·마스킹 뒤):** `get_unique_values(col)` · `get_more_sample(sheet, n)` · `check_join_key(sheetA, sheetB)`. 인명 후보 컬럼은 **값 대신 통계만** 반환(masking §4) — 에이전트 플래너는 마스킹 통과분 외 원문을 볼 경로가 구조적으로 없다.
- **루프 상한 5회**(불변식 6). 저신뢰 컬럼이 소진되면 수렴, 상한 도달 시 미해결 컬럼을 `unmapped`로 명시. 도구 호출·중간 가설 전부 `agent_step` + LangSmith trace.

### 3.4 confidence·needs_review·reused 산정 규약

| 개념 | 규약 |
| --- | --- |
| `confidence` | LLM(1-shot/에이전트)이 컬럼별로 산정한 매핑 신뢰도 0~1. **판정값이 아니라 표시·게이트 입력**. |
| `needs_review=true` | `confidence < CONFIDENCE_REVIEW`(**[제안] 기본 0.9** — 08 threshold 시트로 관리, 하드코딩 금지). 숨기지 않고 강사에게 노출. |
| RequiredField 게이트 | 표준 스키마 **필수 필드**(07 §2·§3의 ✅ — 예: `occurred_at`·`event_type`, 명부 이전 시 `student_name`·`class_name`·`enrolled_at`·`status`·`consent`)가 모두 매핑돼야 통과. 하나라도 미매핑 → `blocked`. |
| Confidence 게이트 | 필수 통과분 중 저신뢰는 발화 차단이 아니라 `needs_review` **플래그**(강사 확인용) — 게이트가 값을 지어내지 않는다. |
| `reused=true` | **양식 시그니처 일치 시** LLM·에이전트 **0회**로 confirmed spec 재사용(03 I3). 시그니처 = 테넌트 스코프 + **정규화된 헤더 집합**(트림·대소문자·공백 정규화 후 정렬) + 시트 구성(시트 수·컬럼 수) 의 결정론 해시. 일치 → 저장된 `spec_version` 그대로, 동일 파일+동일 spec = 동일 결과. **[제안]** 시그니처 구성요소·정규화 규칙은 골든 픽스처로 고정. |

---

## 4. 산출물 경로 — 백엔드 소유 (2026-07-30 확정)

**AI는 매핑 제안까지다.** 전체 행 변환·행별 검증·집계·산출물 저장은 **백엔드 소유**로 이관됐다(2026-07-30, 승우 제안·A 수용 — 99 결정 로그 20). 이 문서가 이전에 §4로 규정했던 출력 스펙(`output_url`·`row_total`·`row_ok`·`row_errors[]`·백엔드 F1 반영 경계)은 **폐기**한다 — AI는 강사가 확정한 매핑 spec(§1.2·§1.3)까지 책임지고, 그 spec으로 원본 전체 행을 표준 스키마로 옮기는 일·행별 정규화 실패 리포트·동의 미보유 행 처리는 백엔드가 자기 경로에서 수행한다. AI에는 결정론 변환기·결과 파일·행 집계가 없다(`import_mapping/transform.py` 삭제·`ImportResult` 계약 제거). 남는 AI 책임은 **파일 판독(프로파일링) · 매핑 추론 · 게이트 · 강사 확정 재검증 · 확정 spec 캐시**다.

---

## 5. 개인정보 — 실명 경계와 자동 제외 규칙

**경계 변화(2026-07-30):** 산출물 경로가 백엔드로 넘어가면서(§4) **AI가 실명 값을 내보내는 경로는 사라졌다** — `output_url`도, 실명이 담긴 결과 파일도 AI가 만들지 않는다. 그러나 **AI 서버가 원본 파일을 읽는 것은 유지된다**((b)안 — 프로파일링·매핑 추론이 AI 책임이므로). 따라서 아래 §5.2 가드레일(LLM·저장소·로그 무접촉)은 **완화 대상이 아니라 유일한 방어선이 된다.** 이전에는 "산출물은 백엔드 vault에서 alias 치환"이라는 하류 방어가 함께 있었으나, 이제 실명이 AI 경계 안에 들어왔다가 나가지 않는다는 보장은 §5.2뿐이다.

**핵심(07 정본):** 불변식은 "파이프라인에 실명이 흐르면 안 된다"가 **아니라** "**AI의 LLM과 저장소가 실명을 보면 안 된다**"이다. 실명은 백엔드가 명부를 세우는 재료이고(07 §1), alias 발급은 백엔드 소유다 — AI는 alias를 **발급할 수 없다**(구조). 그래서 실명 컬럼은 **매핑 목적지로는 다뤄지되 값은 AI를 통과하지 않는다**.

### 5.1 실명 필드 매핑 vs 자동 제외
- **실명 표준 필드는 매핑 대상이다** — `student_name`·`guardian_name`·`guardian_phone`은 07 §2 표준 필드이므로 타사 컬럼(원생명·보호자명·연락처)이 여기로 매핑된다. **매핑은 컬럼→목적지 제안일 뿐이고 값을 옮기는 주체는 백엔드**(§4)다.
- **자동 제외(unmapped)** 는 **표준 스키마에 목적지가 없는 개인정보 컬럼**(주소·이메일·비상연락망 등)에 한정 → `target=null` + `unmapped_reason`(정책적 제외 — "확신 부족"과 구분).

### 5.2 가드레일 (실명 무접촉의 명문화)
- **LLM 입력 = redaction 통과 샘플만** — 매핑 **추론 전용**(헤더 + 마스킹된 통계/샘플). 값 변환은 결정론 코드가 하고 **LLM은 원본 값에 무접촉**.
- **AI PG·로그·에러 메시지에 실명 잔존 0** — 기존 "실명 컬럼 0" 불변식 그대로. 도구 반환·`agent_step`·`LLM_PAYLOAD` 저장 모두 마스킹 통과분만(masking §3·§4 — `get_unique_values`의 인명 컬럼은 값 대신 통계만).
- **원본은 읽고 남기지 않는다** — AI 서버는 프로파일링을 위해 원본 파일을 내려받지만 **임시파일·복사본을 남기지 않는다**(처리 후 즉시 정리). 산출물 방출 경로가 없어진 만큼(§4) 실명이 AI 경계 안에 존재하는 유일한 구간이 이 읽기이며, 그래서 이 항목은 완화되지 않는다.
- **fail-closed** — 마스킹 불확실이면 전송 중단·해당 스텝 결과 폐기 → '모름'(masking §3). LLM/에이전트는 마스킹 통과분 외 원문을 볼 경로가 **구조적으로** 없다(프롬프트 지시로 막지 않는다).

### 5.3 preview 경계 (sample_rows)
- `mapping_preview.sample_rows`(강사 확인 화면)에는 **실명이 포함될 수 있다** — 강사 **본인 데이터**라 화면 노출은 허용. 이 sample_rows는 원본에서 읽은 값의 미리보기이며 **LLM이 생성하지 않는다**.
- 단 **AI 쪽 로그·저장 금지는 동일 적용** — sample_rows를 AI PG·로그·trace에 남기지 않는다(응답 본문으로 강사에게 전달만, 서버 잔존 0).

---

## 6. 열린 질문 절

### 6.1 [백엔드 확인 대기] (승우 확인 안건)
- **★ 실명 경계 정합 — 04 §3.8 예시 개정 [제안 — 백엔드 확인 대기]** — 본 문서는 **07 §1을 정본**으로 삼아 Import 산출물(②)을 **실명 포함**(원생명→`student_name`·연락처→`guardian_phone` 등)으로 확정하고, AI는 값 미접근(§5)으로 설계했다. 이에 따라 04 §3.8 예시(`원생명→student_alias`·`連락처→null`·`weekly_score`)와 07 §4("연락처 자동 제외") 문구는 이 결정과 어긋난다. **실명·vault·명부 소유가 백엔드**이므로 [A 확정]이 아니라 **백엔드(승우) 확인 후** 04 §3.8 예시·07 §4를 함께 개정한다. (07 자체도 "백엔드 확정 대기" 문서.)
- **`output_url` 스토리지 규격** — 버킷·경로·수명·접근 토큰 형식(Open-3 스토리지 소유가 인프라). F1 업로드 경로가 기대하는 파일 포맷(xlsx vs csv vs 표준 JSON)과 일치 여부.
- **동의 미보유 행 보류의 정확한 경계** — AI 산출물엔 `consent` 값을 그대로 싣는다. 보류=백엔드로 확정돼 있으나, `pending`/`revoked` 행을 백엔드가 **드롭**하는지 **격리 적재**하는지 F1 경로 정의 필요.
- **confirm-on-blocked HTTP 형태([제안] 검토)** — 필수 미매핑 상태 confirm을 `200 + status=blocked`(현 제안, 게이트 거부=정상 원칙)로 둘지, `409`(상태 충돌)로 둘지. 계약 §1 코드 사전과 함께 확인.

### 6.2 [문서 정합 — A 후속]
- ~~**상태명 동기화**~~ ✅ **완료(2026-07-30)** — `error_codes.md §2.4`가 `profiled`/`transformed`(과거형)를 쓰던 것을 04 §3.8 정본(`profiling`·`inferring`·`probing`·`preview_ready`·`done`)에 맞춰 동기화했다. `transforming`은 세 문서 모두에서 제거됐다(§4 소유 이동). **ERD 잔여**(`IMPORT_JOB.status`의 `transformed`·`row_*` 컬럼·`IMPORT_ROW_ERROR`)는 양자 파일·마이그레이션이 걸려 별건이다 — 99 D ⑲.
- **`failed` 상태 명문화** — 04 §3.8 화살표엔 `done/blocked`만 있으나 async 규약(04 line 119 "5분 초과 시 failed")·error_codes §2.4·§6에 `failed`가 있어 본 문서가 종단 상태로 편입. 04 §3.8 상태열에 `failed` 한 단어 추가 제안.

### 6.3 A 단독 확정(본 문서 범위)
- 상태기계 진입·실패·재시도(§2), LLM 실패 폴백=전 컬럼 수동 미리보기(§3.2), probing 기동 임계 `<0.9`·루프 ≤5(§3.3), reused 시그니처 산정(§3.4), **실명 무접촉 가드레일·preview 경계(§5.2·§5.3)**, 표준 목적지 없는 개인정보 컬럼 자동 제외(§5.1). *(실명 필드 매핑 자체의 정합은 6.1 백엔드 확인 안건.)*
- **[제안] 임계·시그니처 값**(`CONFIDENCE_REVIEW=0.9`, 시그니처 구성)은 08 threshold 시트·골든 픽스처와 연동해 확정(하드코딩 금지 — CLAUDE.md §6).
