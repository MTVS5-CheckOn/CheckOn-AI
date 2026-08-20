# 문제 출제 스튜디오 AI–Backend 공통 규약 확정안

- 작성일: 2026-08-12
- 대상: CheckOn Backend, Kafka–HTTP Adapter, M2 문제 출제 AI
- 문서 상태: 양 팀 승인 요청용 확정안
- 참고 문서:
  - BE 전달 원본 2건 (`4..txt` · `PROBLEM_STUDIO_AI_TEAM_HANDOFF.md`) — **저장소 미포함**
    ⚠ 종전에는 작성자 기기의 절대경로가 적혀 있었다. 다른 사람은 못 여는 경로라
    파일명만 남긴다(원본은 BE 전달 채널에 있다).
  - `docs/04_api_contract.md`
  - `docs/08_kafka_events.md`
  - `docs/policies/error_codes.md`

## 1. 최종 결정 요약

다음 세 가지를 문제 출제 스튜디오 연동의 기본 원칙으로 확정한다.

1. Step 1 진단 계산과 최종 판정은 AI가 정본이다.
2. 다중 출제 영역은 Backend 부모 요청과 child execution 구조로 저장하고 Kafka–HTTP Adapter가 AI HTTP 요청을 fan-out한다.
3. AI HTTP 요청·응답 계약의 유일한 정본은 버전 관리되는 OpenAPI 명세다.

AI 서버는 Kafka에 직접 연결하지 않는다. Adapter가 Kafka 요청 소비, AI HTTP 호출, HTTP 재시도, Job polling, 결과 정규화 및 Kafka 결과 발행을 전담한다.

## 2. 시스템별 책임 경계

### 2.1 CheckOn Backend

- 프론트 요청 검증 및 사용자 권한 확인
- 부모 문제 생성 요청과 child execution 영속화
- Transactional Outbox를 통한 Kafka 요청 발행
- Adapter 결과 이벤트의 멱등 소비
- 학생·강사·클래스 등 도메인 원본 보유
- 교사 문항 선택, 저장 세트 생성, 과제 발행
- AI 진단 결과의 저장·조회·화면 전달
- AI 결과 상태를 화면 문구로 변환

Backend는 AI 진단 결과를 자체 알고리즘으로 다시 판정하지 않는다.

### 2.2 Kafka–HTTP Adapter

- Kafka 요청 이벤트 소비
- child execution별 AI HTTP 요청 fan-out
- AI HTTP 인증정보와 base URL 보유
- HTTP timeout, retry, polling 관리
- AI 응답을 Backend Kafka 계약으로 정규화
- child execution별 결과 이벤트 발행
- 요청 및 결과 이벤트의 멱등 처리

CheckOn Backend에는 AI HTTP client 또는 AI polling scheduler를 추가하지 않는다.

### 2.3 AI 서비스

- Step 1 진단의 결정론 계산 및 최종 판정
- 문제 생성·검증 워크플로 실행
- Job, 실행 결과, 문제 세트 및 문항 결과 영속화
- OpenAPI에 정의된 HTTP API 제공
- AI 검증 상태와 공개 가능한 사유 코드 제공

AI 검증 상태는 교사 승인 또는 학생 발행 상태가 아니다.

## 3. AI–BE 협의 항목 확정

