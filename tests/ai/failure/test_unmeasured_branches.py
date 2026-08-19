"""도달은 하는데 **아무도 안 재는** 자리 여섯 (99 #103 ㉡ 잔여 · #101).

PR-06 이 미실행 19줄의 목록을 냈고 PR-07 이 그중 가장 무거운 하나(불변식 3)를 닫았다.
남은 여섯은 성격이 같다 — **코드는 맞게 짜여 있고 아무도 그게 도는 걸 본 적이 없다.**

🔴 **PR-07 의 교훈을 잘못 적용하지 않는다.** 그때 문제는 *"우리 코드(`if redacted.uncertain`)를
대역이 흉내 냈다"* 였다. 여기서는 **재려는 분기가 우리 코드 안에 있고 방아쇠가 바깥 입력**
(깨진 yaml · 실패하는 저장소)이다 ⇒ **깨진 입력을 주입하는 것이 실제 경로다.**
분기 자체를 대역으로 흉내 내지만 않으면 된다 — 그 구분이 이 파일의 규율이다.

⚠ **`src/` 는 한 글자도 안 고친다**(PR-07 과 같은 모양).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final
from uuid import uuid4

import pytest

from ai.composition.counsel.refine import (
    RefineRulesError,
    load_refine_rules,
)
from ai.contracts.composition import PlanOutcome
from ai.contracts.counsel import REASON_LLM_FAILED, REASON_REDACTION_BLOCKED
from ai.contracts.execution import Capability, ExecutionContext, VersionSet
from ai.contracts.gates import BlockedReason

_VALID: Final = """
version: "0.1"
rules:
  - reason: comparison_exposure
    patterns: ["다른 학생"]
