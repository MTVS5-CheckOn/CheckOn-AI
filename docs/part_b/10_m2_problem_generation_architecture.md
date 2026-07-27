# [체크온] M2 문제생성 아키텍처 결정 기록

> **지위:** B 1차 결정(B-M2-01~05) 확정 · 후속 결정 수집 중. 이 문서는 두 M2 와이어프레임을 구현 계약으로 전환하기 위한 선행 결정 로그다.  
> **원칙:** B 단독 소유 범위만 이 문서에서 확정한다. 공통 계약·백엔드·제품 합의 항목은 제안으로만 기록하고 정본을 선행 변경하지 않는다.

## 1. 입력 정본

**제품 화면 (M2)**

- `CODEXPROMPT/M2_문제출제시스템_제품와이어프레임_v1.html`
- `CODEXPROMPT/M2_문제출제시스템_와이어프레임_협업설명_v1.html`

**GraphRAG 지식 계층 `[2026-07-27 추가 — 채택 확정]`**

- `CODEXPROMPT/출제스튜디오_AI_GraphRAG_아키텍처_개발설계_v1.md` — 개발 선순위 P0-1~P2-3·Graph 도메인 3종·`ContextPack`/`EvidencePack`·GraphContextService
- `CODEXPROMPT/출제스튜디오_FE_BE_AI_협업_와이어프레임_v1.html` — Step 2·3의 GraphRAG 단계와 FE·BE·AI 책임 분장

> 위 두 문서는 스스로를 "`CODEXPROMPT`의 작업·회의용 설계 자료"로 표기하며 **`docs/` 정본이 우선**임을 명시한다. → **[`11_graphrag_knowledge_layer.md`](11_graphrag_knowledge_layer.md)로 정본 편입 완료**(2026-07-27). 공용 계약을 건드리는 2건은 [`09`](09_integration_proposals.md) §2-12 제안으로 분리했다. 상세는 §4.2.

**공통 규율·B 정본**

- `docs/02_ownership.md`
- `docs/part_b/01_pipeline.md`
- `docs/part_b/05_problem_generation.md`
- `docs/part_b/06_quality_gates.md`
- `docs/part_b/07_refine_policy.md`
- `docs/policies/langgraph_state.md` §5

**외부 준비물 목록**

- `CODEXPROMPT/M2_API_LIST.md` — 외부 LLM·국어 자료·권리 매니페스트·골든셋·임베딩/재랭커 준비물 정의

## 1.1 필수 준수 원칙과 우선순위

M2 문제생성 설계·구현·리뷰는 다음 우선순위를 따른다.

1. `CLAUDE.md` 절대 불변식과 공통 정책·공통 계약
2. `docs/02_ownership.md`의 소유권·승인 경계
3. `docs/part_b/` 전체 정본의 요구사항
4. 두 M2 와이어프레임과 이 문서에서 새로 합의한 결정

적용 원칙:

- 사람에게 보여주는 이해 설명·진행 요약·질문·선택지·판단 근거·설계·계획·인계문은 모두 한국어로 작성한다. 파일 경로·코드 식별자·환경변수·API 필드만 원문 표기를 유지한다.
- `docs/part_b/`의 조건은 일부 문서만 골라 적용하지 않고 전체를 추적해 이행한다.
- `docs/part_b/` 요구를 구현한다는 이유로 공통 불변식·공통 계약·공통 정책을 약화하거나 우회하지 않는다.
- 와이어프레임 또는 신규 결정이 기존 `docs/part_b/` 정본과 다르면 정본을 조용히 덮어쓰지 않고 변경 안건으로 분리한다.
- `docs/part_b/`와 상위 공통 규약이 충돌하면 어느 한쪽을 임의 해석으로 무시하지 않는다. 충돌 파일·절·영향을 기록하고 양립 가능한 대안을 제시한 뒤 필요한 오너 승인을 기다린다.
- 공통 계약이나 양자 승인 파일 변경이 필요한 설계는 B 단독 결정으로 확정하지 않는다.
- 모든 최종 설계와 Codex 구현계획에는 `요구사항 → 설계 요소 → 변경 파일 → 테스트` 추적표를 포함한다.
- 구현 완료 판정에는 `docs/part_b/` 전수 준수 점검과 공통 불변식 회귀 점검이 모두 필요하다.

