# 위험신호 실 OpenAI HTTP 왕복 — **Windows AI 서버 실측**

**A(박진희) 소유 · BE 전달본** · 지시서 78
선례: `2026-08-12_detect_be_live_9000.md`(Fake 회차 · 9000 관문)

> 🔴 **이 문서는 실제 OpenAI를 태운 회차의 실측이다.** Fake 회차와 같은 요청·같은 서버·
> 같은 DB에서 **provider만 바꿔** 돌렸고, 두 회차를 값으로 대조했다.
> ⚠ **접속정보·API 키·`DATABASE_URL`·실제 IP·응답 원본 파일 경로는 적지 않는다.**

---

## 0. 한 장 요약

| 항목 | 결과 |
| --- | --- |
| 실행 경로 | Windows AI 서버 `POST /v1/detect` (내부에서 `api.openai.com` 호출) |
| provider / model | **`openai-compat` / `gpt-5.4-mini`** (응답 model `gpt-5.4-mini-2026-03-17`) |
| HTTP | **200** · `X-Request-Id` echo **일치** |
| 신호 | **7건** |
| Fake 대비 판정 필드 불일치 | **0** |
| 실제 GPT가 바꾼 것 | **`brief.text` 뿐** |
| `gate_passed` | **7/7 true** |
| `fallback_used` | **7/7 false** (템플릿 폴백 0건) |
| 재생성 | **0회** (호출 7 = 신호 7) |
| 근거 밖 숫자·사실 | **0건** |
| PII · 마스킹 토큰 · 금칙어 | 각 **0건** |
| `AI_RUN` | **1행** |
| `LLM_CALL` | **성공 7건** (`outcome=ok`) |
| `LLM_PAYLOAD` 비밀정보 | **0건** |
| 멱등 재요청의 추가 OpenAI 호출 | **0건** |
| 이번 성공 회차 실 호출 | **7건** (진단 2건 포함 **9 / 45**) |
| 프롬프트 수정 | **불필요** |

---

## 1. 실행 환경

