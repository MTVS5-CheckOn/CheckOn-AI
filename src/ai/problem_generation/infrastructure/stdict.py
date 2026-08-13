"""국립국어원 표준국어대사전 오픈 API 어댑터 — `LexiconLookup` 구현.

실측으로 확인한 API 특성(2026-08-05). 문서와 다른 부분이 있어 적어 둔다.

- **`view.do`는 XML만 응답한다.** `req_type=json`을 줘도 본문이 빈 문자열로
  온다(HTTP 200). 문서엔 JSON 예시가 있지만 실제로는 나오지 않아 두
  엔드포인트를 XML로 통일했다.
- **`sense_code`는 `view.do`에만 있다.** `search.do` 응답에는 `sense_no`뿐이라
  의미를 전역 식별할 수 없다. 그래서 `search.do`로 `target_code`를 얻고
  `view.do`로 의미를 채우는 2단 조회다.
- 결과가 없으면 `total`이 0이거나 컨테이너 자체가 비어 온다 — 오류가 아니다.
- 텍스트 노드에 들여쓰기·개행이 섞여 온다(`word_unit` 등). 전부 정리해서 쓴다.
"""

from __future__ import annotations

import xml.etree.ElementTree as ElementTree
from collections.abc import Iterable
from functools import lru_cache
from typing import Final

import httpx
from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from tenacity import AsyncRetrying, retry_if_exception_type, stop_after_attempt, wait_none

from ai.problem_generation.application.ports import LexiconUnavailable
from ai.problem_generation.domain.lexicon import (
    LEXICON_SOURCE_STDICT,
    LexiconEntry,
    LexiconSense,
)
from ai.runtime.env_files import ENV_FILES

#: 조회 1회당 상세를 채울 표제어 상한(불변식 6 — 모든 루프에 상한).
#: 동형어가 아무리 많아도 호출 수가 발산하지 않게 막는다.
DEFAULT_MAX_ENTRIES: Final = 10


class StdictSettings(BaseSettings):
    """표준국어대사전 오픈 API 접속 설정 — 값은 env 주입."""

    model_config = SettingsConfigDict(env_file=ENV_FILES, extra="ignore")

    stdict_api_key: SecretStr | None = None
    stdict_base_url: str = "https://stdict.korean.go.kr/api"
    stdict_timeout_s: float = Field(default=10.0, gt=0)
    stdict_max_entries: int = Field(default=DEFAULT_MAX_ENTRIES, ge=1, le=50)

    @property
    def configured(self) -> bool:
        return self.stdict_api_key is not None


@lru_cache
def get_stdict_settings() -> StdictSettings:
    """환경변수를 한 번 읽어 사전 접속 설정으로 반환한다."""
    return StdictSettings()


