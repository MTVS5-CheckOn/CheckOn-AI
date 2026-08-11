"""Import 축 버전 세트의 **정본** — 응답과 실행 원장이 같은 함수를 읽는다 (99 ㉾).

🔴 **capability가 만들고 api가 참조한다.** 종전에는 상수 여섯과 `import_versions()`가
`api/routers/imports.py`에 있었다 — 조사 워커가 원장을 쓰려면 **워커가 API 라우터를
역참조**해야 하고, 그러면 계층이 뒤집힌다. 같은 판단의 선례가 둘 있다:
`composition/counsel/versions.py`와 `problem_generation/assembly.problem_versions()`
(99 #20 — *"값만 맞추지 않고 자리를 없앴다. 값만 맞추면 다음에 또 갈린다"*).

⚠ **값과 nullable 조합은 안 바꿨다** — 옮기기만 했다. 응답 `meta.versions`와
`AI_RUN.versions`가 **같은 함수**를 읽는 것이 이 파일의 전부다.
"""

from __future__ import annotations

from typing import Final

from ai.contracts.execution import VersionSet

_PIPELINE_VERSION: Final = "0.1.0"
_ENGINE_VERSION: Final = "import-mapping-0.1"
_SCHEMA_VERSION: Final = "0.1"
_CONTRACT_VERSION: Final = "0.1"
_PROMPT_VERSION: Final = "mapping-0.1"
_TAXONOMY_VERSION: Final = "taxonomy-0.1"


def import_versions() -> VersionSet:
    """Import 엔드포인트·조사 워커 공용 버전 세트 — 미해당 키는 None(§2.2).

    ⚠ **`graph_version`이 None인 것은 그대로 뒀다.** 조사 축에는 LangGraph 그래프가
    있으므로 문서·구현이 갈릴 여지가 있지만, **이 회차는 자리를 옮기는 것**이고 값 판정을
    섞으면 무엇이 바뀌었는지 못 가린다 — 별도로 등재한다.
    """
    return VersionSet(
        pipeline_version=_PIPELINE_VERSION,
        engine_version=_ENGINE_VERSION,
        schema_version=_SCHEMA_VERSION,
        contract_version=_CONTRACT_VERSION,
        prompt_version=_PROMPT_VERSION,
        taxonomy_version=_TAXONOMY_VERSION,
    )
