# 전면 PG 플립 관문 — `agent_run.run_id → ai_run` FK 순서 조사 (A+B 판정 요청)

**작성:** member-A · 2026-08-10 · **조사만 했고 아무것도 안 고쳤다**
**요청:** 선택지 넷 중 하나를 A+B가 함께 고른다. 코드·모델·마이그레이션은 판정 뒤에 손댄다.

---

## 0. 한 줄

**`STORE_BACKEND=pg`에서 상담 초안 POST가 `HTTP 500`으로 죽는다.**
SQLSTATE **`23503`** · `fk_agent_run_run_id_ai_run`.
**부모(`AI_RUN`)가 자식(`AGENT_RUN`)보다 나중에 생긴다** — 순서가 뒤집혀 있다.

⚠ **플립 이전에는 드러날 수 없었다** — `memory` 백엔드엔 FK가 없다.

---

## 1. 실측 — 무엇이 언제 일어나는가

### 1-1. 현재 타임라인 (counsel · 세 워커 공통)

| # | 자리 | 하는 일 |
| --- | --- | --- |
| ① | `composition/counsel/enqueue.py` | `execution_id = self._new_id()` — **잡 만들 때 값이 정해진다** |
| ② | `sv.enqueue(job)` → `PgJobStore.add()` | **`AGENT_RUN` INSERT** (`run_id = job.execution_id`) 💥 |
| ③ | (도달 못 함) `composition/counsel/worker.py` `_record_execution` → `record_run()` | **`AI_RUN` INSERT** |

**②에서 ③이 아직 없다** ⇒ FK 위반. `_row_from_job()`이 `run_id=job.execution_id`를 그대로 싣는다.

### 1-2. 재현 (실 LLM 호출 **0** · Fake provider)

```
STORE_BACKEND=pg  ·  POST /v1/counsel/drafts
→ HTTP 500  {"error": {"code": "INTERNAL", "message": "내부 서버 오류"}}
→ asyncpg.exceptions.ForeignKeyViolationError
   SQLSTATE 23503
   insert or update on table "agent_run" violates foreign key constraint
   "fk_agent_run_run_id_ai_run"
```

**실패 직후 행 수** — `agent_run` **0** · `ai_run` **증가 0** · `llm_call` **0**.
**전량 롤백**이라 고아 행은 없다. ⚠ 대신 **요청이 통째로 실패**한다.

⚠ 애플리케이션 층에서는 `JobStoreError("작업 등록 중 DB 오류가 발생했다")`로 감싸여
**SQLSTATE가 안 보인다** — 원인까지 가려면 `__cause__`를 여섯 겹 벗겨야 했다.

### 1-3. 세 워커가 모두 같은가 — **그렇다**

| 워커 | `execution_id` 생성 | `record_run()` 호출 |
| --- | --- | --- |
| `counsel_pack` | `composition/counsel/enqueue.py` | `worker.py` — **실행 뒤** |
| `mapping_probe` | `import_mapping/probe/enqueue.py` | 🔴 **없다(0건)** |
| `problem_generation` | `problem_generation/enqueue.py` | `assembly.py` — **실행 뒤** |

셋 다 **같은 `PgJobStore.add()`** 를 지난다 ⇒ **셋 다 같은 자리에서 죽는다.**

🔴 **`mapping_probe`는 더 나쁘다 — `record_run` 호출이 저장소 전체에 0건이다.**
그 워커의 `AI_RUN`은 **영영 안 생긴다** ⇒ 순서를 고쳐도 **probe는 별도 판정이 필요**하다.
(선택지 ①을 고르면 probe에도 선등록이 생기고, ②·③이면 probe는 `run_id`가 계속 빈다.)

### 1-4. 경로별 `AI_RUN` 생성 여부

