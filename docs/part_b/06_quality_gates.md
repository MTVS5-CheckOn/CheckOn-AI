# [체크온] 3단계 품질 게이트 명세 v1 — RuleValidation · BlindCrossSolve · ReleaseDecision

> **지위:** member-B(염준영) 공식 명세 v1. `src/ai/problem_generation/verification.py`·`cross_solver.py`의 사양 원본. A의 threshold 시트에 대응하는 B의 판정 파라미터 문서 — `[잠정]` 값은 부록 'B 기본값 시트'와 `golden/problems/` 버전 연동으로 관리.
>
> **변경 이력**
> - v1.1 (2026-07-15): 파일 번호 이동(04→06) + 확정 반영 — ① **재시도 총 3회**(item_attempt 공통 예산, regen_max=2 — 최악 논리 콜 6·HTTP 12) ② 게이트 ②에 **약점 의미 정렬 판정** 포함 ③ **세트 조기 중단** 규칙(§6) ④ 검증 차단 문항 **수동 예외 승인 불허 확정** ⑤ 프론트 표시 라벨 매핑(§0) ⑥ v1 mcq만.
> - v1 (2026-07-15): 입력 초안 `CODEXPROMPT/(염준영)_3중_검증_게이트_명세_v0.md` 정리 — 명칭 통일(RuleValidation→BlindCrossSolve→ReleaseDecision), fail-closed, '다른 모델 패밀리', 재시도 2층 분리, "완전 증명" 표현 삭제.
>
> **위치 확인:** 게이트 3단은 problem_generation workflow 노드로 **자체 실행**하되, 기록은 `contracts/gates.py`의 공용 `GateResult` 사용. A 소유 `gates/chain.py` 무변경. **공용 enum 확장:** `owner_kind`+=`problem_set` · `gate_name`+=`RuleValidation|BlindCrossSolve|ReleaseDecision` — ✅ **A+B 승인·`d5283d0` 구현·공용 ERD 문서 동기화 완료**([`09_integration_proposals.md`](09_integration_proposals.md) §2-3·§2-10). 잔여: `EVIDENCE_ITEM.owner_kind`+=`problem_item`만 `evidence/models.py` 구현 시 양자 승인.
>
> **참조** — [`05_problem_generation.md`](05_problem_generation.md)(입출력 계약) · [`01_pipeline.md`](01_pipeline.md) §3 · [`07_refine_policy.md`](07_refine_policy.md)(수정 시 재실행) · [`08_evaluation_plan.md`](08_evaluation_plan.md)(골든셋)

---

## 0. 3단 개요 — 무엇을 누가 막나

| 단 | 이름 | 방식 | 막는 것 |
| --- | --- | --- | --- |
| ① | **RuleValidation** (R-1~R-7) | 결정론 코드 | 구조 결함·근거 부재·금칙·중복 — 기계가 확실히 잡는 것 전부 |
| ② | **BlindCrossSolve** (+정렬 판정) | 다른 모델 패밀리 LLM(blind) + 코드 대조 | 정답 유일성·풀이 가능성·**목표 약점 측정 여부** — 기계 단독으로 못 잡는 것 |
| ③ | **ReleaseDecision** | 결정론 코드 | 공개 가능성 판정 `pass | needs_review | reject` — "통과 ≠ 무결" 선언 |

순서 고정 ①→②→③. ①에서 떨어진 문항은 ②로 가지 않는다(LLM 비용은 구조가 멀쩡한 문항에만). **어느 단도 문항을 승인하지 못한다 — 승인은 오직 강사(백엔드 HITL).**

**판정 → 내부 상태 → 프론트 라벨 매핑** (라벨 문구는 `[제안 — FE 확정]`. 라벨은 프론트 장식이 아니라 AI가 반환하는 구조화 상태의 표시다):

| ReleaseDecision | PROBLEM_ITEM.status (내부) | 사유(drop_reason 등) | 화면 라벨 `[제안]` |
| --- | --- | --- | --- |
| `pass` | `verified` | — | **검증 통과** |
| `needs_review` | `needs_review` | 배지 사유(§5) | **검토 필수** — 검증 완료 + 사람이 자세히 볼 것 |
| `reject` | `dropped` | `generation_exhausted` / `banned_topic` | **생성 실패** |
| (판정 불가) | `verification_unavailable` `[상태 제안]` | timeout·parse_fail·`source_unverified`·기준 자료 장애 | **검증 차단** — 발행 불가, 재검증 필요 |

`needs_review`(검증 절차 완료 — 사람이 더 볼 것)와 `verification_unavailable`(검증 절차 자체 미완 — **우회 불가**)는 다른 상태다. 부가 라벨(근거 대조 완료=R-1 · 사전 대조 완료=T1 R-1 · 교차 풀이 일치=② · 난이도 추정)은 검증 상세 화면용 파생 표시 — 상세에는 실행 게이트·사유·근거 위치·풀이 결과와 confidence·버전 세트·검증 시각을 노출한다.

