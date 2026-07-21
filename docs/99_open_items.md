# 99. 미확정 안건 추적 — 합의되면 여기와 해당 문서를 같이 갱신한다

## ★ 7/15 설계 회의 결정 로그
1. **Open-11 ✅** 수능 6영역 채택 + **문항 형식은 v1에서 mcq(객관식)만** — 수능 국어 전 문항이 객관식. short·essay는 enum 예약만(내신·자체 시험은 후순위)
2. **Open-2 ✅** 비동기 통지는 폴링·웹훅이 아니라 **Kafka** — 토픽·이벤트 스키마는 계약 v1.0 부록으로 후속
3. **B-1 ✅ 승인 + 구조 확장** — **슈퍼바이저 1 + 워커 에이전트 3**(상담팩[A] · 매핑 조사[A] · 문제 생성[B]). 슈퍼바이저 state 스키마 작성이 신규 선행 작업
4. **BE-4 ✅** 쿼터의 차단·카운트·잔여 표시 **전부 백엔드 Billing — AI는 쿼터를 알지 못한다**(meta.quota 동봉 폐기)
5. **BE-5 ✅** 월별 리포트는 학생별 N회가 아니라 **일괄 1회(Kafka 기반)** — 부분 실패 격리는 AI 내부 처리로 유지
6. **BE-7 ✅** 표준 스키마 3자 일치 원칙 합의 — 정의서 초안은 A 작성 후 백엔드 리뷰
7. **Open-1·3·4a~4d·5~8·10 ✅ 일괄 합의** — 전부 A 제안대로(push · 스토리지 URL · 주차 분할 백필 · **과제명 제공 OK(태깅 기능 생존)** · AI 자체 요약 · 10건/90일+1차 마스킹 · 표준 인증·snake_case·분리·배치 02:00→02:10→03:30 · 게이트 우선). Open-9는 "모수 미달 시 차트 생략"만 합의, 출처는 잔여
8. **타겟 협소화 ✅** — "중·고등 국어"가 아니라 **수능 대비 국어 학원(고등) 기준으로 시작.** 중등·내신 대비는 고도화 로드맵(README·CLAUDE·overview·taxonomy 반영 완료). v1 코드에 중등 분기 금지

## ★ 7/16 감지 응답 명세 확정 (`docs/part_a/09_detect_spec.md` — 백엔드 전달본)
9. **alert_context·lifecycle 신설 ✅** — `/detect` 요청에 `alert_context[]`(최근 30일 경보 이력) 신설, 응답 `signals[]`에 `lifecycle`(new·ongoing·follow_up)·`display_label` 신설. **경보 생애 판정(쿨다운 2주)을 AI가 소유**(09 §4). 반영: 04 §3.1·부록A · 05 §1 · 06_erd SIGNAL · `contracts/detection.py`
10. **observed_only 제거 ✅** — 응답에서 판단 보류 학생 목록 제거, `stats.excluded_under_2w` 숫자만. "관찰 중" 기준(재원 14일 미만)은 09 §3으로 고정, 표시는 백엔드 자체 계산. 반영: 04 §3.1 · 05 §1 · 03_usecases D1
11. **`/feedback` 보류 ✅(7/16)** — API·화면 버튼 모두 뺌(임계 캘리브레이션 재개 시 활성). `signal_id`는 향후 대비 저장 유지. 보류 기간 보정은 threshold 시트 §5 섀도 모드 수동 리뷰. 반영: 04 §3.0·§3.2 · 05 §2 · 03_usecases D3 · 04_threshold §4 · 06_erd RULE_FEEDBACK(예약)

> 규칙: 안건이 닫히면 ① 이 표의 상태를 ✅로 ② 관련 문서의 `[제안]`/`TODO(Open-n)` 제거 ③ 계약 문서는 버전 승격. **닫히기 전에는 잠정값으로 개발하되 코드에 `# TODO(Open-n)` 주석 필수.**

