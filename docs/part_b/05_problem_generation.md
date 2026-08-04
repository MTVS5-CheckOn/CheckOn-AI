# [체크온] 문항 생성 규격서 v1 — 요청·지문·문항 계약 · 출처·저작권 · 보안 규격

> **지위:** member-B(염준영) 공식 규격 v1. `src/ai/llm/prompts/templates/problem_generation/`(passage · items · cross_solve)의 사양 원본이며 `contracts/problem_generation.py` 타입 정의 근거. **v1 생성 범위: `mcq`만(7/15 확정)** — `short`·`essay`는 enum 예약(§9).
>
> **변경 이력**
> - v1.5 (2026-08-04): **생성 트랙 3종 → 5종 재정의** — 2027학년도 6월 모평 실측 기준으로 수능 과목 구조와 1:1 정렬했다. ① 어휘를 T1에서 **T2로 이관**(9·17번이 독서 지문의 밑줄 어휘를 묻는 지문 종속 유형이라 "지문 없음" 트랙에 둘 수 없다) ② 화법·작문·매체를 **T4·T5로 승격**(자체 생성이라 착수 장벽이 T3보다 낮다) ③ 착수 순서를 **게이트 성숙도와 커버리지로 분리 표기**(T1 우선은 기술 순서이지 수요가 아니다). §1.0에 자료 조달 3분류(생성·대조·저작물)와 LLM 생성의 경계를 신설하고, 웹 스크래핑 금지·T2에 만료 저작물 부적합을 명문화했다. §1.1은 대조 트랙 전체로 범위를 넓히고 `grammar_rule` ID를 어문 규범 조항 번호로 확정했다. `contracts/taxonomy.py`의 `area_tag` enum은 변경하지 않는다.
> - v1.2 (2026-07-27): B-M2-01·02 확정 반영 — `difficulty_est`와 `difficulty_fit` 책임을 분리하고, 난이도 추정을 ReleaseDecision 앞으로 이동해 요청 난이도 불일치 분기를 결정론 코드가 판정하도록 명문화. **C6**: B 내부 `ProblemRequest.requested_difficulty` 뼈대 신설(§4.1 — band 값·경계는 B 단독 확정 불가, 공개 응답 노출은 BE+B 합의). **GraphRAG 편입**: `EvidenceAnchor` ↔ `EvidencePack` 대응표 추가(§4.2 — 기존 계약 무변경, [`11`](11_graphrag_knowledge_layer.md) §4.3).
> - v1.1 (2026-07-15): 파일 번호 이동(03→05) + 7/15·대화 결정 반영 — ① mcq만(short 정규화 규칙은 예약으로 강등) ② `target_source`(수동 목표 출제) ③ SolveResult에 약점 정렬 판정 필드 ④ meta.quota 폐기 ⑤ 재시도 총 3회(item_attempt) ⑥ 서술형(구 B-4) 폐기. 상호 링크 재편.
> - v1 (2026-07-15): 입력 초안 `CODEXPROMPT/(염준영)_문항_생성_규격서_v0.md` 정리 — 공용 enum 강제·전체 스키마·멱등/버전 필드·injection 방어·T1 자료 요건·사실성 fail-closed.
>
> **원칙 3줄:** ① 비문학 지문은 자체 생성 — 기출·타사 지문 모사 금지(저작권 경계) ② 문학은 공유 저작물 풀 선택만 — LLM이 작품을 만들거나 변형하지 않는다 ③ 모든 문항은 구조화 출력 + rationale 근거 인용 강제 — 근거 없는 문항은 게이트 이전 파싱 단계에서 탈락.
>
> **참조** — [`01_pipeline.md`](01_pipeline.md) §3 · [`04_curriculum_graph.md`](04_curriculum_graph.md) §7 · [`06_quality_gates.md`](06_quality_gates.md) · `docs/policies/taxonomy.md` · `docs/policies/masking_redaction.md`(출제 프롬프트도 전 경로 적용)

---

## 1. 생성 트랙 5종 — 수능 과목 구조와 1:1

