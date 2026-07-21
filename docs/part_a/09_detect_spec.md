# [체크온] 위험신호 감지 API 명세 — 백엔드 구현 가이드 (AI 파트 확정본)

> **읽는 법:** 이 문서는 협의안이 아니라 **AI 파트 확정 스펙**입니다. 아래대로 구현해 주세요. 요청(§2)은 백엔드가 만들어 보내는 것, 응답(§3)은 AI가 돌려주는 것, §3의 `[저장]` 표시 필드를 Alert 테이블에 넣으면 S1(오늘의 브리핑) 화면 재료가 전부 갖춰집니다.
> 작성: 박진희(AI-A) · 대상: 백엔드 · 문의는 저에게

---

## 1. 신호 종류 — 6개뿐입니다 (AI 확정값)

AI가 보내는 신호는 아래 6종이 전부입니다. `signal_type`은 코드에서 분기할 때 쓰는 값이고, **화면에 보여줄 한글 문구(`display_label`)까지 응답에 실어 보내므로 백엔드·프론트에서 문구 매핑 테이블을 만들 필요가 없습니다.** 색·아이콘만 프론트가 정하면 됩니다.

| rule_id | signal_type | display_label | 이 신호가 뜨는 조건 |
| --- | --- | --- | --- |
| R1 | `acc_drop` | 정답률 하락 | 정답률이 그 학생 평소보다 크게 떨어진 상태가 2주 연속 |
| R2 | `submit_drop` | 제출 저조 | 과제 제출률 하락 또는 연속 미제출 |
| R3 | `volume_gap` | 학습 공백 | 주간 학습량이 평소의 40% 미만으로 급감 |
| R4 | `hidden_risk` | 숨은 위기 | **점수는 멀쩡한데 풀이 시간이 급증** — 우리 제품의 핵심 신호 |
| R5 | `return_care` | 복귀 케어 | 휴원했다 복귀한 첫 주 (점수와 무관한 케어 신호) |
| R6 | `type_bias` | 유형 편중 | 특정 영역×유형(예: 문학×추론)에 오답이 몰림 |

- 새 신호가 추가되거나 문구가 바뀌면 AI가 미리 알리고 배포합니다. 응답에 문구가 실려 있으니 **모르는 signal_type이 와도 display_label만 그대로 표시하면 화면이 깨지지 않습니다.**
- R5(복귀 케어)는 TOP 3~5 상한과 별도로 옵니다 — 경보라기보다 체크리스트 성격이라, 카드를 경보와 구분되게 표시해 주세요.

## 2. Request — `POST /v1/detect` (백엔드 → AI, 매일 새벽 02:10 1회)

헤더: `X-Tenant-Id`(강사 alias) · `X-Request-Id` · `Idempotency-Key = tenant + week_start` (같은 키로 재호출하면 같은 결과 — 배치 재시도 안전).
**실명·연락처 필드는 어디에도 없습니다** — 보내기 전에 전부 가명(alias)으로 치환해 주세요.

