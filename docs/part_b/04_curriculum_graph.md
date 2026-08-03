# [체크온] curriculum_graph 사양서 v1 — 진단 입력 계약 · 그래프 3종 · 약점 판정 · 결정론 규격

> **지위:** member-B(염준영) 공식 사양 v1. `src/ai/diagnosis/`(skill_graph.py · diagnoser.py)와 `diagnosis/data/curriculum_graph.yaml`의 사양 원본. 영역 어휘는 `contracts/taxonomy.py` 준수 — **6영역 enum과 측정 대상 기준 경계 사례 7건은 7/27 확정**.
>
> **변경 이력**
> - v1.4 (2026-08-03): §4 판정표의 `[잠정]` 근거를 명시 — ① `n=10` 이항 표준오차 ≈ ±15.8%p로 참값 delta 0인 셀도 약 17%가 `weak`로 뜨고, ② `part_a/13` §4-3-3 실측상 **난이도 변동의 84%가 영역×유형 안에서** 발생해 ①의 균일 가정 자체가 서지 않는다. **파일럿 전까지 셀 verdict는 강사 참고용 힌트이며 자동 처방 근거가 아니다**를 명문화했다(자동 경로는 `overall_low` 하나).
> - v1.3 (2026-07-27): §8 OPEN에 2건 추가 — **W7 적용 교육과정 개정판 확정**(2022/2015 성취기준 정본, 지원 학년 미확정 시 25~40노드 작성 범위가 안 정해짐), GraphRAG projection 관계와 **`graph_version` 재사용 금지** 명시([`11`](11_graphrag_knowledge_layer.md) §2.1·§7).
> - v1.2 (2026-07-15): 구현 착수 전 규칙 3건 확정 — ① 중복 이벤트: 완전 동일 재수신만 dedupe, 필드 상이 시 진단 중단(§3.2) ② 노드 severity 정의: suspect=연결 weak 셀 최댓값·weak_confirmed=직접 재계산+직접 weak 기준 명시(§5.1~§5.2) ③ overall_low 분모=판정 가능 셀(§4).
> - v1.1 (2026-07-15): 파일 번호 이동(02→04) + 7/15 반영 — 6영역 확정 표기, 중등은 v1 범위 아님(타겟 협소화), 데이터 부족 시 수동 목표 출제 분기 연결. 상호 링크 재편.
> - v1 (2026-07-15): 입력 초안 `CODEXPROMPT/(염준영)_curriculum_graph_사양서_v0.md` 정리 — 진단 입력 계약·중복/재처리 규칙·severity 공식·suspect 제한·병합 예제·버전 필드 추가.
>
> **참조** — [`01_pipeline.md`](01_pipeline.md) §2 · `docs/policies/taxonomy.md` · `part_a/04_threshold_config.md`(R6 시트 — 값 참조만) · [`06_quality_gates.md`](06_quality_gates.md) 부록(잠정 파라미터)

---

## 1. 왜 그래프인가 — 셀 표와의 차이

area×type 셀 정답률만으로는 "언어(문법) 약함"까지만 말할 수 있다. 출제의 직접 입력이 되려면 "음운 변동이 약한데, 그 선수 개념인 음운 체계부터 흔들린다" 수준까지 내려가야 한다. 그래프의 역할:

1. **역추적(진단):** 약한 노드에서 선수(prerequisite) 엣지를 거슬러 올라가 근본 결손 후보를 찾는다.
2. **타겟 선정(출제):** 근본 결손 노드부터 출제 대상으로 잡는다 — 말단만 반복 출제하는 낭비 방지.

단, v1 학습 데이터의 태그 해상도는 area×type까지다(하위 분류는 taxonomy v1 예약). 노드-데이터 매핑은 §5의 간접 매핑이며, 노드 단위 직접 정답률은 B가 출제한 문항(`PROBLEM_ITEM.skill_node_id` 보존)의 제출 데이터부터 축적된다.

## 2. 그래프 3종 — 공통 골격, 다른 엣지 의미론

