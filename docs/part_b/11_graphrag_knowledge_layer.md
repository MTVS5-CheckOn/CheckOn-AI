# [체크온] GraphRAG 지식 계층 규격 v1 — ContextPack · EvidencePack · GraphContextService

> **지위:** member-B(염준영) 공식 규격 v1. `src/ai/problem_generation/`의 근거 조립 계층과 `src/ai/diagnosis/`의 교육과정 그래프 projection의 사양 원본. 입력 초안은 `CODEXPROMPT/출제스튜디오_AI_GraphRAG_아키텍처_개발설계_v1.md`(작업·회의용 자료)이며, **본 문서가 `docs/part_b/` 정본이다.**
>
> **변경 이력**
> - v1.3 (2026-07-30): §0의 `contracts/graphrag.py`를 **A+B 양자 승인 완료**로 승격하고, PR #49의 `docs/02_ownership.md` v5 §3·§4·§5 및 `CLAUDE.md` §2 등록 완료(13곳)를 반영했다.
> - v1.2 (2026-07-30): §0의 `contracts/graphrag.py` 소유를 **당시 미승인 상태로 정정**([`09`](09_integration_proposals.md) §2-14 · `docs/02_ownership.md` 미등록 확인). §5에서 `quote_hash` 검증 주체를 A-5 resolver로 바로잡고 `license_ref` 유효성 주체를 `[미확정]`으로 명시 — 종전 표기는 `EvidencePathResult` 3필드에 근거가 없었다.
> - v1.1 (2026-07-30): A-5 초안에 맞춰 §5에 `EvidenceResolver.resolve`의 고정 시그니처·결과 구조를 편입하고, 그래프 내부 경로 검증·실제 근거 해소(A)·R-1 판정의 경계를 분리했다.
> - v1 (2026-07-27): 신규 작성. GraphRAG 채택 확정([`10`](10_m2_problem_generation_architecture.md) §1·§4.2)에 따라 설계 초안을 정본 규격으로 편입. 공용 계약을 건드리는 2건(`VersionSet` 3필드 · evidence resolver 시그니처)은 본 문서에서 확정하지 않고 [`09`](09_integration_proposals.md) §2-12 제안으로 분리했다.
>
> **한 줄 원칙:** GraphRAG는 **근거를 찾아 `ContextPack`을 만든다.** 판정은 게이트가, 승인은 강사가, 발행은 백엔드가 한다 — GraphRAG는 이 셋 중 어느 권한도 갖지 않는다.
>
> **참조** — [`01_pipeline.md`](01_pipeline.md) §3 · [`04_curriculum_graph.md`](04_curriculum_graph.md) · [`05_problem_generation.md`](05_problem_generation.md) §4·§8 · [`06_quality_gates.md`](06_quality_gates.md) §1·§3 · [`07_refine_policy.md`](07_refine_policy.md) §4 · `docs/policies/masking_redaction.md` · `docs/policies/langgraph_state.md` §2.4

---

## 0. 소유·승인 경계 — 무엇을 이 문서가 확정하는가

| 범위 | 확정 주체 | 위치 |
| --- | --- | --- |
| Graph 도메인 3종의 의미·검색 모드·색인 파이프라인·권리 매니페스트 | **B 단독** | 본 문서 |
| `contracts/graphrag.py`의 `ContextPack`·`EvidencePack`·`GraphContextService` 등 공용 타입 | **A+B 양자 승인 ✅** | `docs/02_ownership.md` v5 §3·§4·§5 · `CLAUDE.md` §2 — 등록 완료(13곳) |
| `contracts/execution.py` `VersionSet` GraphRAG 3필드 | **A+B 양자 승인** | [`09`](09_integration_proposals.md) §2-12-① `[제안]` |
| `evidence/models.py`의 `EvidenceRef` 스키마 | **A+B 양자 승인** | `docs/02_ownership.md` §3·§4·§5 |
| `evidence/resolver.py`의 resolver 시그니처·결과 타입·구현 | **A 단독** | [`09`](09_integration_proposals.md) §2-12-② `[A-5]` |
| Graph 저장 백엔드(벡터 DB·인덱스 인프라) | 서비스 인프라 — 본 문서 범위 밖 | — |

