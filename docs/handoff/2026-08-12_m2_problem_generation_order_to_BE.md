# M2 출제 연동 — AI → 백엔드 회신·오더 (2026-08-12)

**B(염준영) 소유 · BE 전달본**
회신 대상: `PROBLEM_GENERATION_AI_TEAM_HANDOFF.md`(백엔드, commit `49f46cb`, 2026-08-12)
선례: `2026-08-11_be_connection_runbook.md` · `2026-08-06_redaction_layer_boundary_to_BE.md`

> 🔴 **§1을 먼저 읽어 주십시오.** 백엔드 명세 §7의 전제 하나가 사실과 다르고, 그것 때문에
> Step 3 화면이 지금 구조로는 그려지지 않습니다.
> 🔴 **§9(안 되는 것)를 §4~§8보다 먼저 보셔도 됩니다** — 되는 것만 적으면 안 되는 것을
> 결함으로 신고하시게 됩니다.

---

## 1. 먼저 정정할 것 — `payload.result`에는 문항 본문이 없습니다

백엔드 §7은 *"AI가 `payload.result` 전체를 보내면 백엔드가 저장하고 프론트 조회 API로
반환할 수 있다"* 를 전제로 1안·2안을 제시했습니다. **1안을 택해도 문항은 안 나옵니다.**

AI의 성공 산출물(`ProblemSetResult`)은 **세트 요약**입니다.

| 들어 있는 것 | 안 들어 있는 것 |
| --- | --- |
| `set_id` · `status` · `requested_count` · `processed_count` | 발문(`stem`) |
| 문항별 `item_id` · 검증 상태 · 시도 횟수 · 난이도 밴드 | 선지 5개(`choices`) |
| 폐기 사유 어휘 · 조기중단 사유 | 정답(`answer`) · 해설(`rationale`) · 근거(`evidence`) |

즉 전체 `result`를 Kafka로 싣더라도 백엔드 DB에는 *"문항 3개, 전부 검증 완료, item_id 셋"*
만 남습니다. 본문은 AI 문항 저장소에 있었고 **노출 경로가 0건**이었습니다.

**⇒ 그래서 이번에 문항 조회 API를 신설했습니다(§2-①).** 결과 이벤트는 `result_ref`까지,
본문은 REST로 가져가는 형태입니다 — 이유는 §3-③에 적었습니다.

---

## 2. 이번에 AI가 구현한 것 (2026-08-12 · 전부 테스트 통과)

### ① `GET /v1/problems/{job_id}/items` — Step 3 검토 화면의 원천 `[신설]`

```http
GET /v1/problems/{job_id}/items
X-Tenant-Id: <tenant alias>
```

```json
{
  "data": {
    "job_id": "…", "job_status": "succeeded",
    "set_id": "…", "set_status": "generated",
    "stop_reason": null, "requested_count": 1,
    "counts": { "verified": 0, "needs_review": 1, "verification_unavailable": 0, "dropped": 0 },
    "items": [
      {
        "slot_index": 0,
        "item_id": "…",
        "status": "needs_review",
        "attempt_no": 1,
        "review_reason": "manual_target_first",
        "failure_reason": null,
        "difficulty_band": "medium",
        "item": {
          "area_tag": "language", "type_tag": "concept", "item_format": "mcq",
          "skill_node_id": "…",
          "stem": "…",
          "choices": [{ "no": 1, "text": "…", "why_wrong": null }, "… 5개"],
          "answer": { "correct_no": 1 },
          "rationale": "…",
          "evidence": [{ "kind": "grammar_rule", "ref": "…", "quote": null }]
        }
      }
    ]
  },
  "error": null,
  "meta": { "execution_id": "…", "versions": { … } }
}
```

- `counts`가 화면의 **「7/1/1/1」** 입니다. 상태 4종은 백엔드 명세와 1:1입니다.
- 🔴 **`failure_detail`은 보내지 않습니다.** 그 필드는 내부 진단 문자열(구조화 파싱 오류
  원문 등)이라 강사 화면에 나가면 안 됩니다. 폐기 사유는 어휘가 고정된 `failure_reason`
  3종(`generation_exhausted`·`source_unverified`·`banned_topic`)뿐입니다. 표시 문구는
  백엔드·프론트가 소유합니다.
