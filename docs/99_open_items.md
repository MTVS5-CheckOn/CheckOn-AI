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

## ★ 7/21 에러 코드 사전 정합
12. **에러 코드 정본 = `error_codes.md`로 통일 ✅(7/21)** — 04 §2.3 표 제거(정본과 코드명·409 의미 드리프트), `IDEMPOTENCY_CONFLICT` 의미 채택(같은 키+다른 바디=거부), `QUOTA_EXCEEDED`는 AI 사전에서 제거(7/15 결정 반영 — 백엔드 Billing 선차단), `rule_skipped`→`rules_skipped` 개명(09 §3·detection.py 정합). 반영: error_codes §1·§2.3·§4·§5 · 04 §2.3

## ★ 7/22 B 크로스체킹 회신
13. **B 크로스체킹 전 항목 A 판정 ✅(7/22)** — PR #9의 `[PART_B 크로스체킹 요청]` 회신. 주요 판정: **① 상한-억제 순서 버그 수정**(lifecycle 억제를 랭킹·상한 앞으로 — 억제 후보가 TOP 슬롯 미소비) · **② score 0점 기준=원임계**(세그먼트 유효 임계 아님 — 상대 비교 일관·G11 유지) · **③ `REVISION_CONFLICT` 정본 편입**(B 규약 수용, detail.reason 내부 노출 허용) · **④ 실패 envelope도 meta.versions 조립**(계약이 정본) · **⑤ 404/422 동의 경계 구분**(조회=404·생성 선조건=422, [백엔드 확인 대기]) · **⑥ INVALID_SCHEMA=요청 계약 위반**(헤더 누락 포함) · **⑦ §4 트리에 IdempotencyConflict·LlmTimeout 편입**(adapter=runtime/errors.py) · **⑧ 부재형 신호 evidence 규칙 명문화**(관련 최근 실존 기록만, 임의 대체 금지) · **⑨ consent enum 강제 안 함**(granted만 존재·발명 금지, 비-granted는 폐기가 사양). 반영: 04_threshold §2·§3·§3.1 · 09 §2·§3·§4 · 04 §2.2·§2.3 · error_codes §1·§2.6·§4 · 신규 협의 안건 2건(아래 B-8·D⑪)
14. **ongoing 상한 제외 ✅(7/22)** — 반별 TOP 상한(3~5)을 `ongoing`(기존 미해소 경보 갱신)에 적용하면 만성 미해소 학생이 매일 슬롯을 점유해 신규 위험(new)이 영원히 잘린다. 확정: **상한은 `new`·`follow_up`에만 적용**(ongoing·R5는 상한 밖). 슬롯 = "오늘 새로 봐야 할 카드"의 예산. 반영: 04 §3 · 09 §4 · engine `_rank_with_lifecycle`

## ★ 7/23 보조 ⓐ 브리핑 문장화 (LLM 활성)