| 그래프 | area_tag | 구조 | 엣지 의미 | 예 |
| --- | --- | --- | --- | --- |
| 문법 DAG | `language` | 방향 비순환 그래프 | `requires` — 엄격한 선수 관계 | 음운 체계 → 음운 변동 → 표준 발음 |
| 독해 기능 사다리 | `reading` | 레벨 있는 DAG | `builds_on` — 기능 누적 관계 | 사실적 이해 → 추론적 → 비판적 → 통합·적용 |
| 문학 개념어 그래프 | `literature` | 느슨한 DAG + 관련 엣지 | `requires`(약) + `related` | 화자/서술자 → 시점 · 심상 ↔ 표현법 |

`speech`·`writing`·`media`는 v1 그래프 미구축 — 셀 정답률 판정만 제공. 그래프 부재 영역도 WeaknessMap 산출은 정상 동작한다(propagated만 빈 값). **중등·내신은 v1 범위 아님(7/15 확정)** — 그래프는 수능 고등 기준 단일, 중등 분기 금지.

**실파일 산출물:** 문법 DAG 약 25~40노드의 실제 YAML 제작은 별도 작업 항목 — 독해 사다리·문학 그래프는 골격(레벨·대분류)만 우선. 등록: [`09_integration_proposals.md`](09_integration_proposals.md) §2-6.

## 3. 진단 입력 계약 — 백엔드 스냅숏 (alias 전용)

### 3.1 입력 스키마 (contracts/diagnosis.py — B 단독)

```python
class DiagnosisInput(BaseModel):
    tenant_id: str
    student_ref: str                     # alias만 — 실명·연락처 필드 자체가 없음
    period: Period                       # {from_date, to_date} — §3.3
    as_of: datetime                      # 집계 기준시점
    snapshot_hash: str                   # 계약 부록 A 해시 규칙
    events: list[DiagnosisEvent]

class DiagnosisEvent(BaseModel):
    event_id: str                        # learning_event 논리 참조 — 중복 제거 키
    area_tag: AreaTag                    # contracts/taxonomy enum (6영역 — 7/15 확정)
    type_tag: TypeTag
    item_format: ItemFormat | None       # 분리 리포팅용 — 판정 축 아님 (v1 데이터는 mcq 중심)
    skill_node_id: str | None            # B 출제 문항만 보유
    correct: bool
    occurred_at: datetime
    tag_confirmed: bool                  # False(ai_suggested)는 수신해도 집계 미반영
```

### 3.2 중복·재처리 규칙

- **중복 이벤트(확정):** **완전 동일 재수신만 dedupe** — 동일 `event_id`이고 전 필드가 동등(frozen 모델 동등성)한 이벤트만 1건으로 처리한다. 동일 `event_id`인데 **필드가 하나라도 다르면 임의 선택 없이 `DiagnosisInputConflictError`로 전체 진단을 중단**한다 — 일부 데이터만으로 약점 결과를 만들지 않는다(fail-closed). 이 예외는 diagnosis 내부 예외이며 **공용 에러 코드를 신설하지 않는다** — API 경계에서는 기존 `SnapshotInvalid → 400 INVALID_SCHEMA` 매핑(error_codes §4)을 따른다(게이트 거부의 200 정상 상태가 아님 — 입력 데이터 결함).
- **집계 순서(확정):** 정상 이벤트는 `event_id` 기준 정렬 후 집계 — 입력 배열 순서가 달라도 동일 결과 보장(event_id는 유일 키라 총순서 성립).
- **동일 스냅숏 재처리:** `(tenant_id, student_ref, graph_version, config_version, snapshot_hash)`가 같으면 기존 WeaknessMap 반환(재계산해도 바이트 동일 — 결정론 회귀 기준). 하나라도 다르면 새 버전 생성, 이전 버전 보존.
- **미확정 태그:** `tag_confirmed=false` 이벤트는 집계 제외 — A의 "미확정 태그 피처 미반영"과 대칭.

