"""서킷 임계가 **뜻이 되는 값**만 받는다 — `counsel_llm_failure_circuit >= 2` (99 #08 ⓐ).

🔴 **`1` 은 「연속 실패」가 아니라 「단발 실패」다** — 서킷이라는 개념 자체와 어긋난다.
실측(2026-08-19): `COUNSEL_LLM_FAILURE_CIRCUIT=1` 로 주면 **기동이 됐고** 첫 LLM 실패에
잡이 `paused` 로 갔다. 그 상태를 푸는 **HTTP 표면이 없어** 고착이었다(99 ㉖).

🔴 **⚠ 이 가드는 서킷을 열지 않는다.** 카운터가 **학생 단위**인데 counsel 은 N=1 이라
**최대 1**이다 — 2로 올려도 안 열린다. **막은 것은 「뜻이 안 되는 설정」이지 서킷이 아니다.**
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from pydantic import ValidationError

from ai.composition.counsel.settings import get_counsel_settings


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> Iterator[None]:
    """🔴 `get_counsel_settings` 는 `@lru_cache` 다 — **검사마다 비운다.**

    안 비우면 앞 검사가 읽은 값이 굳어 `monkeypatch.setenv` 가 **아무 효과가 없다**
    (`store_backend_pin` 이 등재한 함정과 같은 형태).
    """
    get_counsel_settings.cache_clear()
    yield
    get_counsel_settings.cache_clear()


def test_a_circuit_of_one_is_rejected_at_boot(monkeypatch: pytest.MonkeyPatch) -> None:
    """🔴 `1` 은 기동에서 거부된다 — 실측 재현이 이 검사의 입력이다.

    ⚠ **설정 로딩 경로를 통해서 잰다** — 클래스를 직접 만들면 `env_file`·`lru_cache`
    경로를 안 지난다.
    """
    monkeypatch.setenv("COUNSEL_LLM_FAILURE_CIRCUIT", "1")

    with pytest.raises(ValidationError) as excinfo:
        get_counsel_settings()

    assert "counsel_llm_failure_circuit" in str(excinfo.value)


@pytest.mark.parametrize("value", ["2", "3", "10"])
def test_two_or_more_is_accepted(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    """⚠ **경계(2)를 포함한다** — `>= 2` 인지 `> 2` 인지가 갈리는 자리다.

    🔴 한쪽만 보면 *"전부 거부"* 로 짜도 통과한다.
    """
    monkeypatch.setenv("COUNSEL_LLM_FAILURE_CIRCUIT", value)

    assert get_counsel_settings().counsel_llm_failure_circuit == int(value)


def test_zero_and_negative_are_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """⚠ 1 아래도 같은 이유로 막힌다 — 「연속 0회 실패」는 뜻이 없다."""
    for value in ("0", "-1"):
        get_counsel_settings.cache_clear()
        monkeypatch.setenv("COUNSEL_LLM_FAILURE_CIRCUIT", value)
        with pytest.raises(ValidationError):
            get_counsel_settings()
