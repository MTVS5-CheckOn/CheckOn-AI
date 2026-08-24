"""trace_masking_hook 누락 방지 + 훅 동작 — 09 §2-16 P1′.

**왜 정적으로 막는가:** B는 **런타임 기동 가드**(`llm/gateway.py:121` —
`LANGSMITH_TRACING=true`이고 훅이 없으면 ValueError)로 막았다. 그런데 `.env` 정본이
`LANGSMITH_TRACING=false`라 **평소엔 절대 안 터진다** — 세 번째 조립부가 훅 없이
추가돼도 조용히 통과한다. 그래서 A 쪽에서 AST로 정적 단정한다.

**검사 경로가 끊기면 실패시킨다** — `LlmGateway(...)` 생성 호출을 하나도 못 찾으면 통과가
아니라 실패다. 조용한 통과가 7/30 트레이스 사고의 유형이다.
선례: `test_composition_redaction.py` · `test_evidence_blind_isolation.py`.
"""

from __future__ import annotations

import ast
from pathlib import Path, PurePath
from uuid import UUID

import pytest

from ai.composition.counsel.prompt import assemble_prompt
from ai.composition.provider import FakeBriefProvider, build_brief_gateway
from ai.contracts.composition import (
    CommStyle,
    DraftContext,
    EvidenceFact,
    Frequency,
    Interest,
    LabelSnapshot,
    Sensitivity,
)
from ai.contracts.detection import DISPLAY_LABELS, SignalType
from ai.contracts.execution import Capability, ExecutionContext, VersionSet
from ai.contracts.llm import LLMRequest, ModelRole, RedactionBlocked
from ai.llm.settings import LlmSettings
from ai.runtime.trace_masking import RedactionTripwireTraceHook
from ai.runtime.tracing import TRACING_ENV_SYNONYMS

_SRC = Path(__file__).resolve().parents[3] / "src" / "ai"

#: 추적 제어의 **대표 env** — B가 자기 테스트에서 쓴 방식 그대로다
#: (`tests/ai/unit/llm/test_gateway.py`의 `_REPRESENTATIVE_TRACING_ENV`).
#: 목록을 복제하지 않고 `runtime/tracing.py`의 정본에서 가져온다.
_REPRESENTATIVE_TRACING_ENV = TRACING_ENV_SYNONYMS[0]


@pytest.fixture(autouse=True)
def _isolate_tracing_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """추적 env를 비운 상태에서 시작한다 — 개발자 로컬 `.env`·셸 설정과 무관하게.

    B 소유 `tests/ai/unit/llm/conftest.py`의 격리와 같은 규약이되, 그 파일은 B 소유라
    건드리지 않고 여기에 둔다. **목록은 복제하지 않는다**(`TRACING_ENV_SYNONYMS` 재사용).
    """
    for name in TRACING_ENV_SYNONYMS:
        monkeypatch.delenv(name, raising=False)

#: gateway 생성이 허용된 조립부(화이트리스트). 목록 밖에서 생기면 사람이 한 번 본다.
#: 경로는 **POSIX 표기**로 대조한다 — `str()`이면 Windows에서만 깨진다(⑬에서 고친 버그).
_ALLOWED_GATEWAY_BUILDERS: frozenset[str] = frozenset(
    {
        "composition/provider.py",
        "composition/counsel/assembly.py",
        "composition/classify/provider.py",  # 문의 분류(ⓑ) — role=classifier(8/6 정정 · 99 ㊿)
        "problem_generation/provider.py",
        #: 라벨 제안(99 #190·#191) — role=counselor. 🔴 **이력 본문이 프롬프트에
        #: 실린다**(BE 1차 마스킹 통과본이지만 우리 문지기를 다시 지난다 · #104 축)
        #: ⇒ 훅이 **마지막 관문**이라 조립부가 반드시 주입한다.
        "composition/labels/provider.py",
        #: 리포트 문장화(§2-29 ⓒ · 2026-08-25) — role=reporter(#410). guardian으로
        #: 필터한 학부모 지표·여섯 데이터 블록·미산출 축이 프롬프트에 실린다
        #: (`teacher_only` 지표는 물리적으로 제외). ⇒ 훅이 마지막 관문이라 반드시 주입한다.
        "report/provider.py",
    }
)

_HOOK_KEYWORD = "trace_masking_hook"


def _posix_rel(path: PurePath, base: PurePath) -> str:
    """base 기준 상대경로를 항상 `/` 표기로 — 화이트리스트 대조 키."""
    return path.relative_to(base).as_posix()


def _iter_python_files() -> list[Path]:
    return sorted(p for p in _SRC.rglob("*.py") if "__pycache__" not in p.parts)


