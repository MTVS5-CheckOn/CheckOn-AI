"""counsel_pack 설정 — 임계값을 코드에 박지 않는다.

03 §1: "값이 바뀌면 코드 diff가 생기면 위치가 틀린 것".
"""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from ai.runtime.env_files import ENV_FILES

#: ━━ 🔴 **예산 관계의 단일 정본** — 아래 넷은 서로 물려 있다 ━━
#:
#: 이 값들이 **흩어져 있었던 것**이 99 ㉪의 원인이다. `openai_timeout_s` 가
#: 15 → 45 → 90 으로 **두 번** 바뀌는 동안 딸린 계산(K·lease·04 문면·BE 권고)이
#: **두 번 다** 안 따라왔다. ⇒ 한 곳에서 만들고, 문서와의 일치는 검사가 지킨다
#: (`tests/ai/contract/test_timeout_budget_relations.py`).

#: 콜당 LLM 상한(초).
#: 🔴 **정본은 배포 env `OPENAI_TIMEOUT_S` 이고 여기 값은 그 사본이다** —
#: `llm/providers/openai_compat.py` 는 **염준영 소유**라(02 §5) 여기서 못 읽어 온다
#: (읽어 오면 `OpenAiSettings()` 가 `.env` 를 타서 **검사가 환경에 의존**한다 — 99 #109 가
#: 정확히 그 형태다). ⇒ **사본인 것을 인정하고, 갈리면 사람이 고치도록 여기 적는다.**
LLM_CALL_TIMEOUT_S: int = 90
"""2026-08-20 준영님과의 회의 확정. 근거는 `counsel_inline_drain_max` docstring."""

#: 초안 1건의 최악 LLM 콜 수 — plan 1 + write(`regen_max` + 1).
#: ⚠ `DEFAULT_REGEN_MAX` 를 **import 하지 않는다** — `assembly` 가 이 모듈을 import 하므로
#: 순환이다(실측 8/20). ⇒ 리터럴로 두고 **검사가 `regen_max` 와 묶는다**.
WORST_CALLS_PER_DRAFT: int = 5

#: refine 1턴의 최악 LLM 콜 수 — **plan 이 없다**(초안이 고른 강조점을 이어받는다).
WORST_CALLS_PER_REFINE: int = 4

#: `POST /drafts` 가 HTTP 연결을 쥘 수 있는 상한(초) — 04 §2.4 정본과 **같아야 한다**.
RESPONSE_BUDGET_S: int = 480