**미승인 항목에 의존하는 코드는 작성하지 않는다.** 승인 전에는 인터페이스와 FakeGraphContextService로 진행한다.

## 1. 왜 별도 계층인가 — 기존 evidence 체계와의 관계

[`05`](05_problem_generation.md) §4.2의 `EvidenceAnchor`는 **"문항이 무엇을 인용했는가"**만 담는다. 근거가 **어디서 왔고, 그 자료를 쓸 권리가 있으며, 왜 그 근거가 선택됐는가**는 담기지 않는다. T1(사전 표제어 ID 직접 조회)에서는 이 결핍이 드러나지 않지만 T2·T3에서는 세 가지가 동시에 무너진다.

1. **재현 불가** — 같은 요청에 다른 근거가 선택돼도 그 사실을 알 수 없다.
2. **권리 추적 불가** — 라이선스가 만료된 자료로 만든 문항을 역추적할 수 없다.
3. **근거 충분성 판정 불가** — "정답의 근거"와 "오답이 틀린 이유의 근거"를 구분할 수 없다.

GraphRAG 계층은 이 셋을 `EvidencePack`의 `retrieval`·`license_ref`·`coverage`로 각각 닫는다.

**기존 계약은 바꾸지 않는다.** `GeneratedItem.evidence`(`EvidenceAnchor` 튜플)는 그대로 유지하고, 각 anchor가 `EvidencePack`의 상세 항목을 가리키는 **최소 앵커** 역할을 한다. 공용 `EvidenceRef`가 구현되기 전에 기존 계약을 임의 변경하지 않는다.

## 2. Graph 도메인 3종 — 하나의 그래프가 아니다

세 도메인은 **버전 축이 서로 다르므로 분리**한다. 하나로 합치면 사전이 개정될 때 교육과정 버전까지 올라간다.

### 2.1 교육과정·약점 그래프 — 기존 `curriculum_graph.yaml`이 정본

[`04_curriculum_graph.md`](04_curriculum_graph.md)가 사양 원본이며 **결정론을 그대로 유지한다.** GraphRAG는 이 그래프를 새로 만들지 않고 **검색용으로 projection**할 뿐이다.

| 노드 | 의미 | 엣지 | 의미 |
| --- | --- | --- | --- |
| `SkillNode` | 세부 학습 능력 | `REQUIRES` | 선수 능력 |
| `SkillCell` | area×type 셀 | `BUILDS_ON` | 기능 누적 |
| `Concept` | 출제 개념 | `MAPS_TO` | 셀↔노드 연결 |
| `WeaknessMapSnapshot` | 특정 입력·버전의 약점 결과 | `TARGETS` | 약점 지도가 목표 지목 |
| | | `MEASURES` | 문항이 실제 측정하는 능력 |

**불변식:** ① 이 그래프의 정본은 YAML이며 GraphRAG projection은 파생물이다 ② `graph_version`은 이 그래프의 버전이다(§7 이름 규약) ③ `related` 엣지는 전파 제외 규칙([`04`](04_curriculum_graph.md) §5.4)을 그대로 따른다.

### 2.2 콘텐츠·근거 그래프 — GraphRAG 본체

| 노드 | 의미 |
| --- | --- |
| `SourceDocumentVersion` | **버전 고정** 문서 |
| `SourceChunk` | 검색 단위 |
| `Claim` | 출제에 사용할 주장 |
| `Entity` | 인물·기관·개념 |
| `DictionaryEntry` | 사전 표제어 (T1) |
| `GrammarRule` | 문법 규칙 (T1) |
| `LiteraryWorkSpan` | **허용된** 문학 발췌 (T3) |
| `LicenseGrant` | 사용·변형·전송 권리 |

| 엣지 | 의미 |
| --- | --- |
| `CONTAINS` | 문서 → 청크 |
| `ASSERTS` | 청크 → 주장 |
| `SUPPORTS` | 근거 → 주장 지지 |
| `CONTRADICTS` | 주장 간 충돌 |
| `DEFINES` | 사전·규칙 → 개념 정의 |
| `EXEMPLIFIES` | 예문 → 규칙 예시 |
| `DERIVED_FROM` | 출처 계보 |
| `LICENSED_BY` | 사용권 연결 |
| `VERSION_OF` | 자료 버전 계보 |