### 3.3 집계 기간·기준시점

- 기본 집계 기간: `as_of` 기준 **최근 12주** `[잠정]` — 요청 `period`가 우선하되 상한 26주 `[잠정]`.
- 주차 경계·시간대는 스냅숏 계약(`docs/04_api_contract.md` §4)의 주차 정의를 그대로 따른다.

### 3.4 데이터 부족 판정 (확정 — 수동 목표 분기)

판정 가능한 셀이 하나도 없으면(전 셀 `unknown`) 자동 개인화 출제 입력으로 부적합 — `rejected_insufficient` 반환(200 + 정상 상태). 이후 흐름(강사 수동 목표 출제 허용·비개인화 표기)은 [`01_pipeline.md`](01_pipeline.md) §2와 [`03_usecases.md`](03_usecases.md) U2.

## 4. 약점 판정 — 셀 단위 (v1 데이터 해상도)

| verdict | 조건 `[잠정 — 파일럿 보정 대상]` |
| --- | --- |
| `unknown` | 표본 `n < cell_min_items` — 판정 유보, 약점 아님 |
| `weak` | n 충족 + 셀 정답률이 **학생 자기 전체 정답률 대비** −15%p 이하(상대 기준 — A의 이동 기준선 철학과 동일) |
| `ok` | 그 외 |

> ⚠ **`[잠정]`의 근거 — v1 셀 판정은 통계적 판정이 아니라 강사 참고용 운영 힌트다.** 두 종류의 오차가 컷을 상회한다.
>
> **① 표본 오차.** `cell_min_items=10`에서 정답률 추정의 이항 표준오차는 `sqrt(0.25/10) ≈ 15.8%p`(p=0.5 최악)다. 실제 비교 대상인 `cell_delta_pp`는 셀 정답률과 학생 전체 정답률의 **차이**라 오차가 `sqrt(SE_cell² + SE_overall²)`로 합쳐져 그보다 크다. 즉 참값 delta가 0인 셀도 `z = 15 / 15.8 ≈ 0.95` → **단측 약 17%가 `weak`로 뜬다.** 16셀 기준 학생당 평균 2.7개다. 밴드 폭 7%p가 1 표준오차 이상이 되려면 `n ≥ 51`이 필요한데, 주당 16문항·16셀이면 51주라 도달 불가다.
>
> **② 문항 간 난이도 분산.** [`part_a/13_threshold_validation.md`](../part_a/13_threshold_validation.md) §4-3-3 실측 — **난이도 변동의 84%가 같은 영역×유형 안에서 발생**하고 영역×유형이 설명하는 몫은 16%뿐이다. ①의 이항 오차는 셀 안의 문항 난이도가 균일하다는 가정 위에 있는데, **84%면 그 가정이 서지 않는다.** 셀 정답률이 낮은 것이 약점 때문인지 어려운 문항을 뽑아서인지 구분되지 않는다.
>
> ⇒ **파일럿 전까지 어떤 고정 컷도 판정 근거로 쓰지 않는다.** 셀 verdict는 강사에게 보여주는 힌트이며, **자동 경로로 흐르는 유일한 값은 `overall_low`**다(아래 분모 규칙이 그 노이즈를 완화한다). 컷의 분위 전환은 `part_a/13` §9 사다리와 같은 순서를 따른다 — 고정값으로 시작해 데이터가 쌓이면 분위로 옮긴다.