## 2. 결정권 경계

| 구분 | 이 문서에서 처리 | 예 |
| --- | --- | --- |
| **B 단독 결정** | 결정 후 B 정본과 구현 사양에 반영 | 문제생성 내부 알고리즘·프롬프트·게이트·재시도·조기중단·난이도 산식·revision workflow |
| **A+B 양자 승인** | 제안과 필요 변경만 기록 | 공통 LLM·WorkerJob·taxonomy 계약, 공용 DB 모델, 공통 API shell, 모델 fallback 패밀리 |
| **BE·제품·기획 합의** | 질문 목록과 권장안만 기록 | 공개 API·Kafka, 승인·노출, 반 단위 원본, 사용자에게 보이는 난이도 의미, 자료 공급·라이선스 |

## 3. 재논의하지 않는 확정값

| ID | 결정 |
| --- | --- |
| FIX-01 | v1 문항 형식은 5지선다 `mcq`만 지원한다. |
| FIX-02 | 세트 1건은 단일 measured area를 사용하고, 문제생성 워커는 문항을 순차 처리한다. |
| FIX-03 | Generator/Refiner와 Blind Verifier를 논리적으로 분리한다. |
| FIX-04 | Blind Verifier에는 정답·해설·근거를 전달하지 않는다. |
| FIX-05 | 규칙 검사와 ReleaseDecision은 결정론 코드가 수행한다. |
| FIX-06 | 문항당 `item_attempt` 총 3회만 허용하고 검사별 중첩 재시도 루프를 두지 않는다. |
| FIX-07 | `verification_unavailable`은 저장 가능한 정상 도메인 결과지만 발행은 차단한다. 수동 예외 승인은 허용하지 않는다. |
| FIX-08 | 일부 문항만 성공해도 `partial_success`로 정상 수렴하며 실패·미처리 수와 사유를 숨기지 않는다. |
| FIX-09 | AI 수정·직접 수정·롤백 후에는 문항 전체를 다시 검증한다. |
| FIX-10 | 수정은 `base_revision_no` 낙관적 잠금을 사용하며, 충돌 시 LLM을 호출하지 않는다. |

## 4. B 단독 결정 — 1차 확정 `[2026-07-27 확정]`

선택은 5건 모두 **A**다. 아래는 §8 반영 규칙에 따른 확정 기록이며, 각 결정에 딸린 조건은 §4.1에 둔다.

| ID | 확정 | 선택 이유 (1줄) | 기각한 대안 (1줄) | 영향 받는 B 정본 |
| --- | --- | --- | --- | --- |
| **B-M2-01** | 절대 난이도 `difficulty_est`와 학생 적합도 `difficulty_fit`을 **분리** | 보정 루프가 성립하려면 대조 대상이 문항 고유 속성이어야 실측 괴리를 문항 오차와 매칭 오차로 분해할 수 있다 | B(절대만)는 적합도 표현 근거가 없어 후속에 공용 ORM 재개봉 · C·D는 오차 원인 분해 불가로 [`01`](01_pipeline.md) §4 무력화 | [`02_design`](02_design.md) §2 · [`05`](05_problem_generation.md) §6 · [`09`](09_integration_proposals.md) §2-4.3 |
| **B-M2-02** | 파일럿 첫 2주는 스위치를 끄고, 이후 동일 문항의 총 3회 공통 예산 안에서 **난이도 사유 재생성 1회**, 재차 벗어나거나 예산이 없으면 `needs_review` | 허용 오차 상수를 실측 없이 지금 고정하지 않고 네 번째 생성 호출도 만들지 않는다 | C(오차 1단계 고정)는 근거 없는 상수를 선고정 · B는 요청 난이도를 no-op화 · D는 `set_drop_ratio_max` 오발동으로 KPI 위협 | [`05`](05_problem_generation.md) §6 · [`06`](06_quality_gates.md) §5·부록 |
| **B-M2-03** | 처리 3문항 이후 **폐기율 30% 초과 또는 검증 불능 3연속** | 현 기본값 유지 — 정본·골든셋 기대값 무변경 | 완화(B)·강화(C)는 시트+골든셋 동시 개정 비용만 발생 · D는 품질 사유 중단 상실 | 변경 없음 ([`06`](06_quality_gates.md) §6·부록) |
| **B-M2-04** | 초기 파일럿은 **전 문항 전체 검증**, 골든셋 충족 후 경량 개방 | 경량 모드의 근거인 R-1 사전 대조가 기준 자료(D-03) 미확보로 작동하지 않는다 — 대조 없는 경량화는 검증 부재와 같다 | B(현 시트값)는 R-1이 실제로 집행되는 상태를 전제 · C는 게이트 ② 정렬 판정까지 소멸시켜 [`01`](01_pipeline.md) §3 정렬 3층과 충돌 · D는 분기 임계가 파일럿 전 근거 없음 | [`06`](06_quality_gates.md) §1·부록(`t1_light_mode` **true → false**) |
| **B-M2-05** | 수동 목표 세트의 **첫 성공 문항만** `needs_review` | 현 기본값과 동일. 다만 "첫 문항"이 폐기되면 배지가 소실되므로 "첫 성공 문항"이 의도에 맞다 | B(전 문항)는 배지 KPI 오염 · C는 비개인화 확인 절차 상실 · D(20% 표본)는 결정론 표본 규칙이 별도로 필요 | [`06`](06_quality_gates.md) §5 문구 정정 |

