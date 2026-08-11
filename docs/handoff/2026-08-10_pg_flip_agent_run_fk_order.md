# 전면 PG 플립 관문 — `agent_run.run_id → ai_run` FK 순서 조사 (A+B 판정 요청)

**작성:** member-A · 2026-08-10(조사) · **2026-08-10 판정 반영(A+B)**
🔴 **최종 판정: ③ `fk_agent_run_run_id_ai_run` **제약만** 제거한다**(2026-08-10 · A+B).
`run_id`는 **NOT NULL 유지**, 값도 **`job.execution_id` 그대로**다.
⚠ **② nullable은 채택됐다가 구현 착수에서 반증되어 철회됐다** — §0-1.
모델·마이그레이션은 **준영님(B)**, 운영 정합 점검은 **A**다.
⚠ 이 문서는 **판정을 기록만** 한다 — 코드·모델·마이그레이션은 손대지 않았다.

---

## 0. 한 줄 — 🔴 **순서 문제가 아니다. FK가 주장하는 명제가 거짓이다.**

`STORE_BACKEND=pg`에서 상담 초안 POST가 `HTTP 500`으로 죽는다
(SQLSTATE **`23503`** · `fk_agent_run_run_id_ai_run`).

**NOT NULL FK가 주장하는 명제:**

> `AGENT_RUN`이 존재한다 ⇒ **그 잡의 `AI_RUN`이 반드시 이미 존재한다**

**실제 상태:**

| 사실 | 그때 `AI_RUN`은 |
| --- | --- |
| `queued` 잡 | **모델 실행 전** — 아직 없다 |
| `template_only` · 근거 0건 | **모델 실행 없이 종단 가능** — 영영 없을 수 있다 |
| `pause` · `fail` | **없을 수 있다** |
| `mapping_probe` | 🔴 `record_run()` 호출 **0건** — 영영 없다 |

⇒ **부모 INSERT 순서만 앞당겨서는 닫히지 않는다.** 「아직 없다」와 「영영 없다」가
**둘 다 정상**인데 제약이 「반드시 있다」를 요구한다 — **제약이 사실보다 강하다.**

⚠ **플립 이전에는 드러날 수 없었다** — `memory` 백엔드엔 FK가 없다.

---

## 0-1. 판정 — ③ **물리 FK만 제거** (A+B 확정 · ② 철회)

### 🔴 ②는 채택됐다가 **철회됐다** — 구현 프로브가 반증했다

**②(nullable)를 먼저 채택했다.** 그 근거는 아래 §0-2에 **지우지 않고** 남긴다 —
**틀린 판단이 아니라 「그 시점 정보로는 옳았던 판단」**이고, 무엇이 그것을 뒤집었는지가
다음 사람에게 필요한 정보다.

**구현에 착수하자 반례 셋이 나왔다(실측):**

| # | 반례 | 실측 |
| --- | --- | --- |
| ⓐ | **nullable이어도 FK 검사는 그대로 난다** | `_row_from_job()`이 **항상** `run_id=job.execution_id`를 넣는다. 컬럼을 nullable로 바꿔도 **값이 들어가므로** FK가 검사되고 **같은 자리에서 같은 23503**이 난다 |
| ⓑ | **NULL을 넣으면 `WorkerJob`을 복원 못 한다** | `_job_from_row()`가 `execution_id=row.run_id`로 되살린다. `run_id`가 NULL이면 **계약 필드(`execution_id: UUID`, 옵셔널 아님)를 채울 값이 없다** |
| ⓒ | **`execution_id`는 「AI_RUN 포인터」가 아니라 「실행 신원」이다** | API 응답 `meta.execution_id`로 나가고, 나중에 만들어질 **`AI_RUN`의 PK 자체**다(`AiRun.execution_id`가 primary key). 잡이 만들어질 때 **이미 정해져야 하는 값**이라 비울 수 없다 |

