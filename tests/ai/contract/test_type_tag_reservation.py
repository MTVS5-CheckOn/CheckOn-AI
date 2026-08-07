"""🔴 `TypeTag` 예약(`apply`)의 두 축을 잠근다 (99 ㊣).

**축이 둘이고 서로 다른 것을 본다** — 하나로 뭉치면 어느 하나가 빠진다.

  ① **표시 라벨 완전성** — `_TYPE_KO`·`_AREA_KO`가 enum **전체**를 덮는가
     ⚠ `V1_TYPE_TAGS`가 아니라 `TypeTag` 전체다. **표시 어휘를 갖는 것과 산출을 허용하는
       것은 다른 축**이고, 예약 태그도 **입력으로 들어와 표시까지 흘러간다**
       (`LearningEvent.type_tag` → `features.cells` → `_r6_facts`).
  ② **리터럴 복제 금지** — v1 4종을 집합·튜플로 나열한 곳이 없는가(`V1_TYPE_TAGS`가 정본)

🔴 **①과 ②는 겹치지 않는다.** `_TYPE_KO`는 **dict**라 ②의 AST 가드(`ast.Set`·`ast.Tuple`)에
안 걸린다 — 그래서 ①이 따로 필요하다. 반대로 ②가 잡는 것은 *"4종을 어딘가에 또 적었다"* 이고
①은 *"5종을 다 안 적었다"* 다. **방향이 반대다.**

⚠ **누락의 증상이 조용하다** — `_r6_facts:205`가 `.get(tag, tag.value)`로 읽으므로
`KeyError`가 아니라 **enum 값(영문)이 학부모 문장에 섞인다.** 8/8 실증: `"문학·apply"` 가
`check_brief_gate`를 `passed=True`로 **통과**했다(`_SYMBOL_RE`에 영문이 없다).
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path
from typing import Final

from ai.composition import briefing_context
from ai.composition.briefing_context import _AREA_KO, _TYPE_KO
from ai.contracts import taxonomy
from ai.contracts.taxonomy import (
    RESERVED_TYPE_TAGS,
    V1_TYPE_TAGS,
    AreaTag,
    TypeTag,
)

_SRC: Final = Path(__file__).resolve().parents[3] / "src" / "ai"

#: 🔴 **정본과 이 테스트 자신은 제외한다** — 정본(`contracts/taxonomy.py`)은 enum을 정의하는
#: 자리라 멤버가 모여 있는 것이 당연하고, 이 파일은 **위반 예시를 문자열로 들고** 있어야
#: 검사할 수 있다(#125 가드와 같은 판단).
_EXEMPT: Final = frozenset({"contracts/taxonomy.py"})

#: 리터럴 나열로 볼 최소 멤버 수. 2개는 *"이 둘만 특별히 다루는"* 정당한 경우가 있고
#: (`{FACT, INFER}` 같은 도메인 조건), 3개부터는 **집합을 재정의하는 냄새**다.
_LITERAL_THRESHOLD: Final = 3


# ── ① 표시 라벨 완전성 ─────────────────────────────────────────────


def test_type_labels_cover_every_tag_including_reserved() -> None:
    """🔴 `_TYPE_KO`가 **`TypeTag` 전체**를 덮는다 — 예약 태그도 표시된다.

    ⚠ **`V1_TYPE_TAGS` 기준이 아니다.** 예약은 *"우리가 안 만든다"* 이지
    *"안 들어온다"* 가 아니다 — `LearningEvent.type_tag`로 들어와 R6 브리핑까지 간다.
    """
    missing = sorted(tag.value for tag in TypeTag if tag not in _TYPE_KO)
    assert not missing, (
        f"_TYPE_KO에 라벨이 없는 태그: {missing}\n"
        "🔴 라벨이 없으면 _r6_facts의 .get() 폴백이 **enum 값(영문)** 을 돌려주고 그게 "
        "학부모 문장에 섞인다(예: '문학·apply'). KeyError가 아니라 **조용한 누출**이다.\n"
        "🔴 브리핑 게이트는 영문을 **안 막는다** — _SYMBOL_RE에 영문이 없고 금칙어도 "
        "숫자도 아니다(8/8 실증: passed=True로 통과했다).\n"
        "⚠ 예약 태그도 넣어야 한다 — 표시 어휘를 갖는 것과 산출을 허용하는 것은 다른 축이다."
    )


def test_area_labels_cover_every_tag() -> None:
    """⚠ `_AREA_KO`도 같다 — 같은 `.get()` 폴백을 쓴다(`_r6_facts:204`)."""
    missing = sorted(tag.value for tag in AreaTag if tag not in _AREA_KO)
    assert not missing, (
        f"_AREA_KO에 라벨이 없는 태그: {missing} — _TYPE_KO와 같은 이유로 영문이 샌다"
    )


def test_the_reserved_tag_has_a_label_but_is_not_in_v1() -> None:
    """🔴 **두 축이 서로 다르다는 것 자체**를 못 박는다.

    예약 태그는 **라벨을 갖고**(표시된다) **`V1_TYPE_TAGS`에는 없다**(산출 안 한다).
    이 단정이 red가 되면 둘을 같은 축으로 뭉친 것이다.
    """
    assert RESERVED_TYPE_TAGS, "예약 태그가 없다 — 이 파일의 전제가 사라졌다"
    for tag in RESERVED_TYPE_TAGS:
        assert tag in _TYPE_KO, f"{tag.value}에 표시 라벨이 없다"
        assert tag not in V1_TYPE_TAGS, f"{tag.value}가 v1 집합에 들어갔다"


def test_v1_and_reserved_partition_the_enum() -> None:
    """⚠ 두 집합이 `TypeTag`를 **빠짐없이 겹침없이** 나눈다 — 뺄셈 정의의 성질이다."""
    assert V1_TYPE_TAGS | RESERVED_TYPE_TAGS == frozenset(TypeTag)
    assert not (V1_TYPE_TAGS & RESERVED_TYPE_TAGS)


# ── ② 리터럴 복제 금지 ─────────────────────────────────────────────


def _member_literals(path: Path) -> list[tuple[int, int]]:
    """`TypeTag` 멤버를 `_LITERAL_THRESHOLD`개 이상 나열한 집합·튜플의 `(줄, 개수)`."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: list[tuple[int, int]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Set | ast.Tuple):
            continue
        members = sum(
            1
            for element in node.elts
            if isinstance(element, ast.Attribute)
            and isinstance(element.value, ast.Name)
            and element.value.id == "TypeTag"
        )
        if members >= _LITERAL_THRESHOLD:
            found.append((node.lineno, members))
    return found