"""


@pytest.fixture(autouse=True)
def _clear_rules_cache() -> Iterator[None]:
    """🔴 `load_refine_rules` 는 `@lru_cache` 다 — **검사마다 비운다.**

    안 비우면 앞 검사가 읽은 **정상 값이 굳어** 주입이 아무 효과가 없다 — 그러면 아래
    네 검사가 **전부 거짓 green** 이다(PR-01 의 `get_counsel_settings` 때와 같은 함정).
    """
    load_refine_rules.cache_clear()
    yield
    load_refine_rules.cache_clear()


def _with_rules(
    monkeypatch: pytest.MonkeyPatch, text: str, tmp_path: Path
) -> None:
    """🔴 **저장소의 실물 yaml 을 안 건드린다** — 임시 파일로 경로만 갈아 끼운다."""
    from ai.composition.counsel import refine  # noqa: PLC0415

    path = tmp_path / "refine_rules.yaml"
    path.write_text(text, encoding="utf-8")
    monkeypatch.setattr(refine, "_RULES_PATH", path)
    load_refine_rules.cache_clear()


# ── A · refine_rules.yaml 검증 4종 (#103 ㉡ 1~4) ──────────────────


def test_a_non_mapping_rules_file_is_rejected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """ⓐ 최상위가 매핑이 아니면 거부한다."""
    _with_rules(monkeypatch, "- 리스트다\n- 매핑이 아니다\n", tmp_path)
    with pytest.raises(RefineRulesError, match="매핑이 아니다"):
        load_refine_rules()


@pytest.mark.parametrize(
    ("body", "label"),
    [('version: "0.1"\n', "rules 키 없음"), ('version: "0.1"\nrules: []\n', "rules 빈 리스트")],
    ids=["키_없음", "빈_리스트"],
)
def test_a_rules_file_without_rules_is_rejected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, body: str, label: str
) -> None:
    """ⓑ `rules` 가 없거나 비면 거부한다.

    ⚠ **빈 리스트도 같이 잰다** — `not rules` 가 두 경우를 한 줄로 처리하므로, 하나만
    재면 다른 하나가 사라져도 모른다.
    """
    del label
    _with_rules(monkeypatch, body, tmp_path)
    with pytest.raises(RefineRulesError, match="rules가 없다"):
        load_refine_rules()


def test_an_unknown_blocked_reason_is_rejected_with_its_value(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """ⓒ `BlockedReason` 밖의 사유는 거부하고 **그 값을 메시지에 싣는다.**

    🔴 **값을 안 실으면 운영에서 「어느 줄이 틀렸나」를 못 찾는다** — 파일에 규칙이 여럿이라
    *"어딘가 틀렸다"* 만으로는 고칠 수 없다.
    """
    _with_rules(
        monkeypatch,
        'version: "0.1"\nrules:\n  - reason: not_a_real_reason\n    patterns: ["x"]\n',
        tmp_path,
    )
    with pytest.raises(RefineRulesError, match="not_a_real_reason"):
        load_refine_rules()


@pytest.mark.parametrize(
    "patterns_line",
    ["", "    patterns: []\n"],
    ids=["patterns_없음", "patterns_빈_리스트"],
)
def test_a_rule_without_patterns_is_rejected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, patterns_line: str
) -> None:
    """ⓓ 패턴이 없거나 비면 거부한다 — 사유만 있고 규칙이 없는 항은 **아무것도 안 막는다.**"""
    _with_rules(
        monkeypatch,
        f'version: "0.1"\nrules:\n  - reason: comparison_exposure\n{patterns_line}',
        tmp_path,
    )
    with pytest.raises(RefineRulesError, match="패턴이 없다"):
        load_refine_rules()


def test_a_valid_rules_file_still_loads(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """🔴 **없으면 「전부 거부하는 로더」와 구분이 안 된다**(PR-07 의 검사 4와 같은 짝)."""
    _with_rules(monkeypatch, _VALID, tmp_path)
    rules = load_refine_rules()

    assert rules.version == "0.1"
    assert rules.entries == ((BlockedReason.COMPARISON_EXPOSURE, ("다른 학생",)),)


def test_the_real_rules_file_is_valid() -> None:
    """⚠ 저장소의 **실물** yaml 도 통과한다 — 임시 파일만 재면 실물이 깨져도 모른다."""
    rules = load_refine_rules()
    assert rules.entries, "실물 refine_rules.yaml 이 비었다"


# ── C · PlanOutcome ↔ fail_reason 값 정합 (#101) ─────────────────


def test_the_plan_outcome_values_match_the_wire_reasons() -> None:
    """🔴 **의도적 정합이다 — 「우연히 같네」가 아니다.**

    `graph.py` 의 plan 노드 주석이 적어 뒀다:
      *«값 문자열을 student 의 `fail_reason="redaction_blocked"` 와 **같게** 뒀다 —
        한 결함이 어느 노드에서 나느냐에 따라 다르게 기록되던 것을 맞춘 것이다»*(99 #05).

    **왜 같아야 하나:** 같은 사건(마스킹 fail-closed · LLM 장애)이 **plan 노드에서 나면
    `plan_outcome`** 으로, **student 노드에서 나면 `fail_reason`** 으로 기록된다. 두 축은
    서로 다른 자리에 저장되지만(팩 레코드 vs 와이어) **집계할 때는 같은 사건**이다 —
    값이 갈리면 *"이 결함이 얼마나 났나"* 를 셀 때 **두 이름을 같은 것으로 못 센다.**

    ⚠ 화면은 안 깨진다(축이 달라 `plan_outcome` 은 와이어에 안 실린다) — 깨지는 것은
    **관측**이다. 그래서 이 검사가 없으면 갈려도 **조용하다.**
    """
    assert PlanOutcome.REDACTION_BLOCKED.value == REASON_REDACTION_BLOCKED
    assert PlanOutcome.LLM_FAILED.value == REASON_LLM_FAILED


def test_the_plan_only_outcomes_have_no_wire_counterpart() -> None:
    """⚠ **정합은 두 값뿐이다** — 나머지 셋은 plan 전용이라 짝이 없다.

    실측(8/19): `PlanOutcome` 5종 중 `ok`·`unparsed`·`all_dropped` 는 대응하는
    `REASON_*` 가 **없다**. 🔴 이것을 안 적으면 다음 사람이 *"셋도 맞춰야 하나"* 를
    되묻거나, 없는 상수를 만들어 어휘를 늘린다(`WIRE_STATUS_REASONS` 는 13으로 고정이다).

    `ok` 는 사유의 **부재**이고, `unparsed`·`all_dropped` 는 **plan 고유의 실패 모양**이라
    student 축에 대응물이 없는 것이 맞다.
    """
    import ai.contracts.counsel as wire  # noqa: PLC0415

    reason_values = {
        value
        for name, value in vars(wire).items()
        if name.startswith("REASON_") and isinstance(value, str)
    }
    plan_only = {
        PlanOutcome.OK.value,
        PlanOutcome.UNPARSED.value,
        PlanOutcome.ALL_DROPPED.value,
    }

    assert not (plan_only & reason_values), (
        f"plan 전용 값에 와이어 대응물이 생겼다: {plan_only & reason_values} — "
        "어휘를 늘렸다면 error_codes.md §2.1 도 같이 움직여야 한다"
    )
    #: 절단 가드 — 상수를 하나도 못 읽었으면 위 단언이 공허하다.
    assert REASON_REDACTION_BLOCKED in reason_values


# ── B · swallow_errors 규율 (#103 ㉡ 5) ───────────────────────────
#
# 🔴 **분기는 우리 코드 안에 있고 방아쇠는 저장소다** — 저장소가 터지게 만드는 것이
# **실제 경로**이지 분기를 흉내 내는 것이 아니다(위 모듈 docstring 의 규율).


class _ExplodingRunStore:
    """`finalize_run` 만 터뜨리는 얇은 대역 — 나머지는 실물에 위임한다.

    ⚠ **저장소를 통째로 갈지 않는다** — `begin_run` 등이 실물이어야 그 앞 단계가
    실제로 지나간다.
    """

    def __init__(self, exc: Exception) -> None:
        from ai.db.repositories.run_store import InMemoryRunStore  # noqa: PLC0415

        self._inner = InMemoryRunStore()
        self._exc = exc
        self.finalize_calls = 0

    def __getattr__(self, name: str) -> object:
        return getattr(self._inner, name)

    async def finalize_run(self, *args: object, **kwargs: object) -> None:
        del args, kwargs
        self.finalize_calls += 1
        raise self._exc


def _runner_with(store: object) -> Any:  # noqa: ANN401
    """`_record_execution` 만 부르기 위한 최소 러너 — 조립부를 안 지난다.

    ⚠ 이 절이 재는 것은 **그 메서드 안의 분기**다. 잡 실행 전체를 돌리면 그 분기까지
    가는 경로가 길어져 **무엇 때문에 red 인지**가 흐려진다.
    """
    from ai.composition.counsel.worker import CounselPackRunner  # noqa: PLC0415

    runner = CounselPackRunner.__new__(CounselPackRunner)
    #: ⚠ 타입 무시는 **대역을 꽂는 자리에만** 붙인다 — 재려는 분기(`_record_execution`
    #:   안의 `if not swallow_errors`)는 **실물 그대로** 돈다.
    runner._runs = store  # type: ignore[assignment]  # noqa: SLF001
    runner._call_log = _EmptyCallLog()  # type: ignore[assignment]  # noqa: SLF001
    runner._now = lambda: datetime.now(UTC)  # noqa: SLF001
    return runner


class _EmptyCallLog:
    def take(self, execution_id: object) -> tuple[()]:
        del execution_id
        return ()


def _context() -> ExecutionContext:
    return ExecutionContext(
        execution_id=uuid4(),
        tenant_id="t_swallow",
        capability=Capability.COMPOSITION,
        input_snapshot_hash="sha256:" + "a" * 64,
        versions=VersionSet(
            pipeline_version="0.1",
            engine_version="counsel-pack-0.1",
            schema_version="0.1",
            contract_version="0.1",
        ),
    )


def test_a_ledger_failure_rises_when_errors_are_not_swallowed() -> None:
    """B-1 · `swallow_errors=False`(성공 경로)면 적재 실패가 **올라간다.**

    ⚠ 성공 경로는 삼키지 않는다 — 원장이 비면 재현성(불변식 8)이 조용히 깨지므로
    잡을 실패로 떨구는 쪽이 정직하다.
    """
    store = _ExplodingRunStore(RuntimeError("원장 쓰기 실패"))
    runner = _runner_with(store)

    with pytest.raises(RuntimeError, match="원장 쓰기 실패"):
        asyncio.run(runner._record_execution(_context(), swallow_errors=False))  # noqa: SLF001
    assert store.finalize_calls == 1


def test_a_ledger_failure_is_swallowed_on_the_failure_path(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """B-2 · `swallow_errors=True`(실패 경로)면 삼키고 **로그가 남는다.**

    🔴 조용히 삼키지 않는다 — 적재 실패가 원인 예외를 덮으면 *"서킷이 열렸다"* 가
    *"워커가 알 수 없는 이유로 죽었다"* 로 바뀐다(그 메서드 docstring).
    """
    store = _ExplodingRunStore(RuntimeError("원장 쓰기 실패"))
    runner = _runner_with(store)

    with caplog.at_level(logging.ERROR):
        asyncio.run(runner._record_execution(_context(), swallow_errors=True))  # noqa: SLF001

    assert store.finalize_calls == 1
    assert any("실패 경로의 실행 원장 적재 실패" in r.message for r in caplog.records), (
        "삼켰는데 로그가 없다 — `except: pass` 와 구분이 안 된다"
    )


def test_an_identity_conflict_is_never_swallowed() -> None:
    """🔴 **B-3 · 이 절의 값** — `swallow_errors=True` 여도 **의미 충돌은 올라간다.**

    그 자리 주석이 판정해 뒀다: *«그 플래그는 「일반 원장 장애가 원인 예외를 교체하지
    않게」 두는 장치이지 **「다른 실행이 같은 원장 행을 쓰려 한다」를 숨기는 장치가
    아니다»*. 삼키면 **재현성(불변식 8)이 조용히 무너진 채 잡만 실패로 수렴한다.**

    ⚠ B-1·B-2 만 재면 *"플래그가 동작한다"* 까지다 — **「플래그가 무엇을 삼키면 안
    되는가」** 는 안 재는 것이고, 그게 이 절이 있는 이유다.
    """
    from ai.db.repositories.run_store import RunIdentityConflict  # noqa: PLC0415

    store = _ExplodingRunStore(RunIdentityConflict("같은 실행 행에 다른 값"))
    runner = _runner_with(store)

    with pytest.raises(RunIdentityConflict):
        asyncio.run(runner._record_execution(_context(), swallow_errors=True))  # noqa: SLF001
    assert store.finalize_calls == 1