| ID | 협의 항목 | 확정 내용 |
| --- | --- | --- |
| AI-BE-01 | Job 완료 감지 | AI는 HTTP 비동기 Job을 제공하고 Adapter가 polling한다. 상태는 `queued`, `leased`, `running`, `paused`, `succeeded`, `failed`, `cancelled`를 사용한다. `PENDING`, `PROCESSING` 등의 별칭은 만들지 않는다. |
| AI-BE-02 | 다중 영역 처리 | Adapter fan-out으로 확정한다. Backend는 부모 요청 아래 child execution을 저장하며, child 하나는 `POST /v1/problems` 1회, `job_id` 1개, `set_id` 1개에 대응한다. |
| AI-BE-03 | POST 요청 정본 | AI HTTP 계약의 유일한 정본은 버전 관리되는 OpenAPI다. 구현·문서·fixture·DTO·계약 테스트는 OpenAPI에서 생성하거나 CI에서 일치 여부를 검증한다. |
| AI-BE-04 | `target_source` | Backend의 `teacher_weakness_selection`은 Backend 내부 출처값으로 유지한다. Adapter는 AI 호출 시 `teacher_manual`로 변환한다. AI enum은 `weakness_auto`, `teacher_manual`을 사용한다. |
| AI-BE-05 | 진단 소유권 | AI가 진단을 계산하고 최종 판정을 소유한다. Backend는 입력 스냅숏 제공과 결과 저장·조회·전달을 담당하며 동일 셀을 자체 규칙으로 재판정하지 않는다. |
| AI-BE-06 | 비-language 영역 | 최초 v1 운영 연동은 `area_tag=language`, `item_format=mcq`, `type_tags=fact/infer/critic/concept`만 출제를 활성화한다. 나머지 영역은 진단·표시는 가능하지만 출제 요청을 차단한다. |
| AI-BE-07 | items 계약 | Job 조회는 `GET /v1/problems/{job_id}`, 전체 문항 조회는 `GET /v1/problems/{set_id}/items`로 한다. 부분 성공도 HTTP 200으로 반환하며 성공·실패·미처리 슬롯을 모두 제공한다. |
| AI-BE-08 | HTTP 인증 | Adapter만 AI 접속정보를 가진다. TLS 위 `Authorization: Bearer` 서비스 토큰을 사용하고 개발·스테이징·운영 credential을 분리한다. 토큰은 90일 주기, 7일 중첩 기간으로 교체한다. |
| AI-BE-09 | 멱등성 | 멱등 범위는 `(tenant_alias, method, path, Idempotency-Key)`다. 같은 키와 같은 canonical payload는 최초와 같은 `202` 및 같은 `job_id`를 반환한다. 다른 payload는 `409 IDEMPOTENCY_CONFLICT`다. 멱등 레코드는 7일 보존한다. |
| AI-BE-10 | 영속성 | 운영 환경에서 Job, 요청 digest, 실행 결과, set, items 및 조회 view를 PostgreSQL에 영속화한다. 운영 배포에서 in-memory 저장소를 사용하지 않는다. 완료 결과는 30일간 재조회할 수 있어야 한다. |
| AI-BE-11 | 라우터 상태 | endpoint가 `implemented`, `deployed`, `approved`를 모두 만족해야 연동 가능 상태로 본다. 문서 또는 코드만 존재하는 endpoint는 연동 대상으로 보지 않는다. |
| AI-BE-12 | 크기 제한 | AI child 요청당 문항 수는 `1..20`, Backend 부모 요청은 target 최대 20개 및 총 문항 100개로 제한한다. 진단 스냅숏은 최대 5MB, 문제 생성 요청은 최대 512KB다. 초과 시 `413 PAYLOAD_TOO_LARGE`를 반환한다. |
| AI-BE-13 | 상호 Fixture 관리 | AI HTTP OpenAPI와 HTTP fixture는 AI 저장소가 정본이다. Kafka 요청·결과 event schema와 fixture는 Backend/Adapter 계약 저장소가 정본이다. 같은 JSON을 양쪽에서 수작업으로 복사해 독립 관리하지 않는다. |

## 4. Step 1 진단 확정 규약

### 4.1 진단 정본

- AI의 결정론 진단 결과를 유일한 판정 정본으로 사용한다.
- 초기 `cell_min_items`는 `10`으로 한다.
- `cell_min_items`와 진단 설정 버전을 결과에 함께 저장한다.
- Backend에 판정용 숫자 `10`을 별도 상수로 중복 구현하지 않는다.
- 설정이 변경되더라도 기존 결과를 소급 재해석하지 않고 새 설정 버전으로 새 진단을 생성한다.
- `tag_confirmed=false`인 학습 기록은 판정 입력에서 제외한다.
- 유효한 curriculum graph에 존재하지 않는 `skill_node_id`는 판정 입력에서 제외한다.
- 제외된 기록의 개수와 공개 가능한 제외 사유를 진단 메타에 제공한다.

### 4.2 셀 상태

`unknown`과 `no_data`는 의미가 다르므로 분리한다.

| 상태 | 조건 | 의미 |
| --- | --- | --- |
| `no_data` | 판정 가능한 유효 기록이 0건 | 측정 자료 없음 |
| `unknown` | `0 < item_count < cell_min_items` | 자료 수집 중 또는 표본 부족 |
| `ok` | 최소 표본 충족 및 취약 판정 아님 | 현재 기준 정상 |
| `weak` | 최소 표본 충족 및 결정론 규칙상 취약 | 취약 신호 존재 |

`severity`는 `weak`인 경우에만 `low`, `medium`, `high` 중 하나를 사용하고 나머지 상태에서는 `null`로 반환한다.

