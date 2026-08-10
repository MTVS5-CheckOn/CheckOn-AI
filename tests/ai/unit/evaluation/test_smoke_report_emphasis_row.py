"""리포트 §7이 **강조점 스캔 표면 수를 싣는가** (99 #34 · 6차 재실행 실측 2026-08-10).

🔴 **6차 회차의 유일한 목적이 리포트에 안 실렸다.** `pii.emphasis_scanned = 2`가 raw JSON에만
있고 Markdown엔 「emphasis」가 **0건**이었다 — **리포트만 읽는 사람은 32콜을 왜 썼는지 모른다.**

⚠ **그걸 막으라는 검사가 이미 있었는데 안 물었다.**

```python
assert '"emphasis_scanned"' in source, "리포트에 관측 크기가 안 실린다"
```

**러너 모듈의 소스 문자열**을 grep할 뿐 **렌더된 리포트를 안 본다** — 키가 `_pii_scan` 반환
dict에 있기만 하면 통과한다. **메시지는 「리포트에」인데 보는 것은 「소스에」**다(로그 85 계열).
🔴 **오탐이 아니라 미탐이라 green으로 보였다.**

⇒ 이 파일은 **`_render()`가 낸 Markdown 문자열**을 본다. 소스 grep으로는 통과할 수 없다.
"""

from __future__ import annotations

from typing import Any, Final

import pytest

from ai.evaluation.counsel_llm_smoke import _render

_ROW_LABEL: Final = "강조점 스캔 표면"


def _data(*, emphasis_scanned: int) -> dict[str, Any]:
    """리포트 렌더에 필요한 **최소 데이터** — 실 LLM 호출 없이 문면만 만든다."""
    return {
        "run_date": "2026-08-10-2",
        "preflight": {
            "tracing_env_active": [],
            "base_url": "https://example.invalid/v1",
            "model": "test-model",
            "timeout_s": 15.0,
        },
        "demo": {"students": 1, "signals": 1, "threshold_version": 3},
        "s1": {
            "signals": 1,
            "rows": [
                {
                    "rule_id": "R1",
                    "attempts": 1,
                    "outcome": "ok",
                    "gate_passed": True,
                    "fallback_used": False,
                    "mask_residue": False,
                    "elapsed_ms": 100,
                }
            ],
        },
        "s2": {"rows": []},
        "s3": {"rows": []},
        "s5": {
            "comparable": False,
            "identical": False,
            "verdict": "**비교 불가**",
            "status_identical": True,
            "len_first": 0,
            "len_second": 0,
            "first": "",
            "second": "",
        },
        "s4": {
            "records_captured": 1,
            "tokens_total": 10,
            "collector_dropped_calls": 0,
            "collector_evicted_runs": 0,
            "view_cache_evicted": 0,
            "draft_cache_evicted": 0,
            "job_ledger_size": 0,
            "job_ledger_added": 0,
            "quota_consumed_default": 0,
            "llm_call_id_is_constant_none": False,
            "production_recorder_wired": True,
            "production_recorder_is_collector": True,
            "reasoning_tokens_available": False,
            "refine_ledger_observed": False,
            "pack_miss_absent": None,
            "pack_miss_foreign_tenant": None,
            "empty_s1": 0,
            "empty_s2": 0,
            "s2_jobs": 0,
            "record_outcomes": {"ok": 1},
            "empty_s1_detail": [],
            "empty_s2_detail": [],
            "token_spread": [],
            "usage_axis": {
                "ai_run_rows": 0,
                "zero_calls": 0,
                "zero_call_rows_with_params": 0,
                "called_rows_without_params": 0,
                "model_fields_agree": True,
                "observed_params": {},
                "verdict": 0,
                "with_calls": 0,
            },
        },
        "pii": {
            "llm_texts": 3,
            "emphasis_scanned": emphasis_scanned,
            "uncertain": 0,
            "masked": 0,
            "token_residue": 0,
            "residue_detail": [],
            "uncertain_detail": [],
        },
        "verdict": "**테스트 렌더**",
    }


def _rendered(*, emphasis_scanned: int) -> str:
    try:
        return _render(_data(emphasis_scanned=emphasis_scanned))
    except KeyError as exc:  # pragma: no cover — 최소 데이터가 낡으면 즉시 알린다
        pytest.fail(f"렌더 최소 데이터가 낡았다 — 러너가 요구하는 키: {exc}")


def test_the_render_path_actually_runs() -> None:
    """🔴 절단 가드 — 렌더가 안 돌면 이 파일은 아무것도 안 본다."""
    assert "## 7." in _rendered(emphasis_scanned=0)


@pytest.mark.parametrize("scanned", [0, 2, 17])
def test_the_scanned_surface_is_printed_in_the_report(scanned: int) -> None:
    """🔴 **렌더된 Markdown에 값이 있어야 한다** — 소스에 키가 있는 것으로는 통과 못 한다."""
    rendered = _rendered(emphasis_scanned=scanned)
    assert _ROW_LABEL in rendered, (
        f"§7에 「{_ROW_LABEL}」 행이 없다 — 이 회차의 목적이 리포트에 안 실린다(99 #34)"
    )
    row = next(line for line in rendered.splitlines() if _ROW_LABEL in line)
    assert f"{scanned}건" in row, f"스캔 수가 문면에 없다: {row}"


def test_zero_is_shown_not_hidden() -> None:
    """🔴 **0건을 숨기지 않는다** — 「관측 0」과 「행이 없다」는 다른 사실이다.

    ⚠ 행을 조건부로 만들면 **배선이 끊긴 회차가 아무 말도 안 하는 회차**와 같아 보인다.
    """
    row = next(
        line
        for line in _rendered(emphasis_scanned=0).splitlines()
        if _ROW_LABEL in line
    )
    assert "0건" in row, f"0건이 숨겨졌다: {row}"


def test_the_existing_masking_rows_survive() -> None:
    """기존 §7 세 줄은 그대로 — 새 행을 넣다가 밀어내지 않았는가."""
    rendered = _rendered(emphasis_scanned=2)
    for label in ("검사한 LLM 출력", "⟪⟫ 토큰 잔존", "마스킹 **불확실**"):
        assert label in rendered, f"§7의 기존 행이 사라졌다: {label}"


def test_a_source_grep_cannot_satisfy_this_file() -> None:
    """🔴 **이 파일이 #34의 미탐을 실제로 막는지**를 스스로 묻는다.

    종전 검사는 `'"emphasis_scanned"' in source`였다 — 그 조건은 **지금도 참**이고
    **리포트에 행이 없어도 참**이었다. 여기서는 렌더 결과만 본다는 사실을 못 박는다.
    """
    rendered = _rendered(emphasis_scanned=2)
    assert '"emphasis_scanned"' not in rendered, (
        "리포트에 파이썬 키 이름이 그대로 나온다 — 사람이 읽는 문면이 아니다"
    )
