"""원장 완전성 판정이 **FK를 흉내 내지 않는가** (99 #36 G2).

🔴 **가장 쉬운 오답은 *"`AGENT_RUN`이 있는데 `AI_RUN`이 없으면 red"* 다.** 잡은 모델 실행보다
먼저 존재하므로 그 조건은 **정상 생애주기를 결함으로 오인**한다 — FK를 못 쓰게 만든 바로 그 이유다.

이 파일은 **다섯 값이 섞이지 않는지**를 본다: 정상 부재 · 확정 결함 · 증명 불가 ·
㉾의 별도 결손 · 정상.
"""

from __future__ import annotations

import uuid
from typing import Any, Final

import pytest

from ai.contracts.agents import JobPhase, WorkerKind
from ai.contracts.execution import Capability
from ai.db.ledger_completeness import (
    WORKER_CAPABILITY,
    LedgerObservation,
    LedgerVerdict,
    blocks_flip,
    judge_ledger_row,
    summarize,
)

_TENANT: Final = "t_ledger"
_RUN: Final = uuid.UUID("00000000-0000-0000-0000-0000000004a1")


def _obs(**over: Any) -> LedgerObservation:  # noqa: ANN401 — 픽스처 덮어쓰기
    base: dict[str, Any] = {
        "tenant_id": _TENANT,
        "job_id": uuid.uuid4(),
        "run_id": _RUN,
        "agent_kind": WorkerKind.COUNSEL_PACK,
        "status": JobPhase.SUCCEEDED,
        "started_at_is_set": True,
    }
    return LedgerObservation(**(base | over))


def _verdict(**over: Any) -> LedgerVerdict:  # noqa: ANN401 — 픽스처 덮어쓰기
    return judge_ledger_row(_obs(**over)).verdict


# ── 확정 결함 ────────────────────────────────────────────────────


@pytest.mark.parametrize("kind", [WorkerKind.COUNSEL_PACK, WorkerKind.PROBLEM_GENERATION])
def test_a_succeeded_job_without_a_ledger_is_a_violation(kind: WorkerKind) -> None:
    """🔴 종단인데 기록이 없다."""
    assert _verdict(agent_kind=kind, status=JobPhase.SUCCEEDED) is (
        LedgerVerdict.VIOLATION
    )


def test_a_failed_job_with_call_evidence_is_a_violation() -> None:
    """🔴 **실패해도 호출 양성 증거가 있으면 결함**이다.

    ⚠ **`result_ref`는 이 축에서 뺐다**(지시서 59) — 산출물 저장 증거이지 호출 증거가
    아니다. 그 경우는 `test_a_result_ref_is_not_call_evidence`가 따로 본다.
    """
    assert _verdict(status=JobPhase.FAILED, steps_with_llm_call=2) is (
        LedgerVerdict.VIOLATION
    )


def test_a_ledger_from_another_tenant_is_a_violation() -> None:
    """🔴 FK가 사라졌으므로 **논리 결합이 경계를 넘는지** 우리가 본다."""
    assert (
        _verdict(
            ai_run_execution_id=_RUN,
            ai_run_tenant_id="t_other",
            ai_run_capability=Capability.COMPOSITION,
        )
        is LedgerVerdict.VIOLATION
    )


def test_a_mismatched_run_id_is_a_violation() -> None:
    assert (
        _verdict(
            ai_run_execution_id=uuid.uuid4(),
            ai_run_tenant_id=_TENANT,
            ai_run_capability=Capability.COMPOSITION,
        )
        is LedgerVerdict.VIOLATION
    )


def test_a_mismatched_capability_is_a_violation() -> None:
    assert (
        _verdict(
            ai_run_execution_id=_RUN,
            ai_run_tenant_id=_TENANT,
            ai_run_capability=Capability.DETECTION,
        )
        is LedgerVerdict.VIOLATION
    )


# ── 증명 불가 ────────────────────────────────────────────────────