### 4.3 5×4 고정 그리드

AI 진단은 항상 20개 셀을 반환한다. 데이터가 없는 셀도 생략하지 않고 `no_data`로 반환한다.

- 영역 5개: `reading`, `literature`, `speech_writing`, `language`, `media`
- 유형 4개: `fact`, `infer`, `critic`, `concept`

Backend가 DB에 존재하는 셀만 동적으로 반환하는 방식은 사용하지 않는다.

### 4.4 화면 4행 병합

화면을 4행으로 유지해야 하는 경우 `language`와 `media`를 표시 단계에서만 병합한다. AI 원본 5×4 결과는 변경 없이 저장한다.

병합 셀의 상태 우선순위는 다음과 같다.

```text
weak > unknown > ok > no_data
```

- 하나라도 `weak`이면 병합 셀은 `weak`이다.
- 두 셀이 모두 `weak`이면 더 높은 `severity`를 표시한다.
- 하나가 `unknown`이고 다른 하나가 `ok`이면 `unknown`을 표시해 판정 불확실성을 숨기지 않는다.
- 병합은 화면 표현 규칙이며 Backend의 별도 진단 판정으로 취급하지 않는다.

## 5. 다중 영역 요청과 child execution

### 5.1 저장 구조

Backend는 다음 구조를 사용한다.

```text
problem_generation_request 1
 └─ problem_generation_execution N
     ├─ target_index
     ├─ area_tag
     ├─ type_tags
     ├─ requested_count
     ├─ adapter_execution_id
     ├─ ai_job_id
     ├─ ai_set_id
     ├─ status
     └─ public_failure_reason
```

부모 요청에 단일 `ai_job_id`, `ai_execution_id`, `ai_set_id`만 저장하는 구조는 다중 대상 요청에 사용하지 않는다.

### 5.2 처리 순서

1. Backend가 부모 요청과 모든 child execution을 한 트랜잭션으로 저장한다.
2. 같은 트랜잭션에서 Outbox 요청 이벤트를 저장한다.
3. Adapter가 child execution별로 AI의 `POST /v1/problems`를 호출한다.
4. Adapter가 child별 AI `job_id`, `set_id`와 결과를 정규화한다.
5. Adapter가 child별 결과 이벤트를 Kafka에 발행한다.
6. Backend가 이벤트를 멱등 반영하고 부모 상태를 다시 집계한다.

child별 `Idempotency-Key`는 다음 형식을 권장한다.

```text
problem-request:{request_id}:target:{target_index}
```

### 5.3 부모 상태 집계

| 부모 상태 | 조건 |
| --- | --- |
| `generated` | 모든 요청 문항이 처리되고 모두 사용 가능한 상태 |
| `partial_success` | 사용 가능한 문항이 1개 이상이지만 실패 또는 미처리 문항이 존재 |
| `failed` | 사용 가능한 문항이 0개 |
| `cancelled` | 요청 전체가 취소되고 더 이상 실행 중인 child가 없음 |

`rejected_insufficient`, `verification_unavailable`, 문항 `dropped`, 세트 `partial_success`는 정상적으로 확정된 도메인 결과다. 결과 계약 자체를 저장하지 못한 실행 장애가 아니면 Worker 실행 실패로 승격하지 않는다.

## 6. AI HTTP polling 및 재시도

### 6.1 polling

- `POST /v1/problems`의 응답 상태가 종단 상태면 Adapter가 즉시 Job과 items를 조회한다.
- 비종단 상태면 `Retry-After`에 따라 polling한다.
- AI의 기본 `Retry-After`는 `2`초이며 단위는 초다.
- 허용 polling 간격은 최소 1초, 최대 10초다.
- AI Job의 총 실행 상한은 5분이다.
- Adapter의 관찰 상한은 5분 30초다.
- v1에서는 AI callback 또는 webhook을 추가하지 않는다.

### 6.2 HTTP timeout

| 구간 | timeout |
| --- | --- |
| 연결 | 3초 |
| 일반 HTTP 응답 | 10초 |
| 진단 요청 | 60초 |
| AI 비동기 Job 총 실행 | 5분 |

### 6.3 HTTP 재시도

- 네트워크 오류와 `429`, `502`, `503`, `504`만 재시도한다.
- 최대 시도 횟수는 최초 호출을 포함해 3회다.
- 기본 backoff는 0.5초, 1초, 2초에 jitter를 적용한다.
- `Retry-After`가 있으면 기본 backoff보다 우선한다.
- POST 재시도는 최초 요청과 동일한 `Idempotency-Key`를 사용한다.
- 그 밖의 4xx는 재시도하지 않는다.

