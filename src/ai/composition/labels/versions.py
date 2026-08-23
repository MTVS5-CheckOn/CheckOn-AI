"""라벨 제안 실행의 버전 세트 (99 #191 · 04 §2.2).

🔴 **왜 `counsel_versions()` 를 빌려 쓰지 않나** — #382 가 라우터에 그걸 꽂았는데, 그러면
라벨 응답의 `meta.versions.prompt` 가 **counsel 의 프롬프트 버전**을 말한다. BE 가 그 값을
보고 있고 🔴 **거짓말이다**(불변식 8 — 재현성).

⚠ 🔴 **`contracts/execution.py`(양자)를 안 건드린다.** №64 가 «가르려면 양자 변경» 이라
적었는데 **그건 counsel 의 사정을 라벨에 옮겨 읽은 것**이다: `VersionSet.prompt_version` 이
하나뿐이라는 제약은 **counsel 이 프롬프트를 둘 쓰기 때문**에 아픈 것이고(plan · pack),
🔴 **라벨은 프롬프트가 하나**라 그 제약이 없다.

⚠ `counsel/versions.py` 를 복제하지 않았다 — **값이 다르다**(engine·prompt). 같은 것은
`pipeline`·`schema`·`contract` 뿐이고 그건 저장소 전체의 축이다.
"""

from __future__ import annotations

from typing import Final

from ai.composition.labels.prompt import PROMPT_VERSION
from ai.contracts.execution import VersionSet

_PIPELINE_VERSION: Final = "0.1.0"
_ENGINE_VERSION: Final = "labels-suggest-0.1"
"""🔴 **counsel 과 다른 엔진 이름**이다 — 같은 `composition` capability 안의 **다른 경로**다.
⚠ 이 값이 `AI_RUN.engine_version` 으로도 간다 — 원장에서 두 경로가 갈려야 «라벨이 몇 번
돌았나» 를 셀 수 있다."""

_SCHEMA_VERSION: Final = "0.1"
_CONTRACT_VERSION: Final = "0.1"


def labels_versions() -> VersionSet:
    """라벨 제안의 버전 세트 — **응답과 원장이 이 하나를 참조한다**.

    🔴 **선언 축이다**(04 §2.2) — «이 응답을 낸 엔드포인트가 어느 버전 위에서 도는가» 이지
    «이번 실행이 실제로 무엇을 썼는가» 가 아니다. 실패 응답에도 실리므로 **실행이 0인
    시점에도 만들 수 있어야** 한다(그래서 인자가 없다).
    """
    return VersionSet(
        pipeline_version=_PIPELINE_VERSION,
        engine_version=_ENGINE_VERSION,
        schema_version=_SCHEMA_VERSION,
        contract_version=_CONTRACT_VERSION,
        prompt_version=PROMPT_VERSION,
    )


__all__ = ["labels_versions"]
