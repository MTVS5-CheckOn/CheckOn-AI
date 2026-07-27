# 08. Kafka 토픽·이벤트 스키마 초안 v0 — Open-2·BE-5 후속 `[백엔드 공동 확정 대상]`

> **7/15 확정 반영:** 비동기 완료 통지 = Kafka(Open-2) · 월별 리포트 벌크 = Kafka 일괄 1회(BE-5). 이 문서는 A의 초안 제안 — 토픽 이름·파티션 수·클러스터 설정은 백엔드(Kafka 운영 주체)가 최종 결정. 확정되면 계약 v1.0의 부록 B로 편입.
>
> **7/27 AI 내부 계약 확정:** 워커 3종의 실행 정본은 영속 `WorkerJob`, 공통 phase는 `queued|leased|running|paused|succeeded|failed|cancelled`다. terminal phase 전이와 outbox 기록은 한 트랜잭션이며, 워커는 terminal 이벤트를 직접 발행하지 않는다.

---

## 1. 어디까지 REST, 어디부터 Kafka인가 `[제안]`

| 경로 | 프로토콜 | 이유 |
| --- | --- | --- |
| 동기 요청 (detect·classify·tags·feedback·confirmations) | **REST 유지** | 즉답 필요 — 바꿀 이유 없음 |
| 비동기 작업 **기동** (drafts·imports·agents·problems) | **REST 유지(202)** | 멱등키·검증 응답이 즉시 필요 |
| 비동기 작업 **완료 통지** | **Kafka** (AI → 백엔드) | 7/15 확정 — 폴링 제거. GET 조회는 디버그·복구용 보조로만 |
| **월별 리포트 벌크** | **Kafka 양방향** (요청·결과 모두) | 7/15 확정 — 대량 배치는 스트림이 자연스러움 |

## 2. 토픽 제안 (이름은 백엔드 컨벤션에 맞춰 조정)

| 토픽 | 방향 | key | 이벤트 |
| --- | --- | --- | --- |
| `checkon.ai.job-events.v1` | AI → 백엔드 | `tenant_id` | draft/import 및 공통 WorkerJob의 pause·terminal 통지 |
| `checkon.report.monthly-requests.v1` | 백엔드 → AI | `tenant_id` | 매월 1일 벌크 요청 — **테넌트당 1건**(대상 학생 목록 포함) |
| `checkon.report.monthly-results.v1` | AI → 백엔드 | `tenant_id` | **학생별 1건** 결과 — 부분 실패 격리 |

- key=tenant_id → 테넌트 내 순서 보장 + 파티션 분산.
- 버전은 토픽 이름 suffix(v1) — 스키마 파괴 변경 시 v2 토픽 신설(계약 하위호환 규칙과 동일).

## 3. 이벤트 공통 envelope `[제안]`

```json
{
  "event_id": "uuid",                  // 영속 outbox ID — 재전송에도 같은 값
  "event_type": "worker_job.succeeded", // 아래 §4 목록
  "occurred_at": "2026-08-01T03:12:00+09:00",
  "tenant_id": "t_9a2f",
  "schema_version": "evt-1",
  "payload": { }                       // 타입별 — §4
}
```

## 4. 이벤트 타입·페이로드

| event_type | payload 핵심 |
| --- | --- |
| `draft.completed` | `{ draft_id, kind, status(generated·template_only·rejected_insufficient), student_ref }` — 본문은 미포함, GET으로 조회(이벤트에 개인정보성 텍스트 안 실음 `[제안]`) |
| `draft.failed` | `{ draft_id, reason }` |
| `import.preview_ready` / `import.done` / `import.failed` | `{ job_id, status, row_total?, row_ok?, output_url? }` |
| `worker_job.progress` | `{ job_id, execution_id, worker_kind, operation, phase: "running", progress }` — 선택(UI 진행바용, 주기 발행) |
| `worker_job.paused` | `{ job_id, execution_id, worker_kind, operation, phase: "paused", checkpoint_ref }` — 비terminal, resume 가능 |
| `worker_job.succeeded` | `{ job_id, execution_id, worker_kind, operation, phase: "succeeded", result_ref }` |
| `worker_job.failed` | `{ job_id, execution_id, worker_kind, operation, phase: "failed", error_code }` |
| `worker_job.cancelled` | `{ job_id, execution_id, worker_kind, operation, phase: "cancelled" }` |
| `report.monthly.requested` (백엔드→AI) | `{ batch_id, month: "2026-07", students: [{ student_ref, guardian_ref, label_snapshot, benchmarks }] }` — **15일 규칙 적용된 최종 목록** |
| `report.monthly.result` (AI→백엔드) | `{ batch_id, student_ref, draft_id?, status(generated·rejected_insufficient·failed), reason? }` — 학생별 1건 |

`worker_job.*` payload에는 워커 산출물 본문이나 도메인 결과를 복제하지 않는다. 백엔드는 `result_ref`로 결과를 조회한다. `partial_success`·`rejected_insufficient`·`verification_unavailable` 같은 도메인 상태는 `worker_job.failed`의 사유가 아니며, 유효한 결과를 저장했다면 `worker_job.succeeded`로 통지한다.

## 5. 운영 규약 `[제안 — 백엔드 확정]`

- **at-least-once 전제** — 모든 컨슈머는 `event_id`로 멱등 처리한다. exactly-once 전송에 기대지 않는다.
- WorkerJob terminal phase 전이와 outbox 행 생성은 같은 AI PostgreSQL 트랜잭션에서 처리한다. `(job_id, terminal_phase)`는 유일하며 재발행·브로커 재시도에도 저장된 동일 `event_id`를 사용한다.
- `succeeded`·`failed`·`cancelled` 이후 다른 terminal 이벤트로 바꾸지 않는다. 워커가 Kafka에 직접 발행하는 경로는 금지한다.
- 재시도 소진 이벤트는 **DLQ** 토픽(`*.dlq`)으로 — 방치 알럿은 각자 소유.
- 벌크 요청(테넌트당 1건)이 5MB 초과하면 학생 목록을 스토리지 URL로 대체(감지 스냅숏과 동일 패턴).
- AI 쪽 구현: `aiokafka` — 토픽 확정 전에는 consumer/producer 인터페이스만(CLAUDE.md §4).
- 개인정보: 이벤트 페이로드에 자유 텍스트(문의 원문·초안 본문) **미포함** — ID 참조만. 본문은 REST GET.

## 6. 백엔드와 확정할 것 (이 문서의 열린 항목)

- [ ] 토픽 네이밍 컨벤션·파티션 수·보존 기간
- [ ] `worker_job.progress` 발행 주기(매 학생·문항? n초 스로틀?)
- [ ] 백엔드가 `worker_job.*` 공통 payload와 `result_ref` 조회 방식 수용
- [ ] 벌크 요청의 인증(REST 헤더에 상응하는 이벤트 서명 여부)
- [ ] DLQ 모니터링 소유
