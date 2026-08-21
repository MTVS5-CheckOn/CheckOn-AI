# CheckOn AI–BE 문제출제 통신 규약 회의안

- 작성일: 2026-08-21
- AI 기준: `develop` `fc7f9a718767ff09ea1c929cbbe79e758ed70a92`
- 대상: Backend, Kafka–HTTP Adapter, AI FastAPI
- 목적: 약점 진단 → 문제 생성 → 조회 → 수정 → 오답 약점 환류 계약 확정
- 표기: **확정**은 현재 AI 코드·HTTP fixture로 검증된 값, **협의**는 회의 결정 사항

## 1. 회의 결론 요약

1. BE는 AI가 반환한 `skill_node_id`를 임의로 다시 고르지 않고 보존한다.
2. `POST /v1/problems`는 LLM 완료를 기다리지 않고 `202 + queued`를 즉시 반환한다.
3. BE 또는 Adapter는 `Retry-After`에 따라 같은 `job_id`를 조회한다.
4. Worker phase와 문제 세트의 domain status를 서로 다른 필드로 저장한다.
5. 종단 결과의 `set_id`로 문항 목록과 상세를 조회한다.
6. 학생이 고른 오답의 `misconception_tag`를 다음 진단 이벤트로 되돌린다.
7. 고정 시간 초과만으로 잡을 실패 처리하거나 새 멱등키로 중복 생성하지 않는다.
8. startup recovery sweep과 lease heartbeat는 운영 개방 전 AI 후속 조건이다.

## 2. 전체 호출 흐름

```text
POST /v1/diagnosis
  → weakness_map.nodes의 skill_node_id 보존
POST /v1/problems
  → 202 {job_id, status="queued"}
GET /v1/problems/{job_id}
  → queued/leased/running/paused: Retry-After 후 같은 job_id 재조회
  → succeeded: result.set_id 보존
GET /v1/problems/{set_id}/items
  → slot_index와 상태 목록 회수
GET /v1/problems/{set_id}/items/{slot_index}
  → 문항·정답·오답 분석·근거 회수
POST /v1/problems/{set_id}/items/{slot_index}/revisions
  → ai_refine
학생 제출 저장
  → 선택 오답의 misconception_tag를 다음 diagnosis에 전달
```

## 3. 공통 HTTP 계약

### 3.1 필수 헤더

| 요청 | `X-Tenant-Id` | `X-Request-Id` | `Idempotency-Key` |
| --- | --- | --- | --- |
| GET | 필수 | 불필요 | 불필요 |
| POST | 필수 | 필수 | 필수 |

- `X-Tenant-Id`: 테넌트 격리 키. 바디에 중복하지 않는다.
- `X-Request-Id`: 요청 추적 키. 응답 헤더에 echo된다.
- 같은 `Idempotency-Key`와 같은 바디는 기존 결과를 재반환한다.
- 같은 키와 다른 바디는 `409 IDEMPOTENCY_CONFLICT`다.
- 네트워크 timeout 뒤 같은 논리 작업을 재시도할 때 새 키를 발급하지 않는다.

### 3.2 공통 envelope

```json
{
  "data": {},
  "error": null,
  "meta": {
    "execution_id": "uuid-or-null",
    "versions": {}
  }
}
```

- 성공은 `data`, 실패는 `error.code`·`error.message`·필요시 `error.detail`을 사용한다.
- 잡이 있으면 `meta.execution_id`는 AI 실행 원장 키다.
- 실행 전 오류는 `meta.execution_id=null`일 수 있다.
- 등록되지 않은 경로의 Starlette 404/405는 envelope 밖일 수 있다.
- 5xx에서 원문 예외, LLM 원문, API 키, endpoint를 기대하거나 저장하지 않는다.

## 4. Step 1 — 약점 진단

`POST /v1/diagnosis`

```json
{
  "student_ref": "st_opaque_alias",
  "period": {
    "from_date": "2026-06-17",
    "to_date": "2026-08-12"
  },
  "as_of": "2026-08-12T09:00:00Z",
  "snapshot_hash": "sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc",
  "events": [
    {
      "event_id": "submission-001",
      "area_tag": "language",
      "type_tag": "concept",
      "item_format": "mcq",
      "chosen_no": 2,
      "correct_no": 1,
      "misconception_tag": "application_target_substitution",
      "skill_node_id": "language.grammar.sentence.structure",
      "correct": false,
      "occurred_at": "2026-08-01T09:05:00Z",
      "tag_confirmed": true
    }
  ]
}
```