```json
{
  "snapshot_meta": {
    "week_start": "2026-07-13",
        // 이번 주 월요일 날짜. AI가 "이번 주 vs 평소" 비교의 기준으로 쓰는 키

    "snapshot_hash": "sha256:9f2c...",
        // 이 요청 본문 전체(alert_context 포함)를 해시한 값. 백엔드가 계산해서 보냄.
        // 용도: 나중에 "그날 왜 그런 판정이 나왔나" 재현·감사할 때 같은 입력임을 증명

    "term_context": "normal",
        // 학사 상황. normal(평상시) | new_term(신학기 — 반 재편성 직후라 AI가 기준을 느슨하게 잡음)
        //          | vacation(방학 — 과제 관련 신호 R2·R3를 아예 안 봄. 오경보 방지)

    "classes": [ { "class_ref": "cl_a1" }, { "class_ref": "cl_b2" } ]
        // 이 강사의 반 목록. "반마다 TOP 3~5개" 상한을 계산하는 데 필요
  },

  "students": [
    {
      "student_ref": "st_8f2a",
          // 학생 가명 ID. 실명↔가명 매핑표는 백엔드만 보유 — AI는 이 값만 앎

      "class_ref": "cl_a1",

      "enrolled_weeks": 14,
          // 등원한 지 몇 주째인지. 2 미만이면 AI가 이 학생을 판정에서 조용히 제외
          // ("관찰 중" 화면 표시는 백엔드가 enrolled_at으로 직접 계산 — AI는 목록을 안 돌려줌)

      "status": "enrolled",
          // enrolled(재원) | paused(휴원 중 — 판정 제외) | returned(복귀 첫 주 — R5 복귀 케어 발동)

      "consent": "granted"
          // 개인정보 동의 상태. granted가 아니면 이 학생의 learning_events가 와도 AI가 전부 버림
    }
  ],

  "learning_events": [
    // 지난 주차에 새로 생긴 학습 기록만 (매번 전체 재전송 아님)
    {
      "record_id": "le_1029",
          // 백엔드 MySQL의 원본 기록 PK. AI가 "근거"로 이 ID를 되돌려주고,
          // 강사가 [근거 보기]를 누르면 백엔드가 이 ID로 원본을 보여줌. 절대 바뀌면 안 되는 키

      "student_ref": "st_8f2a",

      "type": "solve",
          // solve(문제 풀이) | submit(과제 제출) | attend(출석) | consult(상담)

      "occurred_at": "2026-07-15T19:20:00+09:00",

      "correct": false,
          // solve일 때만. 정답률 계산 재료 (R1 정답률 하락, R6 유형 편중)

      "duration_sec": 183,
          // solve일 때만, 풀이에 걸린 시간(초). 없으면 null로 —
          // null이면 그 학생은 R4(숨은 위기) 판정을 못 하니 가능하면 채워서 주세요

      "passage_word_count": 812,
          // 지문이 있는 문항만. 지문 어절 수 — R4가 "시간 ÷ 지문 길이"로 정규화할 때 씀
          // (긴 지문이라 오래 걸린 건지, 진짜 느려진 건지 구분하는 핵심 필드)

      "area_tag": "reading",
          // 수능 영역 (없으면 생략 가능 — 태깅 제안이 나중에 채움)
          // reading(독서) | literature(문학) | speech(화법) | writing(작문) | language(언어/문법) | media(매체)

      "subject_track": "common",
          // common(공통과목) | elective(선택과목). 없으면 생략 가능

      "type_tag": "infer",
          // 문항 유형: fact(사실) | infer(추론) | critic(비판) | concept(개념). R6의 판정 축

      "item_format": "mcq",
          // 문항 형식. v1은 전부 mcq(객관식) — short·essay는 나중을 위한 예약값

      "assignment_title_text": "6월 모의고사 비문학 대비 #3",
          // 과제/시험 이름 원문. 태그 자동 제안 기능의 입력 (7/15 제공하기로 확정된 필드)

      "source": "trackB"
          // 기록이 들어온 경로: trackA(자료 업로드) | trackB(강사 채점 입력) | studentHome(학생 숙제 앱)
    }
  ],

  "alert_context": [
    // ★ 최근 30일 경보 이력. 백엔드 Alert 테이블에서 뽑아서 보내주세요.
    // 이게 있어야 AI가 "이건 새 경보인지 / 어제 것의 연속인지 / 해소됐다 재발한 건지"를
    // 판정해서 응답의 lifecycle 필드로 알려줄 수 있습니다 (§4 규칙표 참조)
    {
      "student_ref": "st_8f2a",
      "signal_type": "hidden_risk",     // §1의 6값 중 하나
      "status": "open",                 // open(아직 미해결) | resolved(강사가 해소 처리함)
      "resolved_at": null,              // resolved일 때 해소 일시 — "해소 후 2주" 쿨다운 계산 기준
      "followed_up": false              // 해소 후 '팔로업 카드'가 이미 한 번 나갔는지 (중복 방지용)
    }
  ]
}
```