> **`[2026-08-04 개정]`** 종전 3종(T1 어휘·문법 / T2 비문학 / T3 문학)을 **수능 과목 구조에 맞춰 5종으로 재정의**했다. 근거는 2027학년도 6월 모의평가 실측이며, 바뀐 것은 세 가지다 — ① **어휘를 T1에서 T2로 이관**(어휘 문항은 독서 지문의 밑줄 어휘를 묻는 지문 종속 유형이라 "지문 없음" 트랙에 둘 수 없다) ② **화법·작문·매체를 트랙으로 승격**(종전 "미개방"이었으나 자료를 자체 생성하므로 착수 장벽이 T3보다 낮다) ③ **착수 순서의 근거를 커버리지와 분리 표기**(T1 우선은 게이트를 먼저 완성하기 위한 기술 순서이지 수요 우선순위가 아니다). `contracts/taxonomy.py`의 `area_tag` enum은 변경하지 않는다.

| 트랙 | 과목 | `area_tag` | 자료 조달 | 6월 모평 문항 | 검증 가능성 |
| --- | --- | --- | --- | --- | --- |
| **T1. 문법** | 선택 「언어와 매체」 | `language` | **대조** — 어문 규범 | 5 (35~39) | ★★★ 조항 대조로 기계 검증 |
| **T2. 독서** | **공통** | `reading` | **생성** | 17 (1~17) | ★★ 근거 실존 기계 검증 + 교차 풀이 · 사실성은 §2.1 |
| **T3. 문학** | **공통** | `literature` | **저작물** — 공유 풀·BE 라이선스 | 17 (18~34) | ★ 작품 해석 개입 — 교차 풀이 + 검토 배지 강화 |
| **T4. 화법·작문** | 선택 「화법과 작문」 | `speech` `writing` | **생성** | 11 (35~45) | ★ 담화 규범·수사 판단 |
| **T5. 매체** | 선택 「언어와 매체」 | `media` | **생성** | 6 (40~45) | ★ 매체 관습 판단 |

**공통 34문항은 전원이 응시하고 선택 11문항은 택 1이다.** T2·T3가 커버리지의 76%이며, T1은 5문항·선택자 한정이다.

**어휘는 T2의 문항 유형이다** — 6월 모평 9번(`문맥상 ⓐ와 가장 가까운 의미`)·17번(`문맥상 ⓐ~ⓔ와 바꿔 쓰기`)이 각각 독서 지문 `[4~9]`·`[14~17]` 안에 있다. 매회 2문항 고정이며, 지문과 같은 자료 묶음 키(`passage_ref` — [`12`](12_suneung_format_alignment.md) §5)를 공유한다. 다만 **판정은 사전 대조**이므로 자료 요건은 §1.1을 따른다.

**복합 자료는 전 트랙에 걸린다** — `[4~9]`(독서 2지문·6문항)·`[22~27]`(문학 복합·6문항)·`[38~42]`(대화+초고)·`[40~43]`(라디오+글)·`[44~45]`(화상회의+자료). `FMT-2 공유 문항군`은 트랙별 안건이 아니라 **T2 착수 전에 닫아야 하는 공통 기반**이다.

### 1.0 자료 조달 3분류 — LLM 생성의 경계

| 분류 | 트랙 | 규칙 |
| --- | --- | --- |
| **생성** | T2 · T4 · T5 | **LLM이 자료를 만든다.** 정답 근거는 그 자료 내부에서만 성립하며 R-4가 집행한다. 외부 자료가 필요 없다 — 결함이 아니라 설계다 |
| **대조** | T1 · T2의 어휘 문항 | **외부 정본 필수.** 자료가 없으면 발행 차단이며 **LLM으로 대체하지 않는다** — 대조할 정본이 없으면 "그럴듯한 정답"의 진위를 확인할 수단이 없다 |
| **저작물** | T3 | API 수급 + BE 라이선스. **작품 텍스트 변형 절대 금지**(§3) — LLM이 원문을 생성하면 수능 문학이 아니다 |

⚠ **어떤 트랙에서도 웹 스크래핑을 하지 않는다.** 버전 없는 수집물은 `EvidenceAnchor.ref`와 묶을 버전이 없어 재현성(불변식 8)을 깬다. 저작권 만료 자료도 정식 개방 경로로 받는다.

⚠ **T2에 만료 저작물을 쓰지 않는다.** 보호기간이 사후 70년이라 대상은 1956년 이전 사망 저작자인데, 6월 모평 독서 지문(조선 노비의 재산권·완전경쟁시장의 정보 획득·구형과 표면장력)에 대응하는 원본이 그 범위에 존재하지 않는다. **독서는 자체 생성이 정본 경로다.**

### 1.1 대조 트랙 기준 자료 요건 `[OPEN — D-03]`