- ⚠ 승인·발행 상태는 여기 **없습니다.** `status`는 AI 내부 검증 상태입니다.

### ② `POST /v1/diagnosis` — Step 1 `area × type` 그리드 `[신설]`

계산은 있었지만 HTTP로 나가는 자리가 없어 Step 1이 막혀 있었습니다. 상세는 §4.

### ③ `GET /v1/problems/{job_id}` — 프로세스 캐시 의존 제거 `[수정]`

종전에는 **POST를 처리한 그 프로세스**의 인메모리 캐시에 없으면 잡 원장을 조회조차 하지
않고 404였습니다. 백엔드 §14의 *"AI 결과 영속화와 다중 인스턴스 지원 여부"* 의 실제 답이
이것이었습니다. 이제 잡 원장이 정본이고, 응답 버전은 요청 레코드에서 복원합니다.
⚠ 다만 **완전한 해소는 아닙니다** — §9-ⓑ를 보십시오.

### ④ Kafka 계약 fixture 4종 `[신설]`

백엔드 §14가 요청한 4개를, **코드가 만들고 코드가 읽는 형태**로 뒀습니다. 손으로 적은
JSON을 주고받으면 어느 쪽이 먼저 낡았는지 알 수 없습니다.

```
tests/ai/contract/fixtures/kafka/problem_generation.requested.json
tests/ai/contract/fixtures/kafka/worker_job.progress.json
tests/ai/contract/fixtures/kafka/worker_job.succeeded.json
tests/ai/contract/fixtures/kafka/worker_job.failed.json
```

그대로 복사해 백엔드 CI에 넣으시면 됩니다. AI 쪽에서는 이 파일이 실제 산출과 갈리면
CI가 죽습니다(`tests/ai/contract/test_kafka_contract.py`).

---

## 3. Kafka 계약 — 확정·오더

### ① 요청 이벤트: 그대로 받습니다. 단 이름 하나가 갈립니다

백엔드 `payload.request`의 필드는 **전부 우리 계약과 맞습니다**(값 어휘도 소문자로 일치).
갈리는 것은 셋뿐이고, AI 쪽 어댑터가 흡수합니다 — 백엔드는 고칠 것이 없습니다.

| 백엔드 | AI 계약 | 처리 |
| --- | --- | --- |
| `payload.problem_request_id` | `request_id` | AI가 매핑 (1:1이라 상관관계 유지) |
| `payload.idempotency_key` | 동일 | envelope/payload 값을 신뢰 |
| envelope `tenant_id` | 동일 | 🔴 `request` 안에 같은 값을 또 넣지 마십시오 — **envelope이 정본**입니다(Kafka key와 같아야 하므로) |

AI가 형식까지 검증하는 값: `tn_[0-9a-f]{32}` · `st_|cl_[0-9a-f]{32}` · `pg_[0-9a-f]{32}` ·
`sha256:[0-9a-f]{64}`. **대상 종류와 alias 접두가 어긋나면**(예: `student` 요청에 `cl_`)
AI가 먼저 끊습니다.

⚠ `snapshot_hash`는 **그대로 보존만** 합니다. AI는 내부적으로 자기 정규화 해시를 따로
계산하므로 **두 값은 다릅니다** — 백엔드가 대조하지 마십시오.

### ② 결과 이벤트: `worker_job.*`로 확정합니다

| 축 | 확정값 |
| --- | --- |
| event_type | `worker_job.succeeded` · `worker_job.failed` · `worker_job.cancelled` · `worker_job.progress` |
| `schema_version` | `worker-job-1` |
| `payload.worker_kind` | `problem_generation` |
| 상관관계 | `correlation_id`와 `payload.problem_request_id`를 **둘 다** 같은 값으로 싣습니다 |
| `payload.operation` | `problem_set.generate` (추가 필드입니다 — 무시하셔도 됩니다) |

🔴 **`problem_generation.*` 별칭은 쓰지 않습니다.** 워커가 셋(상담팩·매핑조사·출제)인데
M2만 다른 이름을 내면 백엔드 컨슈머가 워커마다 갈립니다.

**`result_status` 어휘 4종** — 백엔드 예시의 `completed`는 우리 어휘에 없습니다.

