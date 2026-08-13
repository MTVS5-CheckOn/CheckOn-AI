"""게이트 ① 규칙 검증 R-1~R-7 — 결정론 판정. LLM·I/O 없음."""

from __future__ import annotations

import re
from typing import Self

from pydantic import BaseModel, ConfigDict, model_validator

from ai.contracts.graphrag import ContextPack
from ai.contracts.problem_generation import GeneratedItem, ProblemRequest
from ai.contracts.taxonomy import TypeTag
from ai.problem_generation.domain.policy import BannedTopicsConfig

_NORMALIZE_PATTERN = re.compile(r"[\W_]+", flags=re.UNICODE)


class RuleValidationResult(BaseModel):
    """게이트 ①의 결정론 결과."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    passed: bool
    verification_available: bool = True
    failed_checks: tuple[str, ...] = ()
    banned_topic: bool = False
    source_unverified: bool = False

    @model_validator(mode="after")
    def validate_result(self) -> Self:
        if self.passed and (
            not self.verification_available
            or self.failed_checks
            or self.banned_topic
            or self.source_unverified
        ):
            raise ValueError("통과 결과에는 실패 상세를 기록할 수 없다")
        if not self.passed and not self.failed_checks:
            raise ValueError("실패 결과에는 failed_checks가 필요하다")
        return self


class RuleValidator:
    """LLM 판정 없이 구조·근거 참조·금칙을 검사한다."""

    def __init__(
        self,
        banned_topics: BannedTopicsConfig,
        *,
        duplicate_similarity_max: float,
    ) -> None:
        if not 0.0 <= duplicate_similarity_max <= 1.0:
            raise ValueError("dup_similarity_max는 0과 1 사이여야 한다")
        self._banned_topics = banned_topics
        self._duplicate_similarity_max = duplicate_similarity_max

    @property
    def banned_topics_version(self) -> str:
        return self._banned_topics.version

    def validate(
        self,
        *,
        item: GeneratedItem,
        request: ProblemRequest,
        type_tag: TypeTag,
        skill_node_id: str,
        context_pack: ContextPack,
        previous_items: tuple[GeneratedItem, ...] = (),
    ) -> RuleValidationResult:
        failed: list[str] = []

        if (
            item.area_tag is not request.area_tag
            or item.type_tag is not type_tag
            or item.item_format is not request.item_format
        ):
            failed.append("R-4:요청_태그_불일치")
        if item.skill_node_id != skill_node_id:
            failed.append("R-7:목표_노드_불일치")

        normalized_choices = tuple(_normalize(choice.text) for choice in item.choices)
        if len(set(normalized_choices)) != len(normalized_choices):
            failed.append("R-2:중복_선지")

        answer_text = next(
            choice.text for choice in item.choices if choice.no == item.answer.correct_no
        )
        if _normalize(answer_text) in _normalize(item.stem):
            failed.append("R-3:문두_정답_노출")

        inspected_text = "\n".join(
            (
                item.stem,
                *(choice.text for choice in item.choices),
                item.rationale,
            )
        ).casefold()
        banned = any(term.casefold() in inspected_text for term in self._banned_topics.all_terms)
        if banned:
            failed.append("R-5:금칙_오염")

        # DICT_ENTRY는 커밋된 stdict 최소 색인의 known ref 대조로 R-1을 만족한다.
        # 증명 범위는 승인 sense_code·표제어·품사·전문분야이며 뜻풀이 일치가 아니다.
        allowed_refs = _allowed_evidence_refs(context_pack)
        if allowed_refs is None or not allowed_refs:
            failed.append("R-1:기준_자료_없음")
            return RuleValidationResult(
                passed=False,
                verification_available=False,
                failed_checks=tuple(failed),
                banned_topic=banned,
                source_unverified=True,
            )

        evidence_refs = {anchor.ref for anchor in item.evidence}
        source_unverified = not evidence_refs.issubset(allowed_refs)
        if source_unverified:
            failed.append("R-1:근거_참조_불일치")

        normalized_stem = _normalize(item.stem)
        if any(
            normalized_stem == _normalize(previous.stem)
            or _ngram_similarity(normalized_stem, _normalize(previous.stem))
            > self._duplicate_similarity_max
            for previous in previous_items
        ):
            # `previous_items`는 같은 세트·같은 학생의 최근 출제분이다. 평가원·EBS
            # 기출과의 중복은 검사하지 않는다 — 외부 대조 코퍼스가 없다(09 §3 C-15).
            # 종전 이름 "기출제_문항_중복"은 실제 검사 범위와 달라 오독을 불렀다.
            # 외부 대조는 R-8로 예약한다(06 §1).
            failed.append("R-6:세트내_중복")

        return RuleValidationResult(
            passed=not failed,
            failed_checks=tuple(failed),
            banned_topic=banned,
            source_unverified=source_unverified,
        )


def _allowed_evidence_refs(context_pack: ContextPack) -> frozenset[str] | None:
    raw = context_pack.retrieval_trace.get("allowed_evidence_refs")
    if not isinstance(raw, list):
        return None
    values: list[str] = []
    for value in raw:
        if not isinstance(value, str):
            return None
        values.append(value)
    return frozenset(values)


def _normalize(value: str) -> str:
    return _NORMALIZE_PATTERN.sub("", value).casefold()


def _ngram_similarity(left: str, right: str, size: int = 3) -> float:
    left_ngrams = _ngrams(left, size)
    right_ngrams = _ngrams(right, size)
    if not left_ngrams and not right_ngrams:
        return 1.0
    union = left_ngrams | right_ngrams
    return len(left_ngrams & right_ngrams) / len(union) if union else 0.0


def _ngrams(value: str, size: int) -> frozenset[str]:
    if len(value) < size:
        return frozenset({value}) if value else frozenset()
    return frozenset(value[index : index + size] for index in range(len(value) - size + 1))

def has_reference_data(context_pack: ContextPack) -> bool:
    """R-1을 수행할 승인 근거 참조가 ContextPack에 있는지 확인한다."""

    allowed_refs = _allowed_evidence_refs(context_pack)
    return bool(allowed_refs)
