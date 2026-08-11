"""전면 PG 플립 **preflight** — 원장 완전성 점검의 **첫 리더** (99 #36 G2).

🔴 **점검기를 만들어 놓고 소비처 0건으로 두지 않는다**(99 #22 형태). 이 파일이 그 리더다.

⚠ **「상시 감시」가 아니다** — BE 스케줄러가 없으므로 그렇게 부르지 않는다.
이것은 **사람이 플립 직전에 손으로 돌리는 점검 명령**이다. 운영 리더(배포 후 점검
명령·리포트)는 같은 판정 함수를 다시 읽는다.

⚠ **G3(실제 enqueue 재검증)는 여기서 안 한다** — 그건 G1 머지 뒤 별도 작업이다.

🔴 **출력에 개인정보·원 요청 본문을 싣지 않는다** — alias와 job/execution UUID까지만.
"""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Sequence

from ai.db.ledger_completeness import (
    LedgerFinding,
    LedgerVerdict,
    blocks_flip,
    judge_ledger_row,
    summarize,
)
from ai.db.repositories.ledger_audit import PgLedgerAudit
from ai.db.session import get_sessionmaker

#: 판정별로 몇 건까지 식별자를 나열할지 — 전량을 쏟으면 읽는 사람이 못 본다.
_SAMPLE = 20


def preflight_blocks(findings: Sequence[LedgerFinding]) -> bool:
    """🔴 **리포트와 종료 코드가 함께 쓰는 단 하나의 판정.**

    ⚠ 종전엔 0건 문면과 종료 코드가 **각자 판정**했다 — 같은 규칙을 두 곳에 두면
    **언젠가 반대로 움직인다**(99 #02). 실제로 0건에서 종료 코드는 1인데
    문면은 「차단 사유 없음」이었다.

    **막는 것:** `violation` · `unknown` · **관측 0건**(측정 없이 관문을 열면 관문이 아니다).
    ⚠ **종전에는 「`separate_gap`은 안 막는다」가 여기 있었다** — 그 판정은 8/12에
    없어졌다(99 ㉾ 해소). **예외 통로가 하나도 없다.**
    """
    return not findings or blocks_flip(summarize(findings))


def render_report(findings: Sequence[LedgerFinding], *, tenant_id: str) -> str:
    """🔴 **네 축을 각각 따로 낸다** — 합치면 어느 것이 결함인지 알 수 없다.

    ⚠ 종전엔 다섯이었다 — `separate_gap`은 8/12에 없어졌다(99 ㉾ 해소).
    """
    counts = summarize(findings)
    lines = [
        f"# PG 원장 완전성 점검 — tenant={tenant_id}",
        "",
        f"관측 행: {len(findings)}건",
    ]
    if not findings:
        #: 🔴 **0건은 「통과」가 아니라 「측정 없음」이다.**
        lines += [
            "",
            "⚠ **관측 행 0건 — 「원장 완전성 통과」가 아니다.**",
            "안 본 것과 없는 것은 다르다. 테넌트·백엔드를 먼저 확인하라.",
        ]
    lines += [
        "",
        f"- ok               : {counts[LedgerVerdict.OK]}건",
        f"- allowed_absence  : {counts[LedgerVerdict.ALLOWED_ABSENCE]}건 (정상 부재)",
        f"- 🔴 violation     : {counts[LedgerVerdict.VIOLATION]}건",
        f"- ⚠ unknown        : {counts[LedgerVerdict.UNKNOWN]}건 (증명 불가 — 초록이 아니다)",
    ]
    for verdict in (LedgerVerdict.VIOLATION, LedgerVerdict.UNKNOWN):
        rows = [f for f in findings if f.verdict is verdict]
        if not rows:
            continue
        lines += ["", f"## {verdict.value} — {len(rows)}건", ""]
        for finding in rows[:_SAMPLE]:
            o = finding.observation
            lines.append(
                f"- job={o.job_id} run={o.run_id} kind={o.agent_kind.value} "
                f"status={o.status.value} — {finding.reason}"
            )
        if len(rows) > _SAMPLE:
            lines.append(f"- … 외 {len(rows) - _SAMPLE}건(전량은 종료 코드로 판정)")
    #: 🔴 **문면도 `preflight_blocks` 하나를 읽는다** — 종료 코드와 갈릴 자리를 없앤다.
    if not preflight_blocks(findings):
        judgement = "✅ 플립 차단 사유 없음(violation 0 · unknown 0)."
    elif not findings:
        judgement = "🔴 **플립 차단** — 관측 0건이라 **판정 자체를 못 했다.**"
    else:
        judgement = "🔴 **플립 차단** — violation 또는 unknown이 있다."
    lines += [
        "",
        "---",
        "",
        judgement,
        "⚠ 예외 통로는 없다 — `mapping_probe`도 같은 규칙으로 판정한다(99 ㉾ 해소).",
    ]
    return "\n".join(lines)


async def _main_async(tenant_id: str) -> int:
    audit = PgLedgerAudit(get_sessionmaker())
    observations = await audit.observe(tenant_id=tenant_id)
    findings = [judge_ledger_row(o) for o in observations]
    print(render_report(findings, tenant_id=tenant_id))
    #: 🔴 **문면과 같은 함수를 읽는다.**
    return 1 if preflight_blocks(findings) else 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="전면 PG 플립 전 원장 완전성 점검(읽기 전용)"
    )
    parser.add_argument("--tenant-id", required=True, help="점검할 테넌트(필수)")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(_main_async(args.tenant_id)))


if __name__ == "__main__":
    main()