| 값 | 의미 |
| --- | --- |
| `generated` | 요청 문항 전부 검증 완료 |
| `partial_success` | 일부 성공 + 일부 실패/미처리 |
| `failed` | 성공 문항 0 |
| `rejected_insufficient` | 자동 개인화 데이터 부족으로 **생성 전 정상 종료** |

🔴 뒤의 셋도 **`worker_job.succeeded`로 나갑니다.** 유효한 결과를 저장했으면 실행 실패가
아닙니다(백엔드 §6.5와 같은 결론). `worker_job.failed`는 실행 자체가 깨진 경우뿐입니다.

**`error_code`는 소문자 snake_case입니다** — `worker_recovery_exhausted` ·
`problem_worker_internal` · `problem_request_missing`. 백엔드 예시의 `LLM_UNAVAILABLE`
같은 대문자가 아닙니다. 원문 저장이므로 동작에는 문제가 없지만 **화면 표기 매핑이
필요합니다.**

### ③ 🔴 §7 결정 요청: 본문은 이벤트가 아니라 REST입니다

**AI 입장은 "요약·참조는 이벤트, 본문은 REST"입니다.** 근거 셋:

1. **개인정보·크기 규약** — 이벤트 페이로드에 자유 텍스트를 싣지 않는 것이 AI 쪽 이벤트
   설계의 고정 규약입니다(`docs/08_kafka_events.md` §5). 문항 10개 본문이면 메시지가
   수십 KB이고, 토픽 보존 기간 동안 그대로 남습니다.
2. **어차피 Step 3에는 REST가 필요합니다** — §1대로 전체 `result`를 실어도 본문이 없으므로
   문항 목록·상세 화면은 REST 호출을 추가해야 합니다. 두 경로를 다 만들 이유가 없습니다.
3. 결과 이벤트에 `result_ref`가 실립니다. 조회는 `GET /v1/problems/{job_id}/items`입니다.

⇒ **백엔드 오더:** 결과 소비 시 성공 이벤트를 받으면 `job_id`로 items API를 1회 호출해
문항을 미러링해 주십시오. (현재 백엔드에 AI REST 클라이언트가 없다고 하셨으므로 이것이
추가 작업입니다.)

**만약 전체 `result`를 이벤트에 싣는 쪽으로 가야 한다면** 그건 08번 공용 문서 개정
사항이라 AI 내부 승인이 한 번 더 필요합니다 — 필요하시면 알려 주십시오. 다만 **문항
본문을 Kafka에 싣는 안에는 반대**합니다(위 1번).

### ④ 그대로 수용하는 것

- 요청 `count` 상한 **10** — AI 계약은 20까지지만 백엔드가 더 좁은 것은 문제없습니다.
- 토픽 이름·파티션 수·보존·TLS/SASL·ACL — **인프라는 백엔드 소유**입니다(BE-3, 7/15 확정).
  AI는 주입받은 값을 씁니다. AI consumer group 이름도 백엔드 컨벤션을 따르겠습니다.
- Outbox at-least-once 전제와 `(job_id, terminal phase)` 멱등 규약.

---

## 4. Step 1 — 대상·약점 확인

### 오더: `POST /v1/diagnosis`

```http
POST /v1/diagnosis
X-Tenant-Id / X-Request-Id / Idempotency-Key
```

```json
{
  "student_ref": "st_…",
  "period": { "from_date": "2026-07-01", "to_date": "2026-07-15" },
  "as_of": "2026-07-15T09:00:00Z",
  "snapshot_hash": "sha256:…",
  "events": [
    {
      "event_id": "…", "area_tag": "language", "type_tag": "concept",
      "correct": true, "occurred_at": "…", "tag_confirmed": true,
      "skill_node_id": "language.grammar.phoneme.system"
    }
  ]
}
```

- `/v1/detect`에 이미 보내시는 learning_event와 **같은 어휘**입니다.
- ⚠ `tag_confirmed=false`(AI 제안 태그)는 **받되 집계하지 않습니다.**
- ⚠ 그래프에 없는 `skill_node_id`가 오면 **400**입니다(호출자가 고칠 요청 — 재시도 금지).

### 응답의 `data.grid`가 화면의 5×4입니다

