"""문항 생성 호출과 인메모리 후보·최종본 저장 경계."""

from __future__ import annotations

import asyncio
import hashlib
import json
from typing import Protocol, runtime_checkable
from uuid import UUID, uuid5

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ai.contracts.execution import ExecutionContext
from ai.contracts.graphrag import ContextPack
from ai.contracts.llm import (
    CallOutcome,
    FieldMissing,
    LLMRequest,
    LLMResult,
    LlmTimeout,
    LlmUnavailable,
    ModelRole,
    ParseFailed,
    RedactionBlocked,
)
from ai.contracts.problem_generation import (
    DifficultyBand,
    GeneratedItem,
    ItemResult,
    ProblemItemStatus,
    ProblemRequest,
    SolveResult,
)
from ai.contracts.taxonomy import TypeTag
from ai.llm.gateway import LlmGateway
from ai.llm.prompts.loader import LoadedPromptTemplate, load_prompt_template
from ai.llm.structured import parse
from ai.runtime.redaction import redact

_ITEM_PROMPT_ID = "pg.items.v1"


class ImmutableStoreConflict(RuntimeError):
    """같은 멱등 키에 서로 다른 불변 스냅숏을 쓰려 함."""


class RetryContext(BaseModel):
    """다음 생성 시도에 전달하는 비민감 실패 요약."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    attempt_no: int = Field(ge=1, le=3)
    failed_checks: tuple[str, ...] = ()
    previous_stem_hash: str | None = Field(
        default=None,
        pattern=r"^sha256:[0-9a-f]{64}$",
    )
    difficulty_direction: str | None = Field(default=None, min_length=1)


class CandidateSnapshot(BaseModel):
    """게이트 ①·②를 통과한 시도 1개의 불변 복귀본."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    set_id: UUID
    slot_index: int = Field(ge=0)
    attempt_no: int = Field(ge=1, le=3)
    item: GeneratedItem
    solve_result: SolveResult
    context_pack_id: UUID
    difficulty_est: float
    difficulty_band: DifficultyBand
    snapshot_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


@runtime_checkable
class CandidateStore(Protocol):
    """ITEM_CANDIDATE 승인 전 사용하는 교체 가능한 저장 경계."""

    async def put(self, candidate: CandidateSnapshot) -> str:
        """후보를 멱등·불변 저장하고 참조를 반환한다."""
        ...

    async def get(self, candidate_ref: str) -> CandidateSnapshot:
        """참조로 저장 후보를 읽는다."""
        ...


class InMemoryCandidateStore:
    """테스트·M2 수직 슬라이스용 불변 후보 저장소."""

    def __init__(self) -> None:
        self._records: dict[str, CandidateSnapshot] = {}
        self._lock = asyncio.Lock()

    async def put(self, candidate: CandidateSnapshot) -> str:
        candidate_ref = (
            f"item-candidate:{candidate.set_id}:"
            f"{candidate.slot_index}:{candidate.attempt_no}"
        )
        async with self._lock:
            existing = self._records.get(candidate_ref)
            if existing is not None and existing != candidate:
                raise ImmutableStoreConflict(
                    f"후보 불변 스냅숏 충돌: {candidate_ref}"
                )
            self._records[candidate_ref] = candidate
        return candidate_ref

    async def get(self, candidate_ref: str) -> CandidateSnapshot:
        async with self._lock:
            try:
                return self._records[candidate_ref]
            except KeyError as error:
                raise LookupError(f"저장되지 않은 후보 참조: {candidate_ref}") from error

    async def list_all(self) -> tuple[CandidateSnapshot, ...]:
        async with self._lock:
            return tuple(self._records[key] for key in sorted(self._records))


