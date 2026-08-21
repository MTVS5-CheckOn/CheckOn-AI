"""🔴 04 §3.7 의 **전제**와 **저장 정책**이 문면에서 사라지는지 잰다 (99 #175 · 로그 174).

⚠ **값을 재는 검사가 아니다** — 라벨 제안은 아직 **코드가 0줄**이다. 이 검사가 무는 것은
「사람 결정이 계약 문면에 살아 있는가」이고, 선례는 `test_canonical_serialization_doc.py`(#359)다.

🔴 **왜 필요한가** — 8/21 에 사람 결정 둘이 났다: ⓐ 문의는 **앱 경로로만** 들어온다(승우님
동의) ⇒ 이력이 있는 학부모는 전원 가입·동의자다 ⓑ 제안은 **확정 안 하면 버린다** ⇒ 새로
쌓이는 개인 데이터가 없다. **이 둘이 「동의 게이트가 없어도 되는」 유일한 근거**다.
⚠ 🔴 **그런데 둘 다 코드로 표현되지 않는다** — `guardian_consent` 는 v1 미사용이고, 버리는
것은 「안 만드는 것」이라 잴 대상이 없다. 근거가 **산문에만** 있으면 산문이 지워질 때
**아무것도 red 가 되지 않는다.** ⇒ 문면 자신을 무는 것 말고 방법이 없다.

⚠ **하지 않는 것**: 문장 완전일치(#359 가 안 쓰기로 한 방식 — 산문을 다듬으면 red 가 된다) ·
문서를 파싱해 표를 구조화(№44 §3-2 — 문서가 코드가 된다) · 04 전문 복제(정본이 둘이 된다).
⇒ **절을 뽑아 핵심 낱말이 다 있는가**만 본다. 다듬어도 통과하고 **사실이 사라지면 red** 다.
"""

from __future__ import annotations

import pathlib
import re
from typing import Final

import pytest

_DOC: Final = pathlib.Path("docs/04_api_contract.md")
_SECTION: Final = "3.7"
_SIBLING: Final = pathlib.Path("docs/part_a/12_label_discovery.md")


@pytest.fixture(scope="module")
def section() -> str:
    """04 §3.7 절만 — `### 3.7` 부터 다음 `### ` 전까지.

    🔴 **못 찾으면 그것도 red 다.** 번호가 바뀌면 검사가 조용히 빈 문자열을 보고
    「사실이 없다」가 아니라 「검사가 눈이 멀었다」가 되는데, 둘은 구분돼야 한다.
    """
    body = _DOC.read_text(encoding="utf-8")
    match = re.search(rf"^### {re.escape(_SECTION)}[ `].*?(?=^### |\Z)", body, re.M | re.S)
    assert match is not None, (
        f"{_DOC} 에서 §{_SECTION} 절을 못 찾았다 — 절 번호가 바뀌었다면 이 검사의 "
        f"`_SECTION` 도 같이 고쳐라. **사실이 사라진 것과 검사가 눈먼 것은 다르다.**"
    )
    text = match.group(0)
    assert len(text) > 500, f"§{_SECTION} 절이 {len(text)}자다 — 절 추출이 잘못됐다"
    return text


@pytest.mark.parametrize("word", ("앱", "동의", "가입"))
def test_the_app_only_premise_is_still_in_the_section(word: str, section: str) -> None:
    """① 전제(앱 경로 단일화 ⇒ 전원 동의자)가 **같은 절 안에** 살아 있다.

    🔴 낱말 셋이 **한 절 안에** 있어야 한다 — 셋이 각자 다른 절에 흩어져 있으면 그 문장은
    이미 없는 것이다.
    """
    assert word in section, (
        f"04 §{_SECTION} 의 전제(앱 경로 단일화 ⇒ 전원 가입·동의자)에서 「{word}」가 사라졌다. "
        f"🔴 유입 경로가 정말 바뀌었다면 {_SIBLING} §6 ②도 같이 고쳐라 — "
        f"그 절의 «(가)는 열렸다»가 이 전제 위에 서 있다."
    )


