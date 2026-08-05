"""어휘 사전 대조 — 근거 참조 형식과 표준국어대사전 어댑터.

R-1이 T2 어휘 문항을 대조할 때 쓰는 경로다. 조회 실패를 "없는 말"로 삼키면
대조 없이 통과하는 길이 생기므로(불변식 2), 그 구분을 여기서 고정한다.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable

import httpx
import pytest
from pydantic import SecretStr, ValidationError

from ai.contracts.problem_generation import EvidenceKind
from ai.problem_generation.application.ports import LexiconUnavailable
from ai.problem_generation.domain.lexicon import (
    LEXICON_SOURCE_STDICT,
    LexiconEntry,
    LexiconSense,
    dict_entry_anchor,
    dict_entry_ref,
)
from ai.problem_generation.infrastructure.stdict import (
    DEFAULT_MAX_ENTRIES,
    StdictClient,
    StdictSettings,
)

# --------------------------------------------------------------------------- 근거 참조


def _sense(code: str = "484613") -> LexiconSense:
    return LexiconSense(sense_code=code, definition="글로 나타내다.", pos="동사")


def _entry(*senses: LexiconSense) -> LexiconEntry:
    return LexiconEntry(
        source=LEXICON_SOURCE_STDICT,
        target_code="215568",
        word="쓰다",
        sup_no="1",
        senses=senses or (_sense(),),
    )


def test_dict_entry_ref_is_source_and_sense_code() -> None:
    assert dict_entry_ref(LEXICON_SOURCE_STDICT, "484613") == "표준국어대사전:484613"


@pytest.mark.parametrize(("source", "code"), [("", "484613"), ("사전", "  ")])
def test_dict_entry_ref_rejects_blank_parts(source: str, code: str) -> None:
    """빈 참조를 만들면 근거가 있는 척하는 앵커가 생긴다(불변식 2)."""
    with pytest.raises(ValueError, match="사전 이름과 의미코드"):
        dict_entry_ref(source, code)


def test_dict_entry_anchor_does_not_copy_definition() -> None:
    """뜻풀이를 quote에 복제하지 않는다 — CC BY-SA 전파 회피(05 §1.1.3)."""
    anchor = dict_entry_anchor(_entry(), _sense())
    assert anchor.kind is EvidenceKind.DICT_ENTRY
    assert anchor.ref == "표준국어대사전:484613"
    assert anchor.quote is None


def test_entry_requires_at_least_one_sense() -> None:
    """의미가 없는 표제어는 대조 단위가 없다."""
    with pytest.raises(ValidationError):
        LexiconEntry(
            source=LEXICON_SOURCE_STDICT,
            target_code="1",
            word="쓰다",
            sup_no=None,
            senses=(),
        )


# --------------------------------------------------------------------------- 어댑터

_SEARCH_XML = """<?xml version="1.0" encoding="UTF-8"?>
<channel>
  <total>2</total>
  <item><target_code>215568</target_code><word>쓰다</word></item>
  <item><target_code>451789</target_code><word>쓰다</word></item>
</channel>
"""

#: 실제 응답처럼 텍스트 노드에 들여쓰기·개행을 섞어 둔다.
_VIEW_XML = """<?xml version="1.0" encoding="UTF-8"?>
<channel>
  <total>1</total>
  <item>
    <target_code>215568</target_code>
    <word_info>
      <word>
        쓰다
      </word>
      <sup_no>1</sup_no>
      <pos_info>
        <pos>동사</pos>
        <comm_pattern_info>
          <sense_info>
            <sense_code>481118</sense_code>
            <definition>획을 그어서 일정한 글자의 모양이 이루어지게 하다.</definition>
            <pattern>…에 …을</pattern>
            <example_info><example>연습장에 붓글씨를 쓰다.</example></example_info>
            <example_info><example>글씨를 또박또박 쓰다.</example></example_info>
          </sense_info>
          <sense_info>
            <sense_code>484613</sense_code>
            <definition>머릿속의 생각을 글로 나타내다.</definition>
          </sense_info>
        </comm_pattern_info>
      </pos_info>
    </word_info>
  </item>