```json
"grid": {
  "areas": ["language","media","literature","reading","speech_writing"],
  "types": ["fact","infer","critic","concept"],
  "cell_min_items": 10,
  "cells": [
    { "area_tag":"language","type_tag":"infer","key":"language×infer",
      "acc":0.2,"n":20,"verdict":"weak","severity":0.6 }
  ]
}
```

**오더 4건:**

1. 🔴 **범례는 셀 축 4종입니다.** 그리드에 노드 축(`suspect`·`weak_confirmed`·
   `root_candidate`)을 섞지 마십시오 — 노드 판정은 `weakness_map.nodes`의 별도 목록이고
   축이 다릅니다("이 칸이 왜 빨간가"에 답이 둘이 됩니다).

   | 표시 | 조건 |
   | --- | --- |
   | `ok` | `verdict: "ok"` |
   | `weak` | `verdict: "weak"` — `severity`(0~1)로 농도를 가르셔도 됩니다 |
   | `unknown` (표본 부족) | `verdict: "unknown"` — 1건 이상이지만 `cell_min_items` 미달 |
   | `no_data` (제출 없음) | `verdict: null` · `acc: null` · `n: 0` |

   🔴 마지막 둘을 하나로 뭉치지 마십시오. 강사의 다음 행동이 다릅니다(*"출제해서 재보자"*
   vs *"조금 더 모으자"*). `acc`를 `null`로 주는 이유도 같습니다 — 표본 0에 `0.0`을 채우면
   **정답률 0%로 읽힙니다.**

2. **「최소 10문항」 문구는 응답의 `grid.cell_min_items`를 그대로 쓰십시오.** 화면에 10을
   박으면 임계 개정 시 조용히 갈립니다.

3. **축 목록도 응답에서 받으십시오**(`areas`·`types`). 프론트가 5영역·4유형을 자기 상수로
   들면 어휘가 두 곳에서 관리됩니다. 🔴 `apply`는 축에 **없습니다**(v1 미산출 예약값).

4. 🔴 **측정은 5행, 표시는 4행입니다** (B 확정 · 2026-08-08). 응답의 `areas`는 **측정 축**
   5개이고, 화면의 과목 행은 그것과 다릅니다 — 「언어와 매체」 한 행이 `language`·`media`
   **두 area를 묶습니다.** 출제 규격이 달라 측정은 갈라 두고, 과목 편제는 화면 축이라
   클라이언트가 묶습니다.

   | 화면 행 | 합칠 `area_tag` | 체제 |
   | --- | --- | --- |
   | 독서 | `reading` | 공통 |
   | 문학 | `literature` | 공통 |
   | 화법과 작문 | `speech_writing` | 선택 |
   | 언어와 매체 | `language` **+** `media` | 선택 |

   ⚠ 수능 체제는 **2027학년도까지** 기준입니다(2028 체제 배제). 학교 현장의 2022 개정
   교육과정은 별개 축이라 같은 분류로 묶지 마십시오.

5. 🔴 **유형 열의 화면 라벨은 클라이언트 소유입니다** (B 확정 · 2026-08-09). AI는 enum 값
   (`fact`·`infer`·`critic`·`concept`)만 보내고 한글 라벨을 싣지 않습니다 — 강사가 고칠 수
   있는 값이라 피커를 그리려면 클라이언트가 전 값의 라벨을 갖고 있어야 합니다. 라벨 정본은
   `docs/policies/taxonomy.md` §3의 표(평가원 행동 영역 긴 형)입니다.

   ⚠ **화면의 「어휘」 열이 이 4종 어디에 붙는지 확인해 주십시오.** *"어휘"* 는 AI 유형 축에
   없습니다 — 어휘 문항은 보통 `language×concept`으로 측정됩니다. 열 이름을 4종에 맞추시거나
   매핑을 명시해 주셔야 합니다.
   ⚠ 라벨에 **가운뎃점(`·`)을 넣지 마십시오** — 우리 쪽 근거 문장이 `"영역·유형"`으로
   조립해서 `"문학·어휘·개념"`처럼 읽히는 사고가 실제로 있었습니다.

### 화면의 나머지

| 화면 | 처리 |
| --- | --- |
| 학생 목록·실명 | 백엔드 소유. AI는 alias만 받습니다 |
| 「이상 신호 2건」 | `/v1/detect` 산출입니다 — **진단과 다른 축**이라 섞지 마십시오 |
| 「영역별 문항 수 12」 | §5-③ 참조 — Step 2의 `count`와 충돌합니다 |