한계 선언: 코드 게이트는 **구조를 검사**한다. **의미적 유일성**은 코드가 증명할 수 없다 — BlindCrossSolve와 강사 검토가 보완한다.

## 1. 게이트 ① RuleValidation — R-1~R-7 (결정론)

파싱 탈락(구조화 출력 위반 — 05 §4)은 이 게이트 **이전**이며 item_attempt만 소모한다:

| ID | 이름 | 검사 | 실패 시 |
| --- | --- | --- | --- |
| **R-1** | 근거 실존 | `evidence`의 모든 anchor 해소 가능: `passage_span` 좌표 실존 + `quote` 원문 일치(정규화 후 완전 일치) · `dict_entry`/`grammar_rule` ID가 T1 기준 자료(05 §1.1)에 실존 · `work_span` 오프셋이 발췌 범위 내 | 재생성 |
| **R-2** | 정답 정합 | 정답 번호 1개, 선지 5개 상호 배타(정규화 문자열 비교까지 — 의미 중복은 ②가 커버) | 재생성 |
| **R-3** | 발문 형식 | stem에 정답 유출 어구 없음 · 부정 발문 강조 표기 · type_tag별 발문 형식 규칙 | 재생성 |
| **R-4** | 외부 지식 차단 | rationale·정답 성립이 지문/예문/발췌 **내부에서 완결** — anchor 없는 핵심 주장, 지문 밖 고유명사·수치가 근거로 등장 시 실패(05 §2.1의 집행부) | 재생성 |
| **R-5** | 금칙 대조 | stem·choices·rationale·지문 전체를 `pg_banned_topics.yaml` 대조 | **즉시 폐기(reject)** — 재생성 우회 금지, 지문 오염이면 세트 중단 |
| **R-6** | 중복 억제 | stem 정규화 해시 + n-gram 유사도가 동일 세트/동일 학생 최근 출제분과 `dup_similarity_max` 초과 (**내부 중복만** — 외부 표절은 P0 OPEN, 05 §8.3) | 재생성 |
| **R-7** | taxonomy·목표 정합(코드) | area·type·item_format·skill_node_id가 요청과 일치 + enum 유효 + 노드 area와 문항 area 일치 — **정렬 3층 중 1층(메타)** | 재생성 |

**T1 특칙:** R-1의 사전·문법 대조가 사실상 정답 검증을 겸한다 — T1은 ②를 경량 모드(1회 풀이·불일치 시 needs_review)로 운용 가능 `[잠정 — 파일럿 오류율 확인 후]`. 단 **기준 자료 장애 시 T1도 발행 차단**(05 §1.1).

## 2. 게이트 ② BlindCrossSolve — 교차 풀이 + 약점 의미 정렬 (확정)

**목적 2가지로 한정:** ⑴ 정답이 유일하게 성립하며 지문만으로 풀리는가 ⑵ **문항 내용이 실제로 목표 약점을 연습·측정하는가**(정렬 3층 중 2층 — 의미). 교육적 품질(좋은 문항인가)은 측정하지 않는다 — 그건 강사(3층)와 골든셋의 몫.

```
verification.py가 blind 페이로드 조립
  (answer·rationale·evidence 물리 제거 · 목표 메타는 제공 — 05 §4.3)
 → gateway: ModelRole.verifier · 다른 모델 패밀리 강제 · temperature 0
 → SolveResult { chosen, confidence, multiple_answers_possible,
                 aligned, alignment_confidence, measured_skill_node_id, alignment_reason }
 → 코드 대조 (판정 순서 고정):
    [풀이]  불일치                             → 재생성 ("선지 N도 성립" 구조화 주입)
            multiple_answers_possible          → 재생성
    [정렬]  aligned = false                    → 재생성 ("측정 대상=X" 사유 주입)
    [통과]  일치 + aligned
            confidence ≥ cross_confidence_high
              AND alignment_confidence ≥ alignment_confidence_min   → PASS
            둘 중 하나라도 경계값 미만                               → PASS + low_confidence 마크 (③에서 배지)
    [불능]  verifier 호출 실패(timeout·parse_fail) → 전송 재시도 1회 → 소진 시 §3
```

**불변식:** ① **검증 불능은 절대 통과·발행으로 처리하지 않는다(fail-closed — §3)** ② verifier reasoning은 VERIFICATION_RESULT.detail에 저장하되 강사 화면 해설로 미노출(해설은 generator rationale만 — 출처 단일화) ③ 재생성 후 문항은 ①부터 다시(②만 재시도 금지) ④ `alignment_confidence`가 낮다고 자동 통과 금지 — 경계는 배지, 명백 불일치는 재생성.

