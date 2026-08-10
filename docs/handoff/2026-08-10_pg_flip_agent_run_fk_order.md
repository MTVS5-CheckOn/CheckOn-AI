# 전면 PG 플립 관문 — `agent_run.run_id → ai_run` FK 순서 조사 (A+B 판정 요청)

**작성:** member-A · 2026-08-10(조사) · **2026-08-10 판정 반영(A+B)**
**판정: ② `AGENT_RUN.run_id`를 nullable로 둔다.** 모델·마이그레이션·짝 가드는 **준영님(B) 구현**이다.
⚠ 이 문서는 **판정을 기록만** 한다 — 코드·모델·마이그레이션은 이 PR에서 손대지 않았다.

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

## 0-1. 판정 — ② `AGENT_RUN.run_id` nullable **(A+B 확정)**

**근거 여섯:**

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

⚠ **②를 「임시방편」이라고 쓰지 않는다.** 현재 사실에 맞는 **플립 해제안**이고,
④는 **식별자 의미를 다시 설계하는 별도 안건**이다. 둘은 대체재가 아니다.

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

### 1-4-1. 🔴 짝 가드 — ②의 대가로 **원장 누락 기준이 바뀐다**

| | 기준 |
| --- | --- |
| **종전(거짓)** | `AGENT_RUN`이 있으면 `AI_RUN`이 **반드시** 있어야 한다 |
| **새 기준** | **종단 상태**이고 **실제 LLM 호출을 소비한** 실행인데 `AI_RUN`이 없다 → **red** |

nullable은 「없어도 된다」를 여는 것이므로, **없어도 되는 경우와 없으면 안 되는 경우**를
가르는 가드가 **함께 서야** 한다. 안 그러면 ②는 결함을 **감추는** 변경이 된다.

⚠ **이 가드는 준영님 모델 PR의 구현 범위다** — 이 문서 PR은 **테스트도 코드도 만들지 않는다.**

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

## 3. 선택지 넷 — **② 채택(A+B 확정).** 비용 비교는 판정 근거로 남긴다.

### ① `AI_RUN` 선등록 후 갱신 — ❌ **기각**

| 축 | 비용 |
| --- | --- |
| 마이그레이션 | 불필요(스키마 그대로) |
| 코드 | enqueue 셋 + `RunStore`에 「선등록」 개념 신설 |
| 🔴 **충돌** | **`record_run()`이 INSERT다** — 나중에 같은 `execution_id`로 또 넣으면 PK 충돌. **UPSERT나 `update_run` 신설이 필요**하다 |
| 🔴 **의미** | 선등록 시점엔 **모델·파라미터·토큰이 없다** — `model_provider`/`model_name`/`generation_params`를 **뭘로 채울 것인가**. `null`로 두면 **#144의 「0콜인데 파라미터가 적혔다」와 정반대 방향의 거짓**(「호출 있는데 파라미터가 빈다」)이 **정상 상태**가 되어 그 가드가 무의미해진다 |
| 🔴 **실패 경로** | 잡이 실패하면 **파라미터가 빈 `AI_RUN`이 영구히 남는다** — 「실행했는데 기록이 없다」와 「등록만 하고 실행 안 했다」를 무엇으로 가를지 새 상태가 필요 |
| probe | `record_run`이 0건이라 **선등록만 있고 갱신이 영영 없다** |

### ② `agent_run.run_id` nullable — ✅ **채택**

| 축 | 비용 |
| --- | --- |
| 마이그레이션 | **필요**(컬럼 nullable + 기존 행) · 🔴 **양자 승인 파일** |
| 코드 | `_row_from_job`에서 `run_id` 제외 + 실행 시작 시 `UPDATE` 1회 |
| 의미 | **queued 잡은 아직 실행이 아니다** — 사실에 가장 가깝다 |
| 대가 | `run_id`로 조인하는 조회가 **null을 다뤄야** 한다. `AI_RUN` 없는 `AGENT_RUN`이 **정상**이 되므로 「원장 누락」 가드의 기준이 바뀐다 |
| probe | run_id가 **영구 null**로 남는다 — 그 자체는 모순이 아니지만 **probe에 원장이 없다는 사실이 드러난다**(별건 등재 후보) |

### ③ 물리 FK 제거(논리 참조) — ❌ **기각** · ⚠ **고아 검사 소유 질문은 이로써 종료**

| 축 | 비용 |
| --- | --- |
| 마이그레이션 | **필요**(제약 DROP) · 🔴 **양자 승인 파일** |
| 코드 | **0줄** |
| 선례 | `06_erd.md`가 **이미 논리 참조를 쓰는 자리**가 있다(`job_id`는 varchar 논리 참조 — #182 주석) |
| 대가 | 🔴 **고아 행을 DB가 안 막는다.** 지금 이 저장소가 겪은 형태(「검사가 없으면 안 보인다」)가 그대로 재연될 수 있다 ⇒ 논리 참조로 가면 **정합 검사를 함께 세우는 것이 조건** |

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
2. **②가 코드 변경이 가장 작다**(`_row_from_job` 한 줄 + 실행 시작 시 UPDATE 1회).
   대신 **양자 파일 + 마이그레이션**이고 **짝 가드가 조건**이다(§1-4-1).
3. **③은 코드 0줄**이었지만 **제약을 더 많이 버린다** — 「값이 있으면 실재하는 실행을
   가리킨다」까지 잃는다. 이 저장소는 최근 **물리 제약이 없어서 안 막힌 것**을 두 번 겪었다
   (둘 다 null인 읽기 모델 행 · JSON null). ⇒ 기각.
4. **④만 refine을 설명한다** — 그 사실은 없어지지 않으므로 **별도 안건으로 남긴다.**
5. ⚠ **`mapping_probe`는 어느 쪽을 골라도 별건이다.** nullable이 되면 **enqueue 차단은
   풀리지만 `record_run()` 0건은 그대로**다 ⇒ **99 ㉾**로 분리했다(불변식 8 대상 · A 작업).

---

## 5. 다음 단계 — 소유가 갈린다

| 일 | 소유 | 상태 |
| --- | --- | --- |
| `AGENT_RUN.run_id` nullable **모델·마이그레이션** | 🔴 **B(준영님)** | 대기 |
| **짝 가드**(종단 + 실 호출 소비인데 `AI_RUN` 없음 → red) | 🔴 **B(준영님)** | 대기 |
| `mapping_probe`의 `AI_RUN` 생성 | **A** | 99 ㉾ · 별건 |
| 실행 식별자 분리 | **A+B** | 99 ㊮ 계열 · 별건 |
| 플립 점검표 관문 갱신 | **A** | ✅ 이 PR |

**A는 B의 모델 PR이 머지되기 전까지 코드·모델·마이그레이션을 손대지 않는다.**
`STORE_BACKEND` 기본값은 **memory 그대로**이고, 읽기 모델 검증은 **저장소 한 축만 주입**해서
계속한다 — 전면 플립에 안 기댄다.

⚠ **이 문서의 §6(「준영님께 여쭙는 것」)은 판정으로 대체돼 삭제했다** — 열린 질문 셋 중
①은 **② 채택**으로, ②는 **㉾ 분리**로, ③은 **③ 기각**으로 각각 닫혔다.
