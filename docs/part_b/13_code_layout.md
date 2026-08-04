# [체크온] M2 코드 배치 규약 v1 — `problem_generation` 계층 구조

> **지위:** member-B(염준영) 소유. `src/ai/problem_generation/` 내부 배치의 정본이며, "새 코드를 어디에 넣는가"의 판단 기준이다. 경계는 `tests/ai/contract/test_pg_layer_boundaries.py`가 AST로 강제한다 — 문서와 코드가 어긋나면 테스트가 먼저 깨진다.
>
> **변경 이력**
> - v1 (2026-08-04): 신설. `problem_generation`을 domain·application·infrastructure 3계층으로 재배치했다.
>
> **참조** — [`01_pipeline.md`](01_pipeline.md) §3 · [`05_problem_generation.md`](05_problem_generation.md) §1 · [`06_quality_gates.md`](06_quality_gates.md) · `docs/02_ownership.md` · `docs/03_coding_rules.md`

---

## 1. 왜 바꿨나 — 재배치 전 실측

| 파일 | 줄 | 문제 |
| --- | ---: | --- |
| `workflow.py` | 920 | LangGraph 노드·재시도·중단 판정·요청 해시가 한 파일 |
| `verification.py` | 477 | **yaml 로딩(I/O)** 과 R-1~R-7 판정(순수 규칙)이 한 파일 |
| `generator.py` | 388 | **저장소 Protocol + InMemory 구현**과 생성 로직이 한 파일 |
| `cross_solver.py` | 93 | — |

그리고 `_canonical_json`이 `generator.py:361`과 `cross_solver.py:84`에 **똑같이 두 번** 정의돼 있었다. 결정론 해시(불변식 8)의 근거 함수가 복제돼 있으면 한쪽만 고쳐도 아무도 모른다.

트랙이 3종에서 5종으로 늘고([`05`](05_problem_generation.md) §1) T4·T5가 붙으면 여기부터 무너진다.

## 2. 계층 3종

```
problem_generation/
├── domain/              순수 규칙·값 객체 — I/O·LLM·프레임워크 의존 0
│   ├── policy.py        VerifyConfig 등 검증 파라미터 스키마
│   ├── rules.py         게이트 ① R-1~R-7 · 유사도 · 근거 실존
│   ├── difficulty.py    T1 난이도 추정·밴드 분류
│   ├── cross_solve.py   게이트 ② 결과 판정
│   ├── identity.py      결정론 해시·ID (canonical_json · sha256_hex · request_hash)
│   └── models.py        CandidateSnapshot · StoredProblemItem · RetryContext · TargetPlan
├── application/         도메인 조합 + 포트 호출 — 오케스트레이션
│   ├── ports.py         CandidateStore · ProblemItemStore Protocol
│   ├── generator.py     문항 생성 호출
│   ├── cross_solver.py  blind 교차 풀이 호출
│   └── workflow.py      ProblemGenerationWorkflow (LangGraph)
├── infrastructure/      포트 구현·파일 I/O 어댑터
│   ├── config.py        verify_config·banned_topics yaml 로딩
│   └── memory_store.py  InMemory 저장 어댑터
└── data/                yaml 정본 (배치 무변경)
```

**의존 방향은 `infrastructure → application → domain` 하나뿐이다.**

```
domain          아무도 모른다. contracts와 pydantic만 안다
application     domain을 안다. 포트를 정의하고 호출한다
infrastructure  application의 포트를 구현한다
```

역방향이 생기면 순수 규칙이 바깥 세계에 묶여 테스트가 어려워지고, 결정론 경로에 파일·네트워크가 섞인다.

## 2.1 무엇이 어디로 갔나 — 심볼 대조

기존 4파일이 11모듈로 갈렸다. **로직은 그대로이고 위치만 바뀌었다.**

### `verification.py` (477줄) → 4곳

| 심볼 | 이동처 |
| --- | --- |
| `DifficultyRange` · `T1DifficultyBandMap` · `PassageWordCountWeights` · `SentenceComplexityWeights` · `DifficultyWeights` · `VerifyConfig` · `BannedTopicCategory` · `BannedTopicsConfig` | `domain/policy.py` |
| `RuleValidationResult` · `RuleValidator` · `has_reference_data` · `_allowed_evidence_refs` · `_normalize` · `_ngram_similarity` · `_ngrams` | `domain/rules.py` |
| `estimate_t1_difficulty` · `classify_t1_difficulty` · `needs_difficulty_regeneration` · `distance_to_requested_midpoint` · `_band_index` | `domain/difficulty.py` |
| `CrossValidationResult` · `validate_cross_solve` | `domain/cross_solve.py` |
| `VerificationConfigError` · `load_verify_config` · `load_banned_topics` · `_load_yaml_model` · 경로 상수 | `infrastructure/config.py` |

