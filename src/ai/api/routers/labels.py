"""라벨 제안 라우터 — `POST /v1/labels/suggest` (04 §3.7 · 99 #190).

소유: 박진희.

🔴 **동기 200 · 학부모 한 명 · 아무것도 저장하지 않는다.** 종전 계약은 `guardians[]` 배열 +
202 였고 그건 **「주간 배치」 시절의 형태**다 — 트리거를 「강사 요청」으로 바꾸면서(8/21)
형태를 같이 안 봤다(2026-08-22 정정).

⚠ 🔴 **`Idempotency-Key` 를 받지 않는다.** 이 경로는 **아무 상태도 안 만든다**(잡·행·캐시
전부 없음) — 같은 요청을 두 번 보내면 두 번 계산해 두 번 돌려줄 뿐이고 **재생할 결과가
없다.** 멱등 키를 요구하면 **BE 가 관리할 상태만 늘고 얻는 게 없다**(`/v1/confirmations`·
`/v1/classify` 와 같은 판단).

⚠ 저장은 **확정 경로**(`/confirmations`)의 일이다 — `(축, 제안값, 확정값)` 집계도 거기서
남는다(04 §3.7 · `part_a/12_label_discovery.md` §6 착수조건 ③). ☐ 미구현.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Final

from fastapi import APIRouter, Request
from pydantic import ValidationError

from ai.api.envelope import success_envelope
from ai.api.version_scope import RouterScope
from ai.composition.labels.grounding import ground_suggestions
from ai.composition.labels.provider import (
    LabelSuggestProvider,
    build_label_suggest_provider,
)
from ai.composition.labels.versions import labels_versions
from ai.contracts.execution import Capability, ExecutionContext
from ai.contracts.labels import LabelSuggestRequest, LabelSuggestResponse
from ai.runtime.errors import SnapshotInvalid

logger = logging.getLogger(__name__)

router = APIRouter()

_REQUIRED_HEADERS: Final = ("X-Tenant-Id", "X-Request-Id")

#: 주입 seam — `set_counsel_provider` 선례. 테스트·합성 루트가 갈아 끼운다.
_provider: LabelSuggestProvider | None = None


def label_suggest_provider() -> LabelSuggestProvider:
    return _provider if _provider is not None else build_label_suggest_provider()


def set_label_suggest_provider(provider: LabelSuggestProvider) -> None:
    global _provider
    _provider = provider


def reset_label_suggest_provider() -> None:
    global _provider
    _provider = None


def _format_validation_error(exc: ValidationError) -> list[dict[str, str]]:
    """필드 경로만 — 🔴 **값은 싣지 않는다**(04 §2.3 · 이력 본문이 실릴 자리다)."""
    return [
        {"field": ".".join(str(part) for part in error["loc"]), "type": error["type"]}
        for error in exc.errors()
    ]


@router.post("/v1/labels/suggest")
async def post_labels_suggest(request: Request) -> dict[str, Any]:
    """라벨 제안 — 동기 200.

    🔴 **인용 실존 게이트 통과분만 돌려준다** — 인용이 이력에 없으면 그 제안은 버린다.
    ⚠ **전량 드롭이면 `suggestions: []` 이고 그것도 200 이다**(불변식 4 — 게이트 거부는
    에러가 아니다). 🔴 다만 **생성기가 없어서 비는 것과 구분된다** — 그쪽은 503 이다
    (`provider.py` 참조).
    """
    missing = [name for name in _REQUIRED_HEADERS if not request.headers.get(name)]
    if missing:
        raise SnapshotInvalid("필수 헤더 누락", {"missing_headers": missing})
    tenant_id = request.headers["X-Tenant-Id"]

    try:
        raw_body = await request.json()
    except ValueError as exc:
        raise SnapshotInvalid("요청 바디가 유효한 JSON이 아님", str(exc)) from exc
    try:
        payload = LabelSuggestRequest.model_validate(raw_body)
    except ValidationError as exc:
        raise SnapshotInvalid(
            "요청 바디 스키마 위반", _format_validation_error(exc)
        ) from exc

    #: 🔴 **`execution_id` 는 이 동기 실행의 키다** — 응답마다 새로 만드는 값이 아니라
    #: **이 실행**을 가리킨다(`/v1/classify` 선례). ⚠ 조회 경로가 없으므로 같은 요청을 두 번
    #: 보내면 **두 실행**이고 값이 다른 것이 맞다 — 잡 기반과 다른 점이다.
    #: ⚠ 재현 키는 요청 참조다 — 🔴 **이력 본문을 해시에도 안 싣는다**(불변식 3).
    execution_id = uuid.uuid4()
    context = ExecutionContext(
        execution_id=execution_id,
        tenant_id=tenant_id,
        capability=Capability.COMPOSITION,
        input_snapshot_hash=f"guardian:{payload.guardian_ref}",
        versions=labels_versions(),
    )
    raw = await label_suggest_provider().suggest(
        guardian_ref=payload.guardian_ref,
        history=payload.history,
        context=context,
    )
    outcome = ground_suggestions(raw, history=payload.history)
    #: ⚠ 🔴 **본문·인용문을 로그에 싣지 않는다**(불변식 3 · 99 #80) — 수와 사유까지다.
    logger.info(
        "라벨 제안 tenant=%s guardian=%s 제안=%d 드롭=%d",
        tenant_id,
        payload.guardian_ref,
        len(outcome.suggestions),
        len(outcome.drops),
    )
    return success_envelope(
        data=LabelSuggestResponse(suggestions=outcome.suggestions).model_dump(
            mode="json"
        ),
        execution_id=str(context.execution_id),
        versions=context.versions,
    )


#: 이 라우터의 경로 접두와 버전 세트 — `api/app.py` 가 **실패 응답**에 쓴다(99 ㊓).
#: ⚠ 🔴 **(8/22 정정) 종전에는 `counsel_versions` 를 빌려 썼다** — 그러면 라벨 응답의
#: `meta.versions.prompt` 가 **counsel 프롬프트 버전**을 말한다. BE 가 그 값을 보고 있고
#: **거짓말이다**(불변식 8). ⇒ `labels_versions()` 를 세웠다.
#: 🔴 **`contracts/execution.py`(양자)는 안 건드렸다** — `prompt_version` 이 하나뿐이라는
#: 제약은 **counsel 이 프롬프트를 둘 쓰기 때문**이고 라벨은 하나다(`labels/versions.py`).
VERSION_SCOPE: Final = RouterScope("/v1/labels", labels_versions)

__all__ = [
    "VERSION_SCOPE",
    "post_labels_suggest",
    "reset_label_suggest_provider",
    "router",
    "set_label_suggest_provider",
]