🔴 **ⓐ가 결정타다** — ②는 **문제를 아예 안 고친다.** 「컬럼을 비울 수 있게 한다」와
「값을 안 넣는다」는 다른 일인데, ②를 고를 때 그 둘을 같은 것으로 봤다.
⇒ ②를 실제로 쓰려면 **`_row_from_job`에서 값을 빼고**, 그러면 ⓑ·ⓒ가 무너진다.

### ✅ 최종 판정 — ③ **물리 FK만 제거**

| 항목 | 결정 |
| --- | --- |
| `fk_agent_run_run_id_ai_run` | 🔴 **제거** |
| `AGENT_RUN.run_id` NOT NULL | **유지** |
| `run_id` 값 | **`job.execution_id` 그대로 유지** |
| `SIGNAL` · `DRAFT` · `WEAKNESS_MAP` · `LLM_CALL`의 `AI_RUN` FK | **유지**(전부 실행 산출물이라 순서가 자연스럽다) |
| `PROBLEM_SET`의 `AI_RUN` FK | ⏸ **별도 등재**(이번 범위 밖) |
| `run_id` → `execution_id` **개명** | ❌ **이번엔 안 한다** — 의미상 맞지만 ORM·마이그레이션·ERD·조회·04 계약까지 범위가 커진다 |
| ④ 별도 ID 컬럼 · backfill | ❌ **안 만든다**(99 ㊮ 계열 별도 안건) |

**대신 의미를 문면으로 못 박는다** — ORM·ERD 주석에
*"`run_id`는 **AI_RUN 포인터가 아니라 잡 실행 신원**이며, `AI_RUN`이 존재하는 경우 **같은 ID로
논리 결합**된다"* 를 적는다. ⚠ **그 두 파일은 양자 승인이라 B 구현 PR의 범위**다.

🔴 **③의 대가는 그대로다** — 물리 제약을 버리므로 **고아를 DB가 안 막는다.**
그래서 **운영 정합 점검이 조건**이고, 소유·리더를 §1-4-1에 못 박았다.

---

## 0-2. 철회된 ② 채택 근거 (기록 보존)

⚠ **지우지 않는다** — 이 근거들은 **여전히 참**이다(잡 생애주기가 FK 명제보다 약하다는 것).
**틀린 것은 「그래서 nullable로 하면 된다」는 결론**이고, 반증한 것은 §0-1의 반례 ⓐ다.


**당시 근거 여섯(그대로 둔다):**

1. **잡은 있지만 `AI_RUN`은 아직 없거나 영영 없을 수 있다** — nullable은 **그 사실을 지어내지
   않고 표현**한다. 값이 없을 때 값을 만들지 않는 것이 이 저장소의 규율이다.
2. **선례가 이미 있다** — `_CachedView.execution_id`가 **같은 이유로** nullable이다
   (`template_only`·근거 0건은 워커도 LLM도 안 타서 **원장에 행이 없다**).
