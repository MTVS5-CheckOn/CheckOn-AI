"""콜당 상한 · 콜 수 · 예산 · lease · 04 문면이 **한 몸으로 선다** (99 ㉪).

🔴 **이 파일이 있는 이유** — `openai_timeout_s` 가 **15 → 45 → 90** 으로 두 번 바뀌는
동안 딸린 계산이 **두 번 다** 안 따라왔다:

  ⓐ K 유도식(`counsel_inline_drain_max` docstring) · ⓑ `counsel_lease_seconds` ·
  ⓒ `docs/04_api_contract.md` 의 네 자리 · ⓓ BE 향 read timeout 권고.

⚠ **기존 가드가 왜 못 잡았나** — `test_inline_drain_bound.py` 는 docstring 이 **자기 안에서**
일관적인지만 봤다(문면의 «콜당 45s» ↔ 문면의 «K=1»). 그 45 는 **아무것과도 안 묶인 리터럴**이라
실 상한이 90 이 돼도 **영원히 green** 이다. ⇒ 여기서 **바깥과 묶는다.**

🔴 **콜당 상한의 진짜 정본은 배포 env `OPENAI_TIMEOUT_S` 다.** 그걸 읽어 오지 않는 이유는
`OpenAiSettings()` 가 `.env` 를 타서 **검사가 환경에 의존**하기 때문이다(99 #109 가 그 형태다).
⇒ `LLM_CALL_TIMEOUT_S` 는 **사본**이고, 이 파일은 「사본이 문서·계산과 갈리지 않는가」를 잰다.
**env 와 사본이 갈린 것은 여기서 못 잡는다** — 그건 배포 축이다(99 ㉪).
"""

from __future__ import annotations

import pathlib
import re
from typing import Final

import pytest

from ai.api.routers.counsel import _REGEN_MAX
from ai.composition.counsel.assembly import DEFAULT_REGEN_MAX
from ai.composition.counsel.settings import (
    COUNSEL_TRANSPORT_ATTEMPTS,
    LLM_CALL_TIMEOUT_S,
    RESPONSE_BUDGET_S,
    WORST_CALLS_PER_DRAFT,
    WORST_CALLS_PER_REFINE,
    CounselSettings,
)
from ai.composition.provider import (
    BRIEFING_BUDGET_S,
    BRIEFING_CALL_TIMEOUT_S,
    DETECT_HTTP_TIMEOUT_S,
)
from ai.llm.gateway import LlmGateway

_CONTRACT: Final = pathlib.Path("docs/04_api_contract.md")


@pytest.fixture(scope="module")
def contract_text() -> str:
    return _CONTRACT.read_text(encoding="utf-8")


# ━━ 검사 2 — 잡당 최악 ≤ lease ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def test_the_worst_job_fits_inside_the_lease() -> None:
    """🔴 **깨지면 「회수가 느려진다」가 아니라 「같은 잡이 두 번 돈다」이다.**

    lease 가 잡당 최악보다 짧으면 만료되는 것은 **죽은 잡이 아니라 아직 실행 중인 잡**이고,
    `Supervisor.run_next` 가 *"만료 작업을 먼저 회수한 뒤"* lease 하므로 recovery 가 그걸
    다시 집는다 ⇒ LLM 비용 2배 · 학부모에게 갈 초안이 두 벌.

    ⚠ 종전 docstring 은 이 관계를 *"이미 만족한다"* 고만 적어 뒀고, 콜당이 45→90 이 되자
    **450 > 300 으로 조용히 깨졌다**(99 ㉪).
    """
    worst = WORST_CALLS_PER_DRAFT * LLM_CALL_TIMEOUT_S
    lease = CounselSettings().counsel_lease_seconds
    assert worst <= lease, (
        f"잡당 최악 {worst}s 가 lease({lease}s)를 넘는다 — 실행 중인 잡의 lease 가 만료돼 "
        "recovery 가 같은 잡을 다시 집는다(중복 실행)"
    )


def test_the_response_budget_holds_both_counsel_paths() -> None:
    """예산은 초안(5콜)뿐 아니라 **refine(4콜)** 도 담아야 한다 — 99 ㉫ 가 빠뜨렸던 축."""
    for label, calls in (("draft", WORST_CALLS_PER_DRAFT), ("refine", WORST_CALLS_PER_REFINE)):
        worst = calls * LLM_CALL_TIMEOUT_S
        assert worst <= RESPONSE_BUDGET_S, (
            f"{label} 최악 {worst}s 가 응답 예산({RESPONSE_BUDGET_S}s)을 넘는다"
        )