**불변식:** ① `LicenseGrant` 없는 `SourceDocumentVersion`은 색인 대상이 아니다 ② `SourceDocumentVersion`은 불변이며 개정은 새 버전 노드 + `VERSION_OF` 엣지로만 표현한다 ③ `CONTRADICTS`가 걸린 `Claim` 쌍은 같은 지문의 근거로 동시 사용하지 않는다.

### 2.3 문항·검증·리비전 그래프 — AI PostgreSQL의 projection

**AI PostgreSQL이 정본**이고([`02_design.md`](02_design.md) §2) 그래프는 검색용 사본이다. 그래프에 쓰고 DB에 안 쓰는 경로를 만들지 않는다.

| 노드 | 의미 | 엣지 | 의미 |
| --- | --- | --- | --- |
| `ProblemSet` | 출제 세트 | `TARGETS` | 목표 약점 연결 |
| `ProblemItemRevision` | 문항의 특정 리비전 | `GENERATED_FROM` | 사용한 ContextPack |
| `EvidenceAnchor` | 인용 근거 | `CITES` | 근거 인용 |
| `VerificationResult` | 특정 시도의 검증 결과 | `REVISES` · `REPLACED_BY` | 리비전·교체 계보 |
| | | `VERIFIED_BY` · `REJECTED_BY` | 검증·실패 게이트 |

### 2.4 개인정보 경계 (전 도메인 공통 · 불변식 3)

**학생 실명·연락처는 어떤 도메인에서도 노드가 될 수 없다.** 모든 노드와 조회는 `tenant_id`와 alias 경계를 강제한다. `student_ref`·`target_ref`는 alias이며, 그래프 저장소에 학습 기록 원본을 저장하지 않는다(스냅숏 입력으로만 통과).

## 3. GraphContextService — 단일 경유지

```python
class GraphContextService(Protocol):
    async def resolve_generation_context(...) -> ContextPack: ...
    async def resolve_revision_context(...) -> ContextPack: ...
    async def resolve_verification_context(...) -> ContextPack: ...
    async def resolve_replacement_context(...) -> ContextPack: ...
    async def verify_evidence_paths(...) -> EvidencePathResult: ...
```

**호출 규칙(불변식):**

1. **생성·수정·검증·교체는 반드시 이 서비스를 거친다** — `llm/gateway.py`가 모든 LLM 호출의 단일 경유지인 것과 같은 규율([`01`](01_pipeline.md) §5).
2. **query plan은 결정론 코드가 만든다** — 계획 전용 LLM 호출을 추가하지 않는다(불변식 1).
3. **검색 결과 0건이면 근거 없이 LLM을 호출하지 않는다** — §6 fail-closed.
4. **tenant·권리 필터는 검색 *후보* 단계에서 적용한다** — 검색 후 필터링은 유출 경로다.
5. **모든 검색은 정렬된 노드·경로 ID와 hash를 남긴다**(`retrieval_trace`).
6. **동점 score는 canonical ID로 정렬**해 재현성을 보장한다(불변식 8).
7. **검색·노드·경로·홉·시간·토큰 상한은 설정으로 주입한다** — 하드코딩 금지(`CLAUDE.md` §6), 무한 확장 금지(불변식 6).

## 4. ContextPack · EvidencePack

### 4.1 ContextPack

```yaml
context_pack_id: uuid
context_pack_schema_version: ctx-1
operation: generate | refine | verify | replace
tenant_id: tenant
target_source: weakness_auto | teacher_manual
weakness_map_id: uuid | null
target_skill_node_ids: [skill_id]
locked_fields:                    # 수정 턴에서 변경 불가 — 07 §2 "수정 중 불변"의 구조화
  target_ref: alias
  area_tag: language
  type_tags: [concept]
  skill_node_id: skill_id
  item_format: mcq
pedagogy_paths: [path_id]
evidence_pack_id: uuid
current_item_snapshot: object | null
redacted_instruction: string | null   # ★ redaction 통과분만
policy_constraints: object            # 금칙 사전 버전 등
retrieval_trace: object
context_pack_hash: sha256
```

