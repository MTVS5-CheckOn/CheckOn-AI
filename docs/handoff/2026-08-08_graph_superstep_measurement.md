# 세 그래프의 super-step 실측 — 유도식을 쓰기 **전에**

**2026-08-08**(`date`·`git log --date=short` 확인) · A(박진희)

> 🔴 **이 표를 채우고 나서 유도 함수를 썼다.** 커밋 순서가 증거다. 유도식을 먼저 쓰고
> 그래프를 맞추면 **정상 실행을 자르는 상한**이 나온다.
> 🔴 **pg 유도식을 복사하지 않았다** — 그래프 모양이 셋 다 다르다.

---

## 0. `StateGraph`는 셋이다

```
src/ai/composition/counsel/graph.py:333       counsel    ← A · 이 PR
src/ai/import_mapping/probe/graph.py:113      probe      ← A · 이 PR
src/ai/problem_generation/application/workflow.py:479   pg  ← B · #156에서 닫힘
```

⚠ 99 #08 ⓑ가 **counsel 하나만** 말하고 있었다. *"전수 0건"* 이라 적었는데 **분모를 안 적어서** 남은 것이 하나로 읽혔다.

---

## 1. 그래프 모양 (코드를 열어서)

| | counsel | probe |
| --- | --- | --- |
| 노드 | `plan` · `student` · `summarize` | `profile_read` · `probe` · `propose_spec` · `confidence_check` |
| self-loop | `student --_route--> student` | `probe --_route--> probe` |
| 루프 종료 조건 | `state.is_complete`(= `cursor` 도달) | `not candidate_columns(state) or state.loop_count >= loop_max` |
| 🔴 루프가 super-step을 먹는가 | **학생 루프만.** 게이트 재생성은 `student` 노드 **안의 파이썬 루프**(`for _ in range(regen_max + 1)` · `graph.py:206`)라 **안 먹는다** | **먹는다.** `probe` 노드가 `loop_count`를 올리고(`:85`) `_route`가 그걸 본다(`:56`) |
| 상한 입력의 출처 | 🔴 **실행 시점 번들 크기**(`len(bundle.contexts)`) — 계약 상한이 없다 | **설정** `ImportSettings.import_probe_loop_max`(기본 5 · `ge=1`) |

---

## 2. 🔴 실측 — `recursion_limit`을 1부터 올려 `GraphRecursionError`가 사라지는 최소값

### counsel

| 학생 수 N | 실측 super-step |
| --- | --- |
| 1 | **4** |
| 2 | **5** |
| 5 | **8** |
| 20 | **23** |

⇒ **`N + 3`**

### probe

| 입력 | `loop_max` | 실측 super-step |
| --- | --- | --- |
| 컬럼 5개 | 1 | **5** |
| 컬럼 5개 | 2 | **6** |
| 컬럼 5개 | 5 | **9** |
| 컬럼 5개 | 6 | **9** ⚠ 후보 소진으로 조기 수렴(5회만 돌았다) |
| 컬럼 12개 | 5 | **9** |
| 컬럼 12개 | 12 | **16** |

⇒ **`loop_max + 4`** (상한이 실제로 물릴 때. 후보가 먼저 소진되면 그보다 적다)

---

## 3. 🔴 손셈이 둘 다 1 작았다 — 실측이 이겼다

```
counsel  손셈: plan + N + summarize                        = N + 2
         실측:                                              = N + 3   ← +1
probe    손셈: profile_read + loop_max + propose + confidence = loop_max + 3
         실측:                                              = loop_max + 4   ← +1
```

**두 그래프에서 같은 +1이 나온다** ⇒ 한쪽 오셈이 아니라 **라이브러리 성질**이다 —
`END` 전이가 super-step 하나를 먹는다.

🔴 **유도식을 먼저 썼으면 모든 counsel 실행과 모든 probe 실행이 정확히 1 super-step 모자라
잘렸을 것이다.** 지시서가 *"표를 채우고 나서 코드를 써라"* 라고 한 이유가 이것이다.

⚠ **그래서 여유분을 임의로 얹지 않는다.** 상수는 **센 것**(counsel 3 · probe 4)이고,
노드가 늘면 이 값도 늘어야 한다 — 그걸 `test_derived_limit_admits_the_maximum_normal_run`이
red로 잡는다. 여유를 얹으면 그 red가 안 난다.

---

## 4. 왜 pg와 유도식이 다른가

| | 상한 입력 | 이유 |
| --- | --- | --- |
| pg (B) | `request.count` + `VerifyConfig` | **계약에 최대치가 있다** — 요청이 문항 수를 말한다 |
| counsel | `len(bundle.contexts)` | 🔴 **계약 상한이 없다.** 라우터가 `contexts={student_ref: context}`로 N=1이고 벌크는 미구현이라 *"학생 최대 몇 명"* 이 계약에 없다 ⇒ **실행 시점 번들 크기**에서 유도한다 |
| probe | `ImportSettings.import_probe_loop_max` | 상한이 **이미 설정에 있다**(`ge=1`) |

⚠ **counsel에서 재개 시 `cursor`를 빼서 좁히지 않는다** — 같은 잡의 상한이 실행마다
달라지고(재개가 두 번이면 값이 셋), 불변식 8(재현성)과 충돌한다. **상한은 잡의 성질이지
시도의 성질이 아니다.** 전체 학생 수로 유도한 값은 재개 시 과대 공급이라 안전하다.

---

## 5. ⚠ 위험의 방향 — 「기본값이 낮아 죽는다」가 아니다

```
langgraph/_internal/_config.py:32
DEFAULT_RECURSION_LIMIT = int(getenv("LANGGRAPH_DEFAULT_RECURSION_LIMIT", "10007"))
```

**10007은 사실상 무한이다.** 따라서 위험은 둘이고 종전 등재문(*"기본값이고 버전마다
다르다"*)은 방향이 틀렸다:

1. 🔴 **불변식 6(모든 루프에 상한)의 방어가 없다** — `cursor`가 안 올라가는 버그가 나면
   10007번 돌고 죽는다.
2. 🔴 **저장소 밖에서 바뀐다** — `LANGGRAPH_DEFAULT_RECURSION_LIMIT` 환경변수를 **BE 운영이
   만질 수 있다.** 호출마다 명시하면 그 변수가 우리 값을 못 덮는다.

⚠ **이건 루프를 막는 장치가 아니다** — 진짜 상한은 `cursor` 단조 증가와 `is_complete`가
든다. 여기는 그게 깨졌을 때 걸리는 **마지막 그물**이고, 기준은 *"정상 최대치보다 크고
폭주보다 작게"* 다.
