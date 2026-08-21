"""된소리되기 규범 근거의 A 소유 redaction 오탐 — 🔴 **해소 기록**(2026-08-22).

⚠ 🔴 **이 파일은 종전에 「재현」이었다.** `language.grammar.fortition` 의 규범 근거 7행 중
**7번 행**이 `redact()` 오탐에 걸려 `uncertain=True` 였고, 그 때문에 B 축이 그 노드를
`_A_OWNED_REDACTION_BLOCKED_NODES` 로 **제외**한 채 57노드 중 56만 돌리고 있었다.

🔴 **언제 무엇으로 해소됐나** — 2026-08-22, `runtime/redaction_patterns.yaml` 의
`whitelists.name_exclude` 에 어간 **`이시옷`·`성립되`** 를 넣었다(99 #185 · #123 계열).
**패턴은 한 글자도 안 고쳤다** ⇒ 미탐 0 유지(`test_no_miss_across_corpus` green).

🔴 **왜 걸렸나**(원인은 지운 게 아니라 여기 남긴다):
    `scoring.name_candidates[3]` = `([성씨][가-힣]{2})(조사)(?![가-힣])`
    「사이시옷**이**」 → `이(성씨)+시옷` + 조사 `이` …… **2회**
    「휴지가 성립되**는**」 → `성(성씨)+립되` + 조사 `는` …… 1회
    ⇒ 한 문장에 후보 **셋** ⇒ 밀도 임계를 넘겨 **문장 통째 `⟪확인필요⟫` + `uncertain=True`**.
⚠ 국어 문법 용어가 「성씨 1자 + 임의 2자 + 조사」 형태를 자주 만든다 — 99 #123
(`문제집을`·`성적표를`)과 **정확히 같은 형태**다.

⚠ 🔴 **검사를 지우지 않았다.** 오탐이 있었다는 사실과 그 크기가 이 파일의 기록이고,
지우면 «왜 그 노드가 한 달 넘게 빠져 있었나» 가 사라진다. `xfail` 로 숨기지도 않는다 —
그건 «지금 깨져 있다» 는 뜻인데 지금은 **고쳐져 있다.**
"""

from ai.problem_generation.infrastructure.grammar_norm import (
    load_grammar_norm_corpus,
    select_node_rows,
)
from ai.runtime.redaction import redact

_NODE_ID = "language.grammar.fortition"


def test_fortition_label_is_not_the_redaction_trigger() -> None:
    assert not redact("된소리되기").uncertain


def test_fortition_quote_no_longer_has_name_candidate_false_positives() -> None:
    """🔴 **오탐이 0 이 됐다** — 종전 이름은
    `test_fortition_quote_has_two_minimal_name_candidate_false_positives` 였다.

    ⚠ 이름을 고친 이유: false positive 가 **0개**가 되어 **이름이 거짓말**을 하게 됐다
    (로그 154 — 값이 바뀌면 이름도 본다). 옛 이름을 여기 남겨 두어야 그 시절을 찾는
    사람이 이 함수에 닿는다.

    ━━ 🔴 **정정 이력 — 종전 단언과 그때의 사실** ━━

        assert len(uncertain_rows) == 1      ← 🔴 지금은 **0**
        assert "⟪확인필요⟫" in redact("이시옷이").masked_text   ← 🔴 지금은 **안 붙는다**
        assert "⟪확인필요⟫" in redact("성립되는").masked_text   ← 🔴 지금은 **안 붙는다**

    ⚠ **아래 둘은 그대로 참이다** — 값이 아니라 **성질**을 재기 때문이다:
        · 후보 1개면 토큰만 바뀌고 **전송은 안 막힌다**(`not result.uncertain`)
        · 조사가 없으면 **후보 자체가 아니다**(패턴이 `[성씨][가-힣]{2}` + 조사다)
    🔴 그 둘이 살아남은 것이 이 검사의 값이다 — **whitelist 가 성질까지 바꾸지 않았다.**
    """
    rows = select_node_rows(load_grammar_norm_corpus(), _NODE_ID)
    uncertain_rows = [row.keyword for row in rows if redact(row.keyword).uncertain]

    assert len(rows) == 7
    assert len(uncertain_rows) == 0, (
        f"오탐이 되살아났다 — `name_exclude` 에서 `이시옷`·`성립되` 가 빠졌는지 보라: "
        f"{[w[:24] for w in uncertain_rows]}"
    )
    #: 🔴 whitelist 가 든 것을 **어간 단위로** 확인한다 — 위 0건만 재면
    #: 「규칙이 통째로 죽었다」와 「이 두 어간이 빠졌다」가 안 갈린다.
    for word in ("이시옷이", "성립되는"):
        result = redact(word)
        assert "⟪확인필요⟫" not in result.masked_text, (
            f"`name_exclude` 가 안 들었다: {word}"
        )
        assert not result.uncertain, f"후보 1개인데 전송을 막았다: {word}"
    #: 조사가 없으면 후보 자체가 아니다(패턴이 `[성씨][가-힣]{2}` + 조사다) — **불변**.
    assert redact("이시옷").masked_text == "이시옷"
    assert redact("성립되").masked_text == "성립되"


def test_fortition_prompt_material_is_usable_again() -> None:
    """🔴 **그 7행이 이제 프롬프트 재료로 살아난다** — 종전 이름은
    `test_fortition_prompt_material_remains_fail_closed` 였다.

    ⚠ 🔴 **무엇을 재는 검사로 바꿨나, 그리고 왜.** 종전은 «묶으면 fail-closed 로 남는다»
    를 단언했다 — 그 문장은 **결함의 크기**를 재는 것이었고, 결함이 사라진 지금은 재현할
    대상이 없다. ⇒ **설계대로 돌아온 것**을 잰다: 라벨 + 7행을 이어 붙인 **실제 프롬프트
    재료**가 전송 가능한 상태인가.
    🔴 그 축을 고른 이유는 **B 가 다음에 할 일이 그것**이기 때문이다 — 제외 목록을 비우고
    57노드를 복원한다. 이 검사가 red 면 **그 복원이 깨진다.**
    ⚠ 이어 붙이는 것이 중요하다 — 밀도 임계는 **문장 안**에서 세므로 행 단위로만 재면
    «한 행씩은 괜찮은데 묶으면 막힌다» 를 못 본다(그게 정확히 종전의 사고였다).
    """
    rows = select_node_rows(load_grammar_norm_corpus(), _NODE_ID)
    prompt_material = "\n".join(("된소리되기", *(row.keyword for row in rows)))

    outcome = redact(prompt_material)
    assert not outcome.uncertain, (
        "fortition 프롬프트 재료가 전송 트립와이어에 걸린다 — "
        "B 축이 이 노드를 다시 제외해야 한다(99 #185)"
    )
    assert not outcome.findings, (
        f"재료에 마스킹 흔적이 남는다 — 트립와이어는 `findings or uncertain` 으로 막는다: "
        f"{[f.type for f in outcome.findings]}"
    )
