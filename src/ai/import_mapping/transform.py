"""결정론 변환기 — 확정된 MappingSpec으로 표준 스키마 산출물 생성 (10_import_spec §4).

**후속(명시적 보류):** 변환 규칙·output_url 산출은 승우 Q2(파일 포맷) 답변 대기라
이 브랜치에서 구현하지 않는다. transforming 진입까지는 라우터가 하고, 실제 변환은 아래
run_transform이 담당하되 지금은 NotImplementedError로 경계를 명시한다(워커 미배선).
"""

from __future__ import annotations

from ai.contracts.imports import ImportResult, MappingPreview
from ai.import_mapping.profiling import SourceProfile


def run_transform(profile: SourceProfile, preview: MappingPreview) -> ImportResult:
    """확정 spec으로 원본을 표준 스키마로 결정론 변환 → output_url·행 리포트(§4). 후속."""
    raise NotImplementedError(
        "결정론 변환기·output_url 후속 — 승우 Q2(파일 포맷) 답변 대기(10_import_spec §6.1)"
    )
