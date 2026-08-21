"""진단 라우터 — POST /v1/diagnosis (동기, 결정론 · LLM 없음).

소유: 염준영 (diagnosis 라우터 — 02_ownership §5 "라우터는 capability 오너를 따름").

**왜 신설했나(2026-08-12):** 강사 화면 Step 1의 `area×type` 그리드를 만드는 계산은
`diagnosis/diagnoser.py`에 이미 있었지만 **HTTP로 나가는 자리가 없었다.** 백엔드가 그
그리드를 그릴 방법이 없어 화면이 막혀 있었다(part_b/09 §2-25).

🔴 **그리드는 셀 축이다.** `CellVerdict`(ok·weak·unknown) 3종이 판정 정본이고,
노드 축(`NodeVerdict`)은 그리드가 **아니라** `weakness_map.nodes`의 목록이다. 두 축을 한
표에 섞으면 "이 칸이 왜 빨간가"에 답이 둘이 된다(part_b/04 §4·§5).

⚠ 이 엔드포인트는 **판정만** 한다 — 표시 문구·색·정렬은 백엔드·프론트 소유다.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any, Final

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ai.api.envelope import success_envelope
from ai.api.version_scope import RouterScope
from ai.contracts.diagnosis import (
    CellVerdict,
    DiagnosisEvent,
    DiagnosisInput,
    DiagnosisResult,
    Period,
    WeaknessCell,
)
from ai.contracts.execution import Capability, ExecutionContext, VersionSet
from ai.contracts.taxonomy import V1_TYPE_TAGS, AreaTag, TypeTag
from ai.db.repositories.idempotency import IdempotencyStore
from ai.db.repositories.run_store import RunStore
from ai.db.store_factory import build_idempotency_store, build_run_store
from ai.diagnosis.config import default_diagnosis_runtime
from ai.diagnosis.diagnoser import DiagnosisError, diagnose
from ai.problem_generation.infrastructure.config import load_misconception_tags
from ai.runtime.errors import IdempotencyConflict, SnapshotInvalid

logger = logging.getLogger(__name__)

router = APIRouter()

_ENDPOINT: Final = "/v1/diagnosis"
_REQUIRED_HEADERS: Final = ("X-Tenant-Id", "X-Request-Id", "Idempotency-Key")
_PIPELINE_VERSION: Final = "0.1.0"
_ENGINE_VERSION: Final = "diagnosis-0.1"
_SCHEMA_VERSION: Final = "0.1"
_CONTRACT_VERSION: Final = "0.1"

#: 🔴 **표시 순서의 정본은 enum 선언 순서다**(`contracts/taxonomy.py` — "이 순서가 표시
#: 순서다"). 프론트가 5×4를 자기 상수로 그리면 어휘가 두 곳에서 관리된다.
GRID_AREAS: Final = tuple(AreaTag)
#: ⚠ 예약 태그(`apply`)는 축에서 뺀다 — 산출하지 않는 축을 표에 그리면 영원히 빈 칸이다.
GRID_TYPES: Final = tuple(tag for tag in TypeTag if tag in V1_TYPE_TAGS)


def diagnosis_versions() -> VersionSet:
    """진단 응답·AI_RUN이 공유하는 버전 세트.

    🔴 **그래프 버전이 실린다** — 같은 입력도 그래프가 바뀌면 노드 판정이 달라진다.
    ⚠ 진단 설정 버전(`b-defaults-v1`)은 `VersionSet`에 자리가 없어(양자 승인 파일)
    **산출물 안**의 `weakness_map.config_version`이 든다 — 계약에 이미 있는 필드다.
    """

    graph, _document = default_diagnosis_runtime()
    return VersionSet(
        pipeline_version=_PIPELINE_VERSION,
        engine_version=_ENGINE_VERSION,
        schema_version=_SCHEMA_VERSION,
        contract_version=_CONTRACT_VERSION,
        graph_version=graph.meta.graph_version,
        taxonomy_version=graph.meta.taxonomy_version,
    )


#: 기동 때 한 번 조립한다 — YAML이 깨졌으면 첫 요청이 아니라 **기동에서** 죽는 게 맞다
#: (`problem.py`의 `_PROBLEM_FAILURE_VERSIONS`와 같은 판단).
_DIAGNOSIS_VERSIONS: Final = diagnosis_versions()
_MISCONCEPTION_VOCABULARY: Final = {
    area: frozenset(tag.id for tag in tags)
    for area, tags in load_misconception_tags().areas.items()
}

VERSION_SCOPE: Final = RouterScope(_ENDPOINT, lambda: _DIAGNOSIS_VERSIONS)

_idempotency_store: IdempotencyStore = build_idempotency_store()
_run_store: RunStore = build_run_store()


def set_diagnosis_idempotency_store(store: IdempotencyStore) -> None:
    """멱등 재반환 테스트·PG 어댑터용 주입 seam."""

    global _idempotency_store
    _idempotency_store = store


def set_diagnosis_run_store(store: RunStore) -> None:
    """실행 원장 적재 관측용 주입 seam."""

    global _run_store
    _run_store = store


def reset_diagnosis_router() -> None:
    """진단 테스트가 명시 호출해 라우터 소유 상태를 격리한다."""

    global _idempotency_store, _run_store
    _idempotency_store = build_idempotency_store()
    _run_store = build_run_store()


class DiagnosisRequestBody(BaseModel):
    """POST /v1/diagnosis 바디 — `tenant_id`는 헤더로 온다(바디에 두지 않는다)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    student_ref: str = Field(min_length=1)
    """alias만 허용한다 — 실명·연락처 필드는 계약에 없다(불변식 3)."""

    period: Period
    as_of: datetime
    snapshot_hash: str = Field(min_length=1)
    events: tuple[DiagnosisEvent, ...] = ()