---

## 5. Step 2 — 출제 조건

| 화면 | 확정 |
| --- | --- |
| 「5지선다」 | `item_format: "mcq"` ✅ v1은 이것만입니다 |
| 「하」 | `requested_difficulty: "low"` ✅ |

**오더 4건:**

1. 🔴 **문항 수의 정본을 하나로 정해 주십시오.** Step 1 화면의 「영역별 문항 수 12」와
   Step 2의 「7」이 충돌합니다. AI에 나가는 값은 요청의 `count` 하나뿐입니다.

2. 🔴 **4영역 출제는 `POST` 4번입니다.** AI 요청은 `area_tag`가 **1개**이고 요청 1건 =
   세트 1개 = `job_id` 1개입니다. 화면이 한 번이어도 백엔드는 N번 발행하고 N개의
   `problem_request_id`를 관리해야 합니다. Step 3의 「7/1/1/1 = 10개」도 **세트 여러 개를
   백엔드가 합친 값**입니다.

3. 🔴 **`apply` 유형을 화면에 열지 마십시오.** 요청에 실리면 AI가 문 앞에서
   **400 `type_tag_not_supported`** 로 끊고 **잡을 만들지 않습니다.** 백엔드 명세의 4종
   (`FACT`·`INFER`·`CRITIC`·`CONCEPT`)은 정확히 우리 v1 산출 축과 같으니 지금 그대로면
   문제없습니다 — 확장하실 때 걸립니다.

4. 🔴 **`language` 밖으로 나가실 때 자료 조달이 함께 옵니다.** 현재 백엔드는 `language`
   고정이라 안 걸리지만, 영역을 여시면 **즉시** 400
   (`source_procurement_not_implemented`)이 납니다.

   | area_tag | 요청에 함께 필요한 것 |
   | --- | --- |
   | `language` | 없음 (지금 이것만 쓰십니다) |
   | `reading` | `passage`(PassageRequest — 소재·분량·문단 수 등) |
   | `literature` | `work_selection`(갈래·시대·개념 키워드) |
   | `speech_writing` · `media` | 생성 자료 요청(`passage` 자리에 자료 종류) |

---

## 6. Step 3 — 초안 검토

`GET /v1/problems/{job_id}/items`(§2-①)를 쓰십시오. **오더 4건:**

1. **상태 4종은 1:1입니다** — `verified`(통과) · `needs_review`(검토 필요) ·
   `verification_unavailable`(검증 불가) · `dropped`(폐기).
2. 🔴 **「검토 필요」를 실패로 세지 마십시오.** 정상 상태이고, 세트 상태는 그대로
   `generated`입니다. 실패 쪽에 얹으면 성공률이 조용히 틀립니다. 배지 사유는
   `review_reason`에 있습니다(`manual_target_first`·`low_confidence` 등).
3. **폐기 사유는 `failure_reason` 3종만** 씁니다(§2-①). 내부 문자열은 안 나갑니다.
4. **「출제 근거」는 `item.evidence` 앵커**입니다(`kind`·`ref`·`quote`). 근거가 빈 문항은
   애초에 저장되지 않습니다 — 있으면 결함이니 알려 주십시오.

---

## 7. Step 4 — 저장 및 발행

| 화면 | 처리 |
| --- | --- |
| 문항 선택·저장 | **백엔드 소유.** AI에 「저장 확정」 개념이 없습니다 — 문항은 `item_id`로 참조하십시오 |
| 과제로 발행 | **백엔드(HITL).** AI에 발송 경로는 존재하지 않습니다 ✅ |
| 🔴 PDF 인쇄 | **AI에 조판 기능이 없습니다**(F17 · Phase 2 예약). v1은 **프론트 인쇄**로 가 주십시오. 서버 조판이 필요하면 별도 안건입니다 |

⚠ 승인된 문항을 다시 수정할 때의 승인 철회·재검증 절차는 아직 양쪽 다 미확정입니다.

---

## 8. 백엔드가 결정·수정해야 할 것 (우선순위)