def _gateway_constructions() -> list[tuple[str, int, bool]]:
    """(모듈, 줄, 훅 키워드 전달 여부) — `LlmGateway(...)` 생성 호출 전수."""
    found: list[tuple[str, int, bool]] = []
    for path in _iter_python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
            if name != "LlmGateway":
                continue
            passes_hook = any(kw.arg == _HOOK_KEYWORD for kw in node.keywords)
            found.append((_posix_rel(path, _SRC), node.lineno, passes_hook))
    return found


# ── 검사 경로 무결성 (조용한 통과 금지) ────────────────────────────


def test_scan_target_exists() -> None:
    assert _iter_python_files(), "src/ai에서 .py를 하나도 찾지 못했다 — 검사 경로가 끊겼다"


def test_at_least_one_gateway_construction_found() -> None:
    """생성 호출을 하나도 못 찾으면 통과가 아니라 실패다(호출 방식이 바뀌었는지 확인)."""
    assert _gateway_constructions(), (
        "src/ai에서 LlmGateway(...) 생성을 하나도 찾지 못했다 — 검사 경로가 끊겼다"
    )


def test_whitelist_entries_all_exist() -> None:
    """화이트리스트에 죽은 항목이 없다 — 삭제된 조립부를 남기면 검사가 헐거워진다."""
    modules = {module for module, _line, _hook in _gateway_constructions()}
    stale = sorted(_ALLOWED_GATEWAY_BUILDERS - modules)
    assert not stale, f"화이트리스트에 실재하지 않는 조립부가 있다: {stale}"


# ── ① 전 조립부가 훅을 주입한다 ────────────────────────────────────


def test_every_gateway_construction_passes_hook() -> None:
    """세 번째 조립부가 훅 없이 추가되면 여기서 빨개진다(기동 가드는 평소 안 터진다)."""
    missing = [
        f"{module}:{line}" for module, line, passes in _gateway_constructions() if not passes
    ]
    assert not missing, (
        f"trace_masking_hook 없이 LlmGateway를 만든다: {missing}. "
        "조립부에서 주입하라(09 §2-16 P1′ · 01 §5 게이트웨이는 값을 모른다)."
    )


def test_no_gateway_construction_outside_whitelist() -> None:
    modules = {module for module, _line, _hook in _gateway_constructions()}
    unexpected = sorted(modules - _ALLOWED_GATEWAY_BUILDERS)
    assert not unexpected, (
        f"화이트리스트 밖에서 gateway를 만든다: {unexpected}. "
        "훅 주입을 확인하고 _ALLOWED_GATEWAY_BUILDERS에 등록하라."
    )


# ── ② 기동 — TRACING=true에서 훅 주입은 성공하고 미주입은 실패한다 ──


def _tracing_off_settings() -> LlmSettings:
    """`langsmith_tracing` 축을 **명시적으로 끈** 설정.

    추적 활성은 **env로만** 태운다(아래 두 테스트). 그래야 가드 발화가 오직 env 판정
    (`external_tracing_active()`)에서 온다는 것이 구조적으로 보장되고, B가 §2-16 후속 1로
    `or settings.langsmith_tracing` 보조 트리거를 걷어도 이 계약이 그대로 성립한다
    (A-11 — 판정 소스 단일화의 A쪽 조건).
    """
    return LlmSettings(langsmith_tracing=False, _env_file=None)


def test_brief_gateway_builds_with_tracing_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """주입 전에는 ValueError였다 — 이 테스트가 P1′의 완료 정의다."""
    from ai.llm.gateway import LlmGateway

    monkeypatch.setenv(_REPRESENTATIVE_TRACING_ENV, "true")
    gateway = LlmGateway(
        {ModelRole.NARRATOR: FakeBriefProvider()},
        transport_retry={ModelRole.NARRATOR: 0},
        trace_masking_hook=RedactionTripwireTraceHook(),
        settings=_tracing_off_settings(),
    )
    assert gateway is not None