### `generator.py` (388줄) → 4곳

| 심볼 | 이동처 |
| --- | --- |
| `RetryContext` · `CandidateSnapshot` · `StoredProblemItem` | `domain/models.py` |
| `item_stem_hash` · `problem_item_id` · `_canonical_json` → **`canonical_json`** · `_sha256` → **`sha256_hex`** | `domain/identity.py` |
| `ImmutableStoreConflict` · `CandidateStore` · `ProblemItemStore` | `application/ports.py` |
| `ProblemGenerator` · `build_candidate_snapshot` · `require_successful_text` · `_ITEM_PROMPT_ID` | `application/generator.py` |
| `InMemoryCandidateStore` · `InMemoryProblemItemStore` | `infrastructure/memory_store.py` |

### `cross_solver.py` (93줄) → 1곳 + 중복 제거

| 심볼 | 이동처 |
| --- | --- |
| `BlindCrossSolver` · `_CROSS_SOLVE_PROMPT_ID` | `application/cross_solver.py` |
| `_canonical_json` | **삭제** — `generator.py`와 동일 정의였다. `domain/identity.py`로 합쳤다 |

### `workflow.py` (920줄) → 1곳 + 2심볼 분리

| 심볼 | 이동처 |
| --- | --- |
| `ProblemGenerationWorkflow` · 예외 3종 · `_AttemptFeedback` · `_stop_reason` · `_checked_update` | `application/workflow.py` (908줄) |
| `TargetPlan` | `domain/models.py` |
| `_request_hash` → **`request_hash`** | `domain/identity.py` |

**공개 승격 3건**(`canonical_json` · `sha256_hex` · `request_hash`)은 중복 제거·계층 분리로 두 모듈이 공유하게 된 결과다. 시그니처와 본문은 그대로다.

## 2.2 import 경로 변경

기존 경로를 쓰던 곳은 **10곳**이었고 전부 갱신했다. shim(구 경로 재수출)은 두지 않았다 — 과도기 잔재가 남으면 어느 쪽이 정본인지 흐려진다.

| 전 | 후 |
| --- | --- |
| `problem_generation.verification` → `RuleValidator` | `...domain.rules` |
| 〃 → `estimate_t1_difficulty` · `classify_t1_difficulty` · `needs_difficulty_regeneration` · `distance_to_requested_midpoint` | `...domain.difficulty` |
| 〃 → `validate_cross_solve` | `...domain.cross_solve` |
| 〃 → `VerifyConfig` · `DifficultyRange` · `T1DifficultyBandMap` · `BannedTopicsConfig` | `...domain.policy` |
| 〃 → `load_verify_config` · `load_banned_topics` | `...infrastructure.config` |
| `problem_generation.generator` → `ProblemGenerator` · `build_candidate_snapshot` · `require_successful_text` | `...application.generator` |
| 〃 → `CandidateStore` · `ProblemItemStore` | `...application.ports` |
| 〃 → `CandidateSnapshot` · `RetryContext` | `...domain.models` |
| 〃 → `item_stem_hash` · `problem_item_id` | `...domain.identity` |
| 〃 → `InMemoryCandidateStore` · `InMemoryProblemItemStore` | `...infrastructure.memory_store` |
| `problem_generation.cross_solver` → `BlindCrossSolver` | `...application.cross_solver` |
| `problem_generation.workflow` → `ProblemGenerationWorkflow` · `ProblemWorkflowConfigurationError` | `...application.workflow` |
| 〃 → `TargetPlan` | `...domain.models` |

**테스트 경로 상수도 두 곳 바뀌었다** — `test_problem_generation_redaction.py`의 gateway 호출 화이트리스트와 `test_evidence_blind_isolation.py`의 blind 조립 지점(§7 참조).

## 3. 새 코드를 어디에 넣는가

| 질문 | 답 |
| --- | --- |
| 파일·DB·네트워크를 만지나 | **infrastructure** |
| LLM을 부르나 | **application** — gateway 호출은 화이트리스트로 고정돼 있다 |
| LangGraph 노드인가 | **application/workflow.py** |
| 입력만 받아 판정·계산해서 돌려주나 | **domain** |
| 값 객체·enum·스키마인가 | 계약이면 `contracts/`, 내부 전용이면 **domain/models.py** |
| 저장·조회 인터페이스인가 | **application/ports.py**(정의) + **infrastructure**(구현) |

