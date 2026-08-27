"""`enrolled_seconds` 는 **받아서 해시에만 싣는다 — 판정에 쓰지 않는다** (99 #264).

🔴 **PR #463 이 필드를 받게 만들었고, 이 파일은 그 다음 문장을 문다.**
계약(`contracts/detection.py`)이 받고 canonical(`detection/canonical.py`)이 해시에
싣는 것까지가 지금 정해진 전부다. R3(학습 공백) 판정이 이 값을 읽기 시작하면
**엔진 버전이 올라가고 골든셋 기대값이 재산정 대상**이 된다(불변식 8).

⚠ 그 판정은 **아직 안 났다**(99 #264). 재료는 있다 — 전이 주(휴원·복귀)는
`activity_count` 가 낮은 것이 **정상**인데 지금 R3 는 그 주를 온전한 주와 같은 자로
잰다. 🔴 **다만 그 헛울림이 얼마나 주는지는 안 쟀다.**

🔴 **「안 쓴다」를 주석으로만 적으면 다음 사람이 R3 에 꽂고도 아무 데도 안 빨개진다.**
   엔진 버전이 조용히 낡는 자리라 **값으로** 문다.
⚠ #208 이 적은 그 형태다 — 「층이 있는데 안 불린다」가 아니라 여기서는 반대로
  「안 쓰기로 한 것이 조용히 쓰이기 시작한다」이고, 둘 다 **아무도 안 알려주는** 축이다.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

import ai

#: 🔴 판정 코드가 사는 곳. `canonical.py` 만 예외다 — 그건 **해시 표면**이지 판정이 아니다.
_JUDGMENT_PACKAGE: Final = "detection"
_HASH_SURFACE: Final = "canonical.py"

#: 🔴 이 이름을 알아도 되는 자리 — 계약(받는다) · canonical(해시) · 평가 픽스처 둘.
#: ⚠ **늘리려면 고의로 늘려야 한다.** 그게 판정이 났다는 표시다(99 #264).
_ALLOWED: Final = frozenset(
    {
        "contracts/detection.py",
        "detection/canonical.py",
        "evaluation/canonical_vectors.py",
        "evaluation/fake_snapshot.py",
    }
)


def _files_that_know() -> frozenset[str]:
    root = Path(ai.__file__).parent
    return frozenset(
        path.relative_to(root).as_posix()
        for path in root.rglob("*.py")
        if "enrolled_seconds" in path.read_text(encoding="utf-8")
    )


def test_no_judgment_module_reads_enrolled_seconds() -> None:
    """🔴 **`detection/` 안에서 이 이름을 아는 파일은 `canonical.py` 하나뿐이다.**

    ⚠ 이 단언이 red 가 되는 경우는 둘이다 — ⓐ 판정에 배선했다(그러면 99 #264 를 닫고
      엔진 버전을 올려야 한다) ⓑ 해시 표면을 다른 파일로 옮겼다(그러면 이 목록을 고친다).
      **어느 쪽이든 사람이 봐야 하는 변경**이라 조용히 통과시키지 않는다.
    """
    judgment_files = {
        path
        for path in _files_that_know()
        if path.startswith(f"{_JUDGMENT_PACKAGE}/")
    }

    assert judgment_files == {f"{_JUDGMENT_PACKAGE}/{_HASH_SURFACE}"}, (
        f"판정 코드가 enrolled_seconds 를 읽는다: {sorted(judgment_files)}"
    )


def test_the_set_of_files_that_know_this_field_is_pinned() -> None:
    """🔴 **판정 밖으로 새는 것도 본다** — 계약·해시·픽스처 넷이 전부다.

    ⚠ 앞 검사만 두면 `composition/` 이나 `api/` 가 이 값을 쓰기 시작해도 안 걸린다.
      그 자리들은 판정은 아니지만 **「받아서 해시에만 싣는다」를 이미 벗어난 것**이다.
    """
    assert _files_that_know() == _ALLOWED, (
        f"이 필드를 아는 자리가 바뀌었다: {sorted(_files_that_know())}"
    )
