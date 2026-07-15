# [체크온] 쿼터·미터링 명세 v0 — API 계약 부록 B `[제안]`

> **(7/15 회의 확정 — 문서 지위 변경)** 쿼터의 **차단·카운트·잔여 표시 전부 백엔드 Billing 소유. AI는 쿼터를 알지 못한다** — `meta.quota` 동봉 폐기, 한도 소진 시 백엔드가 호출 자체를 안 보냄. 따라서 §1~§4는 **백엔드 집행 참고용**이고, AI 저장소에 실제로 남는 것은 §5의 **LLM 원가 관측**(비용·토큰 기록 — ARPU 20% 검증용)뿐이다.

---

## 1. 요금표 → 할당 매핑 (기획서 확정 수치)

| | Free | Standard (월 29,000) | Pro (월 49,000) | 학원급 |
| --- | --- | --- | --- | --- |
| 학생 수 | 20명 | 50명 | 100명 | 협의 |
| 문항 생성 (`problem`) | 100/월 | 300/월 | 500/월 | 협의 |
| 상담 초안 (`draft`) | 100/월 | 300/월 | 500/월 | 협의 |
| 단건 리포트 (`report_single`) | 10/월 | 30/월 | 50/월 | 협의 |
| 단건 리포트 일일 상한 | 5/일 `[제안]` | 10/일 | 15/일 `[제안]` | 협의 |
| 월별 리포트 일괄 | — (유료 전용) `[제안]` | 월 1회 | 월 1회 | 협의 |
| 상담팩 에이전트 | — | — | 월 2회 | 협의 |

리셋: 월 할당 = 매월 1일 00:00 KST · 일일 상한 = 매일 자정 KST(클로드식 — 롤링 윈도 없음).

## 2. 소모 판정표

| 행위 | limit_kind | 소모 |
| --- | --- | --- |
| 초안 생성(reply·counsel_pack_single) | `draft` | 1 |
| **refine 1턴** | `draft` | 1 (사전 정적 차단 턴은 0 — 핑퐁 정책서 §5) |
| 롤백 `revert_to` | — | 0 |
| 문항 세트 생성 | `problem` | 문항 수 기준 `[B 확인 — 세트당 1인지 문항당 1인지]` |
| 단건 리포트 | `report_single` | 1 (+일일 상한 동시 판정) |
| 월별 리포트 일괄·상담팩 | — | 할당 미소모(월 횟수제 별도) |
| 감지·분류·태깅·라벨 제안·브리핑 | — | **무제한**(제품 신뢰 기반 기능 — 미터링만) |

## 3. `meta.quota` 응답 스키마 (모든 생성 응답에 동봉)

```json
"meta": {
  "quota": {
    "draft":         { "limit": 300, "used": 41,  "daily_used": 7 },
    "problem":       { "limit": 300, "used": 120 },
    "report_single": { "limit": 30,  "used": 12, "daily_limit": 10, "daily_used": 2 },
    "resets_at": { "monthly": "2026-08-01T00:00:00+09:00", "daily": "2026-07-15T00:00:00+09:00" }
  }
}
```

UI 표기 원칙: **"이번 달 상담 초안 259/300 남음"** — 턴·세션 개념 노출 금지.

## 4. 429 `QUOTA_EXCEEDED` detail 규격

```json
{ "error": "QUOTA_EXCEEDED",
  "detail": { "limit_kind": "report_single", "scope": "daily",
              "limit": 10, "used": 10, "resets_at": "2026-07-15T00:00:00+09:00" } }
```

`limit_kind: draft | problem | report_single` × `scope: monthly | daily` — UI 안내 문구가 이 조합으로 분기("내일 자정 이후 가능" vs "다음 달 1일 초기화 / 플랜 업그레이드").

## 5. `usage_daily` 미터링 테이블 (AI PG)

```sql
CREATE TABLE usage_daily (
  tenant_id   varchar NOT NULL,
  date        date    NOT NULL,          -- KST 기준
  draft_used  int     DEFAULT 0,         -- refine 턴 포함
  problem_used int    DEFAULT 0,
  report_single_used int DEFAULT 0,
  batch_jobs  jsonb   DEFAULT '{}',      -- {"monthly_report": 1, "counsel_pack": 0}
  unlimited_calls jsonb DEFAULT '{}',    -- {"detect":1,"classify":14,"tag":9} — 원가 관측용
  PRIMARY KEY (tenant_id, date)
);
```

- AI가 소모 발생 시 upsert — **진실의 원본(source of truth)은 백엔드 Billing의 카운터**이고 이 테이블은 대사(reconciliation)·원가 분석용 `[제안 — BE-4]`. 양쪽 카운트 불일치 시 백엔드 값 우선.
- `unlimited_calls`는 무제한 기능의 원가 추적 — LLM 예산 상한(ARPU 20%) 검증 데이터.

## 6. 백엔드 합의 필요 (BE-4 안건 그대로)

1. 카운트 시점 — 202 접수 시 선차감 vs 완료 시 확정 차감(실패 시 환급). **A 제안: 선차감 + `failed` 시 자동 환급**(중복 방지 단순).
2. `meta.quota`의 값 출처 — 백엔드가 요청 헤더로 잔여를 넘겨주면 AI는 에코만(제안) vs AI가 Billing 조회.
3. 학원급(협의) 플랜의 할당 표현 — 커스텀 limit을 계약이 어떻게 받나.
