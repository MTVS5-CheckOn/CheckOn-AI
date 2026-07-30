# 11. LangSmith 트레이스 실측 — `09 §2-16 P1′ 부속 확인` (2026-07-30)

> ⚠ **이 문서는 `langsmith 0.10.2`(uv.lock 고정) 시점의 실측이다 — 버전이 바뀌면 재실측이 필요하다.** 영구 사실로 읽지 말 것. 함께 고정된 버전: `langgraph 1.2.9` · `langchain-core 1.4.9` · Python 3.12.
>
> **왜 실측했나:** `part_b/09_integration_proposals.md` §2-16이 "`(b)` 훅이 덮는 효과 대상은 `[미확정]`"이라고 남겼고, `llm/gateway.py`의 `TraceMaskingHook` docstring도 "LangSmith가 실제 수집하는 span·입출력 필드와 마스킹 효과 대상은 `[미확정]`"이라고 적었다. 추론으로 P2를 설계할 수 없어 켜서 재봤다.
>
> **작성 A(박진희).** 상호참조: `policies/masking_redaction.md`(마스킹 경계) · `policies/langgraph_state.md` §1.2(state 필드) · `src/ai/runtime/trace_masking.py`(이 실측을 근거로 한 훅).

---

## 0. 실측 조건 (재현용)

| 항목 | 값 |
| --- | --- |
| 켠 방법 | **프로세스 환경변수만** — `LANGSMITH_TRACING=true` · `LANGSMITH_PROJECT=checkon-ai-trace-probe`. `.env`는 **무수정**(정본 `LANGSMITH_TRACING=false` 유지) |
| 데이터 | **전부 합성** — `FakeCounselProvider` · `FakeBriefProvider` · alias(`st_probe_1`·`gd_st_probe_1`)·가상 수치(`62%`). 실명·연락처·실제 학생 컨텍스트 **0건** |
| 체크포인터 | `InMemorySaver` (PG 미가용 — PostgresSaver는 **미확인**) |
| 스크립트 | 레포 밖 scratchpad — **커밋하지 않았다**(일회성 탐침) |
| 프로젝트 | 탐침 전용 이름으로 분리 — B의 기본 프로젝트를 오염시키지 않았다 |

---

## 1. Q1 — counsel_pack 그래프 1회의 span과 필드

**span 8개 / 실행 1회.** 이름 분포(2회 실행 = 16 span): `LangGraph`(root) 2 · `plan` 2 · `_route` 6 · `student` 4 · `summarize` 2.

| span | run_type | inputs | outputs |
| --- | --- | --- | --- |
| `LangGraph` (root) | chain | `{input: <state 전체>}` | **state 전체 12필드** |
| `plan` | chain | `{input}` | `emphasis_points` |
| `_route` | chain | `{input}` | `output` |
| `student` | chain | `{input}` | `cursor` · `results` |
| `summarize` | chain | `{input}` | `summary` |

root의 `outputs` 키 전수: `class_ref` · `context_hash` · `context_ref` · `cursor` · `emphasis_points` · `plan_version` · `quota_consumed` · `results` · `state_schema_version` · `student_refs` · `summary` · `tenant_id`.

**즉 노드 입출력 = state다.** 별도 가공이 없다.

### 1.1 무엇이 실리고 무엇이 안 실리는가

수집된 16 span 전체를 문자열로 훑은 결과:

| 항목 | 트레이스 등재 |
| --- | --- |
| `prompt` | **없음** |
| `facts` | **없음** |
| `fallback_text` | **없음** |
| `context_ref` · `context_hash` | 있음 |
| `student_refs` · `tenant_id` | 있음 (alias — 실명 아님) |
| `results[].draft_id` · `status` · `student_ref` | 있음 |
| **`emphasis_points`** | **있음 — 근거 문면이 그대로 실린다** |

`prompt`·`facts`·`fallback_text`가 없는 이유는 설계다 — `langgraph_state.md` §1.2 ⑨ 결정으로 `DraftContext` 본문을 state에 넣지 않고 `context_ref`·`context_hash` 포인터만 싣는다. **그 결정이 트레이스 노출을 이미 상당히 줄여 놨다.**

⚠ **그러나 `emphasis_points`는 예외다.** 실제 관측값:

```
span='plan' → emphasis_points = {
  'st_probe_1': ['이번 주 정답률 62% (record_id=le_st_probe_1)'],
  'st_probe_2': ['이번 주 정답률 62% (record_id=le_st_probe_2)']
}
```

근거 **라벨·수치·`record_id`** 가 문면 그대로 들어간다. 합성 데이터라 여기선 무해하지만 프로덕션에서는 실제 학생의 수치와 백엔드 DB 논리 참조다. 실명은 아니다(alias 스코프). **P2가 다뤄야 하는 1순위 필드다.**

## 2. Q2 — 체크포인터가 별도 span으로 잡히는가

**잡히지 않았다**(`InMemorySaver` 기준). 관측된 span 이름은 위 5종뿐이고 체크포인트 읽기·쓰기에 해당하는 span은 없었다.

⚠ **`PostgresSaver`는 미확인** — 로컬 PG가 없어 실행하지 못했다. serde가 state를 직렬화해 **DB에 적재**하는 경로는 트레이스와 별개의 노출면이므로 P2에서 별도로 봐야 한다. 이 문서는 그 결론을 추정으로 채우지 않는다.

## 3. Q3 — briefing 경로는 트레이스되는가

**전혀 되지 않는다.** briefing만 단독 실행(`build_brief_gateway` → `gateway.complete` 2회, 훅 유/무 각 1회)한 뒤 조회하면:

```
LangSmithNotFoundError: Project checkon-ai-trace-probe-briefing not found
```

span이 0건이라 **LangSmith 프로젝트가 생성조차 되지 않았다.** 이유는 두 가지가 겹친다.
1. briefing은 LangGraph 그래프도, langchain-core Runnable도 아니다 — 직접 `await gateway.complete(...)`다.
2. `llm/providers/openai_compat.py`가 `AsyncOpenAI`를 **raw로** 쓴다 — `langsmith.wrappers.wrap_openai`를 쓰지 않는다(`src/` 전체 `langsmith` import 0건 · 설정 플래그만 존재).

**⇒ P2의 실제 대상은 LangGraph 워커(`counsel_pack`·`mapping_probe`)뿐이고 briefing은 애초에 위험 표면이 아니다.** §2-16의 위험 분포 표를 이 결과로 좁힐 수 있다.

## 4. Q4 — (b) 훅으로 프롬프트를 바꾸면 기록이 바뀌는가

**바뀌지 않는다.** 탐침 훅이 프롬프트에 마커(`ZZPROBEMARKERZZ`)를 붙이도록 하고 `gateway.complete`를 호출했다. 훅은 실제로 호출됐다(`calls=1`, 호출 결과 `outcome=ok`). 그런데 수집된 **span 16개 전부에서 마커가 등재되지 않았다.**

프롬프트를 **지울 수는 없다** — `llm/gateway.py:195-206`이 훅 반환값(`masked_request`)을 그대로 `_complete_once`에 넘겨 provider로 보낸다. 지우면 LLM이 빈 프롬프트를 받는다. 이는 B가 의도한 계약이며(Protocol docstring이 전달 보장을 명시), **훅의 유효 범위를 확정하는 사실**이다.

**⇒ (b) 훅은 LangSmith 기록에 대해 효력이 없다.** 트레이스에 프롬프트가 애초에 실리지 않으므로 가릴 대상 자체가 없고, 실리는 것(`emphasis_points` 등)은 훅이 닿지 않는 노드 경계다.

## 5. Q5 — 실제 은닉의 제어점 (P2 설계 근거)

`langsmith 0.10.2` 정적 확인 결과, 은닉은 **클라이언트 레벨**에 있다.

| 제어점 | 형태 |
| --- | --- |
| `Client(hide_inputs=…)` | `bool` 또는 `Callable[[dict], dict]` |
| `Client(hide_outputs=…)` | 〃 |
| `Client(hide_metadata=…)` | `bool` |
| `Client(anonymizer=…)` | `Callable[[dict], dict]` — inputs·outputs 양쪽에 적용 |
| 환경변수 | `LANGSMITH_HIDE_INPUTS=true` / `LANGCHAIN_HIDE_INPUTS=true`(및 `_HIDE_OUTPUTS`·`_HIDE_METADATA`) — `client.py:1343-1352`의 `ls_utils.get_env_var("HIDE_INPUTS")` 폴백. `get_env_var`가 `LANGSMITH`·`LANGCHAIN` 두 네임스페이스를 본다 |

적용부(`client.py:2695-2713`): `hide_inputs is True` → **`{}`로 대체**(완전 제거) · 콜러블이면 변환 결과 · `anonymizer`가 있으면 JSON 덤프 후 적용.

**⇒ P2의 제어점은 여기다.** 이 훅(P1′)이 아니라 LangSmith 클라이언트 구성 + `PostgresSaver` serde(Q2 미확인분)가 실제 방어선이다.

## 6. 그래서 (b)가 덮는 범위 — 결론

| 질문 | 답 |
| --- | --- |
| (b) 훅이 트레이스를 가릴 수 있나 | **아니다** (Q4) |
| (b) 훅이 이 자리에서 할 수 있는 일 | (a) redaction을 건너뛴 프롬프트를 **전송 전에 차단**(fail-closed). 가리는 게 아니라 막는 것 |
| P1′의 의미 | **기동 가드 충족 이상은 아니다.** `TRACING=true`에서 게이트웨이가 만들어지게 하는 것 |
| 진짜 게이트 | **P2 하나** — 클라이언트 은닉 + 체크포인터 serde |
| 위험 표면 | **LangGraph 워커만**(counsel_pack·mapping_probe). briefing은 트레이스 자체가 안 된다 |
| 1순위 노출 필드 | `emphasis_points`(근거 라벨·수치·`record_id`) → 그다음 `results[].draft_id`·`context_hash` |

**`LANGSMITH_TRACING`은 여전히 `false`로 유지한다.** §2-16이 "P1·P1′·부속 확인·P2 중 하나라도 미완이면 켜지 않는다"고 못박았고 P2가 미완이다.

## 7. 미확인 항목 (추정으로 채우지 않았다)

1. **`PostgresSaver` span·적재 내용** — 로컬 PG 미가용. P2에서 `integration` 마커로 실측할 것.
2. **`mapping_probe` 워커의 span·필드** — 이번 탐침은 `counsel_pack`만 돌렸다. 같은 LangGraph 경로라 유사할 것으로 보이지만 **확인하지 않았다**(도구 호출 span이 추가될 가능성).
3. **`openai_compat` 실 provider 경로** — fake로만 측정했다. 실 provider도 `wrap_openai`를 쓰지 않으므로 결과가 같을 것으로 보이지만 **확인하지 않았다**.
4. **`hide_inputs` 실제 적용 결과** — 정적 코드 확인까지만이고 켜서 재보지 않았다(P2 범위).
5. **`langsmith` 상위 버전 동작** — 0.10.2에서만 측정했다.