**판단이 갈리면 domain에 두려고 해보고, I/O가 필요해지는 순간 밀어내라.** 순수하게 못 쓰는 함수는 domain 소속이 아니다.

## 4. 알려진 예외 1건

```python
application/workflow.py:132
    self._verify_config = verify_config or load_verify_config()
```

**`application → infrastructure` 직접 import는 여기 한 곳뿐이다.** 주입 경로(`verify_config=` 인자)가 정본이고 이건 미주입 시 폴백이다.

`test_pg_layer_boundaries.py`의 `_APPLICATION_TO_INFRASTRUCTURE_ALLOWED`가 이 한 곳만 허용한다. **목록을 늘리기 전에 조립부에서 주입하는 쪽을 먼저 검토한다** — 폴백이 늘면 "설정이 어디서 오는지"가 흩어진다.

## 5. 하지 않은 것과 이유

| 대상 | 판단 |
| --- | --- |
| **`workflow.py` 분할** | `ProblemGenerationWorkflow`가 **757줄 단일 클래스**다. 파일 이동으로는 못 쪼개고 협력 객체 추출이 필요한데, 그건 로직 변경이라 배치 작업과 섞지 않는다. **별건으로 남긴다** |
| **`diagnosis/`** | `diagnoser.py` 377 + `skill_graph.py` 230. 순수 계산과 yaml 로딩이 이미 **파일 단위로 갈려 있다.** 지금 쪼갤 이유가 없다 |
| **`contracts/problem_generation.py`** | 687줄이지만 **계약은 한 파일에서 전체를 보는 게 장점**이다. 쪼개면 "이 capability의 계약 전부"를 한눈에 못 본다 |
| **`src/ai/` 최상위 재편** | `CLAUDE.md` §2·`02_ownership.md`가 **폴더 구조 고정**을 못박았다. capability 위에 레이어를 얹으면 A 소유 경로까지 움직인다. **이번 작업은 capability 내부에 한정한다** |

## 6. 경계를 고정하는 계약 테스트

`tests/ai/contract/test_pg_layer_boundaries.py` — 18 케이스.

```
domain이 상위 계층을 참조하지 않는다          모듈별
domain에 I/O·LLM 의존이 없다                  모듈별 (yaml·sqlalchemy·openai·ai.llm 등)
application → infrastructure는 화이트리스트   모듈별
infrastructure는 포트 밖을 참조하지 않는다
계층이 비어 있지 않다                          구조 붕괴 조기 경보
```

**문서만으로는 계층이 유지되지 않는다** — import 한 줄이면 무너지기 때문이다.

## 7. 이동 중 발견한 것 — skip이 가드를 끈다

`tests/ai/contract/test_evidence_blind_isolation.py`(B-13 blind 격리)가 파일 경로를 하드코딩하고 **없으면 `pytest.skip`** 하는 구조였다. 파일을 옮기자 **실패가 아니라 skip 3건**으로 조용히 꺼졌다.

```
SKIPPED :83 · :112 · :125   "problem_generation/cross_solver.py 아직 없음"
```

경로를 갱신해 복구했다. **미구현 대비 skip은 구현 후에는 함정이 된다** — 같은 패턴이 남아 있으면 파일이 움직일 때마다 가드가 조용히 꺼진다. 신규 계약 테스트에는 skip 대신 실패를 쓴다.

## 8. 검증

```
ruff · mypy 253 files · pytest 1529 passed / 실패 0 / skip 0

재배치 전   1500
재배치 후   1511   (+11)  AST 계약 테스트가 새 파일을 스캔한 증가분
                          test_vendor_isolation 등이 파일마다 돈다
                          테스트를 추가·삭제하지 않았고 로직도 바꾸지 않았다
계약 신설   1529   (+18)  test_pg_layer_boundaries.py
```

**+11의 근거를 확인했다** — `git stash`로 재배치 전 상태를 돌려 `1500 passed`를 재현했고, 차이가 파일 순회 계약 테스트에서만 나온다는 것을 대조했다. 숫자가 늘었다고 그냥 넘기지 않는다.

`domain/identity.py`의 `canonical_json`·`sha256_hex`는 중복 제거로 두 모듈이 공유하게 되어 **private에서 공개 이름으로 승격**했다. 시그니처·본문은 그대로다.
