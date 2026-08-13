# M2 스튜디오 — 승인 보류 12건 확정안 (2026-08-12)

**B(염준영) 소유 · BE 전달본 · 서명 요청본**
회신 대상: 백엔드 검증 결과 `승인 보류`(차단 1~4 · 높은 위험 5~10 · 중간 11~12)
함께 볼 것: `2026-08-12_m2_http_contract_reply_to_BE.md`(실측·fixture) ·
`2026-08-12_m2_problem_generation_order_to_BE.md`(화면 4스텝 오더)

> 🔴 **이 문서는 질문을 남기지 않습니다.** 12건 전부에 **값**을 넣었습니다. AI 소유 항목은
> 확정이고, adapter·Backend 소유 항목은 **승인만 하면 되는 형태의 확정 제안**입니다.
> 이견이 있는 행만 고쳐서 돌려주시면 그 행만 다시 잡겠습니다.
>
> ⚠ **§0의 정정 두 건을 먼저 보십시오** — 초안의 값 그대로 만들면 정상 실행이 실패로
> 처리되거나(관찰 상한), 계약이 빈 채로 잠깁니다(OpenAPI).

---

## 0. 초안 그대로 두면 사고가 나는 값 2건

### ⓐ 🔴 관찰 상한 5분 30초는 **너무 짧습니다** — 정상 실행이 기술 실패가 됩니다

초안은 *"AI 실행 상한 5분, adapter 관찰 상한 5분 30초"* 입니다. 우리 실측 상한은 다릅니다.

```
PG_LEASE_SECONDS = 300                    # lease 1회 = 5분
DEFAULT_MAX_RECOVERY_ATTEMPTS = 3         # lease 만료 시 재큐 최대 3회
⇒ 최악 실행 상한 ≈ 20분, 그 뒤 error_code=worker_recovery_exhausted 로 실패 확정
```

5분 30초에 끊으면 **아직 정상적으로 재시도 중인 잡**을 adapter가 기술 실패로 확정합니다.

**확정안:** adapter 관찰 상한 = **21분**(우리 최악 상한 + 여유). 파일럿 실측 뒤 축소합니다.
좁히고 싶으시면 AI가 `PG_LEASE_SECONDS`·recovery 횟수를 낮추는 쪽이 맞습니다 — **관찰 상한을
실행 상한보다 짧게 두는 구성은 어떤 값에서도 틀립니다.**

### ⓑ 🔴 `POST /v1/problems`는 **즉시 돌아오지 않을 수 있습니다**

초안은 *"202를 받고 폴링"* 을 전제하는데, v1은 **POST 핸들러 안에서 러너를 한 번 돌립니다.**
문항 1개당 LLM 2콜(생성 + 교차 풀이)이므로 `count=20`이면 **응답까지 수 분**이 걸릴 수
있고, 그때 202 바디의 `status`는 이미 `succeeded`입니다.

**확정안:**
- adapter의 POST **HTTP timeout = 300초**(lease와 동일).
- timeout으로 끊겼어도 **잡은 계속 돕니다.** 같은 `Idempotency-Key`로 재호출하면 **새 잡을
  만들지 않고** 기존 응답을 돌려줍니다 — 그것이 복구 경로입니다.
- 폴링은 **202의 `status`가 종단이 아닐 때만** 시작합니다.

---

## 1. 차단 항목 확정 (1~4)

### 1. 문서 상태와 계약 정본 `[AI 확정 + BE 승인]`

| 항목 | 확정값 |
| --- | --- |
| 문서 상태 | `APPROVAL_PENDING` — 세 팀 서명 후 `CONFIRMED` |
| **v1 계약 정본** | 🔴 **AI 저장소의 HTTP fixture 17종** (`tests/ai/contract/fixtures/http/`) |
| 정본 저장소 | `MTVS5-CheckOn/CheckOn-AI` |
| 기준 커밋 | 브랜치 `claude/kafka-implementation-spec-9f8a25`의 **머지 커밋 SHA** — 머지 즉시 통보 |
| OpenAPI | **목표로만 둡니다.** 현재 우리 OpenAPI에는 요청·응답 스키마가 **0개**라 정본이 될 수 없습니다(회신 문서 §1-① 실측). 정본화는 엔드포인트 13개 시그니처 변경이고 8개가 A 소유·envelope이 양자라 **B 단독 불가** |
| 변경 절차 | fixture 변경 = AI PR + BE 리뷰. 호환 깨는 변경은 **사전 통보 후** 머지 |