- **`cell_min_items` 공급 키:** 감지 R6과 **동일 값 참조** — `part_a/04_threshold_config.md` R6 행의 `cell_min_items`(v0 기본 10). B 임의 값 별도 운영 금지. 진단 전용 파라미터는 [`06_quality_gates.md`](06_quality_gates.md) 부록 'B 기본값 시트'의 `config_version`으로 관리.
- 절대 컷을 쓰지 않는 이유: 상위권의 "상대적 구멍"과 하위권의 "전면 부진"을 같은 자로 재면 출제 타겟이 왜곡된다.
- **`overall_low` 판정(확정 — 분모는 판정 가능 셀만, `unknown` 제외):** ① 판정 가능 셀(weak/ok) 0개 → `rejected_insufficient`(§3.4) ② 판정 가능 셀 **1개 이상이고 전부 weak** → `overall_low=True` `[잠정 — 파일럿 재평가: 판정 가능 셀이 극소수일 때의 과잉 판정 여부]` ③ 하나라도 ok → `overall_low=False`. unknown까지 분모에 넣으면 표본이 조금 부족한 셀 하나 때문에 전면 부진 플래그가 막히는 문제를 피한다. overall_low 시 역추적 대신 기초 레벨(level 1 / DAG 루트) 우선 출제 권고 — 플래그만 산출하고 권고 적용은 소비자 몫.

```
cell_delta_pp = (cell_acc − student_overall_acc) × 100   # %p 단위 — 비율(acc)에 100을 곱해 스케일을 맞춘다
weak ⟺ n ≥ cell_min_items AND cell_delta_pp ≤ relative_cut_pp   # relative_cut_pp=−15 → "−15%p 이하"
```

§4.1의 `gap`(비율, severity 전용)과 위 `cell_delta_pp`(%p, 판정 전용)는 서로 다른 변수다 — 혼동 금지.

### 4.1 severity 공식 `[잠정]`

```
gap      = max(0, student_overall_acc − cell_acc)          # 자기 기준 상대 결손
severity = min(1.0, gap / 0.30)                            # 30%p 결손에서 포화 [잠정]
```

- `weak` 셀에만 부여(0 < severity ≤ 1). 동일 입력·동일 `config_version`이면 동일 값 — 부동소수 연산 순서 고정(정렬된 셀 순회).

## 5. 노드 귀속과 전파 — diagnoser.py 로직

### 5.1 셀 → 노드 귀속 (간접 매핑) — `suspect`

각 노드는 `(area_tag, type_affinity)` 조합으로 셀에 귀속. 귀속 셀 중 하나라도 `weak`면 노드는 **`suspect`**(용의 상태 — 현 해상도로 확정 불가함을 정직하게 표기).

**suspect severity(확정):** 노드가 여러 weak 셀에 연결되면 `node_severity = 연결된 weak 셀 severity의 최댓값` — 평균을 쓰면 강한 약점이 정상 인접 셀에 희석되므로 최댓값을 쓴다.

**suspect 사용 제한(불변식):**
- 화면에 "약점"으로 확정 표시 금지(verdict 그대로 전달, 표시 구분은 프론트).
- 확정 약점 기반 출제 근거로 사용 금지 — 유일한 용도는 **탐색 출제**(§7 우선순위 3, `diagnostic_purpose` 플래그 필수·승인율 분리 집계).

### 5.2 직접 매핑 — `weak_confirmed`

`skill_node_id` 보유 제출분은 노드 단위 직접 집계 — `n ≥ node_min_items`(기본 6 `[잠정]`)부터 `suspect`를 `weak_confirmed`/`ok`로 승격·해소.

**직접 판정 기준·severity(확정 — 셀과 동일 규칙):**
- weak 판정: `node_delta_pp = (node_acc − student_overall_acc) × 100`이 `relative_cut_pp` 이하(node_delta_pp > relative_cut_pp면 weak 아님) — 셀 판정(§4)과 동일한 상대 기준·동일 단위(%p).
- `weak_confirmed`의 severity는 **비율 gap으로 재계산**: `node_ratio_gap = max(0, student_overall_acc − node_acc)`, `node_severity = min(1.0, node_ratio_gap / severity_saturation)` — §4.1의 셀 severity 공식과 동일한 스케일(0~1 비율)이며, 판정에 쓰는 `node_delta_pp`(%p)와는 별개 값이다. 직접 데이터가 충분하면 간접(suspect) severity를 대체한다(직접 > 간접).
- 직접 `ok`: 역전파의 **출발점에서 제외**하고, 다른 노드로부터의 **유입 추정도 기각**한다 — 직접 관측이 추정보다 우선.
- 직접 표본 부족(`n < node_min_items`): 직접 severity를 만들지 않는다 — 간접 suspect가 있으면 간접 verdict·severity 유지, 간접 근거도 없으면 노드 결과를 만들지 않는다.

