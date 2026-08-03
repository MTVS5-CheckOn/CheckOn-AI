"""병합·상한·순위 — 04 §3 AlertCapPolicy.

소유: 박진희 (detection). 순수 함수.

- 복수 규칙 동시 발화는 학생당 1경보로 병합, score = 규칙별 정규화 점수의 max (합산 아님).
- 반·일 TOP 3~5 상한. 초과분은 capped_out 집계(이번 스냅숏에서 잘림 — "다음 날 재평가
  대기 큐"는 상태 지속이 필요해 순수 함수 범위 밖, 후속 작업).
- R5(복귀 케어)는 상한 밖(정책 신호 — 쿼터 무관).
"""

from __future__ import annotations

from dataclasses import dataclass

from ai.contracts.detection import RuleId
from ai.detection.rules import RuleFinding


@dataclass(frozen=True)
class StudentAlert:
    """학생 1명의 병합 경보 — 대표 규칙 1개 + 병합된 규칙 목록."""

    student_ref: str
    class_ref: str
    primary: RuleFinding
    """대표 규칙 = score 최고 (병합 score=max)."""

    merged: tuple[RuleFinding, ...]
    """이 경보에 병합된 전체 규칙(대표 포함) — evidence·brief 재료."""

    is_auto_flag: bool
    """R5 복귀 케어 — 상한 밖."""

    is_advisory: bool = False
    """참고 표시 전용 — 상한 밖(04 §1 R4 재정의).

    **병합된 규칙이 전부 R4일 때만 True**다. R1+R4처럼 다른 규칙이 섞이면 그 경보는
    정식이다 — advisory로 내리면 R1 근거가 알림에서 사라진다.
    """


@dataclass(frozen=True)
class RankedAlert:
    """순위가 부여된 경보."""

    alert: StudentAlert
    rank: int


@dataclass(frozen=True)
class RankResult:
    ranked: tuple[RankedAlert, ...]
    capped_out: int


def merge_student(
    student_ref: str, class_ref: str, findings: list[RuleFinding]
) -> list[StudentAlert]:
    """한 학생의 발화들을 경보로 병합한다.

    R5는 상한 밖이라 별도 경보. 나머지 규칙은 1경보로 병합(대표 = max score).

    병합 결과가 **R4뿐이면 advisory**(참고 표시 전용 · 04 §1 R4 재정의)로 표시한다 —
    R4는 병합 자체는 그대로 받고 소비 단계에서만 상한 밖으로 빠진다.
    """
    alerts: list[StudentAlert] = []
    care = [f for f in findings if f.rule_id is RuleId.R5]
    risk = [f for f in findings if f.rule_id is not RuleId.R5]

    if risk:
        primary = max(risk, key=lambda f: f.score)
        alerts.append(
            StudentAlert(
                student_ref=student_ref,
                class_ref=class_ref,
                primary=primary,
                merged=tuple(sorted(risk, key=lambda f: f.score, reverse=True)),
                is_auto_flag=False,
                # 병합 결과가 R4뿐일 때만 참고 표시 — 다른 규칙이 하나라도 섞이면 정식이다.
                is_advisory=all(f.rule_id is RuleId.R4 for f in risk),
            )
        )
    for care_finding in care:
        alerts.append(
            StudentAlert(
                student_ref=student_ref,
                class_ref=class_ref,
                primary=care_finding,
                merged=(care_finding,),
                is_auto_flag=True,
            )
        )
    return alerts


def rank_class(alerts: list[StudentAlert], cap_max: int) -> RankResult:
    """한 반의 경보를 순위 매기고 상한을 적용한다.

    비-R5 경보만 상한 대상(score DESC, cap_max개 통과, 초과분 capped_out).
    R5(auto_flag)는 상한 밖 — 통과 경보 뒤 순위로 전부 포함한다.
    """
    risk = sorted(
        (a for a in alerts if not a.is_auto_flag),
        key=lambda a: (a.primary.score, a.student_ref),
        reverse=True,
    )
    care = sorted((a for a in alerts if a.is_auto_flag), key=lambda a: a.student_ref)

    passed = risk[:cap_max]
    capped_out = len(risk) - len(passed)

    ranked: list[RankedAlert] = []
    rank = 1
    for alert in passed:
        ranked.append(RankedAlert(alert=alert, rank=rank))
        rank += 1
    for alert in care:
        ranked.append(RankedAlert(alert=alert, rank=rank))
        rank += 1

    return RankResult(ranked=tuple(ranked), capped_out=capped_out)