## 3. 검증 불능 — `verification_unavailable` (fail-closed · 수동 예외 불허 확정)

verifier 장애·기준 자료 장애·사실검증 수단 부재(`source_unverified`)로 ②를 완료하지 못한 문항:

- **저장은 가능** — 생성 비용을 버리지 않는다. 상태는 `verification_unavailable` `[공용 상태 사전 제안 — 09 §2-2]`.
- **발행은 차단** — 성공적 재검증 전까지 학생 노출 불가, 강사 승인 대상 목록에도 미등재. **강사 지시·일반 승인·"직접 검토 완료" 확인 어떤 경로로도 우회 불가**(확정 — 수동 예외 승인 불허). 완화 수단은 게이트웨이의 verifier 폴백 패밀리.
- 재검증: 장애 해소 후 저장본에 ②를 재실행(배치·수동 트리거) — ① 재실행 불요(입력 불변), 성공 시 ③으로 진행.
- 장애가 세트에 걸치면 조기 중단 판정(§6)과 연동 — "잠시 후 재검증" 문구는 백엔드 폴백 규약(error_codes §6).
- **결과 계약:** `verification_unavailable`은 검증을 통과했다는 뜻이 아니지만, 문항 상태를 정직하게 저장한 정상 도메인 결과다. 세트는 성공 문항 수에 따라 `partial_success|failed`로 확정하며, 결과 레코드를 확정한 워크플로를 이 상태만으로 실행 실패 처리하지 않는다.

## 4. 재시도 예산 — item_attempt 공통 예산 (확정: 총 3회)

| 층 | 소유 | 상한 | 의미 |
| --- | --- | --- | --- |
| **item_attempt**(생성 시도) | workflow (B) | **문항당 총 3회 = 최초 1 + 재생성 2** (`regen_max=2`) | 파싱 탈락·정렬 실패·규칙 실패·교차 불일치가 **전부 같은 예산 소모** — 검사별 중첩 루프 금지 |
| **전송 재시도** | gateway (공용) | 논리 콜당 1회 | 네트워크·일시 오류 — 동일 페이로드 재전송(멱등 조건) |

**최악 호출 수(문항 1개):**

```
논리 콜:    generator 3 + verifier 3                = 6콜
전송 요청:  generator 3×(1+1) + verifier 3×(1+1)    = 최대 12 HTTP 요청
```

- 비용 상한·타임아웃 예산은 **전송 요청 수(12) 기준**으로 계산. 세트당 상한 = `count × 문항당 최악` — 조기 중단(§6)이 실제 지출을 더 낮춘다.
- verifier 콜 ≤ generator 콜(3:3) — 검증 비용이 생성 비용을 초과하지 않는 자연 상한.
- 재생성과 전송 재시도의 회계 분리 — `LLM_CALL.outcome`으로 구분 기록. LLM 원가는 gateway가 기록(쿼터 아님 — 7/15).

## 5. 게이트 ③ ReleaseDecision — 판정 조건

①② 통과 문항 중 하나라도 해당하면 `needs_review`(+`review_badge=true`):

| 조건 | 근거 |
| --- | --- |
| ② low_confidence 마크(풀이 또는 정렬 confidence 경계) | 맞혔지만 확신 낮음 = 경계 문항 |
| 경계 판정의 낮은 confidence 또는 source/passage와 measured area 불일치 | taxonomy §2의 확정 원칙에 따라 자동 확정하지 않고 강사 확인 |
| T3(문학) 전체 `[잠정 — 파일럿 초기]` | 작품 해석 개입 리스크 — 승인율 축적 후 완화 논의 |
| 탐색 출제(suspect·unknown 타겟) | [`04_curriculum_graph.md`](04_curriculum_graph.md) §7 — 진단 목적 플래그 |
| 수동 목표 세트(`teacher_manual`) 첫 문항 `[잠정]` | 비개인화 출제 — 목표 적합성을 사람이 확인 |

배지는 강사 검수 UI의 우선 표시용이며 워크플로를 막지 않는다. 배지 문항 승인율은 분리 집계(KPI 오염 방지). ※ `essay` 무조건 배지 규칙은 v1 서술형 폐기(7/15)로 예약 이관.

## 6. 실패 예산·세트 판정·조기 중단 (확정)

- 문항 상태: `verified` / `needs_review` / `dropped` / `verification_unavailable`(§3).
- **조기 중단(중단 후 남은 생성 스킵):**

| 트리거 | 기준 `[잠정]` | stop_reason |
| --- | --- | --- |
| 폐기 비율 | dropped / 처리 문항 > `set_drop_ratio_max`(0.3) 이면서 처리 ≥ 3문항 | `drop_ratio_exceeded` |
| 검증 장애 연속 | `verification_unavailable` 연속 `verify_outage_streak_max`(3) | `verifier_outage` |
| 시간 상한 | 비동기 총 상한(계약 §2.4, 5분) 도달 전 자체 데드라인 초과 | `time_budget_exceeded` |
| R-5 지문 오염 | 금칙이 지문 단위 | `banned_topic_passage` |