⚠ 초안이 참조한 `4..txt`·`PROBLEM_STUDIO_AI_TEAM_HANDOFF.md`는 **AI 저장소 파일이 아닙니다.**
합의 문서의 참조는 위 두 handoff 문서 경로로 바꿔 주십시오.

### 2. Step 1 진단 호출 경로 `[확정 제안 — 동기]`

🔴 **비동기로 만들 필요가 없습니다.** 진단은 결정론 계산이라 **LLM 호출이 0건**이고 응답이
**수 ms**입니다(20셀 응답 9,838 bytes 실측). Kafka 왕복·상태 머신·유효기간 정책을 새로
만들 이유가 없습니다.

**확정안 A (권고):**

```
FE → BE  GET /api/v1/problem-studio/students/{id}/weakness-analysis
BE → AD  동기 HTTP
AD → AI  POST /v1/diagnosis         (timeout 5초)
```

| 항목 | 확정값 |
| --- | --- |
| 프론트 상태 | `PENDING` **없음** — 동기 응답 |
| 결과 유효기간 | `snapshot_hash`가 같으면 **같은 지도**입니다. BE가 학습기록 스냅숏 해시로 캐시하면 재계산이 없습니다 |
| 재진단 조건 | 새 학습 이벤트가 들어와 `snapshot_hash`가 바뀔 때 |
| 실패 시 | AI 400/5xx면 BE가 **그리드 없이** 화면을 그립니다(빈 표). 옛 판정을 섞지 않습니다 |
| 🔴 함정 | 다른 입력에 **같은 `snapshot_hash`를 재사용하면 앞선 결과가 그대로 재반환**됩니다(멱등 판정 축이 그 필드입니다) |

**폴백 B (Kafka를 꼭 써야 한다면):** 이벤트 두 개면 됩니다. 형태만 적어 둡니다 —
소유는 BE·adapter입니다.

```
diagnosis.requested   payload: { diagnosis_request_id, tenant_id, student_ref,
                                 period{from,to}, as_of, snapshot_hash, events[] }
diagnosis.completed   payload: { diagnosis_request_id, snapshot_hash, status,
                                 grid{...}, weakness_map{...} }
diagnosis.failed      payload: { diagnosis_request_id, snapshot_hash, error_code }
```

상관관계 키는 `diagnosis_request_id`, 중복 판정 키는 `(tenant_id, student_ref, snapshot_hash)`.

### 3. 문항 본문의 최종 도착 경로 `[확정 제안 — 이벤트에 전량 적재]`

**확정안: adapter가 `GET /v1/problems/{job_id}/items`를 조회해 정규화 이벤트에 문항 전량을
싣습니다.** BE의 현재 projector 가정과 같고, 저장소를 새로 만들 필요가 없습니다.

근거 3건:

| 우려 | 실측 |
| --- | --- |
| 개인정보 | 🔴 **위험 경로가 없습니다.** AI 입력이 alias뿐이라 문항 본문에 실명·연락처가 들어갈 수 없습니다. LLM 전송 직전 redaction도 fail-closed입니다 |
| 메시지 크기 | 문항 1개 1,081 bytes(픽스처) — 실 LLM 문항을 4배로 잡아도 **20문항 ≈ 90KB**. Kafka 기본 상한 1MB에 여유 |
| 보존 | 결과 토픽 보존 **30일 이하**를 권고합니다(AI 멱등 보존과 같은 창) |

**폴백:** 이벤트가 상한을 넘으면 adapter가 `result_ref`만 싣고 자기 저장소에 본문을 두는
쪽으로 내려갑니다. **AI에 재조회를 요구하지 않습니다.**

