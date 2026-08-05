"""redaction 멱등성 — 자기 산출물에 새 finding을 만들지 않는다 (masking_redaction §2).

🔴 **개별 케이스가 아니라 성질을 잡는다.** 8/5에는 별명형 편중으로 성명형이 샜고, 8/6에는
조사 없는 형태 편중으로 `학생의`가 샜다. 형태를 하나씩 쫓으면 **다음 형태에서 또 샌다.**
멱등성은 형태와 무관한 불변식이라 새 패턴이 들어와도 자동으로 물린다.

**왜 멱등성이 안전 문제인가** — 트립와이어(`RedactionTripwireTraceHook`)와 `LLM_PAYLOAD`
저장 훅이 **마스킹 통과본을 다시 검사**한다. 비멱등이면 정상 문면이 차단돼(전송 중단·저장
거부) 기능이 조용히 사라진다. 트립와이어 자체는 옳게 동작한다 — 막힌 이유가 redaction의
비멱등이었다.
"""

from __future__ import annotations

import inspect
from typing import Final

import pytest

from ai.evaluation.golden.redaction.corpus import CORPUS
from ai.runtime import redaction as redaction_module
from ai.runtime.redaction import redact

#: C-2 구체 케이스 — 코퍼스 50~54와 같은 형태를 여기서도 직접 고정한다.
#: 코퍼스는 "미탐 0·오탐 예산"을 보고 이 파일은 "멱등성"을 본다 — 축이 다르다.
_CONCRETE: Final = (
    "⟪이름1⟫ 학생의 어머니입니다",
    "⟪이름1⟫ 학생을 부탁드립니다",
    "⟪이름1⟫ 학생은 성실합니다",
    "학생의 어머니입니다",
)


# ── C-1 🔴 코퍼스 전건 멱등성 ─────────────────────────────────────


def test_corpus_is_idempotent_end_to_end() -> None:
    """🔴 **수용 기준 2.** 코퍼스 전건의 1차 산출을 다시 redact해 새 finding이 0건.

    예외가 나오면 **케이스를 빼지 말고** 왜 비멱등인지 기록한다(허용 목록 금지).
    2026-08-06 실측: 전 54건 예외 **0건**.
    """
    offenders: list[str] = []
    for case in CORPUS:
        first = redact(case.text)
        second = redact(first.masked_text)
        if second.findings or second.uncertain:
            offenders.append(
                f"[{case.id} {case.category}] {case.text!r}\n"
                f"    1차 {first.masked_text!r}\n"
                f"    2차 {second.masked_text!r} "
                f"findings={[f.type for f in second.findings]} "
                f"uncertain={second.uncertain}"
            )
    assert not offenders, (
        "마스킹 산출물을 다시 마스킹해 새 finding이 났다 — 트립와이어가 정상 문면을 "
        "차단한다:\n" + "\n".join(offenders)
    )


def test_corpus_second_pass_does_not_change_the_text() -> None:
    """산출 문면도 바뀌지 않는다 — finding 없이 텍스트만 흔들리는 경우까지 막는다.

    🔴 문면이 바뀌면 **보낸 적 없는 문면이 원장에 남는다**(`LLM_PAYLOAD`가 저장 시 재마스킹
    을 하지 않는 이유 · 99 ㉝). finding 0건만 보면 그 경우를 놓친다.
    """
    drifted = [
        f"[{case.id}] {first!r} → {redact(first).masked_text!r}"
        for case in CORPUS
        if (first := redact(case.text).masked_text) != redact(first).masked_text
    ]
    assert not drifted, "2차 redact가 문면을 바꿨다:\n" + "\n".join(drifted)


# ── C-2 구체 케이스 ───────────────────────────────────────────────


