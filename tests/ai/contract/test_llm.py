"""contracts/llm.py 스모크 — enum 값 고정 · 왕복 직렬화 · Protocol 구현 가능성.

FakeProvider(B 소유, llm/providers/)가 이 Protocol을 구현할 수 있는 모양인지를
여기서 미리 확인한다 — 계약이 구현 불가능한 채로 머지되는 것을 막는다.
"""

import asyncio
from pathlib import Path, PurePath, PureWindowsPath
from uuid import UUID

from ai.contracts.execution import Capability, ExecutionContext, VersionSet
from ai.contracts.llm import (
    CallOutcome,
    FieldMissing,
    LlmError,
    LLMProvider,
    LLMRequest,
    LLMResult,
    LlmTimeout,
    LlmUnavailable,
    ModelRole,
    ParseFailed,
    RedactionBlocked,
    TokenUsage,
)


def _request() -> LLMRequest:
    return LLMRequest(
        role=ModelRole.GENERATOR,
        prompt="[마스킹 통과 프롬프트]",
        prompt_id="composition/reply",
        prompt_version="v0.1",
    )


def _result() -> LLMResult:
    return LLMResult(
        outcome=CallOutcome.OK,
        text="초안 문장",
        provider="fake",
        model="fake-1",
        usage=TokenUsage(tokens_in=10, tokens_out=20, cost_usd=0.0),
        latency_ms=5,
    )


class _StubProvider:
    """Protocol 구현 가능성 확인용 — 실제 FakeProvider는 B가 llm/providers/에 만든다."""

    @property
    def name(self) -> str:
        return "stub"

    async def complete(self, request: LLMRequest, context: ExecutionContext) -> LLMResult:
        return _result()


def test_model_role_values_frozen() -> None:
    """ERD LLM_CALL.role — v2 classifier · v2.1 narrator · v2.2 counselor 편입분 포함."""
    assert {role.value for role in ModelRole} == {
        "generator",
        "verifier",
        "mapper",
        "classifier",
        "narrator",
        "counselor",
    }


def test_call_outcome_values_frozen() -> None:
    """error_codes.md §3 — LLM_CALL.outcome."""
    assert {outcome.value for outcome in CallOutcome} == {
        "ok",
        "parse_fail",
        "field_missing",
        "bad_ref",
        "timeout",
        "provider_error",
        "redaction_blocked",
    }


def test_llm_request_roundtrip() -> None:
    request = _request()
    assert LLMRequest.model_validate(request.model_dump(mode="json")) == request


def test_llm_result_roundtrip() -> None:
    result = _result()
    assert LLMResult.model_validate(result.model_dump(mode="json")) == result


def test_failed_result_has_no_text() -> None:
    """OK가 아니면 text는 없을 수 있다 — 빈 문자열로 덮지 않는다."""
    result = LLMResult(
        outcome=CallOutcome.TIMEOUT,
        provider="fake",
        model="fake-1",
        usage=TokenUsage(tokens_in=10, tokens_out=0, cost_usd=0.0),
        latency_ms=10_000,
    )
    assert result.text is None
    assert LLMResult.model_validate(result.model_dump(mode="json")) == result


def test_stub_satisfies_provider_protocol() -> None:
    assert isinstance(_StubProvider(), LLMProvider)


def test_provider_complete_returns_result() -> None:
    context = ExecutionContext(
        execution_id=UUID("00000000-0000-4000-8000-000000000002"),
        tenant_id="teacher_alias_001",
        capability=Capability.COMPOSITION,
        input_snapshot_hash="sha256:abc",
        versions=VersionSet(
            pipeline_version="v2.1",
            engine_version="rules-1.0",
            schema_version="0.1",
            contract_version="0.1",
            prompt_version="v0.1",
        ),
    )
    # pytest-asyncio를 추가하지 않기 위해 표준 asyncio로 구동한다 (03_coding_rules.md §1b)
    result = asyncio.run(_StubProvider().complete(_request(), context))
    assert result.outcome is CallOutcome.OK


def test_exception_hierarchy() -> None:
    """runtime/errors.py가 이 계층을 받아 HTTP로 매핑한다 (error_codes.md §4)."""
    assert issubclass(LlmUnavailable, LlmError)
    assert issubclass(LlmTimeout, LlmError)
    assert issubclass(ParseFailed, LlmError)
    assert issubclass(RedactionBlocked, LlmError)
    # field_missing은 parse_fail과 같은 재시도 정책(≤3회)을 따른다 — §3
    assert issubclass(FieldMissing, ParseFailed)


def test_no_vendor_sdk_imported() -> None:
    """B-5 확정 전 벤더 SDK 설치·import 금지 (CLAUDE.md §3).

    모듈 소스에 벤더 이름이 등장하면 계약이 벤더에 오염된 것이다.
    """
    import inspect

    import ai.contracts.llm as llm_module

    source = inspect.getsource(llm_module).lower()
    for vendor in ("openai", "anthropic", "google.generativeai", "cohere", "mistralai"):
        assert vendor not in source, f"벤더 SDK 흔적: {vendor}"


# ── 결정론 파라미터 정본 단일성 (99 ⓨ 후속) ──────────────────────


