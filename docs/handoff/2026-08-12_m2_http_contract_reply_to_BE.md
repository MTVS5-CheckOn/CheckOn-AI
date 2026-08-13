# M2 HTTP 계약 회신 — 검증 지적 1~12 · AI-BE-01~13 (2026-08-12)

**B(염준영) 소유 · BE 전달본**
회신 대상: 백엔드 검증 결과(`승인 보류`, 지적 1~12) · `AI-BE-01~13` 세부 협의표 ·
`PROBLEM_STUDIO_AI_TEAM_HANDOFF.md`
선행 문서: `2026-08-12_m2_problem_generation_order_to_BE.md`

> 🔴 **§1을 먼저 읽어 주십시오.** 합의 초안에 들어간 값 네 개가 우리 코드와 다릅니다.
> 계약 문서에 틀린 숫자가 남으면 adapter가 그 숫자대로 만들어집니다.
> ⚠ 이 회신은 **AI가 소유한 축만** 답합니다. adapter·Backend 내부 설계(진단 이벤트 형태,
> 부모 상태 집계, child 저장 구조)는 우리가 정할 자리가 아니며, 그렇게 표시했습니다.

---

## 1. 먼저 정정할 사실 4건 (전부 실측)

### ① 🔴 지금 우리 OpenAPI는 계약 정본이 될 수 없습니다

*"POST /v1/problems의 유일한 정본은 OpenAPI"* 규약(4번 문서 §6-3)에 **원칙적으로 동의**하지만,
현재 AI 앱이 만드는 OpenAPI에는 **요청·응답 스키마가 하나도 없습니다.** 전 엔드포인트가
`Request`를 직접 읽고 `dict`를 돌려주도록 작성돼 있어서입니다.

```
POST  /v1/problems                   requestBody=False  responses=['202']
GET   /v1/problems/{job_id}          requestBody=False  responses=['200','422']
GET   /v1/problems/{job_id}/items    requestBody=False  responses=['200','422']
POST  /v1/diagnosis                  requestBody=False  responses=['200']
components.schemas: ['HTTPValidationError', 'ValidationError']   ← 우리 모델은 0개
```

즉 지금 `/openapi.json`을 받아 가시면 **경로 목록 말고는 아무 정보가 없습니다.** 이걸
정본으로 선언하면 계약이 비어 있는 채로 잠깁니다.

- **단기(이번 주 가능):** `§4`의 **HTTP fixture 17종**을 정본으로 씁니다. 실제 앱을 돌려
  얻은 응답이고, 응답이 바뀌면 우리 CI가 먼저 죽습니다.
- **중기(별도 작업):** OpenAPI 정본화는 엔드포인트 **13개 전부의 시그니처 변경**이고 그중
  8개(`detect`·`counsel` 3종·`imports` 3종·`classify`·`confirmations`)가 **A 소유**입니다.
  공통 응답 envelope 모델도 양자 승인 파일이라 **B 단독으로 못 합니다.** 착수 전 AI 내부
  합의가 필요하며 일정은 별도로 회신하겠습니다.

⇒ **오더:** 합의 문서의 *"OpenAPI 정본"* 조항은 **목표**로 두고, v1 연동 기준은 **fixture
정본**으로 적어 주십시오. 우리 저장소·브랜치는 `MTVS5-CheckOn/CheckOn-AI` ·
`claude/kafka-implementation-spec-9f8a25`이고, 기준 commit은 이 PR 머지 SHA로 고정합니다.

### ② 🔴 멱등 보존은 7일이 아니라 **30일**이고, 동일성 판정 축이 엔드포인트마다 다릅니다

```
DEFAULT_TTL = timedelta(days=30)      # db/repositories/idempotency.py:37
키 스코프    = (tenant_id, endpoint, idempotency_key)   # uq_idempotency_scope
```

지적 8번의 *"AI 멱등 7일 < 결과 30일"* 전제가 성립하지 않습니다. **양쪽 다 30일**입니다.

🔴 **더 중요한 함정 — 바디 동일성 판정이 엔드포인트마다 다릅니다.**