## 3. Response — AI → 백엔드

표시 규칙: `[저장]` = Alert 테이블에 그대로 저장할 것 · `[표시]` = 화면까지 그대로 나가는 값 · `[로그]` = 저장만 하고 화면엔 안 씀.

```json
{
  "data": {
    "signals": [
      // 오늘의 위험신호. 반별 TOP 3~5 선별과 정렬이 이미 끝난 상태 —
      // 백엔드는 순서 그대로 저장하고, 프론트는 순서 그대로 그리면 됩니다
      {
        "signal_id": "0a1b2c3d-…",
            // [저장] AI 쪽 신호 ID. 이 판정에 대한 추적·문의 대응용 키 — Alert 행에 함께 저장
            // (향후 강사 평가 기능이 붙으면 이 ID로 회신하게 되므로 지금부터 저장해 두면 좋음)

        "student_ref": "st_8f2a",
            // [저장] 가명 ID — 백엔드가 vault에서 실명·student_id로 바꿔서 화면에 연결

        "class_ref": "cl_a1",
            // [저장]

        "rule_id": "R4",
            // [로그] 내부 규칙 번호 (§1 표) — 통계·디버깅용, 화면엔 안 씀

        "signal_type": "hidden_risk",
            // [저장] §1의 6값 중 하나 — 코드 분기·통계용

        "display_label": "숨은 위기",
            // [저장][표시] 화면에 그대로 쓸 한글 문구 (AI 확정) — 프론트는 이 값을 뱃지에 그대로

        "score": 0.78,
            // [로그] 0~1 심각도 점수. 정렬 근거용 — 화면엔 노출하지 마세요 (숫자가 학생에게 낙인이 됨)

        "rank": 1,
            // [저장][표시] 반 안에서의 우선순위 (1이 제일 위)

        "lifecycle": "new",
            // [저장] ★ 이 신호를 어떻게 처리할지 AI가 판정한 결과 — 백엔드는 이대로만 처리:
            //   new       → 새 Alert 생성
            //   ongoing   → 같은 유형의 미해결 Alert가 이미 있음. 새 카드 만들지 말고
            //               기존 Alert의 brief·evidence만 오늘 것으로 교체
            //   follow_up → 해소했던 문제가 2주 안에 다시 보임. 새 경보가 아니라
            //               "해소했던 신호가 다시 보여요" 팔로업 카드 1회로 표시하고,
            //               해당 이력의 followed_up을 true로 기록
            // (판정 기준 상세는 §4)

        "brief": {
          "text": "비문학 지문을 붙잡는 시간이 3주째 늘고 있어요 — 정답률은 아직 버티는 중이에요.",
              // [저장][표시] 브리핑 카드에 그대로 실을 한 줄. AI가 수치 왜곡 검사를 통과시킨
              // 문장만 보내므로 가공 없이 노출해도 안전
          "gate_passed": true,
              // [로그] 왜곡 검사 결과
          "fallback_used": false
              // [저장] true면 AI 문장이 검사에 걸려 안전한 템플릿 문장으로 대체된 것.
              // 화면 표시는 동일 — QA·품질 추적용으로만 저장
        },

        "evidence": [
          // [저장][표시] 이 신호의 근거. 항상 1건 이상 — 근거 없는 신호는 AI가 아예 못 만듦
          {
            "source_table": "learning_event",
                // [로그] 원본이 어느 테이블인지 힌트
            "record_id": "le_1029",
                // [저장] 백엔드 MySQL 원본 PK — 강사가 [근거 보기] 클릭하면 이 ID로 원본 조회
            "summary": "7/3 숙제 지연 제출"
                // [표시] 근거 목록에 바로 보여줄 한 줄 요약 (AI 생성)
          },
          { "source_table": "learning_event", "record_id": "le_1077", "summary": "7/8 비문학 #3 풀이 9분(평소 4분)" }
        ]
      }
    ],

    "stats": {
      // [로그] 운영 지표 — 화면에 안 나감. 백엔드 로그/대시보드용으로 저장만
      "students_evaluated": 58,      // 이번에 실제로 판정한 학생 수 (동의 있음 + 재원 2주 이상)
      "signals_raised": 3,           // 상한 적용 후 최종 신호 수
      "excluded_under_2w": 4,        // 재원 2주 미만이라 제외된 학생 수 — 백엔드 '관찰 중' 계산과 맞는지 대조용
      "capped_out": 2,               // 신호가 됐지만 TOP 3~5 상한에 밀린 수
      "rules_skipped": [             // 데이터가 없어서 판정 못 한 규칙 — 데이터 품질 모니터링용
        { "rule_id": "R4", "reason": "duration_missing", "students": 5 }
            // 예: 풀이시간(duration_sec)이 없어서 5명은 R4 판정 불가였음
      ]
    }
  },
  "error": null,
  "meta": {
    "execution_id": "uuid",
        // [저장] 이 실행의 추적 ID. 나중에 "이 경보 왜 떴어요?" 문의가 오면
        // 이 ID로 AI 로그를 역추적할 수 있으니 Alert에 같이 저장
    "versions": { "pipeline": "1.0.0", "engine": "rule-1.3", "threshold": "v4", "contract": "1.0" }
        // [로그] 어떤 버전의 규칙으로 판정했는지 — 재현성 근거
  }
}
```