- `context_pack_hash`는 **redaction 이후** canonical JSON의 SHA-256이다. redaction 전 원문으로 해시하면 마스킹 실패가 재현 키에 남는다.
- `locked_fields`는 [`07`](07_refine_policy.md) §2의 "수정 중 불변(대상 학생·약점 목표·area·type·skill_node)"을 데이터로 표현한 것이다. 수정 턴이 이 값을 바꾸려 하면 `out_of_scope`로 차단한다.

### 4.2 EvidencePack

```yaml
evidence_pack_id: uuid
evidence_pack_schema_version: evp-1
graph_snapshot_id: string
anchors:
  - anchor_id: string
    kind: passage_span | dict_entry | grammar_rule | work_span | source_claim
    ref: string
    source_id: string
    source_version: string
    source_content_hash: sha256
    chunk_id: string | null
    span_start: integer | null
    span_end: integer | null
    quote: string | null
    quote_hash: sha256 | null
    claim_id: string | null
    graph_path_edge_ids: [edge_id]
    license_ref: string
    rights_status: approved          # approved 외의 값은 anchors에 존재할 수 없다
coverage:
  - item_field: answer.correct_no
    supporting_anchor_ids: [anchor_id]
missing_requirements: []
retrieval:
  mode: reuse_only | local | hybrid | delta_retrieve
  query_hash: sha256
  returned_node_ids: [node_id]
  returned_path_ids: [path_id]
  scores: [0.0]
evidence_pack_hash: sha256
```

**`coverage`가 핵심이다.** "근거가 하나 있다"가 아니라 **문항의 어느 필드가 어느 앵커로 지지되는가**를 명시한다.

```text
정답                → anchor A
해설의 핵심 주장      → anchor A, B
2번 오답이 틀린 이유  → anchor C
목표 약점 정렬        → pedagogy path P
```

**불변식:** ① `anchors`에 `rights_status != approved` 항목이 존재하면 Pack 생성 자체가 실패한다 ② `missing_requirements`가 비어 있지 않으면 생성 단계로 진행하지 않는다 ③ `kind`는 [`05`](05_problem_generation.md) §4.2 `EvidenceKind` 4종의 상위집합이며, 추가된 `source_claim`은 T2 전용이다.

### 4.3 `EvidenceAnchor`(기존 계약) ↔ `EvidencePack` 대응

| `EvidenceAnchor` 필드 | `EvidencePack.anchors[]` 대응 | 비고 |
| --- | --- | --- |
| `kind` | `kind` | 동일 어휘 |
| `ref` | `anchor_id` 참조 → `ref` | 문항은 `anchor_id`로만 가리킨다 |
| `quote` | `quote` + `quote_hash` | R-1의 원문 일치 검사는 `quote_hash`로 수행 |
| — | `license_ref`·`rights_status` | 문항 계약에는 없고 Pack에만 존재 |
| — | `graph_path_edge_ids` | 선택 경로 재현용 |

**기존 `GeneratedItem.evidence` 계약은 무변경**이다([`05`](05_problem_generation.md) §4.2).

## 5. 게이트 연동 — R-1·R-4 확장

[`06`](06_quality_gates.md) §1의 결정론 검사를 대체하지 않고 **검사 재료를 확장**한다. 게이트 3단 구조·순서·재시도 예산은 그대로다.

R-1은 아래 두 검사를 한 타입으로 합치지 않는다. 그래프 경로 검증 결과와 실제 저장 근거 해소 결과는 필드명·의미가 겹치지 않으며, 게이트가 두 결과를 받아 최종 판정한다.

