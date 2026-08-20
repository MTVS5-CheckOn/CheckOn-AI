# 문제 출제 스튜디오 Backend 후속 작업 제안

- 작성일: 2026-08-13
- 전달 대상: CheckOn Backend 팀
- 기준 문서: `PROBLEM_GENERATION_AI_RESPONSE_ADVERSARIAL_REVIEW.md` (Backend 팀 전달 원본 · 저장소 미포함)
- 문서 목적: AI 문제 출제 연동을 완료하기 위해 Backend가 직접 수행할 작업과 Adapter·AI 선행조건을 분리하여 실행 순서와 완료 기준을 제시한다.

## 1. 결론

Backend의 다음 작업은 아래 네 축으로 진행한다.

1. 부모 문제 생성 요청 아래 여러 AI child execution을 영속화한다.
2. AI 진단 결과를 Step 1의 정본으로 저장·조회하되 기존 Backend 판정과 혼합하지 않는다.
3. Adapter가 전달한 부분 성공·실패·미처리 결과를 손실 없이 투영하고 부모 상태를 집계한다.
4. 현재 검증된 단일 출제 범위를 기능 플래그로 고정하고 실제 E2E가 통과한 영역만 확장한다.

현재 Backend에 이미 구현된 다음 항목은 신규 작업으로 다시 만들지 않는다.

- `answer.correct_no` 1-based 입력 처리
- `correct_option_index` 0-based 입력 처리
- `verified` → `PASSED`
- `needs_review` → `REVIEW_REQUIRED`
- `verification_unavailable` → `UNVERIFIABLE`
- `dropped` → `EXCLUDED`

단, `dropped`에서 `item_id`와 본문이 `null`인 경우 존재하지 않는 문항을 생성하지 않는 보완은 필요하다.

## 2. 권장 실행 순서

```text
계약 기준 고정
  → child execution 저장 구조
  → Kafka 요청·결과 계약 보완
  → Adapter 실제 AI 회수 경로 완성
  → Backend 결과 투영·부모 집계
  → Step 1 AI 진단 전환
  → 실제 E2E
  → 지원 영역 확대
```

## 3. Backend 직접 작업

### BE-PG-01. 연동 기준 버전과 기능 플래그 고정

**우선순위:** P0

Backend와 Adapter가 검증할 AI 기준을 catalog가 포함된 `a4af8dd` 이상 커밋 또는 별도 승인 태그로 고정한다.

구현 항목:

- 환경별 AI 계약 버전 설정 추가
- 실행 및 결과 메타에 AI 계약 버전 기록
- 현재 출제 가능 범위를 기능 플래그로 관리
- 미지원 영역을 프론트 응답에서 명시적으로 비활성화
- 계약 버전이 다르면 Adapter 연동을 시작하지 않고 운영 오류로 기록

최초 활성 범위:

```text
area_tag=language
skill_node_id=language.grammar.phonological_change
item_format=mcq
type_tag=fact|infer|critic|concept 중 AI capability가 허용한 값
```

**완료 기준:** 실행 로그와 저장 데이터에서 사용한 AI 계약·taxonomy·capability 버전을 추적할 수 있고, 지원하지 않는 영역은 AI 호출 전에 차단된다.

### BE-PG-02. 부모 요청–child execution 영속 구조 추가

**우선순위:** P0

한 Backend 요청에서 여러 영역×유형 target을 선택할 수 있으므로 AI 호출 단위별 child execution을 별도로 저장한다.

권장 관계:

```text
problem_generation_request 1
 └─ problem_generation_execution N
     ├─ id
     ├─ tenant_id
     ├─ request_id
     ├─ target_index
     ├─ area_tag
     ├─ type_tags
     ├─ skill_node_id
     ├─ requested_count
     ├─ idempotency_key
     ├─ adapter_execution_id
     ├─ ai_job_id
     ├─ ai_execution_id
     ├─ ai_set_id
     ├─ phase
     ├─ domain_status
     ├─ public_failure_reason
     ├─ started_at
     └─ terminal_at
```

필수 규칙:

- `(tenant_id, request_id, target_index)` 유일성
- `ai_job_id`, `ai_set_id`는 child에 저장하고 부모 단일 컬럼에 덮어쓰지 않음
- 상태 전이는 단방향이며 종단 상태 이후 다른 종단 상태로 변경하지 않음
- 요청과 child, 최초 Outbox를 같은 DB 트랜잭션에 저장
- 모든 조회와 갱신에 `tenant_id` 조건 및 RLS 적용