R-1 대조(**문법 규칙 ID 실존** · **어휘 표제어 실존**)의 기준 데이터 소스 요건:
- **공급처·버전·라이선스가 명확**할 것(버전 없는 웹 스크랩 금지) — 후보 확정은 D-03(B+기획).
- 자료 버전은 `EvidenceAnchor.ref`의 ID 체계와 함께 고정 — 자료 개정 시 골든셋 동시 개정.
- **`grammar_rule` ID는 별도 체계를 만들지 않는다** — 어문 규범의 조항 번호를 그대로 쓴다(`어문규범:{고시연도}:{규범명}:{조항}`). 규정 정보 데이터가 **항별연혁정보**를 포함하므로 개정 이력이 원본에서 따라온다.
- **장애 시 처리:** 자료 조회 불가 상태에서는 R-1 대조가 불가능하므로 **발행 차단**(`verification_unavailable` — [`06_quality_gates.md`](06_quality_gates.md) §3). 자료 없이 "그럴듯한 정답"으로 통과시키지 않는다. **이 차단은 T1 전체와 T2의 어휘 문항에 적용된다** — T2의 나머지 문항은 생성 트랙이므로 영향받지 않는다.

### 1.2 착수 순서 — 두 축을 분리한다

**게이트 성숙도 순서(기술)** — 게이트를 T1에서 완성하고 그대로 물려받는다. 게이트 미완성 상태로 다음 트랙을 열지 않는다.

```
T1 문법  →  T2 독서  →  T3 문학 · T4 화법·작문 · T5 매체
```

**자료 조달 순서(병렬)** — 게이트와 독립이며 지금 진행할 수 있다.

| 시점 | 작업 | 여는 것 |
| --- | --- | --- |
| 지금 | 어문 규범 확보 | T1 |
| 지금 | 사전 확보 | T2 어휘 문항 |
| 불필요 | — | T2 본문 · T4 · T5 (자체 생성) |
| 장기 | 작품 풀 권리 취득 | T3 |

**T4·T5가 T3보다 먼저 열릴 수 있다** — 자료 대기가 없으므로 게이트만 확보되면 착수 가능하다. 다만 검증 난이도는 T3와 비슷하다(담화 규범·매체 관습은 기계 검증이 어렵다) — 자료가 쉬운 것과 게이트가 쉬운 것은 다르다.

전 트랙 v1 문항 형식은 **mcq(5지선다)**.

## 2. 지문 생성 — T2 파라미터 (passage 템플릿 입력)

```python
class PassageRequest(BaseModel):
    area_tag: Literal[AreaTag.READING]      # 자체 생성은 비문학만
    domain: Literal["humanities","social","science","tech","art","fusion"]
                                            # taxonomy 하위분류(reading.*) 예약과 정렬 — enum 승격 전 자체 관리
    topic_hint: str | None                  # 강사 지정 주제 — §8.2 injection 방어 통과 필수
    word_count: int                         # 어절 목표 (허용 오차 ±10%)
    sentence_complexity: Literal["basic","standard","advanced"]
    paragraph_count: int = Field(ge=2, le=6)
    banned_topics_version: str              # §8.1 금칙 사전 버전
```

어절·복잡도 목표는 생성 후 **코드가 실측 검증**(±10% 초과 시 재생성 1회 — item_attempt 소모 — 소진 시 실측값 저장). LLM의 자기 보고는 신뢰하지 않는다.

### 2.1 지문 사실성 원칙 `[P0 — OPEN D-04]`

비문학 지문은 "그럴듯한 설명문"이지 검증된 사실 출처가 아니다. 방어 3층:

1. **생성 제약(1차):** 실존 인물·기관·통계의 구체 수치 인용 금지(가상 연구·일반론으로 서술) + 정답 근거는 **지문 내부에서만** 성립(R-4가 집행).
2. **승인 근거 자료 기반 생성(목표 상태):** 사실 서술 지문은 승인 자료를 입력으로 고정하고 `source_ref` 저장 — 자료 체계 확정은 D-04(B+기획).
3. **fail-closed:** 사실검증 수단이 없는 사실 서술형 지문은 `needs_review`가 아니라 **발행 차단(`source_unverified`)** — 상태 추가는 공용 제안([`09_integration_proposals.md`](09_integration_proposals.md) §2-2).

## 3. 문학 풀 — T3 선택 규칙

