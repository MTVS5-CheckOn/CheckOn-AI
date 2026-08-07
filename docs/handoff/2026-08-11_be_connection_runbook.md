# BE 연결 런북 — **되는 것과 안 되는 것**

**2026-08-11 · A(박진희) 소유 · BE 전달본**
선례: `2026-08-06_redaction_layer_boundary_to_BE.md`

> 🔴 **①~③은 돌려서 값을 찍었다.** 안 돌린 절차는 런북이 아니라 희망이다.
> 🔴 **§2(안 되는 것)를 먼저 읽어 주십시오** — 되는 것만 적으면 안 되는 것을 결함으로 신고하게 됩니다.

---

## 1. 되는 것 — 절차와 실측값

### ① `LLM_PROVIDER` 세팅

```
LLM_PROVIDER=openai_compat    실 LLM (.env의 OPENAI_BASE_URL·OPENAI_API_KEY·OPENAI_MODEL)
LLM_PROVIDER=fake             결정론 Fake — CI 기본. 실 호출 0건
```

**실측(8/11):**

```
LLM_PROVIDER=fake  →  FakeCounselLlmProvider · name = 'fake-counsel'
```

⚠ **provider는 기본값이 없다.** 조립 루트가 `set_counsel_provider()`를 안 부르면
`CounselProviderNotWired`로 **기동이 실패**합니다 — 배선 실수가 조용한 날조 산출이 되지
않게 일부러 그렇게 뒀습니다.

### ② 🔴 기동 로그에 Fake 경고가 없는지 확인

**실측(8/11) — `LLM_PROVIDER=fake`일 때 반드시 이 줄이 뜹니다:**

```
[WARNING] counsel provider=fake — LLM_PROVIDER='fake'이라 결정론 Fake로 조립한다.
          실 LLM 호출은 0건이고 산출물에는 provider='fake-counsel'가 남는다(사후 구분용).
          실 경로는 LLM_PROVIDER=openai_compat.
```

> **연결 후 이 줄이 보이면 실 LLM이 안 붙은 것입니다.** 없어야 정상입니다.

### ③ 🔴 원장에 실 provider 이름이 찍히는지 확인

한 번 호출하고 `AI_RUN.model_provider`를 봅니다. **실측(8/11 · Fake 경로):**

```
POST /v1/counsel/drafts        202
GET  /v1/counsel/drafts/{id}   200 · status=succeeded · draft_status=generated · 본문 261자

AI_RUN.model_provider = 'fake-counsel'      ← 🔴 이 표식으로 사후에도 구분됩니다
AI_RUN.model_name     = 'template'
```

**실 LLM이면 `model_provider`가 벤더 이름(`local-openai-compat` 등)이고 `model_name`이
모델 ID입니다.** `fake-counsel`이 찍혀 있으면 그 실행은 Fake입니다 — 로그를 놓쳐도
원장으로 되짚을 수 있습니다.

⚠ **호출이 0건인 실행은 셋이 전부 `null`입니다** — 이건 정상입니다(사용 축 · 04 §2.2).
캐시 히트·게이트 선차단이 그 경우입니다.

### ④ 기동 자체

```
uv run uvicorn ai.api.app:app --reload
```

종단 확인 순서(로그 62 선례): `POST 202` → `GET 200` → `refine 200`.

---

## 2. 🔴 안 되는 것 — 연결해도 안 바뀌거나 안 서는 것

| # | 항목 | 실상 |
| --- | --- | --- |
| ⑤ | **등록 안 된 경로의 404** | `GET /v1/nope` → `{"detail":"Not Found"}` — **envelope도 `meta`도 없습니다**(405도 같음). Starlette가 라우트 매칭 **전에** 자기 404를 내 우리 핸들러가 안 탑니다. 🔴 **「버전을 못 읽었다」가 아니라 「경로가 틀렸다」로 읽어 주십시오**(04 §2.2에 명시 · 99 ㊜) |
| ⑥ | **응답 형태 변경** | **0건.** #153에서 축을 확정했지만 응답은 안 바뀌었습니다 — BE가 지금 보는 형태 그대로입니다 |
| ⑦ | **LLM 실패 서킷** | 🔴 **안 섭니다.** 라우터 `enqueue`가 `contexts={student_ref: context}`로 **N=1**이라 연속 실패가 최대 1인데 임계는 3입니다. 테스트는 워커를 직접 N>1로 부르므로 **초록인데 프로덕션에선 안 섭니다**(99 #08). **「서킷이 있으니 안전하다」를 전제로 설계하지 말아 주십시오** |
| ⑧ | **`recursion_limit`** | 미설정 — 라이브러리 기본값이고 버전마다 다릅니다(99 #08) |
| ⑨ | **PG 영속** | 기본 `store_backend=memory`입니다. 재기동 후 잔존은 별도 설정이 필요합니다 |
| ⑩ | **`meta.execution_id`가 항상 원장 키는 아님** | `template_only`·근거 0건은 워커·LLM을 안 타 **원장에 행이 없고**, 그 값은 **상관 ID**입니다. 04 §2.2의 엔드포인트별 부류 표를 먼저 확인해 주십시오 |

---

## 3. 🔴 이번에 바뀐 것 — 게이트에 하한이 생겼습니다 (8/11)

**종전에는 `"네."` 두 글자도 `draft_status=generated`로 나갈 수 있었습니다.** 게이트 규칙이
전부 「있으면 안 되는 것」이라 통과가 떨어져 나오는 값이었습니다.

```
too_short:{len}<{min}      새 사유 코드 (`too_long`과 대칭)
min = 블록 수 × 60자        「블록당 최소 한 문장」 · 24조합에서 180~240자
```

**BE 영향:**

- `draft_status=failed` + `fail_reason=gate_exhausted:too_short:...`가 **새로 나올 수 있습니다.**
  기존 `gate_exhausted` 처리와 같은 경로입니다 — 새 상태값은 없습니다.
- ⚠ **4·5차 실 LLM 실측에서 `gate_exhausted`는 0건**이었고 실 초안은 300~466자라
  **정상 초안이 새로 막힐 여지는 관측되지 않았습니다.** 창은 조합별로 최소 180자입니다.

---

## 4. 확인 체크리스트

```
□ ①  LLM_PROVIDER=openai_compat 세팅 · .env에 OPENAI_* 셋
□ ②  기동 로그에 "counsel provider=fake" 경고가 **없다**
□ ③  한 번 호출 후 AI_RUN.model_provider가 **'fake-counsel'이 아니다**
□ ④  POST 202 → GET 200 → refine 200
□ ⑤  없는 경로 404가 meta 없이 오는 것을 「경로 오류」로 처리한다
□ ⑦  서킷을 전제로 한 재시도 설계가 없다
```

---

## 5. 아직 회신을 기다리는 것

| | |
| --- | --- |
| **#12** | `contracts/execution.py:66`·`db/models.py:782`의 주석 정정 — **양자 승인** 요청(8/11) |
| **㊺** | `assembly.py:318`(pg)의 `generation_params`가 사용 축인지 — 8/10 요청 |