⚠ *"이벤트에 자유 텍스트 금지"* 는 **AI가 발행하는 이벤트**에 대한 우리 규약이었습니다.
이제 이벤트를 만드는 주체가 adapter이므로 **최종 결정권은 BE에 있습니다.**

### 4. child 결과 식별 키 `[확정 제안]`

제안하신 6필드를 그대로 채택합니다. **소유와 생성 주체만 명확히 합니다.**

| 필드 | 생성 주체 | AI가 아는가 |
| --- | --- | --- |
| `problem_request_id` | Backend | ❌ (adapter가 보관) |
| `problem_execution_id` | Backend | ❌ |
| `target_index` | Backend(요청의 `targets[]` 순번) | ❌ |
| `adapter_execution_id` | adapter | ❌ |
| `job_id` · `set_id` | **AI** | ✅ |

🔴 **AI는 부모 축 4개를 모릅니다.** 요청 바디에 임의 필드를 넣어 되돌려 받는 경로가
**없습니다**(`extra="forbid"`).

**운반 수단 확정:**

```
X-Request-Id: {problem_execution_id}:{target_index}
```

앱 공통 미들웨어가 이 헤더를 **응답 헤더로 그대로 echo**합니다. AI 로그·원장에도 이 값이
남아 사후 추적이 가능합니다. `Idempotency-Key`도 같은 값에서 유도하면 재전송에 같은
`job_id`가 나옵니다.

**⇒ 모든 진행·종단 이벤트에 `problem_execution_id`를 싣고, 부모 요청은 child ID 셋
(`job_id`·`execution_id`·`set_id`)을 **child 행에** 저장해 주십시오.** 현재처럼 부모 행에
단일 컬럼으로 두면 두 번째 child가 계약 위반으로 거절됩니다.

---

## 2. 높은 위험 항목 확정 (5~10)

### 5. fan-out 단위 `[확정]`

🔴 **영역×유형 셀 하나당 child 하나.**

```
targets: [{reading, FACT, 4}, {literature, INFER, 3}]
  → child#0  POST /v1/problems  area_tag=reading      type_tags=[fact]   count=4
  → child#1  POST /v1/problems  area_tag=literature   type_tags=[infer]  count=3
```

**유형별 수량은 AI 계약이 아닙니다.** 슬롯의 유형은 `type_tags`를 라운드로빈으로 돌기
때문에(`workflow.py`) 4/3은 우연히 맞고 5/2는 못 맞춥니다. 수량을 보존하는 방법은 셀별
child가 유일합니다.

### 6. 테넌트 바인딩 `[AI 확정 — 이미 그렇습니다]`

| 요구 | 현재 AI |
| --- | --- |
| 모든 API에 `X-Tenant-Id` 필수 | ✅ 4개 엔드포인트 전부. 없으면 400 |
| `job_id`·`set_id`의 tenant 검증 | ✅ 조회가 tenant 스코프 |
| 남의 리소스는 존재 은닉 | ✅ **404**(403이 아닙니다 — 초안 정정) |
| 멱등 레코드 tenant 분리 | ✅ 키 = `(tenant_id, endpoint, idempotency_key)` |
| 서비스 토큰 audience·scope | ❌ **인증 자체가 없습니다** — 7/15 "내부망 전용 호출" 전제 |

**확정안:** v1은 **내부망 전용 + 인증 없음**으로 갑니다. adapter가 망 밖에 서면 API Key
헤더를 도입하고, **BE 발급 · AI 검증**으로 나눕니다(앱 공통 계층 변경이라 AI 내부 합의 선행).

### 7. 부모·child 상태 전이표 `[확정 제안]`

**child 상태 = AI 잡 상태**(AI 소유·확정):

| 축 | 값 |
| --- | --- |
| 실행 phase | `queued` · `leased` · `running` · `paused` · `succeeded` · `failed` · `cancelled` |
| 세트 결과 | `generated` · `partial_success` · `failed` · `rejected_insufficient` |
| 문항 상태 | `verified` · `needs_review` · `verification_unavailable` · `dropped` |
| adapter 관측 실패 | `timed_out` · `delivery_failed` (**AI 어휘가 아닙니다** — adapter 소유) |

