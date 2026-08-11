<!--
⚠ 양자 승인 12곳(`docs/02_ownership.md` §4 정본: 공통 contracts 6 ·
   evidence/models · runtime/metrics · api/app·envelope · db/models·base) 또는
   상대 capability 파일을 건드렸다면, 아래 첫 줄에 이렇게 적어주세요 —
   03_coding_rules §8

   ⚠ 승인 필요: [파일명] (안건번호 — 리뷰어 이름)

   골든셋 기대값을 수정했다면 PR 제목에 [golden]을 붙여주세요 — §7
-->

## ⭐ Key Changes
> 핵심 변경 사항을 적어주세요.
1.
2.
3.

<br>


## ✅ 확인 방법
> 리뷰어가 직접 돌려볼 수 있도록 실행 방법을 적어주세요.
-

<br>

## 👪 To Reviewers
>

<br>

## 📎 참조한 문서 · 불변식
> 구현 전 읽은 문서와 준수한 불변식 번호를 적어주세요 (사람·에이전트 공통 — 03_coding_rules §9).
> 예: `docs/06_erd.md` AI_RUN · `policies/taxonomy.md` §4 / 불변식 1·4·8
-

<br>

## 📋 체크리스트
- [ ] **PR 올리기 전 로컬 검증 통과** — `uv run --frozen python -m ai.evaluation.pre_pr_verify` (§8). 실행 OS와 결과(기본 pytest · integration pass/skip)를 위 확인 방법에 적었다
- [ ] PR은 한 가지 일만 · 목표 ≤400줄 (생성 파일 제외) — §8
- [ ] 테스트 포함 — 최소 정상 1 + 경계 1 + 실패 1 (§7)
- [ ] 하드코딩 없음 — 임계값=DB · 규칙=yaml · 프롬프트=템플릿 · 설정=Settings (§1)
- [ ] `TODO`에 안건 번호를 달았다 (`# TODO(Open-11):`) — §8
- [ ] (새 라이브러리 추가 시) 3줄 사유 — 유지보수 · 무게 · 라이선스 (§1b)

<!--
막판 확인 — 아래에 해당하면 리뷰 반려 대상입니다 (CLAUDE.md §1 · 03_coding_rules §9)
· LLM이 수치·판정을 확정하는 코드
· evidence 없이 생성되는 산출물
· GateRejected를 5xx로 올리는 코드 (게이트 거부 = 정상 200 + status)
· 게이트 스킵 플래그 · 마스킹 off 옵션 · 테스트 skip
· 상한 없는 루프 · capability 간 직접 import
-->