- 풀 원본(작품 원문·라이선스)은 백엔드 소유 — AI PG는 `PASSAGE.source_ref`·**`license_ref`(라이선스·출처·작품 버전)** 참조만([`02_design.md`](02_design.md) §2).
- 선택 로직은 결정론: 요청 조건(갈래·시대·빈출 개념어)과 풀 메타 매칭 — LLM은 발췌 범위 안에서 문항만 생성.
- **작품 텍스트 변형 절대 금지** — 발췌는 오프셋 범위 지정으로만, 고전 현대어 풀이는 풀 등재 풀이본만(버전·license_ref 저장).

## 4. 계약 스키마 — contracts/problem_generation.py (B 단독)

**enum 규율:** `area_tag`·`type_tag`·`item_format`은 `contracts/taxonomy.py` 공용 enum을 **직접 사용**한다(6영역·B-3 경계 사례 확정). 문자열 자유 입력 금지. `item_format`은 v1에서 `mcq` 고정 — short·essay 분기 코드 작성 금지(CLAUDE.md §3).

### 4.1 워크플로 내부 요청 command

```python
class ProblemRequest(BaseModel):
    request_id: str                      # X-Request-Id 상호 추적
    idempotency_key: str                 # 중복 생성 방지 (04_api_contract §2.1)
    tenant_id: str
    target_kind: Literal["student", "class"]
    target_ref: str                      # alias 논리 참조
    target_source: Literal["weakness_auto", "teacher_manual"]
                                         # manual = 강사 수동 목표(확정) — 진단 생략, 비개인화 표기 필수
    weakness_map_id: UUID | None         # auto: 미지정 시 최신본 동결 · manual: None 강제
    manual_targets: list[str] | None     # manual: 강사 선택 노드/셀 ID (1..*)
    snapshot_hash: str
    taxonomy_version: str
    area_tag: AreaTag
    type_tags: list[TypeTag]
    item_format: ItemFormat              # SUPPORTED_ITEM_FORMATS(={MCQ}) 검증 — taxonomy.py의 v1 예약값 규약
    count: int = Field(ge=1, le=20)
    requested_difficulty: DifficultyBand | None        # 내부 band 값 — None=무지정(현행 동작 보존)
                                         # C6 — 값·경계는 B+제품·FE 확정 전 비활성(10 §6)
    target: Literal["cell", "node", "auto"] = "auto"   # 04 문서 §7
    passage: PassageRequest | None       # T2만
    topic_hint: str | None               # §8.2 통과 필수
```

`ProblemRequest`는 HTTP body DTO가 아니라 공통 헤더와 body를 조립한 **내부 command**다. 외부 `POST /problem-sets` body DTO에는 `request_id`·`idempotency_key`·`tenant_id`를 두지 않고, `X-Request-Id`·`Idempotency-Key`·`X-Tenant-Id`를 단일 원천으로 읽어 이 command에 매핑한다([`09`](09_integration_proposals.md) §2-1 B 확정).

**결과 메타:** `target_source=teacher_manual` 세트는 응답과 저장에 `personalized=false`를 명시 — 화면 "약점 데이터 기반 개인화 아님" 표기의 근거 필드.

**요청 난이도 계약 `[C6 · B-M2-01·02 확정]`:** `requested_difficulty`는 **생성 프롬프트 파라미터**이며 게이트 판정의 1차 근거가 아니다. `DifficultyBand` 타입은 B 내부 계약에 두되 **허용 값과 경계값(1~5 → 하·중·상 매핑)은 B 단독 확정 대상이 아니다**([`10`](10_m2_problem_generation_architecture.md) §6 — B+제품·FE). 공동 확정 전에는 `None`만 허용하는 비활성 계약으로 취급한다. B 내부 결과는 `difficulty_est` 원값을 항상 보존하고, 파생 `band`·`band_config_version` 및 화면 문구의 공개 REST 노출은 [`09`](09_integration_proposals.md)의 BE+B·제품/FE 합의로 분리한다. 종전 계약에는 난이도 필드가 없었으나 [`02_design`](02_design.md) §2의 `PROBLEM_SET.request` jsonb는 이미 "난이도"를 포함한다고 기술해 왔다 — 본 필드 뼈대로 그 불일치를 해소한다.

**v1 단일 영역 제한:** `ProblemRequest.area_tag`는 세트 전체의 measured area 하나다. 모든 `GeneratedItem.area_tag`는 요청값을 에코해야 하며, 서로 다른 measured area를 한 세트에서 생성하지 않는다. source/passage 소재 영역은 이 필드와 별도다. 혼합 지문의 문항별 태깅은 가능하지만, 화법+작문 등 혼합영역 자동 세트 생성은 후속 다중 목표 계약 전까지 지원하지 않는다([`taxonomy`](../policies/taxonomy.md) §2.1).