**완료 기준:** target 2개 요청이 서로 다른 `job_id`와 `set_id`를 가진 child 2개로 저장되고 재처리해도 행이 중복되지 않는다.

### BE-PG-03. Kafka 요청 계약에 child 상관관계 추가

**우선순위:** P0

Adapter가 각 AI 실행 결과를 정확한 Backend child에 돌려줄 수 있도록 Kafka 계약에 다음 식별자를 포함한다.

필수 필드:

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
contract_version
taxonomy_version
```

child별 멱등키 권장 형식:

```text
problem-request:{request_id}:target:{target_index}
```

주의사항:

- Backend 내부 학생 UUID를 Kafka 이후 AI 경계로 전달하지 않는다.
- `teacher_weakness_selection`은 Backend 내부 출처값으로 유지할 수 있으나 Adapter의 AI HTTP 변환값은 `teacher_manual`이다.
- 같은 child 재발행에는 같은 멱등키와 동일 payload를 사용한다.

**완료 기준:** 요청 이벤트 fixture가 schema 검증을 통과하고 같은 이벤트 재소비 시 AI Job이 중복 생성되지 않는다.

### BE-PG-04. Adapter 결과 계약과 child 상태 반영

**우선순위:** P0

Backend는 Adapter가 정규화한 결과를 child 단위로 멱등 반영한다.

결과 이벤트 필수 필드:

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
source_result
```

처리 규칙:

- `event_id`와 `(child_execution_id, terminal_phase)`를 이용해 중복 이벤트를 차단
- 실행 phase와 도메인 결과 상태를 별도 컬럼으로 저장
- `partial_success`, 도메인 `failed`, `rejected_insufficient`를 Worker 실패로 변환하지 않음
- `failure_detail`은 저장·노출하지 않고 공개 가능한 reason code만 사용
- AI 원문 결과는 개인정보가 제거된 계약 범위에서 감사용으로 보존
- child 상태 갱신과 결과 반영을 한 트랜잭션으로 처리

**완료 기준:** 같은 terminal 이벤트를 여러 번 소비해도 결과·문항·상태가 한 번만 반영된다.

### BE-PG-05. `dropped` 및 미처리 slot 무손실 처리

**우선순위:** P0

AI의 `dropped` slot은 `item_id`와 본문이 `null`일 수 있다. Backend는 이를 가짜 문항으로 만들지 않는다.

처리 규칙:

- 본문이 없는 `dropped` slot은 문항 엔티티로 생성하지 않음
- child 결과에 `slot_index`, `status`, `failure_reason`을 별도 보존
- `status_counts`, `processed_count`, `unstarted_count`, `stop_reason`을 부모 집계에 포함
- 요청 수량과 실제 문항 행 수가 다르더라도 누락으로 오판하지 않음
- 리뷰 API는 생성 문항과 생성 실패 슬롯의 수량을 분리하여 반환

**완료 기준:** 성공 3개, dropped 1개, 미처리 1개의 요청에서 문항은 3개만 생성되고 요청 수량 5개 및 실패·미처리 수량이 정확히 표시된다.

### BE-PG-06. 부모 요청 상태 집계기 구현

**우선순위:** P0

child 상태가 변경될 때마다 부모 요청 상태를 결정론적으로 다시 계산한다.

권장 규칙:

| 부모 상태 | 조건 |
| --- | --- |
| `REQUESTED` | child가 아직 시작되지 않음 |
| `PROCESSING` | 하나 이상의 child가 비종단 상태 |
| `GENERATED` | 모든 child가 종단이며 요청 수량 전체가 사용 가능한 문항 |
| `PARTIAL_SUCCESS` | 모든 child가 종단이고 사용 가능한 문항이 1개 이상이지만 실패·폐기·미처리가 존재 |
| `FAILED` | 모든 child가 종단이고 사용 가능한 문항이 0개 |
| `CANCELLED` | 모든 child가 취소 또는 취소에 준하는 종단 상태 |

집계기는 문항 행의 개수만 세지 않고 child의 `requested_count`, `processed_count`, `unstarted_count`, `status_counts`를 사용한다.