| | 항목 | 왜 |
| --- | --- | --- |
| **필수** | 성공 이벤트 수신 후 items REST 호출 추가 | §3-③ — 이게 없으면 Step 3에 그릴 것이 없습니다 |
| **필수** | `result_status` 4종·`error_code` 소문자 표기 매핑 | §3-② |
| **필수** | 문항 수 정본 하나로(12 vs 7) | §5-① |
| **필수** | 4영역 = POST 4번 · 세트 합산 주체 | §5-② |
| **높음** | Step 1 진단 호출 배선(learning_events 전달) | §4 |
| **높음** | 그리드 범례 4종을 셀 축으로 · `no_data` 분리 | §4-1 |
| **높음** | 표시 4행(언어+매체 병합) · 유형 열 한글 라벨 보유 | §4-4·§4-5 |
| **높음** | 화면 「어휘」 열 ↔ `type_tag` 매핑 확정 | §4-5 |
| **높음** | 토픽 파티션·보존·consumer group·TLS 값 통보 | §3-④ |
| **보통** | `apply` 유형 화면 비노출 유지 | §5-③ |
| **보통** | PDF를 프론트 인쇄로 확정 | §7 |
| **보통** | taxonomy 스킬 노드 목록 API — **AI가 제공 가능합니다.** 필요하시면 형태를 알려 주십시오 | §14 "보통" |

---

## 9. 🔴 안 되는 것 — 연결해도 안 되거나 아직 없는 것

| # | 항목 | 실상 |
| --- | --- | --- |
| ⓐ | **AI 쪽 Kafka consumer·producer** | **없습니다.** 이번에 만든 것은 **순수 변환 계층**(이벤트 ↔ 내부 계약)뿐이고 브로커 코드·`aiokafka` 의존은 없습니다. 백엔드가 요청 토픽에 발행해도 **아직 아무도 소비하지 않습니다.** 당분간은 `POST /v1/problems`(REST)로 기동해 주십시오 |
| ⓑ | **AI 재기동 후 결과 조회** | 요청·결과 저장소 기본이 **인메모리**입니다. 잡 원장만 PG로 갈 수 있고 요청·결과는 프로세스와 함께 사라집니다. §2-③은 "캐시 없이도 잡 원장을 본다"까지고, **완전한 다중 인스턴스는 저장소 PG 전환이 선행**입니다 |
| ⓒ | **terminal outbox** | 종단 전이와 이벤트 기록을 한 트랜잭션으로 묶으려면 잡 저장소가 PG여야 합니다(인메모리에는 트랜잭션이 없습니다). Kafka 실연동은 그 전환과 같이 옵니다 |
| ⓓ | **`weakness_auto` 자동 약점 출제** | 진단 서비스가 출제 워크플로에 배선되지 않았습니다. 현재는 `teacher_manual`만 동작합니다. Step 1 진단(§4)과 Step 2 출제는 **지금은 별개 호출**이고, 강사가 그리드를 보고 목표를 고르는 흐름이라면 그대로 쓰실 수 있습니다 |
| ⓔ | **문항 수정·교체·삭제** | 계약(`ItemRevisionRequest`)은 있고 엔드포인트는 없습니다 |
| ⓕ | **`template_only`·근거 0건 경로의 완료 통지** | 상담 쪽과 같은 성질입니다 — 잡을 만들지 않는 400/즉시 반환 경로에는 통지가 발생할 자리가 없습니다. **202의 `status`가 종단이면 통지를 기다리지 말고 바로 GET** 하십시오 |

---

## 10. 확인 체크리스트

```
□ ①  성공 이벤트 → GET /v1/problems/{job_id}/items 호출 배선
□ ②  result_status 4종 · error_code 소문자 표기 매핑
□ ③  「검토 필요」를 실패로 합산하지 않는다
□ ④  failure_detail을 기대하지 않는다 (안 보냅니다)
□ ⑤  문항 수 정본 하나 · 4영역이면 POST 4번
□ ⑥  Step 1 그리드 범례를 셀 축 4종으로 (no_data 분리)
□ ⑦  그리드 표시는 과목 4행 — 언어+매체를 한 행으로 묶는다
□ ⑧  「최소 N문항」을 grid.cell_min_items에서 읽는다
□ ⑨  fixture 4종을 백엔드 CI에 복사
□ ⑩  🔴 요청 토픽에 발행해도 아직 소비자가 없다 — REST로 기동
```