def test_a_failed_job_without_evidence_is_unknown_not_ok() -> None:
    """🔴 **초록이 아니다.** 실패 경로는 적재 실패를 삼키므로 「안 불렀다」와
    「부르고 기록을 잃었다」가 **증거상 같다.**"""
    #: ⚠ 부정 단정(`is not OK`)은 안 쓴다 — mypy가 **정적으로 참임을 증명**해 버려
    #:   검사가 아무것도 안 보게 된다. 「초록이 아니다」는 `blocks_flip`이 본다(아래).
    assert _verdict(status=JobPhase.FAILED) is LedgerVerdict.UNKNOWN


def test_unknown_blocks_the_flip() -> None:
    """🔴 `unknown`을 통과시키면 **증명 못 한 것이 초록으로 세어진다.**"""
    counts = summarize([judge_ledger_row(_obs(status=JobPhase.FAILED))])
    assert counts[LedgerVerdict.UNKNOWN] == 1
    assert blocks_flip(counts), "unknown이 플립을 안 막는다"


# ── 정상 부재(거짓 red 방지) ─────────────────────────────────────


@pytest.mark.parametrize(
    "status",
    [JobPhase.QUEUED, JobPhase.LEASED, JobPhase.RUNNING, JobPhase.PAUSED],
)
def test_an_in_flight_job_without_a_ledger_is_allowed(status: JobPhase) -> None:
    """실행이 안 끝났다 — **잡은 모델 실행보다 먼저 존재한다.**"""
    assert _verdict(status=status) is LedgerVerdict.ALLOWED_ABSENCE


def test_a_cancelled_before_start_is_allowed() -> None:
    assert (
        _verdict(status=JobPhase.CANCELLED, started_at_is_set=False)
        is LedgerVerdict.ALLOWED_ABSENCE
    )


def test_a_zero_call_run_with_a_ledger_and_null_model_fields_is_ok() -> None:
    """🔴 **LLM 0콜이 정직한 경로**(`template_only`·근거 0건)는 결함이 아니다.

    원장 행은 있고 모델 필드만 비어 있다 — `#144`의 「사용 축」 규율 그대로다.
    """
    assert (
        _verdict(
            status=JobPhase.SUCCEEDED,
            ai_run_execution_id=_RUN,
            ai_run_tenant_id=_TENANT,
            ai_run_capability=Capability.COMPOSITION,
            steps_with_llm_call=0,
            result_ref=None,
        )
        is LedgerVerdict.OK
    )


# ── ㉾ 분리 ──────────────────────────────────────────────────────


def test_a_finished_probe_without_a_ledger_is_a_violation() -> None:
    """🔴 **㉾ 해소 뒤에는 비켜 가지 않는다**(8/12) — 종단 성공인데 원장이 없으면 결함이다.

    ⚠ 종전에는 이 자리가 `separate_gap`이었다. 그건 *"그 워커는 `record_run()`을 한 번도
    안 부른다"* 는 **당시의 사실** 때문이었고, 그 사실이 바뀌었다.
    """
    counts = summarize(
        [
            judge_ledger_row(
                _obs(agent_kind=WorkerKind.MAPPING_PROBE, status=JobPhase.SUCCEEDED)
            )
        ]
    )
    assert counts[LedgerVerdict.VIOLATION] == 1, "원장 없는 조사 완주가 안 잡힌다"
    assert blocks_flip(counts), "㉾가 이제 관문을 막아야 한다"


def test_a_probe_with_a_matching_ledger_is_ok() -> None:
    """조사 잡도 **다른 둘과 같은 규칙**으로 초록이 된다."""
    assert (
        _verdict(
            agent_kind=WorkerKind.MAPPING_PROBE,
            status=JobPhase.SUCCEEDED,
            ai_run_execution_id=_RUN,
            ai_run_tenant_id=_TENANT,
            ai_run_capability=Capability.IMPORT_MAPPING,
        )
        is LedgerVerdict.OK
    )


def test_a_probe_recorded_under_another_capability_is_a_violation() -> None:
    """🔴 capability를 잘못 적으면 초록이 되면 안 된다."""
    assert (
        _verdict(
            agent_kind=WorkerKind.MAPPING_PROBE,
            status=JobPhase.SUCCEEDED,
            ai_run_execution_id=_RUN,
            ai_run_tenant_id=_TENANT,
            ai_run_capability=Capability.COMPOSITION,
        )
        is LedgerVerdict.VIOLATION
    )


