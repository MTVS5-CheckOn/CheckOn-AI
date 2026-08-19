"""counsel_pack 설정 — 임계값을 코드에 박지 않는다.

03 §1: "값이 바뀌면 코드 diff가 생기면 위치가 틀린 것".
"""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from ai.runtime.env_files import ENV_FILES


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

    ━━ 🔴 값의 근거 — **응답 예산**에서 역산했다 (8/20 재작성 · 99 #106) ━━

        잡당 최악 LLM 콜 = plan 1 + write (regen_max 3 + 1) = **5콜**
        콜당 상한        = **45s** (`OPENAI_TIMEOUT_S` — 아래 실측 근거)
        ⇒ 잡당 최악      = **225s**
        K × 225s ≤ **300s**(04 §2.4 `/drafts` — *"작업 총 5분 초과 시 failed"*)
        ⇒ **K = ⌊300 / 225⌋ = 1**

    🔴 **K는 예산을 넘지 않는 최댓값이다** — 종전 식은 `K ≤ 4` 에서 *"경계에 붙이지
    않는다"* 며 3으로 물러섰는데, 그 여유의 근거가 어디에도 없었다(예산은 이미 최악
    기준이라 여유가 두 번 들어간다). 지금은 **바닥 나눗셈 하나**이고 그 규칙을 검사가
    지킨다 — 콜당 상한이나 예산이 바뀌면 K가 **자동으로 따라오지 않으므로** 갈리면 red다.

    🔴 **상한의 근거가 lease가 아니다 — 그게 종전 식의 오류였다.** 실측(8/20):
    `run_next`는 **회전마다** `lease_next`로 lease를 새로 잡고 종단 전이로 놓는다
    (`ACTIVE_LEASE_PHASES = {leased, running}` ∩ `TERMINAL_PHASES` = ∅). ⇒ 한 잡이 lease를
    쥐는 시간은 **그 잡의 실행 시간**이지 `K`배가 아니다. lease 제약은
    `잡당 최악(225s) ≤ counsel_lease_seconds(300s)` **하나뿐이고 이미 만족**한다.
    ⇒ **K를 정하는 것은 「POST가 HTTP 연결을 얼마나 쥐는가」**이고, 그 상한은
    04 §2.4의 **300s**다.

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
    **염준영 소유**라(02 §5) 여기서 못 고친다. **배포 환경변수 `OPENAI_TIMEOUT_S=45`로
    덮고**, 기본값 상향은 별도 통보다(99 #106).

    ━━ 🔴 K=1의 대가 — 숨기지 않는다 ━━

    앞선 잡이 **하나만** 있어도 내 잡이 `queued`로 나간다(종전엔 3개까지 버텼다).
    그 뒤 **다음 POST가 밀어 주지만** 문의가 드문 학원에서는 오래 걸린다.
    🔴 **진짜 처방은 K를 키우는 것이 아니라 배경 드레인·비동기 워커다**(99 #85 ①) —
    이 값은 **응급처치**다. ⚠ PR-01의 `Retry-After`가 완화한다(BE가 폴링하므로 `queued`가
    보인다) — 다만 **폴링이 잡을 돌리지는 않는다.**

    ⚠ **BE 향 숫자는 안 움직인다** — 종전 `K=3 × 5콜 × 15s`도, 신규 `K=1 × 5콜 × 45s`도
    **최악 225s로 같다.** 04:115의 «최악 225초»와 apidog 권고(240s)를 **안 고쳐도 된다.**
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

    counsel_lease_seconds: int = 300
    """잡 lease 유효 시간(초). 라우터가 같은 요청 안에서 lease→실행→succeed까지 끝내므로
    실제로는 만료 전에 반납된다 — 프로세스가 죽었을 때 recovery가 집어갈 여유값이다."""

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