- 수량 불변식: `processed_count == len(items)` · `requested_count == processed_count + unstarted_count`. 최종 결과에 미처리 문항이 남으면 `unstarted_count > 0`과 `stop_reason`을 함께 기록한다.
- 세트 최종 상태: 전부 처리하고 전부 성공 `generated` · 성공이 하나 이상이고 실패 또는 미처리 문항이 있으면 `partial_success` · **성공 0개 `failed`**. `verification_unavailable`은 세트 성공 문항으로 세지 않지만 누락하지 않고 `items`에 포함한다. **조용한 수량 미달 금지** — 실패·미처리 문항을 숨기지 않는다.
- `partial_success|failed`는 결과를 확정한 정상 워크플로의 도메인 상태다. 체크포인트나 결과 자체를 기록하지 못한 실행 장애와 구분한다.
- 재생성 소진(`generation_exhausted`)과 R-5 폐기는 사유 분리 표기 — 금칙 오염은 품질이 아니라 안전 문제.
- 중단돼도 완료분은 보존(체크포인트) — 강사는 성공분으로 선별을 시작할 수 있다.

## 7. 기록 — GateResult · VERIFICATION_RESULT 이중 기록의 역할 분담

- `GATE_RESULT`(공용): 단 단위 요약 — `owner_kind=problem_set`, `gate_name=RuleValidation|BlindCrossSolve|ReleaseDecision` `[✅ 승인·구현 완료 — d5283d0]`. 운영 대시보드에서 A 파트와 동일 조회면.
- `VERIFICATION_RESULT`(B 전용): 문항×시도 단위 상세 — 실패 규칙 ID·verifier 풀이·confidence·**정렬 판정**. 골든셋 증보·프롬프트 회귀의 재료.
- 중복이 아니라 그레인 차이 — 요약은 공용 규격, 상세는 B 내부.

## 부록. B 기본값 시트 (v1 default — 전부 `[잠정]`)

A threshold 시트 방식 준용: `verify_config` 버전 행(이전 버전 보존) + 개정 시 `golden/problems/` 동시 개정이 머지 조건. `WEAKNESS_MAP.config_version`이 이 시트 버전을 가리킨다.

| 키 | 값 | 소비처 |
| --- | --- | --- |
| `regen_max` | **2** (총 시도 3회 — 확정) | §4 item_attempt |
| `transport_retry` | 1 | gateway 전송 재시도(§4) |
| `dup_similarity_max` | 0.8 | R-6 |
| `cross_confidence_high` | 0.8 | ② 풀이 판정 |
| `alignment_confidence_min` | 0.7 | ② 정렬 판정(경계 미만 = 배지) |
| `set_drop_ratio_max` | 0.3 | §6 조기 중단 |
| `verify_outage_streak_max` | 3 | §6 조기 중단 |
| `t1_light_mode` | true | §1 T1 특칙 |
| `diag_relative_cut_pp` | −15 | 진단 weak 판정(04 §4) |
| `diag_decay` | 0.7 | 역전파 감쇠(04 §5.4) |
| `diag_propagate_threshold` | 0.5 | root_candidate 임계(04 §5.4) |
| `diag_severity_saturation` | 0.30 | severity 포화(04 §4.1) |
| `diag_suspect_damping` | 0.5 | suspect 전파 감쇠(04 §5.4) |
| `node_min_items` | 6 | 직접 판정 최소 표본(04 §5.2) |
| 난이도 가중치 5종 | 05 §6 표 | 난이도 추정 |

※ `cell_min_items`는 이 시트가 아니라 **A threshold 시트 R6 값 참조**(04 §4 — 어휘 통일, 이중 관리 금지).

**섀도 모드 절차(파일럿 첫 2주 — A threshold 시트 §5 준용):** `verify_config` v1 값은 첫 2주 개정 금지 — 게이트 판정·confidence 분포·R-4 오탐률·정렬 오판률을 관측만 하고, 개정은 관측치+골든셋 동시 개정으로만. 난이도 추정도 같은 기간 섀도 대조만.

## 8. OPEN 항목

| 번호 | 항목 | 담당 |
| --- | --- | --- |
| D-03 | T1 기준 자료 확정 — R-1 대조 선결 | B+기획 |
| D-06 / B-5 | verifier 모델 패밀리 벤치마크·공급자 조합·폴백 패밀리 | A+B |
| — | R-4 휴리스틱·정렬 판정 오탐률 — 파일럿 측정 후 보강 | B |
| B-3 완료 | 경계 사례 7건 측정 대상 기준 확정(`taxonomy` §2) | A+B |