@pytest.mark.parametrize("text", _CONCRETE)
def test_concrete_cases_are_stable(text: str) -> None:
    """마스킹 산출물·호칭어 결합형이 재검출되지 않는다."""
    result = redact(text)
    assert not result.findings, (
        f"{text!r}에서 새 finding이 났다 → {result.masked_text!r}"
    )
    assert not result.uncertain
    assert result.masked_text == text  # 문면 무변


def test_honorific_with_particle_is_not_a_name() -> None:
    """🔴 조사가 붙어도 호칭어다 — 종전 필터가 **정확 일치**라 `학생의`가 우회했다."""
    for particle in ("의", "을", "은", "를", "과", "와"):
        text = f"학생{particle} 어머니입니다"
        assert redact(text).masked_text == text, f"'학생{particle}'이 이름으로 마스킹됐다"


def test_particle_stripping_does_not_swallow_longer_words() -> None:
    """🔴 `startswith`가 아니라 **조사 제거**다 — `학생회`까지 스킵하면 미탐이 난다.

    `학생회의`는 `회의`를 벗겨 `학생`이 되는 것이 아니라 `의`만 벗겨 `학생회`가 된다.
    호칭어 목록에 없으므로 후보로 남는다(마스킹돼도 오탐이지 미탐이 아니다).
    """
    stripped = redaction_module._strip_particle("학생회의", ("의", "을", "한테"))  # noqa: SLF001
    assert stripped == "학생회"
    assert redaction_module._strip_particle("학생의", ("의",)) == "학생"  # noqa: SLF001
    # 조사만으로 된 어절은 벗기지 않는다 — 빈 문자열이 목록에 걸릴 여지를 없앤다.
    assert redaction_module._strip_particle("의", ("의",)) == "의"  # noqa: SLF001


# ── A-3 🔴 `_follows_token`이 실제로 불리는지 ─────────────────────


def test_follows_token_is_actually_wired() -> None:
    """🔴 **정의만 있고 안 불리면 죽은 코드다.** 호출부를 소스로 고정한다.

    ⚠ 이 PR의 지시서는 `_follows_token`이 "정의만 있고 호출부가 0곳"이라고 했지만
    **측정 결과 `_name_candidates`에서 이미 호출된다**(정의와 같은 커밋 `b5cfa7b`에서
    함께 배선됐다). 지시서 전제가 stale이었다 — 그 사실을 PR 본문에 적었다.
    """
    source = inspect.getsource(redaction_module)
    calls = source.count("self._follows_token(")
    assert calls >= 1, "_follows_token 호출부가 없다 — 죽은 코드다"
    assert "def _follows_token(" in source


def test_follows_token_is_not_applied_to_the_honorific_stage() -> None:
    """🔴 **호칭 단계에는 걸지 않는다 — 걸면 미탐이 난다.** 실측으로 고정한다.

    호칭 단계는 `_mask_batch`(연락처·학교·주소) **직후**라 직전 토큰이 남의 유형이고 그 뒤
    어절은 아직 마스킹되지 않은 실제 이름일 수 있다. 스코어링 단계에서 같은 스킵이 맞는
    것은 그쪽이 파이프라인 **마지막**이라 "직전 토큰 = 이미 처리된 자리"가 성립하기 때문이다.

    ⚠ 지시서 A-1은 이 자리에도 걸라고 했다. 걸어 보니 아래 단정이 깨졌다(미탐).
    """
    assert redact("010-1234-5678 서연 어머니").masked_text == "⟪연락처1⟫ ⟪이름1⟫ 어머니"
    assert redact("비고: 010-9999-8888 지우 어머니").masked_text == (
        "비고: ⟪연락처1⟫ ⟪이름1⟫ 어머니"
    )
    # 호칭 단계의 `repl`에 토큰 인접 스킵이 들어오면 위 두 단정이 깨진다 — 소스로도 못 박는다.
    honorific = inspect.getsource(redaction_module._Redactor._sub_honorific)  # noqa: SLF001
    assert "_follows_token" not in honorific.split('"""')[-1], (
        "호칭 단계에 토큰 인접 스킵이 들어왔다 — 미탐이 난다(위 단정 참조)"
    )


