# 5차 실 LLM 스모크 — 범위표 (실행 **전**에 작성)

**2026-08-09 · 브랜치 `docs/llm-smoke-5th` · base `a955ef8`(#151)**

> 🔴 **이 표는 스모크를 돌리기 전에 만들었다.** 돌리고 나서 만들면 **결과에 맞춰 범위를 쓰게 된다.** 커밋 순서가 그 증거다(이 파일이 리포트보다 앞 커밋).

---

## 🔴 결론 먼저 — counsel 스모크가 보는 것은 **0개**다

4차(8/7) 이후 코드가 바뀐 PR이 여섯인데, **5차가 확인할 수 있는 변경은 없다.** 지시서는 *"그중 하나뿐(`generation_params`)"* 으로 봤는데 **실측이 그것도 뒤집었다** — 러너가 그 필드를 **안 읽는다**(아래 ②).

⇒ **5차의 값은 「변경 검증」이 아니라 「회귀 없음 확인」이다.** 그 둘은 다르고, 리포트에 그렇게 적어야 한다.

---

## 범위표 (실측 8/9)

| # | 변경 | 어디 | 5차가 보는가 | 근거(실측) |
| --- | --- | --- | --- | --- |
| **#143·#144** | classify 캐시 히트가 원장을 남긴다 | `api/routers/classify.py` | 🔴 **범위 밖** | 러너에 `classify` 문자열 **0건** — S1~S5 어느 시나리오도 분류를 안 부른다 |
| **#144** | `generation_params` 사용 축 (counsel·detect·classify) | 세 곳 | 🔴 **못 본다** | `_run_s4`가 읽는 것은 `observers`(= `_CountingProvider.observed`), 즉 **LLM_CALL 레벨**이다. `generation_params`가 사는 **AI_RUN 행을 안 읽는다** — 러너 전체에 `run_store` 참조 **0건**(import 한 줄 제외) |
| **#146** | plan `REDACTION_BLOCKED` | `composition/counsel/graph.py` | 🔴 **못 본다** | 마스킹 불확실은 **자연 발생을 기다릴 수 없다**(4차에 `RedactionBlockedError` **0건**). 대역으로만 본다 — **결함이 아니라 성질**이다 |
| **#147** | 문 앞 400 · pg `generation_params` | `problem_generation/` | 🔴 **범위 밖** | 러너에 `problem` 문자열 **0건**. pg는 **별도 러너**다 — `tests/ai/integration/test_pg_real_llm_smoke.py`(`pytestmark = pytest.mark.integration` · `addopts = "-m 'not integration'"` 라 **기본 실행에서 제외**) |
| **#149·#151** | 문서·docstring | — | 무관 | 코드 동작 무변경 |
| **#150** | 대조 테스트 | `tests/` | 무관 | CI가 본다 |

**⇒ 여섯 중 확인 가능: 0 · 범위 밖: 2 · 못 봄: 2 · 무관: 2**

---

## 🔴 범위 밖인 것을 무엇으로 봐야 하나

| 축 | 보는 방법 |
| --- | --- |
| **classify**(#143·#144) | 이 러너에 S6를 붙이거나 **별도 러너**. ⚠ 캐시 히트를 보려면 **같은 `inquiry_ref`로 두 번** 불러야 하는데 그건 시나리오 설계가 필요하다 |
| **pg**(#147) | `LLM_PROVIDER=openai_compat uv run pytest -m integration tests/ai/integration/test_pg_real_llm_smoke.py`. 선례 리포트: `docs/handoff/2026-08-05_pg_real_llm_smoke.md` |
| **`generation_params`**(#144) | 🔴 **러너가 AI_RUN을 읽게 해야 한다** — 지금은 LLM_CALL만 본다. 이 PR에서 그 관측을 추가할지는 판정 대상 |
| **`REDACTION_BLOCKED`**(#146) | 실 LLM으로는 **못 본다.** 대역 테스트가 이미 있다(`_plan_outcome_harness`의 `redaction_blocked` 시나리오 · #150) |

---

## 🔴 5차가 증명하지 못하는 것 (BE 연결 판단의 근거)

⚠ **범위를 넓게 적으면 BE가 안 본 축까지 안전한 것으로 읽는다.**

1. **`REDACTION_BLOCKED`(#146)의 도달은 여전히 미실증이다.** 자연 발생을 기다릴 수 없고 대역으로만 본다 — **결함이 아니라 성질**이다.
2. **classify 축을 안 본다.** 캐시 히트의 원장 적재(#143)·`generation_params` 사용 축(#144)이 실 경로에서 확인되지 않았다.
3. **pg 축을 안 본다.** 문 앞 400·pg `generation_params`(#147)는 별도 러너가 필요하다.
4. **서킷이 안 밟힌다**(99 #08) — 스모크의 S2도 라우터를 타므로 **N=1**이고, 연속 실패가 최대 1인데 임계는 3이다.
5. **`AI_RUN` 행을 안 본다** — 러너는 LLM_CALL만 수집한다. `generation_params`·`prompt_version`·`input_snapshot_hash`가 원장에 어떻게 찍히는지는 이 스모크로 확인되지 않는다.

### 🔴 「5차 통과」가 뜻하는 것

> **counsel·briefing 축에 회귀가 없다.**
> **전 축이 안전하다는 뜻이 아니다** — classify·pg는 안 봤고, `REDACTION_BLOCKED`·서킷·AI_RUN 필드는 이 스모크의 관측 범위 밖이다.

---

## 4차 기준선 (대조용)

```
S1  21/21 1차 통과 · 재생성 0 · 폴백 0
    outcome ok 32/32
    evicted_runs 0
    마스킹 토큰 잔존 0
    RedactionBlockedError 0건
⇒ 3차 이후 20커밋 회귀 없음
```

⚠ **S5(재현성) diff를 회귀로 읽지 마라** — LLM 경로는 `seed`가 서버 best-effort라 바이트 동일이 보장되지 않는다(99 ㊼ · 04 §2.2).
⚠ **게이트·프롬프트·금칙어를 스모크 통과를 위해 손대지 않는다** — 걸리면 걸린 대로 싣는다(러너 머리말).

---

## 실행 조건

```
LLM_PROVIDER=openai_compat uv run python -m ai.evaluation.counsel_llm_smoke --date 2026-08-09
```

- `--date` **필수**(기본값 없음 · 99 ⓝ②) · 같은 날 재실행은 `-N` 접미
- 저장 백엔드 **`memory` 고정** — 측정 변수는 LLM 하나다
- 추적 4종이 켜져 있으면 **시작 전에 멈춘다**
- 🔴 **키가 준영님 것이라 통보 후에 돈다**