## A. 계약 Open 안건 (백엔드 리뷰 미팅에서 일괄 — `04_api_contract.md` §1)

| # | 안건 | 관련 문서 | 상태 |
| --- | --- | --- | --- |
| Open-1 | 스냅숏 전달 방식 | 04 | ✅ **(7/15) push** — 배치 주도권 백엔드 |
| Open-2 | ~~폴링/웹훅~~ → 비동기 통지 방식 | 04 §2.4 | ✅ **Kafka (7/15)** — 토픽 설계 후속 |
| Open-3 | Import 파일 전달 | 04·05 | ✅ **(7/15) 스토리지 URL**(입·출력 모두) |
| Open-4a | 과거 데이터 백필 | 04 | ✅ **(7/15) 주차 분할**(해시·재현성 유지) |
| Open-4b | 과제명 텍스트 제공 | 04 | ✅ **(7/15) 제공 가능 — 태깅 제안 기능 생존 확정** |
| Open-4c | 학생 요약 집계 주체 | 04 | ✅ **(7/15) AI 자체 계산** — student_summary 불필요 |
| Open-4d | comm_history 범위·마스킹 | 04 · policies/masking_redaction | ✅ **(7/15) 최근 10건·90일 + 백엔드 1차 마스킹** |
| Open-5~8 | 인증·네이밍·통합/분리·배치 시각 | 04 §5 | ✅ **(7/15) 전부 A 제안대로** — 인프라 표준·snake_case·/feedback 분리·02:00→02:10→03:30 |
| Open-9 | 전국 백분위 출처·모수 | part_a/07_report_spec | 🟠 **부분 합의(7/15): 모수 미달 시 해당 차트 생략** — 출처·갱신 주기는 백엔드 확인 잔여 |
| Open-10 | 15일 규칙 vs DataSufficiency | part_a/07_report_spec | ✅ **(7/15) 게이트 우선** |
| Open-11 | **수능 영역 enum 개정 [A+B]** | policies/taxonomy | ✅ **(7/15) 6영역 채택 · item_format은 v1=mcq만**(short·essay 예약) |
| Open-12 | 답안지 OCR 실명→alias = 백엔드 vault (P2) | policies/f17_paper_exam | ☐ |

## B. member-B(염준영) 합의 안건

| # | 안건 | 결과 반영처 | 상태 |
| --- | --- | --- | --- |
| B-1 | 지시서 개정 4건 + 에이전트 구조 | part_a/01_pipeline · policies/langgraph_state | ✅ **(7/15) 승인 — 슈퍼바이저 1 + 워커 3**(상담팩[A]·매핑조사[A]·문제생성[B]). 착수 가능, 슈퍼바이저 state 스키마가 선행 작업 |
| B-2 | 공용 계약 4파일 초안 리뷰 순서 | contracts/ | ☐ |
| B-3 | 수능 enum 확정(=Open-11) + 경계 사례 7건 판정 | policies/taxonomy · contracts/taxonomy.py | ✅ enum 확정(7/15) · ☐ 경계 사례 7건 판정은 잔여 |
| B-4 | ~~서술형 채점 분담~~ | — | ❌ **폐기(7/15)** — v1은 mcq만이라 서술형 채점 자체가 없음. F17 OCR 소유만 P2 시점에 Open-12와 함께 재론 |
| B-5 | LLM 게이트웨이 인터페이스·벤더 — **확정 전 벤더 SDK 설치 금지** | contracts/llm.py | ☐ |
| B-6 | LangSmith 도입 | runtime/ | ✅ **(7/15) 공통 1개로 도입** — 프로젝트·키 공용. 마스킹 훅은 게이트웨이 앞단(트레이스에 마스킹 통과분만) |
| B-7 | evidence resolver 주입 시그니처 | evidence/resolver.py | ☐ |

## C. 백엔드 합의 안건