| 단계 | 소유·호출 | 검사 범위 | 반환 |
| --- | --- | --- | --- |
| 그래프 내부 검증 | **현행 B 소유** `[§2-14 승인 시 A+B 공용 계약]` · `GraphContextService.verify_evidence_paths(evidence_pack)` | 앵커 실존 · `graph_path_edge_ids` 유효성 | `EvidencePathResult(valid, checked_anchor_ids, invalid_anchor_ids)` — 해소 본문 없음 |
| 실제 근거 해소 | **A 단독** · `EvidenceResolver.resolve(...)` | 저장 근거의 `source_content_hash`·`quote`·`quote_hash` 대조 · `rights_status=approved` 이중 확인 | `ResolvedEvidence(evidence_pack_id, resolved, excluded)` |
| R-1 판정 | B 게이트 ① `RuleValidation` | 위 두 검사를 모두 통과했는지 판정. 한쪽 성공으로 다른 쪽 실패를 상쇄하지 않음 | 기존 `GateResult` |

**`license_ref` 유효성 검증 주체는 `[미확정]`이다.** `EvidencePathResult`에는 이를 표현하는 필드가 없고(`valid`·`checked_anchor_ids`·`invalid_anchor_ids` 3필드), A-5 resolver도 `rights_status` 이중 확인까지만 수행한다. `EvidencePack` 생성 단계에서 `rights_status != approved`가 이미 차단되므로(§4.2 불변식 ①) 만료 검출은 §8 재확인 단계에 의존한다 — 어느 검사가 `license_ref` 자체의 유효성을 담당하는지는 코드에 근거가 없으므로 여기서 확정하지 않는다.

`EvidenceResolver` 호출 계약은 A-5 초안과 같은 순서로 고정한다([`09`](09_integration_proposals.md) §2-12-②).

```python
@runtime_checkable
class EvidenceResolver(Protocol):
    async def resolve(
        self,
        *,
        pack: EvidencePack,
        anchor_ids: Sequence[str],
        tenant_id: str,
        owner_kind: EvidenceOwnerKind,
        owner_id: UUID,
    ) -> ResolvedEvidence: ...
```

- `ResolvedEvidence.resolved`는 최소 1건이며, 해소 0건은 `EvidenceResolutionFailed`로 fail-closed한다. 상위 워크플로는 `verification_unavailable`로 수렴시킨다.
- 일부만 해소되면 성공분은 `resolved`, 실패분과 사유는 `excluded`에 함께 남긴다. resolver가 pass를 결정하지 않고 R-1 게이트가 이 결과를 판정한다.
- `quote`는 실제 근거 대조 결과이므로 verifier 페이로드로 전달하지 않는다. `EvidencePack`·관련 GraphRAG 타입은 `ai.contracts.graphrag`에서 import하며 `evidence/`에서 재정의하지 않는다.

| 규칙 | 기존 | GraphRAG 확장 |
| --- | --- | --- |
| **R-1** 근거 실존 | anchor 해소 가능 + `quote` 원문 일치 | 위 그래프 내부 검증 + 실제 근거 해소가 모두 성공해야 함 |
| **R-4** 외부 지식 차단 | 근거가 지문/예문/발췌 내부에서 완결 | + `coverage`가 정답·해설 핵심 주장·오답 사유를 **전부** 덮는지 검사. `missing_requirements` 비어 있지 않으면 실패 |

**게이트 ②는 변경 없다.** `ContextPack`이 verifier 페이로드로 흘러들지 않으며(§6-4), blind 계약([`05`](05_problem_generation.md) §4.3)은 그대로다.

## 6. 보존 불변식 — GraphRAG가 약화할 수 없는 것