BE 책임:

- `chosen_no`, `correct_no`는 1-based `1..5`로 보존한다.
- 저장 정답과 학생 선택을 대조해 `correct`를 확정한다.
- 오답이면 선택한 선지의 `misconception_tag`를 전달한다.
- 정답이면 `misconception_tag`를 생략하거나 `null`로 보낸다.
- `skill_node_id`, `snapshot_hash`, taxonomy·graph·config version을 보존한다.
- `weakness_map.nodes`의 객체 순서를 추천 순위로 해석하지 않는다.
- 한 cell에 여러 node가 있으면 강사 선택 또는 버전 고정 제품 정책으로 고른다.
- `nodes[].verdict=ok`인 node를 약점으로 취급하지 않는다.
- `data.misconceptions`는 리포트이며 `weakness_map` 판정을 변경하지 않는다.

## 5. Step 2 — 문제 생성

`POST /v1/problems`

```json
{
  "target_kind": "student",
  "target_ref": "st_opaque_alias",
  "target_source": "teacher_manual",
  "manual_targets": ["language.grammar.sentence.structure"],
  "snapshot_hash": "sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc",
  "taxonomy_version": "v1",
  "area_tag": "language",
  "type_tags": ["concept"],
  "item_format": "mcq",
  "count": 1,
  "requested_difficulty": "medium"
}
```

확정 제약:

- `count`: `1..20`
- `item_format`: v1은 `mcq`만 지원
- `requested_difficulty`: `low | medium | high | null`
- `target_source=teacher_manual`: 지원
- `target_source=weakness_auto`: `400 weakness_auto_not_wired`
- 예약 type tag `apply`: `400 type_tag_not_supported`
- `graph_version`, `config_version`은 POST 바디 필드가 아니다.
- extra 필드는 `400 INVALID_SCHEMA`다.

### 5.1 영역별 자료 요청

| 영역 | `passage` | `work_selection` |
| --- | --- | --- |
| `language` | 생략 | 생략 |
| `reading` | `PassageRequest` 필수 | 생략 |
| `literature` | 생략 | `WorkSelection` 필수 |
| `speech_writing` | `SpeechWritingSourceRequest` 필수 | 생략 |
| `media` | `MediaSourceRequest` 필수 | 생략 |

- `speech_writing.source_kind`: `presentation | writing_draft | writing_sources`
- `media.source_kind`: `single | paired`
- 잘못된 조합은 `400 source_procurement_not_implemented`다.

### 5.2 202 응답

```json
{
  "data": {
    "job_id": "00000000-0000-4000-8000-0000000000b0",
    "status": "queued"
  },
  "error": null,
  "meta": {
    "execution_id": "00000000-0000-4000-8000-0000000000e0",
    "versions": {}
  }
}
```

- POST는 잡 적재 후 즉시 반환한다.
- POST 요청 안에서 문제 생성·교차 검증 LLM을 호출하지 않는다.
- LLM 실행은 앱 startup의 `ProblemDrainLoop`가 수행한다.
- POST read timeout을 LLM 전체 실행 시간에 맞춰 늘리지 않는다.
- `job_id`, `meta.execution_id`를 즉시 영속 저장한다.
- 202 응답에는 `set_id`가 없다.

## 6. Step 3 — 잡 polling

`GET /v1/problems/{job_id}`

| `data.status` | 종단 | BE·Adapter 동작 |
| --- | --- | --- |
| `queued` | 아니오 | `Retry-After` 후 같은 `job_id` 재조회 |
| `leased` | 아니오 | `Retry-After` 후 같은 `job_id` 재조회 |
| `running` | 아니오 | `Retry-After` 후 같은 `job_id` 재조회 |
| `paused` | 아니오 | `Retry-After` 후 같은 `job_id` 재조회 |
| `succeeded` | 예 | `data.result` 확인 |
| `failed` | 예 | 실패 코드 저장 후 종료 |
| `cancelled` | 예 | 취소로 종료 |

