"""확정 라벨(평탄 배열) → 4축 `LabelSnapshot` — `part_a/05` §7.

인박스 계약 §4-①의 `labels[]`는 평탄한 4축 값 배열이고 **부분 라벨이 정상 상태**다
(라벨 검토함 계약 §6 "라벨 0개 = 기본 톤 · 라벨은 선택"). 누락 축은 기본값으로 채우고,
4축 enum 밖의 문자열은 **거부**한다 — 누락과 미지값은 다르다(05 §7-4).

순수 함수다(계산·I/O 분리 03 §2). 값 표기의 정본은 `contracts/composition`의 4축 enum이며
**alias를 받지 않는다** — 와이어 alias는 BE가 한 번 보내기 시작하면 영구다.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Final

from ai.contracts.composition import (
    CommStyle,
    Frequency,
    Interest,
    LabelSnapshot,
    Sensitivity,
)

#: 축 이름 → 그 축의 enum. `LabelSnapshot` 필드 이름과 같다(tone_map axis_rules 대응).
_AXIS_ENUMS: Final = {
    "comm": CommStyle,
    "sensitivity": Sensitivity,
    "interest": Interest,
    "frequency": Frequency,
}

#: 축별 기본값 — 05 §7-2. **근거는 그 문서가 소유한다**(여기서 이유를 새로 만들지 않는다).
#: sensitivity=anxious는 비대칭 때문이다 — 과한 완곡보다 무딘 발언의 피해가 크고 HITL이 있다.
DEFAULT_SNAPSHOT: Final = LabelSnapshot(
    comm=CommStyle.NARRATIVE,
    sensitivity=Sensitivity.ANXIOUS,
    interest=Interest.GRADE,
    frequency=Frequency.MONTHLY,
)


class LabelVocabularyError(ValueError):
    """라벨 값이 4축 enum과 맞지 않는다 — 계약 위반이라 조용히 삼키지 않는다."""

    def __init__(self, message: str, *, offending: Sequence[str]) -> None:
        super().__init__(message)
        self.offending = tuple(offending)


def _axis_of(value: str) -> str | None:
    for axis, enum_cls in _AXIS_ENUMS.items():
        if value in {member.value for member in enum_cls}:
            return axis
    return None


def snapshot_from_labels(labels: Sequence[str]) -> tuple[LabelSnapshot, tuple[str, ...]]:
    """확정 라벨 → (4축 스냅숏, 실제 적용된 4축 값).

    두 번째 반환값이 응답의 `labels_applied`다 — **기본값이 쓰였는지 화면이 알 수 있어야
    한다**(05 §7-2). 요청이 보낸 것을 되돌려주는 게 아니라 **실제 적용분**을 싣는다.

    미지값·중복 축은 `LabelVocabularyError`를 올린다(05 §7-4) — 라우터가 400으로 옮긴다.
    누락은 오류가 아니다.
    """
    axes: dict[str, str] = {}
    unknown: list[str] = []
    duplicated: list[str] = []
    for label in labels:
        axis = _axis_of(label)
        if axis is None:
            unknown.append(label)
        elif axis in axes and axes[axis] != label:
            duplicated.append(label)
        else:
            axes[axis] = label
    if unknown:
        raise LabelVocabularyError(
            "4축 라벨 어휘에 없는 값 — 표기 정본은 part_a/05 §1이다(alias 없음)",
            offending=unknown,
        )
    if duplicated:
        raise LabelVocabularyError(
            "같은 축에 값이 둘 — 어느 쪽을 쓸지 임의로 정하지 않는다",
            offending=duplicated,
        )
    # `model_copy(update=…)`는 검증을 건너뛰어 enum 자리에 str이 남는다 — 생성자로 만든다.
    snapshot = LabelSnapshot(**{**DEFAULT_SNAPSHOT.as_axes(), **axes})
    return snapshot, labels_applied_of(snapshot)


def labels_applied_of(snapshot: LabelSnapshot) -> tuple[str, ...]:
    """스냅숏 → 응답의 `labels_applied`.

    🔴 **파생 자리를 하나로 둔다**(99 #02 · ㉻). 늦게 끝난 잡의 결과를 되살릴 때는 원 요청의
    `labels`가 없고 **동결된 스냅숏만** 있다 — 그때 여기서 유도해야 최초 응답과 값이 같다.
    ⚠ 두 곳에 적으면 축 순서가 갈리는 날 **같은 잡의 두 응답이 달라진다.**
    """
    axes = snapshot.as_axes()
    return tuple(axes[axis] for axis in _AXIS_ENUMS)


__all__ = [
    "DEFAULT_SNAPSHOT",
    "LabelVocabularyError",
    "labels_applied_of",
    "snapshot_from_labels",
]