class DiagnosisGridCell(BaseModel):
    """Step 1 그리드 한 칸 — 계약(`WeaknessCell`)의 **표시용 투영**이다.

    🔴 `verdict=None`은 *"판정 실패"* 가 아니라 **그 좌표에 확정 태그 제출이 0건**이라는
    뜻이다(`no_data`). `unknown`(1건 이상이지만 `cell_min_items` 미달)과 **다른 사실**이라
    한 값으로 뭉치지 않는다 — 강사에게 *"자료가 아예 없다"* 와 *"아직 모자란다"* 는
    다음 행동이 다르다.
    🔴 `acc`도 같은 이유로 nullable이다 — 표본 0에 `0.0`을 채우면 **정답률 0%로 읽힌다.**
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    area_tag: AreaTag
    type_tag: TypeTag
    key: str = Field(min_length=1)
    acc: float | None = None
    n: int = Field(ge=0)
    verdict: CellVerdict | None = None
    severity: float | None = None


class DiagnosisGrid(BaseModel):
    """축 목록을 함께 실은 그리드 — 프론트가 어휘를 복제하지 않게 한다."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    areas: tuple[AreaTag, ...]
    types: tuple[TypeTag, ...]
    cell_min_items: int = Field(ge=1)
    """`unknown` 경계 — 화면의 「최소 N문항」 문구가 이 값을 그대로 쓴다."""

    cells: tuple[DiagnosisGridCell, ...]


def build_grid(
    cells: dict[str, WeaknessCell] | None, *, cell_min_items: int
) -> DiagnosisGrid:
    """관측된 셀을 5×4 전체 좌표로 펼친다 — 순수 함수, I/O 없음.

    ⚠ `diagnose()`는 **관측된 좌표만** 돌려준다(제출이 없으면 키 자체가 없다). 화면은
    빈 칸도 그려야 하므로 축의 곱집합으로 펼치되, 없는 칸은 `no_data`로 표시한다.
    """

    observed = cells or {}
    grid_cells: list[DiagnosisGridCell] = []
    for area_tag in GRID_AREAS:
        for type_tag in GRID_TYPES:
            key = f"{area_tag.value}×{type_tag.value}"
            cell = observed.get(key)
            grid_cells.append(
                DiagnosisGridCell(
                    area_tag=area_tag,
                    type_tag=type_tag,
                    key=key,
                    acc=None if cell is None else cell.acc,
                    n=0 if cell is None else cell.n,
                    verdict=None if cell is None else cell.verdict,
                    severity=None if cell is None else cell.severity,
                )
            )
    return DiagnosisGrid(
        areas=GRID_AREAS,
        types=GRID_TYPES,
        cell_min_items=cell_min_items,
        cells=tuple(grid_cells),
    )


def _format_validation_error(exc: ValidationError) -> list[dict[str, str]]:
    return [
        {"field": ".".join(str(part) for part in error["loc"]), "type": error["type"]}
        for error in exc.errors()
    ]