**taxonomy_version 정본 규칙:** 실행 단위의 정본은 `ExecutionContext.versions.taxonomy_version`이다. `ProblemRequest.taxonomy_version`은 요청 대상 약점 지도·실행 버전 확인용 **에코**이고, `WeaknessMap.taxonomy_version`([`04`](04_curriculum_graph.md) §5.5)과 함께 **세 값이 해당 실행에서 반드시 같아야 한다.** 값이 다르면 LLM 호출·문항 생성을 시작하지 않는다 — 워크플로 입력 조립 단계에서 결정론적으로 거부(임의 보정·최신 버전 자동 변환 금지). 강제 주체는 개별 Pydantic 모델이 아니라 워크플로 구현이다.

### 4.2 문항 구조화 출력 (items 템플릿 계약)

```python
class GeneratedItem(BaseModel):
    area_tag: AreaTag                    # 요청값 에코 — 불일치 시 파싱 탈락
    type_tag: TypeTag
    item_format: ItemFormat              # SUPPORTED_ITEM_FORMATS 검증 (에코 불일치 = 파싱 탈락)
    skill_node_id: str | None            # curriculum_graph 노드
    stem: str
    choices: list[Choice]                # 정확히 5개
    answer: Answer
    rationale: str                       # 해설 — 강사 화면 해설의 유일 출처
    evidence: list[EvidenceAnchor]       # ★ 필수 · 최소 1개 (공용 EvidenceRef 체계)

    @model_validator(mode="after")
    def _why_wrong_required(self):
        # 오답 선지의 why_wrong 누락 = ValidationError → 파싱 탈락(outcome=field_missing)
        ...

class Choice(BaseModel):
    no: int                              # 1..5
    text: str
    why_wrong: str | None                # 오답 선지는 필수 — validator가 강제

class EvidenceAnchor(BaseModel):
    kind: Literal["passage_span","dict_entry","grammar_rule","work_span"]
    ref: str                             # passage_span: "문단2:문장3" · dict/rule: 표제어/규칙 ID · work_span: 풀 오프셋
    quote: str | None                    # passage/work span은 원문 대조용 발췌

class Answer(BaseModel):
    correct_no: int                      # mcq 정답 번호 1개 (v1)
    # short용 canonical·accepted_variants·정규화 규칙은 §9 예약 — v1 미구현
```

**`EvidenceAnchor` ↔ `EvidencePack` 대응 `[GraphRAG 편입 — 11 §4.3]`:** 위 `EvidenceAnchor` 계약은 **무변경**이다. GraphRAG 도입 후에도 문항이 담는 것은 최소 앵커뿐이며, 출처 버전·해시·라이선스·검색 경로는 [`11`](11_graphrag_knowledge_layer.md) §4.2의 `EvidencePack.anchors[]`가 보관한다.

| `EvidenceAnchor` | `EvidencePack.anchors[]` | 비고 |
| --- | --- | --- |
| `kind` | `kind` | 동일 어휘. Pack은 T2용 `source_claim`을 추가로 가진다 |
| `ref` | `anchor_id` → `ref` | 문항은 `anchor_id`로 가리킨다 |
| `quote` | `quote` + `quote_hash` | R-1의 원문 일치 검사는 `quote_hash`로 수행([`06`](06_quality_gates.md) §1) |
| — | `source_id`·`source_version`·`source_content_hash` | 문항 계약에 없고 Pack에만 존재 |
| — | `license_ref`·`rights_status` | 〃 — `approved`가 아니면 Pack 생성 자체가 실패 |
| — | `graph_path_edge_ids` | 근거 선택 경로 재현용 |

공용 `EvidenceRef`가 구현되기 전에 이 계약을 임의 변경하지 않는다.

### 4.3 교차 풀이 출력 — SolveResult (+ 약점 정렬 판정, 확정)

```python
class SolveResult(BaseModel):
    # blind 풀이
    chosen: int                          # 선택 번호
    reasoning: str                       # 저장만 — 강사 해설로 미노출
    confidence: float = Field(ge=0, le=1)
    multiple_answers_possible: bool = False

    # 약점 의미 정렬 판정 (게이트 ②에 포함 — 추가 콜 0)
    target_skill_node_id: str | None     # 요청 목표 에코
    measured_skill_node_id: str | None   # verifier가 판정한 실제 측정 대상
    aligned: bool                        # 이 문항이 목표 약점을 연습·측정하는가
    alignment_confidence: float = Field(ge=0, le=1)
    alignment_reason: str                # 구조화 사유 — 재생성 피드백 재료
```