| 경로 | `AI_RUN` |
| --- | --- |
| counsel **성공** | ✅ `_record_execution(..., swallow_errors=False)` |
| counsel **실패**(`_fail`) | ❌ `_fail`에 `record_run` **0건** — 검증 실패는 `_execute` 앞에서 끊긴다 |
| counsel **pause**(서킷) | ❌ `sv.pause()`만 부른다 |
| **0콜 실행** | ❌ **구조적으로 안 생긴다** — 근거 선검사가 LLM 호출보다 **앞**이라 `rejected_insufficient`는 워커·그래프에 **아예 안 간다**(6차 리포트 §10) |
| refine 턴 | ✅ `_record_refine_run` — **라우터**가 쓴다(잡과 다른 `execution_id`) |

⚠ **즉 「실행이 실패하면 부모가 영영 안 생긴다」** — 순서만 바꿔서는 안 되고
**실패·pause 경로에서 `AGENT_RUN`을 어떻게 둘지**가 함께 정해져야 한다.

### 1-5. 🔴 기존 PG 테스트가 왜 못 봤는가

`tests/ai/integration/test_probe_pg_roundtrip.py`가 머리말에 **그대로 적어 뒀다**:

> `ai_run→agent_run(PgJobStore.add로 유효 행 생성)을 먼저 넣는다.`

**테스트가 손으로 `AiRun` 부모를 먼저 INSERT한다.** 프로덕션에는 그 단계가 **없다**.
`tests/ai/db/test_agent_job_store.py`는 **인메모리**라 FK 자체가 없다.

⇒ **검사가 프로덕션이 안 하는 일을 대신 해 주고 있었다.** 「PG 왕복을 봤다」가 거짓 안심이었다.

---

## 2. 파급 — `ai_run.execution_id`를 가리키는 자식은 여섯

| 테이블 | 컬럼 | 언제 쓰이나 |
| --- | --- | --- |
| **`agent_run`** | `run_id` | 🔴 **enqueue 시점 — 이 안건** |
| `llm_call` | `run_id` | 실행 중(부모와 함께 기록) |
| `signal` | `run_id` | 감지 실행 뒤 |
| `draft` | `run_id` | 초안 생성 뒤 |
| `weakness_map` | `run_id` | 진단 실행 뒤 |
| `problem_set` | `run_id` | 출제 실행 뒤 |

**`agent_run`만 「실행 전」에 쓰인다.** 나머지 다섯은 실행 산출물이라 순서가 자연스럽다
⇒ **문제는 FK 자체가 아니라 `agent_run`의 생애가 다른 다섯과 다르다는 것**이다.

---

## 3. 선택지 넷 — **고르지 않았다. 비용만 적는다.**

### ① `AI_RUN`을 enqueue 전에 선등록하고 실행 뒤 갱신

| 축 | 비용 |
| --- | --- |
| 마이그레이션 | 불필요(스키마 그대로) |
| 코드 | enqueue 셋 + `RunStore`에 「선등록」 개념 신설 |
| 🔴 **충돌** | **`record_run()`이 INSERT다** — 나중에 같은 `execution_id`로 또 넣으면 PK 충돌. **UPSERT나 `update_run` 신설이 필요**하다 |
| 🔴 **의미** | 선등록 시점엔 **모델·파라미터·토큰이 없다** — `model_provider`/`model_name`/`generation_params`를 **뭘로 채울 것인가**. `null`로 두면 **#144의 「0콜인데 파라미터가 적혔다」와 정반대 방향의 거짓**(「호출 있는데 파라미터가 빈다」)이 **정상 상태**가 되어 그 가드가 무의미해진다 |
| 🔴 **실패 경로** | 잡이 실패하면 **파라미터가 빈 `AI_RUN`이 영구히 남는다** — 「실행했는데 기록이 없다」와 「등록만 하고 실행 안 했다」를 무엇으로 가를지 새 상태가 필요 |
| probe | `record_run`이 0건이라 **선등록만 있고 갱신이 영영 없다** |

### ② `agent_run.run_id`를 nullable로 두고 실행 시작 후 연결