def test_startup_guard_still_bites_without_hook(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """가드 자체가 살아 있는지 — 훅을 빼면 여전히 기동 실패다(B의 #48 동작 보존).

    제어 수단만 설정 → env로 바뀌었고 **가드가 무는 것 자체는 그대로 검증**한다.
    """
    from ai.llm.gateway import LlmGateway

    monkeypatch.setenv(_REPRESENTATIVE_TRACING_ENV, "true")
    with pytest.raises(ValueError, match="trace_masking_hook"):
        LlmGateway(
            {ModelRole.NARRATOR: FakeBriefProvider()},
            settings=_tracing_off_settings(),
        )


# ── ③ 훅 동작 ─────────────────────────────────────────────────────


def _context() -> ExecutionContext:
    return ExecutionContext(
        execution_id=UUID("00000000-0000-4000-8000-0000000000aa"),
        tenant_id="t1",
        capability=Capability.COMPOSITION,
        input_snapshot_hash="h",
        versions=VersionSet(
            pipeline_version="0.1",
            engine_version="0.1",
            schema_version="0.1",
            contract_version="0.1",
        ),
    )


def _request(prompt: str) -> LLMRequest:
    return LLMRequest(
        role=ModelRole.NARRATOR,
        prompt=prompt,
        prompt_id="composition/brief",
        prompt_version="v0.1",
    )


def test_hook_returns_request_unchanged_when_clean() -> None:
    """통과분은 바이트 동일 — 식별 4종은 물론 프롬프트까지 그대로."""
    request = _request("최근 2주 정답률이 떨어졌습니다.")
    assert RedactionTripwireTraceHook().mask(request, _context()) is request


def test_hook_blocks_when_redaction_was_skipped() -> None:
    """(a)를 건너뛴 프롬프트는 전송 전에 막힌다 — fail-closed(불변식 3)."""
    request = _request("김철수 어머니께 연락 부탁드립니다 010-1234-5678")
    with pytest.raises(RedactionBlocked):
        RedactionTripwireTraceHook().mask(request, _context())


def test_hook_reuses_redaction_blocked_for_retry_policy() -> None:
    """새 예외를 만들지 않는다 — gateway가 REDACTION_BLOCKED로 매핑하고 재시도 제외다.

    09 §1-10 ③ · error_codes §3. 새 예외를 발명하면 정책 차단이 일시 오류처럼 재시도된다.
    """
    from ai.contracts.llm import CallOutcome
    from ai.llm.gateway import _exception_outcome

    assert _exception_outcome(RedactionBlocked("x")) is CallOutcome.REDACTION_BLOCKED


def test_hook_message_carries_no_source_fragment() -> None:
    """사유 상세에 원문 조각을 싣지 않는다 — error_codes §4."""
    leaky = "김철수 010-1234-5678"
    try:
        RedactionTripwireTraceHook().mask(_request(leaky), _context())
    except RedactionBlocked as exc:
        assert "김철수" not in str(exc)
        assert "010-1234-5678" not in str(exc)
    else:  # pragma: no cover - 위에서 반드시 막힌다
        pytest.fail("차단되지 않았다")


# ── ④ 전제 고정: 정상 프롬프트는 트립와이어를 통과한다 ─────────────
#
# 이 설계의 전제는 "정상 트래픽에 findings=0"이다(2026-07-30 실측). 프롬프트 템플릿이
# 바뀌면 **조용히 깨지고 프로덕션이 fail-closed로 죽는다.** 그래서 실측을 회귀로 박는다.


def _draft_context() -> DraftContext:
    return DraftContext(
        student_ref="st_1",
        guardian_ref="gd_1",
        label_snapshot=LabelSnapshot(
            comm=CommStyle.DATA,
            sensitivity=Sensitivity.ANXIOUS,
            interest=Interest.GRADE,
            frequency=Frequency.FREQUENT,
        ),
        facts=(EvidenceFact(label="이번 주 정답률", value="62%"),),
        evidence_summaries=("지난주 과제 2건 미제출",),
        period_label="2026년 7월",
        fallback_text="이번 주 학습 상황을 정리해 보내드립니다.",
    )


def test_counsel_prompt_passes_tripwire() -> None:
    """counsel_pack 조립 프롬프트가 막히지 않는다 — 템플릿 변경 시 여기서 잡힌다."""
    prompt = assemble_prompt(_draft_context())
    assert RedactionTripwireTraceHook().mask(_request(prompt), _context()) is not None


def test_briefing_prompt_passes_tripwire() -> None:
    """브리핑 프롬프트(신호 유형 라벨 + 문면)가 막히지 않는다."""
    prompt = f"신호 유형: {DISPLAY_LABELS[SignalType.ACC_DROP]}\n최근 2주 정답률이 떨어졌습니다."
    assert RedactionTripwireTraceHook().mask(_request(prompt), _context()) is not None


def test_all_display_labels_pass_tripwire() -> None:
    """전 신호 유형 라벨 전수 — 라벨 어휘가 늘어도 트립와이어에 걸리지 않는다."""
    hook = RedactionTripwireTraceHook()
    for signal_type, label in DISPLAY_LABELS.items():
        prompt = f"신호 유형: {label}\n확인이 필요한 학습 신호가 있어요."
        assert hook.mask(_request(prompt), _context()) is not None, signal_type


def test_brief_gateway_end_to_end_passes_hook() -> None:
    """조립부가 만든 게이트웨이로 실제 호출이 통과한다(훅이 정상 경로를 막지 않는다)."""
    import asyncio

    gateway = build_brief_gateway()
    prompt = f"신호 유형: {DISPLAY_LABELS[SignalType.ACC_DROP]}\n합성 문면."
    result = asyncio.run(gateway.complete(_request(prompt), _context()))
    assert result.text
