"""contracts/gates.py 스모크 — enum 값 고정 · 왕복 직렬화 · 거부는 반환값.

불변식 4(CLAUDE.md): 게이트 거부는 에러가 아니다 — 예외가 아니라 반환값이다.
"""

import pytest

from ai.contracts.gates import (
    BlockedReason,
    Gate,
    GateChainResult,
    GateName,
    GateResult,
    OwnerKind,
)


class _PassingGate:
    @property
    def name(self) -> GateName:
        return GateName.EVIDENCE

    def check(self, payload: object, seq: int) -> GateResult:
        return GateResult(gate_name=self.name, passed=True, seq=seq)


class _RejectingGate:
    """거부를 예외가 아니라 반환값으로 표현한다 (불변식 4)."""

    @property
    def name(self) -> GateName:
        return GateName.DATA_SUFFICIENCY

    def check(self, payload: object, seq: int) -> GateResult:
        return GateResult(
            gate_name=self.name,
            passed=False,
            seq=seq,
            reason="data_lt_2weeks",
        )


def test_gate_name_values_frozen() -> None:
    """ERD GATE_RESULT.gate_name의 8종."""
    assert {gate.value for gate in GateName} == {
        "Consent",
        "DataSufficiency",
        "Evidence",
        "SourceGrounding",
        "ToneSafety",
        "RequiredField",
        "MappingConfidence",
        "TeacherConfirm",
    }


def test_owner_kind_values_frozen() -> None:
    assert {kind.value for kind in OwnerKind} == {"draft", "import_job"}


def test_blocked_reason_values_frozen() -> None:
    """error_codes.md §2.2 — 핑퐁 정책서 §4와 동일 enum."""
    assert {reason.value for reason in BlockedReason} == {
        "evidence_missing",
        "comparison_exposure",
        "tone_violation",
        "pii_exposure",
        "out_of_scope",
    }


def test_gate_result_roundtrip() -> None:
    result = GateResult(
        gate_name=GateName.TONE_SAFETY,
        passed=False,
        seq=3,
        reason="tone_violation",
    )
    assert GateResult.model_validate(result.model_dump(mode="json")) == result


def test_gate_chain_result_roundtrip() -> None:
    chain = GateChainResult(
        results=(
            GateResult(gate_name=GateName.CONSENT, passed=True, seq=0),
            GateResult(gate_name=GateName.EVIDENCE, passed=True, seq=1),
        ),
    )
    assert GateChainResult.model_validate(chain.model_dump(mode="json")) == chain


def test_stubs_satisfy_gate_protocol() -> None:
    assert isinstance(_PassingGate(), Gate)
    assert isinstance(_RejectingGate(), Gate)


def test_rejection_is_returned_not_raised() -> None:
    """거부가 예외로 새어나오면 불변식 4 위반이다."""
    result = _RejectingGate().check(payload=object(), seq=0)
    assert result.passed is False
    assert result.reason == "data_lt_2weeks"


def test_chain_passed_requires_all_gates() -> None:
    chain = GateChainResult(
        results=(
            GateResult(gate_name=GateName.CONSENT, passed=True, seq=0),
            GateResult(gate_name=GateName.DATA_SUFFICIENCY, passed=False, seq=1, reason="x"),
        ),
    )
    assert chain.passed is False


def test_empty_chain_is_not_passed() -> None:
    """게이트 미실행을 통과로 읽으면 안 된다 — 스킵이 곧 우회가 된다."""
    assert GateChainResult().passed is False


def test_first_rejection_identifies_stop_point() -> None:
    chain = GateChainResult(
        results=(
            GateResult(gate_name=GateName.CONSENT, passed=True, seq=0),
            GateResult(gate_name=GateName.EVIDENCE, passed=False, seq=1, reason="evidence_missing"),
            GateResult(gate_name=GateName.TONE_SAFETY, passed=False, seq=2, reason="tone"),
        ),
    )
    first = chain.first_rejection
    assert first is not None
    assert first.gate_name is GateName.EVIDENCE
    assert first.seq == 1


def test_first_rejection_is_none_when_all_pass() -> None:
    chain = GateChainResult(results=(GateResult(gate_name=GateName.CONSENT, passed=True, seq=0),))
    assert chain.first_rejection is None
    assert chain.passed is True


def test_negative_seq_rejected() -> None:
    with pytest.raises(ValueError, match="seq"):
        GateResult(gate_name=GateName.CONSENT, passed=True, seq=-1)