| 엔드포인트 | 동일성 판정 |
| --- | --- |
| `POST /v1/problems` | **바디 전체의 canonical 해시** |
| `POST /v1/diagnosis` · `POST /v1/detect` | 바디의 **`snapshot_hash` 필드 값** |

즉 `/v1/diagnosis`는 **입력이 달라도 `snapshot_hash`가 같으면 앞선 결과를 그대로 재반환**
합니다(그 값이 재현 키라서 의도된 동작입니다). adapter가 `snapshot_hash`를 재사용하면
낡은 지도를 받습니다 — 저희 테스트가 실제로 이 함정을 밟았습니다.

**canonicalization 규칙**(지적 8번 요청):

```python
canonical = json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
digest    = "sha256:" + sha256(canonical.encode("utf-8")).hexdigest()
```

키 정렬·구분자 무공백·비ASCII 그대로. `null`은 키를 **남긴 채** `null`로 직렬화되므로
**필드를 빼는 것과 `null`을 보내는 것은 다른 요청**입니다.

### ③ `403 TENANT_MISMATCH`는 우리 응답에 없습니다 — 전부 `404`입니다

지적 6번이 *"현재 문서의 403 TENANT_MISMATCH만으로는 IDOR 방지가 부족하다"* 고 했는데,
우리 구현은 애초에 403을 내지 않습니다. 다른 테넌트의 `job_id`는 **존재 여부를 숨기고
404**입니다(요구하신 처방과 같은 방향이며 이미 그렇습니다).

### ④ 🔴 `language`만 출제 가능한 것이 **아닙니다**

지적 10번은 *"진단은 media를 보여주는데 출제는 language만 된다"* 를 충돌로 봤습니다.
**AI는 5영역 전부 출제합니다.** 다만 `language` 외 영역은 요청에 **자료 명세가 함께** 와야
하고, 없으면 400 `source_procurement_not_implemented`입니다.

| area_tag | 함께 보내야 하는 것 |
| --- | --- |
| `language` | 없음 |
| `reading` | `passage`(도메인·분량·문단 수·문장 복잡도·금칙 버전) |
| `literature` | `work_selection`(갈래·시대·개념 키워드) |
| `speech_writing` · `media` | 생성 자료 요청(자료 종류·주제 힌트·금칙 버전) |

`reading` 요청의 정식 형태는 `post_problems.request.reading.json`에 넣었습니다.

⇒ *"v1은 language-only"* 는 **AI 제약이 아니라 프론트가 자료 입력 화면을 갖고 있지 않다는
제품 사정**입니다. media 취약 셀의 처방(비활성화·미지원 표시)은 그대로 유효하지만, 사유를
*"AI가 못 한다"* 로 적으면 틀립니다.

---

## 2. 검증 지적 1~12 회신

