"""약점 진단 입력과 산출 계약.

사양 원본: docs/part_b/04_curriculum_graph.md §3·§5.5
소유: member-B(염준영) 단독 (docs/02_ownership.md §3)

진단은 결정론 경로이며 LLM을 사용하지 않는다. 이 모듈은 입력 스냅숏과
WeaknessMap의 구조만 정의하고, 판정 임계값·전파 계산은 diagnosis capability가
버전 관리되는 설정을 주입받아 수행한다.
"""

from datetime import date, datetime
from enum import StrEnum
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ai.contracts.taxonomy import AreaTag, ItemFormat, TypeTag


class Period(BaseModel):
    """진단 집계 기간 — 양 끝 날짜를 포함한다."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    from_date: date
    to_date: date

    @model_validator(mode="after")
    def validate_order(self) -> Self:
        if self.from_date > self.to_date:
            raise ValueError("from_date는 to_date보다 늦을 수 없다")
        return self


class DiagnosisEvent(BaseModel):
    """진단 입력 learning_event 1건 — 실명 필드는 존재하지 않는다."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    event_id: str = Field(min_length=1)
    area_tag: AreaTag
    type_tag: TypeTag
    item_format: ItemFormat | None = None
    """분리 리포팅용이며 약점 판정 축에는 사용하지 않는다."""

    chosen_no: int | None = Field(default=None, ge=1, le=5)
    """학생이 고른 1-based 선지 번호. 비객관식이거나 미상이면 null이다.

    **v1 약점 판정 축에는 쓰지 않는다.** 오개념 분리 리포팅을 위한 수신 필드이며,
    정오 판정은 기존 `correct` 값을 그대로 사용한다.
    """

    correct_no: int | None = Field(default=None, ge=1, le=5)
    """BE가 보존한 1-based 정답 번호. 선택·정오 값의 정합 검증에만 사용한다.

    **v1 약점 판정 축에는 쓰지 않는다.** 비객관식이거나 정답 번호를 알 수 없으면
    null이다.
    """

    misconception_tag: str | None = Field(
        default=None,
        min_length=1,
        pattern=r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$",
    )
    """선택한 오답 선지의 P3 검증 완료 라벨. BE가 문항 스냅숏에서 복사한다."""

    passage_ref: str | None = Field(default=None, min_length=1)
    """지문/자료 묶음 참조 — 같은 지문·도표·〈보기〉를 공유하는 문항이 같은 값을 갖고
    재출제 시에도 유지된다(`05_request_json.md` [A 확정 통보 2026-08-03 · 승우 합의]).

    **v1 약점 판정 축에는 쓰지 않는다.** 셀 판정은 원시 정답률 기준이며, 기대치 잔차로의
    이관은 `09` §3 W13이다. 같은 `learning_events`를 감지·진단이 함께 읽으므로
    `extra="forbid"` 하에서 수신 자체가 깨지지 않도록 필드만 먼저 받는다.

    **역참조하지 않는다** — `record_id`와 달리 불투명 키이며 조합 통계의 그룹 키다.
    """

    skill_node_id: str | None = Field(default=None, min_length=1)
    correct: bool
    occurred_at: datetime
    tag_confirmed: bool
    """False인 AI 제안 태그는 수신할 수 있지만 집계에는 반영하지 않는다."""

    @model_validator(mode="after")
    def validate_misconception_selection(self) -> Self:
        if self.misconception_tag is not None and self.chosen_no is None:
            raise ValueError("misconception_tag에는 chosen_no가 필요하다")
        if self.correct and self.misconception_tag is not None:
            raise ValueError("정답 이벤트에는 misconception_tag를 기록할 수 없다")
        if (
            self.correct_no is not None
            and self.chosen_no is not None
            and (self.chosen_no == self.correct_no) != self.correct
        ):
            raise ValueError("chosen_no·correct_no·correct 값이 서로 모순된다")
        return self


