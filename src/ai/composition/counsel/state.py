"""counsel_pack 워커 state — `langgraph_state.md` §1.2(⑨ 개정본) 스키마 그대로.

state는 §1.2를 1:1로 옮긴다(값 대조 테스트로 고정 — 필드를 늘리거나 줄이지 않는다).
`StudentResult`·`DraftContext` 같은 도메인 타입은 저장소를 거쳐 워커 밖으로 오가므로
`contracts/composition.py`(A 단독 소유)가 갖는다.

**본문 미복제(§1.2 ⑨):** 체크포인트에는 학생 컨텍스트 본문·초안 본문을 넣지 않는다.
컨텍스트는 `context_ref`로 역참조하고 초안은 `results[].draft_id` 포인터만 든다 —
`MappingProbeState.sheets_meta`("원본 행 아님 — 마스킹 통과 통계만")와 같은 규율이다.
state는 PostgresSaver 체크포인트와 노드 트레이스 두 경로로 나간다.

불변식(§1.2): ① `emphasis_points`의 모든 강조점은 `record_id` 동반(plan 노드도 Evidence 규칙)
② `cursor`는 단조 증가 — 재개 시 `results` 길이와 일치 ③ 학생 1명 실패가 루프를 멈추지 않는다
④ 재개 시 `context_ref` 역참조 해시를 `context_hash`와 대조(불일치 = 손상 → failed).
"""

from __future__ import annotations

import re
from typing import Annotated, Final, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ai.contracts.composition import StudentResult

STATE_SCHEMA_VERSION: Final = "counsel_pack.v1"

#: `context_hash` 표기 — `ProblemGenerationState.request_hash`와 같은 형식(§2.4 대칭).
type Sha256Hash = Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]

#: 강조점에 동반돼야 하는 근거 표기 — 불변식 ①(plan 노드도 Evidence 규칙 적용).
#: `record_id=le_1029` 처럼 근거 record_id를 명시한 강조점만 통과한다.
_RECORD_ID_RE: Final = re.compile(r"record_id=[\w-]+")


class EmptyEvidenceError(ValueError):
    """강조점에 근거 `record_id`가 없다 — 불변식 2(evidence 없는 산출물 금지)."""


def has_record_id(point: str) -> bool:
    """강조점 한 줄에 근거 `record_id`가 동반됐는지 — 순수 함수(불변식 ①)."""
    return _RECORD_ID_RE.search(point) is not None


def validate_emphasis_points(emphasis_points: dict[str, list[str]]) -> None:
    """불변식 ① — 모든 강조점에 근거 `record_id`가 있어야 한다. 순수 함수.

    모델 생성 경로에서는 Pydantic이 이 예외를 `ValidationError`로 감싼다.
    """
    for student_ref, points in emphasis_points.items():
        for point in points:
            if not has_record_id(point):
                raise EmptyEvidenceError(
                    f"{student_ref}의 강조점에 근거 record_id가 없다: {point!r}"
                )


class CounselPackState(BaseModel):
    """§1.2 그대로. 필드 추가·삭제·개명 금지(값 대조 테스트가 강제)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    state_schema_version: Literal["counsel_pack.v1"] = STATE_SCHEMA_VERSION

    # 불변 입력 (기동 시 고정 — 재개해도 안 바뀜)
    tenant_id: str = Field(min_length=1)
    class_ref: str = Field(min_length=1)
    student_refs: list[str]
    """처리 순서 고정 (재현성)."""

    context_ref: str = Field(min_length=1)
    """학생 컨텍스트 묶음의 저장소 참조 — 본문 미복제."""

    context_hash: Sha256Hash
    """재개 시 역참조한 컨텍스트 묶음 해시와 대조(불변식 ④)."""

    plan_version: str = Field(min_length=1)

    # plan 노드 산출 (LLM 1회 — 확정 수치 내 강조점만, 새 사실 생성 금지)
    emphasis_points: dict[str, list[str]] = {}
    """student_ref → 강조점(근거 record_id 필수 — 불변식 ①)."""

    # 진행 상태 (체크포인트 대상)
    cursor: int = Field(default=0, ge=0)
    """student_refs 인덱스 — 재개 지점."""

    results: list[StudentResult] = []
    quota_consumed: int = Field(default=0, ge=0)
    """미터링 리포트용 (차단은 백엔드)."""

    # 종료 산출
    summary: str | None = None
    """"22명 중 19명 생성·2명 데이터 부족·1명 실패"."""

    @model_validator(mode="after")
    def validate_invariants(self) -> Self:
        """불변식 ①②를 생성 시점에 강제한다(③④는 그래프·워커가 지킨다)."""
        validate_emphasis_points(self.emphasis_points)  # 불변식 ①
        if self.cursor != len(self.results):  # 불변식 ② — 재개 시 일치 검증
            raise ValueError(
                f"cursor({self.cursor})와 results 길이({len(self.results)})가 다르다 "
                "— 체크포인트 손상"
            )
        if self.cursor > len(self.student_refs):
            raise ValueError("cursor가 student_refs 범위를 넘었다 — 체크포인트 손상")
        return self

    @property
    def is_complete(self) -> bool:
        """모든 학생을 처리했다 — terminal 판정(§1.3)."""
        return self.cursor >= len(self.student_refs)

    def next_student(self) -> str | None:
        """다음 처리 대상 — 없으면 None(재개도 이 경로로 이어진다)."""
        if self.is_complete:
            return None
        return self.student_refs[self.cursor]


__all__ = [
    "STATE_SCHEMA_VERSION",
    "CounselPackState",
    "EmptyEvidenceError",
    "Sha256Hash",
    "has_record_id",
    "validate_emphasis_points",
]
