# 99. 미확정 안건 추적 — 합의되면 여기와 해당 문서를 같이 갱신한다

> 규칙: 안건이 닫히면 ① 이 표의 상태를 ✅로 ② 관련 문서의 `[제안]`/`TODO(Open-n)` 제거 ③ 계약 문서는 버전 승격. **닫히기 전에는 잠정값으로 개발하되 코드에 `# TODO(Open-n)` 주석 필수.**

## A. 계약 Open 안건 (백엔드 리뷰 미팅에서 일괄 — `04_api_contract.md` §1)

| # | 안건 | 관련 문서 | 상태 |
| --- | --- | --- | --- |
| Open-1 | push vs pull (스냅숏 전달 방식 — push 가정) | 04 | ☐ |
| Open-2 | 배치 트리거 순서·시각 | 04 | ☐ |
| Open-3 | Import 파일 전달(스토리지 URL 가정)·크기 상한 | 04·05 | ☐ |
| Open-4a | learning_event 백필 범위 | 04 | ☐ |
| Open-4b | 과제명 텍스트 제공(태깅ⓒ 입력 — 불가 시 기능 자체 불가) | 04 | ☐ |
| Open-4c | student_summary 필요 여부 (A 제안: 불필요) | 04 | ☐ |
| Open-4d | comm_history 범위·백엔드 1차 마스킹 | 04 · policies/masking_redaction | ☐ |
| Open-5~8 | 인증·타임아웃·서킷·버전 규약 | 04 §5 | ☐ |
| Open-9 | 전국 백분위 출처·모수·갱신 주기 (콜드스타트 정책 포함) | part_a/07_report_spec | ☐ |
| Open-10 | 15일 규칙 vs DataSufficiency (A 제안: 게이트 우선) | part_a/07_report_spec | ☐ |
| Open-11 | **수능 영역 enum 개정 [A+B]** — 6영역+item_format | policies/taxonomy | ☐ |
| Open-12 | 답안지 OCR 실명→alias = 백엔드 vault (P2) | policies/f17_paper_exam | ☐ |

## B. member-B(염준영) 합의 안건

| # | 안건 | 결과 반영처 | 상태 |
| --- | --- | --- | --- |
| B-1 | 지시서 개정 4건(상담팩 LangGraph 승격·조사 도구 루프·보조 4종·classifier) — **합의 전 에이전트 2종 착수 금지** | part_a/01_pipeline · policies/langgraph_state | ☐ |
| B-2 | 공용 계약 4파일 초안 리뷰 순서 | contracts/ | ☐ |
| B-3 | 수능 enum 확정(=Open-11) + 경계 사례 7건 판정 | policies/taxonomy · contracts/taxonomy.py | ☐ |
| B-4 | 서술형 채점 분담(루브릭=B, 피처·리포트 소비=A) + F17 OCR 소유 | policies/f17_paper_exam | ☐ |
| B-5 | LLM 게이트웨이 인터페이스·벤더 — **확정 전 벤더 SDK 설치 금지** | contracts/llm.py | ☐ |
| B-6 | LangSmith 도입(마스킹 훅 위치·데이터 체류) | runtime/ | ☐ |
| B-7 | evidence resolver 주입 시그니처 | evidence/resolver.py | ☐ |

## C. 백엔드 합의 안건

| # | 안건 | 결과 반영처 | 상태 |
| --- | --- | --- | --- |
| BE-1 | 계약 v0.1 리뷰 → v1.0 승격 (Open-1~12 일괄) | 04·05 | ☐ |
| BE-2 | alias 발급·vault 소유 — 실명 필드 부재를 백엔드 코드로 보장 | 04 | ☐ |
| BE-3 | 인증·VPC·AI PG 프로비저닝(국내 리전) | 인프라 | ☐ |
| BE-4 | 쿼터 게이팅 분계 — 카운트 시점(A 제안: 선차감+실패 환급)·meta.quota 출처 | policies/quota_metering · part_a/06_refine_policy §5 | ☐ |
| BE-5 | 월별 리포트 벌크 형태(A 제안: 학생별 N회+멱등키)·백분위 API 스펙 | part_a/07_report_spec | ☐ |
| BE-6 | AI 장애 폴백 화면 문구 | policies/error_codes | ☐ |
| BE-7 | 체크온 표준 스키마 공동 확정 (F1 템플릿=Import 목적지=스냅숏 3자 일치) | **표준 스키마 정의서 — 미작성(체크리스트 #1)** | ☐ |
| BE-8 | suggested 확정 회신 경로(/confirmations) 분계 | 04 §3.3 | ☐ |
| BE-9 | Import 결과 반영 경로(F1 API vs 벌크) | 04 §3.8 | ☐ |

## D. 남은 작성물 (A 자체)

| 항목 | 상태 |
| --- | --- |
| 체크온 표준 스키마 정의서 (체크리스트 #1 — BE-7 자료) | ☐ 미작성 |
| Alembic 마이그레이션 (06_erd 24테이블) | ☐ |
| tone_map.yaml · buffer_lexicon.yaml 실파일화 (policies/tone_mapping 원본) | ☐ |
| redaction_patterns.yaml + golden/redaction 코퍼스 30건 실파일화 | ☐ |
| FakeSnapshot 픽스처 (05_request_json 기반) | ☐ |
