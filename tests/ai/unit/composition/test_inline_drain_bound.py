"""인라인 드레인 상한 K — **식과 값이 갈리지 않는다** (99 #106).

🔴 **이 PR 이 고친 것이 정확히 「식과 값이 갈렸다」이다.** 종전 도출식은
`K × 잡당(75s) ≤ lease(300s)` 였는데 두 가지가 틀렸다:

  ⓐ **콜당 15s 가 실 지연과 안 맞는다** — 실측(8/19 · n=27): 잡당 p50 **12.2s** ·
    p95 **23.1s** · max **25.3s** · **15s 초과 37%**. ⇒ 그 37% 는 `LlmTimeout` 이다.
  ⓑ **좌변이 `K ×` 인 것이 틀렸다** — lease 는 **잡당**이다(실측 8/20: `run_next` 가
    회전마다 `lease_next` 로 새로 잡고 종단 전이로 놓는다). K 를 정하는 것은 lease 가
    아니라 **「POST 가 HTTP 연결을 얼마나 쥐는가」**이고 그 상한은 04 §2.4 의 **300s** 다.

⇒ `K × 5콜 × 90s ≤ 480s` ⇒ **K = 1**. (8/20: 콜당 45→90 · 예산 300→480 · 99 ㉪)

⚠ **대가를 검사로 못 박는다** — K=1 이면 앞선 잡이 **하나만** 있어도 내 잡이 `queued` 다.
없으면 다음 사람이 「고착이 없다」로 읽는다.
"""

from __future__ import annotations

import inspect
import re
from collections.abc import Iterator
from typing import Final

import pytest

from ai.composition.counsel.settings import (
    LLM_CALL_TIMEOUT_S,
    RESPONSE_BUDGET_S,
    WORST_CALLS_PER_DRAFT,
    CounselSettings,
    get_counsel_settings,
)

#: 🔴 **종전에는 이 둘이 여기 박힌 리터럴이었다(5 · 300).** 그래서 콜당 상한이 45→90 이
#: 돼도 이 파일은 **영원히 green** 이었다 — 문면의 45 와 여기 300 이 둘 다 아무것과도
#: 안 묶여 있었기 때문이다(99 ㉪). ⇒ 정본(`settings`)에서 가져온다.
_WORST_CALLS_PER_JOB: Final = WORST_CALLS_PER_DRAFT
_RESPONSE_BUDGET_S: Final = RESPONSE_BUDGET_S


@pytest.fixture(autouse=True)
def _clear() -> Iterator[None]:
    get_counsel_settings.cache_clear()
    yield
    get_counsel_settings.cache_clear()


def test_the_bound_comes_from_settings_not_a_literal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """🔴 K 가 **설정**에서 온다 — env 로 주면 그 값이 회전 수다(03 §1)."""
    monkeypatch.setenv("COUNSEL_INLINE_DRAIN_MAX", "7")
    get_counsel_settings.cache_clear()
    assert get_counsel_settings().counsel_inline_drain_max == 7


def test_the_default_bound_is_one() -> None:
    """기본 K = 1 — 실 지연 실측에서 역산했다(99 #106)."""
    assert CounselSettings().counsel_inline_drain_max == 1