### 5.3 직접·간접 병합 규칙 (직접 > 간접)

| 간접(셀) | 직접(노드) | 최종 verdict | 이유 |
| --- | --- | --- | --- |
| suspect | (데이터 없음) | `suspect` | 간접뿐 — 확정 불가 |
| suspect | weak | `weak_confirmed` | 직접이 확증 |
| suspect | ok | `ok` | **직접이 간접을 기각** |
| (weak 셀 없음) | weak | `weak_confirmed` | 직접 단독 성립 |
| suspect | n < node_min_items | `suspect` 유지 | 직접 표본 부족 |

**예제:** `lang.phoneme_change`(language, [concept, fact]) — `language×concept` 셀 weak → suspect. 이후 B 출제 8문항 중 7 정답(n≥6) → 직접 ok → 최종 `ok`.

### 5.4 역전파 (root cause 후보)

```
for node in weak_confirmed ∪ suspect (위상 역순):   # 직접 ok 노드는 집합에서 제외 — 출발점 불가
    for prereq in node.requires_parents:      # builds_on 포함 · related 제외
        prereq.propagated_score += node.severity × edge.weight × decay
        # node.severity: suspect = 연결 weak 셀 최댓값(§5.1) · weak_confirmed = 직접 재계산(§5.2)
        # decay = 0.7^거리 [잠정] · suspect 출발은 간접 감쇠 0.5 [잠정] 추가
propagated_score ≥ 0.5 [잠정] → prereq를 root_candidate로 표기
단, prereq가 직접 데이터로 ok 확정이면 유입 전파 기각 (데이터 > 추정 — §5.2)
```

**불변식:** ① 전파는 `requires`·`builds_on` **역방향만**(순방향 금지 — 선수가 약하다고 후행을 확정하지 않는다) ② `unknown`은 전파 출발점 불가(추정 위에 추정 금지) ③ 모든 verdict에 근거 셀/문항 참조 동반(공용 EvidenceRef).

### 5.5 산출 스키마 (WEAKNESS_MAP — [`02_design.md`](02_design.md) §2)

```json
{
  "graph_version": "0.1.0",
  "taxonomy_version": "v1",
  "config_version": "b-defaults-v1",
  "snapshot_hash": "sha256:...",
  "cells": {"language×concept": {"acc": 0.42, "n": 14, "verdict": "weak", "severity": 0.57}},
  "nodes": {"lang.phoneme_change": {"verdict": "suspect", "basis": ["cell:language×concept"]}},
  "propagated": {"lang.phoneme_system": {"score": 0.63, "verdict": "root_candidate",
                  "from": ["lang.phoneme_change"]}}
}
```

버전 3종(graph·taxonomy·config)과 snapshot_hash는 행 컬럼으로도 저장(재현 조회 키 — §3.2).

**taxonomy_version 정본 규칙:** 실행 단위의 정본은 `ExecutionContext.versions.taxonomy_version`이다. `WeaknessMap.taxonomy_version`은 진단 산출 당시 실행 버전의 **에코**이며, 같은 실행에서 두 값은 반드시 같아야 한다. 불일치는 **워크플로 입력 조립 단계에서 결정론적으로 거부**한다 — 임의 보정·최신 버전 자동 변환 금지. 이 규칙은 개별 Pydantic 모델이 아니라 워크플로 구현이 강제한다(출제 측 대응 규칙은 [`05_problem_generation.md`](05_problem_generation.md) §4.1).

## 6. YAML 스키마