def test_the_scan_finds_type_tag_references() -> None:
    """🔴 **검사 경로가 끊기면 통과가 아니라 실패다.**

    `TypeTag`를 참조하는 파일을 하나도 못 찾으면 *"위반이 없다"* 가 아니라 *"안 봤다"* 다.
    """
    referencing = [
        path
        for path in _SRC.rglob("*.py")
        if "TypeTag" in path.read_text(encoding="utf-8")
    ]
    assert referencing, (
        f"{_SRC}에서 TypeTag 참조를 하나도 찾지 못했다 — 위반이 없는 게 아니라 검사가 "
        "끊긴 것이다(이름 개명·경로 변경 확인)"
    )


def test_v1_type_tags_is_the_single_source() -> None:
    """🔴 v1 4종을 **리터럴로 다시 나열한 곳이 없다** — `V1_TYPE_TAGS`가 정본이다.

    나열하면 예약을 여는 날 **한 곳만 고쳐진다.** 이 저장소가 목록형으로 아홉 번 당한
    형태다(#108·#111·#114·#116·#117·#119·#120·#125·로그 67).

    ⚠ **dict는 안 걸린다** — `_TYPE_KO`가 그 형태이고, 그래서 위 라벨 완전성 테스트가
    **따로** 필요하다. 두 장치가 다른 것을 본다.
    """
    offenders: list[str] = []
    for path in sorted(_SRC.rglob("*.py")):
        rel = path.relative_to(_SRC).as_posix()
        if rel in _EXEMPT:
            continue
        for lineno, count in _member_literals(path):
            offenders.append(f"src/ai/{rel}:{lineno} (멤버 {count}개)")
    assert not offenders, (
        "TypeTag 멤버를 집합·튜플로 나열한 곳이 있다:\n  " + "\n  ".join(offenders) + "\n\n"
        "🔴 `V1_TYPE_TAGS`(또는 `TypeTag`)를 참조하라 — 나열하면 예약을 여는 날 한 곳만 "
        "고쳐진다. 정말 그 부분집합이 필요하다면 `contracts/taxonomy.py`에 이름 붙인 상수로 "
        "올려라(정본은 한 곳이어야 한다)."
    )


def test_the_guard_would_catch_a_violation() -> None:
    """⚠ **가드가 실제로 잡는지**를 이 파일 안에서 확인한다 — 뒤집기의 상시화.

    뒤집기가 세 번 헛돌았다(로그 70 · #122·#126·#133). *"red가 났는가"* 이전에
    *"검사가 그 형태를 보긴 하는가"* 를 코드로 남긴다.
    """
    import tempfile

    source = (
        "from ai.contracts.taxonomy import TypeTag\n"
        "X = {TypeTag.FACT, TypeTag.INFER, TypeTag.CRITIC}\n"
    )
    with tempfile.NamedTemporaryFile("w", suffix=".py", encoding="utf-8") as handle:
        handle.write(source)
        handle.flush()
        found = _member_literals(Path(handle.name))
    assert found == [(2, 3)], f"가드가 위반 형태를 못 본다: {found}"


def test_the_exempt_list_names_only_the_canonical_file() -> None:
    """⚠ 제외 목록이 **정본 하나**인지 — 늘어나면 화이트리스트가 된다(#119·#125 판단)."""
    assert _EXEMPT == frozenset({"contracts/taxonomy.py"})
    assert Path(inspect.getfile(taxonomy)).name == "taxonomy.py"
    assert Path(inspect.getfile(briefing_context)).name == "briefing_context.py"