### 4.1 확정에 딸린 조건 6건

| ID | 조건 | 근거 |
| --- | --- | --- |
| **C1** | `difficulty_fit`은 v1에서 **항상 null이며 산출·분기 코드를 만들지 않는다.** 활성화는 실제 익명 풀이 데이터 확보 후 `difficulty_calib` 버전 절차로 | [`04`](04_curriculum_graph.md) §1 — 문항 단위 실측은 B 출제분 제출부터 축적 · `CLAUDE.md` 불변식 2 |
| **C2** | `verify_config`에 `difficulty_regen_enabled`(파일럿 첫 2주 **false**)·`difficulty_regen_max=1`·`difficulty_band_tolerance`를 추가하고 임계를 코드에 두지 않는다. `item_attempt=3`이면 재생성 없이 `needs_review`다 | `CLAUDE.md` §6 하드코딩 금지 · [`06`](06_quality_gates.md) §4·부록 섀도 절차 |
| **C3** | **활성화 전 선결:** 난이도 사유 재생성본이 게이트 ①②에서 실패할 때 첫 검증 통과본을 보존할지 정해야 한다. 권고안은 첫 검증본을 fallback으로 보존하고 재생성 실패 시 그 문항을 `needs_review`로 확정하는 것이다. 재개 후에도 난이도 재생성 횟수와 fallback을 복원해야 하며, 이를 위한 카운터·후보 포인터가 `langgraph_state.md` §2.4 변경을 요구하면 A+B 안건으로 분리한다. 선결 전에는 `difficulty_regen_enabled=false`를 유지한다 | [`06`](06_quality_gates.md) §2 · [`07`](07_refine_policy.md) §4 · `docs/policies/langgraph_state.md` §2.4 |
| **C4** | 문서 기본값은 `t1_light_mode=false`로 확정한다. 실제 `verify_config`와 `golden/problems/` 파일을 만들 때 전체 검증 회귀를 함께 넣고, 이후 경량 모드 개방도 **골든 동시 개정**을 머지 조건으로 한다 | [`06`](06_quality_gates.md) 부록 · [`08`](08_evaluation_plan.md) §10·§12 버전 삼각 연동 |
| **C5** | [`06`](06_quality_gates.md) §5의 "수동 목표 세트 첫 문항"을 **"처리 순서상 게이트 ①②를 처음 통과해 ③에 도달한 문항"**으로 정정한다. 최종 status를 다시 조건으로 쓰는 순환 판정을 피한다 | B-M2-05 확정 |
| **C6** | B 내부 `ProblemRequest`에 **`requested_difficulty` 필드 뼈대가 필요**하다. 현재 계약에는 필드가 없지만 [`02_design`](02_design.md) §2의 `PROBLEM_SET.request` jsonb는 이미 "난이도"를 포함한다(확인된 계약↔ERD 불일치). 하·중·상 값과 band 경계는 §6의 공동 확정 전까지 비활성으로 두고, B 내부 결과는 `difficulty_est` 원값을 보존한다. 외부 응답의 `band`·`band_config_version`·표시 문구는 [`09`](09_integration_proposals.md)에 BE+B·제품/FE 제안으로 분리한다 | [`05`](05_problem_generation.md) §4.1·§6 · [`09`](09_integration_proposals.md) §2-4.3 · §6 |