def _response_data(result: DiagnosisResult, *, cell_min_items: int) -> dict[str, Any]:
    weakness_map = result.weakness_map
    return {
        "status": result.status.value,
        "status_reason": result.status_reason,
        "weakness_map": (
            None if weakness_map is None else weakness_map.model_dump(mode="json")
        ),
        "misconceptions": result.misconceptions.model_dump(mode="json"),
        "grid": build_grid(
            None if weakness_map is None else weakness_map.cells,
            cell_min_items=cell_min_items,
        ).model_dump(mode="json"),
    }


@router.post(_ENDPOINT)
async def post_diagnosis(request: Request) -> dict[str, Any]:
    """학생 1명의 약점 지도를 계산한다 — 헤더·바디 검증 → 멱등 → 결정론 판정 → 원장."""

    missing = [name for name in _REQUIRED_HEADERS if not request.headers.get(name)]
    if missing:
        raise SnapshotInvalid("필수 헤더 누락", {"missing_headers": missing})
    tenant_id = request.headers["X-Tenant-Id"]
    idempotency_key = request.headers["Idempotency-Key"]

    try:
        raw_body = await request.json()
    except ValueError as exc:
        raise SnapshotInvalid("요청 바디가 유효한 JSON이 아님", str(exc)) from exc
    if not isinstance(raw_body, dict):
        raise SnapshotInvalid("요청 바디는 JSON 객체여야 한다")
    if "tenant_id" in raw_body:
        raise SnapshotInvalid(
            "헤더 파생 필드는 요청 바디에 둘 수 없다", {"fields": ["tenant_id"]}
        )

    try:
        body = DiagnosisRequestBody.model_validate(raw_body)
    except ValidationError as exc:
        raise SnapshotInvalid(
            "요청 바디 스키마 위반", _format_validation_error(exc)
        ) from exc

    hit = await _idempotency_store.get(
        tenant_id=tenant_id, endpoint=_ENDPOINT, idempotency_key=idempotency_key
    )
    if hit is not None:
        if hit.snapshot_hash == body.snapshot_hash:
            return hit.response_body
        raise IdempotencyConflict(
            "같은 Idempotency-Key에 다른 바디", {"idempotency_key": idempotency_key}
        )

    graph, document = default_diagnosis_runtime()
    diagnosis_input = DiagnosisInput(
        tenant_id=tenant_id,
        student_ref=body.student_ref,
        period=body.period,
        as_of=body.as_of,
        snapshot_hash=body.snapshot_hash,
        events=body.events,
    )
    try:
        result = diagnose(
            diagnosis_input,
            graph,
            document.params,
            graph_version=graph.meta.graph_version,
            taxonomy_version=graph.meta.taxonomy_version,
            config_version=document.version,
            misconception_vocabulary=_MISCONCEPTION_VOCABULARY,
        )
    except DiagnosisError as exc:
        # 🔴 400이다 — 없는 노드 참조·같은 event_id 내용 충돌은 **호출자가 고칠 요청**이다
        #    (`enqueue.py`의 두 문 앞 400과 같은 판단). 500으로 올리면 BE가 재시도한다.
        raise SnapshotInvalid("진단 입력 검증 실패", str(exc)) from exc

    execution_id = uuid.uuid4()
    envelope = success_envelope(
        data=_response_data(result, cell_min_items=document.params.cell_min_items),
        execution_id=str(execution_id),
        versions=_DIAGNOSIS_VERSIONS,
    )

    context = ExecutionContext(
        execution_id=execution_id,
        tenant_id=tenant_id,
        capability=Capability.DIAGNOSIS,
        input_snapshot_hash=body.snapshot_hash,
        versions=_DIAGNOSIS_VERSIONS,
    )
    # LLM 호출이 0건인 실행이라 model_* 은 비운다 — **사용 축**이다(04 §2.2).
    await _run_store.record_run(
        context.to_run_metadata(datetime.now(UTC)), calls=()
    )
    await _idempotency_store.put(
        tenant_id=tenant_id,
        endpoint=_ENDPOINT,
        idempotency_key=idempotency_key,
        snapshot_hash=body.snapshot_hash,
        response_body=envelope,
    )
    return envelope


__all__ = [
    "GRID_AREAS",
    "GRID_TYPES",
    "VERSION_SCOPE",
    "DiagnosisGrid",
    "DiagnosisGridCell",
    "DiagnosisRequestBody",
    "build_grid",
    "diagnosis_versions",
    "post_diagnosis",
    "reset_diagnosis_router",
    "router",
    "set_diagnosis_idempotency_store",
    "set_diagnosis_run_store",
]