## 7. HTTP API 계약

### 7.1 문제 생성

```http
POST /v1/problems
```

- 요청 단위는 AI 문제 세트 한 개다.
- `area_tag`는 한 개다.
- 정상 접수는 `202 Accepted`다.
- 응답에는 `job_id`와 응답 시점의 `status`가 반드시 포함된다.
- 같은 멱등키와 같은 payload의 재요청도 최초와 동일한 `202`를 반환한다.

### 7.2 Job 조회

```http
GET /v1/problems/{job_id}
```

- 실행 상태와 도메인 결과 요약을 반환한다.
- `queued`, `leased`, `running`, `paused`에서는 `result=null`이어야 한다.
- `succeeded`에서는 유효한 도메인 결과가 반드시 존재해야 한다.
- `failed`, `cancelled`에서는 성공 결과를 반환하지 않는다.

### 7.3 문항 목록 조회

```http
GET /v1/problems/{set_id}/items
```

- 성공 문항뿐 아니라 실패·검증 불능·폐기·미처리 슬롯도 누락 없이 반환한다.
- 일부 문항 실패는 HTTP 실패가 아니라 `200 OK`와 세트 `partial_success`로 반환한다.
- 생성 결과가 0건이어도 유효한 도메인 결과가 확정됐다면 `200 OK`와 세트 `failed` 또는 `rejected_insufficient`를 반환한다.
- `failure_detail`은 외부에 전달하지 않고 공개 가능한 `failure_reason`만 전달한다.

## 8. HTTP 상태 및 오류 코드

| HTTP | 코드 | 의미 |
| --- | --- | --- |
| 200 | — | 조회 성공 또는 정상 도메인 결과 |
| 202 | — | 문제 생성 요청 접수 및 멱등 재반환 |
| 400 | `INVALID_SCHEMA` | 필수 헤더, JSON, body, enum 또는 validation 계약 위반 |
| 401 | `UNAUTHORIZED` | 인증정보 누락 또는 오류 |
| 403 | `TENANT_MISMATCH` | 요청과 리소스의 테넌트 불일치 |
| 404 | `NOT_FOUND` | 리소스 부재 또는 존재를 숨겨야 하는 참조 |
| 409 | `IDEMPOTENCY_CONFLICT` | 동일 멱등키에 다른 canonical payload 사용 |
| 410 | `RESULT_EXPIRED` | 결과 보존 기간 만료 |
| 413 | `PAYLOAD_TOO_LARGE` | 요청 개수 또는 본문 크기 제한 초과 |
| 429 | `RATE_LIMITED` | 할당량과 무관한 기술적 요청 제한 |
| 500 | `INTERNAL` | AI 서비스 내부 결함 |
| 503 | `LLM_UPSTREAM_DOWN` | 외부 모델 서비스 호출 불가 |
| 504 | `TIMEOUT` | 정해진 시간 상한 초과 |

`rejected_insufficient`, `partial_success`, 세트의 도메인 `failed`, 문항의 `verification_unavailable` 및 `dropped`는 HTTP 오류 코드가 아니다.

## 9. 인증 및 Secret 관리

- AI HTTP 호출은 사설 네트워크와 TLS를 전제로 한다.
- Adapter는 `Authorization: Bearer <service-token>`을 사용한다.
- 개발·스테이징·운영 credential을 분리한다.
- 토큰은 코드나 `.env.example`에 실제 값으로 기록하지 않는다.
- 실제 secret은 배포 환경의 secret store에 보관한다.
- 토큰 발급과 폐기는 AI 운영 주체가 담당하고 Adapter 운영 주체가 배포한다.
- 정기 rotation 주기는 90일이다.
- 무중단 교체를 위해 기존 토큰과 신규 토큰을 최대 7일간 동시에 허용한다.
- 모든 요청은 `X-Request-Id`로 AI와 Adapter 로그를 상호 추적할 수 있어야 한다.

## 10. 영속성과 보존

운영 배포에서는 다음 데이터가 프로세스 재시작 후에도 복구되어야 한다.

- Job과 상태 전이
- 요청 canonical digest와 멱등 레코드
- AI execution 식별자
- 문제 세트 결과
- 문항 결과와 공개 상태
- Job 및 set 조회에 필요한 view

보존 기간은 다음과 같다.