```yaml
# src/ai/diagnosis/data/curriculum_graph.yaml
meta:
  graph_version: "0.1.0"          # semver — 노드 추가=minor, 엣지 의미 변경=major
  taxonomy_version: "v1"          # contracts/taxonomy.py 버전 — 로드 시 대조
  updated: 2026-07-15

nodes:
  - id: lang.phoneme_system       # 전역 유일 · <area 접두>.<snake> 규약
    label: "음운 체계"
    area_tag: language            # taxonomy enum만 — 미정의 값은 기동 실패
    type_affinity: [concept]      # 이 노드를 주로 측정하는 type_tag (1..*)
    level: 1                      # 사다리형만 사용 · DAG는 생략 가능
    desc: "자음·모음 체계, 음운의 개념"

edges:
  - from: lang.phoneme_system     # from을 알아야 to를 학습 가능
    to: lang.phoneme_change
    kind: requires                # requires | builds_on | related
    weight: 1.0                   # 전파 감쇠 계수 (related는 전파 제외)
```

**로드 시 검증(skill_graph.py — 기동 실패 조건):**
1. `area_tag`·`type_affinity` 값이 전부 taxonomy enum에 존재
2. `requires`·`builds_on` 엣지만으로 무순환(위상 정렬 실패 = 기동 실패)
3. 노드 ID 유일 · 엣지 양단 실존 · `taxonomy_version` 일치
4. `related` 엣지는 전파에서 제외(표시·추천 참고용) — 검증은 실존만

## 7. 출제 입력 인터페이스 — 타겟 셀·노드 선정

problem_generation의 약점 목표 확정이 받는 우선순위(`target_source=weakness_auto`일 때):

1. `root_candidate` 노드 (근본 결손 후보 — 확인 출제)
2. `weak_confirmed` 노드
3. `suspect` 노드 (**탐색 출제만** — `diagnostic_purpose` 플래그 필수)
4. `unknown` 셀 (표본 확보 출제 — 강사 옵션, 기본 off)

`target_source=teacher_manual`이면 진단을 거치지 않고 강사 선택 노드/셀을 그대로 목표로 — 비개인화 표기 필수([`05_problem_generation.md`](05_problem_generation.md) §4.1). `ProblemRequest.target: cell | node | auto`.

## 8. OPEN 항목

| 번호 | 항목 | 담당 |
| --- | --- | --- |
| B-3 잔여 | 경계 사례 7건 판정(enum은 확정) → taxonomy_version 갱신 | A+B |
| **W7** ✅ **해소(2026-07-27)** | **지원 학년 = 고1·고2·고3 전원, 수능 전용**(내신 제외 — [`05`](05_problem_generation.md) §9의 short·essay 예약 근거를 강화한다). **그래프는 학년별로 분할하지 않고 단일 그래프로 쓴다** — 음운·단어·문장·담화·국어사의 선수 관계는 국어학적 사실이라 학년과 무관하며, 학년별 복제는 `graph_version` 관리를 3배로 만들고 역전파 결과를 갈라놓는다. §2의 "수능 고등 기준 단일"을 그대로 유지한다 | B |
| — `[후속·먼 미래]` | 수능 국어 체제 개편(선택과목 폐지)이 실제로 적용되는 시점에 그래프 출제 범위 필터와 `subject_track` 유도 규칙을 재론한다. **v1 범위 밖이며 지금 대비 코드를 만들지 않는다** | 기획+B |
| — | GraphRAG projection — 본 그래프는 **YAML이 정본**이고 검색용 그래프는 파생물이다. `graph_version`은 이 그래프의 버전이며 콘텐츠 GraphRAG 버전으로 **재사용하지 않는다** | B ([`11`](11_graphrag_knowledge_layer.md) §2.1·§7) |
| — (09 §2-6) | `[잠정]` 파라미터(−15%p·decay 0.7·임계 0.5·node_min_items 6·severity 포화 0.30·간접 감쇠 0.5)는 06 부록 'B 기본값 시트'로 관리 | B |
| — (09 §2-6) | 문법 DAG 25~40노드 실제 YAML 제작 | B |
