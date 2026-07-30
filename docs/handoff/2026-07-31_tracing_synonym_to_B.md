# [A → B] LangSmith 추적 가드가 동의어 env로 우회됩니다 — gateway 판정 확장 요청

**보낸이:** 박진희(A) · **날짜:** 2026-07-31 · **관련:** `part_b/09` §2-16 · 99 D ⑳ · #48
**A 조치:** `fix/langsmith-tracing-synonym-guard`(A 소유 표면은 이미 닫음) · **B 요청:** 아래 ②

---

## ① 실측 요약 — 우회가 실재합니다

준영님이 #48로 넣어주신 기동 가드(`llm/gateway.py:120-128`)는 `LANGSMITH_TRACING` **하나**를 봅니다. 그런데 langsmith가 추적 활성을 판정하는 함수는 이름을 **4개** 봅니다.

`langsmith/utils.py:141`
```python
var_result = get_env_var("TRACING_V2", default=get_env_var("TRACING", default=""))
return var_result == "true"
```
`langsmith/utils.py:423`
```python
namespaces: tuple = ("LANGSMITH", "LANGCHAIN")
```

→ `{LANGSMITH,LANGCHAIN}_{TRACING,TRACING_V2}` = **4개**.

| env 이름 | `tracing_is_enabled()` | `LlmSettings.langsmith_tracing` | #48 가드 |
| --- | --- | --- | --- |
| `LANGSMITH_TRACING` | `True` | `True` | 차단 |
| `LANGCHAIN_TRACING` | `True` | `False` | 🔴 **침묵** |
| `LANGSMITH_TRACING_V2` | `True` | `False` | 🔴 **침묵** |
| `LANGCHAIN_TRACING_V2` | `True` | `False` | 🔴 **침묵** |

**실제 유출 시도를 관찰했습니다**(가짜 키 + 로컬 관찰 소켓 · 실전송 0). `LANGCHAIN_TRACING_V2=true`에서 counsel_pack 그래프 **1회** 실행:

- #48 가드: **침묵**(훅 없이 `LlmGateway` 생성 통과)
- export 연결 시도: **10건** — `POST /runs/multipart` · `Content-Length: 15606`
- 앱 에러: **0건**(그래프 정상 완주)

`LANGCHAIN_TRACING_V2`는 LangChain 문서·튜토리얼에서 가장 흔히 쓰이는 이름이라, 운영자가 관례대로 설정하면 **추적은 켜지고 가드는 조용한** 상태가 됩니다. ⑱ 이후 `emphasis_points`에 근거 라벨·수치·`record_id` 문면이 실려 노출 대상도 커졌습니다(불변식 3).

상세: `docs/part_a/11_langsmith_trace_probe.md` §8(재현 조건·버전 포함).

## ② 요청 — gateway 가드 판정을 `tracing_is_enabled()` 기반으로

`llm/`은 준영님 단독 소유라 **A가 손대지 않았습니다.** 판정만 바꾸면 되는 작은 변경입니다:

```python
# 현재 (llm/gateway.py:121)
if resolved_settings.langsmith_tracing and trace_masking_hook is None:

# 제안 — 라이브러리 판정에 위임
from ai.runtime.tracing import external_tracing_active   # A가 이번에 만든 순수 술어
if external_tracing_active() and trace_masking_hook is None:
```

`runtime/tracing.external_tracing_active()`는 A가 이번 브랜치에서 만들었고 `tracing_is_enabled()`를 그대로 호출합니다 — env 4개뿐 아니라 **컨텍스트 변수·진행 중 run tree·전역 fallback**(`utils.py:132-139`)까지 덮습니다. env 이름을 손으로 복제하면 그 경로들을 놓칩니다.

**어느 쪽이든 좋습니다 — 골라 주세요:**

- **(a) B가 직접 수정** — 가장 깔끔합니다. A는 `runtime/tracing.py`를 안정 API로 유지하겠습니다.
- **(b) A가 PR을 내고 B가 승인** — `llm/gateway.py` 3줄(import 1 + 조건 1 + 메시지 1) 짜리 PR을 올리겠습니다. 소유는 그대로 B입니다.

⚠ `LlmSettings.langsmith_tracing` 필드는 **그대로 두시길 권합니다** — 없애면 `.env`의 정본 표기가 사라집니다. 판정만 위임하면 됩니다.

## ③ 가드 에러 메시지 — 오정보 여부 확인 결과

⑭ 이후 실제로 오정보인지 확인해 달라고 하셨습니다. 결과는 **"조립부 목록은 정확, 조치 안내는 불완전"** 입니다.

| 문구 | 판정 |
| --- | --- |
| "조립부(현재 `composition/provider.py` 및 `composition/counsel/assembly.py`)" | ✅ **정확** — `src/` 전체 `LlmGateway(...)` 생성부가 정확히 그 둘이고, ⑭의 AST 계약(`test_trace_masking_hook.py`의 `_ALLOWED_GATEWAY_BUILDERS`)이 이를 고정하고 있습니다 |
| "훅을 주입하거나" | ✅ 발화 시점 기준 유효 — 가드는 훅이 **없을 때만** 터지므로, 터졌다면 새 조립부가 훅 없이 생긴 것입니다 |
| "**`LANGSMITH_TRACING`을 끄라**" | ⚠ **불완전** — 나머지 3개가 켜져 있으면 이걸 꺼도 추적은 계속 돕니다. ②를 반영하면서 이 문구도 "추적 env 전부" 취지로 손보시는 걸 권합니다 |

참고로 A 쪽 가드는 이렇게 씁니다(값은 절대 안 싣고 **이름만**):

```
외부 트레이싱이 활성인데 P2 은닉이 구현되지 않았다 — counsel_pack 워커를 기동할 수 없다.
마스킹 전 state(emphasis_points의 근거 라벨·수치·record_id)가 외부 SaaS로 나간다(불변식 3).
감지된 env: LANGCHAIN_TRACING_V2. 추적을 끄거나(위 이름 전부 미설정) P2 은닉을 먼저 구현하라.
docs/part_a/11_langsmith_trace_probe.md · 99 D ⑳.
```

🔴 **env 값은 출력하지 않습니다** — 근처에 `LANGSMITH_API_KEY`가 있어서, 값을 찍으면 가드가 막으려던 유출을 에러 메시지가 하게 됩니다. 회귀 테스트로 고정해 뒀습니다.

## ④ A 표면은 이번 브랜치로 닫혔습니다

실측상 위험 표면은 **LangGraph 워커 둘뿐**(`counsel_pack`·`mapping_probe`)이고 briefing은 span 0건입니다(11 §3). 그 두 워커의 조립부가 A 소유라, B 회신을 기다리지 않고 먼저 닫았습니다.

- `runtime/tracing.require_tracing_disabled()` — 추적 활성이면 러너 기동 거부(fail-closed)
- **briefing 조립부에는 걸지 않았습니다** — 위험 표면이 아니라는 실측이 근거이고, 제외 사유를 `composition/provider.py` docstring에 남겼습니다
- `langsmith` import는 `runtime/tracing.py` **한 파일에만** 두고 AST 계약으로 고정했습니다(`runtime/`엔 `redaction.py`가 삽니다 — 03 §1b)

**P2(체크포인터 serde · `Client(hide_inputs=…)`)는 여전히 별건**입니다(99 D ⑳). 이번 조치는 "추적이 꺼져 있어야 할 때 진짜 꺼져 있는가"까지이고, "켜도 안전한가"는 P2입니다. 그래서 `LANGSMITH_TRACING=false` 유지 원칙은 그대로입니다.