### 4.2 GraphRAG 지식 계층 편입 `[2026-07-27 · 채택 확정 · B 정본 편입 완료 · 공용 2건 승인 대기]`

GraphRAG 채택은 확정이며 규격은 **[`11_graphrag_knowledge_layer.md`](11_graphrag_knowledge_layer.md)로 편입 완료**다. 아래 표의 B 단독 항목은 전부 반영했고, **A+B 2건만 승인 대기**다([`09`](09_integration_proposals.md) §2-12 · §1-9 A-4·A-5). 미승인 항목에 의존하는 코드는 작성하지 않는다 — 승인 전에는 인터페이스와 `FakeGraphContextService`로 진행한다.

**이것은 기능 추가가 아니라 개발 순서를 바꾸는 결정이다.** GraphRAG 설계서 §1이 `ContextPack`·`EvidencePack` 계약(P0-3)과 `GraphContextService` 인터페이스(P0-4)를 **문제 생성 LangGraph(P1-2)보다 앞**에 둔다. 계약 없이 워크플로를 먼저 구현하면 전량 재작성이 된다.

#### 편입 대상과 승인 주체

| 편입 대상 | 내용 | 승인 주체 |
| --- | --- | --- |
| `docs/part_b/` 신규 문서 | GraphRAG 지식 계층 규격 — Graph 도메인 3종, `ContextPack`/`EvidencePack`, GraphContextService, 검색 모드(`reuse_only`·`delta_retrieve`), 색인 선행 파이프라인 | **B 단독** |
| [`05`](05_problem_generation.md) §4.2 개정 | `EvidenceAnchor`가 `EvidencePack`의 path·quote·license와 어떻게 대응하는지 | **B 단독** |
| [`06`](06_quality_gates.md) §1 개정 | R-1·R-4가 Graph path·quote·license 검증을 포함하도록 확장(설계서 P1-4) | **B 단독** |
| [`07`](07_refine_policy.md) §4 개정 | 매 수정 턴에 `ResolveRevisionContext`가 선행하도록 처리 순서 갱신 | **B 단독** |
| `contracts/execution.py` `VersionSet` | `content_graph_version`·`graph_index_version`·`retrieval_config_version` **3종 추가** | **A+B 양자 승인** — [`09`](09_integration_proposals.md) §2에 신규 제안 필요 |
| `evidence/` resolver 시그니처 | GraphRAG 경유 근거 해소 — 기존 B-7 안건과 병합 | **A+B** (`evidence/` = A 소유) |
| 자료 권리 매니페스트 | `source_id`·`content_hash`·`license_ref`·`llm_processing_allowed`·`embedding_allowed`·`rights_status` 등 (`M2_API_LIST.md` §9.3) | **B 단독** (데이터 파일 규칙 — 골든 통과가 머지 조건) |

#### `[충돌 — 이름 재사용 금지]` `graph_version`

`contracts/execution.py`의 `VersionSet.graph_version`은 **교육과정 DAG 버전**이며 A+B 승인·구현 완료 상태다(`d5283d0`, [`09`](09_integration_proposals.md) §2-9). GraphRAG 설계서가 직접 경고한 대로 이 필드를 콘텐츠 그래프 버전으로 **재사용하지 않는다.** 콘텐츠 GraphRAG는 별도 3필드를 신설하며, 이는 양자 승인 12파일을 다시 여는 변경이므로 B 단독 확정 대상이 아니다.

#### 보존해야 하는 기존 불변식 (GraphRAG 도입이 약화할 수 없는 것)