class StdictClient:
    """표준국어대사전 조회 어댑터.

    HTTP 클라이언트를 주입받는다 — 수명은 호출부가 관리한다(03 §1 외부 의존 주입).
    """

    def __init__(
        self,
        *,
        client: httpx.AsyncClient,
        settings: StdictSettings,
        transport_retry: int = 1,
    ) -> None:
        if settings.stdict_api_key is None:
            raise LexiconUnavailable(
                "STDICT_API_KEY가 없다 — 어휘 대조를 켤 수 없다(05 §1.1.3)."
            )
        if not 0 <= transport_retry <= 1:
            raise ValueError(
                f"transport_retry={transport_retry}는 0..1을 벗어난다 — 재시도 상한 위반(불변식 6)."
            )
        self._client = client
        self._key = settings.stdict_api_key.get_secret_value()
        self._base_url = settings.stdict_base_url.rstrip("/")
        self._timeout = settings.stdict_timeout_s
        self._max_entries = settings.stdict_max_entries
        self._attempts = transport_retry + 1

    async def lookup(self, word: str) -> tuple[LexiconEntry, ...]:
        """표제어를 정확히 일치로 조회한다. 사전에 없으면 빈 튜플."""
        target = word.strip()
        if not target:
            raise ValueError("조회할 표제어가 비었다")

        search = await self._get("search.do", q=target, advanced="y", method="exact")
        codes = _target_codes(search)[: self._max_entries]
        entries = []
        for code in codes:
            entry = _parse_entry(await self._get("view.do", q=code, method="target_code"))
            if entry is not None:
                entries.append(entry)
        return tuple(entries)

    async def _get(self, path: str, **params: str) -> ElementTree.Element | None:
        """XML을 받아 `channel` 엘리먼트로 돌려준다. 결과 없음이면 None."""
        params.update(key=self._key, req_type="xml")
        url = f"{self._base_url}/{path}"
        try:
            async for attempt in AsyncRetrying(
                stop=stop_after_attempt(self._attempts),
                wait=wait_none(),
                retry=retry_if_exception_type(httpx.TransportError),
                reraise=True,
            ):
                with attempt:
                    response = await self._client.get(
                        url, params=params, timeout=self._timeout
                    )
        except httpx.HTTPError as error:
            raise LexiconUnavailable(f"사전 조회 실패: {path}") from error

        if response.status_code != httpx.codes.OK:
            raise LexiconUnavailable(f"사전 조회 실패: {path} HTTP {response.status_code}")

        body = response.text.strip()
        if not body:
            return None
        try:
            root = ElementTree.fromstring(body)
        except ElementTree.ParseError as error:
            raise LexiconUnavailable(f"사전 응답을 해석할 수 없다: {path}") from error

        if root.tag == "error":
            code = _text(root.find("error_code")) or "?"
            message = _text(root.find("message")) or "?"
            raise LexiconUnavailable(f"사전 API 오류 {code}: {message}")

        channel = root if root.tag == "channel" else root.find("channel")
        if channel is None or _text(channel.find("total")) in {None, "0"}:
            return None
        return channel


def _text(node: ElementTree.Element | None) -> str | None:
    """텍스트를 정리해 꺼낸다 — 들여쓰기·개행이 섞여 오므로 공백을 접는다."""
    if node is None or node.text is None:
        return None
    collapsed = " ".join(node.text.split())
    return collapsed or None


def _target_codes(channel: ElementTree.Element | None) -> list[str]:
    """`search.do` 결과에서 표제어 ID를 순서대로 꺼낸다."""
    if channel is None:
        return []
    codes = (_text(item.find("target_code")) for item in channel.findall("item"))
    return [code for code in codes if code]


def _parse_entry(channel: ElementTree.Element | None) -> LexiconEntry | None:
    """`view.do` 결과 하나를 표제어 모델로 바꾼다. 의미가 없으면 None."""
    if channel is None:
        return None
    item = channel.find("item")
    if item is None:
        return None
    target_code = _text(item.find("target_code"))
    word_info = item.find("word_info")
    if target_code is None or word_info is None:
        return None
    word = _text(word_info.find("word"))
    if word is None:
        return None

    senses = tuple(_iter_senses(word_info))
    if not senses:
        return None
    return LexiconEntry(
        source=LEXICON_SOURCE_STDICT,
        target_code=target_code,
        word=word,
        sup_no=_text(word_info.find("sup_no")),
        senses=senses,
    )


def _iter_senses(word_info: ElementTree.Element) -> Iterable[LexiconSense]:
    """품사 컨테이너를 훑어 의미를 평평하게 편다.

    품사가 `pos_info` 안에 있어서 의미에서 위로 못 올라간다 — 품사를 들고
    내려가야 어휘 문항이 "같은 품사로 교체 가능한가"를 판정할 수 있다.
    """
    for pos_info in word_info.findall("pos_info"):
        pos = _text(pos_info.find("pos"))
        for sense in pos_info.findall(".//sense_info"):
            sense_code = _text(sense.find("sense_code"))
            definition = _text(sense.find("definition"))
            if sense_code is None or definition is None:
                continue
            examples = tuple(
                text
                for text in (
                    _text(example.find("example"))
                    for example in sense.findall("example_info")
                )
                if text
            )
            yield LexiconSense(
                sense_code=sense_code,
                definition=definition,
                pos=pos,
                pattern=_text(sense.find(".//pattern")),
                examples=examples,
            )


__all__ = [
    "DEFAULT_MAX_ENTRIES",
    "StdictClient",
    "StdictSettings",
    "get_stdict_settings",
]
