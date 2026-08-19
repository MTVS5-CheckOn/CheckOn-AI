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

    counsel_inline_drain_max: int = 3
    """POST 한 번이 **자기 잡이 끝날 때까지** 돌리는 최대 회전 수 (99 #21 · 불변식 6).

    🔴 **왜 필요한가** — `lease_next`는 `worker_kind + tenant_id`로만 집고 **`job_id`를
    지정할 수 없다**(`agents/job_store.py`). 큐에 앞선 잡이 있으면 POST가 **남의 잡**을
    돌리고 **내 잡은 `queued`로 남는다.** 그런데 counsel에는 **배경 드레인이 없고 GET은
    잡을 안 돌린다**(실측 2026-08-19: `get_counsel_draft` 경로에 `run_next` 0건) ⇒
    **BE가 폴링해도 영영 안 풀린다.** 재현됨(같은 날: 앞선 잡 2개 → POST `queued` →
    GET 2회 폴링해도 `queued`).

    ━━ 🔴 값의 근거 — lease 예산에서 역산했다 ━━

        잡당 최악 LLM 콜 = plan 1 + write (regen_max 3 + 1) = **5콜**
        콜당 상한        = 15s (`openai_timeout_s` · 04 §2.4)
        ⇒ 잡당 최악      = **75s**
        K × 75s ≤ counsel_lease_seconds(300s)  ⇒  K ≤ 4
        ⇒ **K = 3** (225s · 여유 75s) — 경계에 붙이지 않는다

    ⚠ **넘으면 fencing이 무너진다** — lease가 만료되면 recovery가 잡을 회수해 **같은 잡이
    두 번 돌 수 있다.** 그래서 lease가 상한의 근거다.

    ⚠ **이 값은 「최악」 기준이라 보수적이다.** 실제로는 잡 하나가 수 초다(N=1 인라인).
    🔴 **다만 최악에서 응답이 225s까지 늘 수 있고, `04 §2.4`에 counsel POST의 타임아웃이
    없다** — 그 사실을 99 #85에 등재했다. 클라이언트 read timeout이 정해지면 그쪽이
    더 좁은 상한이 되고 이 값을 다시 계산해야 한다.
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