**blind 계약(명문화):** blind = **정답·해설·근거(evidence) 비공개**. 목표 메타(area·type·skill_node·노드 설명)는 정렬 판정을 위해 **제공한다** — 정답 유추에 기여하지 않는 정보만. `alignment_confidence`가 낮다고 자동 통과시키지 않는다 — 판정 규칙은 [`06_quality_gates.md`](06_quality_gates.md) §2.

### 4.4 응답·상태 — 공용 규약 준수

- 응답은 공용 envelope: `data` / `meta.execution_id` / `meta.versions` / `error`. **meta.versions는 공용 `VersionSet`** — 공통 6종(pipeline·engine·schema·contract + nullable threshold·prompt) + **B 전용 nullable 4종(graph·taxonomy·verify_config·difficulty_calib — ✅ A+B 승인·`d5283d0` 구현 및 공용 문서 동기화 완료, [`09`](09_integration_proposals.md) §2-9·§2-10)** 을 그대로 따른다. B 버전은 산출물 행(WEAKNESS_MAP·PROBLEM_ITEM)에도 함께 저장된다(재현 조회 키). **meta.quota는 없다**(7/15 폐기 — AI는 쿼터 무관).
- 비동기 세트 상태: `queued → generating → generated | partial_success | failed` + 진행률(`"7/10"`). 완료 통지는 Kafka(09 §2-1), GET은 보조. `partial_success` 등 상태 사전 추가는 공용 제안(09 §2-2) — 확정 전 `[제안]`.
- 생성 요청의 정상 결과는 `ProblemGenerationOutcome` 판별 유니언이다.

```python
class RejectedInsufficientOutcome(BaseModel):
    outcome: Literal["rejected_insufficient"]
    status: Literal["rejected_insufficient"]
    target_source: Literal[TargetSource.WEAKNESS_AUTO]
    personalized: Literal[False]
    weakness_map_id: None
    status_reason: str

class ProblemSetResult(BaseModel):
    outcome: Literal["problem_set"]
    set_id: UUID
    status: ProblemSetStatus
    stop_reason: SetStopReason | None
    target_source: TargetSource
    personalized: bool
    requested_count: int
    processed_count: int
    unstarted_count: int
    items: tuple[ItemResult, ...]
    summary: str | None
    dropped_reasons: tuple[ProblemFailureReason, ...]

type ProblemGenerationOutcome = Annotated[
    RejectedInsufficientOutcome | ProblemSetResult,
    Field(discriminator="outcome"),
]
```

- `rejected_insufficient`는 자동 개인화 데이터가 부족해 세트를 만들기 전에 정상 종료한 결과다. 예외나 워커 실패가 아니며 HTTP 200으로 반환한다. 세트가 생성되지 않았으므로 `set_id`와 수량 필드는 없다.
- `processed_count == len(items)`와 `requested_count == processed_count + unstarted_count`를 항상 만족한다.
- **`ItemResult.review_reason` 신설 `[결정안 BAND-5·KEEP-9]`** — 현재 validator가 `needs_review`에 `failure_reason` 기록을 금지하므로([`06`](06_quality_gates.md) §5의) **배지 사유를 담을 필드가 없다.** `failure_reason`(폐기 사유)과 분리된 별도 enum을 둔다: `low_confidence` · `area_mismatch` · `t3_literature` · `diagnostic_purpose` · `manual_target_first` · **`difficulty_band_mismatch`**. 함께 `difficulty_est`·`difficulty_band`도 `ItemResult`에 싣는다. 진행 결과는 문항 체크포인트 경계에서만 기록해 처리 중 슬롯이 별도로 남지 않게 한다.
- `generated`는 요청 문항을 모두 처리하고 모두 `verified|needs_review`인 경우만 허용한다. `partial_success`는 성공 문항이 하나 이상이고 실패 문항 또는 미처리 문항이 있을 때, `failed`는 성공 문항이 없을 때만 허용한다.
- 최종 결과에 `unstarted_count > 0`이면 조기 중단 원인을 `stop_reason`에 반드시 기록한다. `verification_unavailable`과 `partial_success`는 결과를 정상 확정한 워크플로의 도메인 상태이며, 그 자체를 워커 실행 실패로 승격하지 않는다.

## 5. 프롬프트 템플릿 3종 — registry 등록 사양