🔴 **"사용 가능"의 정의(확정):** `verified` **와** `needs_review` **둘 다 발행 가능**합니다.
`needs_review`는 강사 확인 배지일 뿐 실패가 아닙니다. `verification_unavailable`은 강사
확인 후 판단, `dropped`는 문항이 존재하지 않습니다.

**부모 집계 규칙(확정 제안 — BE 소유):**

| child 상황 | 부모 상태 |
| --- | --- |
| 하나라도 `queued`/`running` | `RUNNING` |
| 전부 종단 · 성공 문항 ≥1 · 실패 child 0 | `SUCCEEDED` |
| 전부 종단 · 성공 문항 ≥1 · 실패/타임아웃 child ≥1 | `PARTIAL_SUCCESS` |
| 전부 종단 · 성공 문항 0 · 전부 `rejected_insufficient` | `SUCCEEDED`(문항 0건) |
| 전부 종단 · 성공 문항 0 · 실패 ≥1 | `FAILED` |
| 취소된 child가 섞임 | 취소는 **실패로 세지 않습니다** — 나머지로 위 규칙 적용 |
| Kafka 발행 실패 | `DELIVERY_FAILED` (AI 결과 아님) |
| adapter 관찰 상한 초과 | child `timed_out` → 위 규칙의 "실패"로 셈 |

⚠ **부모에 `PARTIAL_SUCCESS`가 없으면 성공률이 틀립니다** — 현재 부모 상태 집합에 추가가
필요합니다.

### 8. 멱등 보존과 canonicalization `[AI 확정]`

| 항목 | 확정값 |
| --- | --- |
| AI 멱등 보존 | 🔴 **30일** (초안의 7일은 틀립니다 — `DEFAULT_TTL = timedelta(days=30)`) |
| 키 스코프 | `(tenant_id, endpoint, idempotency_key)` |
| 같은 키 + 같은 바디 | 기존 응답 재반환(**같은 `job_id`**) |
| 같은 키 + 다른 바디 | **409** |
| 실패한 잡에 같은 키 | 🔴 **기존 응답을 그대로 돌려줍니다.** 재시도하려면 **새 키**를 쓰십시오 |
| adapter 매핑 보존 | `(problem_request_id, problem_execution_id, target_index) → idempotency_key → job_id → set_id`를 **30일 이상** |

**canonicalization(확정):**

```python
canonical = json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
digest    = "sha256:" + sha256(canonical.encode("utf-8")).hexdigest()
```

🔴 **엔드포인트마다 동일성 판정 축이 다릅니다** — `POST /v1/problems`는 위 canonical 해시,
`POST /v1/diagnosis`는 바디의 **`snapshot_hash` 필드**입니다. `null`은 키를 남긴 채
직렬화되므로 **필드를 빼는 것과 `null`을 보내는 것은 다른 요청**입니다.

### 9. 부모 문항 수 상한 `[확정 제안 — 20]`

**부모 요청 합계 = 20문항.** 100은 근거가 없습니다.

| 축 | 값 |
| --- | --- |
| child(=AI 요청) 하나당 | `count` 1~20 (**AI 계약**) |
| 부모 합계 | **20** — 현재 BE 검증·OpenAPI·Step 4 선택 한도와 이미 일치 |
| 비용 근거 | 문항당 LLM **2콜**. 20문항 = 40콜 |

확대는 파일럿 실측(처리 시간·드롭률) 뒤 제품/FE 승인과 함께 다시 봅니다.

### 10. `language` + `media` 병합 `[확정]`

🔴 **전제 정정: AI는 5영역 전부 출제합니다.** `language` 외 영역은 요청에 자료 명세가 함께
와야 할 뿐입니다(회신 문서 §1-④). v1이 `language`뿐인 이유는 **프론트에 자료 입력 화면이
없다는 제품 사정**입니다.