15. **브리핑 문장화(ⓐ) 구현 ✅(7/23)** — 신호 자연어화(LLM 한 줄 + 왜곡 게이트 + 템플릿 폴백). `composition/briefing.py`(A 단독). **분기표 8종:** ① LLM 실패(LlmUnavailable·LlmTimeout·ParseFailed) → **재시도 없이** 즉시 템플릿 폴백(재시도는 게이트웨이 후속) ② redaction uncertain → 미전송(fail-closed)·템플릿 ③ 왜곡 게이트 실패 → 재생성 ≤3(error_codes §3) → 소진 시 템플릿, gate_passed=false ④ ⟪⟫ 토큰 잔존 → 게이트 실패(#3) ⑤ 시간 예산 45s(호출당 10s) 소진 → 잔여 신호 템플릿 ⑥ 어떤 실패든 **감지 판정 무변** ⑦ provider settings로 fake↔openai_compat(기본 fake) ⑧ `fallback_used=true`가 실의미 획득. 게이트 = 결정론(숫자 EXACT 대조[report numbers_used 선례]·금칙어[05 §4 A군 복사]·⟪⟫토큰·길이). 반영: 04 §2.4(detect 60s) · 09 §3 brief 주석
    - **후속 안건:** ⓐ **LLM_CALL DB 적재** — ERD 테이블 존재하나 이번 범위 밖(outcome은 로그만). ⓑ **게이트웨이 도입 시 재시도 이관** — 현재 재시도 없음(즉시 폴백), tenacity 재시도는 gateway 소유. ⓒ **금칙어 단일화** — `composition/briefing_forbidden.yaml`은 05 §4 원본 복사본, **D-③(buffer_lexicon 실파일화) 때 단일 파일 참조로 전환**(두 곳 분기 방지). ⓓ **브리핑 프롬프트 레지스트리 이관** — 현재 briefing이 템플릿 직접 로드, B의 `llm/prompts` 레지스트리 도입 시 등록 이관.

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
| B-5 | LLM 게이트웨이 인터페이스·벤더 | contracts/llm.py | ✅ **(7/23) 벤더 확정 — 팀 로컬 OpenAI 호환 서버(Gemma 계열).** SDK 설치 금지 해제. `openai` SDK는 `llm/providers/` 안에서만 import 허용(capability·contracts 직접 import 금지 — 벤더 독립 유지). provider 어댑터는 **A 초안 PR + B 승인**(llm/ 소유 §4). 게이트웨이 재시도·라우팅은 B |
| B-6 | LangSmith 도입 | runtime/ | ✅ **(7/15) 공통 1개로 도입** — 프로젝트·키 공용. 마스킹 훅은 게이트웨이 앞단(트레이스에 마스킹 통과분만) |
| B-7 | evidence resolver 주입 시그니처 | evidence/resolver.py | ☐ |
| B-8 | **VersionSet capability별 validator [양자]** | contracts/execution.py | ☐ **(7/22 등록)** — A 실행에 B 버전 키 혼입 금지·B 실행에 B 키 필수를 `ExecutionContext` 조립 경계에서 강제할지. `execution.py`(양자 승인 파일) 변경이라 A·B 강제 수준 합의 필요. 04 §2.2 크로스체킹 회신 |

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
| BE-10 | R2 제출률 분모(주간 기대 과제 수) 제공 | 04 §1 R2 · 09 §2 · contracts/detection.py | ☐ **(7/21 등록)** — `submit_drop_pp` 경로 활성화에 필요. 현재 스냅숏엔 submit 이벤트 유무만 있어 제출률 분모가 없음 → v0는 `consecutive_missing`만 동작. 백엔드가 주간 기대 과제 수를 요청 필드로 제공하면 활성화. 계약 필드 추가라 협의 대상 |

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
| ① FakeSnapshot 픽스처 (05_request_json 기반) | ✅ 구현 완료 — `evaluation/fake_snapshot.py`(A 소유·프로덕션 격리), B 위치 검토 완료(7/22) |
| ② Alembic 마이그레이션 (06_erd 24테이블 — 서술형 폐기로 rubric 관련 필드 없음 확인) | ☐ |
| ③ tone_map.yaml · buffer_lexicon.yaml 실파일화 | ☐ |
| ④ redaction_patterns.yaml + golden/redaction 코퍼스 30건 | ✅ **(7/23) 구현 완료** — `runtime/redaction.py`(순수 함수 `redact()`, 파이프라인 §2·토큰 규격 §1·단방향) + `redaction_patterns.yaml`(P1ⓑ~P8) + 코퍼스 30건 + CI 게이트(미탐 0·오탐 ≤2). **결정 로그:** P8=한글숫자 디코딩 후 P2(§2 개정) · 스코어링 밀도=미확정 인명후보 1개→토큰·≥2→문장 통째 · 명부(P1ⓐ) 없어 별명·영문명·성생략은 fail-closed ⟪확인필요⟫. 훅 배선(§3 5곳)·§4 도구 반환은 각 소비 기능 붙일 때(범위 밖) |
| ⑤ /confirmations 구현 (BE-8) · Import 산출물 출력 스펙 (BE-9) | ☐ |
| ⑥ Kafka consumer/producer 뼈대 (08 확정 후) | ☐ |
| ⑦ capped_out '다음 날 재평가 대기열'(04 §3) — 순수 함수 범위 밖이라 v0는 집계만, API/저장 계층 도입 시 구현 | ☐ |
| ⑧ **api/ 계층 신설** — HTTP 노출 계층(app·envelope=공통계약, 라우터=capability 오너) | ✅ B 확인 완료(7/22) — `api/app.py`·`api/envelope.py`를 §4 양자 승인 목록에 편입(총 9곳). 공통 wire 간극은 각 원본의 `[PART_B 크로스체킹 요청]`으로 별도 추적 |
| ⑨ 멱등 저장소 DB 교체 — v0 인메모리(재시작 소실·멀티워커 비공유) → 영속화 | ✅ **구현 완료(D-② 커밋④)** — `IDEMPOTENCY_RECORD`(06_erd) + PG 저장소. 유니크 `(tenant_id, endpoint, idempotency_key)`, TTL 30일, 캐시 저장·조회 실패=fail-open. 인메모리 테스트용 유지(`store_backend` 선택). 실 PG 왕복은 ⑫ |
| ⑫ CI에 PG 서비스 추가 — D-② 저장 계층은 fake+offline SQL로 검증(로컬 psql 부재). 실 PG 통합(docker) 테스트는 CI 여건 확인 후 `integration` 마커로 추가. 마이그레이션 실측은 실배포 전 로컬 PG에서 upgrade/downgrade 왕복 | ☐ **(7/22 등록)** |
| **D-②b baseline read-path** — 판정 시 `FEATURE_WEEK` 축적분에서 baseline을 조립해 엔진에 **선택 입력으로 주입**(순수 함수 유지 — 시그니처는 추가 인자, 미주입 시 요청 구동 동일). | ✅ **구현 완료(7/23)** — `engine.detect(stored_features=…)` + `detection_store.load_feature_weeks`(fail-closed) + `features.week_features_from_metrics/merge_weeks`(같은 주 요청 승·판정 창까지 병합). 라우터가 요청 students 전원분 조회 주입. 멀티데이(day1 10주 → day2 1주 증분) e2e + 대조군(증분 == 통짜 판정 일치) 테스트. 골든·데모 무변경(미주입 바이트 동일). **증분 전용 전환은 백엔드 일정 합의 후 별도 통보**(그 전 10주 동봉 유지, 10주 와도 동작 동일 — 09 §2 ① 정밀화). |
| ⑩ 섀도 모드 표시 방식 — `error_codes.md` §2.3에 `shadow: true` 행이 있으나 09 §3 응답엔 없음. 섀도 구현 시점(D-② 후)에 09 응답 편입 vs 운영 설정 결정 + 두 문서 정합 | ☐ |
| ⑪ 부재형 신호(R2·R3·R5)의 evidence 전무 한계 — 관련 실존 기록이 전무하면 신호를 생성하지 않는다(계약상 evidence ≥1). 집계/상태 record 참조를 evidence로 허용할지 D-②(저장 계층) 시점 결정. 09 §3 A 판정(7/22) | ☐ |
| ⑬ **pgvector 도입 후보** — 7/25 docker-compose에서 `postgres:16` 유지 확정(현 06_erd에 벡터 컬럼 없음). 향후 유사 학생·유사 오답 패턴 검색 등 임베딩 수요가 생기면 pgvector 확장 도입을 검토(이미지·ERD 함께 개정). | ☐ **아이디어(수요 발생 시 · P2 후보)** |