| # | 지적 | 소유 | AI 회신 |
| --- | --- | --- | --- |
| 1 | 문서 상태·OpenAPI 정본 미지정 | 공동 | §1-① — OpenAPI는 **현재 정본 불가**. fixture 정본 + 기준 commit 고정을 제안 |
| 2 | Step 1 진단 호출 경로 부재 | **BE·adapter** | 이벤트 형태는 AI 축이 아닙니다. 다만 **비동기로 만들 필요가 없습니다** — 진단은 결정론 계산이라 LLM 호출이 0건이고 응답이 수 ms입니다(§5). adapter가 동기 호출하고 Backend가 그 GET에서 기다려도 60초 논쟁이 생기지 않습니다. 유효기간·재진단 조건은 `snapshot_hash`가 같으면 같은 지도라는 성질로 정하시면 됩니다(§1-②) |
| 3 | 문항 본문의 최종 도착 경로 | **BE·adapter** | 🔴 **우리 "이벤트에 본문 금지" 규약은 AI가 발행하는 이벤트에 대한 것**이고, 이제 이벤트를 만드는 주체가 adapter이므로 **BE 결정입니다.** 개인정보 우려는 해소됩니다 — 입력이 alias뿐이라 **문항 본문에 실명·연락처가 들어갈 경로가 없습니다.** 크기는 §5 참조(20문항 ≈ 22KB 수준) |
| 4 | child 결과 식별 키 부재 | **BE·adapter** | AI는 `problem_execution_id`·`target_index`를 모릅니다. **운반 수단은 있습니다**: `X-Request-Id`를 요청에 실으면 응답 헤더로 **그대로 echo**합니다(앱 공통 미들웨어). adapter가 `{problem_execution_id}:{target_index}`를 그 헤더에 넣으면 왕복 상관관계가 섭니다. ⚠ 요청 바디에 임의 필드를 넣어 되돌려 받는 경로는 **없습니다**(`extra="forbid"`) |
| 5 | fan-out 단위 | 공동 | 🔴 **셀(area×type) 하나당 child 하나에 동의합니다.** 유형별 수량은 **AI 계약이 아닙니다** — 슬롯의 유형은 `type_tags`를 라운드로빈으로 돌기 때문에 4/3은 우연히 맞고 5/2는 못 맞춥니다. 수량을 보존하는 방법은 셀별 child가 유일합니다. child는 `type_tags` 1개 + `count` = 그 셀 수량 |
| 6 | 테넌트 바인딩 | AI | §1-③. 실측: 전 엔드포인트 `X-Tenant-Id` **필수** · 남의 `job_id`는 **404** · 멱등 레코드 키에 `tenant_id` 포함. 남은 것은 **서비스 토큰의 audience·scope**뿐이고 그건 AI-BE-08(현재 인증 미구현) |
| 7 | 부모 상태 집계표 | **BE** | 부모 상태는 AI 축이 아닙니다. child 어휘만 확정 제공: 실행 상태 `queued·leased·running·paused·succeeded·failed·cancelled`, 세트 결과 `generated·partial_success·failed·rejected_insufficient`. 🔴 **`needs_review`는 사용 가능한 문항입니다** — 강사 확인 배지일 뿐 실패가 아니고, 세트는 그대로 `generated`입니다 |
| 8 | 멱등 보존 기간 | AI | §1-② — **30일**입니다(7일 아님). canonicalization 규칙도 §1-②에 실측으로 적었습니다 |
| 9 | 부모 100문항 | **BE·제품** | AI 계약 상한은 **child 하나당 `count` 1~20**입니다. 부모 합계는 AI 축이 아니며, 문항당 LLM 2콜(생성 + 교차 풀이)이 드는 것만 계산에 넣어 주십시오 |
| 10 | language+media 병합 충돌 | 공동 | §1-④ — 전제가 틀렸습니다. 화면 처방(미지원 표시·비활성화)은 유효하나 **사유를 "AI 미지원"으로 적지 말아 주십시오** |
| 11 | 재시도 3회 vs backoff 3개 | **adapter** | AI 축 아님. 총 3회면 대기는 2개가 맞습니다 |
| 12 | 관찰 상한 이후 동작 | 공동 | 실측 회신: ⓐ adapter가 폴링을 멈춰도 **AI job은 계속 돕니다**(배경 드레인) ⓑ 나중에 GET하면 **그 결과를 그대로 회수**할 수 있습니다 ⓒ 🔴 **취소 HTTP API가 없습니다** — 계약에는 `cancelled`가 있지만 노출된 엔드포인트가 0개입니다 ⓓ 단 **AI가 재기동되면 사라집니다**(AI-BE-10) |

---

## 3. AI-BE-01~13 회신