def test_the_derivation_in_the_docstring_matches_the_actual_values() -> None:
    """🔴 **식과 값이 같다** — 이 PR 이 고친 것이 정확히 그 갈림이다.

    ⚠ docstring 의 숫자만 낡게 두면 다음 사람이 **틀린 식으로 다시 계산한다.**
    실제로 그랬다: 종전 식의 «콜당 15s»·«실제로는 수 초» 가 실측과 어긋난 채 남아 있었고,
    그 위에서 K=3 이 정당화됐다.
    """
    doc = CounselSettings.model_fields["counsel_inline_drain_max"].description or ""
    if not doc:
        #: pydantic 이 docstring 을 description 으로 안 옮기는 배포도 있다 — 소스를 읽는다.
        doc = inspect.getsource(CounselSettings)

    k = CounselSettings().counsel_inline_drain_max
    timeout = _timeout_in(doc)
    assert timeout is not None, "도출식에서 콜당 상한을 못 읽었다"
    #: 🔴 **문면의 콜당이 정본과 같은가** — 종전에는 이 줄이 없어서 문면의 45 가
    #:   실 상한 90 과 갈려도 안 걸렸다(99 ㉪ · 이 PR 이 고친 축).
    assert timeout == LLM_CALL_TIMEOUT_S, (
        f"도출식의 콜당 상한({timeout}s)이 정본 LLM_CALL_TIMEOUT_S({LLM_CALL_TIMEOUT_S}s)와 다르다"
    )

    #: ⓐ 식이 쓰는 숫자가 실제 K 와 맞는가 — 결론 줄을 문면에서 읽는다.
    stated = re.search(r"⇒ \*\*K = ⌊\d+ / \d+⌋ = (\d+)\*\*", doc)
    assert stated is not None, "도출식의 결론 줄을 못 읽었다"
    assert int(stated.group(1)) == k, (
        f"docstring 이 말하는 K({stated.group(1)})와 실제 K({k})가 다르다"
    )
    #: ⓑ 🔴 **그 전제가 그 K를 실제로 함의하는가** — 상한만 보면 안 된다.
    #:   실측(고의 파괴 1-③): docstring 의 콜당을 45→15 로 낮춰도 `K=1` 은 예산 안이라
    #:   **green 이었다.** 그런데 15면 `⌊300/75⌋ = 4` 라 **K=1 의 근거가 사라진다** —
    #:   식이 자기 결론을 함의하지 않는 상태가 조용히 남는다.
    #:   ⇒ **K = ⌊예산 / 잡당최악⌋** 를 그대로 잰다.
    per_job = _WORST_CALLS_PER_JOB * timeout
    implied = _RESPONSE_BUDGET_S // per_job
    assert implied == k, (
        f"도출식이 K={implied} 를 함의하는데 실제 K는 {k} 다 "
        f"(콜당 {timeout}s · 잡당 {per_job}s · 예산 {_RESPONSE_BUDGET_S}s) — "
        "숫자 하나만 고치고 결론을 안 고친 것이다"
    )
    #: ⓒ **lease 는 잡당이다** — 좌변이 `K ×` 가 아니라는 판정이 식에 남아 있어야 한다.
    assert "lease 가 아니다" in doc or "lease가 아니다" in doc, (
        "lease 가 상한의 근거가 아니라는 판정이 도출식에서 사라졌다"
    )
    #: ⓓ 잡당 최악이 lease 안에 든다(그 제약은 여전히 있다).
    lease = CounselSettings().counsel_lease_seconds
    assert _WORST_CALLS_PER_JOB * timeout <= lease, (
        f"잡당 최악 {_WORST_CALLS_PER_JOB * timeout}s 가 lease({lease}s)를 넘는다 — "
        "fencing 이 무너져 같은 잡이 두 번 돈다"
    )


def _timeout_in(doc: str) -> int | None:
    """도출식이 쓰는 **콜당 상한**을 문면에서 읽는다."""
    m = re.search(r"콜당 상한\s*=\s*\*\*(\d+)s\*\*", doc)
    return int(m.group(1)) if m else None


def test_the_cost_of_k_one_is_written_down() -> None:
    """🔴 **대가가 문면에 있다** — K=1 이면 앞선 잡 **하나**로 `queued` 가 된다.

    ⚠ 이 단언이 없으면 다음 사람이 「고착이 없다」로 읽고 #85 ① 을 안 연다.
    """
    doc = inspect.getsource(CounselSettings)
    assert "응급처치" in doc, "K=1 이 응급처치라는 판정이 사라졌다"
    assert "#85" in doc, "진짜 처방(배경 드레인·비동기 워커)의 안건 번호가 사라졌다"