class CounselSettings(BaseSettings):
    """상담팩 운영 파라미터 — env 주입."""

    model_config = SettingsConfigDict(env_file=ENV_FILES, extra="ignore")

    counsel_llm_failure_circuit: int = Field(default=3, ge=2)
    """연속 LLM 실패 **학생 수** 임계 → paused. 기본 3은 `langgraph_state.md` §1.3
    ("LLM 연속 실패 3학생(서킷)")의 기준 수치를 그대로 옮긴 값이다.

    🔴 **`ge=2` — 1 이하는 기동에서 거부한다** (99 #08 ⓐ). `1`이면 **첫 실패에 열린다** —
    그건 「**연속** 실패」가 아니라 「단발 실패」이고 **서킷이라는 개념 자체와 어긋난다.**
    임계값을 조정하는 것이 아니라 **뜻이 안 되는 값**을 막는 것이다.
    ⚠ 실측(2026-08-19): `COUNSEL_LLM_FAILURE_CIRCUIT=1` 로 주면 **기동이 되고** 첫 LLM
    실패에 잡이 `paused` 로 갔다 — 그 상태를 푸는 **HTTP 표면이 없어** 고착이었다(99 ㉖).

    🔴 **⚠ 2로 올려도 N=1 에서는 안 열린다.** 카운터는 **학생 단위**로 오르는데
    (`graph.py` 의 `consecutive["llm_failed"]`) counsel 라우터는 학생을 **1명만** 넣는다
    ⇒ **최대 1**이다. **이 가드는 footgun 방지이지 서킷을 도달 가능하게 만드는 것이 아니다.**
    이 문장이 없으면 다음 사람이 *"2로 했으니 이제 열리겠지"* 로 읽는다.
    ⚠ 서킷이 실제로 열리는 조건은 **N>1**(월별 벌크·Kafka)이다."""

    counsel_inline_drain_max: int = 1
    """POST 한 번이 **자기 잡이 끝날 때까지** 돌리는 최대 회전 수 (99 #21 · 불변식 6).

    🔴 **왜 필요한가** — `lease_next`는 `worker_kind + tenant_id`로만 집고 **`job_id`를
    지정할 수 없다**(`agents/job_store.py`). 큐에 앞선 잡이 있으면 POST가 **남의 잡**을
    돌리고 **내 잡은 `queued`로 남는다.** 그런데 counsel에는 **배경 드레인이 없고 GET은
    잡을 안 돌린다**(실측 2026-08-19: `get_counsel_draft` 경로에 `run_next` 0건) ⇒
    **BE가 폴링해도 영영 안 풀린다.** 재현됨(같은 날: 앞선 잡 2개 → POST `queued` →
    GET 2회 폴링해도 `queued`).

    ━━ 🔴 값의 근거 — **응답 예산**에서 역산했다 (8/20 재작성 · 99 #106·㉪) ━━

        잡당 최악 LLM 콜 = plan 1 + write (regen_max 3 + 1) = **5콜**
        콜당 상한        = **90s** (`OPENAI_TIMEOUT_S` — 아래 실측 근거)
        ⇒ 잡당 최악      = **450s**
        K × 450s ≤ **480s**(04 §2.4 `/drafts` 응답 예산)
        ⇒ **K = ⌊480 / 450⌋ = 1**

    ⚠ **숫자는 위 모듈 상수가 정본이다**(`LLM_CALL_TIMEOUT_S`·`WORST_CALLS_PER_DRAFT`·
    `RESPONSE_BUDGET_S`) — 이 문면은 그 값을 **설명**하는 것이고, 둘이 갈리면 검사가 red다.

    🔴 **K는 예산을 넘지 않는 최댓값이다** — 종전 식은 `K ≤ 4` 에서 *"경계에 붙이지
    않는다"* 며 3으로 물러섰는데, 그 여유의 근거가 어디에도 없었다(예산은 이미 최악
    기준이라 여유가 두 번 들어간다). 지금은 **바닥 나눗셈 하나**이고 그 규칙을 검사가
    지킨다 — 콜당 상한이나 예산이 바뀌면 K가 **자동으로 따라오지 않으므로** 갈리면 red다.

    🔴 **상한의 근거가 lease가 아니다 — 그게 종전 식의 오류였다.** 실측(8/20):
    `run_next`는 **회전마다** `lease_next`로 lease를 새로 잡고 종단 전이로 놓는다
    (`ACTIVE_LEASE_PHASES = {leased, running}` ∩ `TERMINAL_PHASES` = ∅). ⇒ 한 잡이 lease를
    쥐는 시간은 **그 잡의 실행 시간**이지 `K`배가 아니다.
    ⇒ **K를 정하는 것은 「POST가 HTTP 연결을 얼마나 쥐는가」**이고, 그 상한은
    04 §2.4의 **480s**다.

    🔴 **lease 제약은 별개이고, 그건 여유가 아니라 필수 조건이다** —
    `잡당 최악(450s) ≤ counsel_lease_seconds(480s)`. ⚠ **종전 문면은 이 관계를 「이미
    만족한다」고만 적어 뒀고**, 콜당이 45→90 이 되자 450 > 300 으로 **조용히 깨졌다**
    (99 ㉪). ⚠ 그리고 종전에는 이것을 *"죽은 프로세스의 잡 회수 여유"* 로 읽었는데
    **그 읽기가 틀렸다**: lease 가 잡당 최악보다 짧으면 만료되는 것은 죽은 잡이 아니라
    **멀쩡히 실행 중인 잡**이다 ⇒ recovery 가 그 잡을 다시 집어 **같은 잡이 두 번 돈다**
    (LLM 비용도 두 배다). **회수 지연은 대가가 아니라 이 부등식의 필연**이다.
    ⇒ 이 부등식은 `test_timeout_budget_relations.py` 가 단언한다.

    ━━ 🔴 콜당 45s의 근거 — 실측이다(추정 아님) ━━

    실 LLM 1차 회차(2026-08-19 · `gpt-5.6-luna` · n=27 · 99 #106):

        잡당 지연(plan+write 합)  p50 **12.2s** · p90 21.8s · p95 **23.1s** · max **25.3s**
        15s 초과                   **10/27 = 37%**

    🔴 **종전 값 15s로는 그 37%가 `LlmTimeout`으로 죽는다** — 선행 1건이 실제로 그렇게
    죽었다. ⚠ **종전 근거 문장 «실제로는 잡 하나가 수 초»는 거짓이었다**(99 #85 정정).
    ⚠ **원자료에 콜당 분해가 없다** — 위 숫자는 2콜 합이다. 다만 선행 1건에서 **write
    단독이 15s를 넘어 죽었으므로** 콜당도 15s를 넘는다는 것은 확실하다.
    ⇒ 45s는 잡당 max(25.3s)의 **1.8배**이고 콜당으로는 더 여유다. **2차 회차에서 콜당
    p95를 재고 조정한다.**

    ⚠ **이 값은 `llm/providers/openai_compat.py`가 소유한다**(기본값 15) — 그 파일은
    **염준영 소유**라(02 §5) 여기서 못 고친다. **배포 환경변수 `OPENAI_TIMEOUT_S`로
    덮고**, 기본값 상향은 별도 통보다(99 #106).

    ━━ 🔴 45s → **90s** (2026-08-20 준영님과의 회의 확정) ━━

    ⚠ **위 45s 문단을 지우지 않는다** — 15에서 왜 올렸는지가 다음 사람이 읽을 이력이다.
    2차 회차(8/20 · n=30)가 **콜당** 분해를 냈다: plan p50 2.5s · p95 5.4s ·
    write p50 19.4s · p95 **26.8s** · max **27.2s** · 콜당 45s 초과 **0/60**.
    ⇒ 45 자체는 실측상 충분했다. **그런데도 90으로 올린 것은 여유폭 판정**이고
    (write p95 의 **3.4배**), 근거는 ⓐ 표본이 `gpt-5.6-luna` 한 모델·한 시기이고
    ⓑ 타임아웃 사망은 **한 건도 복구가 안 되는** 실패라 비대칭이 크기 때문이다.
    🔴 **대가는 이 docstring 전체다** — 콜당이 2배가 되면 잡당·예산·lease·04 문면이
    **전부** 따라 움직인다. 그 연쇄를 사람이 기억으로 좇지 않도록 검사로 묶었다(㉪).

    ━━ 🔴 K=1의 대가 — 숨기지 않는다 ━━

    앞선 잡이 **하나만** 있어도 내 잡이 `queued`로 나간다(종전엔 3개까지 버텼다).
    그 뒤 **다음 POST가 밀어 주지만** 문의가 드문 학원에서는 오래 걸린다.
    🔴 **진짜 처방은 K를 키우는 것이 아니라 배경 드레인·비동기 워커다**(99 #85 ①) —
    이 값은 **응급처치**다. ⚠ PR-01의 `Retry-After`가 완화한다(BE가 폴링하므로 `queued`가
    보인다) — 다만 **폴링이 잡을 돌리지는 않는다.**

    🔴 **BE 향 숫자가 이번엔 움직였다** — 종전 두 조합(`K=3 × 5콜 × 15s` · `K=1 × 5콜 × 45s`)은
    **최악 225s로 같아서** 04 를 안 고쳐도 됐다. 90s 는 **450s**라 다르다 ⇒ 04:115 ·
    §2.4 · §3.9 를 **같은 회차에** 고쳤고, 그 일치를 검사가 지킨다(㉭ — 통보와 계약 반영은
    다른 사건이다).
    """

    counsel_poll_retry_after_seconds: int = Field(default=2, ge=1)
    """비종단 GET 응답의 `Retry-After` 값(초) — **호출자에게 언제 다시 오라고 말한다.**

    🔴 **폴링 주기를 코드가 아니라 설정이 정한다**(03 §1). counsel 은 **Kafka 완료 통지가
    없어**(실측 2026-08-19: AI 쪽 프로듀서·컨슈머·outbox **0건** · `aiokafka` 의존성에도
    없다) 폴링이 유일한 길이다. 상대가 자기 상수로 돌면 **우리가 인라인 실행을 바꿔도 그
    주기는 안 따라온다** — 실제로 이번 회차에 POST 가 최대 3회 드레인하도록 바뀌어 응답이
    최악 225s 까지 늘었는데(99 #21·#85) 어댑터 주기는 그대로였다.

    **기본 2의 근거 — 지어낸 값이 아니라 이미 양쪽이 서 있는 값이다.**
    ⓐ 같은 저장소 `api/routers/problem.py` 의 `poll_retry_after_seconds` 기본값이 **2**다
      (같은 역할 · 실측 확인).
    ⓑ 어댑터의 `checkon.ai.problem-generation.poll-interval` 기본값도 **2s** 다
      (`checkon-kafka-adapter` `application.yaml` — 🔴 **이 Mac 에 해당 저장소 클론이 없어
      직접 못 봤다.** 지시서 №1 의 [읽음] 실측을 인용한 것이다).
    ⇒ 양쪽이 이미 2 로 서 있으므로 그 값을 그대로 쓴다.

    🔴 **`ge=1` — 0·음수는 폴링 폭주다.** `Retry-After: 0` 은 「즉시 다시 오라」라서
    어댑터가 쉬지 않고 때린다. 임계 조정이 아니라 **뜻이 안 되는 값**을 막는 것이다.
    """

    counsel_lease_seconds: int = 480
    """잡 lease 유효 시간(초). 라우터가 같은 요청 안에서 lease→실행→succeed까지 끝내므로
    실제로는 만료 전에 반납된다 — 프로세스가 죽었을 때 recovery가 집어갈 값이다.

    🔴 **관계: `잡당 최악 ≤ 이 값`** (`WORST_CALLS_PER_DRAFT × LLM_CALL_TIMEOUT_S`
    = 5 × 90 = **450s** ≤ **480s**). 숫자만 두면 다음에 또 갈리므로 관계를 적는다.

    ⚠ **이 부등식이 깨지면 「회수가 느려진다」가 아니라 「같은 잡이 두 번 돈다」이다** —
    만료되는 것은 죽은 잡이 아니라 **아직 실행 중인** 잡이고, recovery 가 그걸 다시 집는다
    (`Supervisor.run_next` 가 *"만료 작업을 먼저 회수한 뒤"* lease 한다). LLM 비용이 두 배가
    되고, 학부모에게 갈 초안이 두 벌 생긴다. ⇒ **긴 lease 는 대가가 아니라 필수 조건**이다.

    ⚠ 대가는 따로 있다 — **진짜로 죽은** 프로세스의 잡 회수가 300s → 480s 로 느려진다.
    그건 이 부등식을 지키기 위해 받아들이는 것이고, 줄이려면 콜당 상한이나 콜 수를
    줄여야 한다(99 ㉪ ⓑ·ⓒ 안).

    🔴 300 → 480 (8/20 · 99 ㉪ ⓐ). 종전 300 은 콜당 45s(잡당 225s) 전제였다."""

    counsel_priority_aging_seconds: int = 600
    """우선순위 aging 간격(초) — 큐가 길어질 때 오래 기다린 잡을 끌어올린다."""

    llm_provider: str = "fake"
    """LLM 구현 선택 — `classify`와 **같은 env 이름**(`LLM_PROVIDER`)을 읽는다.

    기본이 `fake`인 것은 CI 규약이다(실 LLM 호출 0). ⚠ **기본값이 fake인 것과 배선을
    잊는 것은 다르다** — 전자는 명시적 선택이라 경고 로그와 `LLM_CALL.provider`에 남고,
    후자는 `CounselProviderNotWired`로 기동이 막힌다(99 ㉥).
    """


@lru_cache
def get_counsel_settings() -> CounselSettings:
    return CounselSettings()
