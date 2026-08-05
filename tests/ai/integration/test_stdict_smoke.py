"""표준국어대사전 실 API 왕복 — 계약이 실제 응답과 맞는지.

단위 테스트는 우리가 쓴 XML을 우리가 읽는다. 응답 형태가 바뀌거나 문서와
다르면 여기서만 잡힌다 — 실제로 `view.do`의 JSON 미응답이 그렇게 드러났다.

실행:  uv run pytest tests/ai/integration/test_stdict_smoke.py -m integration
전제:  .env에 STDICT_API_KEY
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

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

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def settings() -> StdictSettings:
    resolved = get_stdict_settings()
    if not resolved.configured:
        pytest.skip("STDICT_API_KEY 미설정 — 실 사전 호출을 건너뛴다")
    return resolved


async def _lookup(settings: StdictSettings, word: str) -> tuple[LexiconEntry, ...]:
    async with httpx.AsyncClient() as http:
        client = StdictClient(client=http, settings=settings)
        return await client.lookup(word)


def test_polysemous_word_yields_sense_codes(settings: StdictSettings) -> None:
    """다의어는 의미마다 고유 코드가 와야 한다 — 어휘 문항의 대조 단위."""
    entries = asyncio.run(_lookup(settings, "쓰다"))

    assert entries, "'쓰다'는 표제어가 있어야 한다"
    assert all(entry.source == LEXICON_SOURCE_STDICT for entry in entries)

    codes = [sense.sense_code for entry in entries for sense in entry.senses]
    assert len(codes) == len(set(codes)), "의미 코드가 중복되면 근거를 특정할 수 없다"
    assert len(codes) > 5, f"의미가 너무 적다: {len(codes)}"

    # 동형어 구분 — 표제어만으로는 특정되지 않는다는 전제
    assert len({entry.target_code for entry in entries}) > 1

    definitions = [sense.definition for entry in entries for sense in entry.senses]
    assert all(text.strip() for text in definitions), "뜻풀이가 비면 대조할 것이 없다"


def test_unknown_word_is_empty_not_error(settings: StdictSettings) -> None:
    """사전에 없는 말은 빈 결과다 — R-1의 '표제어 없음' 판정이 성립해야 한다."""
    assert asyncio.run(_lookup(settings, "깝치다ㅋ")) == ()


def test_anchor_round_trip(settings: StdictSettings) -> None:
    """실 응답으로 만든 근거 앵커가 계약을 통과해야 한다."""
    entries = asyncio.run(_lookup(settings, "나무"))
    assert entries

    entry = entries[0]
    anchor = dict_entry_anchor(entry, entry.senses[0])
    assert anchor.ref.startswith(f"{LEXICON_SOURCE_STDICT}:")
    assert anchor.quote is None, "뜻풀이를 산출물에 복제하지 않는다(05 §1.1.3)"


def test_text_nodes_are_cleaned(settings: StdictSettings) -> None:
    """응답에 들여쓰기·개행이 섞여 오므로 정리된 값이어야 한다."""
    entries = asyncio.run(_lookup(settings, "나무"))
    assert entries

    for entry in entries:
        assert entry.word == entry.word.strip()
        assert "\n" not in entry.word
        for sense in entry.senses:
            assert "\n" not in sense.definition
            assert sense.definition == sense.definition.strip()