class StoredProblemItem(BaseModel):
    """M2에서 DB 대신 보존하는 슬롯 최종본."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    item_id: UUID
    set_id: UUID
    slot_index: int = Field(ge=0)
    result: ItemResult
    candidate_ref: str | None = Field(default=None, min_length=1)
    item: GeneratedItem | None = None

    @model_validator(mode="after")
    def validate_body(self) -> StoredProblemItem:
        verified_statuses = {
            ProblemItemStatus.VERIFIED,
            ProblemItemStatus.NEEDS_REVIEW,
        }
        if self.result.item_id != self.item_id:
            raise ValueError("저장 문항 ID와 ItemResult.item_id가 다르다")
        if self.result.status in verified_statuses and (
            self.item is None or self.candidate_ref is None
        ):
            raise ValueError("검증 완료 최종본에는 item과 candidate_ref가 필요하다")
        if self.result.status not in verified_statuses | {
            ProblemItemStatus.VERIFICATION_UNAVAILABLE
        }:
            raise ValueError("최종본 저장소에는 dropped 상태를 저장하지 않는다")
        return self

    @property
    def attempt_no(self) -> int:
        return self.result.attempt_no

    @property
    def status(self) -> ProblemItemStatus:
        return self.result.status


@runtime_checkable
class ProblemItemStore(Protocol):
    """최종 문제 문항의 교체 가능한 저장 경계."""

    async def save(
        self,
        *,
        set_id: UUID,
        slot_index: int,
        result: ItemResult,
        candidate_ref: str | None,
        item: GeneratedItem | None,
    ) -> StoredProblemItem:
        """슬롯 최종본을 멱등 저장한다."""
        ...

    async def get(self, set_id: UUID, slot_index: int) -> StoredProblemItem:
        """세트와 슬롯으로 최종본을 읽는다."""
        ...


class InMemoryProblemItemStore:
    """테스트·M2 수직 슬라이스용 최종본 저장소."""

    def __init__(self) -> None:
        self._records: dict[tuple[UUID, int], StoredProblemItem] = {}
        self._lock = asyncio.Lock()

    async def save(
        self,
        *,
        set_id: UUID,
        slot_index: int,
        result: ItemResult,
        candidate_ref: str | None,
        item: GeneratedItem | None,
    ) -> StoredProblemItem:
        key = (set_id, slot_index)
        record = StoredProblemItem(
            item_id=problem_item_id(set_id, slot_index),
            set_id=set_id,
            slot_index=slot_index,
            result=result,
            candidate_ref=candidate_ref,
            item=item,
        )
        async with self._lock:
            existing = self._records.get(key)
            if existing is not None and existing != record:
                raise ImmutableStoreConflict(
                    f"문항 최종본 멱등 충돌: set={set_id}, slot={slot_index}"
                )
            self._records[key] = record
        return record

    async def get(self, set_id: UUID, slot_index: int) -> StoredProblemItem:
        async with self._lock:
            try:
                return self._records[(set_id, slot_index)]
            except KeyError as error:
                raise LookupError(
                    f"저장되지 않은 문항: set={set_id}, slot={slot_index}"
                ) from error

    async def list_all(self) -> tuple[StoredProblemItem, ...]:
        async with self._lock:
            return tuple(
                self._records[key]
                for key in sorted(self._records, key=lambda value: (str(value[0]), value[1]))
            )


class ProblemGenerator:
    """버전 프롬프트·redaction·구조화 파서를 거치는 생성기."""

    def __init__(
        self,
        gateway: LlmGateway,
        prompt: LoadedPromptTemplate | None = None,
    ) -> None:
        self._gateway = gateway
        self._prompt = prompt or load_prompt_template(_ITEM_PROMPT_ID)
        if self._prompt.role is not ModelRole.GENERATOR:
            raise ValueError("문항 생성 프롬프트 role은 generator여야 한다")
        if self._prompt.response_schema_name != GeneratedItem.__name__:
            raise ValueError("문항 생성 프롬프트 응답 스키마가 GeneratedItem이 아니다")

    @property
    def prompt_version(self) -> str:
        return self._prompt.version

    async def generate(
        self,
        *,
        request: ProblemRequest,
        type_tag: TypeTag,
        skill_node_id: str,
        context_pack: ContextPack,
        retry_context: RetryContext,
        execution_context: ExecutionContext,
    ) -> GeneratedItem:
        generation_input = {
            "area_tag": request.area_tag.value,
            "type_tag": type_tag.value,
            "item_format": request.item_format.value,
            "skill_node_id": skill_node_id,
            "requested_difficulty": (
                request.requested_difficulty.value
                if request.requested_difficulty is not None
                else None
            ),
            "topic_hint": request.topic_hint,
        }
        prompt_text = self._prompt.render(
            {
                "context_pack_json": _canonical_json(
                    context_pack.model_dump(mode="json")
                ),
                "generation_input_json": _canonical_json(generation_input),
                "retry_context_json": _canonical_json(
                    retry_context.model_dump(mode="json")
                ),
            }
        )
        redacted = redact(prompt_text)
        if redacted.uncertain:
            raise RedactionBlocked("문항 생성 프롬프트의 개인정보 마스킹이 불확실하다")

        result = await self._gateway.complete(
            LLMRequest(
                role=ModelRole.GENERATOR,
                prompt=redacted.masked_text,
                prompt_id=self._prompt.prompt_id,
                prompt_version=self._prompt.version,
                response_schema_name=self._prompt.response_schema_name,
            ),
            execution_context,
        )
        return parse(require_successful_text(result), GeneratedItem)


def build_candidate_snapshot(
    *,
    set_id: UUID,
    slot_index: int,
    attempt_no: int,
    item: GeneratedItem,
    solve_result: SolveResult,
    context_pack_id: UUID,
    difficulty_est: float,
    difficulty_band: DifficultyBand,
) -> CandidateSnapshot:
    body = {
        "set_id": str(set_id),
        "slot_index": slot_index,
        "attempt_no": attempt_no,
        "item": item.model_dump(mode="json"),
        "solve_result": solve_result.model_dump(mode="json"),
        "context_pack_id": str(context_pack_id),
        "difficulty_est": difficulty_est,
        "difficulty_band": difficulty_band.value,
    }
    return CandidateSnapshot(
        set_id=set_id,
        slot_index=slot_index,
        attempt_no=attempt_no,
        item=item,
        solve_result=solve_result,
        context_pack_id=context_pack_id,
        difficulty_est=difficulty_est,
        difficulty_band=difficulty_band,
        snapshot_hash=_sha256(_canonical_json(body)),
    )


def item_stem_hash(item: GeneratedItem) -> str:
    return _sha256(item.stem)


def problem_item_id(set_id: UUID, slot_index: int) -> UUID:
    return uuid5(set_id, f"problem-item:{slot_index}")


def require_successful_text(result: LLMResult) -> str:
    if result.outcome is CallOutcome.TIMEOUT:
        raise LlmTimeout("LLM provider가 timeout 결과를 반환했다")
    if result.outcome is CallOutcome.PROVIDER_ERROR:
        raise LlmUnavailable("LLM provider가 오류 결과를 반환했다")
    if result.outcome is CallOutcome.REDACTION_BLOCKED:
        raise RedactionBlocked("LLM provider 호출이 redaction 단계에서 차단됐다")
    if result.outcome is CallOutcome.FIELD_MISSING:
        raise FieldMissing("LLM provider가 필수 필드 누락 결과를 반환했다")
    if result.outcome in {CallOutcome.PARSE_FAIL, CallOutcome.BAD_REF}:
        raise ParseFailed(f"LLM provider 결과를 사용할 수 없다: {result.outcome.value}")
    if result.outcome is not CallOutcome.OK or result.text is None:
        raise ParseFailed("LLM provider의 성공 본문이 없다")
    return result.text


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _sha256(value: str) -> str:
    return f"sha256:{hashlib.sha256(value.encode('utf-8')).hexdigest()}"


__all__ = [
    "CandidateSnapshot",
    "CandidateStore",
    "ImmutableStoreConflict",
    "InMemoryCandidateStore",
    "InMemoryProblemItemStore",
    "ProblemGenerator",
    "ProblemItemStore",
    "RetryContext",
    "StoredProblemItem",
    "build_candidate_snapshot",
    "item_stem_hash",
    "problem_item_id",
    "require_successful_text",
]
