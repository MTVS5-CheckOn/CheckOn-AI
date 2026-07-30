"""문제 생성·수정·검증용 GraphRAG 경계 계약."""

from enum import StrEnum
from typing import Annotated, Literal, Protocol, Self, runtime_checkable
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from ai.contracts.problem_generation import TargetSource
from ai.contracts.taxonomy import AreaTag, ItemFormat, TypeTag

type NonEmptyStr = Annotated[str, Field(min_length=1)]
type Sha256Hash = Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]


class GraphContextOperation(StrEnum):
    GENERATE = "generate"
    REFINE = "refine"
    VERIFY = "verify"
    REPLACE = "replace"


class EvidencePackAnchorKind(StrEnum):
    PASSAGE_SPAN = "passage_span"
    DICT_ENTRY = "dict_entry"
    GRAMMAR_RULE = "grammar_rule"
    WORK_SPAN = "work_span"
    SOURCE_CLAIM = "source_claim"


class EvidenceRetrievalMode(StrEnum):
    REUSE_ONLY = "reuse_only"
    LOCAL = "local"
    HYBRID = "hybrid"
    DELTA_RETRIEVE = "delta_retrieve"


class ContextLockedFields(BaseModel):
    """수정 턴에서도 바꿀 수 없는 출제 목표."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    target_ref: NonEmptyStr
    area_tag: AreaTag
    type_tags: tuple[TypeTag, ...] = Field(min_length=1)
    skill_node_id: NonEmptyStr
    item_format: Literal[ItemFormat.MCQ] = ItemFormat.MCQ

    @model_validator(mode="after")
    def validate_type_tags(self) -> Self:
        if len(set(self.type_tags)) != len(self.type_tags):
            raise ValueError("type_tags는 중복될 수 없다")
        return self


class GraphContextRequest(BaseModel):
    """GraphContextService의 결정론 query plan 입력."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    tenant_id: NonEmptyStr
    target_source: TargetSource
    weakness_map_id: UUID | None = None
    target_skill_node_ids: tuple[NonEmptyStr, ...] = Field(min_length=1)
    locked_fields: ContextLockedFields
    current_item_snapshot: dict[str, JsonValue] | None = None
    redacted_instruction: str | None = Field(default=None, min_length=1)
    policy_constraints: dict[str, JsonValue]

    @model_validator(mode="after")
    def validate_targets(self) -> Self:
        if len(set(self.target_skill_node_ids)) != len(self.target_skill_node_ids):
            raise ValueError("target_skill_node_ids는 중복될 수 없다")
        return self


class ContextPack(BaseModel):
    """redaction 이후 고정된 생성·수정·검증 문맥."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    context_pack_id: UUID
    context_pack_schema_version: Literal["ctx-1"] = "ctx-1"
    operation: GraphContextOperation
    tenant_id: NonEmptyStr
    target_source: TargetSource
    weakness_map_id: UUID | None = None
    target_skill_node_ids: tuple[NonEmptyStr, ...] = Field(min_length=1)
    locked_fields: ContextLockedFields
    pedagogy_paths: tuple[NonEmptyStr, ...] = Field(min_length=1)
    evidence_pack_id: UUID
    current_item_snapshot: dict[str, JsonValue] | None = None
    redacted_instruction: str | None = Field(default=None, min_length=1)
    policy_constraints: dict[str, JsonValue]
    retrieval_trace: dict[str, JsonValue]
    context_pack_hash: Sha256Hash

    @model_validator(mode="after")
    def validate_references(self) -> Self:
        if len(set(self.target_skill_node_ids)) != len(self.target_skill_node_ids):
            raise ValueError("target_skill_node_ids는 중복될 수 없다")
        if len(set(self.pedagogy_paths)) != len(self.pedagogy_paths):
            raise ValueError("pedagogy_paths는 중복될 수 없다")
        return self


class EvidencePackAnchor(BaseModel):
    """권리 승인이 끝난 근거 1건과 그래프 경로."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    anchor_id: NonEmptyStr
    kind: EvidencePackAnchorKind
    ref: NonEmptyStr
    source_id: NonEmptyStr
    source_version: NonEmptyStr
    source_content_hash: Sha256Hash
    chunk_id: NonEmptyStr | None = None
    span_start: int | None = Field(default=None, ge=0)
    span_end: int | None = Field(default=None, ge=0)
    quote: str | None = Field(default=None, min_length=1)
    quote_hash: Sha256Hash | None = None
    claim_id: NonEmptyStr | None = None
    graph_path_edge_ids: tuple[NonEmptyStr, ...] = ()
    license_ref: NonEmptyStr
    rights_status: Literal["approved"] = "approved"

    @model_validator(mode="after")
    def validate_coordinates(self) -> Self:
        if (self.span_start is None) != (self.span_end is None):
            raise ValueError("span_start와 span_end는 함께 기록해야 한다")
        if (
            self.span_start is not None
            and self.span_end is not None
            and self.span_end <= self.span_start
        ):
            raise ValueError("span_end는 span_start보다 커야 한다")
        if (self.quote is None) != (self.quote_hash is None):
            raise ValueError("quote와 quote_hash는 함께 기록해야 한다")
        if len(set(self.graph_path_edge_ids)) != len(self.graph_path_edge_ids):
            raise ValueError("graph_path_edge_ids는 중복될 수 없다")
        return self


