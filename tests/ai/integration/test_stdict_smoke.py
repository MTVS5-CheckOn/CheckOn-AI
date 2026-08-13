"""표준국어대사전 실제 응답 계약을 네트워크 없이 재생한다.

기본 게이트는 2026-08-12에 수집한 원문 XML을 ``MockTransport``로 재생한다.
실 서비스 가용성은 ``CHECKON_ALLOW_REAL_STDICT=1``을 붙인 별도 external 실행만 확인한다.
"""

from __future__ import annotations

import asyncio
import base64
import gzip
import json
import os
from pathlib import Path
from typing import Any, Final

import httpx
import pytest
from pydantic import SecretStr

from ai.problem_generation.domain.lexicon import (
    LEXICON_SOURCE_STDICT,
    LexiconEntry,
    dict_entry_anchor,
)
from ai.problem_generation.infrastructure.stdict import (
    StdictClient,
    StdictSettings,
    get_stdict_settings,
)

_FIXTURE_PATH: Final = (
    Path(__file__).resolve().parents[1] / "fixtures" / "stdict" / "responses.json"
)
_REAL_STDICT_OPTIN_ENV: Final = "CHECKON_ALLOW_REAL_STDICT"
_TRUTHY: Final = frozenset({"1", "true", "yes", "on"})


def _fixture_responses() -> dict[str, bytes]:
    payload: dict[str, Any] = json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))
    assert payload["captured_on"] == "2026-08-12"
    assert set(payload["queries"]) == {
        "search_write",
        "search_unknown",
        "view_215568",
        "view_451789",
    }
    return {
        name: gzip.decompress(base64.b64decode(encoded))
        for name, encoded in payload["responses"].items()
    }


def _fixture_handler(request: httpx.Request) -> httpx.Response:
    responses = _fixture_responses()
    path = request.url.path.rsplit("/", 1)[-1]
    query = request.url.params.get("q")
    key = (
        "search_unknown"
        if path == "search.do" and query == "깝치다ㅋ"
        else "search_write"
        if path == "search.do" and query == "쓰다"
        else f"view_{query}"
    )
    return httpx.Response(200, content=responses[key])


def _fixture_settings() -> StdictSettings:
    return StdictSettings(
        stdict_api_key=SecretStr("fixture-key"),
        stdict_base_url="https://fixture.invalid/api",
        stdict_max_entries=2,
        _env_file=None,
    )


async def _lookup(
    settings: StdictSettings,
    word: str,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> tuple[LexiconEntry, ...]:
    async with httpx.AsyncClient(transport=transport) as http:
        client = StdictClient(client=http, settings=settings)
        return await client.lookup(word)


@pytest.fixture(scope="module")
def entries() -> tuple[LexiconEntry, ...]:
    transport = httpx.MockTransport(_fixture_handler)
    return asyncio.run(_lookup(_fixture_settings(), "쓰다", transport=transport))


def test_polysemous_word_yields_sense_codes(entries: tuple[LexiconEntry, ...]) -> None:
    """실제 2단 응답에서 동형어·다의어의 고유 의미 코드가 보존된다."""
    assert all(entry.source == LEXICON_SOURCE_STDICT for entry in entries)
    assert len({entry.target_code for entry in entries}) > 1

    codes = [sense.sense_code for entry in entries for sense in entry.senses]
    assert len(codes) == len(set(codes)), "의미 코드가 중복되면 근거를 특정할 수 없다"
    assert len(codes) > 5, f"의미가 너무 적다: {len(codes)}"


def test_unknown_word_is_empty_not_error() -> None:
    """실제 결과 0 XML은 오류가 아니라 빈 결과로 해석된다."""
    transport = httpx.MockTransport(_fixture_handler)
    assert asyncio.run(
        _lookup(_fixture_settings(), "깝치다ㅋ", transport=transport)
    ) == ()


def test_anchor_round_trip(entries: tuple[LexiconEntry, ...]) -> None:
    """실 응답으로 만든 근거 앵커가 계약을 통과한다."""
    entry = entries[0]
    anchor = dict_entry_anchor(entry, entry.senses[0])
    assert anchor.ref.startswith(f"{LEXICON_SOURCE_STDICT}:")
    assert anchor.quote is None, "뜻풀이를 산출물에 복제하지 않는다(05 §1.1.3)"


def test_text_nodes_are_cleaned(entries: tuple[LexiconEntry, ...]) -> None:
    """실 응답의 들여쓰기·개행이 정리된다."""
    for entry in entries:
        assert entry.word == entry.word.strip()
        assert "\n" not in entry.word
        for sense in entry.senses:
            assert "\n" not in sense.definition
            assert sense.definition == sense.definition.strip()


@pytest.mark.external
def test_real_service_round_trip() -> None:
    """명시적 옵트인 때만 실 서비스 계약 드리프트를 확인한다."""
    allowed = os.environ.get(_REAL_STDICT_OPTIN_ENV, "").strip().lower() in _TRUTHY
    if not allowed:
        pytest.fail(
            f"실행 전에 {_REAL_STDICT_OPTIN_ENV}=1을 프로세스 환경에 지정해야 한다"
        )
    settings = get_stdict_settings()
    if not settings.configured:
        pytest.fail("STDICT_API_KEY가 설정되지 않았다")
    entries = asyncio.run(_lookup(settings, "쓰다"))
    assert entries
    assert len({entry.target_code for entry in entries}) > 1
