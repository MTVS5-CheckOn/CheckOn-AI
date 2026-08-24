"""`/v1/labels/suggest` 의 **OpenAPI 표기 상수** — 문서 전용(2026-08-24 · №74).

🔴 **여기 있는 것은 「이름」뿐이다.** 런타임은 `labels.py` 가 그대로 하고, 이 모듈은
요청·응답을 만들거나 다시 직렬화하지 않는다.

🔴 **왜 상수로 빼는가 — `operationId` 가 BE 클라이언트의 메서드명이 된다.**
`detect_openapi.py` 가 적어 둔 그대로다: 자동 생성 id 를 두면 **핸들러 함수 이름을 바꾸는
순간 BE 의 메서드명이 따라 바뀐다.** 실측(8/24 · `/openapi.json`): 종전 이 경로의 id 는
`post_labels_suggest_v1_labels_suggest_post` 였다 ⇒ 🔴 **통신테스트 전에 고정한다.**

⚠ 🔴 **`detect_openapi.py`·`counsel_openapi.py` 를 복제하지 않았다.** 그 둘은 요청 스키마
인라인·응답 envelope 모델까지 낸다 — 이 모듈은 **상수 셋**이다. 본문(`description`)·예시·
헤더 문서·envelope 모델은 **별건**이고, 셋에 다 하면 회차가 커진다.
"""

from __future__ import annotations

from typing import Final

#: `/docs` 의 도메인 그룹 — 🔴 **한글 도메인어**(선례: `위험신호`·`상담`).
LABELS_TAG: Final = "라벨"

#: 🔴 **BE 클라이언트의 메서드명이 된다** — camelCase 동사구(선례: `detectRiskSignals`·
#: `createCounselDraft`). 🔴 **한 번 내보내면 바꾸지 않는다.**
#: ⚠ 04 는 `operationId` 를 **규정하지 않는다**(실측) ⇒ **저장소가 정본**이고,
#: 04 에 같은 목록을 또 적지 않는다(«같은 목록이 두 곳» — #380 회신의 그 판단).
LABELS_OPERATION_ID: Final = "suggestGuardianLabels"

#: `/docs` 한 줄 요약.
LABELS_SUMMARY: Final = "학부모 라벨을 제안한다"

__all__ = [
    "LABELS_OPERATION_ID",
    "LABELS_SUMMARY",
    "LABELS_TAG",
]