**완료 기준:** child 완료 순서가 달라도 최종 부모 상태와 수량이 동일하며 동시 완료 이벤트에서도 lost update가 발생하지 않는다.

### BE-PG-07. Step 1 AI 진단 입력 조립 및 결과 저장

**우선순위:** P0

현재 Backend의 최근 8주 자체 집계를 Step 1의 최종 판정으로 사용하지 않고 AI `/v1/diagnosis` 결과를 정본으로 사용한다.

Backend 입력 조립 항목:

- opaque tenant/student alias
- 판정 기간의 학습 event
- `tag_confirmed`
- `area_tag`
- `type_tag`
- `skill_node_id`
- 정오답 및 계약상 필요한 측정값
- snapshot 기준시각과 `snapshot_hash`

결과 저장 항목:

- AI 원본 5×4 grid
- 각 cell의 `verdict`, `severity`, `item_count`, `cell_min_items`
- `skill_node_id` 또는 node별 진단 근거
- taxonomy·graph·diagnosis config 버전
- 제외된 기록 수와 공개 가능한 사유
- AI `execution_id` 및 요청 추적 ID

전환 규칙:

- 기존 Backend 판정과 AI 판정을 같은 화면에 혼합하지 않음
- 전환 전에는 기능 플래그로 기존 경로를 유지
- shadow 비교는 관측용으로만 허용하며 사용자 응답에는 한쪽 결과만 사용
- AI 진단 장애 시 기존 판정으로 조용히 fallback하지 않고 명시적인 진단 불가 상태를 반환

**완료 기준:** 동일 snapshot을 조회할 때 저장된 AI 진단 결과가 반환되고 Backend가 별도 임계값으로 재판정하지 않는다.

### BE-PG-08. 진단 node 보존 및 출제 target 결정 경계 확정

**우선순위:** P0

Adapter가 `area_tag`와 `type_affinity`만으로 `skill_node_id`를 임의 선택하게 해서는 안 된다.

권장 정책:

1. AI 진단이 제공한 `skill_node_id`를 Backend가 셀 선택 정보와 함께 보존한다.
2. 교사가 셀을 선택하면 Backend 요청에 해당 node 후보 또는 확정 node를 포함한다.
3. generation capability catalog는 해당 node가 실제 출제 가능한지만 검증한다.
4. 하나의 셀에 여러 출제 가능 node가 있으면 Backend 또는 프론트의 명시적인 제품 정책으로 선택한다.
5. 출제 가능한 node가 없거나 하나로 확정할 수 없으면 요청을 만들지 않는다.

금지 사항:

- Adapter 배열 순서의 첫 node 선택
- 문자열 정렬 결과로 node 선택
- 근거 자료가 없는 node를 임의 선택
- 단일 node 하드코딩을 일반 매핑인 것처럼 사용

**완료 기준:** 같은 진단 snapshot과 같은 교사 선택은 catalog 정렬 순서와 무관하게 동일한 `skill_node_id`를 생성한다.

### BE-PG-09. 지원 범위와 프론트 API 정합

**우선순위:** P0

Backend는 프론트가 출제할 수 있는 영역과 node를 현재 실제 generation capability에 맞춰 반환한다.

권장 응답 정보:

```json
{
  "areaTag": "language",
  "typeTag": "concept",
  "skillNodeId": "language.grammar.phonological_change",
  "diagnosisStatus": "weak",
  "generationAvailable": true,
  "unavailableReason": null,
  "capabilityVersion": "..."
}
```

미지원 셀은 숨기기보다 `generationAvailable=false`와 안정된 reason code로 반환한다.

**완료 기준:** 사용자가 현재 미지원인 독서·문학·화법과 작문·매체 셀에서 문제 생성 요청을 만들 수 없다.

### BE-PG-10. Review API의 부분 성공 표현 보완

**우선순위:** P1

Step 3 review API는 생성된 문항만 보여주는 데서 끝나지 않고 요청 대비 결과를 정직하게 표시해야 한다.

추가 또는 확인할 필드:

- `requestedCount`
- `generatedCount`
- `reviewRequiredCount`
- `unverifiableCount`
- `droppedCount`
- `unstartedCount`
- `status`
- `stopReason`
- 공개 가능한 `failureReasons`