| # | 안건 | 결과 반영처 | 상태 |
| --- | --- | --- | --- |
| BE-1 | 계약 v0.1 → v1.0 승격 | 04·05 | 🟠 **리뷰 완료(7/15)** — 잔여: Open-9 출처 + Kafka 토픽 스키마 부록 작성 → 채우고 v1.0 승격 커밋 |
| BE-2 | alias 발급·vault 소유 | 04 | ✅ **(7/15) 확정 — AI는 실명을 아예 받지 않는다.** 학생 공유 ID(alias)만 수신, 실명↔alias 매핑은 백엔드 전유. DTO에 실명 필드 부재를 백엔드가 코드로 보장 |
| BE-3 | 인증·인프라 | 인프라 | ✅ **(7/15) 각자 소유** — 서버 분리이므로 각 팀이 자기 인프라 관리. AI가 백엔드에 요구하는 조건 2개만 유지: 내부망 전용 호출 + AI PG 국내 리전 |
| BE-4 | 쿼터 게이팅 분계 | policies/quota_metering · part_a/06_refine_policy §5 | ✅ **(7/15) 전부 백엔드 — AI는 쿼터 무관**, meta.quota 폐기 |
| BE-5 | 월별 리포트 벌크 형태 | part_a/07_report_spec | ✅ **(7/15) 일괄 1회(Kafka)** — 백분위 출처(Open-9)는 잔여 |
| BE-6 | AI 장애 폴백 화면 문구 | policies/error_codes §6 | ✅ **(7/15) A가 문구 초안 작성**하기로 — 작성 완료(§6), 백엔드/프론트는 검수만 |
| BE-7 | 체크온 표준 스키마 공동 확정 (3자 일치) | 표준 스키마 정의서 — 미작성 | ✅ **(7/15) 원칙 합의** — ☐ 초안 A 작성→백엔드 리뷰 잔여 |
| BE-8 | suggested 확정 회신 경로 | 04 §3.3 | 🟠 **(7/15) A가 만들기로** — 규약은 계약 §3.3에 이미 있음, /confirmations 구현이 A 백로그로 확정 |
| BE-9 | Import 결과 반영 경로 | 04 §3.8 | 🟠 **(7/15) A가 만들기로** — Open-3 확정(산출물 스토리지 URL → 백엔드 F1 경로 반영)대로 A가 출력 스펙 구현 |

## D. 남은 작성물 (A 자체)

**문서 (레포 docs/)**

| 항목 | 상태 |
| --- | --- |
| 체크온 표준 스키마 정의서 (BE-7 자료) | ✅ **작성 완료 — `07_standard_schema.md`** (백엔드 리뷰 대기) |
| Kafka 토픽·이벤트 스키마 초안 (Open-2·BE-5 후속) | ✅ **초안 완료 — `08_kafka_events.md`** (백엔드 공동 확정 대기) |
| 슈퍼바이저 에이전트 state 스키마 (B-1 확장) | ✅ **초안 완료 — `policies/langgraph_state.md` §5** (B 리뷰 대기) |
| AI 장애 폴백 문구 초안 (BE-6) | ✅ **작성 완료 — `policies/error_codes.md` §6** (프론트 검수 대기) |

**코드 (첫 스프린트 백로그 — 순서 제안)**

| 항목 | 상태 |
| --- | --- |
| ① FakeSnapshot 픽스처 (05_request_json 기반) | ☐ |
| ② Alembic 마이그레이션 (06_erd 24테이블 — 서술형 폐기로 rubric 관련 필드 없음 확인) | ☐ |
| ③ tone_map.yaml · buffer_lexicon.yaml 실파일화 | ☐ |
| ④ redaction_patterns.yaml + golden/redaction 코퍼스 30건 | ☐ |
| ⑤ /confirmations 구현 (BE-8) · Import 산출물 출력 스펙 (BE-9) | ☐ |
| ⑥ Kafka consumer/producer 뼈대 (08 확정 후) | ☐ |