| | |
| --- | --- |
| develop HEAD | `71056b6` (#226 `fix(llm): 실서비스 provider를 OpenAI 단일 경로로 정리한다` 포함) |
| OS | Windows 11 Pro (한국어 로케일) · Python 3.12.13 · uv |
| AI 서버 | `uvicorn ai.api.app:app --host 0.0.0.0 --port 9000` (**`--reload` 미사용**) |
| provider 설정 | `LLM_PROVIDER=openai_compat` · `OPENAI_BASE_URL=https://api.openai.com/v1` · `LANGSMITH_TRACING=false` |
| 저장소 | `STORE_BACKEND=pg` · Alembic `0010_counsel_ctx_draft_body` |
| 요청 | 정본 샘플 — 학생 10명 · `learning_events` 1,070 · `detection_evidence` 201 · `snapshot_hash=sha256:test-42-2026-07-20` |
| 식별자 | 회차마다 **새 tenant·request·idempotency key** · 실명·연락처 없음 |

🔴 **두 주소를 혼동하지 말 것 — 고르는 것이 아니라 둘 다 있다.**

```
승우님/테스트 클라이언트  →  AI 서버(:9000)
AI 서버                   →  api.openai.com
```

---

## 2. 판정 — Fake 회차와 값 대조

**같은 요청·같은 서버·같은 DB에서 provider만 바꿨다.** 결정론 축이 하나라도 흔들리면
**LLM이 판정에 새어 든 것**이므로, 아래 필드는 **정확히 같아야** 한다(불변식 1).

```
signal_id 집합 동일   : True
stats 동일            : True
필드 불일치           : 0
  student_ref · rule_id · signal_type · display_label · score · rank ·
  advisory · lifecycle · evidence
brief.text            : 7건 전부 Fake와 다름  ← 실제 GPT가 바꾼 것은 여기뿐
```

### brief 7건 — 전량 게이트 통과

| # | rule | gate | fallback | 길이 | 문장 | 근거 밖 숫자 | mask/연락처/금칙어 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 0 | R1 | true | false | 42 | 1 | 0 | 0 |
| 1 | R2 | true | false | 39 | 1 | 0 | 0 |
| 2 | R4 | true | false | 35 | 1 | 0 | 0 |
| 3 | R6 | true | false | 38 | 1 | 0 | 0 |
| 4 | R3 | true | false | 50 | 1 | 0 | 0 |
| 5 | R5 | true | false | 18 | 1 | 0 | 0 |
| 6 | R1 | true | false | 41 | 1 | 0 | 0 |

### 🔴 "근거 밖 숫자"를 어떻게 셌나 — **한 번 오탐이 났다**

**처음에는 `evidence[].summary` 문자열만 보고 셌고, 그래서 `62`·`88`·`25`가 근거 밖으로
잡혔다. 그건 오탐이었다.** brief에 실리는 수치의 근거는 evidence 요약문이 아니라
`build_contexts()`가 만들어 프롬프트에 싣는 **`EvidenceFact`(계산된 지표)** 다.

**엔진이 쓰는 그 함수를 그대로 불러 다시 세니 7건 모두 0이었다.**

```
[R1 st_02] 이번 주 정답률이 62%로 평소 88%보다 25%p 내려 평상시와 달라졌어요
  facts   ('이번 주 정답률','62%') ('평소 정답률(개인 기준선)','88%') ('하락폭','25%p')
  FEATURE_WEEK 실측  accuracy 0.625 → 62% · 기준선 0.875 → 88% · 낙폭 25%p

[R4 st_03] 정답률은 거의 그대로인데 문제 풀이 시간은 1.8배로 늘어났어요
  facts   ('정답률 변동(평소 대비)','0%p 이내로 유지') ('문제 풀이 시간(평소 대비)','1.8배')

[R3 st_09] 이번 주 학습 활동이 4건으로 평소 대비 36%라서 새 학습 공백이 보이고 평상시와 달라요
  facts   ('이번 주 학습 활동','4건') ('평소 대비','36%')
```

⚠ **다음 사람에게:** 근거 검사를 `evidence.summary` 문자열 매칭으로 만들면 **정상 brief를
환각으로 신고**한다. 근거의 정본은 `ai.composition.briefing_context.build_contexts()`의
`facts`다.

---

## 3. 원장

| | |
| --- | --- |
| `AI_RUN` | **1행** · `model_provider=openai-compat` · `model_name=gpt-5.4-mini` |
| `LLM_CALL` | `openai-compat / gpt-5.4-mini / ok` **× 7** — 실패 시도 0 |
| `LLM_PAYLOAD` | 7행 · `sk-`·`Bearer`·`postgresql` 저장 **0건** |
| `SIGNAL` | 7행 |
| `FEATURE_WEEK` | 90행 |
| `IDEMPOTENCY_RECORD` | 1행 |
| 타 tenant 행 | **0** |

🔴 **`model_provider`가 `fake-brief`가 아니라 `openai-compat`인 것이 "실제로 GPT를 탔다"의
사후 증거다.** 로그를 놓쳐도 원장으로 되짚을 수 있다(런북 ③).

---

## 4. 멱등 재요청 — 추가 외부 호출 0

같은 body · 같은 `Idempotency-Key` · 새 `X-Request-Id`로 1회 재요청.

```
HTTP 200 · execution_id 동일 · data 값 동일
LLM_CALL  28 → 35 → 35        재요청 구간 증가 0
AI_RUN 1 · SIGNAL 7           중복 0
```

**캐시가 외부 호출 앞에서 끊었다.** BE가 같은 Kafka 이벤트를 재전달해도 **OpenAI 비용이
다시 발생하지 않는다.**

⚠ 응답 **바이트**는 키 순서가 다를 수 있다(JSONB 왕복 · 선행 문서 별건 B). **DTO·구조 값**으로
비교한다.

---

## 5. 🔴 최초 403 판정을 정정한다 — **지우지 않고 남긴다**

> **처음 판단(틀림):** `GET /v1/models`가 `403 Missing scopes: api.model.read`를 냈으므로
> **키 권한이 부족해 실호출이 막혔다.** ⇒ 키 교체가 blocker다.

**실측으로 확인한 정확한 결론:**

| | |
| --- | --- |
| `GET /v1/models`의 `api.model.read` 403 | **completion 권한 부재를 뜻하지 않는다** — 모델 *목록 조회* 스코프일 뿐 |
| 기존 키의 completion | **가능했다** (인증 통과 · 권한 없으면 401이 났을 것) |
| `gpt-5.6-luna` | 해당 프로젝트가 **접근할 수 없었다** — `403 model_not_found: Project ... does not have access to model` |
| `gpt-5.4-mini` | **성공** (`'pong'` · in 7 / out 4 토큰) |

⇒ **blocker는 "키 전체 권한 부족"이 아니라 "선택 모델 접근 불가 + 모델 목록 조회 scope 부재"
두 가지였다.**

🔴 **이 구분이 중요한 이유:** `/v1/models` 실패만 보고 **멀쩡한 키를 다시 교체**하면 시간을
버리고 원인은 그대로 남는다. 두 403은 **메시지가 비슷하지만 다른 것**이다.

```
403 insufficient permissions · Missing scopes: api.model.read   → 목록 조회만 막힘. 실행과 무관
403 model_not_found · does not have access to model <id>        → 그 모델을 못 씀. 모델을 바꿔야 함
401                                                              → 키 자체가 문제
```

⚠ **진단은 `GET /v1/models`가 아니라 completion 1회로 한다.** 목록 조회 스코프가 없으면
후보 모델을 열거할 수 없으므로, 팀 확정 모델로 **한 번만** 찔러 본다.

⚠ **앞선 Mac `localhost:9001` 회차(403 · completion 8건)는 최종 검증 경로가 아니다** —
성공 회차에 합치지 않고 **장애 기록으로만** 보존한다.

---

## 6. 프롬프트 — 수정 불필요

지시서가 정한 수정 근거 다섯 가지 중 **관측된 것이 없다.**

```
□ evidence에 없는 수치·사실 생성      → 0건
□ 한 문장 형식 반복 위반              → 0건 (7/7 한 문장)
□ 낙인·단정 표현                      → 0건
□ 게이트 피드백 후 같은 오류 반복      → 재생성 0회라 해당 없음
□ 프롬프트 모순으로 전량 fallback     → fallback 0건
```

⚠ **`403`·`401`·`429`·timeout·5xx는 프롬프트 수정 근거가 아니다.** 이번 403도 프롬프트가
아니라 **모델 접근 설정**의 문제였다.

---

## 7. 호출 예산

| 회차 | 실 호출 |
| --- | --- |
| 앞선 실패 회차(Mac · 403) | 8 — **별도 장애 회차로 분리 집계** |
| 이번 성공 회차 — 진단 | 2 (`gpt-5.6-luna` 실패 1 · `gpt-5.4-mini` 성공 1) |
| 이번 성공 회차 — 본 검증 | **7** (신호 7 = 호출 7 · 재생성 0) |
| **합계** | **9 / 45** |

같은 실패를 확인하려고 반복 호출하지 않았다.

---

## 8. 실응답 파일 처리

승우님 DTO 파싱용 응답 원본은 **Windows 임시 디렉터리에만** 있다.
🔴 **저장소에 넣지 않는다.** 이 문서에 **경로도 적지 않는다** — 적으면 그 자체가 전달 경로가 된다.

**재확인 결과(원본 스캔):**

```
OpenAI 키(sk-) 0 · Authorization 0 · DB URL 0 · Windows 경로 0 · 사설 IP 0
전화 0 · 이메일 0 · 주민번호 0 · 마스킹 토큰 0 · LangSmith 키 0
참조는 전부 alias — student_ref(st_02…st_10) · class_ref(cl_a1·cl_b2) · record_id(le_*·aws_*·ssh_*)
```

⚠ **다만 `execution_id`·`signal_id`·tenant는 테스트 식별값으로 남아 있다.** 장기 보존이
필요하면 **그 값을 마스킹한 별도 fixture**를 만들고, 원본은 전달 후 폐기한다.

**전달 방법:** 파일을 승우님께 직접 전달한다(문서 경유 금지).

---

## 9. 남은 것

| | |
| --- | --- |
| BE → AI 실통신 | ☐ 승우님 호출 대기 |
| Java DTO 역직렬화 | ☐ — 이번 응답은 `signals=7`이라 Signal·Brief·EvidenceItem 경로를 **실제로** 검증할 수 있다(정적 응답 샘플은 `signals=0`이라 못 한다) |
| Java/Python canonical hash | ☐ — 선행 문서 §6-B의 벡터 3종으로 대조 |
| `GET /v1/health`·`/v1/meta/versions` | 미구현 — 별도 승인 PR |
| 멱등 재응답 바이트 동일성 | 미해소 — 별도 fix |
| `SIGNAL_BRIEF`·`EVIDENCE_ITEM` 영속 | 미해소 — 별도 저장 계약 |
