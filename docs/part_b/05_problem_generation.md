# [체크온] 문항 생성 규격서 v1 — 요청·지문·문항 계약 · 출처·저작권 · 보안 규격

> **지위:** member-B(염준영) 공식 규격 v1. `src/ai/llm/prompts/templates/problem_generation/`(passage · items · cross_solve)의 사양 원본이며 `contracts/problem_generation.py` 타입 정의 근거. **v1 생성 범위: `mcq`만(7/15 확정)** — `short`·`essay`는 enum 예약(§9).
>
> **변경 이력**
> - v1.1 (2026-07-15): 파일 번호 이동(03→05) + 7/15·대화 결정 반영 — ① mcq만(short 정규화 규칙은 예약으로 강등) ② `target_source`(수동 목표 출제) ③ SolveResult에 약점 정렬 판정 필드 ④ meta.quota 폐기 ⑤ 재시도 총 3회(item_attempt) ⑥ 서술형(구 B-4) 폐기. 상호 링크 재편.
> - v1 (2026-07-15): 입력 초안 `CODEXPROMPT/(염준영)_문항_생성_규격서_v0.md` 정리 — 공용 enum 강제·전체 스키마·멱등/버전 필드·injection 방어·T1 자료 요건·사실성 fail-closed.
>
> **원칙 3줄:** ① 비문학 지문은 자체 생성 — 기출·타사 지문 모사 금지(저작권 경계) ② 문학은 공유 저작물 풀 선택만 — LLM이 작품을 만들거나 변형하지 않는다 ③ 모든 문항은 구조화 출력 + rationale 근거 인용 강제 — 근거 없는 문항은 게이트 이전 파싱 단계에서 탈락.
>
> **참조** — [`01_pipeline.md`](01_pipeline.md) §3 · [`04_curriculum_graph.md`](04_curriculum_graph.md) §7 · [`06_quality_gates.md`](06_quality_gates.md) · `docs/policies/taxonomy.md` · `docs/policies/masking_redaction.md`(출제 프롬프트도 전 경로 적용)

---

## 1. 생성 트랙 3종 — 착수 순서가 곧 리스크 관리

| 트랙 | area | 지문 | Phase | 검증 가능성 |
| --- | --- | --- | --- | --- |
| **T1. 어휘·문법** | `language` | 없음(또는 1~2문장 예문) | **P1 — 최우선** | 사전·문법 규칙 대조로 기계 검증 가능 — 안전 범위 |
| **T2. 비문학 독해** | `reading` | 자체 생성 | P1 후반 | 근거 실존은 기계 검증, 정답 유일성은 교차 풀이, **사실성은 §2.1** |
| **T3. 문학** | `literature` | 공유 저작물 풀 | P1.5~2 | 작품 해석 개입 — 교차 풀이 + 검토 배지 강화 |

T1부터 골든셋(`golden/problems/`)과 게이트를 완성한 뒤 T2로 확장한다 — 게이트 미완성 상태로 T2를 열지 않는다. 전 트랙 v1 문항 형식은 **mcq(5지선다)**.

### 1.1 T1 기준 자료 요건 `[OPEN — D-03]`

R-1 대조(사전 표제어·문법 규칙 ID 실존)의 기준 데이터 소스 요건:
- **공급처·버전·라이선스가 명확**할 것(버전 없는 웹 스크랩 금지) — 후보 확정은 D-03(B+기획).
- 자료 버전은 `EvidenceAnchor.ref`의 ID 체계와 함께 고정 — 자료 개정 시 골든셋 동시 개정.
- **장애 시 처리:** 자료 조회 불가 상태에서는 T1 문항의 R-1 대조가 불가능하므로 **발행 차단**(`verification_unavailable` — [`06_quality_gates.md`](06_quality_gates.md) §3). 자료 없이 "그럴듯한 정답"으로 통과시키지 않는다.

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

**enum 규율:** `area_tag`·`type_tag`·`item_format`은 `contracts/taxonomy.py` 공용 enum을 **직접 사용**(6영역 확정 — 경계 사례만 `# TODO(B-3)`). 문자열 자유 입력 금지. `item_format`은 v1에서 `mcq` 고정 — short·essay 분기 코드 작성 금지(CLAUDE.md §3).

### 4.1 요청

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
    target: Literal["cell", "node", "auto"] = "auto"   # 04 문서 §7
    passage: PassageRequest | None       # T2만
    topic_hint: str | None               # §8.2 통과 필수
```

**결과 메타:** `target_source=teacher_manual` 세트는 응답과 저장에 `personalized=false`를 명시 — 화면 "약점 데이터 기반 개인화 아님" 표기의 근거 필드.

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

- 응답은 공용 envelope: `data` / `meta.execution_id` / `meta.versions` / `error`. **meta.versions는 공용 `VersionSet`** — 공통 6종(pipeline·engine·schema·contract + nullable threshold·prompt) + **B 전용 nullable 4종(graph·taxonomy·verify_config·difficulty_calib — ✅ A+B 승인·`d5283d0` 구현 완료, [`09`](09_integration_proposals.md) §2-9)** 을 그대로 따른다. B 버전은 산출물 행(WEAKNESS_MAP·PROBLEM_ITEM)에도 함께 저장된다(재현 조회 키 — 공용 문서 동기화는 09 §2-10 요청). **meta.quota는 없다**(7/15 폐기 — AI는 쿼터 무관).
- 비동기 세트 상태: `queued → generating → generated | partial_success | failed` + 진행률(`"7/10"`). 완료 통지는 Kafka(09 §2-1), GET은 보조. `partial_success` 등 상태 사전 추가는 공용 제안(09 §2-2) — 확정 전 `[제안]`.
- 결과: `ProblemSetResult { set_id, status, stop_reason, personalized, items: list[ItemResult], summary, dropped_reasons }` — 수량 미달을 숨기지 않는다.

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

`difficulty_est ∈ [1,5]` = 기본 1 + Σ가중치, 전부 결정론. `[전 항목 잠정 — 파일럿 실측 보정]` 값 관리는 [`06_quality_gates.md`](06_quality_gates.md) 부록.

| 요인 | 규칙 | 가중 |
| --- | --- | --- |
| 지문 길이 | 어절 700 초과 +0.5 · 1000 초과 +1.0 (T1은 0) | +0~1.0 |
| 문장 복잡도 | advanced +0.5 | +0~0.5 |
| type_tag | fact/concept +0 · infer +0.5 · critic +1.0 | +0~1.0 |
| 선지 변별 | 교차 풀이 확신도가 낮았던 문항 +0.5 | +0~0.5 |
| 복수 근거 | evidence 2개 이상 통합 요구 +0.5 | +0~0.5 |

교차 풀이 확신도를 난이도 신호로 재활용(추가 비용 0). 실측 보정 전 `difficulty_est`는 표시·정렬용 — 강사에게 "추정" 라벨 명시.

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
