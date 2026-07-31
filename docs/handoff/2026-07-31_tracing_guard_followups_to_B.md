# [A → B] §2-16 후속 1 완료 회신 + 후속 2 A 의견 (09 §1-9 A-11)

**보낸이:** 박진희(A) · **날짜:** 2026-07-31 · **관련:** `part_b/09` §2-16 후속 1·2 · §1-9 A-11 · 99 D ⑳
**브랜치:** `test/tracing-guard-env-contract` · **`llm/` 무접촉**

---

## ① 후속 1 — A 계약 테스트 전환 완료, **OR 걷으셔도 됩니다**

`or resolved_settings.langsmith_tracing` 보조 트리거를 남기신 이유(A 계약 테스트가
`LlmSettings(langsmith_tracing=True)`로 추적을 흉내 냄)가 해소됐습니다.

**전환한 A 소유 테스트 2건** — `tests/ai/contract/test_trace_masking_hook.py`

| 테스트 | 전 | 후 |
| --- | --- | --- |
| `test_brief_gateway_builds_with_tracing_enabled` | `settings=_tracing_on()` | `monkeypatch.setenv(_REPRESENTATIVE_TRACING_ENV, "true")` |
| `test_startup_guard_still_bites_without_hook` | 〃 | 〃 |

준영님이 `test_gateway.py`에서 쓰신 방식(`_REPRESENTATIVE_TRACING_ENV = TRACING_ENV_SYNONYMS[0]` + `monkeypatch.setenv`)을 **그대로** 따랐고, 동의어 목록은 복제하지 않고 `runtime/tracing.py`에서 가져옵니다.

**추가로** `settings=`에는 `LlmSettings(langsmith_tracing=False, _env_file=None)`을 **명시**했습니다. 추적 활성을 env로만 태우므로, 가드 발화가 **오직 `external_tracing_active()`에서 온다는 것이 구조적으로 보장**됩니다.

### 사전 증명 — OR을 걷은 상태에서 green

로컬에서 `llm/gateway.py:124`를 임시로 이렇게 바꾸고 전체 스위트를 돌렸습니다:

```python
tracing_active = external_tracing_active()   # ← or resolved_settings.langsmith_tracing 제거
```

**결과: `1347 passed, 9 deselected, 3 xfailed`** — 실패 0. 임시 변경은 원복했고 커밋에 넣지 않았습니다(`src/ai/llm/` diff 0).

⇒ **판정 단일화(후속 1)를 진행하셔도 A 쪽에서 깨지는 것은 없습니다.**

### 남은 `langsmith_tracing` 참조 (전수)

| 위치 | 판정 |
| --- | --- |
| `src/ai/llm/gateway.py:124` | **B 몫** — 이번 OR 제거 대상 |
| `src/ai/llm/settings.py:16` | 필드 정의 — **유지 권장**(`.env` 정본 표기) |
| `tests/ai/unit/llm/conftest.py:17·19` | B 소유 격리 fixture(추적을 **끄는** 용도) — 무접촉 |
| `tests/ai/unit/problem_generation/conftest.py:17·19` | 〃 — 무접촉 |
| `tests/ai/contract/test_trace_masking_hook.py:126` | ✅ **이번에 전환** |

### 09 문서 갱신 부탁 — B 소유라 A가 건드리지 않았습니다

