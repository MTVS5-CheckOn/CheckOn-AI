"""실 LLM 호출의 **명시적 허용** 게이트 — 모르면 안 부른다 (99 #32).

🔴 **(2026-08-14 재설계) 막는 자리를 「설정」에서 「테스트 진입점」으로 옮겼다.**

    종전:  `.env`를 안 읽는다            → 🔴 **운영 서버에서도 안 읽혀** 브리핑 11건이
                                            조용히 템플릿 폴백으로 나갔다(2026-08-14 윈도우
                                            실측 · 전건 `provider_error`).
                                            그리고 03 §1 「`os.environ` 직접 접근 금지」 위반.
    지금:  `.env`를 읽는다(설정)          ✅ 운영에서 먹는다
           + 테스트 진입점이 테스트에서 끈다  ✅ 99 #32 사고는 **여기서** 막는다

⚠ **막아야 할 것은 테스트지 설정이 아니었다.** 99 #32 사고는 `pytest -m integration`이
실 API를 부른 것이다. 설정을 숨겨서 그걸 막으면 **운영도 같이 막힌다** — 8/14에 그게 났다.

━━ 사고 원본 (2026-08-09) ━━

`pytest -m integration`이 사용자 키로 실 OpenAI API를 호출했다. 스모크들의 skip 조건이
`"localhost" in openai_base_url`이었고 `.env`의 `OPENAI_BASE_URL`이 **기본값을 덮어**
그 조건이 거짓이 됐다.

⚠ `llm/providers/openai_compat.py`가 *"기본을 localhost로 둬서 미설정을 안전하게 만든다
(fail-safe)"* 로 의도를 적어 뒀고 **그 판단은 옳다 — 기본값은 안 바꿨다.**
🔴 **다만 기본값이 안전한 것은 기본값이 쓰일 때뿐이고** `env_file=".env"`가 그 전제를 깬다.
⇒ **안전은 기본값이 아니라 「명시적 허용」에 걸어야 한다.**

    종전:  localhost가 아니면      → 부른다    🔴 fail-open (사고)
    지금:  명시적 opt-in이 없으면  → 안 부른다  ✅ fail-closed (불변식 3의 결)

━━ 🔴 방어는 **두 겹**이다 — 하나를 지울 때 다른 하나를 확인하라 ━━

    tests/ai/fakes/real_llm_optin_pin.py   pytest 세션 전체 — `.env`를 끊고 프로세스 env로 덮는다
    src/ai/evaluation/pre_pr_verify.py     PR 전 검증 — 켜져 있으면 시작 전에 실패 + 하위 env 제거

🔴 **둘 다 지워지면 게이트가 없다.** 특히 `pre_pr_verify`의 `env.pop`은 **프로세스 env만**
지운다 — `.env`를 읽는 지금은 그것만으로 부족하고 세션 핀이 그 구멍을 막는다.

━━ 실 LLM을 켜는 법 ━━

    운영   `.env`에 `CHECKON_ALLOW_REAL_LLM=1`          ✅ 어떤 기동 방식으로도 산다
           ⚠ **2026-08-14까지 거짓이었다** — 상대경로라 저장소 루트가 아닌 데서 기동하면
             못 찾고 **조용히 꺼졌다**(브리핑 전건 폴백의 나머지 절반 · 99 #73).
             지금은 `ENV_FILES` 앵커가 그 문장을 참으로 만든다(`runtime/env_files.py`).
    테스트 🔴 **셸에서 그 명령에만** —
           `CHECKON_ALLOW_REAL_LLM=1 uv run --frozen pytest tests/…`
           (`.env` 값은 세션 핀이 무시한다 · 프로세스 env > `.env`라 셸이 이긴다)

⚠ **`.env`에 `=1`을 둔 기계에서는 PR 전 검증을 명시로 꺼서 부른다**::

    CHECKON_ALLOW_REAL_LLM=0 uv run python -m ai.evaluation.pre_pr_verify

`pre_pr_verify`는 스위치가 켜져 있으면 **시작 전에 실패한다**(그게 두 겹 중 하나다).
`.env`를 읽게 된 지금은 **운영 설정을 로컬에 둔 기계가 여기서 걸린다** — 정상 동작이고,
`.env`를 고치는 게 아니라 **그 명령에만** `0`을 붙이는 것이 맞다.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Final

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from ai.runtime.env_files import ENV_FILES

#: 🔴 **실 LLM 호출의 유일한 허용 스위치의 정본 이름.** 이 이름을 바꾸면 문서·명령서·
#: `pre_pr_verify`(하위 프로세스 env 제거)·세션 핀이 **같이 낡는다.**
#: ⚠ 상수를 지우지 마라 — 설정 필드의 `alias`이자 `pre_pr_verify`가 쓰는 키다.
REAL_LLM_OPTIN_ENV: Final = "CHECKON_ALLOW_REAL_LLM"

#: ⚠ **켜는 값을 좁게 둔다** — 오타(`"0"`·`"false"`·빈 값·`"yep"`)로 열리면 fail-closed가
#: 아니다. 🔴 pydantic 기본 bool 파싱을 그대로 쓰면 모르는 값에 `ValidationError`가 나서
#: **브리핑이 500으로 죽는다** — 우리는 「모르면 꺼짐」이라야 한다.
_TRUTHY: Final = frozenset({"1", "true", "yes", "on"})


class RealLlmOptInRequired(RuntimeError):
    """명시적 허용 없이 실 LLM client를 만들려고 한 경우."""


class RealLlmSettings(BaseSettings):
    """실 LLM 허용 스위치 — `.env` 또는 환경 변수로 주입한다 (03 §1).

    🔴 **셸 전용이 아니다.** `.env`에 적어두면 어떤 기동 방식으로도 산다. 종전에는
    `os.environ`만 봐서 **운영 서버의 `.env`가 안 먹었다**(8/14 사고).

    🔴 **그 처방(#267)이 절반이었다** — `env_file=".env"`는 상대경로라 *"**작업 디렉터리**
    에서 읽는다"* 였고, 저장소 루트가 아닌 데서 기동하면 **못 찾고 조용히 `False`로**
    떨어졌다. **증상이 8/14 사고와 똑같고 원인만 다르다** ⇒ 브리핑 전건 템플릿 폴백.
    ⚠ 윈도우 서비스·작업 스케줄러는 작업 디렉터리가 저장소 루트가 아니다 — 맥·리눅스에서
    `cd` 해서 띄우면 **평생 안 보인다.** ⇒ `ENV_FILES` 앵커로 막았다(99 #73).

    ⚠ 우선순위는 **init 인자 > 프로세스 env > `.env`(CWD > 앵커) > 선언 기본값**이다.
    ⇒ 셸에서 켜는 기존 사용법(`CHECKON_ALLOW_REAL_LLM=1 uv run …`)이 **안 깨지고**,
    `monkeypatch.chdir` + 임시 `.env`로 값을 만드는 검사·세션 핀도 **그대로 산다.**
    """

    model_config = SettingsConfigDict(
        env_file=ENV_FILES, extra="ignore", populate_by_name=True
    )

    allow_real_llm: bool = Field(default=False, alias=REAL_LLM_OPTIN_ENV)
    """🔴 실 LLM 호출의 유일한 허용 스위치 (99 #32).

    **기본이 `False`다 — fail-closed.** 명시적으로 켜야 실 client가 만들어진다.
    """

    @field_validator("allow_real_llm", mode="before")
    @classmethod
    def _only_explicit_truthy(cls, value: object) -> bool:
        """🔴 **`_TRUTHY`에 있는 값만 켠다** — 모르는 값은 예외가 아니라 **꺼짐**이다.

        ⚠ pydantic 기본 bool 파싱은 `"yep"`에 `ValidationError`를 던진다. 그러면 오타
        하나가 **브리핑 전체를 500으로** 만든다. 여기서는 조용히 꺼지는 쪽이 맞다 —
        게이트의 목적은 「부르지 않는 것」이지 「죽는 것」이 아니다.
        """
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in _TRUTHY


def real_llm_optin() -> bool:
    """실 LLM 호출이 **명시적으로 허용**됐는가 — 🔴 `os.environ` 직접 접근을 하지 않는다(03 §1).

    ⚠ **캐시하지 않는다.** `lru_cache`를 걸면 첫 호출의 값이 굳어 세션 핀·`monkeypatch`가
    무력해지고(`store_backend_pin` 실측과 같은 함정), 운영에서도 `.env` 수정이 재기동
    전까지 안 먹는다. 이 함수는 client 생성 시점에만 불려서 비용이 문제되지 않는다.
    """
    return RealLlmSettings().allow_real_llm


def real_llm_skip_reason(base_url: str) -> str | None:
    """실 호출을 **하지 말아야 하는 사유** — 없으면 `None`.

    🔴 **판정은 opt-in 하나다.** 종전에는 `base_url`이 판정이었는데 그것이 사고의 원인이라
    **판정에서 뺐다** — 다만 사유 문면에 **어디를 가리키고 있었는지**를 실어 다음 사람이
    *"내 `.env`가 실서버를 가리키는군"* 을 알게 한다(값이 아니라 호스트만 남긴다).

    ⚠ **연결 실패로 인한 skip은 여기 축이 아니다** — 그건 호출자의 `except`가 계속 다룬다.
    이 함수는 *"부를지 말지"* 만 답하고 *"불렀는데 안 되면"* 은 안 다룬다.
    """
    if real_llm_optin():
        return None
    return (
        f"실 LLM 호출은 {REAL_LLM_OPTIN_ENV}=1 없이는 하지 않는다 — skip "
        f"(대상 호스트 {_host_of(base_url)} · 99 #32). "
        f"⚠ 운영은 `.env`에 넣어라. 테스트는 셸에서 그 명령에만 붙여라 — "
        f"pytest 세션은 `.env` 값을 무시한다"
    )


def build_real_openai_client[T](
    client_factory: Callable[..., T],
    *,
    denied_client_factory: Callable[[str], T],
    base_url: str,
    api_key: str,
    timeout_s: float,
) -> T:
    """유일한 opt-in 관문을 통과한 뒤 실 OpenAI client를 만든다.

    벤더 SDK import는 기존 경계대로 ``llm/providers``에만 둔다. 이 함수는 그 경계에서
    받은 생성자를 실행하므로, 허용 판정과 실제 client 생성 시점은 한곳에 고정된다.
    """
    reason = real_llm_skip_reason(base_url)
    if reason is not None:
        return denied_client_factory(reason)
    return client_factory(
        base_url=base_url,
        api_key=api_key,
        timeout=timeout_s,
        max_retries=0,
    )


def _host_of(base_url: str) -> str:
    """URL에서 호스트만 — 🔴 경로·쿼리·키가 로그에 남지 않게 한다."""
    without_scheme = base_url.split("://", 1)[-1]
    return without_scheme.split("/", 1)[0] or "?"