def test_the_bypass_verdict_is_gone() -> None:
    """🔴 **판정에 예외 통로가 없다**(99 ㉾ 해소 · 8/12).

    ⚠ `separate_gap`을 남겨 두면 다음 사람이 *"관문을 안 막는 판정이 필요하다"* 고 느낄 때
    거기로 밀어 넣는다 — 규율이 아니라 우회 통로가 된다. **값을 없앴다.**
    """
    assert {v.value for v in LedgerVerdict} == {
        "ok",
        "allowed_absence",
        "violation",
        "unknown",
    }


def test_every_worker_kind_has_an_expected_capability() -> None:
    """🔴 **세 워커가 전부 표에 있다**(99 ㉾ 해소 · 8/12).

    ⚠ 종전에는 `mapping_probe`가 **일부러 비어** 있었고, 그 이유는 *"그 워커는
    `record_run()`을 한 번도 안 부르므로 기대 capability를 **관측한 적이 없다**"* 였다.
    지금은 관측했으므로 적는다 — 값은 여전히 **프로덕션이 실제로 쓰는 것**에서 온다.

    🔴 **목록을 손으로 들지 않는다** — `WorkerKind` 전량이 표에 있는지 본다.
    새 워커가 생기면 **원장 기대값을 정하지 않고 지나갈 수 없다.**
    """
    missing = sorted(k.value for k in WorkerKind if k not in WORKER_CAPABILITY)
    assert not missing, f"기대 capability가 없는 워커가 있다: {missing}"
    assert WORKER_CAPABILITY[WorkerKind.MAPPING_PROBE] is Capability.IMPORT_MAPPING


# ── 절단 가드 ────────────────────────────────────────────────────


def test_zero_rows_is_not_reported_as_a_pass() -> None:
    """🔴 **관측 0건을 「완전성 통과」로 세지 않는다** — 안 본 것과 없는 것은 다르다.

    ⚠ 이 단정은 `summarize`가 **0건도 키로 남기는지**를 본다. 키가 사라지면
    리더가 *"violation 키가 없으니 0이다"* 로 읽는다.
    """
    counts = summarize([])
    assert set(counts) == set(LedgerVerdict), "판정 키가 사라졌다 — 리더가 0으로 읽는다"
    assert all(value == 0 for value in counts.values())
    assert sum(counts.values()) == 0, "관측이 0건이면 「통과」가 아니라 「측정 없음」이다"


def test_every_verdict_is_reachable() -> None:
    """🔴 **네** 값이 전부 실제로 나오는지 — 하나라도 죽어 있으면 그 축을 안 보는 것이다.

    ⚠ 종전엔 다섯이었다 — `separate_gap`은 생산자가 0이 돼 없앴다(99 ㉾ 해소 · 8/12).
    """
    reached = {
        judge_ledger_row(_obs(**case)).verdict
        for case in (
            {
                "ai_run_execution_id": _RUN,
                "ai_run_tenant_id": _TENANT,
                "ai_run_capability": Capability.COMPOSITION,
            },
            {"status": JobPhase.QUEUED},
            {"status": JobPhase.SUCCEEDED},
            {"status": JobPhase.FAILED},
        )
    }
    assert reached == set(LedgerVerdict), f"도달 못 한 판정이 있다: {set(LedgerVerdict) - reached}"


# ── (지시서 59) 숨은 결합·판정 순서 ─────────────────────────────


def test_a_run_id_owned_by_another_tenant_is_a_violation() -> None:
    """🔴 **테넌트 결합 위반이 실 PG에서 도달 불가였다**(실측 8/10).

    조회가 `AI_RUN`을 **테넌트까지 걸어** 조인하므로, 같은 `execution_id`가 **남의 테넌트에**
    있으면 `AI_RUN` 열이 전부 `None`이 되고 판정은 **`allowed_absence`**가 됐다 —
    *"queued라 아직 없다"* 와 **구분이 안 된다.**

    ⚠ **조인에서 테넌트를 빼서 남의 행 전문을 읽는 방식으로 고치지 않는다** — 그건 경계를
    넘는다. **존재 여부만** 별도 관측으로 받고 **남의 식별자는 안 싣는다.**
    """
    assert (
        _verdict(status=JobPhase.QUEUED, run_id_exists_in_other_tenant=True)
        is LedgerVerdict.VIOLATION
    )