기존 정답 및 검증 상태 매핑은 유지한다.

**완료 기준:** 부분 성공 요청에서 강사가 생성된 문항을 검토할 수 있고 부족한 수량과 사유도 함께 확인할 수 있다.

### BE-PG-11. Revision API 연동 정책

**우선순위:** P2

AI revision API가 존재하더라도 v1 자동 연동 대상으로 간주하지 않는다. 다음 정책을 먼저 확정한다.

- Backend 교사 수정과 AI revision의 소유권
- `base_revision_no` 낙관적 잠금
- 수정 후 전체 재검증 의무
- `verification_unavailable`의 발행 차단
- 원본·수정본·롤백본 보존
- language 외 문항 수정 요청 차단

**완료 기준:** 동시 수정 충돌, 수정 후 재검증 실패, 롤백 시나리오를 포함한 계약 테스트가 통과한다.

### BE-PG-12. 관측성과 운영 복구 정보 추가

**우선순위:** P1

다음 ID를 로그와 메트릭에 함께 남긴다.

- Backend `request_id`
- `child_execution_id`
- Adapter execution ID
- AI `job_id`
- AI canonical `execution_id`
- AI `set_id`
- `X-Request-Id`
- `Idempotency-Key`의 비민감 해시

필수 메트릭:

- child phase별 체류 시간
- polling 횟수와 `Retry-After` 적용 횟수
- child observation deadline 초과 수
- 부분 성공 및 dropped 비율
- Kafka 중복 이벤트 차단 수
- 진단 실패 및 generation capability 차단 수

**완료 기준:** Backend 요청 ID 하나로 Kafka 이벤트, Adapter 호출, AI Job 및 review 결과를 연결해 추적할 수 있다.

## 4. Adapter 팀에 전달할 필수 작업

다음 항목은 Backend 코드가 아니라 Kafka–HTTP Adapter의 소유다. Backend 완료 여부와 분리해 관리한다.

### AD-PG-01. 실제 AI 결과 회수 순서 수정

```text
POST /v1/problems
  → GET /v1/problems/{job_id}
  → terminal 결과의 set_id 확보
  → GET /v1/problems/{set_id}/items
  → GET /v1/problems/{set_id}/items/{slot_index} 최대 20회
```

- `/v1/problems/{job_id}/items` 경로를 사용하지 않는다.
- summary 목록에 문항 본문이 있다고 가정하지 않는다.
- slot 상세 N+1 호출은 동시성 상한을 두고 수행한다.
- 일부 상세 조회 실패 시 성공 상세를 버리지 않고 부분 성공으로 정규화한다.

### AD-PG-02. 실행 식별자 보존

- POST 응답의 `job_id`와 canonical `execution_id`를 영속 저장한다.
- GET 응답에서 새로 생성되는 `execution_id`로 POST 값을 덮어쓰지 않는다.
- terminal 응답의 `set_id`를 child execution에 연결한다.

### AD-PG-03. timeout과 관찰 deadline

- POST read timeout: 300초
- child 전체 observation deadline: 21분
- deadline은 재시도와 polling을 포함한 단조시계 기준 총 경과시간으로 계산
- 21분을 AI Job 자체 timeout으로 오해하지 않음
- 관찰 중단 후에도 AI Job의 영속 상태는 이후 재조회 가능해야 함

이 값은 2026-08-13 완료 명세를 우선하며 이전 확정안의 5분 또는 5분 30초 표현을 대체한다.

### AD-PG-04. polling 종료 판정

- HTTP 성공 여부를 먼저 확인한다.
- 종료 여부의 유일한 정본은 `data.status`다.
- `Retry-After`는 비종단 상태에서 다음 polling 간격을 정하는 advisory 값으로만 사용한다.
- 헤더가 없다는 이유로 terminal로 판단하지 않는다.
- 잘못된 status 또는 계약 위반 응답은 명시적인 Adapter 계약 오류로 처리한다.

### AD-PG-05. 문항 정규화

- AI 원본 `answer`를 보존한다.
- `correct_option_index = answer.correct_no - 1`을 함께 제공한다.
- 네 가지 검증 상태를 Backend 계약으로 매핑한다.
- `dropped`의 `item=null`을 허용한다.
- `status_counts`, `failure_reason`, 원문 result를 함께 보존한다.
- 문항 상세를 얻지 못한 slot을 임의 문항으로 생성하지 않는다.