1. **판정·발행 권한 없음** — GraphRAG는 근거를 찾을 뿐 게이트 판정과 발행에 관여하지 않는다([`06`](06_quality_gates.md) §0).
2. **fail-closed** — GraphRAG 장애·검색 0건 시 근거 없이 LLM만 호출하는 우회 경로를 만들지 않는다. `verification_unavailable` 경로로 수렴한다([`06`](06_quality_gates.md) §3).
3. **예산 불변** — 검색 실패를 위한 별도 재시도 루프를 만들지 않는다. 문항당 `item_attempt` 총 3회 공통 예산을 그대로 소모한다(FIX-06).
4. **blind 계약** — `ContextPack`이 verifier 페이로드에 정답·해설·근거를 유입시키지 않는다([`05`](05_problem_generation.md) §4.3 · [`08`](08_evaluation_plan.md) §7 스냅숏 회귀로 고정).
5. **권리 게이트** — `rights_status != approved` 자료는 저장·색인·임베딩·LLM 전송 어디에도 사용하지 않는다.
6. **LLM 쓰기 도구 금지** — `save_revision`·`publish`·`approve` 류 도구를 LLM에 주지 않는다(`CLAUDE.md` 불변식 1).

#### T1 vertical slice와의 관계

T1 어휘·문법의 R-1 대조는 **사전 표제어 ID·문법 조항 ID 직접 조회**라 벡터 검색이 필수가 아니다. 따라서 **T1 vertical slice는 GraphRAG 검색 없이도 완주 가능**하며, GraphRAG의 실효는 T2·T3(문서 코퍼스 근거 검색)에서 나온다. 다만 **계약(P0-3·P0-4)은 T1 구현 전에 확정**해야 워크플로 재작성을 피한다 — 계약 선행, 검색 백엔드는 후행이다.

## 5. B 단독 결정 — 2차 예정

- 약점 진단 집계 기간·최소 표본·상대 격차 기본값
- 동일 세트·최근 출제분 중복 억제 범위와 유사도 임계값
- 10문항 생성 성능 SLO와 전체 시간 상한
- `partial_success` 운영 허용률
- 생성 다양성과 재현성의 균형값
- 난이도 shadow 기간과 보정 데이터 최소 표본

## 6. B가 독단적으로 확정하지 않는 항목

| 항목 | 결정 주체 |
| --- | --- |
| 하·중·상이라는 사용자 표시의 최종 의미와 band 경계 | B+제품·FE |
| generator/verifier 실제 모델 패밀리와 fallback 조합 | A+B |
| 다중 목표·다중 measured area 세트 | A+B+제품 |
| 반 단위 출제의 구성원 기준시점·집계·alias 입력 | BE+제품+B |
| 공개 REST 경로·헤더·응답 envelope | BE+B, 공통 shell 변경 시 A+B |
| Kafka 토픽·파티션·보존·DLQ·`result_ref` 소비 | BE+A+B |
| B 전용 테이블의 공용 ORM 편입 | A+B |
| 승인된 문항 재수정 시 승인 철회·재검수 | BE+제품+B |
| 리비전 보존 기간·상한 | BE+B |
| T1/T2/T3 자료 공급처와 라이선스 | B+기획·BE |
| 지문 수정 허용과 공유 문항 연쇄 재검증 | B+제품 |

## 7. M2 AI 기능 고도화 외부 준비 레지스트리

이 절은 첫 vertical slice만이 아니라 M2 문제출제 AI 기능을 T1→T2→T3·GraphRAG·난이도 보정까지 고도화할 때, 사용자가 프로젝트 밖에서 신청·확보·승인·제공해야 하는 항목을 다룬다. PostgreSQL·Kafka·DNS·인증·배포·모니터링·SLA 같은 서비스 운영 준비는 제외한다.

### 7.1 외부 준비와 Codex 작업의 경계

- 사용자는 모델·API 접근권한, 허가된 원문·코퍼스, 실제 익명 데이터, 교과 전문가 검수 결과를 확보한다.
- Codex는 Settings·adapter·스키마·프롬프트·그래프·게이트·평가 도구와 fake를 구현한다.
- 실제 키 본문은 채팅·문서·Git에 붙이지 않고 커밋되지 않는 `.env`에 등록한다.
- API 키를 발급받았다는 사실만으로 콘텐츠의 상업 이용·변형·임베딩·LLM 전송·학생 배포 권리가 생기지는 않는다.
- 아래 API 후보를 모두 신청하지 않는다. 실제 채택한 공급자 키만 발급받는다.

