# AI–BE/Adapter 문제출제 통신 명세

- 기준 AI 커밋: `d1316992bb2e84159d3ad5a34332eb913626957c`
- 기준 브랜치: `codex/diagnosis-misconception-report-p5`
- 확정일: 2026-08-20
- 대상: Backend, Kafka–HTTP Adapter, AI FastAPI
- 문서 개정: BE 적대적 대조 질의 8건 반영

## 1. 범위와 결론

이 문서는 Step1 진단부터 Step7 수정 결과 재조회까지 Backend와 Adapter가 AI FastAPI를 호출하는 계약을 고정한다.

- AI 영상 MVP 추가 구현은 없다.
- 시연 중 AI 프로세스를 재시작하지 않는다.
- `queued`·`running` 잡의 재시작 내구성은 운영 고도화 항목이다.
- Adapter는 영역·유형 셀에서 교육과정 노드를 임의로 선택하지 않는다.
- Backend가 진단 결과의 `skill_node_id`를 보존하고 Adapter가 이를 `manual_targets`로 전달한다.
- `weakness_map.nodes`의 직렬화 순서는 node ID 사전순일 뿐 추천·심각도·출제 우선순위가 아니다.
- AI의 Kafka 참고 계약은 문항 본문을 싣지 않는 참조형 알림이다. 문항 본문은 `set_id` 기반 REST API로 조회한다.
- 이 명세는 API 계약과 서비스 흐름을 다룬다. 실 LLM 문항 품질을 보증하지 않는다.

기준 커밋의 검증 결과는 다음과 같다.

- Ruff 통과
- Mypy 514파일 통과
- offline 3,632 passed / 20 skipped / 172 deselected / 3 xfailed
- FakeProvider 기준 커리큘럼 57노드 중 56노드 HTTP 생성·게이트·PG 저장·재조회 완주
- `language.grammar.fortition` 1노드는 A 소유 redaction 오탐으로 제외

**(2026-08-21 재검증 · `origin/develop` `7301915`)** 위 다섯 줄은 기준 커밋 시점의 수치다.
현재 develop에서 다시 실행한 결과는 다음과 같다.

- Ruff 통과
- Mypy 520파일 통과
- offline 3,672 passed / 20 skipped / 172 deselected / 3 xfailed
- 57노드 중 56노드 완주와 `language.grammar.fortition` 제외는 **그대로 유효하다.** 코드로
  대조했다 — `tests/ai/integration/test_problem_router.py`의
  `_A_OWNED_REDACTION_BLOCKED_NODES`가 그 1노드만 담고, 같은 파일이
  `len(_RUNNABLE_CURRICULUM_NODES) == 56`을 단언하며, 전용 재현 검사
  `tests/ai/unit/problem_generation/test_fortition_redaction.py`가 따로 있다.
- PostgreSQL integration은 이 회차에서 **실행하지 않았다.** 기준 커밋 시점 수치를 이월하지도 않는다.

### 1.1 실 LLM 5영역 실측 `[2026-08-21 · PR #348]`