### AD-PG-06. 결과 Outbox

- 결과 저장과 Outbox 생성을 같은 DB 트랜잭션으로 처리한다.
- `(child_execution_id, terminal_phase)`를 멱등 처리한다.
- Kafka 재발행 시 기존 `event_id`를 재사용한다.

## 5. AI 팀 선행조건

다음 항목은 Backend나 Adapter가 추정해서 채우지 않는다.

### AI-PG-01. 기준 버전 재선언

- catalog가 포함된 `a4af8dd` 이상 커밋 또는 승인 태그를 기준으로 선언
- API 계약 버전과 catalog/capability 버전을 분리할 경우 각각 명시

### AI-PG-02. 비종단 Job 복구 증거

- `queued`, `running` 상태에서 cache loss가 발생한 경우의 조회 테스트
- 실제 subprocess 또는 container 재기동 테스트
- 재시작 후 상태·`job_id`·canonical `execution_id`·결과 조회 일관성 검증

완료 세트의 cache clear 테스트만으로 전체 재시작 복구 완료를 선언하지 않는다.

### AI-PG-03. Versioned generation capability 제공

최소 필드:

```text
capability_version
skill_node_id
generation_ready
supported_type_tags
required_source_kind
supported_item_formats
max_count
unavailable_reason
```

57-node curriculum catalog 자체는 교육과정 정본으로 사용할 수 있지만 출제 가능 여부 정본으로 사용하지 않는다.

### AI-PG-04. HTTP fixture 제공

- Job의 모든 terminal·nonterminal phase
- `Retry-After` 존재·부재 응답
- items summary
- `verified`, `needs_review`, `verification_unavailable`, `dropped` slot 상세
- 부분 성공, 성공 0건, expired, 실패 응답
- `dropped`의 `item=null` 사례

### AI-PG-05. 계약 문구 보완

- polling 종료 정본은 `data.status`
- `Retry-After`는 advisory
- items 경로의 식별자는 `set_id`
- 목록에는 본문이 없으며 slot 상세 조회가 필요
- GET에서 반환되는 새 `execution_id`는 canonical 실행 ID가 아님

## 6. 테스트 계획

### 6.1 Backend 단위 테스트

- 부모 요청 생성 시 child N개 저장
- child별 멱등키 안정성
- child 상태 전이와 종단 불변식
- 순서가 다른 child 완료 이벤트의 부모 상태 동일성
- `dropped item=null`에서 문항 미생성
- 요청·처리·미처리 수량 보존
- AI 진단 원본 5×4 grid 저장 및 조회
- 미지원 node 출제 차단

### 6.2 계약 테스트

- Backend Kafka 요청 fixture ↔ Adapter consumer schema
- Adapter 결과 fixture ↔ Backend result listener
- `status_counts` 합계와 slot 결과 일치
- 공개 reason code만 Backend에 전달
- canonical `execution_id`가 GET 응답 값으로 덮어써지지 않음
- AI capability version 불일치 시 요청 차단

### 6.3 장애 테스트

- 동일 requested event 중복 소비
- 동일 terminal event 중복 소비
- Kafka 발행 직전 Adapter 재시작
- 일부 slot detail timeout
- Adapter 21분 관찰 deadline 초과
- AI 429·502·503·504 후 복구
- `Retry-After` 헤더 유실
- AI 응답 status 미등록 값
- Backend 결과 반영 중 DB 장애와 재소비

### 6.4 실제 E2E

다음 경로를 실제 프로세스로 검증한다.

```text
Frontend 요청
  → Backend 부모 요청·child·Outbox
  → Kafka
  → Adapter
  → AI POST·polling·set/items/detail
  → Adapter 결과 Outbox·Kafka
  → Backend 결과 투영
  → review API
```

최초 필수 시나리오:

1. 단일 child 전체 성공
2. 다중 child 전체 성공
3. 다중 child 부분 성공
4. `dropped item=null` 포함
5. 일부 slot detail 조회 실패
6. 동일 요청 및 결과 이벤트 중복 전달
7. AI 완료 후 Adapter 재시작
8. AI 비종단 상태 중 재시작
9. 21분 observation deadline 초과 후 재조회

## 7. 영역 확대 게이트