### 7.2 전 트랙 공통 외부 프로세스

| ID | 외부 프로세스 | 사용자가 준비할 결과 | 필요한 기능 |
| --- | --- | --- | --- |
| M2-EXT-01 | Generator/Refiner 모델 접근 확보 | OpenAI 호환 endpoint, 정확한 model ID, API key 설정, 한국어 5지선다 structured output 성공 확인 | 최초 생성·강사 자연어 수정 |
| M2-EXT-02 | 독립 Blind Verifier 접근 확보 | Generator와 다른 모델 패밀리의 endpoint·model ID·key, blind 풀이·정렬 structured output 성공 확인 | 정답 유일성·풀이 가능성·목표 약점 정렬 |
| M2-EXT-03 | 적용 교육과정 정본 확보 | 지원 학년별 국어 교육과정·성취기준 원문, 고시·개정 버전, 파일 hash | `curriculum_graph.yaml`, 출제 목표와 성취기준 연결 |
| M2-EXT-04 | 국어 교과 전문가 검수 창구 확보 | 그래프 노드·엣지, 정답·해설·근거, 난이도, 경계 문항을 승인할 검수자 | 골든셋과 문항 품질의 사람 정답지 |
| M2-EXT-05 | 기능 입력 데이터 제공 | alias 학습 이벤트, 최근 출제분, 생성·수정 요청 샘플, 강사 승인·반려·수정 사유, 학생 풀이 결과 | 개인화·중복 억제·수정 품질·난이도 보정 |
| M2-EXT-06 | 자료별 권리 확인 | 원문마다 권리 매니페스트 작성·승인 | T1/T2/T3 색인·LLM 전송·학생 배포 허용 여부 |

2026년에 고2와 고3을 함께 지원하면 교육과정 전환 시점이 다르므로 2022 개정과 2015 개정 정본을 함께 관리해야 한다. 특정 학년만 지원하면 해당 정본만 준비한다.

권리 매니페스트 최소 필드:

```text
source_id
source_version
content_hash
license_ref
rights_holder
commercial_use_allowed
derivative_use_allowed
llm_processing_allowed
embedding_allowed
student_distribution_allowed
allowed_excerpt
required_attribution
expires_at
revoked_at
rights_status
```

### 7.3 T1 어휘·문법

#### 필수

1. **국립국어원 어문 규범 버전 고정본**
   - 한글 맞춤법, 표준어 규정, 표준 발음법
   - 범위 확장 시 외래어 표기법·국어의 로마자 표기법
   - 수집일·원문 URL·파일 hash·적용 버전
   - 내부 `grammar_rule` ID와 공식 조항의 대응표
2. **표준국어대사전**
   - 실시간 조회를 사용하면 Open API 인증키
   - 버전 고정 export·snapshot을 제공하면 API 키 없이 adapter 구현 가능
   - 표제어·대상 코드·뜻·발음·문법·용례를 `dict_entry` 근거로 사용
3. **전문가 검수 자료**
   - 문법 DAG 25~40노드와 `requires` 엣지
   - 각 노드의 area·type·성취기준·규칙 ID 매핑
   - 정상·경계·오답 가능 문항의 기대 정답·근거

#### 선택 보조 자료

- 우리말샘 API: 신규어·옛말·규범·용례 보조
- 한국어기초사전 API: 쉬운 뜻풀이·어휘 등급·용례 보조
- 한국어 어문 규범 용례 API: 외래어·로마자 표기 문항을 열 때만
- 모두의 말뭉치: 예문 다양성·어휘 빈도·문체 분석을 고도화할 때

보조 사전·말뭉치를 단독 정답 정본으로 사용하지 않는다. 어떤 자료가 최종 정답 근거인지 우선순위를 매니페스트에 명시한다.

### 7.4 T2 사실 기반 비문학

T2 개방 전에 사용자가 준비할 것은 범용 웹 검색키가 아니라 **승인된 사실 자료 카탈로그**다.