# ━━ 콜 수 상수가 실제 `regen_max` 와 묶여 있다 ━━━━━━━━━━━━━━━━━━━━━


def test_the_call_counts_are_tied_to_the_actual_regen_budget() -> None:
    """🔴 **예산 상수가 `regen_max` 와 묶인다** — 안 묶으면 예산이 콜 수를 안 따라간다.

    ⚠ `settings.py` 가 `DEFAULT_REGEN_MAX` 를 import 하면 순환이다(`assembly` → `settings`).
    ⇒ 리터럴로 두고 **여기서 묶는다.** `_REGEN_MAX` 를 바꾸면 이 검사가 red 다.
    """
    assert _REGEN_MAX == DEFAULT_REGEN_MAX, (
        f"라우터({_REGEN_MAX})와 조립부({DEFAULT_REGEN_MAX})의 재생성 상한이 갈렸다"
    )
    #: 재생성 N회 = 시도 N+1회(`range(regen_max + 1)` — 두 경로 공통).
    assert WORST_CALLS_PER_REFINE == _REGEN_MAX + 1, (
        f"refine 최악 콜 수({WORST_CALLS_PER_REFINE}) ≠ regen_max+1({_REGEN_MAX + 1})"
    )
    #: 초안은 그 앞에 `plan` 1콜이 더 붙는다. refine 에는 없다(강조점을 이어받는다).
    assert WORST_CALLS_PER_DRAFT == WORST_CALLS_PER_REFINE + 1, (
        f"초안 최악({WORST_CALLS_PER_DRAFT})이 refine({WORST_CALLS_PER_REFINE}) + plan 1 이 아니다"
    )


# ━━ 검사 4 — 04 문서 ↔ settings 코드 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def _one(pattern: str, text: str, what: str) -> int:
    found = re.findall(pattern, text)
    assert found, f"04 에서 {what} 를 못 읽었다 (패턴: {pattern})"
    values = {int(v) for v in found}
    assert len(values) == 1, f"04 안에서 {what} 가 서로 다르다: {sorted(values)}"
    return values.pop()


def test_the_contract_numbers_match_the_settings(contract_text: str) -> None:
    """🔴 **문서와 코드가 갈리는 것이 이 회차의 원인이다** — 그 둘을 잇는다.

    ⚠ 04 안에만 같은 숫자가 흩어진 자리가 넷이었다(:115 · §2.4 비동기 · §2.4 동기 · §5).
    한 자리만 고치는 것을 막으려면 **문서 안의 일관성도** 같이 봐야 한다(`_one`).
    """
    assert _one(r"콜당 상한 (\d+)초", contract_text, "콜당 상한") == LLM_CALL_TIMEOUT_S
    assert _one(r"콜당 상한은 (\d+)초", contract_text, "콜당 상한(refine 절)") == LLM_CALL_TIMEOUT_S
    assert _one(r"응답 예산 (\d+)초", contract_text, "응답 예산") == RESPONSE_BUDGET_S
    assert (
        _one(r"최악 (\d+)초까지 걸릴 수 있다", contract_text, "counsel POST 최악")
        == WORST_CALLS_PER_DRAFT * LLM_CALL_TIMEOUT_S
    )
    calls = _one(r"콜당 상한 \d+초 × 최악 (\d+)콜", contract_text, "최악 콜 수")
    assert calls == WORST_CALLS_PER_DRAFT


def test_the_refine_budget_table_matches_the_code(contract_text: str) -> None:
    """refine 행의 콜 수·소요가 코드와 같다 — 99 ㉫ 가 만든 표다."""
    row = re.search(
        r"\| `POST /counsel/drafts/\{job_id\}/refine` \| \*\*(\d+)콜\*\*.*?\| \*\*(\d+)초\*\*",
        contract_text,
    )
    assert row is not None, "04 에 refine 예산 행이 없다"
    assert int(row.group(1)) == WORST_CALLS_PER_REFINE
    assert int(row.group(2)) == WORST_CALLS_PER_REFINE * LLM_CALL_TIMEOUT_S

    draft = re.search(
        r"\| `POST /counsel/drafts` \| \*\*(\d+)콜\*\*.*?\| \*\*(\d+)초\*\*", contract_text
    )
    assert draft is not None, "04 에 초안 예산 행이 없다"
    assert int(draft.group(1)) == WORST_CALLS_PER_DRAFT
    assert int(draft.group(2)) == WORST_CALLS_PER_DRAFT * LLM_CALL_TIMEOUT_S