| prompt_id | 역할(ModelRole) | 입력 | 출력 | 온도 정책 |
| --- | --- | --- | --- | --- |
| `pg.passage.v1` | generator | PassageRequest + 금칙 사전 | PassageDraft | 중간(다양성) |
| `pg.items.v1` | generator | 지문/예문 + 타겟(area·type·node) + 기출제 요약 | GeneratedItem | 낮음 |
| `pg.cross_solve.v1` | **verifier** | 지문 + stem + choices + **목표 메타** (answer·rationale·evidence 미포함 — blind) | SolveResult | 0 고정 |

- 세 템플릿 전부 `registry.yaml` 등록(B 단독 행). 버전 업 시 `golden/problems/` 회귀 통과가 머지 조건.
- blind 계약은 **호출 코드가 보장** — `verification.py`가 answer·rationale·evidence를 물리 제거한 페이로드 조립(프롬프트 지시만 믿지 않는다). 회귀: [`08_evaluation_plan.md`](08_evaluation_plan.md) §7.
- verifier 라우팅은 gateway가 **다른 모델 패밀리 강제**(가능하면 다른 공급자 우선) — 템플릿은 벤더 문법에 비의존.
- 모든 프롬프트는 전송 직전 redaction 통과(masking §3 — fail-closed).

## 6. 난이도 추정 초기값 — DIFFICULTY_CALIB v1 default

`difficulty_est` = 기본 1 + Σ가중치, 전부 결정론.

> **⚠️ 실제 범위 정정 `[2026-07-27]`** — 종전 표기 `∈ [1,5]`는 **가중치 합계와 맞지 않는다.** 아래 5요인을 전부 더해도 상한은 **4.5**이고, **T1은 지문이 없어 길이 가중치가 항상 0이므로 `1.0~3.5`**(가능값 6개: 1.0·1.5·2.0·2.5·3.0·3.5)다. M2 v1은 T1만 다루므로 **`[1,5]` 기준으로 하·중·상을 3등분하면 T1에는 "상"이 존재하지 않는다.**
>
> 그래서 밴드 경계는 **트랙별 시트 행**으로 관리한다 — `verify_config.difficulty_band_map.T1`([`06`](06_quality_gates.md) 부록 · `[결정안 BAND-1]`). 산식 자체는 `[잠정]`이므로 파일럿 실측 전에 재설계하지 않는다. `[전 항목 잠정 — 파일럿 실측 보정]` 값 관리는 [`06_quality_gates.md`](06_quality_gates.md) 부록.

| 요인 | 규칙 | 가중 |
| --- | --- | --- |
| 지문 길이 | 어절 700 초과 +0.5 · 1000 초과 +1.0 (T1은 0) | +0~1.0 |
| 문장 복잡도 | advanced +0.5 | +0~0.5 |
| type_tag | fact/concept +0 · infer +0.5 · critic +1.0 | +0~1.0 |
| 선지 변별 | 교차 풀이 확신도가 낮았던 문항 +0.5 | +0~0.5 |
| 복수 근거 | evidence 2개 이상 통합 요구 +0.5 | +0~0.5 |

교차 풀이 확신도를 난이도 신호로 재활용(추가 비용 0). `difficulty_est`는 게이트 ①②를 통과한 뒤 **게이트 ③ ReleaseDecision 전에** 결정론 코드가 계산한다. 그래야 요청 난이도와의 불일치를 게이트 ③이 판정할 수 있다. 실측 보정 전 `difficulty_est`는 추정값이며 강사 화면에는 "추정" 라벨을 명시한다.

`difficulty_fit`은 학생에게 이 문항이 얼마나 적합한지를 나타내는 별도 값이다. v1에서는 실제 익명 풀이 데이터가 없으므로 **항상 null**이며, 값을 계산하거나 분기에 사용하는 코드를 만들지 않는다(B-M2-01).

요청 난이도의 내부 필드와 하·중·상 band 경계는 B+제품·FE 합의 전까지 활성 계약이 아니다. 따라서 해당 값이 확정되기 전과 파일럿 첫 2주에는 `difficulty_regen_enabled=false`를 유지한다. 활성화 후의 재생성·검토 판정은 [`06_quality_gates.md`](06_quality_gates.md) §5를 따른다. 외부 API 응답에 band를 노출하는 변경은 [`09_integration_proposals.md`](09_integration_proposals.md)의 BE+B 합의 대상으로 분리한다.

## 7. 재생성 지시 규약 — 게이트 실패 피드백 루프