현재 안전한 시연 범위는 `language.grammar.phonological_change`로 유지한다.

새 영역 또는 node는 다음 조건을 모두 만족할 때만 활성화한다.

- generation capability에서 `generation_ready=true`
- 필요한 passage·work·material 입력 계약 확정
- 성공·실패 HTTP fixture 제공
- Adapter summary 및 slot detail 회수 테스트 통과
- Backend 결과 투영 및 review API 계약 테스트 통과
- 실제 AI→Adapter→Kafka→Backend E2E에서 문항 확인
- 제품/Frontend가 해당 영역의 입력 및 화면 정책 승인

작업 완료 통보, 코드 존재, catalog 등재만으로 지원 범위를 확대하지 않는다.

## 8. 권장 티켓 분할

| 순서 | 티켓 | 소유 | 우선순위 | 선행조건 |
| --- | --- | --- | --- | --- |
| 1 | AI 계약·capability 기준 버전 고정 | BE+AI | P0 | AI-PG-01, AI-PG-03 |
| 2 | 부모–child execution DB 구조 | BE | P0 | 없음 |
| 3 | child 요청 Kafka 계약 | BE+Adapter | P0 | 1, 2 |
| 4 | Adapter set/items/detail 회수 | Adapter | P0 | AI fixture |
| 5 | Adapter timeout·21분 deadline·polling | Adapter | P0 | AI 상태 계약 |
| 6 | Backend child 결과 반영·부모 집계 | BE | P0 | 2, 3 |
| 7 | dropped·미처리 slot 투영 | BE+Adapter | P0 | 4, 6 |
| 8 | Step 1 diagnosis 입력·저장·전환 | BE+Adapter | P0 | AI diagnosis fixture |
| 9 | 진단 node 보존과 capability 검증 | BE+Adapter | P0 | AI-PG-03, 8 |
| 10 | 프론트 지원 범위 응답·선차단 | BE+FE | P0 | 1, 9 |
| 11 | 부분 성공 review API 보완 | BE | P1 | 6, 7 |
| 12 | 관측성·운영 복구 대시보드 | BE+Adapter | P1 | 3~7 |
| 13 | 실제 프로세스 E2E | BE+Adapter+AI | P0 릴리스 게이트 | 1~10 |
| 14 | Revision API 연동 | BE+AI | P2 | 기본 생성 E2E 완료 |

## 9. Backend 완료 정의

Backend 작업은 다음 조건을 모두 만족할 때 완료로 본다.

- 다중 target이 독립 child execution으로 영속화된다.
- AI `job_id`, canonical `execution_id`, `set_id`가 child별로 보존된다.
- Kafka 요청·결과의 중복 전달에도 결과가 중복 생성되지 않는다.
- 부분 성공·실패·미처리 수량이 손실 없이 review API에 반영된다.
- `dropped item=null`에서 가짜 문항을 만들지 않는다.
- Step 1은 저장된 AI 진단을 정본으로 반환하며 Backend가 재판정하지 않는다.
- 출제 가능 node가 확정되지 않은 셀은 AI 요청 전에 차단된다.
- 현재 허용한 단일 node에서 실제 AI→Adapter→Kafka→Backend→review E2E가 통과한다.
- 장애·재시도·멱등·재시작 시나리오 테스트가 통과한다.
- lint, format, 단위·계약·통합 테스트가 모두 통과한다.

## 10. BE 전달용 요약 문구

> Backend 후속 작업의 핵심은 다중 target을 child execution으로 영속화하고, Adapter 결과의 부분 성공·실패·미처리 정보를 손실 없이 부모 요청과 review API에 반영하는 것입니다. 기존 정답 인덱스와 네 가지 검증 상태 매핑은 이미 완료되어 재작업 대상이 아닙니다. Step 1은 AI diagnosis 결과를 정본으로 전환하되 기존 Backend 판정과 혼합하지 않으며, 진단의 `skill_node_id`를 보존해 출제 요청에 사용해야 합니다. 57-node catalog만으로 Adapter가 node를 임의 선택해서는 안 됩니다. 현재 시연 범위는 `language.grammar.phonological_change`로 유지하고, versioned generation capability와 실제 AI→Adapter→Kafka→Backend E2E가 통과한 node만 순차적으로 개방해 주십시오.