class DiagnosisInput(BaseModel):
    """백엔드가 전달하는 학생 1명의 진단 스냅숏."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    tenant_id: str = Field(min_length=1)
    student_ref: str = Field(min_length=1)
    """alias만 허용한다. 실명·연락처 필드는 계약에 없다."""

    period: Period
    as_of: datetime
    snapshot_hash: str = Field(min_length=1)
    events: tuple[DiagnosisEvent, ...] = ()


class CellVerdict(StrEnum):
    """area×type 셀 판정."""

    UNKNOWN = "unknown"
    WEAK = "weak"
    OK = "ok"


class NodeVerdict(StrEnum):
    """직접·간접 증거를 병합한 커리큘럼 노드 판정."""

    SUSPECT = "suspect"
    WEAK_CONFIRMED = "weak_confirmed"
    OK = "ok"


class PropagatedVerdict(StrEnum):
    """선수 관계 역전파로 얻은 근본 결손 후보 판정."""

    ROOT_CANDIDATE = "root_candidate"


class WeaknessCell(BaseModel):
    """셀 하나의 집계값과 판정."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    acc: float = Field(ge=0.0, le=1.0)
    n: int = Field(ge=0)
    verdict: CellVerdict
    severity: float | None = Field(default=None, gt=0.0, le=1.0)

    @model_validator(mode="after")
    def validate_severity(self) -> Self:
        if self.verdict is CellVerdict.WEAK and self.severity is None:
            raise ValueError("weak 셀은 severity가 필요하다")
        if self.verdict is not CellVerdict.WEAK and self.severity is not None:
            raise ValueError("severity는 weak 셀에만 기록한다")
        return self


class WeaknessNode(BaseModel):
    """커리큘럼 노드 판정과 그 근거 참조."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    verdict: NodeVerdict
    basis: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_basis(self) -> Self:
        if any(not ref for ref in self.basis):
            raise ValueError("basis의 근거 참조는 비어 있을 수 없다")
        return self


class PropagatedNode(BaseModel):
    """역전파로 계산한 root_candidate와 출발 노드."""

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    score: float = Field(ge=0.0)
    verdict: PropagatedVerdict = PropagatedVerdict.ROOT_CANDIDATE
    from_nodes: tuple[str, ...] = Field(alias="from", min_length=1)

    @model_validator(mode="after")
    def validate_from_nodes(self) -> Self:
        if any(not node_id for node_id in self.from_nodes):
            raise ValueError("from의 노드 ID는 비어 있을 수 없다")
        return self


class WeaknessMap(BaseModel):
    """진단 산출물 — WEAKNESS_MAP의 구조화 계약."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    graph_version: str = Field(min_length=1)
    taxonomy_version: str = Field(min_length=1)
    config_version: str = Field(min_length=1)
    snapshot_hash: str = Field(min_length=1)
    cells: dict[str, WeaknessCell] = Field(min_length=1)
    nodes: dict[str, WeaknessNode] = Field(default_factory=dict)
    propagated: dict[str, PropagatedNode] = Field(default_factory=dict)
    overall_low: bool = False

    @model_validator(mode="after")
    def validate_mapping_keys(self) -> Self:
        if any(not key for mapping in (self.cells, self.nodes, self.propagated) for key in mapping):
            raise ValueError("WeaknessMap의 셀·노드 키는 비어 있을 수 없다")
        return self


class MisconceptionReport(BaseModel):
    """선택 오답의 오개념 빈도 — v1 약점 판정과 분리된 리포팅 전용 산출."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    by_area: dict[str, dict[str, Annotated[int, Field(ge=1)]]] = Field(
        default_factory=dict
    )
    by_node: dict[str, dict[str, Annotated[int, Field(ge=1)]]] = Field(
        default_factory=dict
    )
    excluded_missing_chosen_no: int = Field(default=0, ge=0)
    excluded_missing_misconception_tag: int = Field(default=0, ge=0)


class DiagnosisStatus(StrEnum):
    """진단 응답 상태."""

    GENERATED = "generated"
    REJECTED_INSUFFICIENT = "rejected_insufficient"


class DiagnosisResult(BaseModel):
    """정상 진단 또는 데이터 부족을 오류 없이 표현한다."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    status: DiagnosisStatus
    weakness_map: WeaknessMap | None = None
    misconceptions: MisconceptionReport = Field(default_factory=MisconceptionReport)
    status_reason: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def validate_status_payload(self) -> Self:
        if self.status is DiagnosisStatus.GENERATED:
            if self.weakness_map is None:
                raise ValueError("generated 상태에는 weakness_map이 필요하다")
            if self.status_reason is not None:
                raise ValueError("generated 상태에는 status_reason을 기록하지 않는다")
            return self

        if self.weakness_map is not None:
            raise ValueError("rejected_insufficient 상태에는 weakness_map을 반환하지 않는다")
        if self.status_reason is None:
            raise ValueError("rejected_insufficient 상태에는 status_reason이 필요하다")
        return self