- polling 종료 조건의 정본은 `data.status`다.
- `Retry-After`는 다음 조회 시점의 advisory다.
- 헤더가 없다는 사실만으로 종단이라고 판단하지 않는다.
- 고정 21분 같은 관찰 상한을 Worker 실패로 바꾸지 않는다.
- 관찰 SLO를 넘긴 잡은 `processing`으로 두고 reconciliation이 계속 조회한다.
- timeout이 나도 같은 `job_id`를 유지하며 중복 POST를 만들지 않는다.
- 성공 시 `data.result.set_id`를 저장한다.

## 7. Step 4·5 — 문항 목록과 상세

목록: `GET /v1/problems/{set_id}/items`

- `job_id`가 아니라 `set_id`를 사용한다.
- 목록에는 문항 본문이 없다.
- `status_counts`와 각 `slot_index`를 회수한다.
- slot 상태: `verified | needs_review | verification_unavailable | dropped`

상세: `GET /v1/problems/{set_id}/items/{slot_index}`

보존 필드:

- `item.area_tag`, `item.type_tag`, `item.item_format`, `item.skill_node_id`
- `item.stem`, `item.choices[]`, `item.answer.correct_no`, `item.rationale`
- `item.choices[].why_wrong`, `item.choices[].misconception_tag`
- `item.evidence[]`, `cross_solve`, `verification`
- `current_revision_no`, `available_actions`, `review_reason`, `failure_reason`

저장 규칙:

- `answer.correct_no`는 1-based 원문으로 보존한다.
- FE가 0-based면 `correct_option_index = correct_no - 1`을 별도 파생한다.
- 정답 선지의 `why_wrong`, `misconception_tag`는 `null`이다.
- 오답 선지는 두 값이 모두 비어 있지 않다.
- 학생의 `chosen_no`로 선택한 `choices[].no`를 찾는다.
- 선택한 오답의 `misconception_tag`를 다음 diagnosis에 복사한다.
- `dropped`는 `item_id=null`, `item=null`일 수 있다.
- `dropped`를 가짜 문항으로 대체하지 않는다.
- 일부 slot 실패 시 성공 문항을 버리지 않고 부분 성공으로 저장한다.

## 8. Step 6 — 문항 수정

`POST /v1/problems/{set_id}/items/{slot_index}/revisions`

```json
{
  "base_revision_no": 0,
  "revision_kind": "ai_refine",
  "instruction": "수정 지시"
}
```

- 현재는 `ai_refine`만 지원한다.
- 5영역 모두 `ai_refine`을 지원한다.
- `teacher_direct`, `rollback`, 교체, 삭제는 미구현이다.
- 수정 가능 여부는 영역 하드코딩이 아니라 `available_actions`로 판단한다.
- 오래된 revision은 `409 REVISION_CONFLICT / stale_base_revision`이다.
- 수정 진행 중이면 `409 REVISION_CONFLICT / revision_in_progress`다.
- 수정 후 상세를 다시 조회해 revision과 검증 상태를 갱신한다.

## 9. 상태 저장 규칙

Worker phase:

- `queued | leased | running | paused | succeeded | failed | cancelled`

Domain status:

- 세트: `generated | partial_success | failed | rejected_insufficient`
- 문항: `verified | needs_review | verification_unavailable | dropped`

두 축을 한 컬럼에 합치지 않는다. Worker가 `succeeded`여도 세트가 `partial_success`일 수 있다.
데이터 부족, 게이트 거부, 일부 문항 폐기는 transport 장애가 아니므로 5xx나 Worker 실패로
변환하지 않는다.

## 10. 오류 규약

| HTTP/code | 의미 | BE 동작 |
| --- | --- | --- |
| `400 INVALID_SCHEMA` | 필드·enum·지원 범위 오류 | 요청 수정, 자동 반복 금지 |
| `404 NOT_FOUND` | job·set·slot 부재 또는 타 tenant | 식별자·tenant 확인 후 중단 |
| `409 IDEMPOTENCY_CONFLICT` | 같은 키에 다른 바디 | 같은 논리 작업이면 구현 오류 |
| `409 REVISION_CONFLICT` | 수정 동시성 충돌 | reason에 따라 제한 처리 |
| `500 INTERNAL` | AI 내부 오류 | `X-Request-Id`로 추적 |
| `503 LLM_UPSTREAM_DOWN` | LLM 재시도 예산 소진 | 즉시 반복 금지 |
| `504 TIMEOUT` | AI 내부 호출 timeout | 즉시 반복 금지 |

