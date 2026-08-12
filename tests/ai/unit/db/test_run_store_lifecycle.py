"""실행 원장의 **두 단계 생애주기** — begin → finalize (99 #46).

🔴 **`record_run()`은 INSERT 전용이다.** `DRAFT.run_id → AI_RUN.execution_id` FK 때문에
상담 초안을 저장하려면 AI_RUN이 **먼저** 서야 하는데, 지금 그 행은 그래프가 끝난 뒤
`finally`에서 만들어진다(지시서 71 §2 관문). 선등록하고 나중에 다시 부르면
**`pk_ai_run` 충돌 → fail-open 삼킴 → 최종 사용 축 유실**이다(실 PG 실측).

이 파일은 **의미**를 고정한다 — 실 PG 왕복과 재개 3구간은 통합 검사가 든다.

**세 축을 성질로 가른다** — 어느 쪽에도 안 들어간 필드가 생기면 red다:

| 성질 | 필드 | 규칙 |
| --- | --- | --- |
| **확정 축** | `execution_id`·`tenant_id`·`capability`·`input_snapshot_hash`
  + 버전 10 | 하나라도 다르면 **충돌 예외** |
| **사용 축** | `model_provider`·`model_name`·`generation_params`
  | 마지막 **성공** 호출로 갱신 · **값→None 금지** |
| **최초 쓰기 축** | `created_at` | 최초 `begin_run` 값 유지 |

⚠ 이 저장소는 async 플러그인 없이 **`asyncio.run()` 관례**를 쓴다(레포 전반 동일).
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Coroutine, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any, Final

import pytest

from ai.contracts.execution import (
    Capability,
    ExecutionContext,
    GenerationParams,
    RunMetadata,
    VersionSet,
)
from ai.contracts.llm import CallOutcome, ModelRole
from ai.db.repositories.run_store import (
    CONFIRMED_AXES,
    CREATED_AXIS,
    USAGE_AXES,
    CollectedCall,
    InMemoryRunStore,
    RunIdentityConflict,
)
from ai.llm.gateway import LlmCallRecord

_EXEC: Final = uuid.UUID(int=0x46)
_TENANT: Final = "t_ledger"
_T0: Final = datetime(2026, 8, 12, 9, 0, tzinfo=UTC)


def _run[T](coro: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(coro)


def _versions() -> VersionSet:
    return VersionSet(
        pipeline_version="0.1.0",
        engine_version="counsel-pack-0.1",
        schema_version="0.1",
        contract_version="0.1",
    )


def _meta(
    *,
    provider: str | None = None,
    model: str | None = None,
    params: GenerationParams | None = None,
    created_at: datetime = _T0,
    tenant: str = _TENANT,
    snapshot: str = "sha256:aaa",
) -> RunMetadata:
    return ExecutionContext(
        execution_id=_EXEC,
        tenant_id=tenant,
        capability=Capability.COMPOSITION,
        input_snapshot_hash=snapshot,
        versions=_versions(),
    ).to_run_metadata(
        created_at=created_at,
        model_provider=provider,
        model_name=model,
        generation_params=params,
    )


def _call(
    *, outcome: CallOutcome = CallOutcome.OK, provider: str = "A", model: str = "A1"
) -> CollectedCall:
    return CollectedCall(
        id=uuid.uuid4(),
        record=LlmCallRecord(
            role=ModelRole.GENERATOR,
            provider=provider,
            model=model,
            prompt_id="p",
            prompt_version="0.1",
            usage=None,
            latency_ms=10,
            outcome=outcome,
        ),
    )


def _started(**over: Any) -> InMemoryRunStore:  # noqa: ANN401 — `_meta` 오버라이드 전달
    store = InMemoryRunStore()
    _run(store.begin_run(_meta(**over)))
    return store


# ───────────────────────── 축 분류 절단 가드 ─────────────────────────


def test_every_run_metadata_field_is_classified() -> None:
    """🔴 **분류 안 된 필드가 생기면 red다** — 새 축이 조용히 어느 규칙도 안 받는다.

    ⚠ 문자열 grep이 아니라 `RunMetadata.model_fields`와 **정확 대조**한다.
    """
    classified = set(CONFIRMED_AXES) | set(USAGE_AXES) | {CREATED_AXIS}
    fields = set(RunMetadata.model_fields)
    assert classified == fields, (
        f"미분류={sorted(fields - classified)} · 없는 필드={sorted(classified - fields)}"
    )


def test_the_three_axes_do_not_overlap() -> None:
    assert not (set(CONFIRMED_AXES) & set(USAGE_AXES))
    assert CREATED_AXIS not in CONFIRMED_AXES
    assert CREATED_AXIS not in USAGE_AXES


# ───────────────────────── begin_run ─────────────────────────


def test_begin_creates_the_row_with_empty_usage() -> None:
    row = _started().runs[_EXEC]
    assert row.model_provider is None
    assert row.model_name is None
    assert row.generation_params is None
    assert row.created_at == _T0


def test_begin_refuses_a_prefilled_usage_axis() -> None:
    """🔴 **begin 단계에서 사용 결과를 지어내지 않는다** — 아직 안 불렀다."""
    store = InMemoryRunStore()
    with pytest.raises(ValueError, match="사용 축"):
        _run(store.begin_run(_meta(provider="A")))
    assert not store.runs, "거부했는데 행이 생겼다"


def test_begin_is_idempotent() -> None:
    """재개가 같은 `execution_id`로 다시 시작해도 **같은 결과**여야 한다."""
    store = _started()
    _run(store.begin_run(_meta(created_at=_T0 + timedelta(minutes=5))))
    assert len(store.runs) == 1
    assert store.runs[_EXEC].created_at == _T0, "재개가 최초 시작 시각을 덮었다"


@pytest.mark.parametrize(
    ("field", "over"),
    [
        ("tenant_id", {"tenant": "t_other"}),
        ("input_snapshot_hash", {"snapshot": "sha256:bbb"}),
    ],
)
def test_begin_rejects_a_changed_confirmed_axis(field: str, over: dict[str, str]) -> None:
    """🔴 **확정 축이 달라지면 충돌**이다 — 같은 실행이 아니다.

    ⚠ 이건 SQL 장애가 아니라 **의미 충돌**이라 fail-open으로 삼키면 안 된다.
    """
    store = _started()
    with pytest.raises(RunIdentityConflict, match=field):
        _run(store.begin_run(_meta(**over)))  # type: ignore[arg-type]


def test_a_changed_version_is_also_a_conflict() -> None:
    store = _started()
    changed = _meta().model_copy(update={"engine_version": "counsel-pack-0.2"})
    with pytest.raises(RunIdentityConflict, match="engine_version"):
        _run(store.begin_run(changed))


# ───────────────────────── finalize_run ─────────────────────────


def test_finalize_fills_the_usage_axes() -> None:
    store = _started()
    _run(store.finalize_run(_meta(provider="A", model="A1"), [_call()]))
    row = store.runs[_EXEC]
    assert (row.model_provider, row.model_name) == ("A", "A1")
    assert len(store.calls_of(_EXEC)) == 1


def test_finalize_never_clears_a_filled_usage_axis() -> None:
    """🔴 **값 → None은 갱신이 아니다** — 2차 실패 구간이 1차 성공을 지우면 안 된다."""
    store = _started()
    _run(store.finalize_run(_meta(provider="A", model="A1"), [_call()]))
    _run(store.finalize_run(_meta(), [_call(outcome=CallOutcome.PROVIDER_ERROR)]))
    row = store.runs[_EXEC]
    assert (row.model_provider, row.model_name) == ("A", "A1"), "실패 구간이 사용 축을 지웠다"


def test_finalize_updates_to_the_newer_success() -> None:
    store = _started()
    _run(store.finalize_run(_meta(provider="A", model="A1"), [_call()]))
    _run(
        store.finalize_run(
            _meta(provider="B", model="B1"), [_call(provider="B", model="B1")]
        )
    )
    row = store.runs[_EXEC]
    assert (row.model_provider, row.model_name) == ("B", "B1")


def test_finalize_keeps_the_first_created_at() -> None:
    store = _started()
    _run(
        store.finalize_run(
            _meta(provider="A", model="A1", created_at=_T0 + timedelta(hours=1)), []
        )
    )
    assert store.runs[_EXEC].created_at == _T0, "최종화 시각이 시작 시각을 덮었다"


def test_finalize_rejects_a_changed_confirmed_axis() -> None:
    store = _started()
    with pytest.raises(RunIdentityConflict):
        _run(store.finalize_run(_meta(tenant="t_other", provider="A"), []))


def test_finalize_without_begin_is_counted_not_raised() -> None:
    """🔴 **begin 전제가 깨진 상태** — 호출자 요청을 새 오류로 뒤집지 않고 **센다**."""
    store = InMemoryRunStore()
    _run(store.finalize_run(_meta(provider="A"), []))
    assert store.runs == {}, "없는 실행을 지어냈다"
    assert store.swallowed_failures["finalize_run"] == 1


# ───────────────────────── LLM_CALL 멱등 ─────────────────────────


def test_a_repeated_call_id_is_not_duplicated() -> None:
    """재개가 이전 호출을 다시 넘겨도 **행이 늘지 않는다.**"""
    store = _started()
    call = _call()
    _run(store.finalize_run(_meta(provider="A", model="A1"), [call]))
    _run(store.finalize_run(_meta(provider="A", model="A1"), [call]))
    assert len(store.calls_of(_EXEC)) == 1


def test_a_repeated_call_id_with_different_content_conflicts() -> None:
    """🔴 같은 id에 **다른 전문**이면 의미 충돌이다 — 조용히 무시하지 않는다."""
    store = _started()
    call = _call(provider="A")
    twisted = CollectedCall(
        id=call.id, record=call.record.model_copy(update={"provider": "B"})
    )
    _run(store.finalize_run(_meta(provider="A", model="A1"), [call]))
    with pytest.raises(RunIdentityConflict):
        _run(store.finalize_run(_meta(provider="A", model="A1"), [twisted]))


def test_a_duplicate_does_not_drop_the_new_calls_beside_it() -> None:
    """🔴 중복 하나 때문에 **같은 finalize의 새 호출까지** 유실되면 안 된다."""
    store = _started()
    first = _call()
    _run(store.finalize_run(_meta(provider="A", model="A1"), [first]))
    fresh = _call(provider="B", model="B1")
    _run(store.finalize_run(_meta(provider="B", model="B1"), [first, fresh]))
    assert len(store.calls_of(_EXEC)) == 2


# ───────────────────────── record_run 원자성 ─────────────────────────


def test_record_run_still_writes_in_one_shot() -> None:
    """기존 one-shot 소비자는 **코드 변경 없이** 그대로다."""
    store = InMemoryRunStore()
    _run(store.record_run(_meta(provider="A", model="A1"), [_call()]))
    assert store.runs[_EXEC].model_provider == "A"
    assert len(store.calls_of(_EXEC)) == 1


def test_record_run_does_not_delegate_to_the_public_two_phase() -> None:
    """🔴 **`record_run`을 begin+finalize 연속 호출로 만들면 원자성이 사라진다.**

    ⚠ 기존 소비자는 「AI_RUN과 LLM_CALL이 한 트랜잭션」을 전제한다 — 두 호출로 쪼개면
    호출 저장이 실패해도 AI_RUN만 남는다. **AST로 본다**(주석·docstring 언급은 무시).
    """
    import ast  # noqa: PLC0415
    import inspect  # noqa: PLC0415

    from ai.db.repositories import run_store as module  # noqa: PLC0415

    tree = ast.parse(inspect.getsource(module))
    checked = 0
    for cls in (node for node in ast.walk(tree) if isinstance(node, ast.ClassDef)):
        if cls.name not in {"InMemoryRunStore", "PgRunStore"}:
            continue
        for fn in (n for n in cls.body if isinstance(n, ast.AsyncFunctionDef)):
            if fn.name != "record_run":
                continue
            checked += 1
            called = {
                inner.func.attr
                for inner in ast.walk(fn)
                if isinstance(inner, ast.Call) and isinstance(inner.func, ast.Attribute)
            }
            assert not called & {"begin_run", "finalize_run"}, (
                f"{cls.name}.record_run이 공개 2단계를 호출한다 — 원자성이 깨진다"
            )
    assert checked == 2, f"record_run을 {checked}개만 봤다 — 구현 둘 다 봐야 한다"


# ───────────────────────── 삼킨 실패 카운터 ─────────────────────────


def test_the_failure_counter_keys_are_closed() -> None:
    """🔴 임의 문자열 키 금지 — 세 operation만 센다."""
    store = InMemoryRunStore()
    assert set(store.swallowed_failures) == {
        "record_run",
        "finalize_run",
        "record_calls",
    }


def test_a_healthy_run_counts_no_failure() -> None:
    store = _started()
    _run(store.finalize_run(_meta(provider="A", model="A1"), [_call()]))
    assert set(store.swallowed_failures.values()) == {0}


def test_an_identity_conflict_is_not_a_swallowed_failure() -> None:
    """🔴 **의미 충돌을 삼킨 실패로 위장하지 않는다** — 그건 SQL 장애가 아니다."""
    store = _started()
    with pytest.raises(RunIdentityConflict):
        _run(store.finalize_run(_meta(tenant="t_other", provider="A"), []))
    assert set(store.swallowed_failures.values()) == {0}


# ───────────────────────── 마지막 「성공」 호출 ─────────────────────────


def test_the_usage_axis_comes_from_the_last_successful_call() -> None:
    """🔴 배열의 **마지막 행**이 아니라 마지막 **성공** 호출이다.

    ⚠ 실패·timeout이 배열 끝이라고 그 provider를 최종본으로 적으면 **거짓**이다.
    ⚠ 선택 규칙을 두 벌로 만들지 않는다 — 기존 `last_success_id()`와 같은 함수를 쓴다.
    """
    from ai.db.repositories.run_store import (  # noqa: PLC0415
        last_success_call,
        last_success_id,
    )

    ok = _call(provider="A", model="A1")
    failed = _call(outcome=CallOutcome.PROVIDER_ERROR, provider="Z", model="Z9")
    calls: Sequence[CollectedCall] = [ok, failed]
    chosen = last_success_call(calls)
    assert chosen is not None
    assert (chosen.record.provider, chosen.record.model) == ("A", "A1")
    #: 같은 선택을 두 함수가 공유한다.
    assert last_success_id(calls) == chosen.id