class EvidenceCoverage(BaseModel):
    """문항 필드와 이를 지지하는 근거 앵커의 대응."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    item_field: NonEmptyStr
    supporting_anchor_ids: tuple[NonEmptyStr, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_anchor_ids(self) -> Self:
        if len(set(self.supporting_anchor_ids)) != len(self.supporting_anchor_ids):
            raise ValueError("supporting_anchor_ids는 중복될 수 없다")
        return self


class EvidenceRetrieval(BaseModel):
    """EvidencePack을 만든 검색의 재현 메타데이터."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    mode: EvidenceRetrievalMode
    query_hash: Sha256Hash
    returned_node_ids: tuple[NonEmptyStr, ...] = ()
    returned_path_ids: tuple[NonEmptyStr, ...] = ()
    scores: tuple[float, ...] = ()

    @model_validator(mode="after")
    def validate_result_ids(self) -> Self:
        if len(set(self.returned_node_ids)) != len(self.returned_node_ids):
            raise ValueError("returned_node_ids는 중복될 수 없다")
        if len(set(self.returned_path_ids)) != len(self.returned_path_ids):
            raise ValueError("returned_path_ids는 중복될 수 없다")
        return self


class EvidencePack(BaseModel):
    """문항 필드별 근거 coverage와 검색 trace를 고정한 묶음."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    evidence_pack_id: UUID
    evidence_pack_schema_version: Literal["evp-1"] = "evp-1"
    graph_snapshot_id: NonEmptyStr
    anchors: tuple[EvidencePackAnchor, ...] = Field(min_length=1)
    coverage: tuple[EvidenceCoverage, ...] = Field(min_length=1)
    missing_requirements: tuple[NonEmptyStr, ...] = ()
    retrieval: EvidenceRetrieval
    evidence_pack_hash: Sha256Hash

    @model_validator(mode="after")
    def validate_coverage(self) -> Self:
        anchor_ids = tuple(anchor.anchor_id for anchor in self.anchors)
        if len(set(anchor_ids)) != len(anchor_ids):
            raise ValueError("anchors의 anchor_id는 중복될 수 없다")

        known_anchor_ids = set(anchor_ids)
        unknown_anchor_ids = {
            anchor_id
            for coverage in self.coverage
            for anchor_id in coverage.supporting_anchor_ids
            if anchor_id not in known_anchor_ids
        }
        if unknown_anchor_ids:
            raise ValueError(
                "coverage가 존재하지 않는 anchor_id를 참조한다: "
                + ", ".join(sorted(unknown_anchor_ids))
            )
        return self


class EvidencePathResult(BaseModel):
    """EvidencePack 그래프 경로 검증의 결정론 결과."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    valid: bool
    checked_anchor_ids: tuple[NonEmptyStr, ...]
    invalid_anchor_ids: tuple[NonEmptyStr, ...] = ()

    @model_validator(mode="after")
    def validate_result(self) -> Self:
        checked = set(self.checked_anchor_ids)
        invalid = set(self.invalid_anchor_ids)
        if not invalid.issubset(checked):
            raise ValueError("invalid_anchor_ids는 checked_anchor_ids의 부분집합이어야 한다")
        if self.valid == bool(invalid):
            raise ValueError("valid와 invalid_anchor_ids가 일치하지 않는다")
        return self


@runtime_checkable
class GraphContextService(Protocol):
    """생성·수정·검증·교체의 GraphRAG 단일 경유지."""

    async def resolve_generation_context(
        self,
        request: GraphContextRequest,
    ) -> ContextPack: ...

    async def resolve_revision_context(
        self,
        request: GraphContextRequest,
    ) -> ContextPack: ...

    async def resolve_verification_context(
        self,
        request: GraphContextRequest,
    ) -> ContextPack: ...

    async def resolve_replacement_context(
        self,
        request: GraphContextRequest,
    ) -> ContextPack: ...

    async def verify_evidence_paths(
        self,
        evidence_pack: EvidencePack,
    ) -> EvidencePathResult: ...