1. **판정·발행 권한 없음** — GraphRAG는 근거를 찾을 뿐이다. `pass|needs_review|reject` 판정은 결정론 코드가, 승인은 강사가, 노출은 백엔드가 한다([`06`](06_quality_gates.md) §0).
2. **fail-closed** — GraphRAG 장애·검색 0건·권리 만료 시 근거 없이 LLM을 호출하는 우회 경로를 만들지 않는다. `verification_unavailable`로 수렴하며 발행은 차단된다([`06`](06_quality_gates.md) §3).
3. **예산 불변** — 검색 실패를 위한 별도 재시도 루프를 만들지 않는다. 문항당 `item_attempt` 총 3회 공통 예산을 그대로 소모한다([`06`](06_quality_gates.md) §4 · FIX-06).
4. **blind 무오염** — `ContextPack`·`EvidencePack`이 verifier 페이로드에 정답·해설·근거를 유입시키지 않는다. 회귀는 [`08`](08_evaluation_plan.md) §7 프롬프트 스냅숏이 고정한다.
5. **권리 게이트** — `rights_status != approved` 자료는 저장 이후의 **색인·임베딩·LLM 전송·문항 생성** 어디에도 사용하지 않는다.
6. **LLM 쓰기 도구 금지** — `save_revision`·`publish`·`approve` 류 도구를 LLM에 주지 않는다(불변식 1).
7. **redaction 선행** — `ContextPack` 저장·해시 전에 redaction을 통과한다(`masking_redaction.md` §3 · `langgraph_state.md` §3.1).
8. **테넌트 격리** — 모든 검색 후보 단계에서 `tenant_id` 필터를 적용한다.

## 7. 버전·재현성 — `[제안 · A+B 승인 대기]`

**`graph_version`을 재사용하지 않는다.** 기존 `VersionSet.graph_version`은 **교육과정 DAG 버전**이며 A+B 승인·구현 완료 상태다(`d5283d0`).

| 필드 | 대상 | 상태 |
| --- | --- | --- |
| `graph_version` | 교육과정 DAG (§2.1) | ✅ 기존 확정 |
| `content_graph_version` | 콘텐츠 그래프 스키마 (§2.2) | `[제안]` [`09`](09_integration_proposals.md) §2-12-① |
| `graph_index_version` | 색인 스냅숏 | `[제안]` 〃 |
| `retrieval_config_version` | 검색 파라미터 시트 | `[제안]` 〃 |

승인 전에는 `EvidencePack.graph_snapshot_id`와 `ContextPack.retrieval_trace`에만 기록하고 `meta.versions`에는 싣지 않는다.

## 8. 수정 턴 연동 — 검색 모드 3종

| `retrieval_mode` | 사용 조건 | 예시 지시 |
| --- | --- | --- |
| `reuse_only` | 기존 근거의 의미가 바뀌지 않음 | "표현 자연스럽게", "해설 짧게" |
| `delta_retrieve` | 선지 의미·난이도·근거 표현 변경 | "오답을 더 그럴듯하게" |
| `full_retrieve` | 자료·지문·목표 변경 필요 | **v1 refine 불가** — `replace` 또는 Step 1~2 재출제 안내 |

**모든 수정 턴에서 Graph Context 단계를 실행한다.** `reuse_only`도 기존 `EvidencePack`의 **유효성·라이선스·버전을 다시 확인**한다 — 지난 턴 이후 라이선스가 만료됐을 수 있다. 이는 [`07`](07_refine_policy.md) §4의 "기존 검증 결과 재사용 금지"와 같은 논리다.

`full_retrieve`가 필요한 지시는 [`07`](07_refine_policy.md) §3의 `out_of_scope`로 차단하고 대안(교체·재출제)을 안내한다.

## 9. 색인 선행 파이프라인 · 권리 매니페스트

```text
외부 자료 발견
→ 권리·상업 이용·변형·LLM 전송 가능 여부 확인
→ rights_status=approved
→ 원본 저장·checksum·라이선스 스냅숏 고정
→ 정규화·청크·개체·관계·주장 추출
→ GraphRAG index 생성
→ 사람 승인 ✋
→ active graph snapshot 발급
```

`rights_status != approved`이면 **원본 저장 이후의 모든 경로를 차단**한다. 라이선스 만료 시 ① 신규 생성 차단 ② 검색 제외 ③ 인덱스 제거 ④ **파생 문항 추적**이 가능해야 한다(④가 §2.3 `GENERATED_FROM` 엣지의 존재 이유다).

### 9.1 권리 매니페스트 — B 소유 데이터 파일

```yaml
source_id:
source_version:
content_hash:
license_ref:
rights_holder:
commercial_use_allowed:
derivative_use_allowed:
llm_processing_allowed:
embedding_allowed:
student_distribution_allowed:
allowed_excerpt:
required_attribution:
expires_at:
revoked_at:
rights_status:            # approved 외에는 색인·전송 금지
```