- HTTP status와 `error.code`를 함께 사용한다.
- 비공개 `failure_detail`을 사용자에게 노출하지 않는다.
- 화면 문구는 BE·FE가 공개 reason code를 기준으로 번역한다.

## 11. Kafka–HTTP Adapter

요청 이벤트 최소 필드:

```text
tenant_alias
request_id
child_execution_id
target_index
idempotency_key
area_tag
type_tags
skill_node_id
requested_count
snapshot_hash
taxonomy_version
contract_version
```

child 멱등키 권장 형식:

```text
problem-request:{request_id}:target:{target_index}
```

결과 이벤트 최소 필드:

```text
event_id
tenant_alias
request_id
child_execution_id
ai_job_id
ai_execution_id
ai_set_id
worker_phase
domain_status
requested_count
processed_count
unstarted_count
status_counts
stop_reason
public_failure_reason
items
```

- 결과 저장과 Outbox 생성을 같은 DB 트랜잭션으로 처리한다.
- `(child_execution_id, terminal_phase)`로 중복 결과를 차단한다.
- Kafka에는 문항·지문 전체 대신 참조 이벤트를 기본으로 사용한다.
- 문항 본문은 REST의 `set_id`, `slot_index`로 조회한다.
- 본문 전송을 택하면 byte 상한·chunk 순서·재조립·재전송 계약이 별도로 필요하다.

## 12. 저장 모델 권고

```text
problem_generation_request
  └─ children[]
       ├─ child_execution_id
       ├─ target_index
       ├─ idempotency_key
       ├─ ai_job_id
       ├─ ai_execution_id
       ├─ ai_set_id
       ├─ worker_phase
       ├─ domain_status
       ├─ requested_count
       ├─ processed_count
       ├─ unstarted_count
       └─ terminal_at
```

- `(tenant_id, request_id, target_index)`를 유일하게 유지한다.
- 부모 단일 `job_id`에 여러 child를 덮어쓰지 않는다.
- 부모 상태는 child 수량과 상태로 결정론적으로 계산한다.
- 문항 행 개수만으로 부모 성공·실패를 판정하지 않는다.

## 13. 개인정보·관측성

- tenant, student, class는 opaque alias만 전달한다.
- 실명, 연락처, 자유서술 개인정보를 로그에 남기지 않는다.
- BE `request_id`, `child_execution_id`, Adapter ID, AI `job_id`, `execution_id`, `set_id`,
  `X-Request-Id`를 한 trace에서 연결한다.
- `Idempotency-Key`는 로그에 평문 대신 비민감 해시로 남긴다.
- LLM 원문 응답, API 키, endpoint, 내부 예외 원문을 이벤트에 싣지 않는다.

## 14. 현재 제한과 AI 후속

현재 제한:

- `weakness_auto` 생산 diagnosis seam 미배선
- `apply` type tag 미지원
- `ai_refine` 외 revision kind 미지원
- `language.grammar.fortition` redaction 오탐 잔여
- 일부 terminal·5xx 조합의 고정 HTTP fixture 미비

운영 개방 전 AI 후속:

1. startup에서 영속 queued tenant를 재발견하는 recovery sweep
2. 실행 중 `renew_lease`를 호출하는 주기적 heartbeat
3. 워커 사망 후 lease 만료·회수 검사
4. 실제 프로세스·컨테이너 재시작 E2E
5. 두 background drain의 DB connection 합산 관측

PostgreSQL이면 재시작 뒤 잡을 조회할 수 있지만, 현재 `ProblemDrainLoop`의 tenant 예약 큐는
메모리다. 재시작 뒤 기존 queued 잡의 자동 실행 재개까지 보장되는 것은 아니다. BE는 이때
새 멱등키로 중복 잡을 만들지 않는다.

## 15. BE 구현 체크리스트

- [ ] 공통 헤더와 tenant 격리를 구현했다.
- [ ] 같은 논리 작업 재시도에 같은 멱등키를 쓴다.
- [ ] diagnosis의 `skill_node_id`를 임의 재선택 없이 보존한다.
- [ ] 5영역별 자료 요청을 정확히 조립한다.
- [ ] 202의 `job_id`, `execution_id`, `status=queued`를 저장한다.
- [ ] `Retry-After` polling과 reconciliation을 구현한다.
- [ ] Worker phase와 domain status를 별도 저장한다.
- [ ] terminal 결과의 `set_id`로 items를 조회한다.
- [ ] 각 `slot_index` 상세를 조회한다.
- [ ] `dropped item=null`에서 가짜 문항을 만들지 않는다.
- [ ] 1-based 정답 번호를 보존한다.
- [ ] 선택 오답 라벨을 다음 diagnosis에 전달한다.
- [ ] 수정 가능 여부를 `available_actions`로 판단한다.
- [ ] revision 충돌과 멱등 충돌을 구분한다.
- [ ] parent-child-Outbox를 중복 없이 저장한다.
- [ ] 공개 reason code만 화면 문구로 번역한다.

