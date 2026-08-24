# 체크온 AI 서비스 — REST API · 스냅숏 데이터 계약 v0.1 (제안)

| | |
| --- | --- |
| **상태** | 🟢 **7/15 리뷰 완료 — v1.0 승격 대기.** 잔여 2건: Open-9 백분위 출처 · Kafka 토픽/이벤트 스키마 부록. 채워지면 v1.0 커밋 |
| **당사자** | AI 서비스(Python·PostgreSQL, member-A) ↔ 백엔드(Java·Spring·PostgreSQL) |
| **읽는 법** | `[제안]` = 이견 없으면 그대로 확정 · `[Open-n]` = **결정 필요 → §1만 논의하면 30분에 끝남** |
| **확정 후** | A는 FakeSnapshot으로, 백엔드는 AI 스텁으로 **서로 안 기다리고 동시 개발** 시작 |
| **참조** | `part_a/01_pipeline` `part_a/02_design` `06_erd` · 안건 추적 `99_open_items` · 요청 JSON만 모은 실무용: `05_request_json` |

**TL;DR (5줄)**

1. AI는 **신호·초안·매핑을 만들 뿐, 발송·확정·반영은 전부 백엔드**(HITL) — 이 API에 발송 개념이 없다.
2. 어떤 페이로드에도 **실명·연락처 필드 자체가 없다** — 백엔드 DTO 차원에서 보장을 요청한다.
3. '정상적 미생성'(데이터 부족 거부, 템플릿 전용, 문장화 폴백)은 **에러가 아니라 200 + status**.
4. 빠른 것(분류·태그)은 동기, LLM 생성(초안·Import·에이전트)은 **202 + 작업 조회**.
5. 결정 필요한 건 **Open-1 ~ Open-12 열두 개**뿐이다 (4a~4d 포함, 30분 미팅 분량).

**목차** — §1 리뷰 안건 · §2 공통 규약 · §3 엔드포인트(퀵 레퍼런스 + 상세) · §4 스냅숏 데이터 계약 · §5 비기능 · §6 확정 절차 · 부록 A 해시 규칙

---

## §1. 리뷰 안건 — Open-1 ~ 12 (이것만 정하면 됩니다)

| # | 안건 | 선택지 | 💙 A의 제안과 이유 | 결정 |
| --- | --- | --- | --- | --- |
| **Open-1** | 감지 스냅숏 전달 방향 | 백엔드 push / AI pull | **push** — 배치 오케스트레이션이 백엔드(Spring Batch)에 있음 | ✅ 7/15 |
| **Open-2** | 비동기 완료 통지 | 폴링 / 웹훅 / Kafka | ✅ **(7/15 확정) Kafka** — 백엔드 메시징 표준. 토픽·이벤트 스키마는 v1.0 부록 | ✅ |
| **Open-3** | Import 파일 전달·산출물 반환 | multipart / 스토리지 URL | **둘 다 스토리지 URL** — 대용량·재시도 유리. 산출물 반영은 백엔드가 기존 F1 경로로(우회 금지) | ✅ 7/15 |
| **Open-4a** | 초기 도입 시 과거 데이터 백필 | 일괄 1회 / 주차 분할 | 주차 분할(해시·재현성 유지) | ✅ 7/15 |
| **Open-4b** | **과제명 텍스트 제공 가능?** | 가능 / 불가 | ✅ **(7/15) 제공 가능 확정** — 태깅 제안 기능 생존 | ✅ |
| **Open-4c** | 학생 요약 집계 주체 | 백엔드 / AI | **AI** — 자기 feature_week 사용, 중복 계산 방지. 백엔드는 개입·소통 이력만 | ✅ 7/15 |
| **Open-4d** | 소통 원문 전달 범위·마스킹 | — | 최근 10건·90일 · **백엔드 1차 마스킹 + AI redaction 2차** | ✅ 7/15 |
| **Open-5** | 인증·네트워크 | 인프라 표준 | 표준 따름 — A의 조건은 둘: 내부 전용 노출 + AI PG 국내 리전 | ✅ 7/15 |
| **Open-6** | JSON 네이밍 | snake / camel | 백엔드 표준 따름(snake_case) — 전 엔드포인트 일괄 | ✅ 7/15 |
| **Open-7** | /feedback·/confirmations 통합 | 통합 / 분리 | **분리** — 경보 평가(1차 라벨)와 제안 확정(품질 평가셋)은 의미가 다름 | ✅ 7/15 |
| **Open-8** | 야간 배치 시각·순서 | — | 스냅숏 02:00 → /detect 02:10 → 브리핑 조립 03:30 | ✅ 7/15 |
| **Open-9** | 전국 백분위 데이터 출처·모수 | 자체 사용자 풀 / 외부 기준 | 🟠 **부분 합의(7/15): 모수 미달 시 "해당 차트 생략" 확정** — 출처·갱신 주기는 백엔드 확인 잔여 | 🟠 |
| **Open-10** | 리포트 대상 선정(15일 규칙) vs DataSufficiency 게이트 | — | **게이트 우선** — 15일 이전 등원생이어도 데이터 2주 미만이면 그 달 생략. 대상 선정은 백엔드 소유 | ✅ 7/15 |
| **Open-11** | 영역 enum 수능 기준 개정 **[A+B 합의]** | 3갈래 유지 / 수능 6영역 | ✅ **(7/15 확정) 수능 6영역 채택 + item_format은 v1에서 `mcq`만 사용**(수능 국어 전 문항 객관식 — short·essay는 enum 예약, 내신·자체 시험 후순위) | ✅ |
| **Open-12** | 오프라인 시험 답안지 OCR의 실명 처리 **[백엔드]** | — | 답안지에는 학생 실명이 쓰여 있음 — OCR 결과의 이름→alias 매칭은 **vault 보유한 백엔드가 수행**, AI에는 매칭 완료된 alias 답안만 진입(Phase 2 오프라인 시험 루프의 전제) | ☐ |

---

## §2. 공통 규약

### 2.1 기본 `[제안]`

| 항목 | 값 |
| --- | --- |
| Base URL | `http://checkon-ai.internal/v1` — 내부 전용, 외부 미노출 `[Open-5]` |
| 포맷 | JSON (UTF-8) · 네이밍 `snake_case` `[Open-6]` |
| 필수 헤더 | 도메인 API는 `X-Tenant-Id`(강사 alias) · `X-Request-Id`(상호 추적), 쓰기 요청은 `Idempotency-Key`. 운영 프로브는 §3.10의 예외 규약을 따른다 |
| 시간 | ISO-8601 + 오프셋 (`2026-07-14T02:00:00+09:00`) |

### 2.2 응답 envelope `[제안]`

아래 JSON은 `problem_generation` 실행에서 `VersionSet`의 정식 10키 집합을 모두 표시한 예시다.

```json
{
  "data": { },
  "error": null,
  "meta": {
    "execution_id": "uuid",
    "versions": {                          // ⚠ **10키 형태 예시다 — 현재 실행값이 아니다.**
                                           //    🔴 (8/12 정정) `engine`이 **detect의 값**
                                           //    (`detection-rules-*`)이었는데 이 블록은
                                           //    problem_generation 예시다 — 문서 오류였다.
                                           //    실제 detect 값은 `part_a/09` §3과
                                           //    `docs/part_a/examples/detect_demo_response.json`
                                           //    (자동 재현)에 있다.
      "pipeline": "0.1.0",
      "engine": "problem-generation-0.1",
      "threshold": null,
      "prompt": "v0.1",
      "schema": "0.1",
      "contract": "0.1",
      "graph": "curriculum-0.1",
      "taxonomy": "taxonomy-0.1",
      "verify_config": "verify-0.1",
      "difficulty_calib": "difficulty-0.1"
    }
  }
}
```

실패 시 `data: null`, `error: {"code", "message", "detail"}`. **등록된 경로의 응답에는 `meta.versions`가 항상 실린다** — 재현성·디버깅의 기준.

⚠ 🔴 **「항상」에 범위를 적는다(8/10 · 99 ㊜).** **등록 안 된 경로는 이 규약 밖이다** — Starlette가 **라우트 매칭 전에** 자기 404를 내므로 우리 예외 핸들러가 아예 안 탄다. 실측: `GET /v1/nope` → `404 {"detail": "Not Found"}` (envelope·`meta` 없음 · `405`도 같다). **BE는 이 형태를 받으면 「버전을 못 읽었다」가 아니라 「경로가 틀렸다」로 읽어야 한다.** 필요한 정보가 버전이 아니라 경로이므로 **envelope를 씌우지 않는다**(그러려면 `api/app.py`에 핸들러를 더해야 하고 그 파일은 양자 승인이다 — 얻는 것이 그만큼 크지 않다). 🔴 종전에 이 문장이 **범위 없는 「항상」** 이라 다음 사람이 그걸 근거로 쓸 수 있었다 — 그게 이 안건의 전부다.

#### 🔴 202를 받은 BE가 무엇을 하나 — **정본은 런북이다** `[확정 · 2026-08-08]`

