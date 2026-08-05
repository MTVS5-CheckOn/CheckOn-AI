"""어휘 사전 대조 도메인 — 조회 결과 모델과 근거 참조 형식.

R-1이 T2 어휘 문항을 대조할 때 쓰는 순수 모델이다. 어느 사전을 쓰든 모양이
같아야 하므로 사전 이름은 값으로만 들어온다 — 네트워크·파싱은 infrastructure.

**의미(sense)가 대조 단위다.** 표제어만으로는 특정되지 않는다: `쓰다`는
표제어가 6개고 표제어마다 다시 여러 의미를 가진다(실측). 어휘 문항은
"문맥상 ⓐ와 가장 가까운 의미"처럼 **어느 의미인지**를 묻기 때문에
근거를 의미 단위로 걸지 않으면 대조가 성립하지 않는다.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from ai.contracts.problem_generation import EvidenceAnchor, EvidenceKind

#: 국립국어원 표준국어대사전. `05` §1.1.3.
LEXICON_SOURCE_STDICT = "표준국어대사전"


class LexiconSense(BaseModel):
    """표제어의 개별 의미 — 근거 참조의 단위."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: 사전이 부여한 의미 고유 ID. 근거 참조의 정본이다.
    sense_code: str = Field(min_length=1)
    definition: str = Field(min_length=1)
    pos: str | None = Field(default=None, min_length=1)
    #: 문형(`…에 -고` 등). 어휘 문항의 교체 가능성 판정에 쓴다.
    pattern: str | None = Field(default=None, min_length=1)
    examples: tuple[str, ...] = ()


class LexiconEntry(BaseModel):
    """표제어 하나 — 어깨번호까지 포함해 동형어를 구분한다."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source: str = Field(min_length=1)
    #: 사전 내부 표제어 ID.
    target_code: str = Field(min_length=1)
    word: str = Field(min_length=1)
    #: 어깨번호 — `쓰다1`의 `1`. 동형어가 없으면 없다.
    sup_no: str | None = Field(default=None, min_length=1)
    senses: tuple[LexiconSense, ...] = Field(min_length=1)


def dict_entry_ref(source: str, sense_code: str) -> str:
    """`EvidenceAnchor.ref` 문자열 — `{사전}:{의미코드}`.

    `grammar_rule` ref가 `어문규범:{시행일}:{규정코드}:{경로}`인 것과 같은
    자리다(`05` §1.1.1). 사전은 의미코드 자체가 고유하므로 판이 더 짧다.
    """
    if not source.strip() or not sense_code.strip():
        raise ValueError("dict_entry 근거 참조에는 사전 이름과 의미코드가 모두 필요하다")
    return f"{source.strip()}:{sense_code.strip()}"


def dict_entry_anchor(entry: LexiconEntry, sense: LexiconSense) -> EvidenceAnchor:
    """조회 결과를 문항 근거 앵커로 바꾼다.

    **뜻풀이 원문을 `quote`에 넣지 않는다.** 사전 본문은 CC BY-SA(동일조건
    변경허락)라 산출물에 복제하면 라이선스 전파 여부가 문제가 된다(`05`
    §1.1.3). 참조만 남기면 대조 근거로 충분하고 복제가 아니다 — 계약상
    `dict_entry`는 `quote`가 선택이라 그대로 성립한다.
    """
    return EvidenceAnchor(
        kind=EvidenceKind.DICT_ENTRY,
        ref=dict_entry_ref(entry.source, sense.sense_code),
        quote=None,
    )


__all__ = [
    "LEXICON_SOURCE_STDICT",
    "LexiconEntry",
    "LexiconSense",
    "dict_entry_anchor",
    "dict_entry_ref",
]