- 허용 도메인: 인문·사회·과학·기술·예술·융합
- 도메인별 승인 기관·출처 목록
- 원문 파일 또는 API 응답 스냅숏
- 게시일·판본·수집일·hash
- 문장·표·통계·주장 단위 근거 위치
- 상업 이용·변형·LLM 전송·임베딩·학생 배포 허용 여부
- 갱신·폐기·정정 기준
- 정답·해설·근거가 검수된 T2 골든 문항

통계 소재가 필요하면 KOSIS Open API, 기관별 공공자료가 필요하면 공공데이터포털 `serviceKey`를 후보로 검토한다. 각 데이터셋의 이용 조건을 개별 확인하며, `serviceKey` 하나를 모든 공공자료의 포괄 사용권으로 취급하지 않는다. 실시간 웹 검색은 재현성과 권리 추적이 약하므로 기본 근거원으로 사용하지 않는다.

### 7.5 T3 문학

T3 개방 전에 작품별로 다음 묶음을 확보한다.

- 작품 원문과 안정적인 작품 ID
- 저자·정확한 판본·편집자
- 번역·현대어 풀이가 있으면 해당 작성자와 별도 버전
- 원작과 판본 각각의 라이선스 근거
- 허용된 발췌 시작·종료 오프셋
- 출처표시 문구
- 만료·회수 조건
- 작품별 갈래·시대·개념어 메타
- 국어 교사·문학 전문가가 검수한 정상·경계·복수 해석 문항

공유마당 API는 후보 작품 검색에 사용할 수 있다. API 키나 검색 결과만으로 개별 작품의 변형·상업 이용 권리가 증명되지는 않으므로 작품별 라이선스를 별도로 확인한다.

### 7.6 난이도 고도화

별도의 난이도 판정 API는 준비하지 않는다. 사용자가 제공해야 하는 것은 다음 데이터다.

1. **교사 난이도 라벨**
   - 하·중·상
   - 라벨 근거
   - 학년·과목·선택과목
   - 요구 풀이 단계, 추론 깊이, 근거 수, 오답 매력도
2. **익명 풀이 결과**

```text
item_id
revision_no
anonymous_learner_id
grade_or_ability_band
selected_choice
is_correct
response_time_ms
skipped
attempt_no
answered_at
```

초기에는 결정론 산식과 교사 라벨을 사용하고, 실제 응답이 쌓인 뒤 정답률·변별도·응답 시간을 보정 데이터로 사용한다. Rasch·IRT를 도입할 때만 심리측정 검토와 표본 설계를 별도 준비한다.

### 7.7 중복·표절 방지

#### 내부 중복

백엔드 또는 기존 문항은행에서 다음 데이터를 제공한다.

- 최근 생성·발행·삭제 문항의 stem·선지·지문
- 문항 revision, `skill_node_id`, `source_ref`
- 대상 alias 또는 비교 범위
- 생성·발행 시각과 fingerprint

이 데이터가 없으면 동일 세트 안의 중복만 막을 수 있고 과거 출제분 재출제를 막을 수 없다.

#### 외부 표절

아래 둘 중 하나를 준비한다.

1. 합법적으로 비교·색인할 수 있는 자사 문항은행·기출·교재 코퍼스
2. 외부 유사도·표절 API 계약과 접속정보

추가로 `exact_duplicate`, `near_duplicate`, `same_template`, `same_passage_different_question`, `legitimate_similarity`, `unrelated`가 라벨된 문항 쌍이 필요하다. KICE·EBS·시중 교재를 허가 없이 크롤링하거나 임베딩하지 않는다.

### 7.8 GraphRAG·검색 고도화 준비 `[2026-07-27 — 채택 확정]`

**GraphRAG 채택은 확정이다**(§1 입력 정본 · §4.2). 다만 `CODEXPROMPT/출제스튜디오_AI_GraphRAG_아키텍처_개발설계_v1.md`는 아직 **정본 계약이 아니며 `docs/part_b/` 편입이 완료되지 않았다** — 편입 대상·승인 주체·보존 불변식은 §4.2에 있다. 준비 항목은 다음과 같다.