# ━━ 검사 5 — refine 타임아웃 문면이 04 에 있다 ━━━━━━━━━━━━━━━━━━━━


def test_the_contract_tells_backend_about_the_refine_timeout(contract_text: str) -> None:
    """⚠ **종전에는 `/drafts` 에만 안내가 있었다** — 실측(refine 18.4s)이 찾았다(99 ㉫).

    두 경로가 **같은 콜당 상한을 쓴다**는 사실이 문서에 없으면 BE 는 refine 을 짧은
    타임아웃으로 부르고, 게이트 재생성이 붙는 턴에서 끊긴다.
    """
    assert "refine` 도 같은 LLM 예산을 씁니다" in contract_text, (
        "04 에 refine 읽기 타임아웃 절이 없다"
    )
    section = contract_text.split("refine` 도 같은 LLM 예산을 씁니다", 1)[1][:1200]
    assert "read timeout" in section, "refine 절이 BE read timeout 을 안 말한다"
    assert str(RESPONSE_BUDGET_S) in section, (
        f"refine 절의 권고값이 응답 예산({RESPONSE_BUDGET_S}s)과 다르다"
    )


# ━━ detect 축 — 🔴 §G 가 counsel 에만 세운 관계를 여기에도 세운다 (99 #124) ━━


def test_the_worst_briefing_fits_inside_the_backend_timeout() -> None:
    """🔴 **좌변이 「총 예산」만이면 꼬리를 빼먹는다** — 그게 이 결함의 형태였다.

    `briefing.py` 의 예산 검사는 **호출 시작 전에만** 돈다(*"호출 전, 매 반복"* · 실측 8/20).
    ⇒ 마지막 호출은 예산 직전에 시작해 **콜당 상한만큼 더** 쓴다. 전역 상한이 45→90 이
    됐을 때 이 축의 최악이 조용히 45+90 = **135s** 가 됐고 04 의 BE **60s** 를 넘었다.
    ⚠ **아무도 안 봤다** — 99 ㉪ 는 counsel 만 봤다.
    """
    worst = BRIEFING_BUDGET_S + BRIEFING_CALL_TIMEOUT_S
    assert worst <= DETECT_HTTP_TIMEOUT_S, (
        f"브리핑 최악 {worst}s 가 BE 타임아웃({DETECT_HTTP_TIMEOUT_S}s)을 넘는다 — "
        "장애 때 BE 가 끊겨 폴백 문구조차 못 받는다"
    )


def test_the_briefing_does_not_ride_the_global_call_timeout() -> None:
    """🔴 **브리핑은 provider 전역 상한을 안 탄다** — 그 주입이 실제로 서는지 잰다.

    ⚠ 상수만 재면 「값은 맞는데 주입을 안 했다」를 못 잡는다 ⇒ **조립된 provider 의
    실효 타임아웃**을 본다. 선례: `problem_generation/provider.py` 가 verifier 에 같은
    형태로 자기 `OpenAiSettings` 를 준다.
    """
    from ai.composition.provider import BriefingSettings, build_brief_provider

    provider = build_brief_provider(BriefingSettings(llm_provider="openai_compat"))
    #: 🔴 실효값을 본다 — 상수만 재면 「값은 맞는데 주입을 안 했다」를 못 잡는다.
    effective = getattr(provider, "_settings").openai_timeout_s  # noqa: B009 — 실효값 확인
    assert effective == float(BRIEFING_CALL_TIMEOUT_S), (
        f"브리핑 provider 의 실효 타임아웃이 {effective}s 다 — "
        f"{BRIEFING_CALL_TIMEOUT_S}s 주입이 안 섰다(전역을 그대로 탄다)"
    )


def test_the_detect_contract_numbers_match_the_code(contract_text: str) -> None:
    """04 §2.4 `/detect` 행의 세 숫자가 코드 정본과 같다 — §G 의 「04 ↔ 코드」를 이 축에도."""
    assert _one(r"문장화 총 예산 (\d+)s", contract_text, "브리핑 총 예산") == int(BRIEFING_BUDGET_S)
    assert (
        _one(r"LLM 호출당 (\d+)s 상한", contract_text, "브리핑 콜당 상한")
        == BRIEFING_CALL_TIMEOUT_S
    )
    be = _one(r"타임아웃 (\d+)s \[A 확정", contract_text, "detect BE 타임아웃")
    assert be == DETECT_HTTP_TIMEOUT_S
    stated = _one(r"최악 (\d+) \+ \d+ = \d+s", contract_text, "최악 좌변")
    assert stated == int(BRIEFING_BUDGET_S)