| ID | 회신 | 내용 |
| --- | --- | --- |
| **01** 완료 감지 | **자료 제공 + 구현** | 종단 판정은 `data.status` ∈ {`succeeded`,`failed`,`cancelled`} **하나**입니다. 비종단 응답에 **`Retry-After`(기본 2초)를 이번에 붙였습니다** — 값은 AI 설정(`PG_POLL_RETRY_AFTER_SECONDS`)이라 우리가 느려지면 그 값을 올립니다. 종단 응답에는 헤더가 **없습니다**(있으면 adapter가 끝난 잡을 계속 돕니다). callback/webhook 계획은 **없습니다** — adapter가 폴링하는 구조에 동의했으므로 |
| **02** 다중 영역 | **동의(fan-out)** | 셀별 child. batch HTTP는 만들지 않겠습니다 — 부분 실패 격리·체크포인트 재개·`set_id` 의미가 전부 job 단위라 batch를 넣으면 그 셋을 다시 정의해야 합니다 |
| **03** POST 정본 | **자료 제공 + 대안** | fixture 7종(§4). OpenAPI는 §1-① |
| **04** `target_source` | **대안** | `teacher_weakness_selection`을 **추가하지 않겠습니다.** 우리 파이프라인이 가르는 축은 *"강사가 목표를 지정했는가(`manual_targets`)"* 대 *"AI 진단이 골랐는가(`weakness_map_id`)"* 둘뿐이라, 강사가 그리드를 보고 고른 것은 **의미상 `teacher_manual`과 같습니다.** 값만 늘리면 동작이 같은 세 번째 분기가 생깁니다. ⇒ **adapter가 `teacher_manual`로 변환**해 주십시오. 강사가 그리드에서 골랐다는 사실은 `manual_targets`의 스킬 노드 목록에 이미 남습니다 |
| **05** 진단 소유권 | **동의** | AI diagnosis가 정본. Backend는 재판정하지 않고 저장·전달만. 두 계산을 섞지 않는다는 판단에 동의합니다 |
| **06** 비-language | **정정 + 자료 제공** | §1-④. 영역별 필수 입력 스키마는 위 표와 `post_problems.request.reading.json` |
| **07** items 계약 | **자료 제공** | fixture 3종(정상·부분 성공·0건). 🔴 **일부 문항 실패로 HTTP를 실패시키지 않습니다** — 200 + 문항별 상태입니다. 세트 자체가 실패해도 200이고 `set_status: failed`입니다 |
| **08** HTTP 인증 | **추후** | 🔴 **현재 인증이 없습니다.** 7/15 결정이 *"AI 호출은 내부망 전용"* 이라 그 전제로 서 있습니다. adapter가 외부에서 붙는다면 인증이 필요하고, 그건 앱 공통 계층(A 소유·양자 승인) 변경이라 AI 내부 합의가 선행입니다. 방식은 **API Key 헤더**가 가장 싸다고 봅니다. secret 발급·보관·rotation 주체는 각 팀 인프라 소유 원칙(7/15 BE-3)대로 **BE가 발급, AI가 검증**을 제안합니다 |
| **09** 멱등성 | **동의 + 정정** | 같은 키 + 같은 바디 → **같은 `job_id`가 실린 같은 202 재반환**. 다른 바디 → **409**. 보존 **30일**. 🔴 **job이 실패한 뒤 같은 키로 다시 불러도 새 job을 만들지 않고 기존 응답을 돌려줍니다** — 재시도하려면 **새 `Idempotency-Key`** 를 쓰셔야 합니다 |
| **10** 영속성 | **추후(범위 확정)** | 영속: 잡 원장·멱등 레코드·문항 최종본·실행 원장. **비영속(인메모리)**: 요청 레코드·세트 결과. ⇒ **재기동하면 `GET /v1/problems/{job_id}`가 결과를 못 읽습니다.** 남은 것은 `ProblemRequestStore`·`ProblemResultStore`의 PG 구현이고, 저장 테이블 결정이 양자 승인 파일(`db/models.py`)이라 A 합의가 선행입니다. **운영 전 필수**로 등재했습니다 |
| **11** 라우터 상태 | **자료 제공** | `/v1/diagnosis`·`/v1/problems/{job_id}/items` 둘 다 **implemented + 코드상 등록 완료**, **approved 대기**(라우터 등록이 양자 승인 파일이라 A 리뷰 중), **deployed 아님**(배포 파이프라인 자체가 아직 없습니다) |
| **12** 크기 제한 | **자료 제공** | §5. `count` 상한 20이라 items 응답은 최대 20건 — **pagination을 두지 않겠습니다.** 413은 반환하지 않으며, 초과 요청은 400 스키마 위반입니다 |
| **13** 상호 fixture | **동의** | HTTP fixture = **AI 저장소 정본**(우리가 생성·CI 검증). Kafka fixture = **adapter 저장소 정본**으로 이관합니다. 우리 `worker_job.*` fixture 4종은 폐기하지 않고 *"adapter가 HTTP→Kafka 변환 결과를 대조하는 참고 계약"* 으로 남깁니다 |