def test_the_section_says_the_suggestion_row_is_thrown_away(section: str) -> None:
    """② 저장 정책(확정하면 `label_snapshot` · 제안 행은 버린다)이 살아 있다.

    ⚠ 「버린다」와 「폐기」 둘 중 하나면 되고 어미도 자유다 — 산문을 다듬을 여지를 남긴다.
    🔴 다만 **주어에 붙여서** 센다. 「폐기」 한 낱말만 세면 안 된다 — **이 절에는 그 낱말이
    이미 다른 사실로 들어 있다**(인용 실존 게이트의 *"실존 검증 실패 시 제안 자체 폐기"*).
    ⚠ 🔴 **실측(8/21 고의 파괴 ②)**: «제안 행은 버린다» → «보관한다» 로 뒤집었는데 **green**
    이었다 — 낱말 검사가 **엉뚱한 문장에 만족**했다. 저장 정책이 정반대로 바뀌었는데
    검사가 못 봤다. ⇒ 주어(`제안 행`)와 서술어를 **한 자리에서** 본다.
    🔴 그리고 **확정처(`label_snapshot`)와 짝**이어야 한다: 버리는 곳만 적히고 남는 곳이
    없으면 «제안이 어디로 확정되는가»가 문면에서 사라진 것이다.
    """
    thrown_away = re.search(r"제안\s*행[^\n]{0,24}?(버린|폐기)", section)
    assert thrown_away is not None, (
        f"04 §{_SECTION} 의 저장 정책(**제안 행은 버린다**)이 사라졌거나 뒤집혔다. "
        f"🔴 제안을 **영속하기로 바뀌었다면** {_SIBLING} §6 ②의 «저장하지 않는다»가 함께 틀린다 — "
        f"그때는 동의 문구 개정이 (가)에도 선행 조건이 된다."
    )
    assert "label_snapshot" in section, (
        f"04 §{_SECTION} 에서 확정처 `label_snapshot` 이 사라졌다 — "
        f"버리는 곳만 적히고 남는 곳이 없으면 정책이 반쪽이다"
    )


def test_the_consent_field_is_reserved_but_unused(section: str) -> None:
    """③ `guardian_consent` 는 **자리만** 예약돼 있다 — v1 미사용 표식이 붙어 있다.

    🔴 표식이 빠지면 다음 사람이 **처리 로직을 만든다.** CLAUDE.md §3 의 `item_format`
    선례와 같은 형태다(«enum엔 예약값만 두고 처리 로직 만들지 말 것»).
    """
    assert "guardian_consent" in section, (
        f"04 §{_SECTION} 의 `guardian_consent` 예약 자리가 사라졌다"
    )
    assert "v1 미사용" in section, (
        f"04 §{_SECTION} 의 `guardian_consent` 에서 「v1 미사용」 표식이 사라졌다 — "
        f"🔴 표식 없이 두면 구현 회차에서 **처리 로직이 생긴다.** "
        f"실제로 쓰기로 했다면 그것은 계약 변경이고 백엔드 합의가 선행이다"
    )


def test_the_label_suggest_trigger_is_not_called_a_batch_anywhere_in_04() -> None:
    """④ 04 **전문**에서 라벨 제안을 「주간 배치」라 부르는 줄이 없다.

    🔴 **왜 절 검사로 모자란가** — ①~③ 은 §3.7 절만 본다. 그래서 8/21 에 **같은 문서가 두
    말을 했다**: §3.7 은 「강사 요청」, §2 카운트 표와 §3 엔드포인트 표는 「주간 배치」.
    **표 행은 어느 절 검사에도 안 걸린다.**

    ⚠ 🔴 **앵커를 「주간 배치」 단독으로 넓히지 않는다** — 이 저장소에는 **다른 축의 배치**
    문면이 있다(`part_a/03_usecases.md` 의 detect R3 비율 계산 · `06_refine_policy.md` 의
    선호 추출). 넓히면 **남의 사실을 red 로 만든다**(#02 가 반복해서 남긴 것).
    ⇒ **같은 줄에** 「라벨」 축 표식과 「주간 배치」가 **함께** 있을 때만 red 다.
    """
    body = _DOC.read_text(encoding="utf-8")
    stale = [
        f"{n}: {line.strip()}"
        for n, line in enumerate(body.splitlines(), start=1)
        if "주간 배치" in line and ("labels/suggest" in line or "라벨 제안" in line)
    ]
    assert not stale, (
        "라벨 제안의 트리거는 **「강사 요청」**이다(8/21 · 04 §3.7). 표 행이 낡았다:\n  "
        + "\n  ".join(stale)
        + f"\n🔴 정말 배치로 되돌린 것이면 §{_SECTION} 과 {_SIBLING} §6 도 같이 고쳐라 — "
        "지금은 같은 문서가 두 말을 하고 있다"
    )