# ━━ 🔴 관계망의 다섯 번째 입력 — 전송 재시도 (99 #125) ━━


def _gateways() -> list[tuple[str, LlmGateway]]:
    """A 소유 게이트웨이 전수 — 조립부가 늘면 여기도 늘어야 한다."""
    from ai.composition.classify.provider import build_classify_gateway
    from ai.composition.provider import build_brief_gateway

    return [("briefing", build_brief_gateway()), ("classify", build_classify_gateway())]


def test_every_registered_role_declares_its_transport_retry() -> None:
    """🔴 **기본값이 함정이다** — `_DEFAULT_TRANSPORT_RETRY = 1` 이라 `transport_retry` 에
    **안 적힌 role 은 시도 2회**이고, 그러면 콜당 최악이 **조용히 2배**가 된다.

    실측(8/20): A 소유 축은 미등록 **0건**이라 **지금 계산은 틀리지 않았다.**
    ⚠ 이 검사는 «지금 맞나» 가 아니라 **«새 role 을 붙이면서 빠뜨렸나»** 를 잡는다.
    """
    from ai.llm.gateway import _DEFAULT_TRANSPORT_RETRY

    assert _DEFAULT_TRANSPORT_RETRY != 0, (
        "기본값이 0 이 됐다면 이 검사의 전제가 바뀐 것이다 — 관계식을 다시 보라"
    )
    for name, gateway in _gateways():
        providers = gateway._providers
        retry = dict(gateway._transport_retry)
        missing = [role.value for role in providers if role not in retry]
        assert not missing, (
            f"{name}: provider 는 등록됐는데 transport_retry 가 없는 role {missing} — "
            f"기본값 {_DEFAULT_TRANSPORT_RETRY} 를 타서 콜당 최악이 "
            f"{_DEFAULT_TRANSPORT_RETRY + 1}배가 된다"
        )


def test_the_counsel_worst_case_multiplies_the_transport_attempts() -> None:
    """🔴 **식에 곱이 있다** — 없으면 곱하는 값이 바뀌어도 green 이다(㉪ 의 교훈).

    ⚠ №19 가 이 곱을 **발견은 했는데 식에 안 넣었다** — 세 축이 전부 0 이라 green 이었다.
    """
    from ai.composition.counsel.assembly import COUNSELOR_TRANSPORT_RETRY

    assert COUNSEL_TRANSPORT_ATTEMPTS == COUNSELOR_TRANSPORT_RETRY + 1, (
        f"설정의 시도 수({COUNSEL_TRANSPORT_ATTEMPTS})가 조립부"
        f"({COUNSELOR_TRANSPORT_RETRY} + 1)와 갈렸다"
    )
    worst = WORST_CALLS_PER_DRAFT * LLM_CALL_TIMEOUT_S * COUNSEL_TRANSPORT_ATTEMPTS
    lease = CounselSettings().counsel_lease_seconds
    assert worst <= lease, (
        f"재시도를 곱한 잡당 최악 {worst}s 가 lease({lease}s)를 넘는다 — "
        "실행 중인 잡의 lease 가 만료돼 중복 실행이 난다"
    )
    assert worst <= RESPONSE_BUDGET_S, (
        f"재시도를 곱한 잡당 최악 {worst}s 가 응답 예산({RESPONSE_BUDGET_S}s)을 넘는다"
    )


def test_the_briefing_worst_case_multiplies_the_transport_attempts() -> None:
    """브리핑 축도 같다 — `45 + 15 × (retry + 1) ≤ 60`."""
    from ai.composition.provider import _NARRATOR_TRANSPORT_RETRY

    attempts = _NARRATOR_TRANSPORT_RETRY + 1
    worst = BRIEFING_BUDGET_S + BRIEFING_CALL_TIMEOUT_S * attempts
    assert worst <= DETECT_HTTP_TIMEOUT_S, (
        f"재시도를 곱한 브리핑 최악 {worst}s 가 BE 타임아웃({DETECT_HTTP_TIMEOUT_S}s)을 넘는다"
    )