> 🔴 **정본을 갈라 둔다** — 같은 문장을 §3.9·§3.11·런북 셋에 복제하면 갈린다(99 #02).
>
> | 무엇 | 정본 | 왜 |
> | --- | --- | --- |
> | **BE 분기 규칙** · **드레인 공백의 실측·대응** | 런북 §2 ⓕ·ⓖ | **운영 절차**다 — 계약이 아니라 v1 실행 성질이고 대응(신고 요청)이 붙는다 |
> | **잡이 없는 경로에 통지가 없다** | 🔴 **여기(04)** | **계약 사실**이다 — `_generate` ①②는 §3.9가 규정한 경로이고, 그 경로에 통지가 없다는 것도 계약이 말해야 한다(99 #19) |

**규칙:** **202의 `status`가 종단(`succeeded`·`failed`·`cancelled`)이면 통지를 기다리지 말고 바로 GET한다.**
counsel(§3.9)·pg(§3.11) **둘 다 202에 `job_id`와 `status` 2키를 싣는다** — 이 값 하나로 분기가 끝난다.

🔴 **「종단이 아니면 기다린다」에는 끝이 없다 — 통지가 안 올 수 있다.**

| 언제 | 왜 |
| --- | --- |
| **잡이 아예 없는 경로** | counsel `_generate` ①(`topic=schedule` → `template_only`)·②(인용 가능 근거 0건 → `rejected_insufficient`)는 **`enqueue` 앞에서 반환**한다 ⇒ **`WorkerJob` 행 자체가 없다** ⇒ 종단 phase 전이가 없다 ⇒ **통지가 발생할 자리가 없다.** ⚠ **Kafka 배선 뒤에도 참이다** — 「지금 미구현이라 다 안 온다」와 **다른 사실**이다 |
| **`queued`로 나간 잡** | 🔴 **배경 드레인 루프가 없다 — `GET` 폴링은 잡을 돌리지 않는다**(조회 전용). ⚠ **(8/19) POST 가 자기 잡이 끝날 때까지 유한 반복하도록 바뀌어 대부분 해소됐다** — 큐에 **앞선 잡이 상한(3회전)보다 많을 때만** `queued`로 나가고, 그때는 **다음 POST 를 기다린다.** 🔴 **폴링은 그 잡을 진행시키지 않는다** — 결과가 안 오면 재시도(POST)가 답이다. ⚠ 실측·범위·대응은 **런북 §2 ⓖ**가 든다(99 #21). 여기 복제하지 않는다 |

⚠ **v1 인라인 실행의 성질이지 계약 위반이 아니다** — 202 예시(`{job_id, status}` 2키)는 **맞다.**

#### 🔴 응답이 오기 전에 타임아웃이 나면 — 같은 키로 다시 보내세요 `[확정 · 8/19]`

**counsel POST 는 최악 450초까지 걸릴 수 있다** — **콜당 상한 90초 × 최악 5콜**(plan 1 + write 4).
⇒ 🔴 **백엔드 read timeout 은 480초 이상**으로 잡아 주세요(§2.4 응답 예산과 같은 값입니다).
응답을 못 받았을 때 규약은 이것 하나다.

> ⚠ **응답이 오기 전에 타임아웃이 나면 같은 `Idempotency-Key` 로 다시 보내세요.**
> **같은 `job_id` 를 돌려받습니다** — 잡이 중복 생성되지 않습니다.
> 🔴 **`job_id` 를 직접 계산하지 마세요.** 응답에 실린 값을 그대로 쓰시면 됩니다.

**왜 안전한가:** AI 가 `job_id` 를 **멱등 스코프에서 유도**한다. 재시도는 새 잡을 만드는 대신
**첫 요청의 잡에 도착**하고, 그 잡이 이미 끝났으면 `status` 가 종단으로 온다(그다음은 GET).
아직 도는 중이면 `queued`/`running` 이고, **재시도 자체가 큐를 한 번 더 돌린다** — 폴링과
다른 점이다(위 표).

🔴 **유도식은 AI 내부 사정이다.** BE 가 같은 값을 계산해야 하는 설계로 만들지 않았다 —
「두 언어가 같은 해시를 내는가」는 이미 한 번 갈린 적이 있다(99 #50). BE 가 하는 일은
**같은 키로 다시 보내는 것**뿐이다.

⚠ **재시도 대상이 아닌 경우 둘.** ① 바디를 바꿔 다시 부르는 것은 재시도가 아니라 **새 요청**이다
— **새 키**를 써라(문의 유형 정정이 그 경우다 · §3.9 재요청 규약 ⓑ). 같은 키 + 다른 바디는
409 다. ② **`Idempotency-Key = inquiry_ref` 등** 이라는 §3.4 의 안내는 여기에도 그대로 적용된다
— 문의 1건이 초안 1건이므로 문의 참조가 자연스러운 키다. 그 문장의 정본은 **§3.4** 이고
여기서 복제하지 않는다(99 #02).

⚠ **refine 은 이 규약 밖이다** — 동기 호출이라 잡이 없고, 실행 중 재시도는 LLM 을 한 번 더
부른다(99 #94). 결과는 수렴하므로 강사가 보는 문면은 하나다.

#### 🔴 `meta.execution_id` — 원장 키이거나 상관 ID다 `[확정 · 8/7]`

**정의:** `meta.execution_id`는 **그 응답이 말하는 실행의 원장 키**(`AI_RUN.execution_id`)다. 그 값으로 원장을 조회할 수 있고, **같은 잡을 여러 번 조회해도 같은 값**이다. 응답을 만들 때마다 새로 발급하는 값이 **아니다**. 🔴 **단, 잡을 만들지 않는 성공 경로**(counsel `template_only`·근거 0건 `rejected_insufficient` 등)는 워커도 LLM도 타지 않아 **원장에 행이 없다.** 그 경우에는 **응답들을 묶는 상관 ID**가 실리며 **그 값으로는 원장을 못 찾는다** — 안정성(같은 잡의 반복 조회가 같은 값)만 보장된다. ⚠ **BE는 이 값으로 원장을 조회하기 전에 아래 표에서 그 엔드포인트의 부류를 확인해야 한다.**

⚠ **비대칭이 있다** — `error_envelope`는 실행 전 오류(헤더 누락 등)에 `execution_id: null`을 허용하는데 `success_envelope(execution_id: str)`에는 **그 자리가 없다.** 그래서 성공 응답에서 *"가리킬 실행이 없다"* 를 `null`로 표현하지 못하고 위의 **상관 ID**로 대신한다.

**엔드포인트별 부류** (8/7 종단 실측):

| 엔드포인트 | 부류 | 근거 |
| --- | --- | --- |
| `POST/GET /v1/counsel/drafts` — 잡 생성분 | 🔴 **원장 키** | `WorkerJob.execution_id` = `AI_RUN.execution_id`. POST·GET1·GET2·AI_RUN **전부 같다** |
| `POST /v1/counsel/drafts/{id}/refine` | 🔴 **원장 키** | 그 턴이 하나의 실행이고 `_record_refine_run`이 그 값으로 원장을 쓴다 |
| `POST/GET /v1/counsel/drafts` — `template_only`·근거 0건 | ⚠ **상관 ID** | 워커·LLM 미실행 ⇒ **AI_RUN 0건**. 반복 조회는 같은 값 |
| `POST /v1/detect` | 🔴 **원장 키** | 동기 실행이라 그 값이 곧 `ExecutionContext`의 키다 |
| `POST /v1/classify` — **비캐시**(LLM 호출) | 🔴 **원장 키** | 위와 같다 |
| `POST /v1/classify` — **캐시 히트** | 🔴 **원장 키** | ✅ **(8/7 해소)** 캐시 조회를 `try` 안으로 옮겨 `finally`의 원장 적재를 타게 했다 — **예측 재사용도 실행이다**(불변식 8). ⚠ 그 실행은 **LLM 호출 0건**이라 `model_provider`·`model_name`·`generation_params`가 **전부 null**이다(원장에서 그렇게 구분한다 · 99 ㊧). 🔴 **캐시 히트도 원장 적재 실패 시 5xx다** — 저장된 예측이 있어도 나간다. 종전에는 캐시 히트가 원장을 안 타서 **5xx가 구조적으로 불가능한 경로**였는데 이제 가능하다. ⚠ **캐시가 뜨면 항상 200이라는 뜻이 아니다.** 새 사유 코드는 없다(기존 5xx). *원장을 못 남긴 채 「기록했다」로 응답하는 것이 더 나쁘다* 는 판정과 일관된다 |
| `POST /v1/problems` · `GET /v1/problems/{job_id}` | 🔴 **원장 키** | 잡을 **항상** 만든다(잡 없는 성공 경로 없음). ⚠ `GET`은 현재 응답마다 새로 발급 — **아래 미정합** |
<!-- 🔴 (2026-08-22) `POST/GET /v1/imports` 두 행을 뺐다 — 이 표는 «**지금** 어느 엔드포인트가
     원장 키를 쓰나»를 말하는 **계약**이고, 그 엔드포인트는 삭제됐다(§3.8 · 99 #187).
     ⚠ 그 두 행이 담던 **㉾ 해소(8/12 · 조사 워커가 `AI_RUN` 한 행)** 는 99 와 git 이력에 남는다. -->
| `POST /v1/confirmations` | ⚠ **미정합** | 원장을 쓰지 않는데 **호출마다 새 값**이라 상관 ID로도 기능하지 않는다 — 아래 |

🔴 **아직 이 규정과 어긋나는 자리 둘** `[미정합 · 수정 대기]` — 계약이 정본이고 구현을 여기 맞춘다:

| 자리 | 현재 동작 | 처방 |
| --- | --- | --- |
| `GET /v1/problems/{job_id}` | 응답마다 새 값 | `job.execution_id`(같은 파일 `POST`가 이미 그 형태) · **B 소유** |
| `POST /v1/confirmations` | 호출마다 새 값 | **안정적인 값**으로. 무엇으로 할지는 판정 — 이 축은 원장이 없어 *"가리킬 실행이 없다"* 가 정상이다 |

✅ **(8/10 해소) 셋째 자리였던 「classify 캐시 히트의 `prompt`」는 결함이 아니었다** — 🔴 **틀린 것은 코드가 아니라 위 문장이었다.** 종전 §2.2가 *"`prompt`는 **LLM 미사용 실행**에서 null"* 이라 적어 캐시 히트(호출 0건)가 위반으로 보였는데, `meta.versions`는 **선언 축**이고 classify는 프롬프트를 쓰는 capability다 ⇒ `prompt: "v1"` 이 **맞다.** 응답을 `null`로 바꿨다면 **같은 엔드포인트가 캐시 유무에 따라 다른 버전을 말하게** 되고, `classify_versions()`가 *"`prompt_version`을 **반드시 채운다**"* 를 ㊔ 선례로 적어 둔 것도 되살아난다. **문장을 고쳐 계약을 참으로 만들었다** — 응답 변경 **0건**(99 ㊧·㊮).

**비대칭 해소 판정** `[제안 · B 협의]` — ⓐ `success_envelope`가 `str | None`을 받게 한다(**응답 스키마가 바뀌고 `api/envelope.py`는 양자**) · **ⓑ 위 정의대로 «원장 키이거나 상관 ID»로 규정한다(권고)** — 스키마를 안 흔들고 *"meta는 항상 실린다"* 는 기존 규약도 유지된다. ⚠ ⓐ를 고르면 양자 파일이 열리므로 **B 승인 전에는 ⓑ가 현행**이다.

#### 🔴 재현 보장의 범위 — 경로별로 갈린다 `[확정 · 8/9]`

**약속:** 같은 입력·같은 버전 세트면 같은 출력이 나온다(불변식 8 · `contracts/execution.py`의 `VersionSet` docstring).

🔴 **현재 — 그 조건이 성립해도 LLM 경로는 바이트 동일이 아니다.** 같은 출력의 조건에는 `generation_params`(`seed`·`temperature`)도 드는데, **LLM 경로의 `seed`는 서버 best-effort**다(99 ㊼ 실측 8/7: 요청에 `seed=20260805`가 실렸는데 같은 입력이 358자/361자로 갈렸다). ⇒ **BE는 「같은 요청 = 같은 초안 문면」을 전제로 캐시·비교 로직을 만들면 안 된다.**

⚠ **불변식 8이 죽는 게 아니라 경로별로 갈린다** — **결정론 경로(게이트·판정·산식)는 그대로 바이트 동일**이다. 신호 발화·게이트 통과·라벨 확정·채점은 LLM이 아니라 결정론 코드가 정하므로(불변식 1) 같은 입력에 같은 결과가 나온다. **재현이 아예 안 되는 것이 아니라 LLM이 만든 문면만 흔들린다.**

⚠ **우리가 못 바꾸는 부분이다** — 요청에 `seed`를 싣는 것까지가 우리 몫이고(8/6부터 `deterministic_params()`가 전 경로 공유) 서버가 그걸 존중하는지는 벤더 성질이다.

**(7/15) 공통 버전 세트는 6종으로 통일** — 이 §2.2와 ERD의 `AI_RUN`이 각각 4종씩 서로 다르게 적고 있어(§2.2=threshold·contract / ERD=prompt·schema) 합집합으로 맞췄다.

**[PART_A+PART_B] 승인 확장:** 키 집합의 정본은 `contracts/execution.py`의 `VersionSet`이며, `AI_RUN` 컬럼·`meta.versions`와 1:1이다. 현재 정식 키 집합은 공통 6종(`pipeline`·`engine`·`threshold`·`prompt`·`schema`·`contract`) + [PART_B] 실행 전용 nullable 4종(`graph`·`taxonomy`·`verify_config`·`difficulty_calib`)인 **총 10종**이다. 위 JSON은 특정 capability의 예시이며, nullable 값은 실행 종류에 따라 달라진다.

#### 🔴 이 값은 「무엇의」 버전인가 — 두 축을 가른다 `[확정 · 8/10]`

> **`meta.versions`의 열 키는 「이 응답을 낸 엔드포인트/capability가 어느 버전 위에서 도는가」다 — 선언 축이다.**
> **`null`은 「이 capability에 해당 없음」이지 「이번 실행이 안 썼음」이 아니다.**

🔴 **이 축은 고른 것이 아니라 강제된 것이다.** 실패 응답에도 `meta.versions`가 실리는데(A판정 7/22), 실행 전 오류(헤더 누락·JSON 파싱 실패)는 **실행이 0인 시점**이다 — 거기서 *"이번 실행이 실제로 쓴 버전"* 은 존재하지 않는다. `api/envelope.py`가 *"versions는 **엔드포인트의 정적 버전**으로 채운다"* 라 적고 `problem_failure_versions()`가 *"**실행 config 확정 전에도** 나간다"* 라 적은 것이 같은 사실이다.

**`AI_RUN`은 컬럼이 두 종류다:**

| | 컬럼 | 축 | 응답에 나가나 |
| --- | --- | --- | --- |
| **선언** | `VersionSet` **열 키** 전부 | 엔드포인트/capability | ✅ **`meta.versions`와 같은 값**(㊔ — 응답과 원장이 다른 답을 하면 안 된다) |
| **사용** | `model_provider` · `model_name` · `generation_params` | 이 실행이 **실제로** 호출한 것 | 🔴 **안 나간다** — `RunMetadata`에만 있다 |

⚠ **#144가 바꾼 것은 아래 셋뿐이고 위 열 키는 안 건드렸다** — 그래서 **응답은 갈리지 않았다.** 종단 실측(8/10 · 같은 `inquiry_ref` 2회): 응답 `meta.versions`가 **두 회차 완전히 동일**하고 원장의 `model_provider`·`generation_params`만 캐시 히트에서 `null`이 됐다.

⚠ 🔴 **「LLM 미사용 실행」이라는 표현을 쓰지 않는다.** 그 말이 ⓐ*"LLM을 안 쓰는 capability"* / ⓑ*"호출이 0인 실행"* 둘로 읽혔고 실제로 오독을 낳았다(99 ㊧·#11 ⓓ). 선언 축 키의 조건은 **언제나 capability(또는 그 기능의 배선 여부)** 로 적는다.

**키별 조건 — 전부 선언 축이다:**

| 키 | `null`인 경우 |
| --- | --- |
| `pipeline` · `engine` · `schema` · `contract` | 없음(필수) |
| `threshold` | 감지 임계값 시트를 쓰는 capability(detection) 외 |
| `prompt` | **프롬프트를 쓰지 않는 capability** 외 — ⚠ 종전 *"LLM 미사용 실행"* 표기를 고친 것이다. counsel·classify·detect·pg는 전부 프롬프트를 쓰므로 **호출 0건인 실행에서도 채운다**(캐시 히트 포함) |
| `graph` · `taxonomy` · `verify_config` | 관련 [PART_B] capability 외 |
| `difficulty_calib` | `problem_generation` 외에는 항상 null이고, **v1은 보정 미배선(`difficulty_regen_enabled: false`)이라 pg도 null**이다 |

⚠ **(8/10) `difficulty_calib`의 축 표기를 다시 고쳤다 — 다만 8/8 정정의 「내용」은 유지한다.** 8/8이 *"pg여도 null이다"* 로 바로잡은 것은 **맞고 그대로 둔다.** 고치는 것은 그 근거의 **표기**다 — 8/8은 그것을 *"실제로 적용한 **실행**에서만"* 이라 적었는데, 그 문장 자신이 든 근거(`difficulty_regen_enabled: false`)는 **실행이 아니라 빌드 설정**이다. 즉 조건은 「이 실행이 보정을 했는가」가 아니라 「이 빌드에 보정이 배선됐는가」다. **내용은 같고 축 이름만 틀렸다.**

> 🔴 **(8/8 정정) 위 괄호가 `(감지·진단)`이었는데 감지는 LLM을 쓴다.** 경보 브리핑 문장화(ⓐ)가 **선형 LLM 1콜**이고(위 §1 표 · `part_a/01_pipeline.md` ⓐ) 이 문서 자신도 `brief`를 *"LLM 생성 + 왜곡 게이트 통과분"* 이라고 적는다. 그 괄호는 **브리핑이 붙기 전**에 쓰였고, 그동안 `/v1/detect`의 응답과 `AI_RUN`이 **둘 다 `prompt=null`** 인 채로 프롬프트 `0.2`를 쓰고 있었다 — *"그때 어떤 프롬프트로 브리핑을 만들었나"* 를 원장에서 못 읽었다(불변식 8). 지금은 `briefing.PROMPT_VERSION`을 싣는다. ⚠ **브리핑이 폴백으로 LLM을 안 탄 실행에서도 싣는다** — 버전 세트는 *"이 실행이 어떤 버전으로 조립됐나"* 이고, 실제 사용 여부는 신호별 `brief.fallback_used`가 따로 말한다.

> **[PART_B 크로스체킹 요청 · 미확정 — capability별 version 조건]** 위 null 조건은 문서에는 있으나 현재 공용 모델이 capability별 필수·금지 조합을 강제하지 않아 B 실행의 B 버전 누락이나 A 실행의 B 버전 혼입이 통과할 수 있다. **제안 해결안:** `ExecutionContext` 조립 경계에서 capability별 VersionSet 불변식을 validator와 음수 테스트로 고정한다. A·B가 공용 계약의 강제 수준을 확인해 달라.
>
> ✅ **A 판정(7/22):** 타당한 지적이나 `execution.py`(양자 승인 파일) 변경이라 A 단독으로 확정하지 않는다 — **양자 협의 안건으로 등록**(99 등록). capability별 VersionSet validator(A 실행에 B 키 혼입 금지·B 실행에 B 키 필수)는 B와 강제 수준을 합의한 뒤 execution.py에 반영한다.

> **[PART_B 크로스체킹 요청 · 미확정 — 실패 envelope]** 현재 공용 `api/envelope.py`는 실패 응답의 `meta`를 null로 만들 수 있어 위 “meta.versions는 항상” 규약과 어긋난다. **제안 해결안:** 실행 전 오류까지 포함해 version meta를 조립하는 단일 규칙을 두고 성공·실패 HTTP 테스트에서 10키 집합을 검증한다. 계약을 유지할지 구현 동작을 정본으로 삼을지 A·B 확인을 요청한다.
>
> ✅ **A 판정(7/22):** **계약이 정본** — 실패 응답에도 `meta.versions`를 싣는다(구현을 계약에 맞춰 수정 · ⚠ 양자 파일 `api/envelope.py`). 실행 전 오류(헤더 누락 등 config 확정 전)의 조립 규칙: 엔드포인트가 아는 **정적 앱 버전 + 기본 config의 threshold**로 채운다(실행 여부와 무관하게 그 엔드포인트의 버전 정보). 성공·실패 HTTP 테스트에서 10키 집합을 검증한다.

### 2.3 에러 코드

**에러 코드의 정본은 [`docs/policies/error_codes.md`](policies/error_codes.md) §1이다 — 여기 중복 정의하지 않는다.** (과거 이 표의 일부 코드가 정본과 코드명·409 의미가 어긋나 있어 표를 제거 — 7/21 통일, 상세는 99_open_items #12. 404의 "동의 없는 학생 참조 포함"·429 쿼터 주석은 정본 §1로 이관.)

- **멱등:** 같은 `Idempotency-Key` + **같은 바디** = 기존 결과를 **최초 요청과 같은 상태코드로** 재반환 · 같은 키 + **다른 바디** = `409 IDEMPOTENCY_CONFLICT`로 거부(기존 결과 반환 안 함).

  🔴 **재반환 상태코드는 동기 200 · 비동기 202다(8/7 확정).** 종전 표기는 *"200으로 반환"* 이었는데 그건 **동기 엔드포인트만 있던 시절**의 문장이고, 비동기 202가 생기면서 뒤처졌다. 멱등 재요청에 200을 주면 *"결과가 준비됐다"* 는 뜻이 되는데 **그 시점에 잡이 `running`일 수 있다** — 202가 뜻하는 *"접수했고 아직 안 끝났을 수 있다"* 와 정반대라 거짓말이 된다.

  | 엔드포인트 | 최초 | 멱등 재반환 |
  | --- | --- | --- |
  | `POST /v1/detect` · `POST /v1/classify` | 200 | **200** |
  | `POST /v1/counsel/drafts` · `POST /v1/problems` | 202 | **202** |

  ⚠ **구현 세 곳은 이미 일관됐다** — 틀린 것은 문서였다(실측 8/7 · ⚠ 🔴 그때의 세 곳 중 `imports` 는 **2026-08-22 에 삭제됐다**(§3.8) — 아래는 **그 시점의 기록**이다). `imports`·`counsel`은 데코레이터 `status_code=202`가 재반환에도 그대로 걸리고, `detect`·`classify`는 기본 200이다. 회귀는 `tests/ai/contract/test_idempotent_replay_status.py`가 세 축을 한 자리에서 잡는다.

> **[PART_B 크로스체킹 요청 · 미확정 — 공통 HTTP 경계]** 현재 감지 v0 캐시는 전역 Idempotency-Key와 클라이언트 `snapshot_hash`를 신뢰한다. **제안 해결안:** `(tenant_id, method/path, idempotency_key)`로 스코프하고 서버가 canonical body digest를 계산해 원자 저장·TTL·영속화를 보장한다. 아울러 `RequestValidationError`를 공통 envelope의 `400 INVALID_SCHEMA`로 매핑하고, 실제 `contracts.llm` 예외를 503/504로 변환하는 단일 adapter, `RedactionUncertain` 상세 강제 제거, `X-Request-Id` 응답 echo·로그 correlation을 공통 계층에 두는 안을 A·B·백엔드가 확인해 달라.
>
> ✅ **A 판정(7/22):**
> - **멱등 고도화(스코프·TTL·서버 digest·영속화):** D-② 안건으로 이관(99 ⑨ 확장). 이 API는 내부 백엔드 전용(§2.1 Base URL — 외부 미노출)이라 v0는 클라이언트 `snapshot_hash` 신뢰 + 인메모리로 충분하고, 서버 digest·스코프·TTL은 DB 교체 시 함께 구현한다.
> - **X-Request-Id 응답 echo·correlation:** 수용 — 공통 계층에 편입(커밋 api 공통부, ⚠ 양자).
> - **예외→HTTP adapter:** `error_codes.md` §4 트리를 정본으로, canonical adapter 위치는 `runtime/errors.py`로 확정. `IdempotencyConflict`→409·`LlmTimeout`→504 편입(error_codes §4 갱신). `RequestValidationError`→400 INVALID_SCHEMA는 현 구현과 일치.

> ⭐ **가장 중요한 원칙:** '정상적 미생성'은 에러가 아니다.
> `rejected_insufficient`(데이터 부족) · `template_only`(데이터 무관 문의) · `fallback_used`(문장화 폴백)는 **200 + `data.status`**로 온다. 화면 문구 번역은 백엔드/프론트 몫.

### 2.4 동기 / 비동기 `[제안]`

| 방식 | 대상 | 규칙 |
| --- | --- | --- |
| 동기 | `/classify` `/tags/suggest` `/confirmations` | 타임아웃 10s (`/feedback`은 7/16 보류 — §3.2). ⚠ **종전 「≤2s」는 실측과 달랐다** — `/classify` 배포 실측(2026-08-20 · n=5 · 종단): min **2.21s** · p50 **2.65s** · max **4.62s** · **2초 초과 5/5**. `/confirmations` 는 0.9s(n=1). 🔴 **최악은 3콜 × 90초 = 270초**(`MAX_PARSE_RETRY` 2 + 1)로 타임아웃 10s 를 크게 넘는다 — 그 간극은 99 ㉬ |
| 동기 (`/detect`) | `/detect`(야간 배치라 지연 무관) | **타임아웃 60s [A 확정 7/23 · 백엔드 통보 필요]** — 브리핑 문장화(ⓐ) 포함으로 상향. **문장화 총 예산 45s · LLM 호출당 15s 상한** ⇒ 🔴 **최악 45 + 15 = 60s** (예산 검사는 호출 **시작 전**에만 도므로 마지막 호출이 상한만큼 더 쓴다). ⚠ **8/20 정정** — 콜당 90초 확정(99 ㉪) 이후 `/detect` 도 provider 전역을 타서 최악 **~135s** 가 돼 있었다(99 #124). ⇒ **브리핑 role 에만 15s 를 주입**해 이 표의 숫자를 되살렸다(`composition/provider.py` 정본 · 검사가 04 와 잇는다). **백엔드 read timeout ≥60s 는 그대로다** — 재통보 불필요. ⚠ **`deadline` 은 감지·조립 뒤에 시작하므로 HTTP 총 시간에는 감지 시간이 더 얹힌다**(종전 설계부터 그랬다 · 99 #124), 예산 소진 신호는 템플릿 폴백. **백엔드 클라이언트 read timeout ≥60s 필요.** 다른 동기 API는 10s 유지 |
| 비동기 (202) | `/drafts` `/agents/*` | 202 + `job_id` → **완료 통지는 Kafka 이벤트 (7/15 확정)** · `GET`은 상태 보조 조회로 유지 · 🔴 **`/drafts` 응답 예산 480초**(종전 「총 5분」 — 콜당 90초 확정으로 재산정 · 8/20). ⚠ **이 문장은 계약 문면이며 현재 강제하지 않는다** — 초과 시 `failed` 로 떨구는 코드는 **0건**이다(실측 8/20 grep 전수). 잡은 계속 돈다 |
| 🔴 **동기 (200)** | `/labels/suggest` | 🔴 **잡을 안 만든다** — 한 요청 = 학부모 **한 명** · **1콜**이라 그 자리에서 계산해 돌려준다(2026-08-22 판정 · §3.7). ⚠ 종전에는 **202 + 폴링** 이었고 그 전에는 **202 + Kafka** 였다 — **트리거가 바뀌면서**(8/21 · §3.7) 형태가 두 번 따라왔다. 🔴 **202 를 쓰면 워커·드레인·`WorkerKind`·마이그레이션 여덟 자리가 딸려 온다** — 그 비용이 이 판정의 근거다. ⚠ 콜당 상한은 **아직 전역**이 받는다(`call_timeouts.yaml` 비어 있음 — 실측 뒤 채운다). |

#### 🔴 `refine` 도 같은 LLM 예산을 씁니다 `[신설 · 8/20]`

⚠ **종전에는 `/drafts` 에만 안내가 있었고 `refine` 은 빠져 있었다** — 실측이 찾았다(배포 종단 2026-08-20: refine **18.4초**). 두 경로가 **같은 콜당 상한**을 쓴다는 사실이 문서에 없었다(99 ㉫).

| 경로 | 최악 콜 수 | 최악 소요 | 백엔드 read timeout |
| --- | --- | --- | --- |
| `POST /counsel/drafts` | **5콜** (plan 1 + write 4) | **450초** | **480초 이상** |
| `POST /counsel/drafts/{job_id}/refine` | **4콜** (plan 없음 · write 4) | **360초** | **480초 이상** |

- **콜당 상한은 90초**입니다(2026-08-20 확정). 위 소요는 `콜당 90초 × 최악 콜 수` 입니다.
- 🔴 **`refine` 은 `plan` 을 다시 돌리지 않습니다** — 최초 생성이 고른 강조점을 이어받습니다. 그래서 초안(5콜)보다 1콜 적습니다.
- 실측 분포는 위 최악보다 훨씬 짧습니다(초안 21.0초 · refine 18.4초 · 배포 종단 n=1). **최악은 게이트 재생성이 상한까지 갔을 때입니다.**

### 2.5 사용량 한도 — 기능별 할당 + 일일 상한 `[제안]`

**할당 수치는 기획서 5.3 요금표를 따른다**(Free 20명·문항 100·상담 100·단건 10 / Standard 50명·300·300·30 / Pro 100명·500·500·50 — 기간 단위는 월 권장, 기획 확정 대상). 본 계약은 **소진 메커니즘**을 확정한다: 클로드식 단순 모델 — 일일 상한 병행 + 매일 자정(KST) 리셋, 이월 없음, "오늘 8/10 남음" 상시 표시.

| 구분 | 카운트 | 규칙 |
| --- | --- | --- |
| **상담 답변 초안** | 초안 생성 1 · refine 1턴 1 (같은 풀) | 플랜 할당 소모 · 일일 상한 병행 |
| **시험 문제 초안** | 문항 1개 = 1 (B 소유 기능이지만 미터링은 공통) | 〃 |
| **단건 리포트(수시)** | 1건 = 1 | 플랜 할당(10/30/50) + **일일 상한**(예: Standard 하루 10) — 반 전체를 단건으로 뽑아 일괄을 우회하는 것 방지 + 고토큰 원가 방어 |
| **일괄 작업** (야간·비동기) | **월별 리포트 일괄 생성**(매월 1일 전월분·재원생 전원 — 생성까지만, 승인·전달은 강사/HITL) · 상담팩 | 할당과 **별도**, 전 유료 티어 월 1회 기본(상담팩은 Pro 월 2회) |
| 카운트 제외 | 분류·태깅(캐시) · 감지(LLM 무관) · 라벨 제안(강사 요청) | 무제한 |

- **(7/15 확정 — BE-4)** 차단·카운트·잔여 표시는 **전부 백엔드 Billing 소유 — AI는 쿼터를 알지 못한다.** 한도 소진 시 백엔드가 AI 호출 자체를 하지 않으며, `meta.quota` 동봉은 폐기. 위 요금표·소진 규칙 표는 백엔드 집행 참고용으로 유지.
- **월별 리포트 대상 규칙(15일 규칙)은 백엔드 소유** — 15일 이전 등원생 포함 / 이후 등원생 그 달 생략. AI의 DataSufficiency 게이트(2주 미만 거부)가 우선(`[Open-10]`).

---

## §3. 엔드포인트

### 3.0 퀵 레퍼런스

| 엔드포인트 | 방식 | 언제 호출 | 돌려주는 것 |
| --- | --- | --- | --- |
| `POST /detect` | 동기 | 야간 배치 02:10 | 신호(`new`·`follow_up` 반별 TOP 3~5 + 상한 밖 `ongoing`·`return_care`) + display_label + lifecycle + 근거 + 브리핑 문장 |
| `POST /confirmations` | 동기 | 태그·라벨·분류·초안수정 확정 시 | ack (품질 평가셋 재료) |
| `POST /drafts` → `GET /drafts/{id}` | 202 | 문의 도착 즉시 · 리포트 주기 | 블록별 초안 + 근거 + 게이트 + status |
| `POST /drafts/{id}/refine` | 202 | 채팅형 다듬기(자유 지시 · 핑퐁) | 지시 반영 리비전 — 매 턴 게이트 재통과, 1턴 = 초안 할당 1 |
| `POST /classify` | 동기 | 문의 도착 즉시 | topic + sentiment + urgency 3축 + 축별 confidence |
| `POST /tags/suggest` | 동기 | 과제 입력 화면 | 영역·유형 제안 + 신뢰도 + 캐시 여부 |
| `POST /labels/suggest` | 🔴 **200** | 강사 요청 · 학부모 **한 명** | 라벨 제안 + 근거 인용(실존 검증 통과분) — 트리거·저장 정책은 §3.7 |
| `POST /counsel/drafts` → `GET` → `/refine` | 202 | **문의 도착 즉시** | 초안 1건 자동 생성 · 근거 인용 ≥1 · 다듬기(동기) — §3.9 |
| `POST /problems` → `GET /problems/{job_id}` | 202 | 강사가 출제를 요청할 때 | 세트 생성 잡 → 문항 **요약** 목록(본문·evidence 아님) — §3.11. 🔴 **v1은 `area_tag=language` + `passage` 없음만** · `type_tags`에 `apply` 금지 |
| `GET /health` · `GET /ready` · `GET /meta/versions` | 동기 | 상시 | 프로세스 liveness · DB readiness · capability별 선언 버전 — §3.10 |

---

### 3.1 `POST /v1/detect` — 야간 감지 `[Open-1: push 가정]`

> **사양 원본: `docs/part_a/09_detect_spec.md`(AI 확정 · 백엔드 전달본).** 여기는 요약이며 필드 주석·저장 규칙·lifecycle 판정표(§4)는 그 문서가 원본이다. 아래 예시와 09가 어긋나면 09가 정답.

| 방식 | 호출자 | 멱등 |
| --- | --- | --- |
| 동기 | 백엔드 배치 | `Idempotency-Key = tenant + analysis_date`(배치 실행 기준일 — 09 §2 [A 확정 7/28]) |

**Request** — 필드별 타입·필수 여부는 §4.1 표 · 09 §2 참조 (주석은 JSON5 스타일 — 실제 전송 시 제거):

```json
{
  "snapshot_meta": {
    "week_start": "2026-07-13",          // 이 주차의 월요일 — 피처 계산 기준 키
    "snapshot_hash": "sha256:...",       // 부록 A 규칙으로 백엔드가 산정 — alert_context 포함해 해시
    "term_context": "normal",            // normal | new_term | vacation — 신학기·방학 오경보 방지용
    "classes": [{ "class_ref": "cl_a1" }]  // 반 목록 — 경보 상한(반별 TOP 3~5, new·follow_up만 대상) 계산에 필요
  },
  "students":        [ { "...": "§4.1 · 09 §2 students 표 참조" } ],        // 재원생 전체 (consent 포함)
  "learning_events": [ { "...": "§4.1 · 09 §2 learning_events 표 참조" } ], // 지난 주차 증분만
  "detection_evidence": [ {              // ★(8/12 신설·optional) R2·R3·R5의 정본 근거 (09 §2-보강 · 99 #43)
    "kind": "assignment_window",         // assignment_window | weekly_activity | enrollment_transition
    "source_table": "assignment_week_summary",  // 백엔드 정본 테이블 **논리명** — AI는 SQL 식별자로 쓰지 않는다
    "record_id": "aws_20260810_st_8f2a", // 백엔드 원본 PK — 응답 evidence에 그대로 실린다
    "student_ref": "st_8f2a",
    "week_start": "2026-08-10",          // assignment_window · weekly_activity
    "expected_count": 3,                 // assignment_window만 — 0이면 「과제가 없던 주」(미제출 아님)
    "submitted_count": 0                 // assignment_window만 — expected 이하
  } ],                                   // ⚠ **없으면 R2·R3·R5는 fail-closed skip**(다른 기록을 근거로 삼지 않는다)
  "alert_context":   [ {                 // ★(7/16 신설) 최근 30일 경보 이력 — lifecycle 판정 입력 (09 §2·§4)
    "student_ref": "st_8f2a",
    "signal_type": "hidden_risk",        // 09 §1의 6값 중 하나
    "status": "open",                    // open | resolved
    "resolved_at": null,                 // resolved일 때 해소 일시 — "해소 후 2주" 쿨다운 기준
    "followed_up": false                 // 해소 후 팔로업 카드가 이미 나갔는지 (중복 방지)
  } ]
}
```

**Response 200:**

```json
{
  "data": {
    "signals": [{                        // TOP 3~5 상한은 new·follow_up에만 적용하며,
                                         // ongoing·return_care(R5)는 상한 밖으로 추가되어
                                         // signals 길이와 rank가 5를 초과할 수 있다
      "signal_id": "uuid",               // AI 신호 ID — Alert와 함께 저장(향후 강사 평가 회신 대비)
      "student_ref": "st_8f2a",
      "class_ref": "cl_a1",
      "rule_id": "R4",                   // R1~R6 — 내부 규칙 번호(로그용, 화면 미노출)
      "signal_type": "hidden_risk",      // 09 §1의 6값
      "display_label": "숨은 위기",        // ★(7/16 신설) 화면에 그대로 쓸 한글 문구(AI 확정) — 09 §1 표
      "score": 0.78, "rank": 2,          // score는 로그용(화면 미노출) · rank = 반 내 우선순위
      "advisory": false,                 // ★(8/3 신설) true면 알림·카드에서 빼고 학생 상세의 참고 표시로 — 아래 [A 확정 통보]
      "lifecycle": "new",                // ★(7/16 신설) new | ongoing | follow_up — AI 경보 생애 판정(09 §4)
      "brief": {                         // 브리핑에 바로 실을 한 줄 문장 (LLM 생성 + 왜곡 게이트 통과분)
        "text": "비문학 지문을 붙잡는 시간이 3주째 늘고 있어요 — 정답률은 아직 버티는 중이에요.",
        "gate_passed": true,
        "fallback_used": false           // true면 게이트 실패 → 템플릿 문장으로 대체된 것 (그래도 표시 가능)
      },
      "evidence": [{                     // 근거 — 항상 1건 이상 (없으면 신호 자체가 생성 안 됨)
        "source_table": "learning_event",
        "record_id": "le_1029",          // 백엔드 DB PK — 강사가 "근거 보기" 누르면 이걸로 원본 조회
        "summary": "acc_drop 근거 기록",  // ★(8/14) deprecated — 아래 [A 확정 통보] 참조
        "role": "trigger",               // ★(8/14 신설) trigger | baseline — 필수. 나머지 셋은 null 가능
        "observed": null,                // ★(8/14 신설) 그 기록 자신의 값 — 주 단위 집계에만 실린다
        "sample_size": null,             // ★(8/14 신설) 그 기록의 분모
        "occurred_on": "2026-07-03"      // ★(8/14 신설) 그 기록의 날짜 — 주간 집계는 주 시작일
      }],
      "metric": "accuracy",              // ★(8/14 신설) 이 신호가 무엇을 쟀나 — 단위는 규칙마다 다르다
      "observed": 0.51,                  // ★(8/14 신설) 판정 창 마지막 주의 관측값
      "baseline": 0.72,                  // ★(8/14 신설) 비교 기준 — 비교 안 하는 규칙은 null
      "sample_size": 20                  // ★(8/14 신설) 분모 — 규칙마다 뜻이 다르다
    }],
    "stats": {                           // 운영 지표(로그용, 화면 미노출)
      "students_evaluated": 58,
      "signals_raised": 3,
      "excluded_under_2w": 4,            // ★(7/16) 재원 2주 미만 제외 수 — 구 observed_only 목록을 숫자로 대체
      "capped_out": 2,                   // lifecycle 억제 후 new·follow_up 후보의 탈락 수만 (advisory 제외)
      "r1_threshold_pp": 17.4,           // ★(8/3 신설) 이번 실행에 실제로 쓴 R1 임계 — 아래 [A 확정 통보]
      "r1_threshold_source": "quantile", // quantile | fallback — 표본 부족 시 고정 15%p로 폴백
      "r1_pool_n": 384,                  // 분위 산출에 쓴 표본 수(8주 × 전 학생의 주간 하락폭)
      "rules_skipped": [{ "rule_id": "R4", "reason": "duration_missing", "students": 5 }]
    }
  }
}
```

> ### `[A 확정 통보 — 2026-08-14]` 근거 구조화 필드 8개 (BE 대상)
>
> 🔴 **응답에 필드가 늘었습니다. 기존 필드는 삭제·개명 0건입니다.**
>
> | 자리 | 필드 | 필수 |
> | --- | --- | --- |
> | `signals[]` | `metric` · `observed` · `baseline` · `sample_size` | 전부 nullable |
> | `signals[].evidence[]` | **`role`** · `observed` · `sample_size` · `occurred_on` | 🔴 **`role`만 필수** · 나머지 nullable |
>
> `role`은 `"trigger"`(판정을 성립시킨 기록) 또는 `"baseline"`(비교 대상이 된 기록)입니다.
>
> 🔴 **`signals[].observed`와 `evidence[].observed`는 다른 값입니다.** 앞은 **신호 전체**의
> 관측값이고 뒤는 **개별 레코드**의 값입니다. `submit_drop`에서 앞은 «연속 3주», 뒤는
> «그 주 제출 0건»입니다. 같은 이름이지만 같은 것이 아닙니다.
>
> 🔴 **`observed`의 단위가 규칙마다 다릅니다** — `submit_drop`은 **주 수**라 `%`로 찍으면
> 300%가 나갑니다. **규칙별 단위 정본은 `part_a/14_evidence_fields.md` §3-2′** 입니다
> (여기 복제하지 않습니다 — 같은 표가 두 곳에 살면 하나가 낡습니다).
>
> ⚠ **`role="baseline"` 행은 `volume_gap`에서만 옵니다.** 나머지 규칙은 기준선이 집계값이거나
> (직전 주들의 평균) 비교 자체를 안 해서 **가리킬 레코드가 존재하지 않습니다** —
> **누락이 아닙니다.** `acc_drop`의 «평소 대비»는 `signals[].baseline`에서 읽어 주십시오.
>
> ⚠ **근거 개수 상한이 3 → 최대 6으로 늘었습니다** — `trigger` 최대 3 + `baseline` 최대 3.
> 🔴 **최신 것부터 남깁니다**(2026-08-13 수정 · 종전에는 오래된 주가 남아 «이번 주» 근거가
> 잘려 나갔습니다).
>
> ⚠ **`summary`는 deprecated입니다** — 그 안의 숫자를 전부 구조화 필드로 옮겼습니다.
> **필드는 지우지 않으니** 전환은 편하실 때 하시면 됩니다.
>
> 🔴 **배포 순서**: 백엔드 DTO·마이그레이션이 **먼저**입니다. AI가 먼저 나가면 역직렬화가
> 깨집니다 — `handoff/2026-08-11_be_connection_runbook.md` §2 ⓗ에 재배포 금지를 걸어 뒀습니다.

> ### `[A 확정 통보 — 2026-08-03]` 감지 v1.5 ①② 반영 (BE·FE 대상)
>
> 외부 대규모 로그 검증([`part_a/13_threshold_validation.md`](part_a/13_threshold_validation.md)) 결과에 따른 확정분 둘이다. **이의는 회신으로.**
>
> **① R4(숨은 위기)를 advisory로 강등한다 — 판정은 그대로다.**
> R4는 지금처럼 평가·기록되고 **응답에도 그대로 실린다**(evidence 포함). 바뀌는 것은 소비 방식뿐이다 — **TOP N 랭킹 비참여 · 상한 슬롯 미소비 · `capped_out` 미산입**. 응답의 **`advisory: true`** 로 구분되니 **알림·신호 카드에서 빼고 학생 상세의 참고 표시로** 보내면 된다.
> ⚠ **`signal_type`에서 `hidden_risk`가 사라지는 게 아니다** — 값 삭제가 아니라 **주장 강도**를 내린 것이다. 그리고 **R4가 다른 규칙과 함께 발화하면 그 경보는 정식**이다(`advisory`는 병합 규칙이 전부 R4일 때만 `true`).
> 근거: 성과 하락 선행성은 근거를 찾지 못했고, 활동 종료 연관은 방향성이 있으나 검열 분리 불가로 확정할 수 없다(13 §3). 파일럿에서 우리 데이터로 재검증한다.
>
> **② R1(정답률 하락) 임계를 고정 −15%p에서 발동률 목표 방식으로 바꾼다.**
> 임계 = 테넌트 풀(8주 × 전 학생) 하락폭 분포의 **하위 5% 분위**. 표본 100 미만이면 **고정 15%p로 폴백**한다. **적중률은 고정 임계와 동등**하고, 바뀌는 것은 **알림 수가 설계값으로 고정**된다는 성질이다(13 §4-1).
> ⚠ **BE 작업은 없다** — 요청 계약 무변경이고, AI가 스냅숏에서 산출한다. 다만 실행마다 임계가 달라지므로 **`stats.r1_threshold_pp`·`r1_threshold_source`·`r1_pool_n`** 을 함께 싣는다. 강사가 "왜 오늘은 안 떴냐"고 물을 때 답할 근거다.
>
> **응답 계약 변경 요약:** `signals[].advisory`(bool) 1필드 · `stats` 3필드 추가. **기존 필드는 무변경**이며 추가분은 전부 기본값이 있다.

**규약:** evidence 빈 신호는 스키마상 불가 · **`observed_only` 목록은 제거(7/16)** — AI는 `stats.excluded_under_2w` 숫자만 내고, "관찰 중"(재원 14일 미만) 표시는 백엔드가 `enrolled_at`으로 직접 계산(09 §3) · **lifecycle 판정은 AI 소유**(09 §4 · 쿨다운 2주) · **Alert 생성·상태 관리는 백엔드 소유** — AI는 신호 산출까지.

> ✅ **A+BE+FE 확인 완료(2026-07-30) — 감지 상한 응답.** TOP 3~5 상한은 `new`·`follow_up`에만 적용하며, `ongoing`과 `return_care`(R5)는 상한 밖으로 추가되어 `signals` 길이와 `rank`가 5를 초과할 수 있다. **백엔드·프론트 확인 결과 양쪽 모두 응답 길이 ≤5 또는 `rank` ≤5를 가정·검증하지 않는다**(백엔드 DTO에 rank 상한 제약 없음 · 프론트 렌더링 개수 제한 없음). 파생 정의: `signals_raised`=상한 밖 합류를 **포함한** 최종 반환 신호 수 · `capped_out`=lifecycle 억제 후 **`new`·`follow_up` 후보의 탈락 수만** · `rank`=반 내 **최종 표시 순번**(통과분 뒤 `ongoing`·R5, 5 초과 가능). 정본은 `part_a/04_threshold_config.md` §3 · `09_detect_spec.md` §4 · 99 #14.

---

### 3.2 `POST /v1/feedback` — 경보 평가 회신 `🕓 보류(7/16)`

🕓 **보류(7/16) — 임계 캘리브레이션 재개 시 활성.** API·화면 버튼 모두 이번 범위에서 뺀다. 단 `/detect` 응답의 `signal_id`는 향후 회신 대비해 백엔드가 계속 저장한다(09 §3). 보류 기간의 오경보 보정은 threshold 시트 §5 섀도 모드 수동 리뷰가 대신한다.

### 3.3 `POST /v1/confirmations` — 제안 확정 회신 `[Open-7: 분리 제안]`

```json
// Request — 태그·라벨·분류 제안을 강사가 확정/거절/수정한 결과 회신
{
  "kind": "tag",                        // tag | label | classification | draft_edit
  "suggestion_id": "uuid",              // 제안 API가 반환했던 ID
  "action": "corrected",                // confirmed | rejected | corrected
  "corrected_value": {                  // corrected일 때만 — 강사가 고친 최종값
    "area_tag": "language",             // 수능 6영역 enum (Open-11 확정 7/15)
    "type_tag": "concept",
    "item_format": "mcq"
  }
}
// Response 200
{ "data": { "accepted": true } }
```

kind: `tag | label | classification | draft_edit`(강사 수정 diff → 문체 프로필 재료 — 예시는 `05_request_json.md` §3 참조). **확정 전 제안은 어디에도 반영되지 않는다**(태그는 learning_event에, 라벨은 초안 생성에 미사용) — 이 보장은 백엔드 몫.

#### (8/5 · P2-c) 구현 상태와 규약

🔴 **`suggestion_id`는 kind마다 다른 네임스페이스의 키다** — 단일 UUID 공간이 아니다.

| `kind` | `suggestion_id` | v1 구현 |
| --- | --- | --- |
| `tag` | `TAG_SUGGESTION.id` | ❌ **400** `kind_not_implemented` |
| `label` | `LABEL_SUGGESTION.id` | ❌ **400** `kind_not_implemented` |
| **`classification`** | **`inquiry_ref`**(§3.5 응답이 에코한다) | ✅ **구현** |

🔴 **`classification`은 초안 재생성과 짝이다** — 강사가 문의 유형을 정정하면 BE는 **이 API(평가셋 기록)와 `POST /v1/counsel/drafts`(새 키 + 정정된 `topic` · §3.9)를 둘 다** 호출한다. **하나만 하면 초안이 안 바뀌거나 평가셋이 빈다**(상세는 §3.9 재요청 규약).
| `draft_edit` | `job_id`(§3.9) | ❌ **400** `kind_not_implemented` |

**미지원 3종을 400으로 거절하는 이유** — 제안 **생성기가 없다**(`TAG_SUGGESTION`·`LABEL_SUGGESTION` 적재 0건 · `label_suggestions`는 v1 상수 `[]`). 확정할 대상이 없는데 받아서 조용히 버리면 BE가 "저장됐다"고 오해한다.

**`classification`이 `inquiry_ref`인 이유** — 문의 1건 = 분류 1건이고 **BE가 이미 갖고 있는 값**이라 왕복이 없다. 응답에 새 UUID를 노출하면 BE가 관리할 식별자만 는다(§3.9 refine이 `job_id`로 통일된 것과 같은 판단).

**`corrected_value`(classification)** — 3축 **전부 nullable**이다. 축이 독립이므로 **부분 정정**이 성립한다(topic만 고치고 나머지는 그대로).

```json
{ "kind": "classification", "suggestion_id": "iq_204", "action": "corrected",
  "corrected_value": { "topic": "grade" } }   // sentiment·urgency는 안 바꿈
```

🔴 **BE가 알아야 할 규약 2건**

1. **`rejected`는 `classification`에서 400이다**(`action_not_supported`). 3축은 값이 반드시 있어야 하는 축이라 "거절"이 정의되지 않는다 — tag·label은 "이 제안을 안 쓴다"가 성립하지만 분류는 아니다.
2. **예측과 같은 값을 보내도 정정으로 세지 않는다.** 저장 층이 예측과 대조해 **다른 축만** `corrected_*`에 남긴다(P2-b 기록 규약 ①). 강사가 드롭다운을 열어 같은 값을 다시 골라도 **재분류율이 부풀려지지 않게** 하는 장치다. `reviewed_at`은 어느 경우든 기록된다.

**404** — 대상 분류가 없을 때다(폴백이라 적재 안 됐거나 분류를 부른 적이 없다). **헤더**: `X-Tenant-Id`·`X-Request-Id` 필수, **`Idempotency-Key` 없음**(자연키 갱신이라 재시도가 안전하다).

---

### 3.4 `POST /v1/drafts` — 초안 생성 (202)

| 방식 | 호출자 | 멱등 |
| --- | --- | --- |
| 202 → `GET /v1/drafts/{draft_id}` | 백엔드 (문의 도착 즉시 — 사전 생성 / 월간 리포트 주기) | `Idempotency-Key = inquiry_ref` 등 |

**Request:**

```json
{
  "kind": "reply",                     // reply(문의 답변) | report(월별 일괄 — 매월 1일 전월분, 할당 미소모)
                                       // | report_single(단건 수시 — report_single 할당+일일 상한) | counsel_pack_single
  "student_ref": "st_8f2a", "guardian_ref": "gd_11b0",   // 전부 alias — 실명 매핑은 백엔드 vault만 보유
  "label_snapshot": {                  // 강사가 확정한 학부모 라벨 4축 — 이 시점 값으로 동결(생성 중 라벨 변경 무영향)
    "comm": "narrative",               // data(수치 선호) | narrative(서사 선호)
    "interest": "attitude",            // grade | attitude | admission
    "sensitivity": "anxious",          // anxious(완곡 강화) | direct(결론 선행)
    "frequency": "frequent"            // frequent(짧게·변경분만) | monthly(충실하게)
  },
  "inquiry": {                         // kind=reply일 때만
    "inquiry_ref": "iq_204",
    "body_text": "요즘 아이가 힘들어하는 것 같은데...",   // 백엔드 1차 마스킹 통과본 (Open-4d)
    "received_at": "2026-07-10T21:04:00+09:00"
  },
  "context": { "...": "§4.2 표 참조 — interventions·comm_history·(리포트면) benchmarks" }
}
```

**Response (GET) 200:**

```json
{
  "data": {
    "draft_id": "uuid",
    "status": "generated",             // generated | template_only | rejected_insufficient | failed — 아래 status 값 설명 참조
    "status_reason": null,             // 미생성/실패 시 사유 코드 (화면 문구 번역은 백엔드)
    "classification": { "topic": "grade", "sentiment": "complaint", "urgency": "immediate" },   // 분류ⓑ 3축 동봉 — 인박스 정렬용
    "blocks": [{                       // 초안은 블록 단위 — 강사가 블록별로 수정 가능하게
      "seq": 1,
      "block_type": "fact",            // greeting | fact | suggestion | closing | chart_analysis(리포트)
      "content": "서연이는 6월 한 달 지문 42개·312문항을 성실히 풀었고...",
      "evidence": [{ "source_table": "learning_event_agg", "record_id": "agg_w27" }],  // 블록별 근거 — "근거 보기" UI용
      "empty_reason": null             // 값이 있으면 = 재시도 3회 소진 섹션 → "직접 작성해 주세요" 표시
    }],
    "gates": [{ "gate": "SourceGrounding", "passed": true }, { "gate": "ToneSafety", "passed": true }]
  }
}
```

**status 값:** `generated` 정상 · `template_only` 데이터 무관 문의(일반 템플릿만) · `rejected_insufficient` 데이터 부족(정상 상태!) · `failed` LLM 장애 등(사유 포함). 블록 `content`가 비고 `empty_reason`이 있으면 = 재시도 3회 소진 섹션.

**리포트(kind=report) 전용 규약 — 차트별 해설 블록 `[제안]`:** 리포트는 차트(영역별 약점 현황 · 보강 후 개선 추이 · 전국 백분위) 단위로 구성되며, 블록에 `block_type: "chart_analysis"` + `chart_ref`가 붙는다. **차트 수치·개선율·백분위는 전부 코드/백엔드가 확정하고 LLM은 해설 문장만** 생성(SourceGrounding이 문장 속 수치를 benchmarks·집계와 대조). **노출 정책 3층:** 학생 = 비교 일절 비노출 / 학부모 리포트 = 전국 백분위만(반 평균·석차 비노출 — teacher_only 데이터는 컨텍스트에서 구조적 제외) / 반 평균 = 강사 내부 화면 전용(성적 지표 관리 도구). 하위 백분위는 수치는 사실대로 + 문장은 성장 추이 중심(라벨 anxious 시 완곡 강화). **(7/15 확정 — BE-5)** 월별 일괄 생성은 학생별 N회 호출이 아니라 **일괄 1회(Kafka 배치)** — AI는 내부에서 학생 단위로 처리·부분 실패 격리하고 결과를 학생별 이벤트로 발행. 스트림 스키마는 v1.0 부록.

**`POST /v1/drafts/{id}/refine`** — **채팅형 다듬기 (핑퐁)** `[제안]`:

```json
// Request — 강사가 초안 아래 채팅창에 입력하면 백엔드가 그대로 전달
{
  "scope": "block",                    // whole(전체) | block(특정 블록만)
  "block_seq": 2,                      // scope=block일 때만
  "instruction": "마지막에 다음 상담 일정을 제안하는 문장 하나 넣어주세요. 전체적으로 조금 더 짧게.",
  "preset": null                       // UI 버튼은 preset으로: softer | conclusion_first | shorter (instruction과 병용 가능)
}
// 롤백은 { "revert_to": 2 } — 2턴째 결과로 복원, 할당 미소모
```

- 강사가 자유 문장으로 지시하며 초안을 반복 다듬는 대화형 루프. 202 → GET 시 `revision_no` 증가 + `revisions[]` 턴 이력(롤백: `{"revert_to": n}` — 할당 미소모).
- **핑퐁의 가드레일:** ① 매 턴 결과도 게이트 전체(Evidence·SourceGrounding·ToneSafety) 재통과 — **강사 지시가 게이트를 이기지 못한다**("정답률 95%라고 써줘"는 근거 부재로 차단+사유 반환) ② 다듬기 1턴 = 상담 초안 할당 1 소모라는 과금 규칙은 **백엔드가 집행(7/15 — AI는 무제한 처리·쿼터 무관)**, 잔여 표시도 백엔드 ③ 지시문·수정 이력은 문체 프로필 재료로 축적.

**규약:** 승인·발송은 백엔드(HITL) — 다듬기는 초안까지, 이 API에 발송 개념 없음. `label_snapshot`은 **백엔드 확정 라벨만**(ai_suggested 미포함).

---

### 3.5 `POST /v1/classify` — 문의 분류 (동기)

```json
// Request — 학부모 문의 도착 즉시 (초안 생성과 별도로 먼저 호출해도 됨 — 인박스 정렬용)
{ "inquiry_ref": "iq_204", "body_text": "여름방학 특강 시간표가 궁금합니다" }
// Response 200 (동기 — 수 초 내)
{ "data": { "topic": "schedule", "sentiment": "normal", "urgency": "normal",
            "confidence": { "topic": 0.95, "sentiment": 0.88, "urgency": 0.91 },
            "classified": true, "fallback_reason": null } }
// Response 200 — 분류하지 못한 경우(500이 아니다 · error_codes §2.5)
{ "data": { "topic": "etc", "sentiment": "normal", "urgency": "normal",
            "confidence": { "topic": 0.0, "sentiment": 0.0, "urgency": 0.0 },
            "classified": false, "fallback_reason": "tripwire_blocked" } }
```

**3축 독립 분류다** — 축마다 값 집합도 용도도 다르고, 한 축의 오분류가 다른 축을 오염시키지 않는다(Zendesk intelligent triage와 같은 구조). ⚠ `confidence`는 **축별 객체**다 — 3축이 독립이므로 확신도도 축마다 다르다(Zendesk는 필드마다 별도 confidence를 단다).

| 축 | 값(enum 강제 — 목록 밖 값은 나올 수 없음) | 쓰이는 곳 |
| --- | --- | --- |
| `topic` | `grade` \| `schedule` \| `counsel_request` \| `etc` | 초안 종류(§3.9 `template_only`) · 정렬 |
| `sentiment` | `normal` \| `complaint` | 인박스 최상단 정렬 · 완충 강도 |
| `urgency` | `immediate` \| `normal` | 정렬 |

> 🔴 **(8/5) `complaint`가 `topic`에서 `sentiment`로 이동했다.** 상세는 `part_a/01` §4-ⓑ 참조. **BE는 `inquiry.topic`에 `complaint`를 보내면 400이다.**

**응답에 `inquiry_ref`를 에코한다**(8/5) — 요청값 그대로다. BE가 아는 값이지만 **응답만 보고 다음 호출을 구성할 수 있어야** 계약이 폐쇄 회로가 된다. 확정 회신(§3.3)의 `suggestion_id`가 **이 값**이다.

🔴 **캐시 규약(8/5 · P2-c)** — 같은 `(tenant_id, inquiry_ref)` 재호출은 **저장된 예측을 그대로 돌려주고 LLM을 부르지 않는다**(§1의 "분류·태깅(캐시)" 규정 · 태깅 §3.6 선례와 동형). 부수 효과로 **재시도가 멱등 키 없이 안전**해지고 같은 문의엔 항상 같은 답이 나간다.
⚠ **`classified=false`는 캐시되지 않는다** — 적재 자체를 안 하므로(평가셋 오염 방지) 재호출하면 **다시 시도**한다. redaction·파싱 실패는 일시적일 수 있다.

**헤더 규약** — `X-Tenant-Id` · `X-Request-Id` **필수**, **`Idempotency-Key` 없음**(부작용 없는 동기 호출이고 멱등 저장이 없다 — 같은 본문은 결정론 설정으로 같은 결과다).

🔴 **`body_text`는 원문이다**(counsel의 `text_masked`와 다르다). **AI가 2차 redaction을 적용한 뒤 LLM에 보내며**(masking_redaction §3·§4), ⟪확인필요⟫가 남거나 전송 직전 잔여 흔적이 발견되면 **LLM을 호출하지 않고** `classified: false`로 응답한다. 원문은 로그·에러 detail 어디에도 남지 않는다(§2.3).

**`classified` · `fallback_reason`** — 분류 실패는 **200**이다. `classified: false`면 `etc`를 확신 있는 판정으로 읽지 말고 **정렬을 적용하지 않은 채 시간순으로** 둔다(`error_codes` §2.5). 사유는 **enum 2종**(`tripwire_blocked` | `parse_exhausted`)이며 자유 문자열이 아니다(`error_codes` §2.7 규칙 1). `classified=true`면 `fallback_reason`은 반드시 `null`이고 그 반대도 마찬가지다 — 짝이 어긋나면 스키마가 거부한다(규칙 2). **LLM 장애는 폴백이 아니라 503**이다.

⚠ **`⟪확인필요⟫`가 남아도 분류를 계속한다**(8/5 정정) — 가려진 텍스트는 이미 안전하고 분류에 이름은 필요 없다. 종전 `redaction_uncertain` 사유는 없어졌다. 전송 직전 트립와이어가 **잔여 흔적**을 발견한 경우만 `tripwire_blocked`로 폴백한다(그건 '안 가려진 게 남았다'는 신호라 성격이 다르다).

⚠ **`confidence`는 LLM 자기보고이며 캘리브레이션되지 않았다** — 확률로 읽지 말 것. 임계값을 아직 정하지 않은 이유가 이것이다(아래).

**사용 범위 — 되돌릴 수 있는 것까지다**(`part_a/01` §4-ⓑ). 학부모에게 나가는 자동 응답에는 쓰지 않는다(승인·발송은 전부 HITL — 백엔드도 준수). 되돌리기 경로는 §3.9 재요청 규약이 보장한다.

**confidence에 따른 자동화 강등 — Salesforce Einstein 3단 패턴.** 낮은 확신은 "`etc`로 넣기"가 아니라 **자동화 수준을 한 단계 내리는 것**으로 처리한다.

🔴 **강등 판단의 주체는 BE다.** `confidence`는 **이 응답에만** 실리고 `POST /v1/counsel/drafts` 요청에는 **없다**(`inquiry`는 `inquiry_ref`·`topic`·`urgency`·`received_at`·`text_masked` 5필드뿐) — **AI는 받은 `topic`을 확정값으로 신뢰하며 확신도를 재평가하지 않는다.** 분류는 이 절에서 끝나고 초안 생성은 확정값을 받는다. BE가 임계값 미만이면 `topic`을 초안 요청에 싣지 않거나(`etc`로 보내거나) **강사 확인을 먼저 받는다**.

| `confidence.topic` | 처리 |
| --- | --- |
| 임계값 이상 | `topic`을 확정값으로 사용 — `template_only` 분기 포함 |
| 임계값 미만 | `topic`은 **표시·정렬에만**. 초안은 **일반 경로로 생성**하고 강사에게 "일정 문의로 보입니다" 배지만 표시 |

이 방향이 fail-safe다 — 확신이 낮을 때 정상 초안을 만들어두면 강사가 안 쓰면 그만이지만, 반대(확신 낮은데 `template_only`)는 근거가 있는데도 초안이 사라진다.
⚠ **임계값 숫자는 여전히 미정이다**(99 ⓐ) — 실측 데이터가 없다. P2-c(#88)로 **관측 경로는 열렸지만**(적재 + 확정 회신) 파일럿 전이라 평가셋 크기가 0이다. `reviewed_at IS NOT NULL`인 행이 쌓여야 confidence 구간별 실제 정확도를 그릴 수 있다. 🔴 **그때까지 BE는 `classified`만 보고 `confidence`로 분기하지 않는다** — 이게 현행 규약이다. 재분류율은 §3.9 ⓐ(confirmations) 호출에서 나오므로 **BE가 정정 시 그 API를 부르지 않으면 임계값을 영원히 못 정한다**. Salesforce 권고도 "Start in recommendation mode, and monitor accuracy... before setting up auto-triage"다.

### 3.6 `POST /v1/tags/suggest` — 태그 제안 (동기·캐시)

```json
// Request — 강사가 과제/채점 입력 화면에서 제목을 입력하는 순간 호출
{
  "source_kind": "trackB_grading",     // trackA_upload | trackB_grading
  "title_text": "6월 모의고사 비문학 대비 #3"
}
// Response 200 — 입력 폼에 미리 선택된 태그로 표시 → 강사 원탭 확정(→ /confirmations)
{
  "data": {
    "suggestion_id": "uuid",
    "area_tag": "reading",             // 수능 6영역 enum (Open-11 확정 7/15): reading·literature·speech·writing·language·media
    "type_tag": "infer",               // fact | infer | critic | concept | apply(v1 미산출·예약)
    "item_format": "mcq",              // mcq | short | essay
    "confidence": 0.92,
    "cached": true                     // true = 같은 명명 패턴 캐시 히트 (LLM 호출 0회 — 비용 없음)
  }
}
```

**강사 확정(→ /confirmations) 전 learning_event 반영 금지**는 백엔드 책임. 동일 명명 패턴은 캐시 히트(LLM 0회).

### 3.7 `POST /v1/labels/suggest` — 라벨 제안 (🔴 **강사 요청**·**동기 200**·학부모 **한 명**)

🔴 **v1 응답 스키마 확정 (2026-08-24)** — 이 절의 요청·응답 **키와 상태코드는 고정**이다.
⚠ 값(콜당 상한·버전 문자열)은 실측에 따라 갱신되지만 **형태는 안 바뀐다.**
🔴 바꾸려면 **BE 합의가 선행**한다.
⚠ 🔴 **직전 판(8/24 오전)은 「확정 보류」였다** — 예시가 `axis`·`value` 를 최상위에 두는데
실제는 `label` 중첩이라 갈렸다(99 #215). **판정: 중첩 유지 · 예시를 고쳤다.**

```json
// Request — 🔴 **학부모 한 명**. 강사가 라벨 검토함에서 그 학부모를 열 때 보낸다
//           (소통 이력 5건 이상 + 라벨 미설정인 대상만)
{
  "guardian_consent": null,            // optional · 🔴 v1 미사용 — 자리만 예약한다(처리 로직 없음)
                                       //   필요해지는 시점: (나) 새 라벨 발굴에서 동의가 개정되면
                                       //   재동의 안 한 학부모가 생긴다. 값이 실제로 갈리는 첫 순간이다.
  "guardian_ref": "gd_11b0",
  "history": [                         // 백엔드 1차 마스킹 통과본 (Open-4d) · 5건 이상
    { "record_id": "cm_88", "direction": "inbound", "text": "숫자로 정리해 주세요", "at": "..." }
  ]
}
// Response **200 (동기)** — 학부모 360 화면에 '점선 칩'으로 표시 → 강사 확정(→ /confirmations) 전 초안에 미사용
{
  "data": {
    "suggestions": [{
      "suggestion_id": "uuid", "guardian_ref": "gd_11b0",
      // 🔴 **왜 중첩인가** — `LabelSuggestion(axis, value)` 은 **재사용 모델**이고
      //    `SuggestedLabel` 이 그것을 **품어서** 쓴다. 4축 값의 정의를 **한 곳**에 두려는
      //    것이다(`contracts/counsel.py` 의 `LabelSuggestion` docstring). 평평하게 펴면
      //    그 재사용이 깨지고 **4축 정의가 두 곳으로 갈린다.** ⇒ 중첩이 계약이다(99 #215).
      "label": { "axis": "comm", "value": "data" },  // 4축 enum만 — 자유 텍스트 라벨은 스키마상 불가
      "confidence": 0.86,
      "evidence_quotes": [               // 제안 근거 인용 — 실제 이력에 실존하는 문장만 (실존 검증 실패 시 제안 자체 폐기)
        { "record_id": "cm_88", "quote": "숫자로 정리해 주세요" }
      ]
    }]
  }
}
```

**인용 실존 게이트 통과분만 반환** — 인용이 이력에 없으면 제안 자체가 폐기됨. 확정 전 초안 생성에 미사용.

🔴 **응답 의미론 (2026-08-24 확정 · v1)** — 🔴 각 줄 끝에 **그 문면을 무는 검사**를 적는다
(«문서에 적었다» 로 끝내지 않는다 — 표·목록을 아무도 안 물던 자리다).

① `suggestions: []` — **정상 200** 이다. 「게이트를 통과한 제안이 없다」는 뜻이고, 모델이
근거를 못 찾았거나 인용 실존 게이트가 전부 폐기한 경우다. 🔴 강사 화면에는 **「제안 없음」**
으로 표시한다. **오류가 아니다.**
  · 검사: `tests/ai/contract/test_labels_response_semantics.py::test_a_fully_dropped_request_is_a_normal_200`

② 🔴 이력이 **마스킹 문지기에 전부 걸려 0건**이 되면 `500` + `reason` 이다.
⚠ 「제안이 없다」(①)와 **다른 사건**이다 — 우리가 계산을 **못 한** 것이다.
🔴 **4xx 가 아닌 이유**: 강사 입력은 조건을 만족했다. **우리 마스킹이 걸러서** 생긴 일이다.
🔴 **200 이 아닌 이유**: BE 가 성공으로 처리하면 ①과 **구분이 사라진다**(빈 칩 영역이 뜨고
「못 했다」가 「없다」로 보인다).
⚠ 사유 상세·본문은 안 싣는다(원문 노출 방지 · `error_codes` §4).
⚠ 🔴 `meta` 에 사유를 실어 200 으로 내는 안은 **버렸다** — `meta` 는 `{execution_id, versions}`
둘뿐이고 늘리려면 `api/envelope.py`(**양자 승인 13목록**)를 열어야 한다. 라벨 하나 때문에
**공용 envelope 를 늘리지 않는다.**
  · 검사: `tests/ai/contract/test_labels_response_semantics.py::test_all_history_blocked_is_a_500_not_an_empty_200`

③ 인용 실존 게이트는 **제안 단위**로 버린다 — 인용이 여럿인 제안에서 **하나라도** 실존하지
않으면 **그 제안 전체**를 버린다. 🔴 「남은 인용이 하나면 유지」로 안 하는 이유: 지어낸 인용이
진짜에 **묻어서** 통과한다.
  · 검사: `tests/ai/unit/composition/labels/test_labels_generator.py::test_a_forged_quote_does_not_take_the_true_ones_with_it`

④ 같은 `(axis, value)` 는 **두 번 오지 않는다** — 근거 인용을 합쳐 **한 건**으로 낸다.
`confidence` 는 합친 것들 중 **최댓값**이다.
  · 검사: `tests/ai/unit/composition/labels/test_labels_generator.py::test_the_router_actually_merges_after_the_gate`

⑤ `confidence` 는 **`0.0` 이상 `1.0` 이하**다(계약이 막는다 — `SuggestedLabel.confidence`).
🔴 **범위 밖 값은 500 이 아니라 파서가 그 줄을 버린다**(실측 8/24 — `parse_suggestions` 가
`ValidationError` 를 받아 드롭한다). ⇒ 나머지 제안은 산다. ⚠ 🔴 **그 처리가 맞는지는 판정
대기**(99 #207) — 지금 문면은 **현행을 적은 것**이지 정한 것이 아니다.
  · 검사: `tests/ai/contract/test_labels_response_semantics.py::test_an_out_of_range_confidence_drops_only_that_line`

🔴 **완료 통지 개념이 없다 — 동기 200 이라 응답이 곧 결과다(2026-08-22 판정).** Kafka 이벤트도 폴링도 쓰지 않고 **`job_id` 를 발급하지 않는다.** ⚠ 종전 문면은 **202 + 폴링 시절**의 것이다(8/22 이전) — «완료 통지는 폴링(`GET`)이다» 라고 적혀 있었고, 그 앞에는 «완료 통지는 Kafka 이벤트» 였다. 🔴 **형태가 세 번 바뀌는 동안 이 문단만 두 번 뒤처졌다.** ⚠ **BE 통보 대상**이다 — 기대하던 이벤트도, 폴링할 `GET` 도 없다.

🔴 **전제 — `history` 는 앱 경로 문의만이다(8/21 확정).** 앱 밖 소통(전화·문자·대면 기록)은 실리지 않는다. ⇒ 이력이 있는 학부모는 **앱 가입자**이고, **가입 시 필수 동의**를 마쳤다 — 동의 없는 학부모가 이 요청에 실릴 경로가 없다.
⚠ 🔴 **이 전제가 깨지는 방식은 런타임 값이 아니라 「새 유입 경로」다** — 앱 밖 기록을 넣는 경로가 생기면 **이 문장부터 고친다**(그때 `guardian_consent` 가 열린다 · `part_a/12_label_discovery.md` §6 ②도 같이 본다).

🔴 **왜 「한 명 · 동기」인가(2026-08-22 판정)**
① 한 학부모의 4축은 **그 학부모 이력**에서 나온다 — 여러 명을 한 콜에 섞으면 **한 명의 오류가 전체를 죽이고** 인용 실존 게이트가 **학부모별로 안 갈린다**.
② ⚠ 🔴 **`guardians[]` 배열은 「주간 배치」 시절의 형태다**(8/21 이전). 트리거를 「강사 요청」으로 바꿀 때 **형태를 같이 안 봤다** — 그 잔여를 여기서 걷는다.
③ 🔴 **동기로 갈 수 있는 이유** — 한 요청 = **1콜**이고 입력이 「이력 5건」이라 짧다. counsel 은 잡당 최악 **5콜 · 480초**라 202 가 필수였고, 라벨은 그 축이 아니다. ⚠ **콜당 실측은 아직 없다** — 구현 뒤 잰다(`llm/call_timeouts.yaml` 은 비워 두고 전역 상한이 받는다).
④ **부분 실패가 자연스럽다** — 한 명이 실패해도 다른 요청은 산다.
🔴 ⇒ **워커·드레인·`WorkerKind`·마이그레이션이 하나도 안 필요하다.** 202 를 쓰면 그 여덟 자리가 딸려 온다.

🔴 **이력이 마스킹 문지기에 걸리면 그 건만 빼고 진행한다(2026-08-24).** AI 는 받은 이력을 **한 건씩** 자기 마스킹 규칙에 태우고 **걸린 건만 제외**한 뒤 나머지로 제안을 만든다 — 🔴 **걸러진 뒤 1건만 남아도 조립한다.** 위 「5건 이상」은 **BE 의 대상 선정 조건**이지 AI 의 조립 조건이 아니다(근거가 약하면 **인용 실존 게이트**가 뒤에서 떨어뜨린다). ⚠ 🔴 **전량이 걸려 0건이면 `suggestions: []` 가 아니다** — 그건 «게이트가 전량 드롭했다» 의 자리이고, 이 경우는 «**우리가 못 만들었다**» 라 **실패로 낸다**(지금은 500 · 강사 화면 문면은 미정 · 99 #194). ⚠ 드문 경우다 — 실측 오탐률(20건 중 2건)에서 전량이 걸릴 확률은 낮다.

⚠ 🔴 **제안은 재실행 시 달라질 수 있다(2026-08-24 실측).** 같은 이력을 두 번 보내면 **다른 제안 조합**이 올 수 있다 — 실측: 같은 이력 2회에 «comm+interest» vs «comm+frequency+interest». 🔴 **재시도가 멱등이 아니다** — BE 가 타임아웃 뒤 재시도하면 강사가 **다른 결과**를 본다. ⚠ 원인은 모델 쪽이다(`temperature` 를 안 싣고 `seed` 를 싣는데, 저장소 실측이 «같은 seed 8회에 3종» 을 이미 기록했다 · 99 #203). ⚠ **제안은 강사 확정 전 참고**라 치명적이지 않지만, **BE 가 「같은 요청 = 같은 응답」을 가정하면 안 된다.**

🔴 **저장 정책 — 제안 API 는 아무것도 저장하지 않는다.** 🔴 **동기로 계산해 바로 돌려주고 끝난다** — 제안을 **어디에도 안 쓴다**(테이블·캐시·원장 전부). 강사가 수락하면 `label_snapshot` 으로 확정되는 것은 **확정 경로**(`/confirmations`)의 일이다. ⇒ 🔴 **새로 영속되는 개인 데이터가 0이다** — 「버린다」가 아니라 **애초에 안 만든다**.
⚠ 🔴 **(8/22 정정)** 종전 문면은 «counsel 초안과 같은 축출 정책 · 제안 행은 버린다» 였다 — 그건 **202 + 잡** 을 전제한 말이고, 동기가 되면서 **보관 자체가 없어졌다.**
⚠ 🔴 **집계 `(축, 제안값, 확정값)` 은 확정 경로에서 남긴다** — `guardian_ref` **없이**. 개인에 대한 판단이 아니라 **우리 모델의 성적표**이고 `part_a/12_label_discovery.md` §6 착수조건 ③(섀도 지표)의 재료다. ☐ **미구현** — 제안 API 의 일이 아니다.

---

### 3.8 `/v1/imports` — 스마트 데이터 이전 🔴 **(2026-08-22 삭제 — v1 범위 밖)**

> 🔴 **이 엔드포인트는 없다.** import 축은 **v1 에서 개발하지 않기로** 했다(사람 결정 · 승우님 확인 완료 · B 축 참조 0건).
> `POST /v1/imports` · `GET /v1/imports/{job_id}` · `POST /v1/imports/{job_id}/confirm` **셋 다 사라졌고**, `GET /v1/ops` 응답의 **`imports` 키도 빠졌다**.
>
> ⚠ **BE 영향:** 알려진 소비처는 **0건**이다(계약·BE 명세 실측). `/v1/ops` 를 파싱하는 코드가 `imports` 키를 **필수로 읽으면** 고쳐야 한다.
>
> 🔴 **원장은 남는다** — `WorkerKind.MAPPING_PROBE`·`OperationKind.MAPPING_PROBE_RESOLVE`·`Capability.IMPORT_MAPPING`·CHECK 제약·**DB 테이블 5개**는 그대로다. `mapping_probe` 행은 **실행 기록**이고, 지우면 «그 실행이 없었다» 가 되어 **불변식 8(재현성)·원장 완전성**에 걸린다.
> ⚠ 종전 §3.8 의 흐름·스키마 문면은 **`docs/handoff/` 와 git 이력**에 남는다 — 되살릴 때 그것을 본다(99 #187).

---

### 3.9 `/v1/counsel/drafts` — 상담 초안 (202 · **요청 단위 = 문의 1건**)

> **개정 근거:** 인박스 데이터계약 v1 §4(2026-07-31 **A 확정 통보**) · 99 D ㉛.
> 종전 `/v1/agents/counsel-pack`(학생 묶음 pack)은 **폐기**한다 — 화면 확정으로 선제 상담 자료·반 배치 생성이 사라지고 **문의 도착 시 자동 1회 생성**이 됐다. 재생성 API는 없다(다듬기가 곧 수정).
> 종전 절의 resume·완료분 개별 조회 규약은 **99 D ㉖에 옮겨 적었다**(정보 증발 방지).

```json
// ① POST /v1/counsel/drafts — 기동 (헤더: X-Tenant-Id · X-Request-Id · Idempotency-Key)
{
  "inquiry": {
    "inquiry_ref": "iq_884",             // BE 원본 문의 논리 참조 — AI에겐 불투명 키
    "topic": "grade",                    // grade | schedule | counsel_request | etc — 🔴 8/5 complaint 제거(§3.5)
    "urgency": "immediate",              // immediate | normal — 🔴 **BE 소유 축(인박스 정렬·SLA).**
                                         //   **AI 톤에는 쓰지 않는다** — 받아서 쓰지 않는 값이다(8/6 판정).
                                         //   ⚠ `immediate`와 `normal`의 **산출은 바이트 동일**하다 —
                                         //     「완충이 강해진 초안」을 기대하지 마라.
                                         //   근거: sensitivity 기본값이 이미 최대 완충이라 올릴 여지가 없고,
                                         //   문의 하나가 강사의 학부모별 톤 설정을 뒤집으면 안 되며,
                                         //   톤 축이 되면 24조합이 48이 돼 tone_map 로더가 깨진다
                                         //   (`contracts/counsel.py` InquiryUrgency 근거 ⓐⓑⓒ · 99 ㊰)
    "received_at": "2026-07-31T14:20:00+09:00",
    "text_masked": "요즘 아이가 힘들어하는 것 같은데…"   // redaction 통과분 (불변식 3)
  },
  "student_ref": "st_8f2a", "parent_ref": "pa_9c1d", "class_ref": "cl_a1",  // 전부 가명
  "labels": ["narrative", "anxious"],                  // 🔴 **4축 enum 값만** — alias 없음. 그 밖은 400
                                                       //   comm: data|narrative · sensitivity: anxious|direct
                                                       //   interest: grade|attitude|admission · frequency: frequent|monthly
                                                       //   ⚠ **선택이다** — 누락 축은 기본값(narrative·anxious·grade·monthly)
  "dismissed_suggestions": [{ "axis": "frequency", "value": "monthly" }],   // 재제안 억제
  "context": {                                         // 인용 가능한 사실의 전체 우주
    "snapshot_hash": "sha256:…",                       // 재현성 축(불변식 8) — 🔴 **약속 vs 현재**(㊩)
                                                       //   🔴 **현재 소비되지 않는다.** `AI_RUN.input_snapshot_hash`에
                                                       //   들어가는 것은 **AI가 자기 `DraftContext`를 직렬화해 만든
                                                       //   해시**(`content_hash(contexts)` = `job.payload_hash`)다.
                                                       //   ⇒ **이 값으로 원장을 조회하면 0건이다**(src 소비처 0 · 99 ㊺)
                                                       //   ⚠ 필드는 필수(`NonEmptyStr`)로 남는다 — 빼면 파괴적이다
    "period_label": "2026년 7월",
    "facts": [{ "record_id": "le_2041", "summary": "6월 지문 42개·312문항" }]
  }
}
// → 202 { "job_id": "cj_1029", "status": "queued" }
//   같은 Idempotency-Key + 같은 바디 = 기존 결과 재반환 · 다른 바디 = 409 IDEMPOTENCY_CONFLICT (§2.3)
//   ⚠ 응답 전 타임아웃 시 재시도 규약 — §2 «응답이 오기 전에 타임아웃이 나면»(정본 · 복제 금지)

// ② 완료 통지 — Kafka (job_id, terminal phase) 멱등. 본문 없음 — 본문은 ③으로만 (08)
//   🔴 **안 오는 경우가 있다** — 잡이 없는 경로(위 ①②)와 `queued`로 나간 잡.
//   §2.2 「202를 받은 BE가 무엇을 하나」 참조 — 분기 규칙의 정본은 런북 §2 ⓕ·ⓖ다.

// ③ GET /v1/counsel/drafts/{job_id} — 결과 회수
{
  "data": {
    "job_id": "cj_1029",
    "status": "succeeded",                  // 공통 phase 7종(error_codes §2.5)
    "result": {
      "draft_status": "generated",          // generated | template_only | rejected_insufficient | llm_failed | gate_exhausted
      "text": "어머님, 먼저 세심하게…",       // 게이트 통과본만 — 미통과는 text 없음 + 사유
      "citations": [                        // **항상 1건 이상** — 근거 없는 초안은 존재 불가(불변식 2)
                                            // 🔴 **각주가 아니다** — 아래 규약 참조
        { "cite_id": "L1", "record_id": "le_2041", "summary": "6월 지문 42개·312문항" }
      ],
      "labels_applied": ["narrative", "anxious", "grade", "monthly"],   // 🔴 **항상 4값**(4축 전수)
                                                       //   요청이 2개만 보내도 기본값으로 채운 4축이 나온다 — 에코가 아니다
      "label_suggestions": [],              // ⚠ v1 상수 [] — 생성기 미구현(99 D ㊲)
      "status_reason": null,                // 거부·실패 사유 코드(error_codes §2.1)
      "generated_at": "2026-07-31T14:24:11+09:00"
    }
  },
  "error": null,
  "meta": { "execution_id": "…", "versions": { "…§2.2…": "…" } }
}

// ④ POST /v1/counsel/drafts/{job_id}/refine — 다듬기 (대상 키 = ①이 돌려준 job_id · 동기 · 매 턴 게이트 전체 재통과)
//    🔴 헤더: X-Tenant-Id · Idempotency-Key (둘 다 필수 — 누락은 400 INVALID_SCHEMA)
//    ⚠ 멱등 스코프는 **잡별**이다 — 같은 키를 다른 job_id 에 써도 섞이지 않는다.
//      같은 잡 + 같은 키 + 같은 바디 = 저장분 재반환(LLM 미호출 · 원장 미기록),
//      같은 잡 + 같은 키 + 다른 바디 = 409 IDEMPOTENCY_CONFLICT (99 #76)
// 요청  { "instruction": "정답률이 오르고 있다고 강조해서 써줘", "turn_no": 3 }
// 반영  { "applied": true,  "text": "…", "citations": [ … ] }
// 차단  { "applied": false, "blocked_reason": "comparison_exposure" }   // ⚠ `message` 없다(8/5 제거 · 아래 규약)
```

**규약**

- **잡 성공 ≠ 초안 존재.** `status="succeeded"` + `result.draft_status="rejected_insufficient"`는 **정상 조합**이다(데이터 부족은 에러가 아니다 — 불변식 4). 화면은 "아직 데이터를 모으는 중이에요"를 그린다.
- 🔴 **`citations[]`는 「이 초안이 참고한 근거 목록」이지 「본문 문장의 각주」가 아니다** (8/7 명시).
  - **본문 문장과의 대응이 없다.** 라우터가 요청의 `context.facts` **전수**를 그대로 싣는다(`_citations_of`) — **초안 본문을 한 글자도 안 본다.** ⇒ BE가 fact 5건을 보내고 LLM이 1건만 언급해도 **5건 전부** 실린다.
  - 🔴 **FE가 각주(「이 문장의 근거」)로 렌더하면 화면이 거짓이 된다.** `cite_id`는 **순서 키**일 뿐이다(7/31 handoff ① 통보분과 같은 내용이며, 그때 통보한 것은 *앵커 미지원*이고 여기서 명시하는 것은 *목록이 본문과 무관하다*는 것이다).
  - **왜 이 형태인가:** 본문 인라인 앵커(`#Ln`)는 `#`이 counsel 게이트의 금지 기호라 **v1.1**이다(99 ㊳). 실측(8/7 · 4차 원문): 실 LLM 초안 본문 **5건 중 0건**에 `record_id`·`le_` 흔적이 남았다 ⇒ **본문에서 뽑아 거르는 것이 불가능**하다.
  - ⚠ **refine 반영 턴도 같은 목록을 재사용한다.** 강사가 *"이 부분 빼줘"* 로 그 근거를 지워도 목록은 그대로다.
- 🔴 **`context.facts` 에 「기준선(baseline)」 행을 싣지 마세요** `[확정 · 8/19]`
  - **위험신호 evidence 를 상담 재료로 쓰는 것 자체는 막지 않습니다.** 다만 그 목록에서
    **`role=baseline` 인 행은 빼고** 보내세요 — 「이번 주 정답률 42%」는 보내고,
    「직전 8주 평균 71%」는 **빼는** 것입니다.
  - **왜:** counsel 게이트의 허용 숫자 집합(`allowed_numbers`)이 `context.facts` **전량**에서
    나옵니다. 기준선 숫자가 그 집합에 들어가면 **LLM 이 그 숫자를 아무 자리에나 써도 게이트를
    통과**합니다 — 방어가 그만큼 넓어집니다. 브리핑은 이미 `role=TRIGGER` 만 프롬프트에
    싣는 필터가 있는데 **counsel 에는 그 필터가 없습니다.**
  - ⚠ **AI 는 이 값을 받지 않습니다** — `ContextFact` 에 `role` 축을 열지 않았습니다
    (`extra="forbid"` 라 보내면 400 입니다). **거르는 책임은 BE 에 있습니다.**
  - 근거·규칙별 baseline 유무는 `part_a/14_evidence_fields.md` §3-3·§3-4′ 가 정본입니다
    — 여기 복제하지 않습니다.
- **`citations[]`는 ≥1이 타입 계약**이다. 인용 가능한 근거(`record_id`가 있는 fact)가 0건이면 **LLM 호출 전에** `rejected_insufficient`로 끊는다 — 게이트를 통과한 초안을 만들어 놓고 근거가 없어 버리는 낭비를 만들지 않는다.
- **`refine` 차단도 200**이다(`applied:false` + `blocked_reason`). `GateRejected`를 5xx로 올리면 리뷰 반려(불변식 4 · error_codes §4).
- 🔴 **차단 문구는 AI가 주지 않는다(8/5).** `blocked_reason` 8종에 대한 표시 문구는 `part_a/06_refine_policy.md` §4 표가 원본이며 **BE가 매핑**한다 — 초안 `draft_status`·classify 폴백과 같은 규약이다(`error_codes` §2.1 "백엔드 표시 문구" 열 · §2.7 규칙 3). ⚠ **종전 응답의 `message` 필드는 제거됐다.**
- 🔴 **강사가 화면에서 직접 편집한 내용은 AI 에 전달되지 않습니다** `[확정 · 8/19]`
  - `refine` 은 **AI 가 마지막으로 생성한 본문**을 기준으로 다듬습니다(실측: 라우터가
    `previous_text` 로 **AI 쪽 상태**를 넘기고, 요청 바디에서 읽는 것은 `instruction`·`turn_no`
    **둘뿐**입니다 — `RefineRequest` 에 본문 필드가 없습니다).
    ⇒ **직접 편집 후 다듬기를 부르면 편집분이 AI 의 마지막 버전으로 덮입니다.**
  - 🔴 **다듬기를 막지 마세요.** 강사가 「손으로 마무리」와 「AI 에 다시 맡기기」를 고를 수
    있어야 합니다. **대신 덮어쓴다는 것을 누르기 전에 보여 주세요.**
  - **왜 AI 가 본문을 안 받나:** 받으면 강사가 손으로 쓴 숫자가 게이트의 허용 숫자 집합에
    없어 `ungrounded_number` 로 막힙니다. 넣어 주면 게이트가 무의미해지고, 안 넣으면 강사
    문장이 막힙니다 — **불변식 2 의 경계를 건드리는 판정이라 v1 범위가 아닙니다**(99 #91).

- 🔴 **v1 은 문의 1건 = 초안 1개입니다 — 대화 스레드는 백엔드가 가집니다** `[확정 · 8/19]`
  - 한 문의의 대화(1차 질문·1차 답장·2차 질문·2차 답장…)는 **백엔드가 보관·표시**하고,
    **AI 는 각 답장 요청을 독립된 문의 1건으로** 받습니다. ⇒ AI 계약·코드 변경 **0**.
  - ⚠ **AI 는 같은 스레드의 앞 대화를 모릅니다.** 중복된 표현이 나오거나 매번 인사로 시작할
    수 있습니다 — **인사 블록은 `sensitivity` 축이 정하며**(`anxious` 는 인사로 시작, `direct` 는
    결론 선행) **「첫 답장인가」와 무관합니다.** 강사가 승인 화면에서 거릅니다.
  - 🔴 후속 답장임을 AI 가 알아야 할 만큼 어색하면 **`is_follow_up` 같은 불리언 한 비트**를
    추가합니다. **대화 이력 전체를 받는 것은 별개 설계입니다** — 근거(불변식 2)·마스킹 표면·
    재현성·토큰이 전부 달라지므로 그때 따로 정합니다(99 #92).

- ⚠ **발송본을 보관해 주세요** `[요청 · 8/19]`
  - AI 가 만든 **마지막 버전**과 **실제 발송된 본문**의 차이를 나중에 집계하고 싶습니다 —
    **강사가 어디를 자주 고치는지**가 프롬프트·톤·어휘를 개선하는 근거가 됩니다.
  - 🔴 **AI 가 그것을 학습하지 않습니다.** 집계해서 **사람이** 프롬프트 버전을 올리는 데 씁니다
    (학습하지 않는 이유는 99 #93).

- **refine 대상 키는 `job_id`다.** 문의 1건 = 잡 1개 = 초안 1개(pack N=1)라 별도 `draft_id`를 노출하지 않는다 — BE는 **Kafka 완료 통지가 싣는 `job_id`를 그대로** 쓰면 되고 별도 조회가 필요 없다. FE 계약 §3-③은 `inquiry_id` 기준이므로 **BE가 `inquiry_id → job_id` 매핑을 중계**한다(AI는 원본 문의에 접근하지 않는다).
  > 🔴 **정정(8/5).** 종전 표기는 `draft_id`였는데 그 값이 **어떤 응답에도 실리지 않아** BE가 refine을 호출할 계약 경로가 없었다(`CounselDraftJobView`는 `job_id`·`status`·`result` 3필드뿐 — 호출하면 404 확정). 스키마에 필드를 추가하는 대신 **키를 `job_id`로 통일**했다. 응답 스키마 무변경.
- **`template_only`는 정상이다.** `inquiry.topic=schedule`처럼 학습 데이터가 필요 없는 문의는 **근거 유무와 무관하게** 이 상태로 수렴한다(근거 선검사보다 **앞**에서 갈린다 — 시간표 문의 + 신규생이라고 "아직 데이터를 모으는 중이에요"가 나가면 안 된다). `text`는 **null**, `citations`는 **빈 배열**이다 — 안내 문구는 **BE 소유**다(`error_codes` §2.1 표시 문구 열 · §2.7 규칙 ③). **다듬기로 되돌릴 수 없고**(refine 대상 미등록 → 404), 오분류였다면 아래 정정 경로(confirmations + 새 키 재요청)를 탄다.
- **문의 유형(`topic`)을 정정하면 초안을 재요청한다 — 되돌리기가 계약 의무다.** `inquiry.topic`은 분류(ⓑ)의 판정값이고, 그 값이 `template_only`처럼 **초안 종류를 가른다**(§3.5). 분류가 초안을 가르는 것이 허용되는 전제가 **강사가 되돌릴 수 있다**는 것이므로(`part_a/01` §4-ⓑ), 오분류 시 경로를 계약으로 보장한다.
  - 강사가 인박스에서 문의 유형을 정정한다 → BE가 **새 `Idempotency-Key`**로 `POST /v1/counsel/drafts`를 정정된 `topic`으로 다시 호출한다 → 새 초안이 생성된다.
  - **재생성 전용 API는 없다**(이 절 서두). 같은 키 + 다른 바디는 `409 IDEMPOTENCY_CONFLICT`이므로 **반드시 새 키**여야 한다.
  - **다듬기(refine)로는 되돌릴 수 없다.** `template_only`는 `text`가 `null`이고 refine 대상으로 등록되지 않는다 — 다듬을 원본이 없다.
  - 정정 이력은 **`POST /v1/confirmations`**(§3.3 · `kind: "classification"`)로 받아 `INQUIRY_CLASS`에 축적한다(`part_a/03` §C7). 축별 정정은 `corrected_topic`·`corrected_sentiment`·`corrected_urgency`에 남고 `corrected_by_teacher`는 그 셋의 NULL 여부에서 나오는 **파생값**이다. ⚠ `llm_call_id`는 아직 NULL이다(99 ⓕ·㊻ 선행 의존).
  - 🔴 **BE는 정정 시 두 API를 모두 호출한다.** 하나는 기록, 하나는 생성이라 **서로를 대체하지 않는다.**

    | | 호출 | 무엇을 하는가 |
    | --- | --- | --- |
    | **ⓐ** | `POST /v1/confirmations` — `kind: "classification"` · `action: "corrected"` · `suggestion_id`는 **`inquiry_ref`** · `corrected_value`에 **바뀐 축만** | **평가셋 기록.** 99 ⓐ 임계값을 정할 **재분류율의 원천**이다 |
    | **ⓑ** | `POST /v1/counsel/drafts` — **새 `Idempotency-Key`** + 정정된 `topic` | **새 초안 생성.** 같은 키 + 다른 바디는 `409` |

    - **순서는 무관**하다 — ⓐ는 기록, ⓑ는 생성으로 서로 독립이다.
    - 🔴 **하나만 하면 안 된다** — **ⓐ만 하면 초안이 안 바뀌고**, **ⓑ만 하면 평가셋이 비어 99 ⓐ의 재분류율을 영원히 못 잰다**(임계값을 못 정한다).
    - ⓐ는 **멱등**이다(자연키 `(tenant_id, inquiry_ref)` upsert) — 재시도해도 안전하고 **LLM을 부르지 않는다**.
  - 🔴 **정정 후 유효한 `topic`의 소유는 BE다.** 정정은 BE 화면(인박스)에서 일어나므로 원본도 BE가 갖는다. AI가 보관하는 `corrected_*`는 **평가셋용 사본**이고 **조회 API가 없다**.
    - ⚠ **정정값을 얻으려고 `POST /v1/classify`를 다시 부르지 말 것.** classify는 `(tenant_id, inquiry_ref)` 캐시가 있어 **저장된 예측**을 그대로 돌려준다(§3.5) — 이는 **예측 고정 원칙**(예측이 소실되면 평가셋의 (입력·예측·정답) 3요소가 깨진다)의 결과이지 버그가 아니다.
    - ⇒ 이후 `POST /v1/counsel/drafts`에 싣는 `inquiry.topic`은 **BE가 보관한 정정값**이다.
- **턴 상한은 AI가 판정하지 않는다.** `turn_no`는 로그·이력용으로 받기만 한다 — "세션 턴 상한 없음, 월 할당이 자연 상한"(`part_a/06` §1)이고 할당 집행은 전부 백엔드 Billing이다(7/15 BE-4).
- **v1 구현 범위 정정 5건**(계약보다 낮게 구현되는 부분)은 `docs/handoff/2026-07-31_counsel_router_v1_scope_to_BE.md`가 정본이다 — ① `citations` 각주형(앵커는 v1.1) · ② `label_suggestions[]` 항상 빈 배열 · ③ `draft_status` 4종 유지 · 🔴 **④ 4축 라벨 값 표기 정정** · ⑤ `labels[]`는 선택(누락 축은 기본값).
  > 🔴 **(8/7 정정) 종전 표기는 「3건」이었다** — handoff는 **5건**이고, 빠진 ④가 정확히 이 절의 예시를 틀리게 만든 항목이다. **BE에는 7/31에 통보했는데 04를 안 고쳤다** — `anxiety_sensitive`(존재하지 않는 값)가 예시에 남아 그대로 호출하면 **400**이었다. *통보와 계약 반영은 다른 사건이다.*

#### 🔴 v1 지원 한계 — BE가 알아야 하는 것 (약속 vs 현재)

⚠ **§3.11과 같은 형식이다** — 계약이 약속한 것과 **구현 전인 지금의 동작**을 함께 적는다(㊩). 아래는 **8/7 종단 실측**이다.

| 한계 | 🔴 **현재 동작** |
| --- | --- |
| **읽기 모델 축출** | `GET`·`refine`이 보는 것은 **인메모리 LRU 캐시**이고 상한은 **256건**이다(`_MAX_CACHED_JOBS`). 넘으면 가장 오래된 항목이 밀려 **404 `NOT_FOUND`** 가 난다. 🔴 **「없어졌다」가 아니라 「캐시에서 밀렸다」다** — 실측: 축출 후 `GET` 404인데 **잡 원장 1건 · 초안 본문 1건이 그대로 살아 있다** |
| **`GET`/`refine` 비대칭** | 두 캐시(`_view_cache`·`_drafts`)가 **독립으로 축출**된다 ⇒ 실측: 같은 `job_id`에 **`GET`은 404인데 `refine`은 200**이다. 한쪽이 되면 다른 쪽도 된다고 가정하지 마라 |
| **테넌트 격리 없음(캐시 한정)** | 상한이 **전역**이라 **다른 테넌트의 트래픽이 내 항목을 밀어낼 수 있다.** ⚠ 데이터 격리는 지켜진다(키에 `tenant_id`가 있다) — 밀려나는 것이 격리와 무관하게 일어난다 |
| **재시작** | 초안 본문·컨텍스트·팩 결과 저장소가 **인메모리 고정**이다(`store_backend=pg`여도). 재시작하면 **사라진다.** ⚠ 잡 원장·멱등·실행 원장은 PG로 **살아남는다** — **비대칭이다** |
| **멱등 재전송** | 멱등 저장소가 PG면 재시작을 견뎌 같은 키에 **202 + 같은 `job_id`** 가 돌아온다. 🔴 그런데 그 `job_id`의 **캐시는 사라져 `GET`이 404**다 — *"만들어졌다는데 조회가 안 된다"* 가 이 조합이다 |

🔴 **재시작 후 복구가 보장되는 것으로 해석하지 마라.** 영속(`DRAFT_REVISION` 이관)은 06 §7 후속이고 **이 문서는 현재 동작을 적는다.**

### 3.11 `/v1/problems` — 문제 생성 (202 · **요청 단위 = 세트 1개**)

> **이관 근거:** `docs/part_b/09_integration_proposals.md` §2-19(B 초안 `[제안 · 2026-08-07 · A 반영 대기]`)에서 **구현된 절만** 옮겼다 — §2-19.0~.4. 🔴 **`[v1 스펙 확정 · 구현 후속]` 7절(§2-19.5~.8·.9~.11)은 09에 남긴다.** 04는 「구현된 계약의 정본」이고 09는 「스펙 초안」이라, 미구현 스펙을 04에 넣으면 **BE가 구현된 것으로 읽는다** — 8/7에 `apply`에서 정확히 그렇게 됐다(04가 "400"이라 적었고 실제로는 500이었다 · 99 ㊩·㊨).
> ⚠ **B 확인 필요** — B는 *"04에 옮길 초안"* 이라고만 적었지 전부인지 구현분만인지는 안 적었다. 이 범위는 A 판정이고 반대 의견이 설 자리가 있다.
> **v1 operation은 `problem_set.generate` 하나**다 — `problem_item.refine`·`reverify`는 공용 enum의 예약값이고 이 API에 엔드포인트가 없다.

```json
// ① POST /v1/problems — 세트 생성 기동 (헤더: X-Tenant-Id · X-Request-Id · Idempotency-Key)
{
  "target_kind": "student",              // student | class
  "target_ref": "st_8f2a",               // 가명 참조 — 비어 있을 수 없다
  "target_source": "teacher_manual",     // weakness_auto | teacher_manual
  "weakness_map_id": null,               // UUID | null — teacher_manual이면 금지
  "manual_targets": ["grammar:sentence-structure"],  // teacher_manual이면 ≥1 필수 · weakness_auto면 금지
  "snapshot_hash": "sha256:…",           // 재현성 축(AI_RUN · 불변식 8)
  "taxonomy_version": "2026.08",
  "area_tag": "language",                // 🔴 v1은 language만 — 아래 「v1 지원 한계」
  "type_tags": ["concept"],              // fact | infer | critic | concept 중 중복 없이 ≥1 · 🔴 apply 금지(아래)
  "item_format": "mcq",                  // v1은 mcq만(short·essay는 예약값)
  "count": 1,                            // 1..20
  "requested_difficulty": "medium",      // low | medium | high | null
  "target": "auto",                      // cell | node | auto
  "passage": null,                       // 🔴 v1은 생략 또는 null만
  "topic_hint": null
}
// → 202 { "data": { "job_id": "8e94ceac-…", "status": "succeeded" }, "error": null, "meta": { … } }
//   🔴 `status`가 실린다(#160) — counsel 202와 대칭이고 **BE는 이 값 하나로 분기한다**(아래 ⚠).
//   ⚠ `queued`로 나가는 경로가 실재한다 — `run_next()`가 자기 잡을 처리한다는 보장이 없다.
//   `queued`여도 다음 POST를 기다리지 않는다. 앱 startup에서 뜬 유한 배경 드레인이 같은
//   tenant의 기존 `run_next()` 경로를 주기적으로 실행한다. 이 status는 응답 시점 값이므로 GET으로 갱신한다.
//   헤더 파생 3필드(tenant_id·request_id·idempotency_key)를 바디에 중복하면 400
//   같은 Idempotency-Key + 같은 바디 = 최초 202 재반환 · 다른 바디 = 409 IDEMPOTENCY_CONFLICT (§2.3)

// ② GET /v1/problems/{job_id} — 상태·결과 회수 (헤더: X-Tenant-Id 필수)
{
  "data": {
    "job_id": "8e94ceac-…",
    "status": "succeeded",                  // 공통 JobPhase 7종(error_codes §2.5)
    "result": {
      "outcome": "problem_set",             // problem_set | rejected_insufficient
      "set_id": "…", "status": "generated", // generated | partial_success | failed
      "stop_reason": null,
      "target_source": "teacher_manual", "personalized": false,
      "requested_count": 1, "processed_count": 1, "unstarted_count": 0,
      "items": [                            // ⚠ 요약만 — 문항 본문·지문·evidence는 없다(아래)
        { "item_id": "…", "status": "needs_review", "attempt_no": 1,
          "failure_reason": null, "failure_detail": null,
          "difficulty_est": 1.5, "difficulty_band": "low", "difficulty_fit": null,
          "review_reason": "manual_target_first" }
      ],
      "summary": null, "dropped_reasons": []
    }
  },
  "error": null,
  "meta": { "execution_id": "…", "versions": { "…§2.2…": "…" } }
}
```

**규약**

- **다른 테넌트의 `job_id`는 존재를 숨겨 404**로 수렴한다.
- **실행 phase와 도메인 결과를 섞지 않는다**(99 ㉥). `queued|leased|running|paused` → `result`는 **반드시 null** · `succeeded` → **반드시** `ProblemGenerationOutcome` · `failed|cancelled` → `null`. 🔴 `status="succeeded" + result=null`, `status="running" + result.status="rejected_insufficient"` 같은 조합은 **금지**다.
- **잡 성공 ≠ 문항 존재.** `rejected_insufficient`와 세트의 `generated|partial_success|failed`는 **정상 도메인 결과**이지 에러가 아니다(불변식 4). `result.status` 값의 정본은 `policies/error_codes.md` §2.6.
- **오류 판별은 §2.4 주체 3분할 그대로**다 — 라우터가 다시 판단하지 않는다. 강사가 바꿀 수 있다 → 200 + 도메인 결과 / BE가 고쳐야 한다 → 400 `INVALID_SCHEMA`(409·404 포함) / 아무도 지금 못 바꾼다 → **재시도 예산 소진 후에만** 503·504.
- **LLM 예외 매핑**은 `runtime/errors.py`의 `domain_error_for()`가 정본이다: `LlmTimeout`→504 `TIMEOUT` · `LlmUnavailable`→503 `LLM_UPSTREAM_DOWN` · `ParseFailed`·`FieldMissing`·plain `LlmError`→500 `INTERNAL`. ⚠ **파싱·필드 오류를 503으로 뭉개지 않는다** — 벤더는 살아 있고 우리 요청이 틀린 경우라 "잠시 후 다시"가 거짓이 된다.
- **워크플로 설정 예외:** `ProblemWorkflowConfigurationError`→400 · `ProblemTenantMismatch`→403 `TENANT_MISMATCH` · `ProblemSourceUnsupported`→400 + `detail.reason=source_procurement_not_implemented` · `ProblemExecutionContextMismatch`→500(내부 조립 버그). ⚠ 통째로 400으로 바꾸면 403과 조달 미구현 사유가 뭉개진다.
- **Step3 `job_id` 보존:** BE는 POST 및 polling에서 받은 `job_id`를 보존한다. 상세 응답은 캐시가 살아 있으면 `job_id`를 반복 제공하지만, PG 복원 경로에서는 없는 값을 만들지 않고 해당 키를 생략한다(`job_id: null`도 금지). 문항 수정 API는 `job_id` 없이 `problem_set.request`의 원 요청 정본으로 동작한다. 이는 §2.2의 “없는 실행을 가리키는 값을 지어내지 않는다” 규약을 따른다.

#### 🔴 v1 지원 한계 — BE가 **선검사**해야 하는 것 (약속 vs 현재)

⚠ **아래 표는 「약속한 동작」과 「구현 전인 지금의 동작」을 함께 적는다**(99 ㊩ 규칙). 약속만 적으면 BE가 그 동작을 기대하고 호출한다.

| 요청 | 약속한 동작 | 🔴 **현재 동작** |
| --- | --- | --- |
| 영역별 필수 자료 요청이 없거나 지원하지 않는 자료 조달 조합이다 | 400 `INVALID_SCHEMA` + `detail.reason=source_procurement_not_implemented` | **같다 — 문 앞 검사로 구현됨** ✅ `ProblemGenerationEnqueuer.enqueue()`가 요청 레코드와 `WorkerJob`을 만들기 전에 조합을 검사한다. 따라서 HTTP **400** · `WorkerJob` **0건** · `AI_RUN` **0건**이며 고아 잡이 남지 않는다. 독서는 `PassageRequest`, 문학은 `WorkSelection`, 화법과 작문·매체는 각 영역의 생성 자료 요청이 필요하다. 근거: `problem_generation/enqueue.py::reject_unsupported_source_procurement()` 및 `test_problem_router.py`의 문 앞 검사 테스트. |
| `passage`·`work_selection`과 `area_tag`의 조합이 지원 범위와 다르다 | 위와 같다 | **같다 — 문 앞 검사로 구현됨** ✅ `detail`에는 `reason=source_procurement_not_implemented`와 실제 `area_tag`·자료 요청 존재 여부가 실린다. 요청 저장·잡 생성·원장 기록보다 먼저 끝나므로 `WorkerJob` **0건** · `AI_RUN` **0건**이고, 응답 없는 실패 잡도 만들지 않는다. 지원 조합의 자료 생성·선택이 성공한 경우에는 이 400에 해당하지 않는다. 근거: `problem_generation/domain/policy.py::supports_source_procurement()`·`problem_generation/enqueue.py`. |
| `type_tags`에 **`apply`** | 400 `type_tag_not_supported` | **같다 — 구현됨** ✅ (8/9 · B 구현). 🔴 **이 경로도 잡을 만들지 않는다** — 거절이 `enqueue.py::reject_unsupported_type_tags()`, 즉 요청 레코드·`WorkerJob` 생성보다 **앞**이다. ⇒ **고아 잡이 남지 않는다**(실측 8/9: HTTP **400** · `잡 0건` · `AI_RUN 0건`). 자료 조달 조합까지 함께 위반하면 관측 순서를 보존해 이 사유가 먼저 난다. `detail` = `{reason: "type_tag_not_supported", type_tags: [...], supported: ["concept","critic","fact","infer"]}` |
| `target_source=weakness_auto` | 400 `INVALID_SCHEMA` + `detail.reason=weakness_auto_not_wired` | **같다 — 문 앞 거절로 구현됨.** 생산 WeaknessMap 저장·조회와 `DiagnosisCallable` 배선 전에는 요청 레코드·`WorkerJob`·`AI_RUN`을 만들지 않는다. 영상 MVP는 Step1 응답에서 선택한 노드를 `teacher_manual.manual_targets`로 전달한다. 근거: `enqueue.py::reject_unwired_weakness_auto()` 및 `test_problem_router.py`. |
| 프로세스 재시작 후 이전 `job_id`·`set_id` 조회 | — | **`STORE_BACKEND=memory`: 404 `NOT_FOUND`.** 잡 원장·요청·결과·문항·라우터 조회 캐시가 프로세스 메모리라 모두 사라진다. **`STORE_BACKEND=pg`: 종단에 도달한 세트는 200으로 복구한다.** `AI_RUN → problem_set → problem_item` 순서로 부모와 슬롯 스냅숏을 저장하고, `_views` 캐시가 없으면 `problem_set_store.py`가 원 요청 정본·결과·버전 세트를 재조립한다. 따라서 이전 `job_id` GET과 `set_id`의 items 목록·상세 GET 및 `language` 문항 수정은 프로세스 캐시에 기대지 않는다. dropped 슬롯도 본문 없는 `problem_item.snapshot`으로 남아 `dropped_reasons`가 복구된다. **단, 재시작 시점에 queued·running이던 잡의 실행 재개는 v1 범위 밖이다.** 실행 전에는 아직 `problem_set` 부모가 없으므로 종단 세트의 원 요청 복구와 미완료 잡 재개는 다른 계약이다. “종단 결과 조회·수정 가능”을 “미완료 실행 재개 가능”으로 해석하면 안 된다. 근거: `problem_set_store.py`·`problem_store.py`·`problem.py`·`workflow.py` 및 `test_problem_pg_persistence.py`. `store_backend` 기본값은 이 변경에서 바꾸지 않는다. |

> **배경 드레인 근거(99 #21 해소):** `api/routers/problem.py`가 router startup/shutdown에
> 드레인을 붙이고, `problem_generation/application/drain.py`가 사이클별 잡 수 상한·유휴 대기·
> 연속 실패 백오프·종료 정리를 맡는다. 실행은 새 워커 경로가 아니라 기존
> `ProblemGenerationRunner.run_next(tenant_id=…)`를 그대로 사용한다. 따라서 lease·fencing·
> 테넌트 범위는 기존 규약과 같다. Kafka 배선은 포함하지 않는다.

🔴 **`language` 제한은 트랙 제한이 아니라 「자료 조달 방식」 제한이다.** 현행 그래프에 지문·담화·매체를 만드는 *생성* 노드와 승인 저작물 풀에서 고르는 *저작물* 노드가 **없다**(05 §1.2). 게이트를 완화하거나 빈 자료로 실행하지 않는다.

> **BE 연동 지뢰:** 와이어프레임의 **독서·문학 영역×유형 칸을 그대로 생성 요청으로 보내면 전량 400**이다. 미지원 칸은 **생성 동작을 비활성화하거나 「지원 대기」로 표시**해야 한다.

> 🔴 **`apply` 선검사가 왜 필요한가 — 실수가 아니라 정상 동작이 그리로 간다.** BE가 약점 지도를 보고 **자동 출제 요청**을 만들면 `type_tags=["apply"]`가 자연스럽게 나온다: 학습 이벤트(`LearningEvent.type_tag`)로 `apply`를 **받으므로**(99 ㊣ — 강사가 매긴 사실이라 막지 않는다) diagnoser가 `"문학×apply"` 셀을 만들고, 그 셀이 약한 것으로 나오면 출제 대상이 된다. **정상 동작의 결과로 나오는 것이지 실수가 아니다.** 그래서 요청을 만드는 쪽에서 걸러야 한다.

#### 09에 남아 있는 것 (04로 옮기지 않았다)

⚠ **다른 내용을 다른 문서가 갖는 것은 중복이 아니다** — §7이 지적한 중복은 *"같은 목록이 두 곳에 산다"* 였다. 아래는 **04에 없는 내용**이고 04는 **가리키기만** 한다.

| 09 절 | 무엇 | 왜 04에 없나 |
| --- | --- | --- |
| §2-19.5 | `GET /v1/problems/{set_id}/items` — Step3 검토 목록 | **구현됨** — 상태 카운터·슬롯·현재 리비전 번호 반환 (`problem.py`, 2026-08-12) |
| §2-19.6 | `GET …/items/{slot_index}` — 문항 상세 | **구현됨** — 현재 검증본·evidence·검증 상태·리비전 이력 반환 (`problem.py`, 2026-08-12) |
| §2-19.7 | `POST …/revisions` — 수정·롤백 | **부분 구현** — `language`의 `ai_refine`만 200 동기 처리. `teacher_direct`·`rollback`·다른 4영역 수정은 미구현 (`problem.py`·`refiner.py`, 2026-08-12) |
| §2-19.8 | 교체·삭제 | 같음 + `[경로 제안 · BE 합의 대기]` |
| §2-19.9 | evidence `quote=null`과 "출처 확인됨" 배지 | 🔴 **구현된 표면에 evidence가 없다** — `ItemResult`는 `item_id`·`status`·난이도 등 **요약 9필드뿐**이고 `evidence`를 싣지 않는다(실측 8/7). evidence는 `GeneratedItem`에 있고 §2-19.6으로만 나간다 ⇒ 배지 규약은 **지금 도달 불가**다 |
| §2-19.10 | 완료 알림 최소 payload | `[구현 후속]` + Kafka 토픽 미확정(§8) |
| §2-19.11 | 약점 진단(Step1) 응답 요구 | `[구현 후속]` |

🔴 **BE는 위 표의 구현 상태를 종류별로 따라야 한다.** Step3 목록·상세는 호출 가능하고,
리비전은 `language`의 `ai_refine`만 가능하다. 교체·삭제·직접 수정·롤백과 다른 4영역 수정은
아직 화면 계약일 뿐 호출 가능하다고 해석하면 안 된다.

#### 🔴 `type_tag` 화면 라벨 — AI 근거와 화면 표시는 **소유가 다르다**

> **근거:** `part_b/09` §2-21.2 — B가 `_TYPE_KO` **교체 요청을 철회하고 분리로 갔다.** 정본 표는 09에 있고 여기·`policies/taxonomy.md` §3에 반영한다.

| `TypeTag` | **AI 근거** (`_TYPE_KO`) — 🔴 **AI 소유**(`composition/briefing_context.py`) | **화면 표시** — 🔴 **클라이언트 소유** |
| --- | --- | --- |
| `fact` | 사실 | 사실적 이해 |
| `infer` | 추론 | 추론적 이해 |
| `critic` | 비판 | 비판적 이해 |
| `concept` | 개념 | 어휘·개념 |
| `apply` | 적용 | 적용·창의 |

🔴 **어휘가 두 곳에 사는 게 아니라 소비 목적이 둘이다.** 왼쪽은 **R6 근거 팩트**로 LLM 프롬프트에 실려 **학부모 문장**이 되고(`f"{area}·{type_}"` → `"문학·적용"`), 오른쪽은 **Step 1 그리드**에 뜬다. 같은 값의 두 표기가 아니라 **다른 자리의 두 어휘**다.

🔴 **`display_label`과 방향이 반대인 이유** — `display_label`은 **강사가 못 고치는 값**이라 AI가 실어 보낸다. `type_tag`는 **강사가 화면에서 고치는 값**이라 클라이언트가 **전 값의 라벨을 갖고 있어야** 한다 — **피커가 없으면 못 고친다.** 다섯 값 중 넷만 라벨이 있으면 `apply`로 바꿀 수가 없다.

⚠ **`_TYPE_KO`의 짧은 형은 유지한다** — `f"{area}·{type_}"` 조립에서 `"적용·창의"` 를 그대로 쓰면 `"문학·적용·창의"` 가 되어 **구분자가 모호**해진다(기존 넷이 전부 2글자인 것도 같은 이유로 보인다).
⚠ **짧은 형이 프롬프트에 실제로 더 나은지는 미실측**이다 — 골든셋(`part_a/08`) 축으로 등재만 해 둔다.

### 3.10 운영 `[확정 · 구현 대기]`

> **현재 구현 상태(2026-08-12):** 아래 세 라우트는 아직 등록되지 않아 Starlette 기본
> `404 {"detail":"Not Found"}`를 반환한다. `/openapi.json`은 네트워크 접근 확인에는 쓸 수
> 있지만 liveness·readiness 판정이 아니다. 구현 전까지 이 절의 응답을 실제 동작으로 보고
> 연동하면 안 된다.

운영 API는 도메인 실행이 아니므로 `X-Tenant-Id`와 `Idempotency-Key`를 요구하지 않는다.
`X-Request-Id`가 있으면 응답에 돌려주되, 없다는 이유로 운영 프로브를 거부하지 않는다.
세 경로 모두 공통 envelope를 사용하고 `meta.execution_id=null`이다.

#### 3.10.1 `GET /v1/health` — liveness

프로세스와 ASGI 라우터가 응답 가능한지만 확인한다. DB·체크포인터·LLM·외부 API를 호출하지
않으며, 핸들러에 도달하면 `200`이다. DB 장애를 liveness 실패로 올려 살아 있는 프로세스를
반복 재시작하게 만들지 않는다.

```json
{
  "data": { "status": "alive" },
  "error": null,
  "meta": { "execution_id": null, "versions": { "...": "ops_versions" } }
}
```

#### 3.10.2 `GET /v1/ready` — DB readiness

주 DB에 제한시간이 있는 경량 질의를 수행한다. 체크포인터가 별도 DB URL을 쓰면 그 연결도
확인한다. migration·테이블 생성·실 LLM·외부 API 호출은 하지 않는다.

🔴 **`ready`는 「DB에 접속된다」만 뜻한다 — 스키마 준비도, 잡 처리 가능성도 보지 않는다.**
2026-08-12~08-20 배포에서 LangGraph 체크포인트 테이블이 0/4인 채로 **8일간 `200 ready`**
였고, 그동안 counsel·problem_generation·import 프로브 잡은 전량
`worker_internal_error`로 죽었다. ⚠ **이 엔드포인트로 배포 성공을 판정하지 마라** —
BE·모니터링 모두 마찬가지다.

⚠ 그렇다고 이 엔드포인트에 스키마 점검을 넣지 않는다. 배포 순서로 막는 것이 더 강하다 —
`docker-compose.deploy.yml`의 `migrate`가 두 DDL(`alembic upgrade head` +
`python -m ai.agents.checkpointer`)을 돌고, `app`이 그 **성공 종료**를 기다리므로
테이블 없이 앱이 뜨는 창 자체가 없다. 그 배선은 `tests/ai/contract/test_deploy_compose_contract.py`가 고정한다.

- 전부 준비됨: `200` + `data.status="ready"`.
- 하나라도 미준비·제한시간 초과: `503 SERVICE_NOT_READY`.
- 실패 `detail`에는 `unavailable_components`의 논리 이름만 싣는다. DB URL·계정·SQL·드라이버
  예외 원문은 응답하지 않는다.

```json
{
  "data": null,
  "error": {
    "code": "SERVICE_NOT_READY",
    "message": "서비스 준비가 완료되지 않았습니다",
    "detail": { "unavailable_components": ["database"] }
  },
  "meta": { "execution_id": null, "versions": { "...": "ops_versions" } }
}
```

`503 LLM_UPSTREAM_DOWN`은 LLM 벤더 장애 전용이므로 readiness에 재사용하지 않는다.
🔴 503은 status가 아니라 code로 분기한다 — SERVICE_NOT_READY(준비 미완)와
LLM_UPSTREAM_DOWN(벤더 장애)이 같은 status를 쓴다.
`SERVICE_NOT_READY`의 공용 오류 사전 편입과 표시 문구는 운영 라우터 구현 PR에서 함께
동기화한다.

#### 3.10.3 `GET /v1/meta/versions` — capability별 선언 버전

버전은 단일 전역값으로 합치지 않는다. 각 capability의 기존 버전 팩토리가 내는
`VersionSet`을 `data.capabilities` 아래에 따로 싣는다. 서로 다른 engine·prompt·threshold를
한 객체로 합치면 실제로 존재하지 않는 실행 조합이 되기 때문이다.

```json
{
  "data": {
    "app_version": "0.1.0",
    "capabilities": {
      "detection": { "...": "detection_versions()" },
      "classification": { "...": "classify_versions()" },
      "counsel": { "...": "counsel_versions()" },
      "problem_generation": { "...": "problem_failure_versions()" }
    }
  },
  "error": null,
  "meta": { "execution_id": null, "versions": { "...": "ops_versions" } }
}
```

- ops 라우터에 기존 버전 문자열을 복제하지 않고 각 팩토리 결과를 사용한다.
- 구현돼 버전 팩토리가 등록된 capability만 싣는다. 없는 축을 임의 값으로 채우지 않는다.
- `confirmations`는 `classify_versions()`를 공유하므로 별도 capability 키를 두지 않는다.
- `data.capabilities.*`는 조회 대상의 버전이고, 최상위 `meta.versions`는 이 운영 API 자체의
  버전이다.
- DB·LLM·외부 API를 호출하지 않는 정적 선언 조회다.

운영 라우터는 `/v1/health`·`/v1/ready`·`/v1/meta`의 좁은 version scope를 각각 등록한다.
`/v1` 전체를 운영 scope로 잡아 다른 capability의 실패 응답을 가로채면 안 된다.

---

## §4. 스냅숏 데이터 계약 (백엔드 → AI)

> **3대 원칙 `[제안]`** ① 실명·연락처·주소는 **필드가 스키마에 없다**(백엔드 직렬화 DTO에서 보장) ② 모든 식별자는 alias/불투명 ID ③ `snapshot_hash`로 재현성 보장(부록 A).

### 4.1 감지 스냅숏 (`/detect`)

**snapshot_meta**

| 필드 | 타입 | 필수 | 쓰는 곳 | 비고 |
| --- | --- | --- | --- | --- |
| `week_start` | date | ✅ | 피처 주차 키 | 월요일 기준 `[제안]` |
| `snapshot_hash` | string | ✅ | 재현성 | 부록 A |
| `term_context` | enum `normal·new_term·vacation` | ✅ | 세그먼트(재적응·방학 오경보 방지) | 원천은 백엔드 term_config |
| `classes[]` | array | ✅ | 랭킹 상한(per_class) | `{class_ref}` |

**students[]**

| 필드 | 타입 | 필수 | 쓰는 곳 | 비고 |
| --- | --- | --- | --- | --- |
| `student_ref` | string | ✅ | 전체 | alias |
| `class_ref` | string | ✅ | 랭킹 상한 | |
| `enrolled_weeks` | int | ✅ | 관찰 중 판정(2주 미만) | |
| `status` | enum `enrolled·paused·returned` | ✅ | R5(복귀 케어)·재적응 | returned = 복귀 첫 주 |
| `consent` | enum `granted·pending·revoked` | ✅ | 동의 게이트 | granted 외 이벤트는 폐기 |

**learning_events[]** — 지난 주차 증분 `[Open-4a: 백필]`

| 필드 | 타입 | 필수 | 쓰는 곳 | 비고 |
| --- | --- | --- | --- | --- |
| `record_id` | string | ✅ | **evidence 역추적 키** | 백엔드 DB PK — **불변 필수** |
| `student_ref` | string | ✅ | | |
| `type` | enum `solve·submit·attend·consult` | ✅ | 규칙별 | |
| `occurred_at` | datetime | ✅ | 시계열 | |
| `correct` | bool | solve만 | 정답률(R1·R6) | |
| `duration_sec` | int | solve만 | 풀이시간(R4) | 없으면 R4 미적용(대체 신호) |
| `passage_word_count` | int | 지문형만 | **어절 정규화** | 국어 특화의 핵심 필드 |
| `area_tag` / `type_tag` | enum | 있으면 | 유형별 정답률(R6)·약점 지도 | 미태깅 허용 — 태깅 제안이 채움. **area 값(수능 6영역): `reading(독서)·literature(문학)·speech(화법)·writing(작문)·language(언어/문법)·media(매체)`** · **type 값(평가원 5축): `fact·infer·critic·concept·apply`** — 🔴 **`apply`는 v1 미산출·예약**이다(99 ㊣). 학습 이벤트에는 **보내도 된다**(받아서 R6 집계·표시까지 한다). **출제 요청(`POST /v1/problems`의 `type_tags`)에 보내면 400 `type_tag_not_supported`** — ✅ **구현됨**(8/9 · `enqueue.py::reject_unsupported_type_tags()`). 🔴 **잡을 만들지 않는다** — 거절이 요청 레코드·`WorkerJob` 생성보다 앞이라 **고아 잡이 없다**(실측: 400 · 잡 0건 · AI_RUN 0건 · §3.11) + `subject_track: common·elective` 메타 · `item_format: v1은 mcq만`(short·essay 예약) — Open-11 확정(7/15) |
| `assignment_title_text` | string | 있으면 | **태깅 제안(ⓒ) 입력** | ⚠ `[Open-4b]` 제공 불가 시 기능 자체 불가 |
| `source` | enum `trackA·trackB·studentHome` | ✅ | 품질 가중 | |

### 4.2 초안 컨텍스트 (`/drafts` · `/counsel/drafts`)

| 필드 | 타입 | 필수 | 쓰는 곳 | 비고 |
| --- | --- | --- | --- | --- |
| `interventions[]` | array | ✅ | 초안 맥락 | `{record_id, type, memo_text, at}` — 강사 본인의 기록 |
| `comm_history[]` | array | ✅ | 톤·이력·라벨 제안 | `{record_id, direction, text, at}` — `[Open-4d]` 최근 10건·90일 · 백엔드 1차 마스킹 제안 |
| `guardian.label_snapshot` | object | ❌ **(7/31 정정)** | 톤 매핑 | 4축 enum — **확정본만**. **선택이다** — 라벨 검토함 계약 §6이 "라벨 0개 = 기본 톤으로 생성"을 확정했고 개통 첫날은 전원이 0개다. **누락 축은 기본값**(`part_a/05` §7-2), 응답 `labels_applied`에 실제 적용된 4축을 싣는다. 4축 enum 밖의 문자열은 400(§7-4 — alias 금지) |
| `inquiry` | object | reply만 | 분류·초안 | `{inquiry_ref, body_text, received_at}` |
| `student_summary` | object | ❌ | — | `[Open-4c]` A 제안: 불필요(AI가 자기 피처 사용) |
| `benchmarks` | object | report만 | 리포트 차트·해설 | 백엔드 집계 API 산출. **공개 등급 분리**: `{ "national_percentile": { "value": 68, "as_of": "...", "source": "...", "audience": "guardian" }, "class_avg": { "value": 74, "audience": "teacher_only" } }` — **teacher_only 항목은 학부모向 draft의 LLM 컨텍스트 조립에서 구조적으로 제외**(프롬프트에 미포함 = 유출 불가). 전국 백분위 출처·모수는 `[Open-9]` 백엔드 확인 필요(콜드스타트) |

### 4.3 크기·빈도 `[제안]`

| 페이로드 | 크기 상한 | 빈도 |
| --- | --- | --- |
| 감지 스냅숏 | 5MB/요청 (초과 시 분할) | 일 1회 (야간) |
| 초안 컨텍스트 | 512KB | 문의당 · 리포트 주기 |
| Import 파일 | 20MB | 비정기 `[Open-3]` |

---

## §5. 비기능 규약 `[제안]`

- **타임아웃:** 동기 10s · 비동기 `/drafts` **응답 예산 480초**(§2.4 정본 · 종전 「총 5분」). ⚠ **초과 시 `failed`** 는 계약 문면이며 **현재 강제하지 않는다**(강제 코드 0건 · 실측 8/20). 백엔드 서킷브레이커 임계는 v1.0에서 함께 확정.
- **AI 장애 시:** 백엔드는 전일 브리핑 유지 + 배지(기존 NFR). `/detect` 실패 시 다음 배치까지 대기 — 멱등키(tenant+analysis_date)로 중복 방지.
- **감사:** 모든 요청·응답은 `X-Request-Id`로 양쪽에서 상호 추적 가능.
- **하위호환:** 필드 **추가** = 마이너(무통보 가능) · 필드 **삭제·의미 변경** = 메이저(협의 필수). `meta.versions.contract`로 상호 확인.

## §6. 확정 절차

1. 리뷰 미팅(30분) — §1의 Open-1~12만 논의 → 체크박스 채움 (Open-11은 B 동석 필요, Open-12는 P2라 소유 확인만)
2. 반영 후 **v1.0 승격** → 저장소 커밋(이후 변경은 PR)
3. 양측 병행 개발 시작: A = FakeSnapshot · 백엔드 = AI 스텁
4. 통합 시점: 부록 A 해시 테스트 벡터 3건으로 상호 검증

---

## 부록 A. snapshot_hash 산정 `[제안]`

```
sha256( canonical_json({
  snapshot_meta: { week_start, term_context },
  students:        sorted by student_ref,
  learning_events: sorted by record_id,
  alert_context:   sorted by (student_ref, signal_type),  // (7/16) lifecycle 입력이라 해시 대상에 포함
  detection_evidence: sorted by (kind, student_ref, at, source_table, record_id)
                                                          // ★(8/12) R2·R3·R5 정본 근거 — 판정 입력이라 포함
}) )
```

canonical_json = 키 정렬 · 공백 제거 · UTF-8. **동일 구현 검증용 테스트 벡터 3건을 v1.0에 첨부한다** (백엔드 Java와 AI Python이 같은 해시를 내는지 통합 전 확인).

🔴 **`detection_evidence`는 `optional`이지만 보내면 해시 대상이다**(8/12 · 99 #43).
- **안 보낸 요청의 canonical payload에는 키 자체가 없다** — 기존 요청의 해시가 **바뀌지 않는다**(하위 호환).
- 🔴 **빈 배열과 생략은 같은 의미·같은 해시다.** 모델이 둘 다 `()`로 받아 *"클라이언트가 빈 배열을 명시했는가"* 를 **복원할 수 없으므로**, 복원 못 하는 구분을 해시에 넣지 않는다(넣으면 BE와 AI가 다른 값을 낼 수 있다).
- **정확 벡터 3종**(Python 참조 · `tests/ai/contract/test_detection_evidence_contract.py`에 고정):

| 요청 | `snapshot_hash` |
| --- | --- |
| legacy(새 필드 없음 = 빈 배열) | `sha256:4e90fe4929dced9d3fed2a4c8585766569dd7680a8c50a1be7e1f282c59d8e54` |
| `assignment_window` + `weekly_activity` | `sha256:42bf93a71cdaecc0b3d6e4348ba8eddaf0f556894a81869285fade630c4e265d` |
| `enrollment_transition` | `sha256:103fd498b6bc7e09f0bc981acf8cde9a839981b81e398761d37af1a5a1ffb732` |

- 🔴 **`passage_ref`는 해시 대상이 아니다** — 백엔드 요청 계약(`AiDetectionRequest.LearningEventSnapshot`)에 **없는 AI 전용 필드**라, 넣으면 `learning_events`가 있는 모든 실요청에서 Java와 값이 갈린다(2026-08-13 대조 · 99 #50). ⚠ 위 벡터 3종은 `learning_events`가 비어 있어 **값이 바뀌지 않는다**.
- `source_table`은 **kind마다 값이 하나**다(`assignment_window`→`assignment_week_summary` · `weekly_activity`→`student_week_activity` · `enrollment_transition`→`student_status_history`). ⚠ **JSON 타입은 문자열 그대로** — 허용값만 닫았다(BE DTO 무변경). 교차 조합은 **400**.
- `at` = 집계는 `week_start`, 상태 전환은 `occurred_at`. 배열 **입력 순서가 달라도 같은 해시**.
- 값 하나가 바뀌면 해시가 달라진다 — **같은 멱등키에 근거만 다른 요청은 409 `IDEMPOTENCY_CONFLICT`**.
- AI 쪽 참조 구현: `src/ai/detection/canonical.py`(`canonical_snapshot_payload`). ⚠ **AI는 요청 해시를 재계산해 검증하지 않는다** — 산정 주체는 백엔드다. Java ↔ Python 실 대조는 후속 API 통신 테스트.

> 요약·리뷰용 시각 버전(팀공유본 html)은 노션에 있다 — **충돌 시 레포의 본 md가 정답.** JSON 예시의 `//` 주석은 설명용이며 실제 전송 페이로드에는 포함하지 않는다. 요청 바디만 빠르게 볼 때는 `05_request_json.md`.