def test_determinism_reexport_is_the_same_object() -> None:
    """composition은 llm의 **재수출**이다 — 값이 아니라 **동일성**으로 고정한다.

    🔴 `==`를 쓰지 마라. 값 비교는 *"각자 정의했는데 마침 같다"* 를 통과시킨다 — 그게
    8/5~8/6의 상태였고 우리가 없앤 것이다. 두 모듈이 상수 2개와 함수 1개를 각자 정의했고,
    값이 우연히 같아서 **아무 테스트도 안 깨졌다.** 한쪽만 고치면 브리핑·초안·분류와
    문항 생성의 재현 조건이 조용히 갈린다(불변식 8).

    ⚠ **함수 객체 `is`가 본체이고 상수 `is`는 보조다** — 다음 사람이 이 int `is`를
    버그로 보고 `==`로 바꾸지 않도록 적어 둔다.
    ⚠ **실측(8/6):** 병존을 일부러 되살려 보니 상수 `is`도 red가 났다
    (`assert 20260805 is 20260805` 실패) — 20260805가 CPython 소형 정수 캐시(-5~256) 밖이라
    두 모듈의 리터럴이 **다른 객체**이기 때문이다. 다만 그건 **구현 세부이지 보장이 아니다**
    (상수 폴딩·캐시 정책이 바뀌면 우연히 같아질 수 있다). 그래서 함수 `is`를 먼저 둔다.
    """
    import ai.composition.determinism as composition
    import ai.llm.determinism as canonical

    assert composition.deterministic_params is canonical.deterministic_params
    assert composition.LLM_SEED is canonical.LLM_SEED
    assert composition.DETERMINISTIC_TEMPERATURE is canonical.DETERMINISTIC_TEMPERATURE


def _posix_rel(path: PurePath, base: PurePath) -> str:
    """base 기준 상대경로를 항상 `/` 표기로 — 비교 키.

    선례: `test_composition_redaction._posix_rel`. `str(Path)`는 Windows에서 백슬래시를
    내서 `/` 표기 기대값과 안 맞는다 — CI가 win32라 실제로 빨갰다(8/7).
    🔴 **가드와 회귀 단정이 이 함수를 함께 쓴다** — 인라인으로 두면 되돌림을 잡는 테스트를
    쓸 수 없다(순수 경로 연산만 남아 헛돈다).
    """
    return path.relative_to(base).as_posix()


def test_determinism_is_defined_in_exactly_one_file() -> None:
    """재현 키의 **정의**가 한 파일에만 있다 — 병존이 다시 생기면 red.

    🔴 **정규식 주의(8/6 실측).** `LLM_SEED\\s*=`는 **0건**을 낸다 — 실제 코드가
    `LLM_SEED: Final = 20260805`라 `\\s*=`가 `: Final =`을 못 잡는다. 그 패턴으로
    `<= 1`을 단언하면 **조용히 통과**한다 — ⓒ·ⓛ·㉯가 세 번 겪은 "게이트가 통과하는 동안
    샜다"와 같은 형태다.

    앵커 `^`가 핵심이다 — 들여쓴 참조(`c.LLM_SEED`)와 docstring 안 언급, 재수출 파일의
    `__all__` 문자열을 전부 걸러 준다.

    🔴 **0건이면 통과가 아니라 실패다**(검사 경로 절단 검출 · `test_scan_finds_*` 선례).

    ⚠ **경로는 `_posix_rel`로 정규화한다** — `str(Path)`는 Windows에서 백슬래시를 내서
    기대값과 안 맞는다. CI가 win32라 실제로 빨갰다(8/7). 레포에 같은 선례가 이미 있었고
    (`test_composition_redaction`), **그걸 안 쓴 게 이번 실수다.**
    """
    import re

    src = Path(__file__).resolve().parents[3] / "src" / "ai"
    pattern = re.compile(r"^(LLM_SEED|DETERMINISTIC_TEMPERATURE)\b", re.MULTILINE)
    defining = sorted(
        _posix_rel(path, src.parent)
        for path in src.rglob("*.py")
        if "__pycache__" not in path.parts and pattern.search(path.read_text("utf-8"))
    )
    assert defining == ["ai/llm/determinism.py"], (
        f"재현 키 정의가 1파일이 아니다: {defining} — 0건이면 검사 경로가 끊긴 것이고, "
        "2건 이상이면 병존이 되살아난 것이다(둘 다 red)"
    )


def test_determinism_guard_key_is_posix_on_windows() -> None:
    """Windows 시맨틱을 맥에서 재현 — 🔴 **가드가 쓰는 `_posix_rel`을 직접 부른다.**

    선례: `test_composition_redaction.test_posix_rel_normalizes_windows_separator`.
    ⚠ 첫 판에서 `PureWindowsPath` **산술만** 단정했다가 헛돌았다 — 가드가 `.as_posix()`를
    잃어도 그 단정은 통과한다. 헬퍼를 빼서 **같은 함수**를 부르게 고쳤다.
    ⚠ 두 인자를 **모두** `PureWindowsPath`로 준다(flavour 혼합은 파이썬 버전마다 갈린다).
    """
    base = PureWindowsPath("C:/repo/src")
    target = base / "ai" / "llm" / "determinism.py"

    assert _posix_rel(target, base) == "ai/llm/determinism.py"
    assert "\\" not in _posix_rel(target, base)
    # `str()`이면 이 값이 나온다 — 기대값(`/` 표기)과 불일치하는 그 값.
    assert str(target.relative_to(base)) == "ai\\llm\\determinism.py"