---

## 4. 제공 자료 — HTTP fixture 17종

`tests/ai/contract/fixtures/http/` (그대로 복사해 adapter CI에 넣으시면 됩니다)

| 구분 | 파일 |
| --- | --- |
| POST 요청 | `post_problems.request.json` · `post_problems.request.reading.json` |
| POST 응답 | `post_problems.202.json` |
| POST 오류 | `post_problems.400.missing_header.json` · `post_problems.400.type_tag_not_supported.json` · `post_problems.400.source_procurement_not_implemented.json` · `post_problems.409.idempotency_conflict.json` |
| 상태 조회 | `get_problem.succeeded.json` · `get_problem.queued.json` · `get_problem.404.json` |
| 문항 조회 | `get_problem_items.generated.json` · `get_problem_items.partial_success.json` · `get_problem_items.no_items.json` |
| 진단 | `post_diagnosis.request.json` · `post_diagnosis.200.json` · `post_diagnosis.200.rejected_insufficient.json` · `post_diagnosis.400.unknown_skill_node.json` |

⚠ 실행마다 달라지는 `execution_id`·`job_id`·`set_id`·`item_id`는 고정 토큰으로 바꿔 두었습니다.
**adapter는 이 값들의 형식(UUID)만 신뢰**하고 특정 값에 의존하면 안 됩니다.

⚠ `get_problem.queued`·`items.partial_success`·`items.no_items` 셋은 실행으로 만들기 어려워
**응답 모델에서 직접** 생성했습니다. 모델이 바뀌면 같이 바뀌므로 형태 드리프트는 잡힙니다.

---

## 5. 크기·시간 실측

| 항목 | 실측 |
| --- | --- |
| 문항 1개 JSON | **1,081 bytes** (픽스처 기준 — 실 LLM 문항은 발문·해설이 길어 2~4배로 보십시오) |
| items 응답(1문항) | 1,732 bytes |
| items 응답(20문항 추정) | **20~90KB** — Kafka 기본 메시지 상한(1MB)에는 여유가 있습니다 |
| diagnosis 200 응답 | **9,838 bytes** (20셀 + 노드 판정 전량) |
| diagnosis 계산 | LLM 호출 **0건** · 결정론 순수 계산 — 수 ms |
| 문항 생성 | 문항 1개당 LLM **2콜**(생성 + 교차 풀이), 재생성 시 최대 3시도 |

---

## 6. AI가 아직 못 하는 것 (합의 문서에 그대로 적어 주십시오)

| # | 항목 |
| --- | --- |
| ⓐ | **요청·결과 저장소가 인메모리** — 재기동하면 `GET`이 결과를 못 읽습니다(AI-BE-10) |
| ⓑ | **HTTP 인증 없음** — 내부망 전용 전제(AI-BE-08) |
| ⓒ | **job 취소 API 없음** — 관찰 상한 초과 시 adapter가 AI에 취소를 요청할 방법이 없습니다 |
| ⓓ | **OpenAPI가 비어 있음** — 계약 정본으로 못 씁니다(§1-①) |
| ⓔ | **문항 수정·교체·삭제 엔드포인트 없음** — 계약만 있고 노출 0건 |
| ⓕ | **`weakness_auto` 미배선** — 자동 약점 출제는 진단이 출제 워크플로에 붙어야 합니다 |
| ⓖ | **배포 없음** — 두 신규 엔드포인트는 코드에만 있습니다 |

---

## 7. 우리 쪽 다음 작업 (합의와 무관하게 진행 가능한 것)

1. `ProblemRequestStore`·`ProblemResultStore` PG 구현 — ⓐ 해소. 저장 테이블 결정에 A 합의 필요
2. OpenAPI 정본화 범위·일정 — §1-①, AI 내부 합의 후 회신
3. job 취소 HTTP 노출 — ⓒ. 필요하시면 우선순위를 올리겠습니다

**⇒ 회신 요청:** 위 3건 중 adapter 착수를 막는 것이 있으면 알려 주십시오. 없으면 ⓐ부터
잡겠습니다.