| 데이터 | 보존 기간 |
| --- | --- |
| 멱등 레코드 | 7일 |
| 완료 Job 조회 | 30일 |
| 문제 세트 및 items 조회 | 30일 |
| Backend에 반영된 교사 선택·저장·발행 결과 | Backend 도메인 보존 정책 적용 |

보존 기간이 지난 AI 결과 조회는 `410 RESULT_EXPIRED`로 반환한다. 운영 환경에서 in-memory 저장소를 사용해서는 안 된다.

## 11. 크기 및 개수 제한

| 대상 | 제한 |
| --- | --- |
| AI child 문제 생성 문항 수 | `1..20` |
| Backend 부모 요청 target 수 | 최대 20개 |
| Backend 부모 요청 총 문항 수 | 최대 100개 |
| 진단 스냅숏 HTTP body | 최대 5MB |
| 문제 생성 HTTP body | 최대 512KB |

제한 초과 요청은 Job을 만들기 전에 `413 PAYLOAD_TOO_LARGE`로 거절한다.

## 12. OpenAPI 및 Fixture 관리

### 12.1 HTTP 계약

- OpenAPI는 AI 저장소에서 관리한다.
- 필수 여부, enum, nullable, format, 길이, 개수 및 교차 validation 조건을 모두 명시한다.
- 구현 변경은 OpenAPI 변경을 먼저 반영한 후 진행한다.
- OpenAPI와 실제 응답이 다르면 CI를 실패시킨다.
- 연동 테스트는 승인된 OpenAPI commit 또는 tag를 기준 버전으로 고정한다.

### 12.2 버전 정책

- 선택 필드 추가처럼 기존 소비자가 무시할 수 있는 변경은 minor 버전이다.
- 필수 필드 추가, 필드 삭제, 타입 변경, 의미 변경은 major 버전이다.
- enum 추가·삭제·이름 변경은 exhaustive consumer에 영향을 주므로 major 버전으로 관리한다.
- OpenAPI 변경은 AI와 Backend 양측 승인을 받아야 확정된다.

### 12.3 Fixture 소유권

- AI HTTP request/response fixture는 AI OpenAPI 저장소가 정본이다.
- Kafka 요청·결과 fixture는 Backend/Adapter 계약 저장소가 정본이다.
- fixture는 OpenAPI example에서 생성하거나 schema validation을 통과해야 한다.
- 정상 응답뿐 아니라 400, 409, 410, 413, 429, 5xx, 부분 성공, 결과 0건 fixture를 제공한다.

## 13. 운영 연동 선행 조건

다음 항목이 완료되기 전에는 AI HTTP end-to-end 연동 완료로 판정하지 않는다.

1. `/v1/diagnosis` OpenAPI 확정 및 운영 Router 등록
2. `/v1/problems/{set_id}/items` 구현 및 배포
3. 프로세스 재시작 후 기존 `job_id`, `set_id`, items 조회 복원
4. OpenAPI와 FastAPI 구현의 자동 계약 검사
5. Backend child execution 스키마 및 상태 집계 구현
6. Adapter fan-out과 child별 멱등 처리 구현
7. Backend의 기존 자체 진단 판정 제거 또는 명시적 비활성화
8. 미지원 영역 및 `apply` 출제 요청의 프론트·Backend 선차단
9. AI HTTP 정상·부분 성공·0건·오류 fixture 제공
10. Backend–Adapter Kafka 계약 테스트 통과

## 14. 변경 적용 원칙

- 계약 변경은 OpenAPI와 계약 문서를 먼저 변경한다.
- AI, Adapter, Backend 구현은 승인된 계약 버전을 기준으로 수정한다.
- 양쪽 구현이 모두 준비되기 전에는 기능 플래그로 실제 연동을 차단한다.
- 진단 정본 전환 시 AI 결과와 Backend 기존 계산을 같은 화면에 혼합하지 않는다.
- 기존 Backend 계산은 일정 기간 관측용 shadow 비교에만 사용할 수 있으며 사용자 응답에는 노출하지 않는다.
- 상호 계약 테스트와 장애·재시도·멱등·부분 성공 테스트가 통과해야 운영 연동을 승인한다.

## 15. 승인 기록

| 구분 | 담당 | 승인 여부 | 승인일 | 비고 |
| --- | --- | --- | --- | --- |
| CheckOn Backend |  |  |  |  |
| Kafka–HTTP Adapter |  |  |  |  |
| M2 문제 출제 AI |  |  |  |  |
| 제품/Frontend |  |  |  | 지원 영역 및 화면 4행 병합 확인 |