계약 검증과 별개로, 운영 GraphContext 배선(PR #347)에서 5영역이 실제로 어디까지 가는지
측정했다. **문항 품질 보증이 아니라 도달 지점 기록이다.**

- 기준 SHA `7301915` · 모델 `gpt-5.6-luna` · 벤더 공개 API
- 영역별 **1회**. 실패를 재시도로 덮지 않았다.

먼저 `language` 1회를 사전 측정했다.

| 구분 | 생성 시도 | 스키마 통과 | 최종 status | failure_reason | 저장 |
| --- | ---: | ---: | --- | --- | ---: |
| language(사전) | 3 | 2 | `dropped` | `generation_exhausted` | 0 |

그 뒤 5영역 행렬을 **별도 실행**했다. 🔴 **사전 실패는 아래 행렬로 덮이지 않는다** — 같은
영역이 회차에 따라 다른 결과를 낸다는 사실 자체가 기록이다.

| 트랙 | 영역 | 생성 시도 | 스키마 통과 | 최종 status | failure_reason | 저장 |
| --- | --- | ---: | ---: | --- | --- | ---: |
| T1 | language | 2 | 1 | `verified` | - | 1 |
| T2 | reading | 4 | 3 | `verification_unavailable` | - | 1 |
| T3 | literature | 2 | 2 | `needs_review` | - | 1 |
| T4 | speech_writing | 4 | 1 | `dropped` | `generation_exhausted` | 0 |
| T5 | media | 4 | 3 | `verification_unavailable` | - | 1 |

- `reading`·`speech_writing`·`media` 셋 다 **PR #347 이전에 막히던 합성 승인 ref 고정 지점을
  통과했다.** 그 차단은 스모크 하네스가 운영에 없는 `curriculum:<node>` 승인 ref를 주입해
  생성기를 엄격 대조 모드로 뒤집었기 때문이었다.
- `reading`·`media`는 문항 생성과 저장까지 도달했다. ⚠ 다만 최종 status는
  `verification_unavailable`이며 **`verified`가 아니다.**
- `speech_writing`은 문항 생성 4회·스키마 통과 1회까지 갔으나 `generation_exhausted`로
  `dropped`됐고 저장 0건이다. 🔴 **차단 지점이 뒤로 옮겨졌을 뿐 해소가 아니다.**
- ⚠ `speech_writing`의 `status_reason` 원문은 CLI 집계가 출력하지 않아 **미수집이다.**
  재호출로 보충하지 않았고 추측해서 적지 않는다.

## 2. 공통 HTTP 계약

### 2.1 헤더

모든 요청:

- `X-Tenant-Id`: 테넌트 식별자

모든 POST 요청:

- `X-Request-Id`: 요청 상관 추적 식별자
- `Idempotency-Key`: 논리 작업의 멱등 키

`X-Request-Id`는 응답 헤더에 echo된다. 재전송은 같은 논리 작업에 같은 `Idempotency-Key`를 사용한다.

CheckOn opaque alias를 그대로 사용할 수 있다.

- tenant: `tn_` + 소문자 16진수 32자리
- student: `st_` + 소문자 16진수 32자리
- class: `cl_` + 소문자 16진수 32자리

AI 내부에서 `tenant_id`, `student_ref`, `target_ref`는 UUID가 아니라 문자열이다. REST 계약은 비어 있지 않은 문자열을 받으며, Kafka 참고 경계는 위 alias 형식을 검증한다. 실명·연락처 대신 opaque alias만 전달한다.

### 2.2 응답 봉투

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

- 성공은 `data`를 사용하고 `error`는 `null`이다.
- 실패는 `data`가 `null`이고 `error.code`, `error.message`, 필요한 경우 `error.detail`을 사용한다.
- 실행 전 실패는 `meta.execution_id=null`일 수 있다.
- 없는 식별자를 임의 UUID나 `null`로 만들어 채우지 않는다.

## 3. 호출 순서

### Step1. 약점 진단

`POST /v1/diagnosis`

요청 본문:

```json
{
  "student_ref": "st_0123456789abcdef0123456789abcdef",
  "period": {
    "from_date": "2026-06-17",
    "to_date": "2026-08-12"
  },
  "as_of": "2026-08-12T09:00:00Z",
  "snapshot_hash": "sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc",
  "events": [
    {
      "event_id": "submission-correct-001",
      "area_tag": "language",
      "type_tag": "concept",
      "item_format": "mcq",
      "chosen_no": 1,
      "correct_no": 1,
      "skill_node_id": "language.grammar.sentence.structure",
      "correct": true,
      "occurred_at": "2026-08-01T09:00:00Z",
      "tag_confirmed": true
    },
    {
      "event_id": "submission-wrong-002",
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

위 JSON은 `DiagnosisRequestBody.model_validate(...).model_dump_json(exclude_none=True)`의 실제 출력이다.

- `chosen_no`: 1-based `1..5`. 비객관식이거나 선택 번호를 알 수 없으면 `null` 또는 생략한다.
- `correct_no`: BE가 보존한 1-based 정답 번호다. 비객관식이거나 정답 번호를 알 수 없으면 `null` 또는 생략하며, 약점 판정 축에는 사용하지 않는다.
- `misconception_tag`: 오답일 때 Step5 문항 스냅숏의 `choices[].misconception_tag`를 복사한다. 정답이면 `null` 또는 생략한다.
- BE는 저장된 `answer.correct_no`와 `chosen_no`를 대조해 `correct`를 확정한 뒤 세 값을 전송한다. 채점 책임은 정답 키를 보유한 BE에 있고, AI는 수신한 세 값의 자기 정합성을 재검증한다.
- `chosen_no`와 `correct_no`가 모두 있으면 두 번호의 일치 여부와 `correct`가 모순될 때 `400 INVALID_SCHEMA`다.
- `misconception_tag`가 있는데 `chosen_no`가 없거나, `correct=true`인데 라벨이 있으면 `400 INVALID_SCHEMA`다.
- 라벨이 이벤트 영역의 YAML 닫힌 어휘를 벗어나면 `400 INVALID_SCHEMA`다.

정상 응답:

- HTTP `200`
- `data.status`
- `data.weakness_map.graph_version`
- `data.weakness_map.taxonomy_version`
- `data.weakness_map.config_version`
- `data.weakness_map.snapshot_hash`
- `data.weakness_map.cells`
- `data.weakness_map.nodes`
- `data.misconceptions.by_area`
- `data.misconceptions.by_node`
- `data.misconceptions.excluded_missing_chosen_no`
- `data.misconceptions.excluded_missing_misconception_tag`
- `data.grid`

`data.misconceptions`는 리포팅 전용이다. `weakness_map.cells`·`nodes`·`propagated`·`overall_low` 판정에는 영향을 주지 않는다. `chosen_no`가 없는 오답 이벤트와 라벨이 없는 오답 이벤트는 집계하지 않고 각각의 제외 건수에만 반영한다.

Backend는 `weakness_map.nodes`의 키인 `skill_node_id`와 `snapshot_hash`를 원문 그대로 보존한다.

```json
{
  "language.grammar.addition": {
    "verdict": "suspect",
    "basis": ["cell:language×infer"]
  }
}
```

Adapter는 `area_tag`나 `type_affinity`로 노드를 다시 선택하지 않는다. `curriculum_nodes.json`은 검증·표시용이며 선택 정본이 아니다.

한 cell에 node가 여러 개 있으면 어떤 node를 선택할지는 Backend 제품 정책이다. 현재 AI 응답에는 추천 순위가 없다. 객체의 첫 키를 자동 선택하지 않는다. 강사 선택 또는 별도의 버전 고정 정책으로 선택한 뒤, 선택된 ID를 변경 없이 전달한다.

`nodes`에는 `suspect`, `weak_confirmed`뿐 아니라 직접 관측 결과에 따라 `ok`도 포함될 수 있다. Backend는 `verdict`를 읽지 않고 모든 키를 약점으로 취급하면 안 된다.

권장 timeout은 5초다. 실패는 `400 INVALID_SCHEMA` 또는 `409 IDEMPOTENCY_CONFLICT`로 처리한다.

### Step2. 문항 생성 요청

`POST /v1/problems`

필수 요청 본문:

```json
{
  "target_kind": "student",
  "target_ref": "student-id",
  "target_source": "teacher_manual",
  "manual_targets": ["diagnosis에서 받은 실제 skill_node_id"],
  "snapshot_hash": "diagnosis의 weakness_map.snapshot_hash",
  "taxonomy_version": "v1",
  "area_tag": "language",
  "type_tags": ["concept"],
  "item_format": "mcq",
  "count": 1,
  "requested_difficulty": "medium"
}
```

- `manual_targets`는 1건 이상이어야 한다.
- `count`는 1~20이다.
- `requested_difficulty`는 `low | medium | high`이며 선택 필드다.
- 영역에 따라 §4의 `passage` 또는 `work_selection`을 추가한다.
- POST read timeout은 300초로 설정한다.

정상 응답:

- HTTP `202`
- `data.job_id`
- `data.status`
- `meta.execution_id`

Adapter는 `job_id`와 `execution_id`를 응답 즉시 영속 저장한다. `202` 응답에는 `set_id`가 없다.

진단에서 문제 생성까지 동일하게 보존하는 값:

- 선택된 `skill_node_id` → `manual_targets[]`
- `weakness_map.snapshot_hash` → `snapshot_hash`
- `weakness_map.taxonomy_version` → `taxonomy_version`

`graph_version`과 `config_version`은 문제 생성 POST 필드가 아니다. body에 추가하면 extra 필드로 거절된다. 두 값은 진단 provenance와 stale 판정용으로 Backend가 저장하되, AI 요청에 임의 필드로 추가하지 않는다.

멱등 규칙:

- 같은 `Idempotency-Key` + 같은 본문: 저장된 `202` 재반환, 잡 재생성 없음
- 같은 `Idempotency-Key` + 다른 본문: `409 IDEMPOTENCY_CONFLICT`

문 앞 거절:

| 조건 | HTTP/code | `detail.reason` |
| --- | --- | --- |
| `target_source=weakness_auto` | `400 INVALID_SCHEMA` | `weakness_auto_not_wired` |
| `type_tags`에 `apply` 포함 | `400 INVALID_SCHEMA` | `type_tag_not_supported` |
| 영역별 자료 요청 조합 불일치 | `400 INVALID_SCHEMA` | `source_procurement_not_implemented` |

문 앞 거절은 잡·AI_RUN을 생성하지 않는다. 위반이 겹치면 `type_tag` 검사가 자료 조달 검사보다 먼저 관측된다.

### Step3. 잡 상태 조회

`GET /v1/problems/{job_id}`

정상 응답:

- HTTP `200`
- `data.job_id`
- `data.status`
- 종단 성공 시 `data.result.set_id`
- 비종단 시 `Retry-After` 헤더

| `data.status` | 구분 | Adapter 동작 |
| --- | --- | --- |
| `queued` | 비종단 | `Retry-After` 후 재조회 |
| `running` | 비종단 | `Retry-After` 후 재조회 |
| `succeeded` | 종단 성공 | `data.result.set_id`를 저장하고 Step4로 이동 |
| `failed` | 종단 실패 | 관찰 종료 및 실패 처리 |
| `cancelled` | 종단 실패 | 관찰 종료 및 실패 처리 |

종료 판정의 정본은 `data.status`다. `Retry-After`는 다음 polling 시점의 advisory이며, 헤더 부재를 종료 신호로 사용하지 않는다.

Adapter의 전체 child 관찰 상한은 21분이다. 이는 Adapter 정책이며 AI에는 전체 deadline이 없다.

비종단 관찰 중 AI 프로세스가 재시작되거나 API view cache가 유실되면 현재 `404`가 발생할 수 있다. 영상 시연 중 이 구간에서 AI 프로세스를 재시작하지 않는다.

### Step4. 문항 슬롯 목록 조회

`GET /v1/problems/{set_id}/items`

`job_id`가 아니라 종단 job 응답에서 얻은 `set_id`를 사용한다.

응답 필드:

- `data.set_id`
- `data.status_counts.verified`
- `data.status_counts.needs_review`
- `data.status_counts.dropped`
- `data.status_counts.verification_unavailable`
- `data.items[].slot_index`
- `data.items[].item_id`
- `data.items[].status`
- `data.items[].current_revision_no`
- `data.items[].review_reason`
- `data.items[].failure_reason`

목록 응답에는 `stem`, `choices`, `answer` 등 문항 본문이 없다.

### Step5. 문항 슬롯 상세 조회

`GET /v1/problems/{set_id}/items/{slot_index}`

`count`가 최대 20이므로 Adapter는 목록의 각 `slot_index`를 최대 20회 조회한다.

응답 필드:

- `data.set_id`
- `data.job_id`: 캐시가 있을 때만 존재
- `data.slot_index`
- `data.item_id`
- `data.status`
- `data.current_revision_no`
- `data.available_actions`
- `data.item`
- `data.cross_solve`
- `data.verification`
- `data.revisions`
- `data.review_reason`
- `data.failure_reason`

`data.item` 주요 필드:

- `area_tag`
- `type_tag`
- `item_format`
- `skill_node_id`
- `stem`
- `choices[]`
- `choices[].why_wrong`
- `choices[].misconception_tag`
- `answer.correct_no`
- `rationale`
- `evidence[]`

`data.available_actions`가 `refine`을 포함할 때만 수정 버튼을 활성화한다. Backend가 영역을 하드코딩해 수정 가능 여부를 결정하지 않는다.

정답 선지의 `why_wrong`·`misconception_tag`는 `null`이고, 오답 네 선지는 두 필드가 모두 비어 있지 않다. BE는 학생 제출의 `chosen_no`로 선택한 `choices[].no`를 찾고 그 선지의 `misconception_tag`를 다음 Step1 진단 이벤트에 복사한다.

전체 응답 예시 — `tests/ai/contract/fixtures/http/get_problem_items.detail.json`의 실제 HTTP 산출:

```json
{
  "data": {
    "set_id": "00000000-0000-4000-8000-000000000050",
    "job_id": "00000000-0000-4000-8000-0000000000b0",
    "slot_index": 0,
    "item_id": "00000000-0000-4000-8000-000000000010",
    "status": "needs_review",
    "current_revision_no": 0,
    "available_actions": ["refine"],
    "item": {
      "area_tag": "language",
      "type_tag": "concept",
      "item_format": "mcq",
      "skill_node_id": "grammar.sentence-structure",
      "stem": "문장 성분의 개념과 종류를 설명한 것으로 옳은 것을 고르시오.",
      "choices": [
        {
          "no": 1,
          "text": "문장 구조 선택지 1",
          "why_wrong": null,
          "misconception_tag": null
        },
        {
          "no": 2,
          "text": "문장 구조 선택지 2",
          "why_wrong": "2번은 문법 근거와 다르다.",
          "misconception_tag": "application_target_substitution"
        },
        {
          "no": 3,
          "text": "문장 구조 선택지 3",
          "why_wrong": "3번은 문법 근거와 다르다.",
          "misconception_tag": "application_target_substitution"
        },
        {
          "no": 4,
          "text": "문장 구조 선택지 4",
          "why_wrong": "4번은 문법 근거와 다르다.",
          "misconception_tag": "application_target_substitution"
        },
        {
          "no": 5,
          "text": "문장 구조 선택지 5",
          "why_wrong": "5번은 문법 근거와 다르다.",
          "misconception_tag": "application_target_substitution"
        }
      ],
      "answer": {"correct_no": 1},
      "rationale": "승인된 문법 근거에 따르면 1번이 옳다.",
      "evidence": [
        {
          "kind": "grammar_rule",
          "ref": "grammar:rule-1",
          "quote": null
        }
      ]
    },
    "cross_solve": {
      "chosen": 1,
      "reasoning": "문법 근거를 독립적으로 확인했다.",
      "confidence": 0.95,
      "multiple_answers_possible": false,
      "target_skill_node_id": "grammar.sentence-structure",
      "measured_skill_node_id": "grammar.sentence-structure",
      "aligned": true,
      "alignment_confidence": 0.95,
      "alignment_reason": "목표 문법 노드와 일치한다."
    },
    "verification": {
      "rule_validation": "passed",
      "blind_cross_solve": "passed",
      "release_decision": "needs_review"
    },
    "revisions": [],
    "review_reason": "manual_target_first",
    "failure_reason": null
  },
  "error": null,
  "meta": {
    "execution_id": "00000000-0000-4000-8000-0000000000e0",
    "versions": {
      "pipeline": "0.1.0",
      "engine": "problem-generation-0.1",
      "schema": "0.1",
      "contract": "0.1",
      "threshold": null,
      "prompt": "v6",
      "graph": "curriculum-five-area-v1",
      "taxonomy": "v1",
      "verify_config": "verify-config.v1",
      "difficulty_calib": null
    }
  }
}
```

캐시 유실 시 `data.job_id`는 키 자체가 생략된다. `null`이나 임의 UUID를 기대하지 않는다. Adapter는 Step2에서 저장한 `job_id`를 사용한다.

`dropped` 슬롯은 `item_id=null`, `item=null`이다. Backend나 Adapter가 대체 문항을 만들지 않는다.

AI terminal Kafka 참고 이벤트에는 dropped slot이나 문항 본문을 직접 싣지 않는다. 이벤트는 `job_id`, `execution_id`, `set_id`, `result_status`, `result_ref`를 전달하고, Adapter가 REST 목록·상세에서 dropped metadata를 읽는다.

Adapter→Backend normalized event만으로 read model을 완성할 경우에는 최소한 `slot_index`, `status=dropped`, `item_id=null`, `failure_reason`, `failure_detail`, `status_counts`를 보존해야 한다. `status_counts`만으로는 어느 slot이 dropped인지 재구성할 수 없다. normalized event의 최종 구조는 BE·Adapter 소유 정책이다.

### Step6. 문항 수정

`POST /v1/problems/{set_id}/items/{slot_index}/revisions`

요청 본문:

```json
{
  "base_revision_no": 0,
  "revision_kind": "ai_refine",
  "instruction": "수정 지시"
}
```

- `base_revision_no`는 0 이상의 정수다.
- `revision_kind`는 현재 `ai_refine`만 지원한다.
- `instruction`은 비어 있을 수 없다.
- extra 필드는 허용하지 않는다.
- `ai_refine`은 `language`·`reading`·`literature`·`speech_writing`·`media` 5영역을 모두 지원한다.
- `job_id`는 필요하지 않다.
- 캐시 유실 후에도 PG의 `problem_set.request` 원 요청 정본을 사용한다.

AI revision API는 준비·검증돼 있다. 생성 문항 조회까지만 촬영하는 영상에서는 BE·Adapter revision 연결을 후속으로 둘 수 있다. 문항 수정과 이력 확인을 영상에 포함하면 revision 연결도 영상 MVP 필수다. 이는 AI 기술 제약이 아니라 제품 시연 범위 결정이다.

정상 응답:

- HTTP `200`
- `data.set_id`
- `data.slot_index`
- `data.revision`
- `data.current_revision_no`
- `data.verification`

> 🔴 **(2026-08-20) 영역 제한이 없어졌다.** 종전에는 `language` 문항만 수정할 수 있어
> **출제는 5영역인데 수정은 하나뿐인 구멍**이 있었다. 수정 컨텍스트가 생성 경로와 같은
> 축으로 갈리고, 생성·저작물 트랙은 **원 문항이 이미 승인받은 근거만 재사용**한다.
> ⚠ 새 근거를 지어내는 것은 여전히 막힌다 — 허용 집합이 원 문항의 것으로 닫혀 있어
> `R-1:근거_참조_불일치`가 잡는다. `teacher_direct`·`rollback`·교체·삭제는 **여전히 미구현**이다.

실패:

| 조건 | HTTP/code | 처리 |
| --- | --- | --- |
| `ai_refine` 외 revision kind | `400 INVALID_SCHEMA` / `revision_kind_not_implemented` | 요청 수정 |
| ~~language 외 영역~~ | ~~`400 INVALID_SCHEMA` / `revision_area_not_implemented`~~ | 🔴 **2026-08-20 해소 — 5영역 전부 수정된다.** 이 행은 이력으로 남긴다 |
| 슬롯 부재 또는 `item=null` | `404 NOT_FOUND` | 수정 중단 |
| 수정 진행 중 | `409 REVISION_CONFLICT` / `revision_in_progress` | 잠시 후 재시도 |
| 오래된 base revision | `409 REVISION_CONFLICT` / `stale_base_revision` | `current_revision_no`로 재시도 |
| 같은 키에 다른 요청 | `409 IDEMPOTENCY_CONFLICT` | 새 논리 작업 키 발급 |

### Step7. 수정 결과 재조회

Step5를 다시 호출해 다음을 확인한다.

- `current_revision_no`가 정확히 1 증가
- `revisions[]`에 새 revision 누적
- 상세 조회 자체는 추가 LLM 호출을 만들지 않음

## 4. 영역별 자료 요청

허용 조합은 정확히 일치해야 한다.

| `area_tag` | `passage` | `work_selection` | 근거 종류 |
| --- | --- | --- | --- |
| `language` | 생략 | 생략 | 어문규범 또는 사전 |
| `reading` | 필수 | 생략 | 생성 지문 |
| `literature` | 생략 | 필수 | 승인 저작물 원문 발췌 |
| `speech_writing` | 필수 | 생략 | 생성 담화·작문 자료 |
| `media` | 필수 | 생략 | 생성 매체 자료 |

- `language`에 `passage`나 `work_selection`을 보내면 `400`이다.
- `passage`와 `work_selection`을 동시에 보낼 수 없다.
- `passage.area_tag`는 상위 요청의 `area_tag`와 같아야 한다.
- 모든 하위 모델은 extra 필드를 거절한다.

### 4.1 reading

```json
{
  "passage": {
    "area_tag": "reading",
    "domain": "humanities",
    "topic_hint": "선택 주제",
    "word_count": 900,
    "sentence_complexity": "standard",
    "paragraph_count": 4,
    "banned_topics_version": "pg-banned-v1"
  }
}
```

- `domain`: `humanities | social | science | tech | art | fusion`
- `word_count`: 0보다 큰 정수
- `sentence_complexity`: `basic | standard | advanced`
- `paragraph_count`: 2~6
- `topic_hint`: 선택 필드이며 값이 있으면 비어 있을 수 없음

### 4.2 literature

```json
{
  "work_selection": {
    "genre": "modern_novel",
    "era": "선택 시대",
    "concept_keywords": ["서술 방식"]
  }
}
```

- `genre`: `classical_poetry | modern_poetry | modern_novel`
- `era`: 선택 필드이며 값이 있으면 비어 있을 수 없음
- `concept_keywords`: 중복 없는 비어 있지 않은 문자열 배열, 기본값 `[]`
- 작품 생성 요청이 아니라 승인된 만료 저작물 원문 선택 조건이다.

### 4.3 speech_writing

외부 필드명은 `source_request`가 아니라 `passage`다.

```json
{
  "passage": {
    "area_tag": "speech_writing",
    "source_kind": "presentation",
    "topic_hint": "선택 주제",
    "banned_topics_version": "pg-banned-v1"
  }
}
```

`source_kind`: `presentation | writing_draft | writing_sources`

### 4.4 media

외부 필드명은 `source_request`가 아니라 `passage`다.

```json
{
  "passage": {
    "area_tag": "media",
    "source_kind": "paired",
    "topic_hint": "선택 주제",
    "banned_topics_version": "pg-banned-v1"
  }
}
```

`source_kind`: `single | paired`

### 4.5 금지 주제 버전

정본은 `pg-banned-v1`이다. 이 값은 자료 생성 단계에서 설정과 대조된다. 다른 값을 보내면 `202` 이후 실행이 실패할 수 있다.

## 5. 식별자 수명주기

| 식별자 | 생성 주체 | 용도와 보존 규칙 |
| --- | --- | --- |
| `request_id` | BE/Adapter | 요청마다 새 값. 응답 echo로 추적 |
| `idempotency_key` | BE/Adapter | 논리 작업 단위. 같은 작업 재전송 시 동일 값 |
| `execution_id` | AI | POST 값이 해당 실행의 정본. GET에서 재생성하지 않음 |
| `job_id` | AI | 잡 polling 경로. Adapter 영속 저장 필수 |
| `set_id` | AI | items·detail·revision 경로. 종단 job GET에서 획득 |
| `slot_index` | AI | 0-based 연속 슬롯. dropped도 자리 유지 |
| `item_id` | AI | 문항 식별자. dropped면 `null` |
| `revision_no` | AI | 다음 수정의 `base_revision_no`. 성공할 때 정확히 1 증가 |

revision POST는 별도 AI 실행이므로 새 `execution_id`를 갖는다.

## 6. 정답과 검증 상태

`answer.correct_no`는 1-based이며 1~5다. `choices[].no`도 1~5를 중복 없이 사용한다.

Adapter는 AI 원문 `answer`를 보존하면서 다음 파생 필드를 함께 제공한다.

```text
correct_option_index = answer.correct_no - 1
```

원문 `answer`를 덮어쓰지 않는다.

| AI status | Backend status |
| --- | --- |
| `verified` | `PASSED` |
| `needs_review` | `REVIEW_REQUIRED` |
| `verification_unavailable` | `UNVERIFIABLE` |
| `dropped` | `EXCLUDED` |

`status_counts`, `failure_reason`, `failure_detail`은 원문 그대로 보존한다.

`verification_unavailable`은 검증 절차가 완료되지 않은 상태이며 `needs_review`와 다르다. 발행할 수 없고 수동 예외로 우회하지 않는다.

### 6.1 Kafka payload와 결과 재조회

AI는 최대 20개 문항 본문을 단일 Kafka 이벤트에 싣지 않는다. Kafka 참고 계약은 `stem`, `choices`, `rationale`, `evidence`, `passage_text`가 결과 이벤트에 포함되면 실패한다.

- 현재 `worker_job.succeeded` fixture: 약 780바이트
- 현재 HTTP slot detail fixture: 약 2.7KB

위 값은 샘플 실측일 뿐 최대 크기 계약이 아니다. 문항 문자열에는 계약상 총 byte 상한이 없으므로 “20문항 최대 N바이트”를 보장할 수 없다.

완료 결과의 영속 재조회 API:

- `GET /v1/problems/{set_id}/items`
- `GET /v1/problems/{set_id}/items/{slot_index}`

Adapter→Backend가 본문을 Kafka로 전달해야 한다면 참조형, slot 단위, byte 상한이 있는 chunk 방식 중 하나를 BE·Adapter 계약으로 정한다. broker의 `max.message.bytes`와 producer 제한도 BE·Adapter 인프라가 확정한다.

## 7. 오류 처리

| HTTP/code | 의미 | 호출자 처리 |
| --- | --- | --- |
| `400 INVALID_SCHEMA` | 요청 계약 위반 | `detail.reason`에 따라 요청 수정, 동일 요청 재시도 금지 |
| `404 NOT_FOUND` | job·set·slot 부재 또는 타 테넌트 | polling·작업 중단 |
| `409 IDEMPOTENCY_CONFLICT` | 같은 키에 다른 본문 | 새 논리 작업이면 새 키 발급 |
| `409 REVISION_CONFLICT` | revision 동시성·버전 충돌 | `detail.reason`에 따라 제한 재시도 |
| `500 INTERNAL` | AI 내부 오류 | `X-Request-Id`로 추적, 원문 예외 기대 금지 |
| `503 LLM_UPSTREAM_DOWN` | LLM 재시도 예산 소진 | 즉시 반복 호출 금지 |
| `504 TIMEOUT` | AI timeout | 즉시 반복 호출 금지 |

4xx는 필요한 `detail`을 제공한다. 5xx는 민감 정보 보호를 위해 detail을 노출하지 않는다. HTTP status만 보지 말고 `error.code`로 분기한다.

## 8. Backend·Adapter 구현 체크리스트

### 8.1 영상 MVP 필수 — Adapter

- [ ] items 경로를 `/v1/problems/{job_id}/items`에서 `/v1/problems/{set_id}/items`로 교체
- [ ] summary 응답에서 문항 본문을 추출하는 로직 제거
- [ ] `/v1/problems/{set_id}/items/{slot_index}` 상세 N+1 회수 구현, 최대 20회
- [ ] 문제 생성 POST read timeout을 300초로 설정
- [ ] 하드코딩된 `language.grammar.phonological_change` 제거
- [ ] diagnosis의 `weakness_map.nodes` 키를 `manual_targets`로 전달
- [ ] §4에 따른 영역별 `passage`·`work_selection` 조립
- [ ] `banned_topics_version=pg-banned-v1` 사용

### 8.2 영상 MVP 필수 — 운영

- [ ] 런북에 “비종단 관찰 중 AI 프로세스 재시작 금지” 명시
- [ ] 시연 절차서에 재시작 금지 구간 표시
- [ ] AI 서버와 PostgreSQL 준비 상태 확인 후 시연 시작

### 8.3 BE 연동 직전 — Adapter

- [ ] child 전체 관찰 상한 21분 적용
- [ ] 비종단 상태에서만 `Retry-After`를 polling 간격에 반영
- [ ] `job_id`·`execution_id` 영속 저장
- [ ] `correct_option_index` 파생 및 AI 원문 answer 보존
- [ ] Step5 `choices[].why_wrong`·`misconception_tag`를 손실 없이 Backend에 전달
- [ ] dropped 슬롯과 `failure_reason`·`failure_detail`·`status_counts` 보존
- [ ] terminal 참조 이벤트 또는 합의된 normalized event를 결과 Outbox와 같은 트랜잭션으로 저장
- [ ] 본문을 Kafka에 싣는다면 byte 상한·chunk 순서·재조립 규칙을 별도 계약으로 고정
- [ ] AI→Adapter→Kafka→Backend→review 화면 E2E 통과

### 8.4 BE 연동 직전 — Backend

- [ ] Step1을 AI `/v1/diagnosis` 호출로 연결
- [ ] 선택된 `weakness_map.nodes` 키와 `snapshot_hash`·`taxonomy_version` 원문 보존
- [ ] `graph_version`·`config_version`을 진단 provenance로 저장
- [ ] 복수 node 선택 정책을 명시하고 객체 순서를 우선순위로 사용하지 않음
- [ ] 수정 버튼을 `available_actions`로 판정
- [ ] `REVISION_CONFLICT`의 `current_revision_no`로 재시도
- [ ] `tag_confirmed`·`skill_node_id` 연결 보강
- [ ] 학생 제출의 `chosen_no`와 저장된 `answer.correct_no`를 1-based로 보존하고 대조해 `correct` 확정 후 세 값 전달
- [ ] 선택한 오답 선지의 `misconception_tag`를 Step1 이벤트에 복사하고 정답이면 생략
- [ ] `data.misconceptions`의 영역·노드별 빈도와 제외 건수를 별도 리포트로 저장

### 8.5 BE/Adapter 시연 범위 확장

- [ ] T2·T4·T5 영역별 실제 HTTP 요청·응답 fixture 확인
- [ ] 각 영역의 AI→Adapter→Kafka→Backend 화면 E2E 통과 후 시연 범위 확장
- [ ] AI 구현 완료와 실제 통신 검증 완료를 구분해 보고

## 9. 운영 고도화 — MVP 범위 밖

- queued/running 잡의 AI 프로세스 재시작 내구성
- 실제 프로세스·컨테이너 재시작 E2E
- `language.grammar.fortition` redaction 오탐 정정
- running·failed·cancelled job 응답 fixture
- 500·503·504 응답 fixture
- `weakness_auto` 실배선

위 항목은 영상 MVP 통신을 막지 않으며 별도 승인과 작업으로 관리한다.

## 10. Fixture와 OpenAPI 취급

- `tests/ai/contract/fixtures/http/`의 **응답 fixture**가 현재 HTTP 응답 계약의 정본이다.
- `*.request*.json`은 요청 예시이며 다음 알려진 결함 때문에 그대로 복사할 수 없다.
  - `manual_targets: ["grammar.sentence-structure"]`는 실제 curriculum node ID가 아니다.
  - `banned_topics_version: "v1"`은 실행 정본과 다르며 실제 값은 `pg-banned-v1`이다.
- 실제 node ID는 diagnosis 응답의 `weakness_map.nodes`에서 얻는다.
- 정확한 enum과 스키마는 FastAPI `/openapi.json` 및 Pydantic 계약과 대조한다.
- `WRITE_HTTP_FIXTURES=1`로 기존 계약을 덮어쓰지 않는다.
- fixture가 없는 응답을 추측해 구현하지 말고 AI 측에 요청한다.

현재 fixture가 없는 주요 형태:

- running·failed·cancelled job
- 500·503·504
- `weakness_auto_not_wired`
- `REVISION_CONFLICT`

뒤의 두 형태는 커밋된 테스트가 있지만 JSON fixture는 없다.

## 11. 인수 기준

Backend·Adapter 작업은 다음이 모두 충족되면 인수한다.

- [ ] diagnosis에서 받은 실제 node ID와 snapshot hash가 문제 생성 요청까지 동일하게 전달됨
- [ ] 문제 생성 `202`의 `job_id`와 `execution_id`가 영속 저장됨
- [ ] 종료 판정을 `data.status`로 수행함
- [ ] terminal 응답에서 얻은 `set_id`로 items 목록을 조회함
- [ ] 목록 이후 각 slot detail을 조회해 실제 문항 본문을 확보함
- [ ] dropped 슬롯을 임의 문항으로 대체하지 않음
- [ ] 1-based 정답 원문을 보존하고 0-based index를 정확히 파생함
- [ ] 선택 오답의 `chosen_no`와 `misconception_tag`가 다음 diagnosis 요청까지 동일하게 전달됨
- [ ] `data.misconceptions`를 weakness 판정값과 섞지 않고 별도 리포트로 보존함
- [ ] 수정 가능 여부를 `available_actions`로 판정함
- [ ] stale revision과 revision in progress를 구분함
- [ ] tenant·request·idempotency 경계를 보존함
- [ ] 비종단 관찰 중 AI 프로세스를 재시작하지 않는 시연 런북이 있음
- [ ] AI→Adapter→Kafka→Backend→review 화면 E2E가 최소 1회 통과함

## 12. 최종 경계

### 12.1 재현 정본

- 저장소: `https://github.com/MTVS5-CheckOn/CheckOn-AI.git`
- 원격 브랜치: `codex/diagnosis-misconception-report-p5`
- full SHA: `d1316992bb2e84159d3ad5a34332eb913626957c`
- immutable tag·CI artifact: 현재 없음

```powershell
git fetch origin
git switch --detach d1316992bb2e84159d3ad5a34332eb913626957c
docker start checkon-ai-db-1
$env:STORE_BACKEND="memory"
uv run --frozen python -m ai.evaluation.pre_pr_verify
```

검증 당시 인터프리터는 `C:\verith\.venv\Scripts\python.exe`다. 브랜치 이름보다 full SHA를 재현 정본으로 사용한다.

### 12.2 책임 경계

AI 오개념 연동 계약은 기준 커밋 `d1316992bb2e84159d3ad5a34332eb913626957c`에서 고정한다. Backend나 Adapter 편의를 위해 AI 계약을 임의로 변경하지 않는다.

실제 통신을 불가능하게 만드는 BLOCKER가 코드와 fixture로 입증될 때만 소유자·최소 처방·승인 범위를 분리해 AI 변경을 요청한다.

### 12.3 확정 서명표

| 주체 | 확정 책임 | 서명 상태 | 기준 |
| --- | --- | --- | --- |
| AI / member-B 염준영 | `chosen_no`·`misconception_tag` 수신, 닫힌 어휘 검증, 별도 빈도 리포트, Step5 원문 제공 | 확정 | `d1316992bb2e84159d3ad5a34332eb913626957c` · 2026-08-20 |
| Kafka–HTTP Adapter | Step5 원문 필드 무손실 전달, 참조형 이벤트·REST 재조회 경계 유지 | AI 연동안 제시 · 상대 확인 필요 | 본 문서 §3 Step5 · §6.1 · §8.3 |
| Backend | 저장 정답과 선택 번호 대조, 선택 오답 라벨 복사, diagnosis 리포트 별도 저장 | AI 연동안 제시 · 상대 확인 필요 | 본 문서 §3 Step1 · §8.4 · §11 |

Adapter·Backend 행은 AI가 제시한 계약이며 각 소유자의 확인 전에는 상대 서명으로 간주하지 않는다. 구현 완료 판정은 §11 인수 기준의 E2E 증적으로만 한다.