| 축 | 비용 |
| --- | --- |
| 마이그레이션 | **필요**(컬럼 nullable + 기존 행) · 🔴 **양자 승인 파일** |
| 코드 | `_row_from_job`에서 `run_id` 제외 + 실행 시작 시 `UPDATE` 1회 |
| 의미 | **queued 잡은 아직 실행이 아니다** — 사실에 가장 가깝다 |
| 대가 | `run_id`로 조인하는 조회가 **null을 다뤄야** 한다. `AI_RUN` 없는 `AGENT_RUN`이 **정상**이 되므로 「원장 누락」 가드의 기준이 바뀐다 |
| probe | run_id가 **영구 null**로 남는다 — 그 자체는 모순이 아니지만 **probe에 원장이 없다는 사실이 드러난다**(별건 등재 후보) |

### ③ 물리 FK를 제거하고 논리 참조로 유지

| 축 | 비용 |
| --- | --- |
| 마이그레이션 | **필요**(제약 DROP) · 🔴 **양자 승인 파일** |
| 코드 | **0줄** |
| 선례 | `06_erd.md`가 **이미 논리 참조를 쓰는 자리**가 있다(`job_id`는 varchar 논리 참조 — #182 주석) |
| 대가 | 🔴 **고아 행을 DB가 안 막는다.** 지금 이 저장소가 겪은 형태(「검사가 없으면 안 보인다」)가 그대로 재연될 수 있다 ⇒ 논리 참조로 가면 **정합 검사를 함께 세우는 것이 조건** |

### ④ 잡 실행 식별자와 AI 실행 원장 식별자를 분리

| 축 | 비용 |
| --- | --- |
| 마이그레이션 | **필요**(컬럼 추가/의미 변경) · 🔴 **양자 승인 파일** |
| 코드 | 셋 이상(계약 `WorkerJob` · 세 enqueue · 원장) — **가장 크다** |
| 의미 | 🔴 **가장 정확하다** — 「잡 한 건」과 「AI 실행 한 건」은 **원래 1:1이 아니다**(refine이 이미 별도 `execution_id`를 만든다 · 재시도·재개는 잡 하나에 실행 여럿) |
| 대가 | `meta.execution_id` 계약(04)·99 ㊮의 판정과 **정면으로 얽힌다** — BE 통보 필요 |

---

## 4. A가 판정에 붙이는 관찰 (선택은 안 한다)

1. 🔴 **①의 「단순함」은 겉보기다.** `record_run()`이 INSERT라 **UPSERT/갱신 경로 신설**이
   따라오고, **선등록 행의 파라미터를 뭘로 채울지**가 #144(사용 축)의 판정을 되돌린다.
2. **②가 코드 변경이 가장 작다**(`_row_from_job` 한 줄 + UPDATE 1회). 대신 **양자 파일 + 마이그레이션**이다.
3. **③은 코드 0줄**이지만 **가드를 함께 세우는 것이 조건**이다 — 이 저장소는 최근에
   「물리 제약이 없어서 안 막힌 것」을 두 번 겪었다(둘 다 null인 읽기 모델 행 · JSON null).
4. **④만 refine을 설명한다.** 지금도 refine은 잡과 다른 `execution_id`를 만든다 —
   **1:1 가정이 이미 깨져 있고** 나머지 셋은 그 사실을 남겨 둔다.
5. ⚠ **어느 쪽을 고르든 `mapping_probe`는 별도 판정이 필요하다** — 그 워커는 `record_run`을
   **한 번도 안 부른다.**

---

## 5. 판정 전까지 A가 지키는 것

- **코드·모델·마이그레이션 무접촉** — 이 문서만 추가했다.
- `STORE_BACKEND` 기본값 **그대로 memory**.
- 읽기 모델(#186·#35) 검증은 **저장소 한 축만 주입**해서 계속한다 — 전면 플립에 안 기댄다.

## 6. 준영님께 여쭙는 것

1. **넷 중 어느 축인가** — ②(nullable)와 ③(논리 참조)이 비용이 가장 작고 성격이 반대다.
2. **`mapping_probe`의 원장을 만들 것인가** — 만들면 ①이, 안 만들면 ②·③이 자연스럽다.
3. ③이면 **정합 검사의 소유**는 어디인가(A 테스트 · B 배치 · 둘 다).