3. 🔴 **선등록(①)은 #01에서 걷어낸 「잡 없는 원장」 비대칭을 되살린다.**
4. **선등록 시점에는 모델·`generation_params`의 「사용 축」을 모른다** — 안 쓴 값을 적으면
   재현 키가 *"그 파라미터로 돌렸다"* 는 **없는 사실**을 말한다(#144가 없앤 방향).
5. **FK 제거(③)는 nullable로 표현 가능한 사실보다 제약을 더 많이 버린다** — 「값이 있으면
   반드시 실재하는 실행을 가리킨다」까지 함께 잃는다.
6. **④(식별자 분리)는 의미를 다시 설계하는 일**이라 플립 관문에 묶을 수 없다(§3-④).

⚠ **아래는 ②를 채택하던 시점의 판단으로 보존한다** — 당시에는 ②를 「임시방편」이 아니라
**현재 사실에 맞는 플립 해제안**으로 판단했다. 🔴 **그러나 §0-1 ⓐ에서 「값이 항상 들어간다」가
확인되어 그 결론은 철회됐다.** 지금 유효한 판정은 **③**이다.
✅ **여기서 유지되는 것은 「④를 별도 안건으로 분리한다」는 판단 하나뿐**이다.

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

### 1-4. 경로별 `AI_RUN` 생성 여부 — 🔴 **초판을 정정한다**

⚠ **초판(8/10 조사)의 이 표는 틀렸다.** *"counsel 실패·pause는 `AI_RUN`이 없다"* 라고 적었는데,
그건 `_fail`·`sv.pause()` **호출 자리만** 보고 `_execute`의 **`finally`를 안 본 것**이다.
실측하니 **counsel도 pg와 같은 `finally` 구조**다.

```python
# counsel/worker.py · problem_generation/assembly.py — 같은 형태
failed = True
try:
    ...                                   # 실행
    failed = False
finally:
    await self._record_execution(context, swallow_errors=failed)
```

| 경로 | 원장 **시도** | 결과 |
| --- | --- | --- |
| counsel 성공 | ✅ `swallow_errors=False` | 적재 실패면 **예외가 올라간다** |
| counsel **pause**(서킷 개방) | ✅ `finally`를 지난다 | 🔴 초판이 ❌라 적었다 — **틀렸다** |
| counsel **hash mismatch · worker internal** | ✅ `finally`를 지난다 | 초판이 ❌라 적었다 — **틀렸다** |
| counsel **bundle_missing · tenant_mismatch** | ❌ **정상이다** | `_execution_context` **앞**에서 죽는다 — **실행이 없는데 실행 기록을 만들지 않는다**(`test_no_ledger_before_the_execution_starts`가 고정) |
| **problem_generation 성공·실패** | ✅ 둘 다 `finally`에 도달 | ⚠ **실패 경로는 `swallow_errors=True`** |
| problem_generation **요청 부재** | ❌ | `context` 생성 **전**에 `DomainException` |
| **mapping_probe** | ❌ **호출 0건** | §1-3 |
| **0콜 실행**(근거 0건) | ❌ **구조적** | 근거 선검사가 LLM 호출보다 **앞**이라 워커·그래프에 **아예 안 간다**(6차 리포트 §10) |
| refine 턴 | ✅ `_record_refine_run` | **라우터**가 쓴다(잡과 **다른** `execution_id`) |

🔴 **「성공·실패 모두 기록된다」까지만 쓰면 거짓이다.**
정확한 표현은 **「성공·실패 모두 기록을 *시도*하지만, 실패 경로의 기록 실패는 삼킨다」**이다.

```python
except Exception:
    if not swallow_errors:
        raise
    logger.exception("실패 경로의 PG 실행 원장 적재 실패 — 원인 예외를 유지한다")
```

⚠ **삼키는 것 자체는 옳다** — 원장 적재 오류가 **원인 예외를 덮으면** 진짜 실패 사유가 사라진다.
🔴 **대가는 「실패 잡은 `AI_RUN`이 없을 수 있고, 그 누락이 조용하다」**는 것이다
(로그는 남지만 상태에는 안 남는다).

⇒ **이것이 ②가 필요한 이유를 한 겹 더 강하게 만든다** — 순서를 고쳐도 **실패 경로의 누락은
정상 상태**다. 제약이 「반드시 있다」를 요구할 수 없다.

### 1-4-1. 🔴 ③의 조건 — **생애주기 기반 원장 완전성 점검**(FK 흉내가 아니다)

물리 제약을 버리므로 **고아를 DB가 안 막는다** ⇒ 점검이 **③의 조건**이다.

⚠ **저장소에 FK를 흉내 내는 검사를 만들면 안 된다.** *"`AGENT_RUN`에는 있는데 `AI_RUN`에는
없다"* 를 **전부 red로 만들면 정상 생애주기가 red가 된다.** 갈라야 한다:

🔴 **(8/10 정정 · 준영님 지적)** 종전 표는 **코드보다 좁았고 한 줄은 틀렸다.**
`leased`가 없었고 `cancelled` 두 갈래가 통째로 빠졌으며, `failed` 무증거를 *"정상"* 이라
적었는데 **코드는 `unknown`**이다. ⇒ **판정 함수를 실측해 전 조합을 옮겨 적는다.**

| 상태 | 호출 증거 **없음** | 호출 증거 **있음** |
| --- | --- | --- |
| `queued` | `allowed_absence` | 🔴 `violation` |
| `leased` | `allowed_absence` | 🔴 `violation` |
| **`running`** | `allowed_absence` | **`allowed_absence`** ← ⚠ 유일한 예외 |
| `paused` | `allowed_absence` | 🔴 `violation` |
| `succeeded`(counsel · problem_generation) | 🔴 `violation` | 🔴 `violation` |
| `failed` | ⚠ **`unknown`** | 🔴 `violation` |
| `cancelled` — **실행 전**(`started_at` 없음) | `allowed_absence` | — |
| `cancelled` — **실행 후** | ⚠ **`unknown`** | 🔴 `violation` |
| `mapping_probe`(상태 무관) | ⚠ `separate_gap` — ㉾ | ⚠ `separate_gap` |
| 같은 `run_id`가 **남의 테넌트에** 있음(상태 무관) | 🔴 `violation` | 🔴 `violation` |
| **`AI_RUN`이 있고 논리 결합이 맞음**(상태 무관) | `ok` | `ok` |
| `AI_RUN`은 있는데 `run_id`·테넌트·capability 불일치 | 🔴 `violation` | 🔴 `violation` |

⚠ **`running`만 증거가 있어도 허용한다** — `finally`의 원장 적재 **직전**일 수 있고,
그 시점엔 「아직 안 썼다」와 「안 쓸 것이다」를 **못 가른다.**
⚠ **`failed`·실행 후 `cancelled`의 무증거가 `unknown`인 이유** — 실패 경로는
`swallow_errors=True`라 **적재 실패를 삼킨다.** 「안 불렀다」와 「부르고 기록을 잃었다」가
**증거상 같다** ⇒ **초록으로 세지 않는다**(플립을 막는다).
🔴 **호출 증거는 `AGENT_STEP.llm_call_id` 하나뿐**이다 — `result_ref`는 산출물 증거이지
호출 증거가 아니다(§ 관측 가능성 전수).

⚠ **표가 코드보다 좁으면 다음 사람이 표를 읽고 「거기까지만 본다」고 믿는다** —
그래서 **표를 코드에 맞추는 것이 이 절의 일**이다. 반대로 고치지 않는다.

🔴 **`failed`가 「호출 여부와 함께」인 이유** — 실패 경로는 `swallow_errors=True`라
**적재 실패를 삼킨다**(§1-4). 그래서 「실패해서 원장이 없다」와 「원장 적재가 조용히 실패했다」가
**상태만으로는 구분이 안 된다.** 호출 소비 여부가 그 둘을 가르는 유일한 축이다.

**소유와 리더:**

| 축 | 결정 |
| --- | --- |
| 소유 | 🔴 **A** |
| 위치 | **매 쓰기 저장소가 아니라** 별도 **PG 원장 점검** |
| 첫 리더 | **전면 PG 플립 preflight** |
| 운영 리더 | 배포 후 **운영 점검 명령/리포트** |
| ⚠ 부르지 말 것 | **BE 스케줄러가 생기기 전에는 「상시 감시」라고 안 부른다** |

⚠ **쓰기 경로에 넣지 않는 이유** — 그건 FK를 애플리케이션으로 옮기는 것이고,
**모든 쓰기에 비용을 물리면서도 「나중에 생길 원장」을 못 기다린다.** 점검은 **사후 축**이다.

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

## 3. 선택지 넷 — **③ 채택(A+B 확정 · ② 철회).** 비용 비교는 판정 근거로 남긴다.

### ① `AI_RUN` 선등록 후 갱신 — ❌ **기각**

| 축 | 비용 |
| --- | --- |
| 마이그레이션 | 불필요(스키마 그대로) |
| 코드 | enqueue 셋 + `RunStore`에 「선등록」 개념 신설 |
| 🔴 **충돌** | **`record_run()`이 INSERT다** — 나중에 같은 `execution_id`로 또 넣으면 PK 충돌. **UPSERT나 `update_run` 신설이 필요**하다 |
| 🔴 **의미** | 선등록 시점엔 **모델·파라미터·토큰이 없다** — `model_provider`/`model_name`/`generation_params`를 **뭘로 채울 것인가**. `null`로 두면 **#144의 「0콜인데 파라미터가 적혔다」와 정반대 방향의 거짓**(「호출 있는데 파라미터가 빈다」)이 **정상 상태**가 되어 그 가드가 무의미해진다 |
| 🔴 **실패 경로** | 잡이 실패하면 **파라미터가 빈 `AI_RUN`이 영구히 남는다** — 「실행했는데 기록이 없다」와 「등록만 하고 실행 안 했다」를 무엇으로 가를지 새 상태가 필요 |
| probe | `record_run`이 0건이라 **선등록만 있고 갱신이 영영 없다** |

### ② `agent_run.run_id` nullable — ❌ **채택 후 철회**(§0-1 반례 ⓐ — **문제를 안 고친다**)

| 축 | 비용 |
| --- | --- |
| 마이그레이션 | **필요**(컬럼 nullable + 기존 행) · 🔴 **양자 승인 파일** |
| 코드 | `_row_from_job`에서 `run_id` 제외 + 실행 시작 시 `UPDATE` 1회 |
| 의미 | **queued 잡은 아직 실행이 아니다** — 사실에 가장 가깝다 |
| 대가 | `run_id`로 조인하는 조회가 **null을 다뤄야** 한다. `AI_RUN` 없는 `AGENT_RUN`이 **정상**이 되므로 「원장 누락」 가드의 기준이 바뀐다 |
| probe | run_id가 **영구 null**로 남는다 — 그 자체는 모순이 아니지만 **probe에 원장이 없다는 사실이 드러난다**(별건 등재 후보) |

### ③ 물리 FK 제거(논리 참조) — ✅ **채택** · 🔴 **고아 검사 소유가 다시 열렸다 → A**

| 축 | 비용 |
| --- | --- |
| 마이그레이션 | **필요**(제약 DROP) · 🔴 **양자 승인 파일** |
| 코드 | **0줄** |
| 선례 | `06_erd.md`가 **이미 논리 참조를 쓰는 자리**가 있다(`job_id`는 varchar 논리 참조 — #182 주석) |
| 대가 | 🔴 **고아 행을 DB가 안 막는다.** 지금 이 저장소가 겪은 형태(「검사가 없으면 안 보인다」)가 그대로 재연될 수 있다 ⇒ **정합 검사를 함께 세우는 것이 조건**(§1-4-1) |
| 범위 | 🔴 **`agent_run`의 FK 하나만** — 나머지 `AI_RUN` FK는 유지한다(`PROBLEM_SET`은 별도 등재) |

### ④ 잡 실행 식별자와 AI 실행 원장 식별자 분리 — ⏸ **별도 안건(99 ㊮ 계열)**

| 축 | 비용 |
| --- | --- |
| 마이그레이션 | **필요**(컬럼 추가/의미 변경) · 🔴 **양자 승인 파일** |
| 코드 | 셋 이상(계약 `WorkerJob` · 세 enqueue · 원장) — **가장 크다** |
| 의미 | 🔴 **가장 정확하다** — 「잡 한 건」과 「AI 실행 한 건」은 **원래 1:1이 아니다**(refine이 이미 별도 `execution_id`를 만든다 · 재시도·재개는 잡 하나에 실행 여럿) |
| 대가 | `meta.execution_id` 계약(04)·99 ㊮의 판정과 **정면으로 얽힌다** — BE 통보 필요 |

⚠ **근거는 유지한다** — refine이 **이미 잡과 다른 `execution_id`를 만든다.** **1:1 가정은 이미
흔들려 있고** ②는 그 사실을 없애지 않는다. 🔴 **다만 이번 플립 관문과 분리한다** —
`04_api_contract.md` · 응답 `meta.execution_id` · 99 ㊮ · **BE 통보**가 함께 걸려 있어
**플립을 여는 일과 식별자 의미를 다시 설계하는 일은 크기가 다르다.**

---

## 4. 판정 근거로 남기는 관찰

1. 🔴 **①의 「단순함」은 겉보기였다.** `record_run()`이 INSERT라 **UPSERT/갱신 경로 신설**이
   따라오고, **선등록 행의 파라미터를 뭘로 채울지**가 #144(사용 축)의 판정을 되돌린다.
2. 🔴 **②가 「코드 변경이 가장 작다」던 계산이 틀렸다.** `_row_from_job` 한 줄로 끝나는 줄
   알았는데, 값을 안 넣으면 **`_job_from_row`의 복원이 깨지고**(§0-1 ⓑ) `execution_id`가
   **API 응답·`AI_RUN` PK로 이미 나가는 값**이라(ⓒ) **계약까지 번진다.**
   ⇒ **③이 실제로 코드 0줄**이다(제약 DROP 하나).
3. 🔴 **③을 처음엔 「제약을 더 많이 버린다」는 이유로 기각했다 — 그 비교가 틀렸다.**
   ②가 남긴다던 「값이 있으면 실재하는 실행을 가리킨다」는 **애초에 지킬 수 없는 명제**다
   (§0-1 ⓐ: 값은 항상 들어가고 그 시점에 `AI_RUN`은 없다). **버리는 것이 없었다** —
   ②는 같은 것을 **못 지키면서 컬럼만 비울 수 있게** 만들 뿐이었다.
   ⚠ 다만 **물리 제약이 없어서 안 막힌 것을 최근 두 번 겪었다**(둘 다 null인 읽기 모델 행 ·
   JSON null) ⇒ **③은 정합 검사 없이는 못 간다.**
4. **④만 refine을 설명한다** — 그 사실은 없어지지 않으므로 **별도 안건으로 남긴다.**
5. ⚠ **`mapping_probe`는 어느 쪽을 골라도 별건이다.** nullable이 되면 **enqueue 차단은
   풀리지만 `record_run()` 0건은 그대로**다 ⇒ **99 ㉾**로 분리했다(불변식 8 대상 · A 작업).

---

## 4-1. ✅ 최종 결과 — 관문 셋 통과 (2026-08-11 실측)

| 관문 | 결과 |
| --- | --- |
| **G1** 물리 FK 제거 | ✅ ORM `run_id`: `nullable=False` · **FK 0건** · 타입 `Uuid` 유지 · 실 PG `pg_constraint`에 `fk_agent_run_run_id_ai_run` **0건** · `alembic_version = 0009_drop_agent_run_ai_fk` · **나머지 다섯 FK 유지**(ORM·실 PG 양쪽에서 각각 확인) |
| **G2** 고아 감시 실제 pass 전환 | ✅ 실 PG **8 passed · xfailed 0 · skipped 0**. `test_an_orphan_agent_run_is_flagged`가 **마커 없이 본문을 완주**했다(이름으로 확인) — **부모 `AI_RUN` 선삽입 0 · 테스트 중 FK DROP 0** |
| **G3** 세 capability 실제 enqueue | ✅ 아래 |

**G3 실측** — 실제 Enqueuer → 실제 `PgJobStore` → 실제 runner → 실제 원장 recorder.

| capability | enqueue 직후 | 종단 후 | 감사 판정 |
| --- | --- | --- | --- |
| `counsel_pack` | `AGENT_RUN` 1건 · `run_id == execution_id` · **`AI_RUN` 없음(정상)** · PG 복원 성공 | `succeeded` · `result_ref` 있음 · `AI_RUN` 1건 · 결합·테넌트 일치 · capability `composition` | **`ok`** |
| `problem_generation` | 동일(부모 선삽입 없음) | `succeeded` · `result_ref` 있음 · `AI_RUN` 1건 · capability `problem_generation` | **`ok`** |
| `mapping_probe` | `AGENT_RUN` 1건 · 복원 성공 | 계약 종단 도달 · 🔴 **`AI_RUN` 없음** | ⚠ **`separate_gap`** — ㉾ |

**세 경로를 한 테넌트에서 합친 판정**: `ok 2 · separate_gap 1 · violation 0 · unknown 0` ·
`preflight_blocks = False`. 🔴 **행 순서에 기대지 않고 `execution_id`로 정확 대조**했다.

⚠ **`mapping_probe`는 ㉾로 분리 유지** — G1이 연 것은 **잡이 PG에 앉는 것**이고
`record_run()` 0건은 그대로다. **`ok`로도 `violation`으로도 판정하지 않았다.**

🔴 **G3에서 발견한 것 — counsel에는 타입이 맞는 PG step sink가 없다.**
`PgAgentStepSink`는 **probe 축의 `AgentStepRecord`**로 타입돼 있고, counsel의 동명 클래스와
**필드는 완전히 같지만 별개 클래스**다(실측) ⇒ `CounselPackRunner(step_sink=...)`에 넣으면
mypy가 거부한다. **런타임은 되고 타입만 안 맞는다** ⇒ **별건**이고 이 회차에서 안 고쳤다.
⚠ 그래서 G3의 counsel 검사는 **스텝만 인메모리**로 뒀다 — 이 파일의 축은
**`AGENT_RUN`↔`AI_RUN` 결합**이고 그 축은 전부 실 PG다.

---

## 5. 완료 뒤 남은 후속 (2026-08-11)

| 일 | 상태 |
| --- | --- |
| FK 제거 | ✅ **#201** |
| 생애주기 원장 점검 | ✅ **#202** |
| G3 실제 enqueue 3종 | ✅ **#202** |
| **counsel `AGENT_STEP` PG 배선** | ☐ **#37** — 🔴 **전면 플립 전 해소 관문** |
| `mapping_probe` 원장 | ☐ **㉾** |
| 실행 식별자 분리(④) | ☐ 별도(99 ㊮ 계열) |
| `PROBLEM_SET` 쓰기 0건 | ☐ 별도 |
| 기본 PG 플립 · ㉿ⓓ·㉬·㉻ | ⏭ **다음 단계** |

### ⚠ 아래는 **G1 전의 실행 순서 기록**이다 — 현재 지침이 아니다

당시 A는 `db/models.py`·마이그레이션·`06_erd.md`를 안 건드리고, **G2 분류기·점검기는
G1과 독립으로** 진행했으며, **G3는 G1 뒤**로 묶어 뒀다. 실 PG에서 고아를 못 만들던
동안에는 **제약을 임의로 지우거나 부모 `AI_RUN`을 선삽입하지 않는다**가 규율이었다 —
그 규율은 #202의 검사에도 그대로 남아 있다.

⚠ **이 문서의 §6(「준영님께 여쭙는 것」)은 판정으로 대체돼 삭제했다** — 열린 질문 셋 중
①은 **③ 채택**으로, ②는 **㉾ 분리**로, ③은 **A 소유 생애주기 점검 채택**으로 닫혔다.