</channel>
"""

_EMPTY_XML = '<?xml version="1.0" encoding="UTF-8"?>\n<channel><total>0</total></channel>\n'
_ERROR_XML = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    "<error><error_code>020</error_code><message>Unregistered key</message></error>\n"
)


_Handler = Callable[[httpx.Request], httpx.Response]


def _settings(max_entries: int = DEFAULT_MAX_ENTRIES) -> StdictSettings:
    """env 오염을 막는다 — 실제 .env가 있으면 테스트가 환경에 따라 흔들린다."""
    return StdictSettings(
        stdict_api_key=SecretStr("test-key"),
        stdict_base_url="https://example.invalid/api",
        stdict_max_entries=max_entries,
        _env_file=None,
    )


def _client(handler: _Handler) -> StdictClient:
    return StdictClient(
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        settings=_settings(),
    )


def _route(**body_by_path: str) -> _Handler:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path.rsplit("/", 1)[-1].replace(".", "_")
        return httpx.Response(200, text=body_by_path[path])

    return handler


def test_lookup_parses_senses_with_pos_and_examples() -> None:
    client = _client(_route(search_do=_SEARCH_XML, view_do=_VIEW_XML))
    entries = asyncio.run(client.lookup("쓰다"))

    assert len(entries) == 2, "search.do가 준 표제어마다 상세를 채워야 한다"
    entry = entries[0]
    assert entry.word == "쓰다", "개행이 섞인 텍스트를 정리해야 한다"
    assert entry.sup_no == "1"
    assert entry.source == LEXICON_SOURCE_STDICT
    assert [s.sense_code for s in entry.senses] == ["481118", "484613"]
    first = entry.senses[0]
    assert first.pos == "동사", "품사는 pos_info에 있어 의미로 들고 내려와야 한다"
    assert first.pattern == "…에 …을"
    assert first.examples == ("연습장에 붓글씨를 쓰다.", "글씨를 또박또박 쓰다.")
    assert entry.senses[1].examples == ()


def test_lookup_of_unknown_word_returns_empty() -> None:
    """사전에 없는 말은 오류가 아니다 — R-1의 '표제어 없음' 판정 경로."""
    client = _client(_route(search_do=_EMPTY_XML))
    assert asyncio.run(client.lookup("깝치다ㅋ")) == ()


def test_api_error_is_not_silently_empty() -> None:
    """오류를 빈 결과로 삼키면 대조 없이 통과한다(불변식 2)."""
    client = _client(_route(search_do=_ERROR_XML))
    with pytest.raises(LexiconUnavailable, match="020"):
        asyncio.run(client.lookup("쓰다"))


def test_http_failure_raises_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="")

    client = _client(handler)
    with pytest.raises(LexiconUnavailable, match="503"):
        asyncio.run(client.lookup("쓰다"))


def test_transport_error_raises_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    client = _client(handler)
    with pytest.raises(LexiconUnavailable, match="사전 조회 실패"):
        asyncio.run(client.lookup("쓰다"))


def test_max_entries_bounds_detail_calls() -> None:
    """동형어가 많아도 호출 수가 발산하지 않는다(불변식 6)."""
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path.rsplit("/", 1)[-1]
        calls.append(path)
        return httpx.Response(200, text=_SEARCH_XML if path == "search.do" else _VIEW_XML)

    client = StdictClient(
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        settings=_settings(max_entries=1),
    )
    assert len(asyncio.run(client.lookup("쓰다"))) == 1
    assert calls == ["search.do", "view.do"]


def test_missing_key_fails_closed() -> None:
    """키 없이 조립되면 조회가 조용히 비는 대신 기동에서 막힌다."""
    settings = StdictSettings(stdict_api_key=None, _env_file=None)
    with pytest.raises(LexiconUnavailable, match="STDICT_API_KEY"):
        StdictClient(client=httpx.AsyncClient(), settings=settings)


def test_retry_budget_is_bounded() -> None:
    with pytest.raises(ValueError, match="재시도 상한"):
        StdictClient(
            client=httpx.AsyncClient(),
            settings=_settings(),
            transport_retry=3,
        )