## 16. 회의 결정표

| 번호 | 안건 | AI 권고 | 회의 결과 |
| --- | --- | --- | --- |
| D1 | polling 소유자 | Adapter가 소유하고 BE에는 상태 이벤트 전달 |  |
| D2 | 관찰 SLO 초과 | 실패 전환 금지, reconciliation 이관 |  |
| D3 | 한 cell의 복수 node | 강사 선택 또는 버전 고정 제품 정책 |  |
| D4 | 다중 target 저장 | target별 child execution |  |
| D5 | Kafka 결과 payload | 참조 이벤트 + REST 상세 조회 |  |
| D6 | 부분 성공 표시 | 성공 문항과 dropped·미처리 수량 동시 표시 |  |
| D7 | `verification_unavailable` | 저장 허용, 학생 발행 차단 |  |
| D8 | revision v1 | `ai_refine`만 개방 |  |
| D9 | recovery·heartbeat | 운영 개방 전 필수 |  |
| D10 | 두 drain DB 예산 | 배포 환경 합산 실측 후 확정 |  |

## 17. 공동 E2E 인수 기준

1. 단일 target 전체 성공
2. 다중 target 전체 성공
3. 다중 target 부분 성공
4. `dropped item=null` 포함
5. 일부 slot 상세 조회 실패
6. 요청 이벤트 중복 소비
7. terminal 이벤트 중복 소비
8. Adapter 재시작 후 같은 `job_id` polling 재개
9. 관찰 SLO 초과 후 reconciliation 재개
10. 선택 오답 라벨이 다음 diagnosis에 반영
11. 5영역별 자료 요청 조립
12. 두 revision 충돌 사유 분리

릴리스 게이트:

- 실제 E2E 실패 0건
- 중복 잡·문항·결과 0건
- tenant 교차 조회 0건
- 정답 번호·오답 라벨 손실 0건
- 부분 성공 수량 손실 0건
- 로그·이벤트의 키·endpoint·LLM 원문 노출 0건

## 18. 기계 대조 snapshot

아래 JSON은 회의 문면 전체를 고정하지 않고 BE 구현에 영향을 주는 의미만 코드·fixture와
대조하기 위한 snapshot이다. 문장을 다듬어도 값이 같으면 가드는 통과한다.

<!-- ai-be-contract-snapshot:start -->
```json
{
  "schema_version": "ai-be.problem-generation.v1",
  "problem_post": {
    "http_status": 202,
    "initial_status": "queued",
    "execution_mode": "background_drain"
  },
  "job_phases": [
    "queued",
    "leased",
    "running",
    "paused",
    "succeeded",
    "failed",
    "cancelled"
  ],
  "terminal_phases": ["succeeded", "failed", "cancelled"],
  "supported_areas": [
    "language",
    "reading",
    "literature",
    "speech_writing",
    "media"
  ],
  "source_shapes": {
    "language": "none",
    "reading": "passage",
    "literature": "work_selection",
    "speech_writing": "passage",
    "media": "passage"
  },
  "supported_revision_kinds": ["ai_refine"],
  "unsupported_target_sources": ["weakness_auto"],
  "answer_number_base": 1,
  "misconception_feedback_field": "choices[].misconception_tag",
  "retry_after_is_advisory": true,
  "startup_queued_recovery": "not_guaranteed"
}
```
<!-- ai-be-contract-snapshot:end -->

## 19. 정본 우선순위

충돌 시 다음 순서로 판정한다.

1. 현재 AI HTTP fixture와 OpenAPI
2. `docs/04_api_contract.md`
3. 이 회의 명세
4. 과거 회의록·PR 본문·메신저

문서와 코드가 갈리면 추측하지 않고 재현 가능한 fixture를 추가한 뒤 함께 확정한다.