def test_the_cross_tenant_check_outranks_a_normal_absence() -> None:
    """🔴 **판정 순서** — 정상 부재보다 **먼저** 걸려야 한다."""
    for status in (JobPhase.QUEUED, JobPhase.RUNNING, JobPhase.PAUSED):
        assert (
            _verdict(status=status, run_id_exists_in_other_tenant=True)
            is LedgerVerdict.VIOLATION
        ), f"{status.value}에서 교차 테넌트 충돌이 정상 부재에 가려졌다"


def test_a_paused_job_with_call_evidence_is_a_violation() -> None:
    """🔴 **`paused`가 in-flight라는 이유로 증거를 덮으면 안 된다.**

    서킷 개방은 **호출을 이미 소비한 뒤**에 온다 — 증거가 있으면 원장이 있어야 한다.
    """
    assert (
        _verdict(status=JobPhase.PAUSED, steps_with_llm_call=1)
        is LedgerVerdict.VIOLATION
    )


def test_a_running_job_with_call_evidence_is_still_allowed() -> None:
    """⚠ `running`은 **아직 `finally` 전**일 수 있다 — 증거가 있어도 red로 만들지 않는다."""
    assert (
        _verdict(status=JobPhase.RUNNING, steps_with_llm_call=3)
        is LedgerVerdict.ALLOWED_ABSENCE
    )


def test_a_cancelled_after_start_follows_the_evidence() -> None:
    """실행 후 취소 — 증거가 있으면 violation, 없으면 unknown."""
    assert (
        _verdict(status=JobPhase.CANCELLED, started_at_is_set=True, steps_with_llm_call=1)
        is LedgerVerdict.VIOLATION
    )
    assert (
        _verdict(status=JobPhase.CANCELLED, started_at_is_set=True)
        is LedgerVerdict.UNKNOWN
    )


def test_a_result_ref_is_not_call_evidence() -> None:
    """🔴 **`result_ref`는 호출 증거가 아니다** — 산출물 저장·종단 증거다.

    ⚠ **LLM 0콜 성공 경로도 결과를 만든다.** 호출 증거라고 부르면
    **관측의 이름이 실제로 보는 것보다 넓어진다**(로그 85 계열).
    """
    assert _obs(result_ref="pack://x").consumed_a_call is False
    assert _obs(steps_with_llm_call=1).consumed_a_call is True
    #: `failed` + `result_ref`만으로는 결함을 확정할 수 없다 ⇒ **증명 불가**다.
    assert (
        _verdict(status=JobPhase.FAILED, result_ref="pack://x") is LedgerVerdict.UNKNOWN
    )


def test_the_call_violation_reason_does_not_cite_the_result_ref() -> None:
    """🔴 **사유에도 `result_ref`를 안 싣는다** — 옆에 적으면 근거로 읽힌다.

    호출 판정의 근거는 `AGENT_STEP.llm_call_id` **하나뿐**이다.
    """
    reason = judge_ledger_row(
        _obs(status=JobPhase.FAILED, steps_with_llm_call=2, result_ref="pack://x")
    ).reason
    assert "result_ref" not in reason, f"사유가 호출 증거가 아닌 것을 근거처럼 적는다: {reason}"
    assert "llm_call 2건" in reason


def test_an_unknown_capability_is_a_violation_not_a_crash() -> None:
    """🔴 **낯선 `capability` 하나가 점검 전체를 죽이면 안 된다.**

    DB 문자열을 변환 단계에서 바로 enum으로 바꾸면 **미등록 값 하나에 리포트가 통째로
    안 나온다.** 그 행만 `violation`이고 나머지는 계속 렌더돼야 한다.
    """
    assert (
        _verdict(
            ai_run_execution_id=_RUN,
            ai_run_tenant_id=_TENANT,
            ai_run_capability="future_unknown",
        )
        is LedgerVerdict.VIOLATION
    )