문항당 시도 예산은 **item_attempt 총 3회(최초 1+재생성 2 — 확정)** 하나뿐 — 파싱 탈락·정렬 실패·규칙 실패·교차 불일치가 전부 같은 예산을 소모한다(검사별 중첩 루프 금지). 재생성 콜은 동일 템플릿 + **실패 사유 구조화 주입**: `retry_context: {attempt_no, failed_checks: ["R-2 정답 유일성: 선지 3도 성립" | "정렬: 측정 대상=표준 발음"], previous_stem_hash}`. 자유 문장 피드백 금지. 동일 stem 해시 재출력은 즉시 폐기(시도 소모). 전송 재시도와의 분리·최악 호출 수는 [`06_quality_gates.md`](06_quality_gates.md) §4.

## 8. 안전 규격

### 8.1 금칙·안전 사전 — `pg_banned_topics.yaml` (B 소유 데이터 파일)

- 교육 콘텐츠 금칙: 정치·종교 논쟁 현안, 특정 실존 인물 평가, 자해·폭력 묘사, 차별적 소재. A의 `buffer_lexicon.yaml`과 목적이 다르므로 별도 파일 — 관리 규율 동일(PR 리뷰 + 골든 통과가 머지 조건).
- 적용 2중: 생성 프롬프트 회피 지시(1차) + 게이트 코드 대조(2차 — R-5). 프롬프트 지시만 믿지 않는다.

### 8.2 prompt injection 방어 `[P0 — C-15 계열]`

교사 입력(`topic_hint`·refine `instruction`)과 외부 텍스트는 신뢰할 수 없는 입력이다:

1. **지시-데이터 분리:** 사용자 입력은 지시 영역이 아니라 **구조화 데이터 필드로만** 주입(템플릿이 격리 슬롯 제공).
2. **입력 정적 검사(결정론):** 지시 이탈 패턴("이전 지시 무시", 프롬프트 유출 요구, 금칙 우회) 검출 시 요청 거부(200 + status — 게이트 거부는 에러 아님). 패턴은 데이터 파일 관리.
3. **출력 게이트 재검증:** injection이 1·2를 뚫어도 산출물은 게이트 ①②를 통과해야 저장 — "강사 지시가 게이트를 이기지 못한다"의 출제판.
4. 공격 코퍼스: [`08_evaluation_plan.md`](08_evaluation_plan.md) §8.

### 8.3 중복 억제 vs 외부 표절·유사도 `[P0 — C-15]`

| 검사 | 대상 | 상태 |
| --- | --- | --- |
| 내부 중복 억제(R-6) | 동일 세트·동일 학생 최근 출제분과 stem 유사도 | v1 규격 확정([`06`](06_quality_gates.md) §1) |
| **외부 표절·유사도** | 기출·시중 교재·타사 지문과의 유사도(저작권 리스크) | `OPEN [P0]` — 대조 코퍼스·수단 미확정. 확보 전 T2 저작권 방어는 "자체 생성 원칙+금칙"뿐임을 명시. 담당 B+기획 |

## 9. 예약 (v1 미구현 — 분기 코드 작성 금지)

1. **`short`(단답형):** Answer의 canonical·accepted_variants·정규화 규칙(공백·문장부호·조사 변형·동의어·복수 정답 명시) — 내신·자체 시험 타겟 확장 시 활성화. enum 자리만 예약.
2. **`essay`(서술형):** 루브릭 생성·정합 검사 — **구 B-4 안건은 7/15 폐기**(v1 서술형 없음). F17·Open-12와 함께 P2 재론.
3. **F17 조판(P2):** `print_layout.py`는 문항 메타(area·type·item_format·skill_node_id) 무손실 보존. MVP 단순 PDF 여부·소유자는 D-09 `OPEN`.
4. taxonomy 하위 분류(점 표기) 확정 시 §2 `domain`을 공용 enum으로 승격.

## 10. OPEN 항목

| 번호 | 항목 | 담당 |
| --- | --- | --- |
| D-03 | T1 사전·문법 규칙 데이터 소스(공급처·버전·라이선스) | B+기획 |
| D-04 | T2 사실성 보장 수단(승인 자료 체계) — 확보 전 사실 서술 지문 발행 차단 | B+기획 |
| C-15 `[P0]` | 외부 표절·유사도 수단 + injection 공격 코퍼스 | B+기획 |
| — | 문학 풀 초기 등재 규모·라이선스 확인 | BE+기획 |