데이터 파일 규칙(`02_ownership.md` §4-5)을 따른다 — PR 리뷰 대상이며 **골든 코퍼스 통과가 머지 조건**이다.

### 9.2 트랙별 1차 소스

| 트랙 | 1차 소스 | 사용 방식 |
| --- | --- | --- |
| T1 | 표준국어대사전 · 우리말샘 · 한국어기초사전 · 승인 말뭉치 | 완성 문제 수입이 아니라 **사실·규칙·예문 근거** |
| T2 | 상업·변형 이용 가능한 공공자료 · 계약 자료 · 강사 권리 자료 | 근거 기반 **신규** 지문·문항 생성 |
| T3 | 만료·기증·공유저작물 · 별도 계약 작품 | **허용 발췌 오프셋만**, 원문 변형 금지 |

**KICE·EBS 문제 PDF를 공개 문제은행으로 간주하거나 무단 크롤링하지 않는다**([`05`](05_problem_generation.md) §8.3).

## 10. 구현 선순위

| 순서 | 묶음 | 완료 조건 |
| --- | --- | --- |
| **P0-1** | Graph 도메인·버전 계약 | §2·§7 — 노드·엣지·버전 이름 동결 |
| **P0-2** | 권리 확인 콘텐츠 파이프라인 | §9 — `approved`만 색인·전송 |
| **P0-3** | `ContextPack`·`EvidencePack` 계약 | §4 — canonical hash·좌표·라이선스·검색 trace |
| **P0-4** | `GraphContextService` 인터페이스 | §3 — 생성·수정·검증·교체가 같은 인터페이스 사용 |
| P1-1 | T1 검색 어댑터 | 사전·규칙 ID 대조, 장애 시 fail-closed |
| P1-2 | 문제 생성 LangGraph | ContextPack → 생성 → 3단 게이트 → 체크포인트 |
| P1-3 | 문제 수정 LangGraph | 매 턴 Graph Context → 구조화 patch → 전체 재검증 |
| P1-4 | Graph 근거 검증 | §5 — R-1·R-4 확장 |
| P1-5 | 골든·실패 테스트 | tenant 격리 · 검색 0건 · 라이선스 만료 · timeout · hash 변조 · blind 누출 |

**P0-3·P0-4가 P1-2보다 앞선다.** 계약 없이 워크플로를 먼저 구현하면 전량 재작성이다.

**T1과의 관계:** T1의 R-1 대조는 사전 표제어 ID·문법 조항 ID **직접 조회**라 벡터 검색이 필수가 아니다. **T1 vertical slice는 검색 백엔드 없이 완주 가능**하며, 임베딩·재랭커의 실효는 T2·T3에서 나온다. 계약은 선행, 검색 백엔드는 후행이다([`10`](10_m2_problem_generation_architecture.md) §7.8).

## 11. OPEN 항목

| 번호 | 항목 | 담당 |
| --- | --- | --- |
| B-8ⓑ | `VersionSet` GraphRAG 3필드 확장 | A+B ([`09`](09_integration_proposals.md) §2-12-①) |
| B-7 확장 | evidence resolver 시그니처 — GraphRAG 경유 해소 병합 | A+B (〃 §2-12-②) |
| — | Graph 저장 백엔드 선정(벡터 DB·인덱스) — **계약 확정 후** | B+인프라 |
| — | `pedagogy_paths` 산출 규칙 — 교육과정 그래프 경로를 정렬 근거로 쓰는 방식 `[잠정]` | B |
| — | 임베딩·재랭커 모델·한국어 벤치마크 | B+기획 ([`10`](10_m2_problem_generation_architecture.md) §7.8) |
| C-15 `[P0]` | 외부 표절·유사도 대조 코퍼스 — 색인 권한 | B+기획 |

> **B-8 병합 근거:** ⓐ capability별 validator와 ⓑ GraphRAG 3필드는 모두 `contracts/execution.py`(양자) + `docs/04_api_contract.md` §2.2 + `docs/06_erd.md` AI_RUN 동시 개정이 필요해 PR 단위가 같다. A-5→B-7 병합과 대칭이다.
