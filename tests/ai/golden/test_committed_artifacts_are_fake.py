"""커밋된 산출물의 문면이 **fake 산출인가** (99 #58 보조).

🔴 **2026-08-13 — 실 LLM 문면이 산출물에 들어갔다.** 데모 재생성 중
`python -m ai.evaluation.demo_snapshot`이 맥 `.env`의 `LLM_PROVIDER=openai_compat`을 읽어
실 OpenAI를 불렀고, `detect_demo_response.json`의 brief가
*"이번 주 정답률이 평소 88%에서 62%로 떨어져 25%p 하락한 상태가 새로 나타났어요."* 로 바뀌었다.
**사람이 눈으로 알아채 되돌렸다.** 🔴 **눈에 의존하는 방어는 방어가 아니다.**

━━ 왜 요금보다 이게 무거운가 ━━

실 LLM 문면은 **재현이 안 된다**(동일 seed 8회 → 유일 문장 3종 · 99 ㊼). 골든에 들어가면
**그 파일을 그 뒤로 아무도 신뢰할 수 없고**, 언제 들어갔는지도 모른다. 요금은 청구서에
남지만 이건 아무 데도 안 남는다.

━━ ⚠ PR-π(관문)가 이것을 덮지 않는다 ━━

준영님 PR-π가 `build_real_openai_client()` 한 곳으로 관문을 모아 **실 client 생성**을
막는다. 그러나 **opt-in이 켜진 기계**(윈도우 AI 서버, 실측하는 맥)에서는 실물이 정상
동작한다 — 거기서 산출물을 재생성하면 **이 검사만이 막는다.**
⇒ 관문은 «부르는 것»을, 이 검사는 «커밋되는 것»을 막는다. 층이 다르다.

━━ 🔴 fake 를 **양성으로** 판별한다 ━━

*"실물처럼 보이면 red"* 가 아니라 *"fake 가 아니면 red"* 다. 실 LLM 문면은 **미리 알 수
없으므로** 블랙리스트가 성립하지 않는다. fake 산출은 결정론 템플릿이라 **집합이 닫혀 있다.**
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Final

import pytest

from ai.composition.counsel.provider import _DEFAULT_FAKE_TEXT
from ai.contracts.detection import SignalType
from ai.detection.brief import build_brief

_ROOT: Final = Path(__file__).resolve().parents[3]

#: 🔴 **글롭이다 — 목록을 하드코딩하지 않는다.** 새 산출물이 생기면 자동으로 걸려야 하고,
#: 손으로 적으면 **추가한 사람이 이 파일을 모른다.**
_ARTIFACT_GLOBS: Final = ("docs/part_a/examples/*.json", "src/ai/evaluation/golden/**/*.json")

#: 산출물에서 **문면이 실리는 키** — 이 이름이면 값이 사람이 읽는 문장이다.
_PROSE_KEY: Final = "text"

#: 🔴 **낡은 fake 문면 — 값과 사유를 등재한다.** 조용히 예외로 빼지 않는다.
#:
#: ⚠ **오염이 아니다(git 실측).** counsel 데모 4종은 2026-08-07에 **수동으로** 받은
#: 산출이고(`ea338a1` — README가 수동임을 명시), **그 시점 `_DEFAULT_FAKE_TEXT`가 정확히
#: 이 문장이었다**(`git show ea338a1:src/ai/composition/counsel/provider.py`). 이후
#: `536c04d`가 `gate_floor_draft`로 확장해 현재 fake는 8문장이다. ⇒ **낡은 것이지 실물이 아니다.**
#: ⚠ 커밋 메시지의 *"실물 산출"* 은 **별도 uvicorn 프로세스로 받았다**는 뜻이다
#: (그 커밋 본문이 그렇게 적고 있다).
#:
#: 🔴 **만료 조건이 걸려 있다** — 아래 `test_the_legacy_prose_registry_has_no_dead_entries`가
#: 「아무 산출물도 안 쓰는 등재」를 red로 만든다. 재생성하면 지우게 된다
#: (`test_redaction_coverage`의 `_PENDING_APPROVAL`과 같은 규약).
_LEGACY_FAKE_PROSE: Final = {
    "이번 주 학습 상황을 정리해 드립니다.": (
        "counsel 데모 수동 산출(2026-08-07 · ea338a1) 시점의 _DEFAULT_FAKE_TEXT"
    ),
}


def _artifacts() -> list[Path]:
    found: list[Path] = []
    for pattern in _ARTIFACT_GLOBS:
        found.extend(sorted(_ROOT.glob(pattern)))
    return found


def _prose(node: object) -> list[str]:
    """산출물에서 문면 값을 전부 긁는다(중첩 무관)."""
    out: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            if key == _PROSE_KEY and isinstance(value, str):
                out.append(value)
            else:
                out.extend(_prose(value))
    elif isinstance(node, list):
        for value in node:
            out.extend(_prose(value))
    return out


def _fake_brief_templates() -> frozenset[str]:
    """`FakeBriefProvider`가 낼 수 있는 문면 — 엔진 기본 템플릿 전량.

    ⚠ **리터럴로 안 적는다** — 템플릿이 바뀌면 여기가 같이 낡아 **검사가 정상 산출물을
    실물로 오판**한다. 정본(`detection/brief.py`)에서 뽑는다.
    """
    return frozenset(build_brief(signal_type).text for signal_type in SignalType)


def _is_fake(prose: str) -> bool:
    """이 문면이 **fake 산출로 설명되는가.**

    ⚠ `build_brief(st, detail)`은 `"{템플릿} ({detail})"` 형태라 접두 일치도 허용한다
    (R6의 셀 정보 등). 그 외는 전부 red다 — **모르는 문면은 실물로 본다**(fail-closed).
    """
    if prose == _DEFAULT_FAKE_TEXT:  # counsel fake의 고정 초안
        return True
    if prose in _LEGACY_FAKE_PROSE:  # 낡은 fake — 등재돼 있고 만료 가드가 붙어 있다
        return True
    return any(
        prose == template or prose.startswith(f"{template} (")
        for template in _fake_brief_templates()
    )


def test_the_artifact_glob_actually_matches_something() -> None:
    """🔴 **검사가 조용히 아무것도 안 도는 것**이 가장 나쁜 실패다.

    글롭 경로가 틀리면 «전부 통과»로 보인다 — 오늘 하루가 정확히 그 형태였다
    (핀 모듈이 자기 import로 자기를 통과시킨 것 · 99 #57·#63).
    """
    artifacts = _artifacts()
    assert artifacts, f"산출물을 하나도 못 찾았다 — 글롭이 틀렸다: {_ARTIFACT_GLOBS}"

    prose = [
        line
        for path in artifacts
        for line in _prose(json.loads(path.read_text(encoding="utf-8")))
    ]
    assert prose, "산출물은 찾았는데 문면이 0건이다 — 키 이름이 바뀌었나"


@pytest.mark.parametrize("artifact", _artifacts(), ids=lambda p: p.name)
def test_committed_artifact_carries_only_fake_prose(artifact: Path) -> None:
    """🔴 커밋된 산출물의 문면이 전부 fake 산출로 설명된다.

    red가 나면 **그 파일에 실 LLM 문면이 들어갔다는 뜻**이다. 되돌리고
    `LLM_PROVIDER=fake`를 명시해 재생성하라 — `.env`가 실물을 가리킬 수 있다(99 #58).
    """
    payload = json.loads(artifact.read_text(encoding="utf-8"))
    intruders = [line for line in _prose(payload) if not _is_fake(line)]

    assert not intruders, (
        f"{artifact.relative_to(_ROOT)}에 fake로 설명 안 되는 문면이 있다 "
        f"— 실 LLM 산출이 커밋된 것으로 본다: {intruders}"
    )


def test_the_legacy_prose_registry_has_no_dead_entries() -> None:
    """🔴 **낡은 문면 등재가 스스로 만료된다.**

    산출물이 재생성되면 그 문면은 사라지는데 등재만 남으면 **다음 사람이 그 예외를 사실로
    읽는다.** 아무 산출물도 안 쓰는 등재는 red로 만들어 지우게 한다.
    """
    live = {
        line
        for path in _artifacts()
        for line in _prose(json.loads(path.read_text(encoding="utf-8")))
    }
    dead = sorted(set(_LEGACY_FAKE_PROSE) - live)

    assert not dead, f"등재된 낡은 문면을 쓰는 산출물이 없다 — 재생성됐으면 등재를 지워라: {dead}"