- **§1-9 A-11 행** — `☐ 확인` → **✅ 완료**(A 테스트 env 전환 · 사전 증명 green). A 쪽 조건은 끝났고 남은 건 OR 제거뿐입니다.
- **§3 B-14 행** — 지금 문구가 **#59 머지로 stale**입니다:

  > 동의어 4종 확장 `5d8e1a4` **PR 대기**(현행 develop 가드는 `LANGCHAIN_TRACING_V2` 등으로 **우회 가능**)

  `1cc9fac`(#59 머지)로 develop의 `gateway.py:124`가 이미 `external_tracing_active()`를 봅니다 — **develop 가드는 더 이상 동의어로 우회되지 않습니다.** A-11 반영하실 때 "✅ 머지 완료(#59 · `5d8e1a4`) · 판정 소스 단일화(OR 제거)만 잔여" 취지로 같이 갱신 부탁드립니다.
- **§2-16 후속 1** 서술도 같은 이유로 "A 테스트 전환 완료 → B의 OR 제거만 잔여"가 됩니다.

A쪽 기록(`99` D ⑳)은 이번 커밋에서 갱신했습니다.

---

## ② 후속 2 — "훅을 상시 요구로 바꿀까" 에 대한 A 의견

**결론을 정하지 않고 재료만 드립니다** — 가드 의미 변경은 B 소유 파일의 동작 변경이라 양자 협의 사안입니다.

### 실측 — 훅 미주입 `LlmGateway` 생성부

`src/` + `tests/` 전수(`LlmGateway(` 호출 29곳, docstring 언급 제외):

| 구분 | 훅 주입 | 훅 미주입 |
| --- | --- | --- |
| **프로덕션 조립부** | **2** (`composition/provider.py:122` · `composition/counsel/assembly.py:69`) | **0** |
| 테스트 (`tests/ai/unit/llm/test_gateway.py`) | 6 | **13** |
| 테스트 (`problem_generation` 2파일) | 0 | **2** |
| 테스트 (`contract/test_trace_masking_hook.py`) | 1 | **3** |

**프로덕션은 이미 100% 주입**입니다(⑭ 이후, AST 계약 `_ALLOWED_GATEWAY_BUILDERS`가 고정). 상시 요구로 바꿀 때 **깨지는 곳은 전부 테스트 18곳**이고, 대부분은 훅과 무관한 라우팅·재시도·recorder 검증입니다.

### 수선 비용

18곳에 no-op 훅을 꽂는 일 자체는 기계적입니다. 다만 두 가지가 걸립니다.

1. **테스트가 훅을 꽂게 되면 "훅 없이도 도는가"를 아무도 안 보게 됩니다.** 지금은 훅 미주입 13곳이 사실상 "훅과 무관한 경로" 회귀 역할을 합니다.
2. **A 쪽에 no-op 훅이 없습니다.** ⑭에서 B의 private `_NoOpTraceMaskingHook`을 쓰지 않고 A 이름의 구현(`RedactionTripwireTraceHook`)을 만들었는데, 그건 **트립와이어**라 no-op이 아닙니다. 상시 요구가 되면 "테스트용 no-op을 어디 둘지"가 새 안건이 됩니다.

### A 잠정 의견

**redaction 우회의 1차 방어는 이미 AST 계약**입니다 — `tests/ai/contract/test_composition_redaction.py`가 gateway 호출부 화이트리스트 + `redact()`·`uncertain` 분기 존재를 정적으로 강제합니다(⑱에서 `GatewayPlanner.plan`이 실제로 걸려 등록됐습니다). 훅은 **런타임 심층 방어**(문자열을 실제로 봄)이고, 층이 달라 서로를 대체하지 않습니다.

그래서 "상시 요구"의 실익은 **AST가 못 잡는 경로**(gateway를 직접 만들어 쓰는 새 소비자)에 한정되는데, 프로덕션에서 그 경로는 **AST 화이트리스트가 이미 막고** 있습니다. 반면 비용은 테스트 18곳 + no-op 훅 소유 결정입니다.

⇒ **A는 현행(추적 활성 시에만 요구) 유지 쪽으로 기웁니다.** 다만 두 가지는 준영님 판단이 필요합니다:

- **(가)** 추적이 꺼져 있어도 훅을 요구하면 얻는 게 있는지 — B 경로(`problem_generation`)에서 gateway를 직접 만드는 계획이 있으신지에 따라 답이 달라집니다.
- **(나)** 상시 요구로 간다면 **no-op 훅의 소유**(B의 private을 공개할지, A가 `runtime/`에 하나 더 둘지).

**결론은 열어 둡니다** — 위 수치가 판단 재료가 되면 좋겠습니다.

---

## ③ 참고 — §2-16 재작성 문서 PR과의 관계

준영님이 예고하신 "§2-16을 `part_a/11` 실측 기준으로 재작성하는 문서 PR"에 이 ②의 수치를 그대로 실으셔도 됩니다. 그래서 독립 md로 뽑았습니다.

이번 브랜치는 **후속 1의 A쪽 조건**까지이고, 후속 2는 **구현하지 않았습니다**(의견 자료까지).