# ── 조사 목록의 정본이 하나인지 ───────────────────────────────────


def test_particles_come_from_one_yaml_list() -> None:
    """🔴 조사 목록은 yaml **한 곳**이 정본이다 — 인명 패턴과 호칭어 필터가 공유한다.

    종전에는 조사가 정규식 문자열 안에만 있어 호칭어 필터가 그 목록을 볼 수 없었고,
    그래서 정확 일치로 비교하다 `학생의`를 놓쳤다. 코드가 정규식을 파싱하는 방향은
    금지이므로(§6) yaml에서 목록을 읽어 **조립**한다.
    """
    cfg = redaction_module._config()  # noqa: SLF001
    assert cfg.honorific_particles, "조사 목록이 비었다"
    joined = "|".join(cfg.honorific_particles)
    korean_patterns = [
        pattern.pattern for pattern in cfg.name_candidates if joined in pattern.pattern
    ]
    assert korean_patterns, (
        "인명 후보 패턴이 호칭어 필터와 같은 조사 목록으로 조립되지 않았다 — 정본이 둘이다"
    )


# ── C-3 🔴 트립와이어 종단 — 정상 문면이 차단되지 않는다 ──────────


def test_typical_guardian_inquiry_is_not_blocked_by_the_tripwire() -> None:
    """🔴 **수용 기준 3.** `"김민준 학생의 어머니입니다"`가 classify에서 차단되지 않는다.

    **노출면이 여기다** — classify는 문의 **원문**을 받아 마스킹 통과본을 프롬프트에 싣고,
    "○○ 학생의 어머니입니다"는 학부모 문의의 전형 문면이다. 종전에는:

        원문  → (a) redaction → "⟪이름1⟫ 학생의 어머니입니다"
              → 트립와이어가 다시 redact → `학생의`가 새 finding
              → RedactionBlocked → `classified=false, fallback_reason=tripwire_blocked`

    ⚠ #86이 이 형태를 못 잡은 이유는 수용 기준이 **조사 없는 케이스**(`⟪이름1⟫ 학생
    어머니입니다`)만 봤기 때문이다.
    """
    from fastapi.testclient import TestClient

    from ai.api.app import create_app
    from ai.api.routers.classify import reset_inquiry_class_store

    reset_inquiry_class_store()
    try:
        with TestClient(create_app()) as client:
            response = client.post(
                "/v1/classify",
                json={
                    "inquiry_ref": "iq_tripwire_1",
                    "body_text": "김민준 학생의 어머니입니다. 성적이 궁금해서 문의드립니다.",
                },
                headers={"X-Tenant-Id": "t1", "X-Request-Id": "rq-tripwire-1"},
            )
        assert response.status_code == 200, response.text
        data = response.json()["data"]
        assert data["fallback_reason"] != "tripwire_blocked", (
            "정상 문면이 트립와이어에 막혔다 — redaction 비멱등이 되돌아왔다"
        )
        assert data["classified"] is True
    finally:
        reset_inquiry_class_store()


def test_masked_prompt_reaching_the_gateway_is_tripwire_clean() -> None:
    """트립와이어가 보는 것과 같은 판정을 직접 확인한다 — 층을 분리해 고정한다.

    라우터 테스트가 통과해도 "왜 통과했는지"가 흐리면 다음에 못 고친다. 트립와이어는
    `redact(prompt).findings or uncertain`으로 판정하므로 그 값을 그대로 본다.
    """
    from ai.composition.classify.classifier import render_prompt

    masked = redact("김민준 학생의 어머니입니다. 성적이 궁금해서 문의드립니다.")
    verdict = redact(render_prompt(masked.masked_text))
    assert not verdict.findings, f"트립와이어가 잡을 잔여가 있다 → {verdict.findings}"
    assert not verdict.uncertain