# ━━ §B — 커넥션 관계 (99 #127) ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def test_the_connection_budget_fits_inside_postgres() -> None:
    """🔴 **앱 워커 × (풀 + PG 드레인) + counsel 드레인 ≤ PG 상한**.

    ⚠ №20 ⑤ 의 실측(동시 300 → `TooManyConnectionsError` 35 · `OperationalError` 145)이
    이 관계가 없어서 났다. 인라인 요청의 풀 밖 체크포인터는 제거됐지만, 두 드레인은 잡을
    실행하는 동안 각각 풀 밖 체크포인터를 쓴다. PG 드레인의 직렬 실행 1개도 앱 워커 수를
    따라 늘어나므로 빠뜨리지 않는다.
    """
    from ai.composition.counsel.settings import CounselSettings
    from ai.db.settings import DbSettings
    from ai.problem_generation.application.drain import (
        PROBLEM_DRAIN_CONNECTIONS_PER_APP_WORKER,
    )

    db = DbSettings()
    counsel = CounselSettings()
    bounded = db.app_workers * (db.db_pool_size + db.db_max_overflow)
    bounded += db.app_workers * PROBLEM_DRAIN_CONNECTIONS_PER_APP_WORKER
    bounded += counsel.counsel_drain_concurrency
    assert bounded <= db.db_max_connections, (
        f"상한 있는 커넥션 {bounded} 가 PG max_connections({db.db_max_connections})를 넘는다 — "
        "앱 워커·풀·드레인 동시성 중 하나를 줄여라"
    )


def test_the_drain_concurrency_is_bounded() -> None:
    """드레인 동시성이 곧 커넥션 수다 — 상한이 없으면 §B 관계식이 성립하지 않는다."""
    from ai.composition.counsel.settings import CounselSettings

    assert CounselSettings().counsel_drain_concurrency >= 1
    import inspect

    doc = CounselSettings.model_fields["counsel_drain_concurrency"].description or ""
    if not doc:
        #: pydantic 이 docstring 을 description 으로 안 옮기는 배포도 있다 — 소스를 읽는다.
        doc = inspect.getsource(CounselSettings)
    assert "커넥션" in doc, "드레인 동시성 docstring 에 「커넥션 수와 같다」는 관계가 사라졌다"


def test_the_out_of_pool_connection_terms_are_named_and_bounded() -> None:
    """🔴 두 드레인의 풀 밖 커넥션이 관계식과 운영 문면에서 빠지지 않는다 (99 #127).

    현재 관계식은 다음 세 항이고 모두 상한이 있다:

        앱워커 × (pool + overflow)        ← 설정
      + 앱워커 × PG 드레인 1             ← 직렬 태스크 구조
      + counsel 드레인 동시성             ← 세마포어

    counsel POST가 체크포인터를 열지 않는 성질은 별도 lazy-runner 검사가 잰다. 여기서는
    남은 두 풀 밖 항이 설정 문면과 각 드레인 문면에 함께 존재하는지를 잰다.
    """
    import inspect

    from ai.composition.counsel import drain
    from ai.db.settings import DbSettings
    from ai.problem_generation.application import drain as problem_drain

    doc = inspect.getsource(DbSettings)
    assert "PG 드레인 1" in doc and "counsel 드레인 동시성" in doc, (
        "커넥션 관계식에서 두 드레인 중 하나가 사라졌다"
    )
    assert problem_drain.PROBLEM_DRAIN_CONNECTIONS_PER_APP_WORKER == 1
    assert "잡을 직렬 실행" in inspect.getsource(problem_drain)
    assert "커넥션 몫이 앱과 갈린다" in (drain.__doc__ or ""), (
        "드레인 docstring 에서 커넥션 몫 조건(#128 ③)이 사라졌다"
    )


def test_the_drain_docstring_carries_every_deployment_condition() -> None:
    """🔴 **compose 는 저장소 밖이라 정의에는 검사가 못 닿는다** — 조건만이라도 안에 둔다.

    ⚠ 99 #128 은 「결함」이 아니라 **「알려진 한계」**다. 배포 파일이 밖에 있는 것은 운영
    방식의 선택이고, 그 대신 **닿을 수 있는 만큼**(조건·식·절차)을 덮는다.
    """
    from ai.composition.counsel import drain

    doc = drain.__doc__ or ""
    for condition in ("migrate", "restart", "커넥션 몫", "env_file"):
        assert condition in doc, f"배포 조건 「{condition}」이 드레인 docstring 에서 사라졌다"