### Alert 테이블 저장 정리

```
AI 응답에서 복사:   signal_id · student_ref(→student_id 변환) · class_ref · signal_type
                   · display_label · lifecycle · rank · brief.text · brief.fallback_used
                   · evidence(JSON 컬럼 또는 자식 테이블) · execution_id · rule_id · score
백엔드가 덧붙임:    alert_id(자체 PK) · status(=raised) · created_at
상태 전이(백엔드):  raised → acknowledged(확인) → intervened(개입) → resolved(해소)
                   resolved/dismissed는 브리핑 조회에서 제외 — 해소된 학생은 화면에서 사라짐(낙인 방지)
```

### 관찰 중(신규생) 처리

AI는 재원 2주 미만 학생을 판정에서 조용히 제외하고, **목록은 돌려주지 않습니다.** 브리핑의 "관찰 중" 섹션은 백엔드가 `enrolled_at`으로 직접 계산해 주세요. 기준은 이 문서로 고정: **재원 14일 미만 = 관찰 중.** (바뀌면 AI가 문서 개정으로 알립니다.)

## 4. lifecycle 판정 기준 — AI가 이렇게 정합니다 (백엔드는 결과대로 처리만)

§2의 `alert_context`를 입력으로 받아 AI가 판정합니다. 경보 정책(쿨다운 2주 등)은 AI 소유 설정값이라, 바뀌면 문서 개정으로 알립니다.

| 이력 상황 | AI 판정 | 백엔드가 할 일 |
| --- | --- | --- |
| 같은 학생·같은 유형의 open 경보가 이미 있음 | `ongoing` | 기존 Alert의 brief·evidence만 교체 (새 카드 없음) |
| 해소(resolved) 후 **2주 이내** 같은 유형 재발 + 팔로업 아직 안 나감 | `follow_up` | 팔로업 카드 1회 + `followed_up=true` 기록 |
| 해소 후 2주 이내 + 팔로업 이미 나감 | (응답에서 제외) | 없음 — 브리핑에 안 뜸 |
| 해소 후 2주 지나서 같은 유형 재발 | `new` | 새 Alert 생성 |
| 이력 없음 또는 다른 유형 | `new` | 새 Alert 생성 — 다른 문제는 쿨다운 없이 바로 알림 |