> **준비 시점 주의:** 아래 임베딩·재랭커는 **검색 백엔드**이므로 계약(설계서 P0-3 `ContextPack`/`EvidencePack` · P0-4 `GraphContextService`)보다 **뒤**에 확보해도 된다. T1 vertical slice는 사전·규범 ID 직접 조회라 벡터 검색 없이 완주 가능하고, 임베딩의 실효는 T2·T3에서 나온다. 설계서 §1이 금지한 "GraphRAG 계약 없이 벡터 DB부터 설치하는 일"에 해당하지 않도록 순서를 지킨다.

- 임베딩 모델 endpoint·key·정확한 model revision 또는 승인된 오픈 모델 가중치
- 재랭커 endpoint·key·model revision 또는 승인된 오픈 모델 가중치
- gated 모델 사용 시 모델 저장소 접근 토큰
- 자료별 임베딩 허용 권리
- 검색 query→정답 근거·hard negative·evidence coverage 전문가 라벨
- 콘텐츠·index·retrieval·embedding·reranker 버전 정보

벡터 DB 계정은 서비스 인프라이므로 이 외부 기능 준비 목록에 포함하지 않는다.

### 7.9 외부 설정 레지스트리

현재 코드가 실제로 읽는 설정:

```dotenv
LLM_PROVIDER=openai_compat
LOCAL_LLM_BASE_URL=
LOCAL_LLM_API_KEY=
LOCAL_LLM_MODEL=
LOCAL_LLM_TIMEOUT_S=
LOCAL_LLM_DISABLE_THINKING=
```

M2 역할 분리 시 추가할 후보 이름. 아직 코드는 읽지 않으며 공통 LLM 오너 합의가 필요하다.

```dotenv
VERIFIER_LLM_BASE_URL=
VERIFIER_LLM_API_KEY=
VERIFIER_LLM_MODEL=
VERIFIER_LLM_TIMEOUT_S=
```

채택한 외부 자료·고도화 기능에만 추가할 후보 이름:

```dotenv
M2_T1_STDICT_API_KEY=
M2_T1_OPENDICT_API_KEY=
M2_T1_KRDICT_API_KEY=
M2_T1_KORNORMS_API_KEY=
M2_T1_CORPUS_API_KEY=

M2_T2_KOSIS_API_KEY=
M2_T2_DATA_GO_KR_SERVICE_KEY=

M2_T3_GONGU_API_KEY=

M2_SIMILARITY_PROVIDER=
M2_SIMILARITY_BASE_URL=
M2_SIMILARITY_API_KEY=

M2_EMBEDDING_BASE_URL=
M2_EMBEDDING_API_KEY=
M2_EMBEDDING_MODEL=
M2_RERANKER_BASE_URL=
M2_RERANKER_API_KEY=
M2_RERANKER_MODEL=
HF_TOKEN=
```

후보 변수명은 Settings 구현 전에 확정한다. 실제로 선택하지 않은 공급자의 빈 변수를 미리 만들지 않는다.

### 7.10 `.env`에 넣지 않는 것

- 교육과정·그래프·taxonomy 버전
- 자료 버전·hash·라이선스·권리 상태
- 난이도 가중치·게이트 임계값·검색 상한
- 금칙·문법 규칙
- 골든셋 기대값
- embedding·reranker·retrieval config version

비밀·접속정보만 `.env`에 두고, 규칙과 버전은 YAML·버전 레지스트리·DB 정본으로 관리한다.

### 7.11 사용자 외부 준비 대상에서 제외

- PostgreSQL·checkpoint DB
- Kafka·DLQ·SSE
- DNS·TLS·방화벽·mTLS·JWT
- LangSmith·모니터링
- 배포 계정·운영 SLA·비용 알림
- F17 OCR·PDF 조판
- `short`·`essay` 루브릭과 채점 데이터
- 별도 대화용 세 번째 LLM

마지막 세 항목은 현재 M2 고등 국어 5지선다 기능 범위 밖이거나 Generator/Refiner가 이미 담당한다.

## 8. 답변 반영 규칙

1. 선택 답변을 이 문서의 결정 표로 승격한다.
2. 선택 이유와 기각한 대안을 한 줄씩 남긴다.
3. 결정에 따라 영향을 받는 B 정본 문서를 연결한다.
4. 양자·백엔드 합의가 필요한 파생 변경은 별도 안건으로 등록한다.
5. 결정되지 않은 값을 코드에 하드코딩하지 않는다.