| 항목 | 확정값 |
| --- | --- |
| 그리드 데이터 | AI가 **측정 5셀을 그대로** 보냅니다 — 병합은 화면이 합니다 |
| 병합 행 표시 | `language`·`media` **원본 두 셀을 보존**하고 각각의 판정을 볼 수 있게 합니다 |
| `media`가 weak일 때 | **"현재 출제 미지원" 배지 + 출제 버튼 비활성** |
| 🔴 금지 | 선택 시 `language`로 **임의 변환하지 않습니다** |
| 사유 표기 | *"AI 미지원"* 이 아니라 *"자료 입력 화면 미구현"* 으로 적어 주십시오 |

---

## 3. 중간 위험 항목 확정 (11~12)

### 11. 재시도 횟수와 backoff `[확정]`

**총 3회(최초 1 + 재시도 2) · backoff `0.5초, 1초`.** 세 번째 backoff는 삭제합니다.
재시도 대상은 **5xx·네트워크 오류·timeout뿐**입니다 — **400·409는 재시도하지 않습니다**
(호출자가 고쳐야 하는 요청입니다).

### 12. 관찰 상한 초과 이후 `[확정]`

| 질문 | 확정값 |
| --- | --- |
| 관찰 상한 | **21분** (§0-ⓐ) |
| 초과 시 child | `timed_out`으로 **기술 실패 확정** |
| AI job 취소 호출 | ❌ **불가** — AI에 취소 HTTP 엔드포인트가 없습니다 |
| 그동안 AI는 | 🔴 **계속 돕니다.** 폴링을 멈춰도 잡은 진행됩니다 |
| 늦게 성공한 결과 | `GET`으로 **회수 가능**합니다. 단 **부모 terminal은 번복하지 않습니다** — 재출제는 새 요청입니다 |
| 재처리 시 | 같은 `Idempotency-Key`로 POST하면 **기존 잡을 그대로** 받습니다(새 잡 아님) |
| ⚠ 단서 | AI가 그 사이 **재기동되면 결과가 사라집니다**(§4 ⓐ) |

---

## 4. 남아 있는 AI 쪽 미해소 (합의 문서에 그대로 적어 주십시오)

| # | 항목 | 운영 전 필수 |
| --- | --- | --- |
| ⓐ | **요청·결과 저장소가 인메모리** — 재기동하면 `GET`이 결과를 못 읽습니다 | 🔴 **예** |
| ⓑ | **job 취소 API 없음** | 아니오(12번 확정으로 우회) |
| ⓒ | **OpenAPI 스키마 0개** | 아니오(fixture 정본으로 우회) |
| ⓓ | **HTTP 인증 없음** | 내부망 전제면 아니오 |
| ⓔ | **배포 파이프라인 없음** — 두 신규 엔드포인트는 코드에만 있습니다 | 🔴 **예** |
| ⓕ | **문항 수정·교체·삭제 엔드포인트 없음** | 아니오(Step 3 범위 밖) |
| ⓖ | **`weakness_auto` 미배선** | 아니오(v1은 강사 선택) |

**ⓐ가 유일한 AI 쪽 착수 대기 항목**이고, 저장 테이블 결정이 양자 승인 파일이라 AI 내부
합의가 선행입니다. 다음 순번으로 잡겠습니다.

---

## 5. 서명

| 팀 | 항목 | 상태 |
| --- | --- | --- |
| AI(B) | §1-1·6·8, §2-5·9·10, §3-11·12, §0-ⓐⓑ | ✅ **확정** |
| Backend | §1-3·4, §2-7, §1-2(A안 승인) | ☐ |
| Adapter | §0-ⓐⓑ, §3-11·12 | ☐ |
| 제품/FE | §2-9(20문항), §2-10(미지원 표시) | ☐ |

**이견이 있는 행만 표시해 돌려주시면 그 행만 다시 잡습니다.** 전 행 승인이면 문서 상태를
`CONFIRMED`로 올리고 fixture 기준 커밋을 고정하겠습니다.
