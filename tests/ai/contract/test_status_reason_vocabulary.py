"""`status_reason` 와이어 어휘 — 코드와 BE 문서를 **양방향**으로 댄다 (99 #78).

🔴 **PR-02 가 이 PR 이 왜 필요한지 증명했다.** BE 에 보낸 산출물과 코드를 대조하는 층이
없어서 `meta.versions` 키가 11일간 21곳 틀린 채로 나가 있었다(99 #98). `status_reason` 은
같은 모양의 자리다 — BE 가 화면 매핑을 만드는 값 목록인데 **전수를 재는 검사가 0건**이었다.

⚠ 기존 검사들이 왜 이걸 못 잡았나(실측):
  · `test_every_internal_status_is_mapped` — 내부 `DraftStatus` 전수다(**값이 아니다**)
  · `_RESULT_FIELDS` — 필드**명** 전수다(값이 아니다)
  · `wire_status_for` — `partition(":")[0]` 로 접두를 **그대로 통과**시킨다(검증 안 한다)
  · 라우터가 직접 만드는 다섯은 그 함수를 **아예 안 지난다**

🔴 **한 방향만 재면 안 된다.** 8/8 에 `data_lt_2weeks`(코드가 안 내는 값)가 문서에 있었다 —
그건 「문서 → 코드」 방향으로만 잡힌다.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Final

from ai.contracts.counsel import UNMAPPED_REASON_PREFIX, WIRE_STATUS_REASONS

_ERROR_CODES_DOC: Final = (
    Path(__file__).parents[3] / "docs" / "policies" / "error_codes.md"
)
_SECTION_HEADING: Final = "#### 🔴 `status_reason` 전수"


def _census_section() -> str:
    """§2.1 의 「`status_reason` 전수」 절만 잘라 낸다.

    🔴 **표를 파싱하지 않는다** — 열 개수·정렬 같은 문서 형식에 검사가 묶이면 문서를
    다듬을 때마다 red 가 된다. 절을 텍스트로 자르고 코드 스팬을 뽑는 것으로 충분하다.
    """
    text = _ERROR_CODES_DOC.read_text(encoding="utf-8")
    start = text.index(_SECTION_HEADING)
    rest = text[start + len(_SECTION_HEADING) :]
    section = rest[: rest.index("\n### ")]
    #: 🔴 **전수 표 하나만 남긴다 — 제목 뒤 첫 번째 연속 `|` 블록이다.**
    #: ⓐ 절 아래 산문에는 `citations`·`draft_id` 같은 **필드명**이 코드 스팬으로 섞여
    #:   있어서, 절 전체를 훑으면 그것들이 「문서에만 있는 사유」로 잡힌다(실측 유령 5건).
    #: ⓑ 🔴 **그리고 같은 절에 표가 하나 더 있다** — 「`failed`의 `status_reason` 대응」
    #:   표(와이어 5종 ↔ 내부 `DraftStatus`)에도 사유 값이 코드 스팬으로 나온다.
    #:   `|` 줄을 전부 모으면 두 표가 합쳐져서, **전수 표에서 행을 지워도 옆 표에 남은
    #:   같은 값 때문에 통과한다** — 고의 파괴 1-③이 green 으로 그걸 잡았다.
    #: ⚠ 열을 가르지는 않는다 — 블록을 고르는 것이지 표를 해석하지 않는다.
    lines = section.splitlines()
    block: list[str] = []
    for line in lines:
        if line.startswith("|"):
            block.append(line)
        elif block:
            break  # 첫 표가 끝났다 — 뒤의 표는 전수가 아니다
    return "\n".join(block)


def _documented_reasons() -> set[str]:
    """절 안의 `` `값` `` 코드 스팬 중 **사유로 보이는 것**만 추린다.

    ⚠ **이 방식이 못 보는 것을 적어 둔다**(지시서 요구):
      · 코드 스팬으로 감싸지 **않은** 값 — 표에 그냥 텍스트로 적으면 안 보인다
      · 표 **밖**(산문)에만 적힌 사유 — 표 줄만 보므로 안 보인다. 전수의 정본이 표라는
        전제이고, 그 전제가 깨지면 아래 절단 가드가 먼저 운다
      · `draft_status`·함수명 등 사유가 아닌 코드 스팬 — 아래 제외 목록으로 거른다
      ⇒ **제외 목록이 곧 이 검사의 사각지대다.** 새 이름을 여기 넣기 전에
        「그게 정말 사유가 아닌가」를 먼저 물어야 한다.
    """
    #: 사유가 아닌 코드 스팬 — 같은 절에 섞여 나오는 것들.
    not_a_reason = {
        "status_reason",
        "draft_status",
        "wire_status_for",
        "generated",
        "template_only",
        "rejected_insufficient",
        "llm_failed:{예외클래스}",
        "gate_exhausted:{게이트사유}",
        "unmapped:{값}",
        "DraftStatus",
        "ERROR_*",
        "composition/counsel/worker.py",
        "contracts/agents.WORKER_RECOVERY_EXHAUSTED",
        "_generate",
        "_wire_result",
        #: 「어디서 나오나」 열이 드는 **필드명** — 사유가 아니다( 행의
        #: *"… 부재"*). 실측으로 걸렸다.
        "draft_id",
        "graph.py",
        "topic=schedule",
        "failed",
        "[BE 작성]",
        "RefineResponse.message",
        "llm_failed:LlmTimeout",
        "gate_exhausted:too_short:13<180",
    }
    spans = set(re.findall(r"`([^`\n]+)`", _census_section()))
    #: 사유 값의 모양 — 소문자·숫자·밑줄만. 경로·호출 표기는 여기서 이미 빠진다.
    return {
        span
        for span in spans - not_a_reason
        if re.fullmatch(r"[a-z][a-z0-9_]*", span)
        #: 🔴 `llm_failed`·`gate_exhausted` 는 사유이자 판정 이름이라 위 집합에서 못 뺀다.
        #: 판정 이름 셋만 따로 제외한다.
    } - {"generated", "template_only", "rejected_insufficient"}


# ── 방향 ① 코드 → 문서 ────────────────────────────────────────────


def test_every_wire_reason_is_documented_for_the_backend() -> None:
    """🔴 집합의 **모든** 값이 §2.1 전수 표에 있다.

    못 잡으면: 새 사유를 만들고 문서를 안 고친 것 ⇒ **BE 화면이 빈칸**이 된다.
    ⚠ 이 방향만으로는 「문서에만 있는 유령 값」을 못 잡는다 — 아래가 그쪽이다.
    """
    documented = _documented_reasons()
    missing = sorted(WIRE_STATUS_REASONS - documented)

    assert not missing, (
        f"코드가 내는데 error_codes.md §2.1 에 없는 사유: {missing}\n"
        "BE 는 이 표로 화면 매핑을 만든다 — 없는 사유는 빈칸이 된다."
    )


# ── 방향 ② 문서 → 코드 ────────────────────────────────────────────


def test_the_document_carries_no_ghost_reason() -> None:
    """🔴 §2.1 표의 모든 사유가 집합에 있다 — **문서에만 있는 값은 유령이다.**

    못 잡으면: BE 가 **없는 값**에 화면 문구를 붙인다. 실제로 있었다 — 8/8 에
    `data_lt_2weeks` 가 문서에 있었는데 코드는 그 값을 내지 않았다.
    """
    documented = _documented_reasons()
    ghosts = sorted(documented - WIRE_STATUS_REASONS)

    assert not ghosts, (
        f"문서에는 있는데 코드가 안 내는 사유: {ghosts}\n"
        "BE 가 없는 값에 화면 문구를 붙인다(8/8 data_lt_2weeks 가 그랬다)."
    )


def test_the_extractor_actually_sees_the_table() -> None:
    """🔴 **절단 가드** — 추출기가 빈손이면 위 둘이 아무것도 안 본다.

    ⚠ 문서 제목이 바뀌거나 절 구분이 달라지면 `_census_section` 이 조용히 다른 곳을
    자를 수 있다. 그때 「0건 vs 0건」으로 **둘 다 green** 이 된다.
    """
    documented = _documented_reasons()
    assert len(documented) >= len(WIRE_STATUS_REASONS), (
        f"추출한 문서 사유가 {len(documented)}건뿐이다 — 절을 잘못 잘랐을 수 있다"
    )


def test_the_unmapped_prefix_is_not_a_member_of_the_vocabulary() -> None:
    """`unmapped:{값}` 은 **접두**라 어휘의 멤버가 아니다.

    ⚠ 멤버로 넣으면 BE 가 `"unmapped"` 라는 고정 문자열을 기다린다 — 실제로 오는 것은
    `unmapped:some_status` 다.
    """
    assert UNMAPPED_REASON_PREFIX not in WIRE_STATUS_REASONS
    assert not any(":" in reason for reason in WIRE_STATUS_REASONS), (
        "어휘에 접두:상세 형태가 섞였다 — 와이어에는 접두만 간다"
    )


# ── 라우터 축 — 집합과 문서가 맞아도 라우터가 밖의 값을 낼 수 있다 ──


def test_every_reason_the_router_emits_is_in_the_vocabulary() -> None:
    """🔴 **라우터가 실제로 내는 값이 집합의 멤버다.**

    ⚠ 위 두 검사는 「집합 ↔ 문서」만 본다 — **라우터가 집합 밖 값을 내는 것**은 못 잡는다.
    ⇒ 라우터가 만드는 경로를 **HTTP 응답으로** 재서 그 구멍을 닫는다.

    ⚠ **픽스처를 재사용한다** — PR-02 가 그 경로 셋을 이미 실제 앱으로 돌려 저장해 뒀다
    (`template_only` · `rejected_insufficient` · `draft_body_missing`). 새로 만들면
    **같은 응답의 사본이 둘**이 되고, 갈리면 어느 쪽이 정본인지 알 수 없다.
    """
    fixture_dir = (
        Path(__file__).parent / "fixtures" / "http"
    )
    seen: dict[str, str] = {}
    for path in sorted(fixture_dir.glob("get_counsel_draft.*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        result = (payload.get("data") or {}).get("result")
        if not isinstance(result, dict):
            continue  # 404 등 — `data`가 없다
        reason = result.get("status_reason")
        if reason is None:
            continue  # `generated` — 값의 부재는 멤버가 아니다
        seen[path.name] = reason

    assert seen, (
        "counsel GET 픽스처에서 status_reason 을 하나도 못 읽었다 — "
        "이 검사가 눈이 멀었다(PR-02 의 픽스처가 사라졌나?)"
    )
    stray = {name: reason for name, reason in seen.items() if reason not in WIRE_STATUS_REASONS}
    assert not stray, (
        f"라우터가 어휘 밖의 status_reason 을 냈다: {stray}\n"
        "집합과 문서가 맞아도 라우터가 딴 값을 내면 BE 화면은 빈칸이다."
    )


def test_the_router_literals_are_gone() -> None:
    """🔴 라우터가 **상수를 지난다** — 리터럴을 다시 박으면 어휘 밖으로 샐 수 있다.

    ⚠ 행동으로는 못 잰다 — 리터럴이든 상수든 같은 문자열이 나간다. 값이 같은 두 형태를
    가르는 것은 **소스뿐**이다(PR-01 에서 배운 자리 · 결정 로그 121).
    """
    source = (
        Path(__file__).parents[2] / "ai" / "api" / "routers" / "counsel.py"
    )
    if not source.exists():  # 레이아웃이 바뀌면 조용히 통과하지 않는다
        source = Path(__file__).parents[3] / "src" / "ai" / "api" / "routers" / "counsel.py"
    text = source.read_text(encoding="utf-8")

    literals = sorted(
        reason
        for reason in WIRE_STATUS_REASONS
        if f'status_reason="{reason}"' in text
    )
    assert not literals, (
        f"라우터가 status_reason 에 리터럴을 박고 있다: {literals} — "
        "어휘 상수를 쓰면 집합에 없는 값을 넣을 때 이름부터 없다"
    )
