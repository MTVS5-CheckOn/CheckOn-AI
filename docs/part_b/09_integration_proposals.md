# [체크온] B 통합 제안서 v2 — A 변경 요청 · 공용 문서/계약 변경 제안 · OPEN 총괄

> **지위:** member-B(염준영)의 공식 통합 제안과 승인 이력. `[제안]` 항목은 오너 승인 전까지 확정되지 않으며, `✅ A+B 승인 완료`로 표시된 항목은 승인된 결정 기록이다. A 소유 문서·공용 계약·정책·ERD 변경은 `docs/02_ownership.md` 절차를 따른다. A가 이미 요청한 리뷰 반영은 직접 갱신하고, 독립 크로스체크에서 새로 발견한 A·백엔드 안건은 기존 정본 값을 바꾸지 않은 채 원본 조항에 `[PART_B 크로스체킹 요청 · 미확정]`으로 남긴다.
>
> **변경 이력**
> - v4.8 (2026-08-10): **T3 만료 작품 선택 경로를 개방했다.** `literature + WorkSelection`은 동봉 5편의 리비전·해시·만료를 검증한 뒤 결정론 발췌를 사용한다. 남은 T3 과제는 살아 있는 작품의 BE 라이선스 계약, 코퍼스 확충, C-15 외부 대조다.
> - v4.7 (2026-08-10): **상담 읽기 모델 영속 스키마를 선택지 ⓑ(`COUNSEL_DRAFT_VIEW` 전용 테이블 + 분리 JSONB 스냅숏)로 판정하고 자리만 신설했다.** `_view_cache`·`_drafts`의 실제 PG 배선은 A 축으로 남아 `㉿`·`㉻`·`㉬`는 아직 닫히지 않는다.
> - v4.6 (2026-08-10): **T2 본문 지문 생성 경로 개방을 현재 API·워크플로·OPEN 표에 반영했다.** 지원 요청은 `language + passage 없음` 또는 `reading + PassageRequest`이며, T4·T5는 `PassageRequest` 다영역화, T3는 BE 라이선스 원문 풀 주입 계약이 남는다. `LexiconLookup` 미배선은 막힌 문 뒤가 아니라 도달 가능한 잔여로 승격했다.
> - v4.5 (2026-08-09): `AreaTag`의 출제 규격 기준 5영역 전환을 현재 본문에 반영하고, part_a·공용·양자 승인 파일의 잔여 변경 요청을 소유권별로 등록했다. `source_procurement_not_implemented`도 요청·잡 생성 전 문 앞으로 이동해 API·OPEN 문서 정합 제안을 갱신했다.
> - v4.4 (2026-08-09): **§2-24 신설 — `POST /v1/problems` 202에 `status` 추가(04 개정 요청).** A의 런북 §2 규칙(*"202의 status가 종단이면 통지를 기다리지 말고 바로 GET한다"*)을 pg에 적용할 수 없었다 — 202가 `job_id` 하나만 실었다. 🔴 pg는 POST 안에서 워커를 돌리지만 `run_next()`가 자기 잡을 처리한다는 보장이 없어 **종단·`queued` 두 경로가 실재**하고, 응답만으로는 구분이 안 됐다. counsel 202와 대칭으로 맞췄다. ⚠ 함께 적었다 — `run_next()` 프로덕션 호출처가 라우터 둘뿐이라 **배경 드레인 루프가 없다**(잡을 도는 유일한 계기가 새 요청이다).
> - v4.3 (2026-08-09): **§2-22·§2-23 신설 — #156의 결정과 관측을 저장소에 남긴다.** ⓐ pg 그래프 super-step 상한: 실측(langgraph 1.2.9 기본값 **10007** · 환경변수로 덮임)이 99 `#08` ⓑ 등재문과 위험의 방향이 달랐고, 유도식의 재료가 어디 사는지(계약·설정·코드)와 **다른 그래프에 복사하면 안 되는 이유**를 적었다. ⓑ `LexiconLookup` 소비처 0: 어휘 대조가 파이프라인에 안 물려 있다 — 지금은 T2가 400으로 막혀 도달 불가지만 **T2 개방 시 선행 항목**이다. 🔴 둘 다 종전에는 PR 본문·커밋 메시지에만 있었다 — **PR 본문은 grep이 안 된다**(결정 로그 87과 같은 형태).
> - v4.2 (2026-08-09): **§2-21.2 해소 — 라벨 교체 요청 철회, 정본은 `policies/taxonomy.md` §3.** 진단(목적이 둘)은 맞았는데 처방이 「A 소유 dict 하나 교체」라 하나를 바꾸면 두 목적이 같이 바뀌었다. 매핑을 둘로 나누고 화면 축은 클라이언트 소유로 확정했다(강사가 고치는 값이라 피커가 전 값의 라벨을 이미 들어야 한다). ⚠ 요청 표의 `어휘·개념`이 `f"{area}·{type_}"` 조립을 밟는 것도 A가 잡았다 — 우리가 「적용·창의」를 줄인 이유를 우리 표에서 어겼다. 값은 여기 복제하지 않는다.
> - v4.1 (2026-08-09): **예약 태그 거절 구현 반영 · §2-19.4를 04 §3.11 포인터로 축소.** ②(출제 요청)의 자리를 라우터가 아니라 `enqueue()` 최상단으로 확정했다 — 불변식이 「400이 난다」가 아니라 「미지원 태그로는 잡이 만들어지지 않는다」이기 때문이다(99 #01). §2-19.4의 지원 한계 목록은 04로 이관된 정본을 가리키게 하고 **구현 상태만** 남겼다(같은 목록을 두 곳에 두면 BE가 선검사를 하나만 넣는다). §2-21.3의 단정형(*"400으로 거부한다"*)을 약속·현재 병기로 고쳤다(㊩) — 8/7~8/9 사이 그 문장은 거짓이었다.
> - v4.0 (2026-08-08): **§2-21 신설 — Step 1 그리드 축과 v1 출제 범위.** 2027학년도까지의 수능 체제에 맞춰 화면 y축을 과목 4행으로 확정하되 측정 축 `AreaTag` 6개는 유지하고, TypeTag 행동 영역 라벨·「적용·창의」 예약의 소비처 5곳을 분리했다. 자료 조달 게이트와 커리큘럼 그래프가 서로 다른 잠금임을 명시하고, v1 미지원 표시는 셀이 아니라 출제 버튼에 붙이는 로드맵을 정리했다.
> - v3.8 (2026-08-07): **§2-19 확장 — PG MVP 조회·수정 API의 BE 인계 규약.** 엔드포인트 표면 6종을 구현 여부로 나누고 Step3 목록·상세의 상태 카운터, `available_actions`, `current_revision_no`, 리비전 충돌과 전 게이트 재검증, 교체·삭제 경로 제안을 명시했다. 근거 `quote=null`의 배지 금지, 본문 없는 완료 알림, Step1의 상대 판정·skill node 집계, 독서·문학 요청 전량 400인 v1 제한을 함께 고정했다.
> - v3.7 (2026-08-07): **§2-20 신설 — `PROBLEM_ITEM` 스키마 결손과 해소안 제안.** `ProblemItemStore`의 `(set_id, slot_index)` 조회 키와 `StoredProblemItem` 무손실 왕복을 막는 결손 12건을 계약·ORM 행 단위로 대조하고, `ITEM_REVISION` 우회가 성립하지 않는 이유 4건을 기록했다. A가 결정할 조회 키·보존 방식·본문 없는 슬롯 표현의 선택지를 분리했으며, B는 `ITEM_CANDIDATE` 선례에 맞춘 전체 스냅숏 1컬럼 + nullable 파생 투영을 권고한다. §2-19에는 스키마 결정 전 v1 인메모리 운용과 프로세스 재시작 후 404 한계를 추가했다.
> - v3.6 (2026-08-07): **§2-19 신설 — `/v1/problems` v1 API 스펙 초안.** A 확정 회신에 따라 자체 job 대신 공용 슈퍼바이저를 사용하고, v1 operation을 `problem_set.generate` 하나로 고정했다. counsel/drafts와 같은 필수 헤더·멱등 규약, `JobPhase` 기반 GET 상태, 미완료 `result=null`, 오류 주체 판별, `domain_error_for`의 504/503/500 매핑, 현행 `LANGUAGE`·자료 없음 제약을 한곳에 모았다. 이 절은 §2-1의 가칭 경로와 refine·reverify 범위를 v1에서 대체하며, A가 `04_api_contract.md`에 옮길 초안이다.
> - v3.5 (2026-08-05): **라이선스 전제 명시에 따라 W16·W18을 P3으로 내렸다.** 이 프로젝트는 부트캠프 대회 출제용이며 상업 서비스가 아니다 — AI Hub 이용정책은 비상업 연구개발을 허용하므로 71857을 **사용 가능**으로 재판정했고, CC BY-SA 전파도 비상업 범위에서는 출처 표시로 닫힌다. **두 항목은 삭제하지 않았다** — 상용 전환 시 P1으로 되살아나며 그 조건을 `05` §1.1.4 머리에 함께 못 박았다.
> - v3.4 (2026-08-05): **W18 신설 — AI Hub 71857 상업 이용 협의 필요.** 9.1·9.4·C-15·F17을 한 번에 닫을 수 있는 자료를 확보했으나 이용정책이 구축기관 협의를 요구해 v1에서 쓰지 않는다. 실측값과 대안 경로를 함께 적었다.
> - v3.3 (2026-08-05): **W17 신설 — 어휘 대조 재현성 갭.** 사전 API에 자료 버전이 없어 `VersionSet`에 적을 값이 없다. 전체 덤프로 닫을 수 있으나 색인 99.8 MB가 동봉 선을 넘고 W16과 겹쳐 A-1 저장 계층으로 미뤘다.
> - v3.2 (2026-08-05): **W16 신설(사전 뜻풀이 CC BY-SA 전파 미확인) · D-03 자료 확보 완료로 갱신.** 표준국어대사전 오픈 API를 확보해 어휘 대조 자료가 닫혔고, `sense_code`가 `dict_entry` 근거의 정본임을 실측 확정했다. 라이선스가 공공누리 제1유형보다 세서(동일조건변경허락) 뜻풀이 비복제 방침을 먼저 적용하고 법적 확인은 W16으로 남긴다.
> - v3.1 (2026-08-05): **§3 W15 신설 — 기준 자료 출처 표시 페이지.** 어문 규범 자료가 공공누리 제1유형이라 상업 이용은 되지만 출처 표시가 의무이고, 사전 자료는 조건이 다를 수 있다(우리말샘 CC BY-SA는 동일조건변경허락). 표시 위치를 **서비스 소개·라이선스 페이지**로 확정하고(`05` §1.1.2) FE 협업 안건으로 등록했다.
> - v3.0 (2026-08-04): **§2-18 신설 — `02_ownership.md` 트리의 `problem_generation/` 하위 표기 동기화 제안.** capability 내부를 domain·application·infrastructure 3계층으로 재배치해 트리의 파일 열거가 stale해졌다. 소유는 불변(염준영 단독)이며 `src/ai/` 최상위·A 소유 경로 무접촉이다. 계층 정본은 [`13_code_layout.md`](13_code_layout.md) 신설.
> - v2.9 (2026-08-04): **§3 W14 신설 — 골든·데모 픽스처의 셀 분포 대표성.** JSON 파싱 실측에서 진단 골든 123건이 3셀, 시드 14건이 2셀, A 감지 데모 973건이 2셀로 **24셀 중 합집합 5셀**이었다. `unknown` 라우팅·`overall_low` 분모 규칙이 골든으로 검증되지 않고, A 쪽에서는 셀이 2개일 때 `cell_error_share ≥ 0.5`가 필연 충족이라 R6 편중 판정의 작동 근거가 없다. **A 회신(2026-08-04)의 수치 정정 3건을 반영한 결과**이며, 종전 B 집계(`language 107`·`literature 0`·`8셀`)는 `grep -c`가 데이터가 아니라 코드 심볼 참조까지 센 오류였다.
> - v2.8 (2026-08-04): **`passage_ref` 확정(`05` [A 확정 통보 8/3 · 승우 합의]) 반영 3건.** ① `DiagnosisEvent`가 이 필드를 못 받아 `extra="forbid"` 하에서 백엔드 송신 시 진단 입력이 깨지는 상태였다 — B 구현으로 해소(수신만, v1 판정 축 미사용). ② **W13 신설** — A가 `03b0397`로 R1을 잔차로 이관해 원시 정답률을 판정에 쓰는 곳이 B의 셀 판정만 남았다. `PASSAGE_TYPE_STAT`을 재료로 기대치 잔차 이관을 등록하고 선결 조건 3건을 명시했다. ③ [`12`](12_suneung_format_alignment.md) §5 FMT-2의 묶음 키를 신설하지 않고 `passage_ref` 규약을 따르기로 정리했다. 더불어 **A-3 기준 수를 정정**했다 — A의 기대치 층 2테이블(`dbafdb9`)로 현행 develop이 이미 36이므로 목표 상수는 `26 → 34`가 아니라 **`36 → 44`**다.
> - v2.7 (2026-08-03): **§3에 W12 신설 — 난이도 밴드 비단조.** `part_a/13` §4-2 실측(AI Hub 국어 8,572건 · NORMAL 34.6% < HARD 49.2%)에서 강사 주관 난이도 라벨이 단조가 아님이 확인됐다. `DIFFICULTY_BAND_MISMATCH`는 `ReviewReason`이라 폐기가 아닌 `needs_review`이고 `difficulty_regen_enabled`도 `false`여서 현행 영향은 강사 주의 예산에 한정되지만, **그 플래그를 켜기 전 선결 조건**으로 등록했다. 같은 근거로 [`04_curriculum_graph.md`](04_curriculum_graph.md) §4에 셀 판정 `[잠정]`의 오차 근거를 명시했다 — 이항 표준오차 ±15.8%p(참값 delta 0인 셀도 허위 `weak` 약 17%)에 §4-3-3의 **문항 간 난이도 분산 84%**가 얹혀 균일 가정이 서지 않으므로, 파일럿 전까지 셀 verdict는 강사 참고용 힌트이며 자동 처방 근거가 아니다.
> - v2.6 (2026-08-03): **§2-16 후속 1 해소 — 추적 판정 소스 단일화.** A가 `test_trace_masking_hook.py`를 `monkeypatch.setenv` 기준으로 이관하고(PR #61) OR 제거 상태의 사전 증명을 제공해, B가 `gateway.py`의 `or resolved_settings.langsmith_tracing` 보조 트리거를 제거했다. 판정 정본은 `external_tracing_active()` 하나이며 `LlmSettings.langsmith_tracing`은 `.env` 표기용으로만 남는다. A-11을 ✅ 완료로 닫고, #59 머지로 stale이 된 B-14·§2-16의 "PR 대기·우회 가능" 문구를 현행으로 정정했다. B-14의 4단 중 P2(serde·클라이언트 은닉)만 잔여다.
> - v2.5 (2026-07-30): **§2-14 GraphRAG 공용 계약의 A+B 승인·A 반영 완료(PR #49 · `02_ownership` v5)를 동기화**하고 B-12를 해소했다. §2-17은 경로 구분자 실제 결함과 W 번호를 과대·누락 판정한 B의 패턴 검사를 철회한 기록을 유지하면서, ① `as_posix()`·② Windows CI의 A 실행 완료(PR #50), A가 제안 밖에서 추가한 OS 무관 회귀(`b994017`), ③ `.gitattributes` 미합의 잔여를 분리해 갱신했다. §2-16은 PR #51 실측으로 `(b)` 훅의 LangSmith 기록 효력이 없음을 확정하고 briefing 제외·`emphasis_points` 우선 노출면·P2 제어점·미확인 5건을 반영했으며, 동의어 env 우회와 A PR #58 완료·B `5d8e1a4` PR 대기를 기록했다. §1-9 A-11과 B-14에는 추적 판정 소스 단일화 요청과 P1·P2 미완 상태를 동기화했다. `01_pipeline`·`02_design`의 백엔드 DB 표기도 PostgreSQL로 정정했다.
> - v2.4 (2026-07-30): **§2-16 LangGraph 트레이스 경로와 B-14 신설** — gateway `(b)` 훅 미배선과 LangGraph 경계를 분리하고, PR #43의 counsel_pack 본문 미복제 정책과 `counsel/state.py` 구현 모두를 확인했다. 개인정보 활성 유출 경로 해소에 따라 B-14를 P1으로 두되, 프롬프트 IP 노출·실측 미확정이 남아 `LANGSMITH_TRACING=false` 유지를 재활성화 전제로 고정했다. P1' 대상도 briefing·counsel_pack 조립부 2곳으로 확정했으며, §1-10 ③의 `(a)` 실제 조립부 redaction·AST 계약 보호와 `(b)` 조건부 no-op도 정정했다.
> - v2.3 (2026-07-30): §2-14의 양자 승인 카운트 위치를 `02_ownership.md` §4로 정정하고, §2-15에 blind 격리 범위 B-13 해소(PR #40)를 기록했다. §2-13의 승인 시 동시 개정 대상을 R6 임계 재산정·`taxonomy_version` 기축적분 혼재 정책·taxonomy 제목/결정 로그·ERD 2곳까지 4건 확장했다.
> - v2.2 (2026-07-30): **크로스체크 정정 3건 + 안건 2건 신설.** ① §2-12-②를 `✅ A 승인·구현 완료(PR #37) · B 사후 검증 완료`로 승격하고 조건 5개 충족 근거를 코드 기준으로 기록 ② `verify_evidence_paths`의 소유를 **`현행 B 소유 · §2-14 승인 시 A+B`로 정정**(승인 전 확정 표기 철회)하고 `quote_hash`·`license_ref`가 이 검사의 책임이 아님을 명시 ③ **§2-14 신설** — `contracts/graphrag.py` 양자 승인 편입(12→13곳, A 제기·B 수용) ④ §3에 `B-12`(graphrag 소유)·**`B-13 [P0]`**(blind AST 테스트가 R-1 resolver 연동을 차단) 등록, `B-7` 해소 처리.
> - v2.1 (2026-07-30): §2-12-②를 A-5 초안의 `EvidenceResolver.resolve` 시그니처·결과 타입에 정합하고, `GraphContextService.verify_evidence_paths`와 실제 근거 해소 및 R-1 판정의 3단 경계를 명문화했다.
> - v1.10 (2026-07-29): §2-13 `type_tag` 어휘 확장 제안 신설, §3에 수능형 포맷 총괄 W8·`type_tag` 표현력 W9·롤백 재검증 W10 등록, §2-8에 `12_suneung_format_alignment.md` INDEX 링크 제안을 추가했다.
> - v2.0 (2026-07-28): **A 공식 회신 전수 반영** — §2-4의 B 테이블 수·ERD parity 기준을 8종·34개로 통일하고 `ITEM_CANDIDATE` 유니크 제약을 원자 PR 조건에 추가. A-10 gateway 공용 규칙 승격과 데모 rank를 완료 처리하고, A-4 조건(meta.versions 10→13키 동시 개정·GraphRAG 버전 축 독립성)을 §2-12-①에 반영. `ReviewReason`·`DifficultyBand`의 `error_codes.md` §2.6 편입을 제안하고, A-6 판정에 따라 RLS 문서 불일치를 해소·실도입을 BE-11로 이관. B-8은 ⓐ validator와 ⓑ GraphRAG 3필드를 한 안건으로 병합했다.
> - v1.9 (2026-07-27): **§1-10 「A 게이트웨이 협의 3건 — B 회신」 신설** — ① role별 전송 재시도 `0..1` 파라미터를 **B가 게이트웨이에 신설**(생성자 주입·기본 1로 문제생성 무변경·A의 "어댑터 직결" 차선책은 `01` §5 위반이라 수용 불가) ② recorder가 `ExecutionContext`를 함께 받는 ②안 동의 + `LlmCallRecord`가 양자가 아닌 **B 단독 소유**임을 정정 ③ 마스킹 훅을 **전송 redaction(fail-closed·no-op 불가)과 트레이스 마스킹(no-op 허용) 2개로 분리**.
> - v1.8 (2026-07-27): B-M2-01 확정 반영 — `PROBLEM_ITEM.difficulty_fit` 제안의 확정 대기 표기를 해소.
> - v1.7 (2026-07-27): **§1-9 「A 작업 지시 일람」 신설** — A 승인·작업 9건(P0 5건)을 한 표로 집약. **§2-12 GraphRAG 공용 계약 확장 제안** 신설(`VersionSet` 3필드 · evidence resolver 병합 · `graph_version` 재사용 금지). §3 OPEN 총괄에 B-8~B-10과 와이어프레임 충돌 W1~W7 등록.
> - v1.6 (2026-07-27): §2-4 증보 — B 테이블 편입의 승인 형태(PR 리뷰 = 승인)·원자 PR 파일 목록·컬럼 스펙 반영분·저장소 ORM 규약을 확정 수준으로 기술. 독립 크로스체크에서 새로 발견한 3건 등록: `WEAKNESS_MAP.overall_low` 컬럼 부재(계약 존재), `PROBLEM_SET.request`의 난이도 표기와 `ProblemRequest` 필드 부재 불일치, **RLS 구현 부재**(문서 원칙과 현행 구현 불일치 — §2-4.5).
> - v1.4 (2026-07-22): PR #10·#11의 A 판정을 B 추적표에 회신 반영 — 기존 크로스체크의 완료·후속 백로그를 분리하고, `REVISION_CONFLICT`·B 결과 어휘 5종의 A 정본 편입 완료를 갱신. A가 새로 확정한 **ongoing·R5 상한 제외**는 B가 수용하되, 공용/API 요약 동기화·병합 lifecycle 경계·회귀/데모 보강은 해당 A 문서에 새 크로스체킹 요청으로 등록.
> - v1.3 (2026-07-22): `develop`의 A PR 리뷰 요청 4건에 B 회신 — 승인 항목은 직접 반영하고, 그 과정에서 새로 발견한 간극만 해당 A·공용 원본에 **B 제안 해결안과 크로스체킹 요청**으로 등록. B 소유 충돌 규약·상태 필드 분류·HTTP DTO 경계와 §2-10 실제 완료 현황은 확정 반영.
> - v1.2 (2026-07-15): **공용 계약 B 확장 14항목 A+B 승인 완료 반영** — §2-3·§2-9를 승인·구현 완료(커밋 `d5283d0`)로 전환(제안 이력 보존), §2-10(공용 문서 동기화 요청 일람) 신설, §3 B-2 갱신.
> - v1.1 (2026-07-15): 파일 번호 이동(06→09) + 7/15 결정 정리 — 해소 안건 분리(§0), Kafka 이벤트·409 충돌 코드·라벨 사전 제안 추가, B-1 회신 갱신, 쿼터 제안 폐기 반영. part_b 재편(01~09)에 따른 참조 갱신.
> - v1 (2026-07-15): part_b 정리 과정에서 도출된 요청·제안 일괄 등록.

---

## §0. 7/15 회의로 해소된 안건 (기록 — 재론 불요)

| 안건 | 결과 | part_b 반영 |
| --- | --- | --- |
| Open-11 / B-3 / D-01 | ✅ 현행 5영역 채택 · **v1 item_format = mcq만**(short·essay 예약) · 측정 대상 기준 경계 사례 7건 확정 | 05 §4·§9, taxonomy §2·08 코퍼스 |
| B-1 | ✅ 슈퍼바이저 1 + 워커 3(문제 생성 = B 워커) — 영속 Job·lease·부분 수렴 실행 계약 확정 | 01 §0·§6 · langgraph_state §5 |
| B-4 / D-08 | ❌ **폐기** — v1 서술형 없음. F17 OCR 소유만 Open-12와 P2 재론 | 05 §9 예약 |
| BE-4 / D-05 | ✅ 쿼터 전부 백엔드 — **AI는 쿼터 무관, meta.quota 폐기** | 01 §5, 05 §4.4, 07 §5 |
| Open-2 | ✅ 비동기 완료 통지 = **Kafka** | 02 §1-A — 이벤트 증분 제안(§2-1) |
| B-6 / D-07 | ✅ LangSmith 공통 1개 도입(마스킹 훅 게이트웨이 앞단) | 01 §5 |
| D-02 | ✅ (대화 확정) 검증 차단 문항 저장 가능·발행 차단·**수동 예외 승인 불허** | 06 §3 |
| 타겟 협소화 | ✅ 수능 고등 기준 — v1 중등 분기 금지 | 04 §2 |
| (7/15 후속) contracts 공용 5파일 | ✅ A가 구현 완료(`feat/contracts-base` — taxonomy·execution·llm·gates·evaluation + 테스트 74종) | B 회신 §1-5 · (당시) 확장 제안 §2-3·§2-9 → 아래 행에서 승인 완료 |
| (7/15 오프라인) **공용 계약 B 확장 14항목** | ✅ **A+B 승인 완료** — A와 실시간 협의로 승인, 커밋 `d5283d0` 구현 반영(Capability 2 · VersionSet 4 · GateName 3 · OwnerKind 1 · BlockedReason 3 · GoldenSuite 1) | §2-3·§2-9(승인 완료) — §2-10 문서 동기화 **3/7 완료, 4건 잔여** |

## §1. A 소유 문서·코드 변경 요청 (승인 주체: 박진희)

### 1-1. part_a의 OCR=A 표현 정정 요청 — Open-12 `(C-02)`

- 대상: `part_a/01_pipeline.md` F17 절("OCR 답안 추출(A: import 확장)", "OCR·수집·learning_event 변환 = A") 및 `part_a/02_design.md` 동일 표현.
- 근거: `docs/policies/f17_paper_exam.md` §5·`99_open_items` Open-12는 OCR 판독 소유를 **미정**으로 둔다(B-4 폐기 후 Open-12로 흡수). 공용 정책 우선 — "미정(Open-12·P2)"으로 정정 요청.
- 확정 분담(유지): 스캔 수신·실명 매칭·이미지 마스킹=백엔드.

### 1-2. `agents/` 반영 + 슈퍼바이저 확인 — B-1 완료

- `AGENT_RUN.agent_kind=problem_generation`과 공통 Job 실행 필드를 반영한다. 슈퍼바이저 실행 계약(`policies/langgraph_state.md` §5)은 **B 리뷰 완료** — Job 단위는 세트·문항 리비전·재검증 operation 각각 1건이며, 인터랙티브 작업은 실행 중 강제 선점하지 않고 워커 체크포인트 경계에서만 협력적으로 양보한다. `agents/` 구현은 A, 공통 Job·라우팅 계약은 양자 승인이다.
- LangGraph 버전은 llm/ 의존성과 함께 B가 고정하고 A 리뷰.

### 1-3. `gates/chain.py` — 변경 불요 확인 요청 `(C-07)`

- B 게이트 3단은 workflow 노드로 **자체 실행**, 기록 타입만 공용 `GateResult` 사용(chain.py 무변경). 이견 시 재론.

### 1-4. `part_a/08_evaluation_plan.md` §1 트리 증보 요청

- `golden/problems/`(하위 7종 — [`08`](08_evaluation_plan.md) §1)·`golden/diagnosis/` 행 추가. 편입 방식(§9 편입 vs 병렬)은 A 리뷰 — B는 병렬 유지 기본.

### 1-5. A → B 승인 요청 회신 — `feat/contracts-base` (contracts 5파일 + 테스트 74종)

A가 요청한 B 검토 2건에 대한 회신:

| 요청 | B 회신 |
| --- | --- |
| `execution.py` 신규 필드 2개(`threshold_version` nullable · `contract_version` non-null) — 양자 승인 | **승인** — 독자 설계가 아니라 §2.2/ERD 두 문서의 합집합 정합. **교집합 대안도 검토했으나 기각**: 교집합이면 `{pipeline, engine}` 2종만 남아 threshold(감지 판정 기준)·prompt(LLM 실행 기준)를 잃고 과거 실행 재현 불가(불변식 8). 두 문서는 별개 시스템이 아니라 같은 대상(실행 1건의 재현 키)의 불완전한 명세였으므로 합집합이 정답이고, 실행 유형별 차이(개별로 돌아가는 부분)는 필드 삭제가 아니라 **nullable로 흡수**(detection: prompt=null / LLM 실행: threshold=null). VersionSet은 워커 통합 스키마가 아니라 실행 1건마다 찍히는 재현 도장이라 슈퍼바이저 통합 여부와 무관. 같은 선례(용도별 nullable)에 따라 **B 버전 확장을 §2-9로 예고** — 승인 시점에 함께 논의 희망 |
| `llm.py`의 `LLMProvider` Protocol을 B의 FakeProvider가 구현 가능한지 | **구현 가능 — 이견 없음.** `name` + `async complete(request, context) → LLMResult` 시그니처로 결정론 응답·장애 시나리오(timeout·parse_fail·연속 실패)를 `outcome`/`LlmError` 계열로 전부 재현 가능. 재시도·백오프를 어댑터가 아닌 게이트웨이(tenacity) 소유로 둔 규약도 B의 전송 재시도 설계([`06`](06_quality_gates.md) §4 `transport_retry`)와 정합. blind 계약은 `LLMRequest.prompt`가 조립 완료본이므로 조립 단계(verification.py) 책임으로 유지 — 계약 충돌 없음 |
| OpenAI 호환 어댑터의 빈 응답 매핑 | **B 확정:** `ParseFailed`를 유지한다. 게이트웨이 전송 재시도 대상이 아니며 상위 소비자의 블록 재생성·`item_attempt` 예산이 소진한다. |

> **[PART_B 크로스체킹 요청 · 미확정 — 타임아웃 상한]** 어댑터의 호출 전체 상한 15초와 `error_codes.md` §1의 동기 10초가 다르다. 동기 경로에 별도 10초 상한이 있는지 A·백엔드 확인이 필요하다.

### 1-6. 7/22 A PR 리뷰 요청 4건 — B 회신

아래는 A가 PR에서 이미 요청한 검토에 대한 B 회신이다. 다시 A 확인 안건으로 돌리지 않고 승인·보완 여부를 직접 정리했다.

| A 요청 묶음 | B 회신 |
| --- | --- |
| 감지 계약(lifecycle AI 소유·3값, `display_label`, taxonomy 공용 enum) | ✅ 승인. 계약·구현·테스트가 일치하며 B 추가 변경 없음 |
| FakeSnapshot 위치·제외 학생·hash 플레이스홀더 | ✅ `evaluation/fake_snapshot.py` 위치 승인. A 감지 픽스처를 B 소유 `tests/ai/fakes/`에 두지 않는 근거가 타당하며, 프로덕션 import 금지 원칙과 재현용 placeholder 설명도 수용. `02_ownership.md`·`99_open_items.md`에 완료 반영 |
| score/readapt/R2·meta/capped_out 설계 | ✅ R2 v0 `consecutive_missing` 한정, 엔진 밖 `meta.versions`, `capped_out` 집계 한정을 승인. A 후속 판정으로 readapt=최근 30일 이력 존재, score 0점=원임계, `capped_out`=`new`·`follow_up` 상한 탈락분으로 확정. ongoing·R5 상한 제외 후속은 §1-8 |
| `api/` 구조·v0 한계 | ✅ `app.py`·`envelope.py` 공통 양자 승인 + 라우터 capability 오너 구조 승인. `02_ownership.md` 양자 승인 9곳과 99 ⑧에 확정 반영. 인메모리 멱등·DB 미적재 등 공지된 v0 한계는 백로그 유지; 별도로 발견한 wire·보안 간극만 §1-7로 요청 |

### 1-7. 독립 크로스체크에서 새로 발견한 A·백엔드 협업 요청

A PR 요청을 반영·검토한 뒤 B가 추가로 발견한 간극만 해당 원본 조항 바로 아래에 `[PART_B 크로스체킹 요청 · 미확정]`으로 남겼다. 기존 A 규약 값은 바꾸지 않았다.

| 원본·위치 | 새로 발견한 확인 요청 | 상태 |
| --- | --- | --- |
| `part_a/09_detect_spec.md` §2·§3·§4 | 증분 입력↔8주 baseline · date/enum 경계 검증 · 제외 이벤트 처리 순서 · AlertContext 불변식 · brief/evidence · lifecycle 다건/억제 순서·14일 경계 | ◐ A 판정 대부분 완료 — 영속 baseline은 D-②, 관련 실존 evidence 전무 시 처리는 D⑪, 미래 `resolved_at` 거부 경계는 잔여 |
| `part_a/04_threshold_config.md` §2·§3·§3.1 | readapt 시간 검증 주체 · lifecycle 억제/상한 순서 · score 0점 기준 | ✅ A 판정·엔진 반영 완료 — readapt=30일 이력, 억제 선적용, score=원임계, ongoing·R5 상한 제외(#14) |
| `part_a/02_design.md` §1-A · `03_usecases.md` D1 · `08_evaluation_plan.md` §2 · `09_detect_spec.md` §3·§4 · `06_erd.md` SIGNAL | 새 상한 정책과 기존 “전체 TOP 3~5” 요약 동기화 · 병합 primary/secondary lifecycle 경계 · 초과/capped_out 회귀 · rank/데모 | ☐ A+BE 확인 — 원본 조항에 신규 메모, §1-8 |
| `04_api_contract.md` §2.2·§2.3 | capability별 version 불변식 · 실패 meta · tenant/경로 멱등 스코프 · 요청 검증 · 실제 LLM 예외 매핑 · 민감 detail · tracing | ◐ A 방향 판정·detect 경로 일부 반영 — VersionSet 양자 협의, 공용 실패 meta/검증, LLM adapter, 민감 detail, 멱등 D-②, 로그 correlation 잔여 `[P0]` |
| `04_api_contract.md` §3.0·§3.1·§4.1 | `signals[]` 수·rank·`capped_out`을 ongoing·R5 상한 제외 정책과 동기화 | ☐ A+B+BE 확인 — 원본 조항에 신규 메모, §1-8 |
| `02_ownership.md` §5 아래 | 프로덕션 capability → `ai.evaluation` 역방향 import 금지 자동 검사 | ✅ A 수용·AST 회귀 테스트 반영 완료 (`tests/ai/contract/test_evaluation_isolation.py`) |

### 1-8. ongoing·R5 상한 제외 — A 확정 수용·후속 크로스체크

**A 확정(#14, 7/22)을 B도 수용한다.** 파이프라인은 `학생별 병합 → lifecycle 억제 탈락 → new·follow_up만 랭킹·상한 → ongoing·R5 상한 밖 합류 → 응답`이며, `capped_out`은 `new`·`follow_up` 후보의 탈락 수만 센다. 따라서 전체 응답 신호 수와 rank는 `cap_max`를 넘을 수 있다. 기존 Alert의 brief·evidence를 교체하는 백엔드 처리에는 변경이 없다.

다만 아래는 A 확정값을 바꾸지 않고 원본 조항에 `[PART_B 크로스체킹 요청 · 미확정]`으로 등록했다.

| 후속 | B 제안 · 확인 요청 | 상태 |
| --- | --- | --- |
| 요약 계약 동기화 | **확인된 불일치:** 공용 `04_api_contract` §3.0·§3.1은 아직 “신호 TOP 3~5”·“상한 적용 후의 신호만”·`capped_out=상한에 밀린 후보 수`로 적고, `contracts/detection.py`도 Signal·rank·capped_out을 전체 상한 기준으로 설명한다. `02_design`·`03_usecases`·`08_evaluation`·`09_detect_spec`까지 `new`·`follow_up` 대상, 최종 수/rank>5, `capped_out` 범위로 동기화하고 BE·FE의 길이/rank≤5 가정도 확인 | ☐ A+B+BE |
| 병합 lifecycle 경계 | **확인된 현행:** `merge_student`는 비-R5를 1경보로 병합하고, 엔진은 `primary.signal_type` 하나로만 lifecycle을 판정한다. 따라서 primary=ongoing·secondary=new이면 전체가 ongoing 상한 밖이고, secondary의 finding은 `_build_signal` evidence 조립에만 쓰여 new lifecycle은 응답에서 드러나지 않는다. 반대 방향(secondary만 ongoing)도 해당 이력을 판정하지 않는다. 현행 primary 기준 고정과 lifecycle별 분리 중 A+BE가 결정하고 양방향 회귀 필요 | ☐ A+BE(+B 리뷰) |
| 회귀 보강 | **확인된 커버리지:** `tests/ai/unit/detection/test_engine.py`에는 `cap_max`·`capped_out`·rank 조합 회귀가 0건이다. golden에는 new 6→5·억제 선탈락·`ongoing 3 + new 5 → 8, capped_out=0`만 있다. `ongoing 3 + new 6 → 8, capped_out=1`, new+follow_up 공동 상한, R5 상한 밖 조합, 다중 반 독립 rank/합산, 상한 밖 `student_ref` 정렬·정확한 rank, 병합 lifecycle 양방향을 추가 고정 | ☐ A |
| 데모 rank | ✅ **해소(7/26, `4a4f1b1`)** — `detect_demo_response.json`이 현 정책 순서인 `st_07=1` · `st_09=2` · `st_08(R5)=3` · `st_10(ongoing)=4`로 재생성돼 엔진 출력과 정합한다 | ✅ 완료 |

### 1-10. A 게이트웨이 협의 3건 — B 회신 `[2026-07-27]`

A가 브리핑·mapping_probe 배선 전에 요청한 게이트웨이(`llm/` — **B 단독 소유**) 협의 3건에 대한 B 확정 회신이다.

| 안건 | B 회신 | 작업 주체 |
| --- | --- | --- |
| **① role별 전송 재시도 0회 허용** | ✅ **승인 → `[2026-07-28] 구현 완료·머지`**(A PR `feat/llm-gateway-policy-v1`). A가 제시한 차선책 "어댑터 직결 유지"는 [`01`](01_pipeline.md) §5 "모든 LLM 호출은 gateway 경유 — 예외 없음" **위반이므로 수용 불가**였고, 대신 B가 파라미터를 열기로 했으나 **A가 합의문 그대로 구현**했다. 조건 (a)~(d) 전수 충족 확인: 생성자 주입 · `0..1` 기동 실패(`_validated_transport_retry`) · sheet-agnostic · 성공·예외 양쪽 경로 시도별 기록 | A(구현·머지) · B(리뷰 완료) |
| **② `LlmCallRecord.execution_id`** | ✅ **②안 동의 → `[2026-07-28] 구현 완료·머지`.** `LlmCallRecorder = Callable[[LlmCallRecord, ExecutionContext], None]`로 확장됐고, B 조건이던 **"적재 실패가 호출을 실패시키지 않되 조용한 누락도 금지"** 가 `LlmGateway.record_failures` 카운터로 구현됐다. `LlmCallRecord`의 정의("호출 1회분 비민감 관측 메타")를 지켜 실행 문맥을 record에 복제하지 않았다 | A(적재) · B(리뷰 완료) |
| **③ LangSmith 마스킹 훅** | ◐ **조건부 승인 — 훅을 2개로 분리**. "기본 no-op 단일 훅"은 `CLAUDE.md` 불변식 3과 충돌. **잔여 — 별도 PR** | B(훅 신설) · A(마스킹 함수 주입) |
| **(부수) `ModelRole.NARRATOR` 신설** | ✅ **O → `[2026-07-28] 구현 완료.`** `composer` 대신 **작업 성격 기반** 이름을 택해 기존 4종(생성·검증·매핑추론·분류) 관례와 정합. capability 전체를 뜻하지 않으므로 초안·리포트·refine이 자동 흡수되지 않는다 — 그 셋을 narrator에 넣을지는 배선 시점에 **재시도 정책이 브리핑과 같아도 되는지**로 판단한다. `llm_call.role`이 varchar라 마이그레이션 없음(`06_erd.md` §255 반영 완료) | A(신설) · B(승인) |

#### ① 전송 재시도 파라미터 — B 확정 사양

```python
LlmGateway(providers, *, recorder=..., transport_retry: Mapping[ModelRole, int] | None = None)
```

| 규약 | 내용 | 근거 |
| --- | --- | --- |
| 주입 지점 | **생성자**(호출별 금지) — 같은 role이 호출마다 다른 재시도를 가지면 재현성이 깨지고 `LLM_CALL` 원가 회계 해석이 갈린다 | 불변식 8 · [`06`](06_quality_gates.md) §4 |
| 값 범위 | `0..1`, 벗어나면 **기동 실패** | 불변식 6(모든 루프에 상한) |
| 기본값 | 미지정 role은 `1`(총 2회 — 현행 보존) → **문제생성 무변경** | [`06`](06_quality_gates.md) §4 최악 논리 6콜·전송 12요청 유지 |
| 단일 원천 | `generator`·`verifier`는 `verify_config.transport_retry`를 조립 시점에 주입. 게이트웨이는 시트를 모른다(계산·I/O 분리) | `CLAUDE.md` §6 · `03_coding_rules` |
| **가드 위치** `[2026-07-28 확정 — A 해석 승인]` | 단일 원천 보장은 **게이트웨이 내부 검사가 아니라 조립부 테스트**로 한다. 게이트웨이는 sheet-agnostic을 유지하고 `0..1` 범위 검증만 남긴다. **B 담당분(B-5 정리 PR):** `src/ai/problem_generation/provider.py`(신규 — `composition/provider.py`·`import_mapping/provider.py` 선례)가 `verify_config.transport_retry`를 읽어 `{GENERATOR, VERIFIER}`에 주입하고, `tests/ai/unit/problem_generation/test_provider.py`가 ① 시트값이 그대로 주입 ② 시트에 없는 role은 미주입(기본 1로 낙하) ③ 시트값이 `0..1` 밖이면 **조립 단계**에서 실패 를 고정한다. 시트를 patch했을 때 주입값이 따라 바뀌는지로 **리터럴 하드코딩 부재**를 증명한다 | 위 "단일 원천" 행의 문언 해석 — 게이트웨이에 시트 비교를 넣으면 계산·I/O 분리와 충돌 |

> **`[2026-07-28]` A 해석 확인 — O.** A가 게이트웨이에 `verify_config` 비교를 넣지 않고 sheet-agnostic을 유지한 것은 **위 "단일 원천" 행(line 124)의 문언 그대로**다. B 회신 본문에 쓴 "role 파라미터가 우회하지 못하게"라는 표현이 게이트웨이 내부 강제로 읽힐 여지를 준 **B 측 문언 문제**이며, 같은 회신의 다음 문장("게이트웨이는 시트를 모르고 주입만 받는다")과 이 표가 정본이다. 현재 `problem_generation` 패키지가 없어 **gen/verifier를 게이트웨이에 배선하는 조립부 자체가 존재하지 않으므로** 이번 PR(narrator 단독 배선)에는 실효 차이가 없다. 가드 착수 시점은 B-5 정리 PR이다.
| 회계 | 재시도 0회여도 **시도별 `LlmCallRecord` 기록 유지** | [`06`](06_quality_gates.md) §4 재생성/전송 회계 분리 |
| 예외 | `RedactionBlocked`는 재시도 대상 아님(정책 차단 ≠ 일시 오류) — 현행 `retry_if_exception_type` 유지 | 불변식 3 |

A 소비자 근거(수용): 브리핑은 결정론 템플릿 폴백이 있어 "LLM 실패 = 무재시도 즉시 폴백"이 확립 원칙이고, `04_api_contract` §2.4 [A 확정]의 detect 60s·브리핑 45s·호출당 15s 예산에서 게이트웨이 재시도가 얹히면 호출당 최악 30s가 되어 병렬 예산이 깨진다. mapping_probe도 `part_a/10_import_spec` §3.2 폴백 보유로 동일.

> **B-5 연결:** role 키 설정 구조가 생기므로 `gateway.py`의 `TODO(B-5)`(provider 1개일 때 verifier 패밀리 강제가 우회되는 현재 동작) 제거를 같은 구조에 얹는다. 단 **별도 PR**로 분리해 A 배선을 대기시키지 않는다.

#### ② recorder 시그니처 — 소유권 정정

`LlmCallRecord`는 **양자 계약이 아니라 B 단독 소유**다(`src/ai/llm/gateway.py`, `02_ownership.md` §2에서 `llm/` 전체가 B). 양자는 `contracts/llm.py`이며, 이 record가 **`runtime/metrics.py` 이벤트로 발행되는 시점의 스키마**만 양자다.

- 시그니처: `LlmCallRecorder = Callable[[LlmCallRecord, ExecutionContext], None]`
- **적재 실패가 LLM 호출을 실패시키지 않는다** — gateway의 원가 기록은 **관측**이지 게이트가 아니다([`01`](01_pipeline.md) §5). 실패는 경고+메트릭으로 처리하고 호출은 성공시킨다.
- 단 **조용한 누락도 금지** — 실패 카운터를 둔다.

#### ③ 마스킹 훅 — 2개 분리 + 순서 고정

`CLAUDE.md` 불변식 3과 [`05`](05_problem_generation.md) §5가 요구하는 것이 둘인데 A 제안은 하나로 묶여 있었다.

| 훅 | 대상 | 기본 no-op |
| --- | --- | --- |
| **(a) 전송 redaction** | provider로 나가는 페이로드 | ❌ **불가** — 조립부가 직접 fail-closed 호출한다. 주입 검사 지점 대신 B 호출부 AST 계약 테스트가 누락을 차단 |
| **(b) 트레이스 마스킹** | LangSmith로 나가는 트레이스 | ✅ **조건부** — 트레이스 미사용 시만 no-op 허용. **트레이스 사용 시 no-op 불가**(미주입 시 기동 실패) |

```text
조립(verification.py) → (a) redaction 훅 → (b) trace 훅 → provider 호출
```

- (b)가 (a) **뒤**에 있어야 B-6의 "트레이스엔 마스킹 통과분만"이 성립한다.
- (a) 위반은 `RedactionBlocked` → `CallOutcome.REDACTION_BLOCKED`로 매핑(계약 기존재). **전송 재시도 대상 제외.**
- 마스킹 함수는 `runtime/redaction`(A 소유) 주입, 훅 포인트 신설은 `llm/gateway.py`(B).

#### A 재회신에 대한 B 리코멘트 — 확인 2건 `[2026-07-27]`

A가 조건 포함 3건 전부 수용하며 확인 2건과 제안 1건을 추가했다. B 회신은 아래와 같다.

**확인 1 — `ModelRole` 신설: ✅ O (이름 범위 기준 1건 부기)**

- ERD 영향 축소 확인: `llm_call.role`은 PG enum이 아니라 `String`(`db/models.py`, D-② varchar 확정)이므로 **마이그레이션 불필요**. `contracts/llm.py`(양자) + `docs/06_erd.md` 값 집합 + 인라인 주석이면 충분하다.
- **확인된 구조적 부채:** `ModelRole.GENERATOR`의 docstring이 **"초안·지문 등 생성"** 이다 — **A의 초안과 B의 지문·문항이 현재 같은 role을 공유**한다. ① 로 role이 *정책 경계*가 되면서 드러난 문제이며, 브리핑 하나를 분리해도 초안(reply)·리포트·refine은 여전히 `generator`를 B와 공유한다.
- 당장은 안전하다 — 미지정 role 기본값이 `1`이라 `generator`는 현행 유지되고 B는 무변경이다.
- **이름 결정 기준(A 판단 사항):** 이 role을 초안·리포트·refine이 함께 쓸 것인가. ⑴ 함께 쓴다면 `composer`가 적절하나 그 셋의 전송 재시도가 **브리핑과 동일하게 0회로 강제**된다(refine은 폴백 구조가 달라 사전 확인 권장). ⑵ 브리핑 전용이라면, 기존 4종이 전부 **작업 성격**(생성·검증·매핑추론·분류)이므로 그 관례에 맞는 좁은 이름이 양자 계약 재개봉을 막는다.

**확인 2 — redaction 훅 멱등성: ✅ O (3건 추가)**

1. **토큰 번호 안정성** — 텍스트 동일성뿐 아니라 **매핑 자체의 동일성**까지. `⟪이름1⟫`이 2회 통과 후에도 같은 번호여야 감사 추적·근거 참조가 어긋나지 않는다.
2. **멱등 ≠ 검사 생략** — "이미 토큰이 있으니 통과"로 단축하면 불변식 3("불확실하면 전송 중단")이 깨진다. 훅은 매번 실제 검사하고 `⟪확인필요⟫` 판정도 매번 내린다.
3. **토큰 위조 케이스 `[신규 발견]`** — refine `instruction`·`topic_hint`는 **강사 자유 입력**이므로 강사가 `⟪이름1⟫`을 직접 타이핑할 수 있다. "`⟪⟫` 토큰은 건드리지 않는다"를 그대로 적용하면 **위조 토큰이 마스킹을 우회**한다. 진짜 토큰과 사용자 입력 토큰을 구분할 근거(예: 해당 호출 registry에 실재하는 토큰만 보존)가 필요하다. 성격이 [`05`](05_problem_generation.md) §8.2 injection 방어와 같다.
4. **테스트 위치:** ③ PR 단발이 아니라 **`golden/redaction/` 코퍼스**(미탐 0건 CI 게이트)에 편입해야 이후 패턴 개정에서도 유지된다.

**제안 — "모든 LLM 호출은 gateway 경유" 공용 승격: ✅ O**

규칙의 문서 위치가 곧 적용 범위인데 현재 [`01`](01_pipeline.md) §5에 있어 B 규율로 읽힌다. `docs/03_coding_rules.md` 승격이 적절하다("capability 간 직접 import 금지"와 같은 층). **A 소유 문서이므로 §1-9 A 작업 지시 일람에 등록한다.** 과도기(브리핑 #22 어댑터 직결)도 수용하되 조건 2건: **종료 시점을 ①+② 머지로 고정** · **그때까지 신규 어댑터 직결 추가 금지**.

#### PR 순서 (합의)

**①(B 구현) + ②(A 적재·B 리뷰, +`ModelRole` 신설) 선행 → 머지 후 B가 B-5 정리 → ③ 별도 PR.** `transport_retry` 파라미터는 B가 ① PR 이전에 선행 제공한다.

### 1-9. **A 작업 지시 일람** `[2026-07-27 신설 — A가 여기만 보면 됨]`

M2 문제생성 착수에 필요한 A 승인·작업을 한 표로 모았다. 각 행의 상세는 링크된 절에 있다. **B는 승인 대기 중에도 구현을 진행하며, 멈추는 지점은 머지뿐이다**(§2-4.1).

| # | A가 해야 하는 일 | 상세 | 유형 | 승인되면 풀리는 것 | 상태 |
| --- | --- | --- | --- | --- | --- |
| **A-1** | **B 8테이블의 `docs/06_erd.md` 편입 승인** + 이 PR 한정 `06_erd.md` 편집 go-ahead `[7/27 정정: 7 → 8테이블 — KEEP-4로 ITEM_CANDIDATE 추가]` | §2-4 · §2-4.2 · §2-4.6 | 문서(A 소유) | B 저장 계층 전체. 미승인 시 `problem_generation` 영속화 불가 | ☐ **P0** |
| **A-2** | `EVIDENCE_ITEM.owner_kind` += `problem_item` 양자 승인 | §2-3 마지막 행 | 공용 계약 | `PROBLEM_ITEM.rationale` 근거 저장. 공용 확장 14항목 중 **유일한 미승인 잔여** | ☐ **P0** |
| **A-3** | `tests/ai/db/test_erd_model_parity.py` 상수 변경 동의 — **`== 36` → `== 44`** `[8/4 정정]` | §2-4.2 · §2-4.6 | 테스트(양자 성격) | A-1과 같은 PR. 미변경 시 CI 적색 | ☐ P0 |
| **A-4** | **`VersionSet` GraphRAG 3필드 확장** 양자 승인 | §2-12 | 공용 계약 | GraphRAG 실행 재현 키. **`graph_version` 재사용 금지가 핵심** | ☐ **P0** |
| **A-5** | **evidence resolver 주입 시그니처 확정** (기존 B-7 + GraphRAG 경유 해소 병합) | §2-12 · §3 B-7 | `evidence/`(A 소유) | 게이트 ① R-1·R-4의 Graph path·quote·license 검증 | ☐ **P0** |
| **A-6** | ✅ **판정 완료 — 문서 표현을 현행 애플리케이션 계층 격리로 정정하고, RLS 실도입은 BE-11로 이관.** B 8테이블은 기존 26테이블과 동일한 `tenant_id` 컬럼 + 앱 계층 격리 패턴을 유지한다 | §2-4.5 · §3 BE-11 | 공용 정책 | B 저장 계층의 격리 방식 확정. RLS 실도입은 `db/session.py`·`db/store_factory.py` 연결·역할 설계와 함께 백엔드에서 재론 | ✅ 판정 완료 |
| **A-7** | **`[7/27 범위 축소]`** `docs/policies/langgraph_state.md` §2.4의 `ProblemGenerationState` 코드블록에 **필드 2줄 추가 리뷰** — `fallback_ref: str \| None` · `difficulty_regen_used: bool`. `state_schema_version`은 **`v1` 유지**(기본값 보유로 기존 체크포인트 그대로 재개 · 올리면 §3.2에 따라 진행 중 세트 전량 재기동) | §2-4.6 · [`10`](10_m2_problem_generation_architecture.md) §4.1 C3 | 공용 정책(A 리뷰) | 종전 "보존 규칙을 정해달라"에서 축소됨 — **B가 KEEP-1~9로 설계를 닫았고**, 본문은 `ITEM_CANDIDATE`에 두고 state엔 포인터만 둬 §2.4의 "본문 미복제" 원칙을 지킨다 | ☐ 확인 |
| **A-8** | §2-10 문서 동기화 **잔여 4건** — `99_open_items`(B-2 완료 표기) · `part_a/08 §1`(`golden/diagnosis/` 행) · `02_ownership §5`(`golden/diagnosis/` 소유 행) · `00_INDEX`(part_b 링크 절) | §2-10 | 문서(A·공용) | 승인·구현이 끝난 항목의 문서 지연분 | ☐ 잔여 |
| **A-9** | 7/22 감지·API 리뷰 잔여 회신 — ongoing 상한 제외 후 요약 동기화 · 병합 lifecycle 경계 · 회귀/데모 · 공용 실패 meta · 민감 detail 제거 `[P0]` | §1-7 · §1-8 · §2-11 | A(+BE) | B 무관하나 공용 wire 확정에 필요 | ◐ 진행 |
| **A-10** | **"모든 LLM 호출은 gateway 경유 — 예외 없음" 규칙을 `docs/03_coding_rules.md` §2에 승격 완료**(PR #29 · `1d2882c`). 브리핑의 gateway 배선도 `77e016f`로 완료돼 과도기가 종료됐다 | §1-10 | 문서(A 소유) | 규칙의 적용 범위가 A·B 공용으로 확정되고 신규 provider 직결 금지가 공용 규율이 됨 | ✅ 완료(PR #29 · `1d2882c`) |
| **A-11** | ✅ **완료(PR #61).** A가 `tests/ai/contract/test_trace_masking_hook.py`의 추적 활성 표현을 `monkeypatch.setenv` 기준으로 이관하고, `settings=`에는 `LlmSettings(langsmith_tracing=False, _env_file=None)`을 명시해 **가드 발화가 오직 `external_tracing_active()`에서 온다는 것을 구조적으로 보장**했다. OR을 걷은 상태의 사전 증명(전체 스위트 green)도 함께 제공했다 | §2-16 후속 1 · §3 B-14 · `handoff/2026-07-31_tracing_guard_followups_to_B.md` ① | 테스트(A 소유) | gateway 기동 가드의 판정 소스 단일화 — **B의 OR 제거로 해소 완료** | ✅ 완료(PR #61) |

**P0 5건(A-1~A-5)이 M2 착수의 실질 관문이다.** 나머지는 병렬로 진행 가능하다.

> **B가 A에게 요청하지 않는 것(참고):** `t1_light_mode` 시트 변경·`golden/problems/` 코퍼스·`curriculum_graph.yaml`·`verify_config`·`ProblemRequest.requested_difficulty` 신설·GraphRAG 규격 문서는 전부 **B 단독**이므로 A 승인 대상이 아니다.

### A 소유 문서 변경 요청 — 5영역 잔여 `[번호 미부여 · 2026-08-09]`

파일은 B가 직접 고치지 않고 A에게 아래 문면 정합을 요청한다.

| 파일·자리 | 제안 문면 |
| --- | --- |
| `part_a/02_design.md` ERD의 `area_tag` 값 목록 | `language`·`media`·`literature`·`reading`·`speech_writing` 5개로 정정 |
| `part_a/03_usecases.md`의 “수능 6영역 enum” 2곳과 값 목록 | 출제 규격 기준 5영역 enum과 현행 값 목록으로 정정 |
| **`part_a/07_report_spec.md`의 레이더 6축** | **레이더 5축으로 변경 — 화면 산출물 영향이 있으므로 제품 확인 필요** |
| `part_a/09_detect_spec.md`의 `area_tag` 값 목록 주석 | 현행 5개 값으로 정정 |
| `part_a/10_import_spec.md`의 “수능 6영역/유형 enum” | “수능 출제 5영역/유형 enum”으로 정정 |

## §2. 공용 문서·계약 변경 제안 (승인 주체: 02_ownership 절차)

### 공용 문서 변경 요청 — 문 앞 이동 정합 `[번호 미부여 · 2026-08-09]`

- `docs/04_api_contract.md` §3.11의 `source_procurement_not_implemented` 관련 두 400 행은
  “잡을 만든 뒤에 발생해 고아 잡이 남는다”에서 “요청·잡 생성 전 400으로 거절하며 잡과
  AI_RUN을 남기지 않는다”로 정정한다. BE가 고칠 요청이라는 통보 의미는 유지한다.
- `docs/99_open_items.md` #01은 문 앞 이동 축만 해소로 갱신한다. assembly 재던지기 개정과
  Kafka 이관 축은 이번 변경으로 닫지 않는다.
- ⚠ **(2026-08-10) `04`의 날짜 정정은 아직 안 됐는데 가드에서만 빠졌다.**
  `test_recorded_dates_have_passed._PENDING_NOTIFICATION`이 비었다 — 기준일이 `8/10`을
  지나면서 *"아직 안 온 날짜"* 라는 **그 가드의 위반 조건**이 자연 소멸했기 때문이고,
  **문면은 한 글자도 안 바뀌었다.** 정본 `2026-08-07`과의 **값 불일치는 그대로 열려 있다.**

### 양자 승인 요청 — `db/models.py` 영역 주석 정합 `[번호 미부여 · 2026-08-09]`

`src/ai/db/models.py`의 `area_tag` 주석 “수능 6영역”을 5영역으로 바꾼다. 양자 승인 파일이라
B가 고치되 A 승인이 필요하고, 이번 PR 범위 밖이므로 다음 모델 PR에 함께 반영한다.

### 2-1. API·Kafka 계약 증분 `[제안]` — D-10·BE-1 `(C-05)`

현재 정본 계약에 B 엔드포인트가 없다. 아래는 **경로 확정이 아닌 증분 초안** — 공통 규약(envelope·헤더·멱등·202) 준수, 완료 통지는 Kafka(7/15).

| 항목 | 내용 |
| --- | --- |
| `POST /problem-sets` `[가칭]` | 최초 요청 202 + job_id. 내부 command = `ProblemRequest`([`05`](05_problem_generation.md) §4.1 — `target_source` 포함). 같은 Idempotency-Key+같은 바디 재전송은 기존 job 상태·결과 200 |
| `GET /problem-sets/{job_id}` `[가칭]` | **디버그·복구 보조**(폴링 아님) — 상태·진행률·`ProblemSetResult` |
| `POST /problem-sets/{set}/items/{item}/refine` `[가칭 · MVP]` | `instruction`+`base_revision_no` — [`07_refine_policy.md`](07_refine_policy.md). 멱등 조회 → revision/진행 중 검사 순서. 롤백 `revert_to` |
| **Kafka 이벤트** | 공통 `worker_job.succeeded|failed` 사용. 이벤트에는 `job_id`·`operation`·`result_ref|error_code`만 싣고, 문제 세트 status와 성공·실패·미처리 수량은 `result_ref` 조회로 얻는다 |
| 약점 지도 조회 API | **상세 제안 보류** — 세트 응답 동봉 vs 별도 조회 vs 백엔드 사본 동기화는 `OPEN`(D-10, BE+B) |

**B HTTP 경계 확정:** `ProblemRequest`·`ItemRevisionRequest`는 워크플로 내부 command로 유지한다. 외부 HTTP body는 별도 DTO로 만들고 `X-Request-Id`·`Idempotency-Key`·`X-Tenant-Id`를 포함하지 않는다. 공통 헤더를 단일 원천으로 읽어 내부 command에 매핑한다. `api/` 소유권 승인은 완료됐으며, 실제 라우터 편입은 `04_api_contract.md`의 공통 wire 크로스체킹과 백엔드 D-10 합의가 닫힌 뒤 진행한다.

### 2-2. 상태·에러 코드 사전 증보 제안 — `docs/policies/error_codes.md` `(C-10)`

**B 결과 어휘 5종 — ✅ A 정본 편입 완료(7/22, `error_codes.md` §2.6):** 전부 HTTP 에러가 아니라 성공 응답의 `data` 안에 있지만 같은 `status` 필드가 아니다.

| enum · wire 필드 | 값 | 뜻 | 근거 |
| --- | --- | --- | --- |
| `ProblemSetStatus.status` | `partial_success` | 세트 일부 폐기 — 완료분 유효 + stop_reason·사유 보고 | 06 §6 |
| `ProblemItemStatus.items[].status` | `needs_review` | 게이트 통과 + 검토 필수 배지 — 승인 전 발행 불가 | 06 §5 |
| `ProblemItemStatus.items[].status` | `verification_unavailable` | 검증 불능 — 저장 가능·발행 차단·재검증 필수·**수동 우회 불가** | 06 §3 |
| `ProblemFailureReason.items[].failure_reason`·`dropped_reasons[]` | `generation_exhausted` | 재생성 상한(총 3회) 소진 폐기 | 06 §6 |
| `ProblemFailureReason.items[].failure_reason`·`dropped_reasons[]` | `source_unverified` | 사실검증 수단 부재 지문 — 발행 차단 | 05 §2.1 |

**409 멱등 의미 — ✅ A 정본 적용 완료:** 같은 멱등키+같은 바디는 기존 상태·결과 200, 같은 키+다른 바디는 `409 IDEMPOTENCY_CONFLICT`다. 폐기된 구 코드명과 “기존 결과를 409로 반환” 의미는 사용하지 않는다.

**별도 HTTP 충돌 코드 1종 — ✅ A 정본 편입 완료(7/22):** `409 REVISION_CONFLICT`. 새 키+stale `base_revision_no`면 `detail.reason=stale_base_revision`, 새 키+진행 중 refine이면 `detail.reason=revision_in_progress`다. 내부 API에서 두 reason 노출을 허용하며, 멱등 조회를 먼저 수행해 `IDEMPOTENCY_CONFLICT`와 의미를 섞지 않는다.

**refine `blocked_reason` B 증분 3종:** ✅ `answer_integrity` · `banned_topic` · `prompt_injection`은 `error_codes.md` §2.2 편입 완료([`07`](07_refine_policy.md) §3 — `pii_exposure`·`out_of_scope`는 A enum 재사용).

**`ReviewReason`·`DifficultyBand` 편입 `[제안 — A 소유 문서]`:** `docs/policies/error_codes.md` §2.6은 이미 `items[].status`·`failure_reason`·`dropped_reasons[]`를 B 결과 wire 정본으로 관리하므로, BE·FE가 함께 읽는 아래 두 enum도 같은 절에 편입을 요청한다. **값 정의의 소유는 B에 유지하고 공용 사전 편입만 요청한다.** 절차는 `BlockedReason` 3종 편입과 동일하다.

| enum · wire 필드 | 값 | 근거 |
| --- | --- | --- |
| `ReviewReason.items[].review_reason` | `low_confidence` · `area_mismatch` · `t3_literature` · `diagnostic_purpose` · `manual_target_first` · `difficulty_band_mismatch` | [`06`](06_quality_gates.md) §5의 `needs_review` 배지 사유. 폐기 사유인 `failure_reason`과 분리한다 |
| `DifficultyBand.items[].difficulty_band` | `low` · `medium` · `high` | 응답에서 `difficulty_est` 원값과 함께 제공하는 표시·필터용 밴드 |

**프론트 표시 라벨 사전 `[제안 — FE 확정]`:** 내부 상태→한국어 라벨 매핑([`06`](06_quality_gates.md) §0 표) — 검증 통과/검토 필수/검증 차단/생성 실패. 색상만으로 구분 금지, `검토 필수`와 `검증 차단`은 다른 아이콘·문구.

**참고(B 무관 공용 불일치):** ✅ **7/21 통일 완료(`error_codes.md` 정본).**

### 2-3. 공용 enum·계약 확장 — ✅ **A+B 승인 완료 · `d5283d0` 구현 반영** `(C-07·C-09)`

> **상태(7/21):** 아래 확장 중 EVIDENCE_ITEM 행을 제외한 전부가 A와 실시간 협의로 **승인 완료**됐고 커밋 `d5283d0`에 구현·테스트 반영됐다. `GoldenSuite.diagnosis`(`contracts/evaluation.py`)도 같은 승인에 포함. 공용 `error_codes.md` §2.2 동기화도 완료됐다. 아래 표·시안은 제안 당시 이력으로 보존한다.

| 대상 (코드 + ERD) | 변경 | 절차 |
| --- | --- | --- |
| `contracts/execution.py` `Capability` | `diagnosis`·`problem_generation` 추가 — 현 enum은 A 3종뿐(docstring이 "B 테이블 증분 시 함께 확장" 명시) | ✅ 승인·구현 완료(`d5283d0`) |
| `contracts/gates.py` `GateName` + `GATE_RESULT.gate_name` | `RuleValidation \| BlindCrossSolve \| ReleaseDecision` 추가 | ✅ 승인·구현 완료(`d5283d0`) |
| `contracts/gates.py` `OwnerKind` + `GATE_RESULT.owner_kind` | `problem_set` 추가 | ✅ 승인·구현 완료(`d5283d0`) |
| `contracts/gates.py` `BlockedReason` | `answer_integrity`·`banned_topic`·`prompt_injection` 추가([`07`](07_refine_policy.md) §3) | ✅ 승인·구현 완료(`d5283d0`) — ✅ `error_codes.md` §2.2 동기화 완료 |
| `EVIDENCE_ITEM.owner_kind` | `problem_item` 추가 | `evidence/models.py` 구현 시 양자 승인(현재 미구현 — ERD 제안 선반영) |

**제안 당시 코드 시안** (이력 보존 — `d5283d0`에 동일 내용 반영 완료):

```python
# contracts/execution.py — Capability 확장 [제안]
class Capability(StrEnum):
    ...
    DIAGNOSIS = "diagnosis"                      # B — 약점 진단 (결정론 · LLM 금지)
    PROBLEM_GENERATION = "problem_generation"    # B — 출제 워커 ③ (B-1 승인 구조)

# contracts/gates.py — 확장 3종 [제안]
class GateName(StrEnum):
    ...
    RULE_VALIDATION = "RuleValidation"           # B 게이트 ① (06 §1)
    BLIND_CROSS_SOLVE = "BlindCrossSolve"        # B 게이트 ② (06 §2)
    RELEASE_DECISION = "ReleaseDecision"         # B 게이트 ③ (06 §5)

class OwnerKind(StrEnum):
    ...
    PROBLEM_SET = "problem_set"

class BlockedReason(StrEnum):
    ...
    ANSWER_INTEGRITY = "answer_integrity"        # 07 §3 — 정답 구조 훼손 지시
    BANNED_TOPIC = "banned_topic"                # 07 §3 — 금칙 소재 지시
    PROMPT_INJECTION = "prompt_injection"        # 07 §3 · 05 §8.2
```

### 2-4. 공용 ERD 반영 요청 — `docs/06_erd.md` `(C-06)`

B 소유 8테이블(WEAKNESS_MAP·PASSAGE·PROBLEM_SET·PROBLEM_ITEM·VERIFICATION_RESULT·ITEM_REVISION·DIFFICULTY_CALIB·ITEM_CANDIDATE)을 [`02_design.md`](02_design.md) §2와 §2-4.6 기준으로 현재 애플리케이션 26→34테이블로 통합하는 제안이다. 통합 전 정본은 "`docs/06_erd.md`의 26테이블 + part_b 증분 8종"이다.

D-② ERD-parity 안전망은 `tests/ai/db/test_erd_model_parity.py`의 ERD↔`db/models.py` 대조와 `tests/ai/db/test_migration_parity.py`의 모델↔마이그레이션 대조로 연결되므로, B 8테이블 추가 시 `06_erd.md`(정본)+`db/models.py`(양자 승인 12곳)+마이그레이션을 동시에 반영한다.

#### 2-4.1 승인 형태 — 별도 결정 문서 왕복 없음 `[7/27 명확화]`

`02_ownership.md` §4가 "상대방도 코드는 자유롭게 읽고 PR을 보낼 수 있다 — **머지 승인만 오너가 한다**"로 정의하므로, 본 항목의 승인 절차는 **PR 리뷰 자체**다. B가 제안 문서를 내고 A의 결정 문서를 회신받은 뒤 구현에 착수하는 2왕복 절차가 아니다. 구현·문서 diff·테스트를 갖춘 PR이 곧 승인 요청서이며, 멈추는 지점은 **머지 한 곳**이다.

이 PR에서 A 승인이 필요한 항목은 **2건**이다.

| 항목 | 근거 |
| --- | --- |
| B 8테이블의 공용 ERD·ORM 편입 | 본 §2-4 |
| `EVIDENCE_ITEM.owner_kind` += `problem_item` | §2-3 마지막 행 — 공용 계약 B 확장 14항목 중 **유일한 미승인 잔여**. `PROBLEM_ITEM.rationale`의 근거 저장에 선결 |

#### 2-4.2 원자 PR 조건 — 쪼개면 CI가 깨진다

`test_erd_model_parity.py`의 대조는 **양방향**(`orm == erd` — 누락도 초과도 실패)이고, 테이블 수가 상수로 고정돼 있다. 따라서 아래 파일은 **한 PR에 함께** 들어가야 한다.

| 파일 | 변경 | 소유 |
| --- | --- | --- |
| `docs/06_erd.md` | **36 → 44테이블** `[8/4 정정 — §2-4.6 참조]` | A — **A-1 승인 완료, 이 PR 한정 편집 go-ahead** |
| `src/ai/db/models.py` | ORM 8클래스 추가 | 공통 계약(양자) — **A-1 승인 범위** |
| `src/ai/db/migrations/versions/0003_*.py` | 신규 마이그레이션 | 테이블 오너(B) — 모델 diff PR에서 함께 리뷰(§4-1) |
| `tests/ai/db/test_erd_model_parity.py` | `== 26` → `== 34`, `EXPECTED_UNIQUES`에 `weakness_map` (`tenant_id`, `student_ref`, `graph_version`, 주차 컬럼)과 `item_candidate` (`tenant_id`, `set_id`, `slot_index`, `attempt_no`) 2행 추가 | 프로덕션 대칭(양자 성격) |
| `docs/part_b/09_integration_proposals.md` | 본 절 상태 갱신 | B |

`tests/ai/db/test_migration_parity.py`·`test_no_realname_columns.py`는 신규 테이블을 자동으로 검사 대상에 편입하므로 별도 수정이 없어야 정상이다.

#### 2-4.3 8테이블 컬럼 스펙

정본은 [`02_design.md`](02_design.md) §2의 ERD 블록이며, 아래는 그 위에 얹히는 **B 결정 반영분과 확인된 간극**만 적는다.

| 테이블 | 반영분 | 근거 |
| --- | --- | --- |
| `WEAKNESS_MAP` | **`overall_low` boolean 컬럼 추가** — `contracts/diagnosis.py`의 `WeaknessMap.overall_low`가 산출물에 존재하나 §2 ERD 블록에는 컬럼이 없다(확인된 간극) | [`04`](04_curriculum_graph.md) §4 |
| `WEAKNESS_MAP` | `UNIQUE(tenant_id, student_ref, graph_version, 주차)` — 주차 컬럼 표현을 `computed_at` 파생이 아니라 명시 컬럼으로 둘지 확정 필요 | §2 주석 |
| `PROBLEM_SET` | `request` jsonb가 이미 **"난이도"를 포함**한다고 적혀 있으나 `contracts/problem_generation.py`의 `ProblemRequest`에는 난이도 필드가 없다(확인된 간극). 요청 난이도 필드 신설과 함께 정합 | §2 · [`05`](05_problem_generation.md) §4.1 |
| `PROBLEM_ITEM` | **`difficulty_fit` numeric nullable 추가** — 절대 난이도(`difficulty_est`)와 학생 적합도를 분리한다. **v1은 값을 산출하지 않고 항상 null이며 처리 분기 코드를 만들지 않는다** | B-M2-01 = A `[2026-07-27 B 확정]` · 공용 ERD·ORM 편입은 A+B 승인 대상 · [`04`](04_curriculum_graph.md) §1(문항 단위 실측은 B 출제분 제출부터 축적) |

#### 2-4.6 `ITEM_CANDIDATE` — 8번째 테이블 `[KEEP-4 확정 2026-07-27]`

난이도 사유 재생성 시 **첫 검증본 보존**이 확정되면서(KEEP-1) 슬롯 후보 스냅숏 저장소가 필요해졌다. **`PROBLEM_ITEM`에 넣을 수 없는 구조적 이유**가 있다 — `PROBLEM_ITEM`은 슬롯당 1행이고 후보는 `attempt_no`별로 여러 행이라 **키 차수가 다르다.**

| 컬럼 | 타입 | 비고 |
| --- | --- | --- |
| `id` | uuid PK | |
| `set_id` | uuid FK → `PROBLEM_SET` | |
| `tenant_id` | varchar | 전 테이블 공통 |
| `slot_index` | int | `ProblemGenerationState.cursor` 대응 |
| `attempt_no` | int | 1..3 — `item_attempt` 회차 |
| `snapshot` | jsonb | `GeneratedItem` 전문(불변) |
| `gate_summary` | jsonb | ①② 판정 결과·confidence·정렬 판정 |
| `difficulty_est` | numeric | 후보 시점 추정값 |
| `created_at` | timestamptz | |

- **UNIQUE `(tenant_id, set_id, slot_index, attempt_no)`** — `langgraph_state.md` §2.4의 "슬롯 저장 키는 결정론적이며 저장소에서 unique/upsert로 강제"를 후보 축까지 확장한 것.
- **불변 스냅숏**이다. 생성 후 갱신하지 않는다.
- 슬롯 확정 시 승자를 `PROBLEM_ITEM`으로 승격하고, **state의 `fallback_ref`는 clear하되 이 행은 보존**한다(KEEP-8 — 난이도 회귀·골든셋 증보 재료).
- 기각한 대안: `ITEM_REVISION`(강사 수정 이력이라 화면에 오노출) · `VERIFICATION_RESULT.detail`(관측 상세지 본문 저장소 아님) · state 인라인(§2.4 위반) · `PROBLEM_ITEM` 후보 행(수량 불변식 오염).

**연쇄 영향:** B 테이블 **7 → 8**, `test_erd_model_parity.py` 상수 변경. A-1·A-3에 반영했다.

> **`[8/4 정정 — 기준 수가 바뀌었다]`** 종전 표기는 공용 ERD `26 → 34`였으나, A가 기대치 입력 층으로 `PASSAGE_TYPE_STAT`·`EXPECTATION_INGEST` 2개를 신설해(`dbafdb9` · `06_erd.md`) **현행 develop이 이미 36**이다. 따라서 B 8테이블 편입 시 목표 상수는 **`36 → 44`**다. B 테이블 수(8)와 A-1의 승인 범위는 그대로다.

#### 2-4.4 저장소 ORM 규약 (편입 시 준수 — `db/models.py`·`db/base.py`에서 확인)

1. **enum성 컬럼은 PG enum이 아니라 `varchar` + 앱 계층 검증**(D-② 확정). `status`·`verdict`·`stage`·`revision_kind`·`drop_reason`·`stop_reason` 전부 `String`이며 값 검증은 `contracts/`의 `StrEnum`이 담당한다.
2. **`…_ref`는 varchar 논리 참조이고 물리 FK가 아니다.** `test_foreign_keys_match_erd`가 ERD의 FK 마커와 ORM 물리 FK를 대조한다.
3. **실명·연락처 컬럼 없음**(불변식 3) — `DIFFICULTY_CALIB.approved_by_ref`도 alias다. `test_no_realname_columns.py`가 강제한다.
4. 타입 매핑 고정: `uuid=Uuid · varchar=String · text=Text · jsonb=JSONB · int=Integer · numeric=Numeric · timestamptz=DateTime(timezone=True) · boolean=Boolean`.
5. 제약·인덱스 이름은 `db/base.py`의 `NAMING_CONVENTION`을 따른다(alembic autogenerate diff 안정화). `UniqueConstraint`는 `uq_<table>_<의미>` 형태로 이름을 명시한다.

#### 2-4.5 ✅ 해소 — 현행 앱 계층 격리 확정 · RLS 실도입은 BE-11 이관

[`02_design.md`](02_design.md) §2와 `docs/06_erd.md`의 원칙은 "전 테이블 `tenant_id`+**RLS**"로 적혀 있으나, **현재 저장소에는 RLS가 구현돼 있지 않다** — `src/ai/` 전체에서 `ROW LEVEL SECURITY`·`CREATE POLICY` 검색 결과 0건이며, 기존 26테이블은 `tenant_id` varchar 컬럼만 두고 격리를 앱 계층에서 수행한다.

**A 판정 완료:** 문서 표현을 현행 구현인 **전 테이블 `tenant_id` 컬럼 + 애플리케이션 계층 격리**로 정정한다. B 8테이블도 기존 26테이블과 동일한 패턴으로 편입하며, B 테이블에만 RLS를 도입하지 않는다.

RLS 실도입 여부는 `db/session.py`·`db/store_factory.py`의 연결·역할 설계와 함께 다뤄야 하므로 **BE-11(백엔드 합의 안건)**로 이관한다. 도입 시 기존 26테이블과 B 8테이블에 일괄 적용하며, 테이블마다 격리 방식을 갈라놓지 않는다.

### 2-5. `docs/02_ownership.md` §5 트리 증보 제안

`evaluation/golden/problems/` 하위 7종([`08`](08_evaluation_plan.md) §1)·`golden/diagnosis/` 신설 행(전부 [염준영]). `pg_banned_topics.yaml`을 B 데이터 파일 규칙(§4-5 — 골든 통과가 머지 조건)에 추가.

### 2-6. `docs/99_open_items.md` 증분 제안 — B 산출물 등록

| 항목 | 선결 |
| --- | --- |
| 문법 DAG 실제 YAML 25~40노드 (`curriculum_graph.yaml`) | B-3 잔여(경계 사례) |
| `golden/problems/` 코퍼스 실파일(§2 18건·§3 11건·§4 10건·§7·§8·§9 RF1~11) | — |
| `golden/diagnosis/` 회귀 실파일 | ✅ 완료 |
| `t1_reference/` 케이스 | D-03 |
| `pg_banned_topics.yaml` 실파일 | — |
| B 기본값 시트 `verify_config` v1 실데이터 | — |
| `contracts/diagnosis.py`·`problem_generation.py` 초안 | ✅ 완료(`d5283d0`) |

### 2-7. `docs/05_request_json.md` 주석 수정 제안 `(C-03)`

learning_events의 `item_format` 주석 "R6·약점 지도가 **형식별로 분리 집계**"가 판정 축 포함으로 오독될 수 있다. 정정 제안: **"편중·약점 판정 축은 `area_tag × type_tag`, `item_format`은 분리 리포팅만"**(+ v1 데이터는 mcq 중심).

### 2-8. `docs/00_INDEX.md` 링크 추가 제안

'B 파트 상세 (`part_b/`)' 절 신설 — 01~09 문서 행 추가. 승인 후 반영.
§2-8 추가 제안: [`12_suneung_format_alignment.md`](12_suneung_format_alignment.md) 수능형 문항 포맷 적합성·확장 경계 행을 같은 절에 등록한다.

### 2-9. `contracts/execution.py` `VersionSet` B 확장 — ✅ **A+B 승인 완료 · `d5283d0` 구현 반영**

> **상태(7/22):** 아래 4필드 확장(+`RunMetadata`·`to_run_metadata` 동시 확장)은 A와 실시간 협의로 **승인 완료**됐고 커밋 `d5283d0`에 구현·테스트 반영됐다. `04_api_contract §2.2`·`06_erd AI_RUN` 문서 동기화도 완료됐다. 아래는 제안 당시 근거·시안의 이력 보존이다.

(제안 당시 배경) `VersionSet`은 6종 고정(`extra="forbid"`)이라 B 실행의 재현 키가 실릴 자리가 없다. `threshold_version`(detection 전용 nullable)과 같은 선례로 **B 전용 nullable 필드 확장**을 제안:

| 필드 | 의미 | null 조건 |
| --- | --- | --- |
| `graph_version` | curriculum_graph.yaml 버전 | 진단·출제 외 실행 |
| `taxonomy_version` | contracts/taxonomy 어휘 버전 | 〃 |
| `verify_config_version` | B 기본값 시트([`06`](06_quality_gates.md) 부록) | 〃 |
| `difficulty_calib_version` | 난이도 보정 버전 | 〃 |

- 근거: 불변식 8 — 이 버전들 없이는 과거 진단·검증 판정을 재현할 수 없다(threshold_version과 동일 논리 — 스키마는 합집합, 실행별 차이는 nullable로 흡수. §1-5 회신의 교집합 기각 사유와 동일).
- 대안(확장 부결 시): 산출물 행(WEAKNESS_MAP·PROBLEM_ITEM)에만 저장하고 meta.versions는 6종 유지 — 단 API 응답만으로 재현 키를 못 얻는 비대칭 발생.
- (제안 당시 절차 항목) 양자 승인 + `04_api_contract §2.2`·`06_erd AI_RUN` 동시 개정 대상 → ✅ **승인·구현·두 문서 동기화 완료**.

**제안 당시 코드 시안** (이력 보존 — `d5283d0`에 동일 내용 반영 완료 · `threshold_version` docstring 패턴 준용):

```python
# contracts/execution.py — VersionSet B 확장 [제안 · 양자 승인]
class VersionSet(BaseModel):
    ...
    graph_version: str | None = None
    """curriculum_graph.yaml 버전 — **diagnosis·problem_generation 실행에만 의미.**
    그래프 개정은 버전으로만 이뤄지므로(part_b/04 §6) 이 값 없이는 과거 약점
    판정을 재현할 수 없다. A capability 실행에서는 None."""

    taxonomy_version: str | None = None
    """어휘 버전 — 진단·출제 실행 외 None (태깅ⓒ 등 A 소비처 확장 시 재론)."""

    verify_config_version: str | None = None
    """B 판정 파라미터 시트 버전 (part_b/06 부록) — 출제 실행 외 None."""

    difficulty_calib_version: str | None = None
    """난이도 보정 버전 — 출제 실행 외 None."""
```

### 2-10. 승인 결과 공용 문서 동기화 실제 현황 `[7/22 재대조 — 3/7 완료, 4건 잔여]`

공용 계약 B 확장 14항목의 승인·구현(`d5283d0`)은 완료됐다. 7/22 원본을 다시 대조한 결과 아래 7건 중 3건은 이미 반영됐고 4건이 남아 있다.

| 문서 | 소유 | 요청 내용 | 실제 상태 |
| --- | --- | --- | --- |
| `docs/04_api_contract.md` §2.2 | 공용 | meta.versions = 공통 6종 + B nullable 4종 | ✅ 완료 — 10키 예시·정본 규칙 반영 |
| `docs/06_erd.md` | A | AI_RUN B 버전 4컬럼·capability 2종, GATE_RESULT B enum 반영 | ✅ 완료 — B 8테이블 통합은 §2-4 별도 |
| `docs/99_open_items.md` | 공용 | B-2 승인·구현 완료 상태 반영 | ☐ 잔여 — B-2가 아직 미완료 표기 |
| `docs/policies/error_codes.md` §2.2·§2.6·§1 | A | BlockedReason 3종·B 결과 어휘 5종·`REVISION_CONFLICT` 편입 | ✅ 완료 — 세 범주 분리·409 reason 공개 범위·멱등 선조회까지 정본 반영 |
| `docs/part_a/08_evaluation_plan.md` §1 | A | `golden/diagnosis/` 행 추가 | ☐ 잔여 |
| `docs/02_ownership.md` §5 | 공용 | `golden/diagnosis/` [염준영] 소유 행 추가 | ☐ 잔여 — `api/`·FakeSnapshot 소유 반영과는 별개 |
| `docs/00_INDEX.md` | 공용 | part_b 문서 9종 링크 절 신설 | ☐ 잔여 |

※ Kafka 완료 이벤트는 공통 `worker_job.*` 계약으로 확정됐다. B API 경로와 `result_ref` 조회 방식은 **백엔드 합의(D-10) 전 — 제안 유지**다.

### 2-11. `api/` 공통 계층 B 리뷰 — 구조 승인 완료·wire 크로스체크 잔여 `(99 ⑧·⑨)`

> **B 회신:** `app.py`·`envelope.py`는 공통 양자 승인, capability 라우터는 해당 오너 소유로 확정 승인했다(`02_ownership.md` §4·§5, 99 ⑧). 아래 wire 간극은 이번 독립 리뷰에서 새로 발견해 `04_api_contract.md`·`error_codes.md`의 관련 조항에 해결안과 확인 요청을 남겼다. 해당 정본 값은 바꾸지 않았다.

| 항목 | 원본 크로스체킹 위치 | 상태 |
| --- | --- | --- |
| 소유권 | `02_ownership.md` §4·§5 | ✅ B 승인·9곳 편입 완료 |
| 실패 envelope | `04_api_contract.md` §2.2 | ◐ detect 경로의 실패 `meta.versions` 구현·테스트 완료, 공용 `error_envelope`의 versions 필수화 또는 전 라우터 조립 보장 잔여(양자) |
| 요청 검증 | `04_api_contract.md` §2.3 · `error_codes.md` §1 | ◐ `INVALID_SCHEMA` 범위 확정·detect 구현 완료, 공용 재사용 handler 보장 잔여 |
| LLM 예외 매핑 | `04_api_contract.md` §2.3 · `error_codes.md` §4 | ◐ `runtime/errors.py` adapter 위치·정본 트리 확정, `contracts.llm` 실제 예외 연결·503/504 통합 테스트 잔여 |
| 민감 detail 강제 제거 | `04_api_contract.md` §2.3 · `error_codes.md` §4 | ☐ 공통 handler의 `RedactionUncertain.detail` 제거 보장·통합 테스트 확인 `[P0]` |
| tenant/경로 멱등 스코프·서버 digest·TTL·영속화 | `04_api_contract.md` §2.3 | ☐ D-② DB 교체 안건(99 ⑨)으로 이관 `[P0]` |
| tracing | `04_api_contract.md` §2.3 | ◐ `X-Request-Id` 응답 echo 완료, 로그 correlation 구현 근거·회귀 잔여 |
| B HTTP DTO | §2-1 | ✅ 외부 body DTO와 내부 command 분리 확정 |
| 동의 오류 경계(404/422) | `policies/error_codes.md` §1 | ◐ A 구분안 채택·정본 반영, 백엔드 보안 의도 확인 대기 |

### 2-12. GraphRAG 지식 계층 — 공용 계약 확장 `[제안 · 2026-07-27]` `(A-4·A-5)`

GraphRAG 채택은 확정이다([`10`](10_m2_problem_generation_architecture.md) §1·§4.2). 규격은 **[`11_graphrag_knowledge_layer.md`](11_graphrag_knowledge_layer.md)로 정본 편입 완료**(B 단독)이며, **아래 2건만 양자 승인 파일을 건드리므로 B 단독으로 확정하지 않는다.** A는 `11` 문서를 함께 보면 된다 — §0에 소유·승인 경계표가 있다.

#### 2-12-①. `contracts/execution.py` `VersionSet` — GraphRAG 3필드 `[A-4]`

| 필드 | 의미 | null 조건 |
| --- | --- | --- |
| `content_graph_version` | 콘텐츠·근거 그래프(Document·Chunk·Claim·Rule·License)의 스키마 버전 | GraphRAG를 경유하지 않는 실행 |
| `graph_index_version` | 색인 스냅숏 버전 — 같은 그래프라도 색인이 갱신되면 검색 결과가 달라진다 | 〃 |
| `retrieval_config_version` | 검색 파라미터(top-k·필터·재랭킹) 시트 버전 | 〃 |

- **근거:** 불변식 8(재현성). 이 셋이 없으면 "그때 그 ContextPack이 왜 그 근거를 골랐는가"를 재현할 수 없다. `threshold_version`·B 버전 4종(§2-9)과 **동일한 선례**다 — 스키마는 합집합, 실행별 차이는 nullable로 흡수.
- **⚠️ 이름 재사용 금지(핵심):** 기존 `VersionSet.graph_version`은 **교육과정 DAG 버전**이며 이미 A+B 승인·구현 완료(`d5283d0`, §2-9)다. 콘텐츠 GraphRAG 버전으로 **재사용하지 않는다.** GraphRAG 설계서도 이 충돌을 직접 경고한다.
- **A-4 조건 ① — 동시 개정 절차:** 필드 추가로 `meta.versions`는 **10 → 13키**가 된다. `contracts/execution.py`와 함께 `docs/04_api_contract.md` §2.2의 키 수·예시와 `docs/06_erd.md`의 `AI_RUN` 3컬럼을 **같은 PR에서 함께 개정**한다. 백엔드 통보는 A가 BE-1(v1.0 승격)과 묶어 수행한다.
- **A-4 조건 ② — 축 독립성:** `graph_index_version`과 `retrieval_config_version`은 실제로 독립적으로 움직인다. ① 색인을 재빌드하지 않고 top-k·필터 조건·재랭킹 on/off 같은 검색 파라미터만 바꿀 수 있고, ② 반대로 검색 파라미터는 유지한 채 승인 자료 추가나 라이선스 만료 자료 제외로 색인만 갱신할 수 있다. 두 경우 모두 같은 질문에서 다른 근거가 선택되지만 원인이 다르므로, 한 필드로 합치면 재현 실패 원인을 분해할 수 없다(불변식 8).
- **확장 부결 시 대안:** 산출물 행에만 저장하고 `meta.versions`는 유지 — 단 API 응답만으로 재현 키를 못 얻는 비대칭이 §2-9과 동일하게 발생한다.

#### 2-12-②. `evidence/` resolver 주입 시그니처 — B-7 안건과 병합 `[A-5]` `✅ A 승인·구현 완료(PR #37) · B 사후 검증 완료 2026-07-30`

`evidence/`는 A 소유다. GraphRAG 도입으로 근거 해소 경로가 "앵커 → 원문 직접 조회"에서 "앵커 → `EvidencePack`(path·quote·license·hash) 대조"로 확장되므로 기존 B-7과 하나로 묶어 확정했다. **A가 B 요구 조건 5개를 전량 수용하고 PR #37로 구현·머지했다** — `src/ai/evidence/models.py`(177줄) · `resolver.py`(249줄) · 테스트 3종.

**B 사후 검증 결과 `[2026-07-30]`** — `evidence/models.py`는 양자 승인 12곳 중 하나이므로 B 검증 기록을 남긴다. 5개 조건 전부 충족을 코드에서 확인했다.

| 조건 | 판정 | 근거 |
| --- | --- | --- |
| 1 fail-closed | ✅ | `EvidenceResolutionFailed` — `ResolvedEvidence.resolved`가 `min_length=1`이라 빈 성공 결과를 **타입으로** 만들 수 없다 |
| 2 권리 게이트 | ✅ | `_APPROVED` 상수 · `ExclusionReason.RIGHTS_NOT_APPROVED` · Pack 단계 1차 차단 후 resolver 이중 확인임을 명시 |
| 3 결정론 | ✅ | `classify_anchor` 순수 함수 · 시계·난수 미생성 · `EvidenceRef.id`도 입력에서 파생 |
| 4 예산 불변 | ✅ | 내부 재시도 루프 없음 · `tenacity` 미사용 |
| 5 blind 무오염 | ✅ | `tests/ai/contract/test_evidence_blind_isolation.py` — AST 3중 검사(verifier 경로의 `ai.evidence` import 차단 · `blind_item` 리터럴 키 검사 · `SolveResult` 역방향 유출 차단). **B가 요구한 수준을 넘는다** |

조건 5의 구현이 [`12`](12_suneung_format_alignment.md) FMT-6 머지 조건 (1)을 이미 충족한다.

**A-5 초안 기준 시그니처(필드·순서 고정):**

```python
@runtime_checkable
class EvidenceResolver(Protocol):
    async def resolve(
        self,
        *,
        pack: EvidencePack,
        anchor_ids: Sequence[str],
        tenant_id: str,
        owner_kind: EvidenceOwnerKind,
        owner_id: UUID,
    ) -> ResolvedEvidence: ...
```

`*` 뒤의 다섯 인자는 키워드 전용이며 이름과 순서를 바꾸지 않는다. `EvidencePack`·`EvidencePackAnchor`·`NonEmptyStr`·`Sha256Hash`는 `ai.contracts.graphrag`에서 import하고 `evidence/`에서 재정의하지 않는다.

| 결과 타입 | 고정 필드 | 규약 |
| --- | --- | --- |
| `ResolvedEvidence` | `evidence_pack_id` · `resolved` · `excluded` | `resolved`는 최소 1건. 해소 0건은 빈 성공이 아니라 `EvidenceResolutionFailed` |
| `ResolvedAnchor` | `anchor_id` · `evidence_ref` · `source_content_hash` · `quote` | 저장 근거와 대조를 통과한 값. `quote`는 verifier 페이로드에 전달 금지 |
| `ExcludedAnchor` | `anchor_id` · `reason` | 권리 미승인·본문 hash 불일치·인용 불일치·미존재를 조용히 누락하지 않음 |

**경계는 세 줄로 고정한다.**

1. `GraphContextService.verify_evidence_paths(evidence_pack) -> EvidencePathResult`(**현행 B 소유** · §2-14 승인 시 A+B 공용 계약)는 앵커와 `graph_path_edge_ids`의 그래프 내부 실존을 검증한다. 결과는 `valid`·`checked_anchor_ids`·`invalid_anchor_ids`만 가지며 해소 본문을 담지 않는다. **`quote_hash`·`license_ref` 유효성은 이 검사의 책임이 아니다** — 반환 3필드에 이를 표현할 자리가 없다([`11`](11_graphrag_knowledge_layer.md) §5).
2. `EvidenceResolver.resolve(...) -> ResolvedEvidence`(**A evidence 구현**)는 앵커를 실제 저장 근거로 해소하고 `quote`·`source_content_hash`·권리를 대조한다. `EvidencePathResult`의 필드명이나 의미를 재사용하지 않는다.
3. 게이트 ① **R-1은 두 검사를 모두 통과해야 pass**한다. 어느 한쪽의 실패도 다른 쪽의 성공으로 상쇄하지 않는다.

resolver가 만족해야 하는 조건(B 요구):

1. **fail-closed** — 해소 실패·검색 0건에 대해 빈 결과를 반환하지 않고 실패를 신호한다. 상위에서 `verification_unavailable`로 수렴시킨다([`06`](06_quality_gates.md) §3).
2. **권리 게이트** — `rights_status != approved` 자료는 해소 대상에서 제외한다.
3. **결정론** — 같은 `(EvidencePack, anchor)` 입력에 같은 결과. 시계·난수 주입 금지.
4. **예산 불변** — resolver 내부에 별도 재시도 루프를 두지 않는다. 문항당 `item_attempt` 총 3회를 그대로 소모한다(FIX-06).
5. **blind 무오염** — resolver 반환값이 verifier 페이로드로 흘러들지 않는다([`05`](05_problem_generation.md) §4.3).

#### 2-12-③. B 단독으로 진행하는 부분 (A 승인 불요 — 참고)

`docs/part_b/` GraphRAG 규격 문서 신설, [`05`](05_problem_generation.md) §4.2의 `EvidenceAnchor`↔`EvidencePack` 대응, [`06`](06_quality_gates.md) §1의 R-1·R-4 확장, [`07`](07_refine_policy.md) §4의 `ResolveRevisionContext` 선행, 자료 권리 매니페스트 스키마, 검색 모드(`reuse_only`·`delta_retrieve`) 규약.

### 2-13. `type_tag` 어휘 확장 `[제안 · A+B 양자 승인]` `(W9)`

**근거.** 2027학년도 6월 모의평가 국어 45문항(공통 34문항 + 선택 11문항)을 현행
4종(`fact`·`infer`·`critic`·`concept`)으로 분류하면 약 18문항만 무리 없이
분류된다. 다음 문항군은 현재 어휘로 측정 능력을 정직하게 표현할 수 없다.

- 어휘 문항 9·17: 매회 고정 2문항으로 출제되지만 현행 4종 어디에도 속하지 않는다.
- 글의 전개 방식·공통점 문항 4·10·22.
- 문학 표현·서술 방식 문항 19·20·26·27·32.
- `<보기>` 외적 준거 적용·감상 문항 3·8·13·16·21·24·31·34:
  `infer`로 밀어 넣으면 강사가 이를 “추론 약점”으로 오독한다. 외적 준거 적용은
  추론과 구별해 진단해야 하는 능력이다.
- 화법과작문 11문항 전체와 매체 6문항 전체.

**제안.** 어휘 의미, 글의 조직·표현 방식, 외적 준거 적용·감상, 화법·작문·매체
수행을 서로 구별해 진단할 수 있도록 `type_tag`의 표현 범위를 확장한다. 이 절은
필요한 능력 범주와 확장 방향만 제안하며, **신규 enum 식별자와 최종 개수는
확정하지 않는다.** `contracts/taxonomy.py`는 양자 승인 파일이므로 실제 어휘와
경계 사례는 A+B가 태깅 골든셋과 함께 확정한다.

**승인 시 동시 개정 대상 — 기존 3건 + 확장 4건.**

1. `docs/policies/taxonomy.md`의 어휘·경계 사례.
2. `area_tag × type_tag` 진단 셀 구조.
3. 약점 지도 API 응답.
4. **R6 임계 재산정(A).** `detection/thresholds.py`의 R6(오답 유형 편중)은
   `area_tag × type_tag` 24셀 기반이다. type이 N종이면 6×N셀로 늘어 셀당 표본이
   희석되므로, [`part_a/04_threshold_config.md`](../part_a/04_threshold_config.md)
   §1의 R6 최소 표본·편중 임계를 그대로 두면 R6가 조용히 죽거나 오발동한다.
   어휘 확정과 임계 재산정을 같은 결정 단위로 묶는다. 근거는
   `docs/02_ownership.md` §3의 `taxonomy.py` 행에 이미 명시된
   “감지 R6·약점 지도·태깅ⓒ·출제가 전부 이 어휘를 씀”이다.
5. **`taxonomy_version` 승격 + 기축적분 혼재 정책(A+B).**
   `VersionSet.taxonomy_version`은 재현 키이므로 어휘 변경은 불변식 8 사안이다.
   이미 쌓인 `FEATURE_WEEK` 셀 통계·`SIGNAL`이 옛 4종 기준이므로 구 어휘 매핑,
   재계산, 버전별 분리 중 하나를 정한다. `docs/policies/taxonomy.md` §1.1의
   “기존 3갈래 → 신규 매핑” 선례와 같은 형식으로 기록한다.
6. **`docs/policies/taxonomy.md` §3 제목·결정 로그(A).**
   현재 제목은 “`type_tag` — 인지 유형 (area와 직교, 변경 없음)”이며, 7/15에
   “변경 없음”으로 확정한 축이므로 `docs/99_open_items.md` 결정 로그 갱신이 선행한다.
7. **`docs/06_erd.md`의 `type_tag` 2곳(A).** A 회신으로 확인된 위치는
   `PROBLEM_ITEM`(:298)과 `TAG_SUGGESTION`(:442)이다. A 소유 문서이므로 A가 둘 다
   처리한다. 같은 자리인 `docs/part_b/02_design.md`의 `PROBLEM_ITEM`은 B가 이미
   완료했다.

이 제안에서는 위 파일을 직접 수정하지 않는다. `golden/tagging/` 라벨 확정은 A+B
공동 부담이므로 어휘 초안 후 라벨링 일정을 조율하고, B-3 경계 7건과 같은 단위로
묶는 것이 효율적이라는 A·B 합의를 따른다. **신규 enum 식별자와 최종 개수는
확정하지 않는다.**

**부결 시 대안.** 셀을 현행 6×4로 유지하고 `skill_node`만 세분한다. 계약 변경은
피할 수 있지만, 4종에 매핑할 수 없는 어휘·화법·작문·매체 약점은 셀 진단으로
표현할 수 없어 **진단 자체가 불가능**해지는 비용을 감수해야 한다.

### 2-14. `contracts/graphrag.py` 소유 미등록 — 양자 승인 편입 `✅ A+B 승인 완료 · A 반영 완료(PR #49 · 02_ownership v5)` `(B-12)`

**승인·반영 완료.** A가 먼저 제기하고 B가 수용한 `contracts/graphrag.py` 공용
계약 편입을 A+B가 승인했으며, A가 PR #49로 `docs/02_ownership.md` v5와
`CLAUDE.md`에 반영했다. 양자 승인 대상은 **12곳 → 13곳**이다.

**반영 완료 5곳.**

1. `docs/02_ownership.md` 제목을 v5로 올리고, :7의 v3→v4 이력을
   “12곳이었다(v5에서 13곳)”로 정정했으며, :9에 v4→v5 이력을 추가했다.
   이 중 :7 정정은 B 제안에 없었고 A가 추가로 찾아냈다.
2. `docs/02_ownership.md` §3 파일 표에 `graphrag.py`를 공용 계약으로 등록하고,
   **B가 생산하고 A `evidence/`가 소비해 생산·소비가 갈린다**는 양자 사유를 기록했다.
3. `docs/02_ownership.md` §4의 양자 승인 대상을 **13곳**으로 갱신했다.
4. `docs/02_ownership.md` §5 소유권 주석 트리에 `graphrag.py` 행을 추가했다.
5. `CLAUDE.md` §2의 양자 승인 대상을 **13파일**로 갱신했다.

**근거.** [`11`](11_graphrag_knowledge_layer.md) §0과 §2-12-②(PR #37)에 따라
A 소유 `evidence/`가 B가 생산한 `EvidencePack`·`EvidencePathResult`를 직접
소비한다. 동일 형상을 두 곳에 만들지 않고 `contracts/graphrag.py` 하나를 공용
경계로 두는 것으로 소유와 실제 생산·소비 구조가 일치했다.

**부결 시 대안(승인으로 무효).** 종전에는 `contracts/graphrag.py`를 B 단독으로
유지하고 A가 `evidence/` 안에 별도 프로토콜을 재정의하는 안을 남겼으나, PR #49
승인·반영 완료로 이 대안은 적용하지 않는다.

**승인 전 처리(종료).** [`11`](11_graphrag_knowledge_layer.md) §0의
`[제안 — 미승인]` 표기와 “현행 B 소유 · §2-14 승인 시 A+B 공용 계약” 병기는
이번 동기화로 해소한다.

### 2-15. blind 격리 범위 — R-1 resolver 연동 차단 `✅ A 승인·PR #40 구현 완료` `(B-13)`

**사실.** `tests/ai/contract/test_evidence_blind_isolation.py`의
`_VERIFIER_PACKAGES = ["problem_generation"]`은 패키지 전체에서 `ai.evidence`
import를 금지했다. AST walk가 `ImportFrom`·`Import`를 모두 잡으므로
`TYPE_CHECKING` 아래 타입 전용 import도 걸렸다.

**충돌.** [`06`](06_quality_gates.md) §1의 R-1은 근거 해소 통과 확인이 필요하고,
R-1은 `src/ai/problem_generation/verification.py:272`에 있다. §2-12-②의 확정
분담대로 R-1은 A resolver 통과도 확인해야 하므로, R-1의 GraphRAG 확장을 구현하는
순간 종전 테스트가 실패한다.

**A 판정 근거.** [`06`](06_quality_gates.md) §1은 게이트 ①을 `RuleValidation`
“결정론 코드”로, 교차 풀이를 게이트 ②로 분리한다. R-1은 verifier가 아니다.
문서상 근거가 명확하므로 종전 테스트가 과잉이었다고 A가 인정했다.

**결정 — B 권고 (가)안 채택.**

```python
_VERIFIER_PACKAGES = ["problem_generation"]
# ↓
_BLIND_PAYLOAD_MODULES = ["problem_generation/cross_solver.py"]
```

blind 제약의 대상은 verifier에게 보내는 페이로드이고, 실제 경계는
`src/ai/problem_generation/cross_solver.py:45`의 `blind_item` 리터럴이다.

**기각한 대안.** `ResolvedEvidence`·`EvidenceResolver`를 `contracts/` 경유로
노출하는 안. `ResolvedEvidence`를 A 단독 타입으로 둔 것은 양자 승인 표면적을
줄이려는 결정이므로, `contracts/`로 올리면 그 결정을 되돌린다.

**`TYPE_CHECKING` 예외 없음.** 검사 대상을 한 파일로 좁히면 예외가 불필요하고,
예외 자체가 blind 경계의 구멍이 된다.

**범위 축소의 반대급부 — 화이트리스트 강화.** 종전 금칙 키 검사는 블랙리스트라
새 키가 추가돼도 걸리지 않았다. 현재 6키를 화이트리스트로 고정해 정확 일치를
검사하므로, FMT-6로 학생 가시 자료 블록을 넣을 때 사람이 한 번 확인하게 된다.

**B 후속 2건.**

1. R-1의 GraphRAG 확장 착수가 가능해졌다([`06`](06_quality_gates.md) §1 ·
   [`11`](11_graphrag_knowledge_layer.md) §5).
2. FMT-6 자료 블록 추가 시 화이트리스트 동시 갱신을 머지 조건으로 둔다
   ([`12`](12_suneung_format_alignment.md) FMT-6 머지 조건).
### 2-16. LangGraph 트레이스 경로 `[제안 · A+B]` `[P1]`

**실측 조건과 유효 범위.** 근거는 [`part_a/11`](../part_a/11_langsmith_trace_probe.md)
§1.1·§2·§3·§4·§5·§6·§7·§8이다. `langsmith 0.10.2` · `langgraph 1.2.9` ·
`langchain-core 1.4.9` · Python 3.12, `InMemorySaver`, 전부 합성 데이터,
`.env` 무수정 조건에서 측정했다. 버전이 바뀌면 재실측해야 하며, 로컬 PG 미가용으로
`PostgresSaver`는 이 조건에 포함되지 않았다.

**실측 결론.** `(b)` 훅은 LangSmith 기록에 **효력이 없다.** 프롬프트에 탐침 마커를
붙인 요청이 provider까지 전달되고 호출도 성공했지만 수집된 span 16개 전부에 마커가
등재되지 않았다. 프롬프트 자체가 트레이스에 실리지 않아 가릴 대상이 없고, 실제로 실리는
`emphasis_points` 등은 훅이 닿지 않는 LangGraph 노드 state 경계다. `(b)` 훅의 실효는
`(a)` redaction을 건너뛴 요청을 provider 전송 전에 차단하는 fail-closed다. **가리는
것이 아니라 막는 것이며, P1·P1'의 의미도 기동 가드 충족이지 트레이스 방어가 아니다.**

**위험 분포 — state 정책 판정과 span 실측을 분리한다.**

| 경로 | state·호출 형태 | 판정 |
| --- | --- | --- |
| problem_generation (B) | `request_ref`·`request_hash`·`items`·`fallback_ref` | ✅ `langgraph_state` §2.4 "본문 미복제" 정책 준수. 이번 counsel_pack 탐침의 span 실측으로 승격하지 않는다 |
| mapping_probe (A) | `sheets_meta={"columns": 컬럼명만}` · `steps[].observation_masked` | ⚠ **span·필드 미확인.** §2.2는 state 정책 판정이며 이번 탐침은 counsel_pack만 실행했다. 같은 LangGraph 경로라도 도구 호출 span이 추가될 수 있어 추정하지 않는다 |
| counsel_pack (A) | `context_ref`+`context_hash` · `results[].draft_id` · `emphasis_points` | 포인터화 정책·구현은 완료됐지만, 실측에서 `emphasis_points`의 근거 라벨·수치·`record_id`가 문면 그대로 등재됐다. §1.2 불변식 ①에 따른 설계이며 **P2의 1순위 노출면**이다 |
| briefing | LangGraph·langchain-core `Runnable` 비경유 · raw `AsyncOpenAI`(`wrap_openai` 미사용) | ✅ span 0건 — LangSmith 프로젝트가 생성되지 않아 위험 표면에서 제외 |

종전 B 초안은 `probe/stores.py:113`의 `sample_rows`를 구멍으로 지목했으나, 이는
`SourceProfile` 저장소 역직렬화 경로이며 LangGraph state가 아니다. A 정정을 반영해
철회한다. 가드레일 근거는 `profiling.py:55`·`:155`, `contracts/imports.py:101`에 있다.

**응급조치.** `LANGSMITH_TRACING=false`. 다만 이것만으로 끝내지 않는 이유는 세
가지다. 환경 플래그는 재발 방지가 0이고(이번에 켜진 것이 그 증거), [`01`](01_pipeline.md)
§5가 B-6으로 LangSmith 도입을 확정했으므로 영구 false는 그 결정을 되돌리며, 멀티노드
LangGraph를 트레이스 없이 디버깅하는 실질 손실이 있다.

포인터·alias 정책과 `(a)` redaction으로 실명·연락처 유입 경로가 없으므로 **불변식 3의
활성 유출 경로는 해소됐다.** 다만 `emphasis_points`의 정답률·근거 라벨·논리 참조
`record_id`는 학습 정보의 IP·프라이버시 노출면이며 P2가 다뤄야 한다.

**분담과 재활성화 4단.**

- **P0 (A) ✅ 해소 — 정책(PR #43)과 구현(`counsel/state.py`) 모두 확인. 잔여 없음.**
  `contexts: dict[str, DraftContext]`를 `context_ref`+`context_hash`로 바꾸고, 초안
  본문은 `results[].draft_id`로만 가리킨다. `StudentResult`는 `student_ref`·`draft_id`·
  `status`·`fail_reason`만 가지며, `summary`는 생성·부족·실패 수량 요약이라 PII가
  없다. 재개 시 `context_ref` 역참조 해시를 `context_hash`와 대조하는 불변식 ④도
  구현했으며, `counsel/state.py:28`은 `ProblemGenerationState.request_hash`와 같은
  형식이라는 §2.4 대칭을 명시한다.
- **P1 (B) ✅ 완료.** PR #48로 가드를 세웠으나 `settings.langsmith_tracing` 하나만 봐
  `LANGCHAIN_TRACING_V2` 등으로 우회됐고, 동의어 4종 확장(#59 · `5d8e1a4`)이 판정을
  `external_tracing_active()`에 위임해 우회를 닫았다. A 계약 보존을 위해 남겨뒀던
  `settings.langsmith_tracing` 보조 OR은 A-11(PR #61) 해소 후 제거해 **판정 정본이
  하나가 됐다**. 이 단계는 기동 가드이며 트레이스 은닉이 아니다.
- **P1' (A) ✅ 완료(PR #51).** `src/ai/runtime/trace_masking.py`를 신설하고
  `composition/provider.py:112`(briefing)·`composition/counsel/assembly.py:56`
  (counsel_pack) 두 조립부에 `(b)` 훅을 주입했으며
  `tests/ai/contract/test_trace_masking_hook.py`로 회귀를 고정했다.
- **부속 확인 (A+B) ✅ 완료(PR #51).** 실제 span·입출력을
  [`part_a/11`](../part_a/11_langsmith_trace_probe.md)에서 측정해 `(b)` 훅의
  LangSmith 기록 효력이 없음을 확정했다.
- **P2 (A) ☐ 미완.** LangSmith 클라이언트 입출력과 LangGraph 체크포인터 serde를
  각각 은닉한다. **P1·P1'·부속 확인·P2 중 하나라도 미완이면 TRACING을 켜지 않는다.**

**P2 제어점.** `langsmith 0.10.2`의 실제 은닉 위치는
`Client(hide_inputs=)`·`hide_outputs=`·`hide_metadata=`·`anonymizer=` 또는
`LANGSMITH_HIDE_INPUTS` 계열이다(`client.py:1343-1352` 폴백,
`:2695-2713` 적용). `hide_inputs is True`이면 입력을 `{}`로 완전 대체한다.
`PostgresSaver` serde는 트레이스와 별개로 state를 DB에 적재하는 노출면이다.
우선순위는 **`emphasis_points` → `results[].draft_id` → `context_hash`**다.

**미확인 5건 — 추정으로 채우지 않는다.**

1. `PostgresSaver` span·적재 내용
2. `mapping_probe` 워커 span·필드
3. `openai_compat` 실 provider 경로
4. `hide_inputs` 실적용 결과
5. `langsmith` 상위 버전 동작

**동의어 env 우회.** PR #48 가드는 `LANGSMITH_TRACING` 하나만 봤지만
`langsmith 0.10.2`의 실제 판정 대상은
`{LANGSMITH,LANGCHAIN}_{TRACING,TRACING_V2}` 4종이다. A가
`LANGCHAIN_TRACING_V2=true`로 실측했을 때 가드는 침묵했고
`POST /runs/multipart` 연결 시도 10건(`Content-Length: 15606`), 앱 에러 0건이었다.
A는 PR #58의 `runtime/tracing.py`로 자기 워커 표면을 닫았다. B는
`codex/tracing-synonym-gateway-guard`의 `5d8e1a4`에서 gateway 판정을
`external_tracing_active()`로 위임했으나 **PR 리뷰 대기**이며 머지 완료가 아니다.
상세는 `99` D ㉒-a를 따른다.

**gateway docstring.** `TraceMaskingHook`의 `[미확정]` 표기는 `5d8e1a4`(#59 머지)에서
위 실측 기준으로 갱신했다 — 이 훅은 트레이스 기록에 효력이 없고 실효는 전송 전 차단이다.

**후속 결정.**

1. ✅ **추적 판정 소스 단일화(A-11) — 해소.** A가 `test_trace_masking_hook.py`를
   `monkeypatch.setenv` 기준으로 이관했고(PR #61), B가 `gateway.py`의
   `or resolved_settings.langsmith_tracing` 보조 트리거를 제거해 판정 정본을
   `external_tracing_active()` 하나로 단일화했다. `LlmSettings.langsmith_tracing`
   필드는 `.env` 표기용으로 남되 **가드는 이 값을 보지 않는다**. `settings` 파라미터도
   호출 호환을 위해 유지하며 가드에 관여하지 않는다.
2. **가드 트리거와 훅 실효 불일치.** 트리거는 외부 추적 활성인데 실제 훅은 redaction
   우회 요청을 전송 전에 차단한다. 훅 상시 요구·추적 활성 시 요구·별도 검증기 분리 중
   어느 계약이 맞는지 재검토한다. 동작 변경이므로 이번 문서 작업에서 고치지 않는다.

**부결 시 대안.** 영구 `LANGSMITH_TRACING=false`. B-6 확정을 철회해야 하고
LangGraph 디버깅 수단을 잃는다.

**부수 안건.** redact 누락 계약 테스트를 본 PR에서 B 경로에만 건다.
`composition/`·counsel_pack은 A가 counsel_pack PR에 같은 AST 형식으로 넣기로
확인했다.

### 2-17. 크로스 플랫폼 정합성 — 경로 구분자·개행 `[제안 · A+B]` `✅ ①② A 실행 완료(PR #50) · ③ 잔여`

**사실 ① 경로 구분자.** 실제 결함은
`tests/ai/contract/test_composition_redaction.py:46` 한 줄이다.
`str(path.relative_to(_COMPOSITION))`은 Windows에서 백슬래시를 반환하지만,
화이트리스트는 `("counsel/provider.py", "write")`처럼 슬래시로 표기해 같은
호출부를 서로 다른 경로로 비교한다. 참조 구현은
`tests/ai/contract/test_problem_generation_redaction.py:42`의 `.as_posix()`다.

**A 크로스체크로 축소된 범위.** `relative_to()`를 쓰는 다른 네 테스트는 비교에
경로 문자열을 사용하지 않아 같은 결함이 아니다.

- `test_evaluation_isolation.py:45` — `offenders` 오류 메시지 표시용
- `test_error_mapping.py:49` — `.parts` 뒤 `"."` 결합으로 구분자 무관
- `test_vendor_isolation.py:47` — assert 메시지 f-string 표시용
- `tests/ai/failure/test_import_held_boundaries.py:31` — `offenders` 오류 메시지 표시용

경로 결함이 다섯 파일에 걸친다는 B의 종전 판정은 사용 여부만 세고 비교 방식을
보지 않은 과대 판정이므로 철회한다. 잔여는 실패 메시지의 구분자 표기 차이뿐이다.
W 번호도 `W1`~`W10`의 실제 표기를 놓치는 하이픈 필수 grep 패턴 때문에 “기존 번호
0개”로 잘못 판정했다. 같은 원인의 누락이므로 철회하고 다음 번호를 `W11`로 잡는다.

**사실 ② 개행.** 저장소에 `.gitattributes`가 없어 개행 정규화가 저장소 수준에서
정의되지 않았다. 각 로컬 `core.autocrlf`에 따라 워킹트리 개행이 갈릴 수 있고,
`src/ai/evaluation/golden/problems/prompt_snapshots/*.snapshot.txt`처럼 파일
내용을 비교하는 경로가 개행에 민감해지면 플랫폼별 결과가 달라질 수 있다.
현재 발현된 결함은 아니다.

**충돌 당시 상태와 해소.** PR #50 전 `.github/workflows/ci.yml:18`·`:41`은 모두
`runs-on: ubuntu-latest` 단독이라 Windows 경로 구분자 결함을 구조적으로 잡지 못했고,
이번 결함도 머지 전 CI를 통과했다. A가 PR #50에서 `windows-latest` 매트릭스를 추가해
이 경로를 닫았다.

**제안 3건.**

1. `test_composition_redaction.py:46`을 `.as_posix()`로 바꾼다.
   **✅ A 실행 완료(`17fecfb`, PR #50).**
2. `ci.yml` 매트릭스에 `windows-latest`를 추가한다.
   **✅ A 실행 완료(`e5cd95f`, PR #50).**
3. `.gitattributes`를 신설해 최소 `* text=auto`로 개행 정규화를 저장소 수준에
   고정한다. **⬜ 유일한 잔여 — 공용 · A+B 미합의.**

**A의 제안 외 추가 회귀.** A는 `b994017`에서 경로 구분자 회귀를 OS와 무관하게
고정하는 테스트를 별도로 추가했다. 이는 B의 ①② 요청에 포함된 조치가 아니며,
`windows-latest` 매트릭스와 함께 같은 유형을 이중으로 잡는다.

**부결 시 대안(③ 한정).** `.gitattributes` 없이 각자 로컬 설정에 의존한다.
스냅숏·픽스처 비교가 개행에 민감해지는 순간 플랫폼별 결과가 갈리고, ①과 같은
“로컬에서만 발견” 구조가 반복되는 비용을 감수한다.

### 2-18. `02_ownership.md` 트리의 `problem_generation/` 하위 갱신 `[제안 · 표기 동기화]`

**소유 변경이 아니다.** `problem_generation/` 전체가 염준영 단독인 것은 그대로이며, 트리에 열거된 **파일명이 실제와 달라진 것**만 정정 요청한다. 공용 문서라 B가 직접 고치지 않는다.

**현행(`02_ownership.md:161-163`)**

```
├── problem_generation/                 [염준영]    문항 생성 — LangGraph
│   ├── passage.py · generator.py · verification.py
│   ├── cross_solver.py · workflow.py
│   └── (P2 예약) print_layout.py       [염준영]    F17 시험지 조판 — 문항 메타 보존
```

**제안**

```
├── problem_generation/                 [염준영]    문항 생성 — LangGraph · 계층 정본 part_b/13
│   ├── domain/                         [염준영]    순수 규칙 — policy · rules · difficulty
│   │                                               cross_solve · identity · models
│   ├── application/                    [염준영]    오케스트레이션 — ports · generator
│   │                                               cross_solver · workflow
│   ├── infrastructure/                 [염준영]    어댑터 — config(yaml) · memory_store
│   ├── data/                           [염준영]    verify_config · pg_banned_topics yaml
│   └── (P2 예약) print_layout.py       [염준영]    F17 시험지 조판 — 문항 메타 보존
```

**근거.** 재배치 전 `workflow.py` 920줄에 노드·재시도·중단 판정이 뭉쳐 있었고, `verification.py`는 yaml 로딩(I/O)과 R-1~R-7 판정(순수 규칙)이 한 파일이었으며, `_canonical_json`이 두 모듈에 복제돼 있었다(결정론 해시의 근거 함수 — 불변식 8). 트랙이 5종으로 늘면(§2 `05` §1) 여기부터 무너진다.

**`CLAUDE.md` §2 "구조 재편 금지"와의 관계.** 이번 변경은 **capability 내부**에 한정한다 — `src/ai/` 최상위 폴더를 신설·이동하지 않았고 A 소유 경로는 건드리지 않았다. `02_ownership.md` §5 원칙("폴더는 capability 기준 그대로")은 유지된다. **capability 안쪽 배치가 오너 재량이라는 해석이 맞는지 확인 부탁드린다** — 아니라면 되돌린다.

**검증.** ruff · mypy 252 files · pytest **1511 passed / 실패 0 / skip 0**(재배치 전 1500 → +11은 AST 계약 테스트가 새 파일을 스캔한 증가분이며 테스트를 추가·삭제하지 않았다). 계층 경계는 `tests/ai/contract/test_pg_layer_boundaries.py` 18케이스가 AST로 고정한다. 상세는 [`13_code_layout.md`](13_code_layout.md).

### 2-19. `/v1/problems` v1 API 스펙 초안 `[제안 · 2026-08-07 · A 반영 대기]`

> **반영 대상은 A 소유 정본 `docs/04_api_contract.md`다.** 이 절은 B가 넘기는 초안이며
> 정본을 직접 고치지 않는다. A 확정 회신에 따라 §2-1의 가칭 `/problem-sets` 경로와
> refine·reverify 범위를 **v1에서 대체**한다. 자체 job은 만들지 않고 공용 슈퍼바이저의
> `WorkerKind.PROBLEM_GENERATION`·`OperationKind.PROBLEM_SET_GENERATE`
> (`"problem_set.generate"`)를 쓴다. v1 operation은 이것 하나뿐이다.

#### 2-19.0 엔드포인트 구현 상태 퀵 레퍼런스

| # | 엔드포인트 표면 | 구현 상태 | BE 용도 |
| --- | --- | --- | --- |
| 1 | `POST /v1/problems` | `[v1 구현]` | 생성 잡 기동 |
| 2 | `GET /v1/problems/{job_id}` | `[v1 구현]` | 잡 상태와 생성 결과 회수 |
| 3 | `GET /v1/problems/{set_id}/items` | `[v1 스펙 확정 · 구현 후속]` | Step3 문항 목록과 상태별 카운터 |
| 4 | `GET /v1/problems/{set_id}/items/{slot_index}` | `[v1 스펙 확정 · 구현 후속]` | Step3 문항 상세와 현재 허용 동작 |
| 5 | `POST /v1/problems/{set_id}/items/{slot_index}/revisions` | `[v1 스펙 확정 · 구현 후속]` | AI 수정·강사 직접 수정·롤백 |
| 6 | 교체·삭제 | `[v1 스펙 확정 · 구현 후속]` | 새 생성 계보 시작 또는 감사 이력을 보존한 삭제 |

3~6번은 BE가 화면 계약을 먼저 맞출 수 있도록 확정된 응답 의미를 적은 것이며, 현행
라우터에 존재한다고 해석하지 않는다. 6번의 HTTP 경로만 아래에서 별도 제안으로 표시한다.

#### 2-19.1 `POST /v1/problems` — 세트 생성 기동(202)

**구현 상태:** `[v1 구현]`

필수 헤더와 멱등 범위는 `04_api_contract.md` §3.9 counsel/drafts와 같다.

| 필수 헤더 | 내부 `ProblemRequest` 매핑 | 규약 |
| --- | --- | --- |
| `X-Tenant-Id` | `tenant_id` | 테넌트 범위. 바디에는 중복하지 않는다 |
| `X-Request-Id` | `request_id` | 요청 추적 키. 바디에는 중복하지 않는다 |
| `Idempotency-Key` | `idempotency_key` | 같은 키+같은 바디는 최초 202 응답을 재반환하고, 같은 키+다른 바디는 409 `IDEMPOTENCY_CONFLICT` |

외부 HTTP 바디는 내부 command의 헤더 파생 3필드를 제외한 투영이다
(`contracts/problem_generation.py:83-131`, 이 문서 §2-1의 B HTTP 경계 확정).

| 바디 필드 | 형식·제약 |
| --- | --- |
| `target_kind` | `student | class` |
| `target_ref` | 비어 있지 않은 가명 참조 |
| `target_source` | `weakness_auto | teacher_manual` |
| `weakness_map_id` | UUID 또는 `null`. `teacher_manual`이면 금지 |
| `manual_targets` | 문자열 배열 또는 `null`. `teacher_manual`이면 1건 이상 필수, `weakness_auto`이면 금지 |
| `snapshot_hash` | 비어 있지 않은 입력 스냅숏 해시 |
| `taxonomy_version` | 비어 있지 않은 taxonomy 버전 |
| `area_tag` | `language | reading`. `reading`은 `passage`와 함께 보낼 때만 허용 |
| `type_tags` | `fact | infer | critic | concept` 중 중복 없는 1건 이상 |
| `item_format` | **v1은 `mcq`만 허용** |
| `count` | 1..20 |
| `requested_difficulty` | `low | medium | high | null` |
| `target` | `cell | node | auto`, 기본 `auto` |
| `passage` | `language`은 생략 또는 `null`, `reading`은 `PassageRequest` 필수. T4·T5 입력은 아직 계약에 없음 |
| `topic_hint` | 비어 있지 않은 문자열 또는 `null` |

```json
{
  "target_kind": "student",
  "target_ref": "st_8f2a",
  "target_source": "teacher_manual",
  "manual_targets": ["grammar:sentence-structure"],
  "snapshot_hash": "sha256:…",
  "taxonomy_version": "2026.08",
  "area_tag": "language",
  "type_tags": ["concept"],
  "item_format": "mcq",
  "count": 1,
  "requested_difficulty": "medium",
  "target": "auto"
}
```

성공 응답은 공통 envelope를 사용하며 HTTP 202의 식별자는 `job_id` 하나다. 생성 결과
본문은 POST에 싣지 않고 GET으로만 회수한다.

```json
{
  "data": { "job_id": "8e94ceac-2213-4e58-b4b7-d48b1c922785" },
  "error": null,
  "meta": { "execution_id": "…", "versions": { "…": "…" } }
}
```

#### 2-19.2 `GET /v1/problems/{job_id}` — 상태·결과 회수

**구현 상태:** `[v1 구현]`

GET은 `X-Tenant-Id`가 필수이고 다른 테넌트의 `job_id`는 존재를 숨겨 404로 수렴한다.
`status`는 새 enum을 만들지 않고 `contracts/agents.py:43-52`의 `JobPhase`를 그대로 쓴다:
`queued | leased | running | paused | succeeded | failed | cancelled`.

```json
{
  "data": {
    "job_id": "8e94ceac-2213-4e58-b4b7-d48b1c922785",
    "status": "queued",
    "result": null
  },
  "error": null,
  "meta": { "execution_id": "…", "versions": { "…": "…" } }
}
```

`result`는 `contracts/problem_generation.py`의 `ProblemGenerationOutcome`
(`RejectedInsufficientOutcome | ProblemSetResult`) 또는 `null`이다. 실행 phase와 도메인
결과 status를 섞지 않는다.

| `JobPhase` | `result` | 계약 |
| --- | --- | --- |
| `queued | leased | running | paused` | 반드시 `null` | 아직 결과 계약이 확정되지 않았다. 실패·거부 결과를 미리 만들지 않는다 |
| `succeeded` | 반드시 `ProblemGenerationOutcome` | `rejected_insufficient`와 세트의 `generated | partial_success | failed`는 **정상 도메인 결과**다 |
| `failed | cancelled` | `null` | 실행 실패·취소를 도메인 게이트 결과로 위장하지 않는다 |

도메인 `result.status` 값의 정의 정본은 `docs/policies/error_codes.md` §2.6이다.
이 절은 **실행 phase ↔ result 조합 규약만** 정하고 값 자체를 재정의하지 않는다.

따라서 `status="queued" + result.status="failed"`, `status="running" +
result.status="rejected_insufficient"`, `status="succeeded" + result=null` 같은 조합은
금지한다(99 ㉥). GET 자체는 잡이 존재하면 200이며, 게이트 거부·근거 부족도 5xx가 아니다.

#### 2-19.3 오류 판별과 canonical LLM 매핑

판별 기준은 A 확정본을 그대로 쓴다. 상태코드를 PG 라우터에서 다시 판단하지 않는다.

| 누가 입력을 바꿀 수 있는가 | 사례 | wire 결과 |
| --- | --- | --- |
| 강사가 바꿀 수 있다 | 게이트 거부, 자동 개인화 근거 부족 | HTTP 200 GET + `status=succeeded` + 도메인 `result.status`. 정상 결과이므로 4xx·5xx로 올리지 않는다 |
| 호출자(BE)가 고쳐야 한다 | 필수 헤더·JSON·스키마·enum 위반, v1 미지원 요청 | 400 `INVALID_SCHEMA`. 같은 멱등키+다른 바디는 409 `IDEMPOTENCY_CONFLICT`, 없는/다른 테넌트 job은 404 `NOT_FOUND` |
| 호출자·강사가 현재 요청에서 고칠 수 없다 | 벤더 장애·타임아웃 | 게이트웨이 재시도 예산이 소진된 뒤에만 503/504. 재시도 전 5xx로 승격하지 않는다 |

`PROBLEM_ITEM` 영속 스키마가 §2-20의 A 결정을 기다리는 동안 v1은 기존 인메모리
저장소로 동작한다. 따라서 프로세스 재시작 뒤에는 이전 `job_id`와 결과가 소실되어 GET이
위와 같은 404 `NOT_FOUND`로 수렴한다. 새 상태코드를 만들지 않으며, BE는 이 한계를
재시작 후 복구가 보장되는 것으로 해석하지 않는다.

LLM 예외는 `runtime/errors.py:142-173`의 `domain_error_for()` 표를 **인용만** 한다.

| 입력(`contracts.llm`) | canonical 출력(`runtime.errors`) | HTTP |
| --- | --- | --- |
| `LlmTimeout` | `LlmUpstreamTimeout` | 504 `TIMEOUT` |
| `LlmUnavailable` | `LlmUpstreamDown` | 503 `LLM_UPSTREAM_DOWN` |
| `ParseFailed`·`FieldMissing` | `DomainException` | 500 `INTERNAL` |
| 그 밖의 plain `LlmError` | `DomainException` | 500 `INTERNAL` |

plain `LlmError`와 파싱·필드 오류를 503으로 뭉개지 않는다. 벤더가 살아 있는데 우리
요청·스키마가 틀린 경우라 “잠시 후 다시” 문구가 거짓이 된다. 반대로
`ProblemWorkflowConfigurationError` 계열은 `DomainException`이며 라우터가 매핑하지 않고
`api/app.py`의 예외 핸들러가 잡는다.

##### Provider 실패 전달 계약 초안 `[A 승인 대기]`

provider는 실패를 **예외로 올린다.** 반환값의 `outcome`은 `OK` 하나뿐이고, 그 밖의
`CallOutcome` 값은 게이트웨이가 예외에서 유도해 남기는 **원장 기록용**이다.

1. `gateway.py:210-218`의 재시도는
   `retry_if_exception_type((LlmTimeout, LlmUnavailable))`로 예외 타입에만 걸린다.
   실패를 outcome으로 반환하면 재시도가 동작하지 않는다.
2. `gateway.py:95-108`의 `_exception_outcome()`은 예외를 outcome으로 바꾸는 단방향
   매핑이며, 반대 방향 매핑은 없다.
3. 실 provider인 `openai_compat.py:193-226`은 실패를 계약 예외로 올리고,
   `openai_compat.py:247-249`에서 성공만 `CallOutcome.OK`로 반환한다(99 ㉴ 8/6 전수 실측).

소비자의 outcome 분기는 **도달 불가 방어**로 남긴다(#129). 이 규약을 바꾸면 재시도
계약도 함께 바뀌므로 게이트웨이 소유자(B)에게 먼저 통보해야 한다.

##### 구조화 출력 스키마 본문 전달 제안 `[A+B 양자 승인 필요 · 미구현]`

2026-08-12 실 LLM 스모크(`gpt-5.4-mini` · language · count=1)에서 생성 응답의
`GeneratedItem` 스키마 통과가 회차별 `1/3 · 3/3 · 0/3`으로 흔들렸고, 두 회차는
`FieldMissing`으로 생성 상한 3회를 소진했다. 현 `LLMRequest`는
`response_schema_name="GeneratedItem"`이라는 이름만 싣고 JSON Schema 본문은 싣지
않으므로 provider가 OpenAI 호환 `response_format.type=json_schema`를 만들 근거가 없다.

양자 승인 뒤 `contracts/llm.py::LLMRequest`에 다음 선택 필드를 추가하는 안을 제안한다.

```python
response_schema: dict[str, object] | None = None
```

값은 capability 호출자가 자신의 Pydantic 계약 모델에서 `model_json_schema()`로 만들어
넘긴 JSON Schema 본문이다. `response_schema_name`은 관측·registry 대조용 이름으로 유지하고,
본문이 있으면 provider는 이름과 본문으로 strict `json_schema` response format을 조립한다.
본문이 없으면 기존 자유 텍스트 호출과 파싱 경로를 유지해 점진적으로 이관한다.

🔴 provider가 `ai.contracts.problem_generation`·`ai.contracts.classify` 같은 capability 계약을
직접 import해 이름별 모델 표를 갖는 형태는 금지한다. 그렇게 하면 벤더 어댑터가 도메인을
알게 되고 새 capability가 추가될 때마다 공통 provider를 고쳐야 한다. 스키마는 호출자가
만들어 넘기고 provider는 전달받은 표준 JSON Schema만 해석해야 벤더 독립 경계가 유지된다.

이 변경은 양자 파일 `contracts/llm.py`의 공통 요청 계약을 넓히므로 A+B 승인이 필요하다.
이번 변경에서는 구현하지 않았으며, provider·게이트·재시도 상한과 기존 스키마 제약도
변경하지 않았다.

| 예외 | wire 결과 |
| --- | --- |
| `ProblemWorkflowConfigurationError` | 400 `INVALID_SCHEMA` |
| `ProblemTenantMismatch` | 403 `TENANT_MISMATCH` |
| `ProblemSourceUnsupported` | 400 `INVALID_SCHEMA` + `detail.reason=source_procurement_not_implemented` |
| `ProblemExecutionContextMismatch` | 500 `INTERNAL` — 내부 조립 버그 |

이 계열을 통째로 400으로 바꾸면 403과 `source_procurement_not_implemented`가 뭉개진다.

**난이도 보정 버전 확인:** v1은 난이도 보정을 쓰지 않아 `difficulty_calib_version`이
`null`이다(`difficulty_regen_enabled: false` · `DifficultyCalib` 저장소 미배선). 실패
응답에서는 `taxonomy_version`도 `null`이다. 요청 바디에 따라 갈리는 값은 그 시점에
정적이지 않다(`04_api_contract.md` §2.2 A 판정 “엔드포인트가 아는 정적 앱 버전”).

#### 2-19.4 v1 지원 한계 — 정본은 04 §3.11 `[이관 완료 · 2026-08-09]`

🔴 **지원 한계 목록을 여기 두지 않는다.** #138로 04 §3.11에 옮겼고, **BE가 읽는 정본은
04 하나**다. 같은 목록을 두 곳에 두면 갈리고, 그때 BE는 **선검사를 하나만 넣는다** — 이
절이 걱정하던 바로 그 사고를 이 절 자신이 만들게 된다.

| 무엇 | 정본 |
| --- | --- |
| `area_tag`·`passage` 제한과 400 `source_procurement_not_implemented` | **04 §3.11** |
| `type_tags`의 예약 태그와 400 `type_tag_not_supported` | **04 §3.11** (구현 8/9 — §2-21.3) |
| 사유 코드 사전 | `docs/policies/error_codes.md` §6 |

B가 여기서 유지하는 것은 **구현 상태**뿐이다.

| 한계 | 구현 자리 | 잡을 만드는가 |
| --- | --- | --- |
| 지원하지 않는 `area_tag`·`passage` 조합 | `enqueue.py::reject_unsupported_source_procurement()` + `application/workflow.py::_validate_execution()` | **안 만든다** — 문 앞에서 거절하고 워크플로가 이중 방어한다 |
| `type_tags`에 예약 태그 | `enqueue.py::reject_unsupported_type_tags()` | **안 만든다** — 잡 생성 이전이다 |

두 호출자 오류는 모두 문 앞에서 막으므로 4xx가 실패 잡을 남기지 않는다. 워크플로 검사는
직접 호출·체크포인트 재개가 문 앞 검사를 우회해도 같은 정책으로 중단시키는 방어선이다.

또한 v1은 `problem_set.generate` 하나뿐이다. `problem_item.refine`·
`problem_item.reverify`는 공용 enum의 예약 operation일 뿐 이 API에 엔드포인트나 분기를
만들지 않는다.

#### 2-19.5 `GET /v1/problems/{set_id}/items` — Step3 검토 목록 `[v1 스펙 확정 · 구현 후속]`

`X-Tenant-Id`가 필수다. 다른 테넌트의 세트는 존재를 숨겨 404 `NOT_FOUND`로 수렴한다.
응답은 슬롯별 검토 요약과 Step3 상단에 바로 표시할 상태별 카운터를 함께 제공한다.

```json
{
  "data": {
    "set_id": "5ac7a8c1-83cb-4c86-b224-f08e4b1ae8e9",
    "status_counts": {
      "verified": 7,
      "needs_review": 2,
      "verification_unavailable": 1,
      "dropped": 0
    },
    "items": [
      {
        "slot_index": 0,
        "item_id": "c8f4530e-a64c-4e78-80d5-cb3bd95bfc30",
        "status": "verified",
        "current_revision_no": 0,
        "review_reason": null,
        "failure_reason": null
      }
    ]
  },
  "error": null,
  "meta": { "execution_id": "…", "versions": { "…": "…" } }
}
```

카운터의 네 키는 `ProblemItemStatus`의 `verified | needs_review |
verification_unavailable | dropped`와 1:1 대응한다. BE가 목록을 다시 세어 상단 값을
추론하지 않으며, 합계 불일치는 서버 계약 위반으로 취급한다.

#### 2-19.6 `GET /v1/problems/{set_id}/items/{slot_index}` — Step3 문항 상세 `[v1 스펙 확정 · 구현 후속]`

문항 조회 응답이 곧 화면이다. 본문·선지·정답·해설·근거와 blind 교차 풀이 결과,
검사 상태를 한 번에 돌려준다. 특히 아래 두 필드는 협업 경계의 필수값이다.

- `available_actions`: 서버가 현재 허용하는 `refine | replace | teacher_direct | delete |
  rollback`의 부분집합. FE는 상태로 버튼 활성화를 재계산하지 않는다.
- `current_revision_no`: 다음 수정 요청의 `base_revision_no`로 그대로 보내는 낙관적 잠금
  번호다.

```json
{
  "data": {
    "set_id": "5ac7a8c1-83cb-4c86-b224-f08e4b1ae8e9",
    "slot_index": 0,
    "item_id": "c8f4530e-a64c-4e78-80d5-cb3bd95bfc30",
    "status": "needs_review",
    "current_revision_no": 2,
    "available_actions": ["refine", "replace", "teacher_direct", "delete", "rollback"],
    "item": {
      "area_tag": "language",
      "type_tag": "concept",
      "item_format": "mcq",
      "skill_node_id": "grammar:sentence-structure",
      "stem": "…",
      "choices": [
        { "no": 1, "text": "…", "why_wrong": null }
      ],
      "answer": { "correct_no": 1 },
      "rationale": "…",
      "evidence": [
        { "kind": "grammar_rule", "ref": "grammar:rule-1", "quote": null }
      ]
    },
    "cross_solve": {
      "chosen": 1,
      "reasoning": "…",
      "confidence": 0.91,
      "multiple_answers_possible": false,
      "target_skill_node_id": "grammar:sentence-structure",
      "measured_skill_node_id": "grammar:sentence-structure",
      "aligned": true,
      "alignment_reason": "…",
      "alignment_confidence": 0.95
    },
    "verification": {
      "rule_validation": "passed",
      "blind_cross_solve": "passed",
      "release_decision": "needs_review"
    }
  },
  "error": null,
  "meta": { "execution_id": "…", "versions": { "…": "…" } }
}
```

위 예시는 화면 필드 배치를 보여 주기 위해 선지 첫 항목만 축약했다. 실제 `choices`는
`GeneratedItem` 계약대로 1~5번 다섯 항목을 모두 반환한다.

삭제·변경 진행 중처럼 누를 수 있는 동작이 없으면 `available_actions=[]`를 명시한다.
`rollback`은 되돌릴 이전 리비전이 있을 때만 포함한다.

#### 2-19.7 `POST /v1/problems/{set_id}/items/{slot_index}/revisions` — 수정·롤백 `[v1 스펙 확정 · 구현 후속]`

필수 헤더는 다른 쓰기 API와 같은 `X-Tenant-Id`·`X-Request-Id`·`Idempotency-Key`다.
바디는 `revision_kind=ai_refine | teacher_direct | rollback`과
`base_revision_no`를 반드시 포함하고 종류별 payload는 다음과 같이 서로 배타적이다.

| `revision_kind` | 추가 필드 | 의미 |
| --- | --- | --- |
| `ai_refine` | `instruction` | 기존 계보 안에서 AI가 수정한다 |
| `teacher_direct` | `edited_item` | 강사가 완성 문항을 직접 제출한다 |
| `rollback` | `revert_to` | 현재 번호보다 앞선 리비전으로 되돌린다 |

`base_revision_no != current_revision_no`이면 409 `REVISION_CONFLICT`와
`detail.reason="stale_base_revision"`, 같은 슬롯에 수정이 진행 중이면 같은 코드와
`detail.reason="revision_in_progress"`를 반환한다(`error_codes.md` §1).

수정 폭과 종류에 관계없이 매 턴 게이트 ① 스키마·규칙 검증, ② blind 교차 풀이,
③ 근거·금칙어·노출 판정을 **전부 다시 실행**한다. 강사 직접 수정과 rollback도 예외가
아니며 일부 필드만 검사하고 이전 통과 상태를 재사용하지 않는다(`07_refine_policy.md` §4).

**구현 상태(2026-08-12 · B):** 위 한 경로 중 `ai_refine`만 먼저 열었다. 응답은 200이며
`revision`·`current_revision_no`와 규칙/교차 풀이/release 재판정 결과를 반환한다. 같은
멱등키+같은 바디는 같은 200을 재반환하고, stale·진행 중 충돌은 LLM 호출 전에 위 409로
끝난다. 통과·차단 턴 모두 이력을 남기되 현재 본문은 마지막 전체 검증 통과본을 유지한다.
현재 근거 재조회가 가능한 `language` 문항만 `available_actions=["refine"]`이며,
`teacher_direct`·`rollback`과 다른 4영역의 수정은 아직 열지 않았다. 다른 4영역은 생성 당시
자료 원문/EvidencePack 영속 재조회가 선행돼야 하며, 그 전에는 API가 임의로 근거를 복원하지
않는다. 근거: `api/routers/problem.py`·`application/refiner.py`·
`db/repositories/problem_revision_store.py`와 해당 테스트.

#### 2-19.8 교체·삭제 경로 `[v1 스펙 확정 · 구현 후속]` `[경로 제안 · BE 합의 대기]`

counsel의 동작별 하위 경로 관례에 맞춰 교체는
`POST /v1/problems/{set_id}/items/{slot_index}/replace`, 삭제는
`DELETE /v1/problems/{set_id}/items/{slot_index}`로 **제안**한다. 경로와 동기·비동기
응답 형태는 BE 합의 전 정본이 아니며, 의미 규약은 다음과 같이 고정한다.

- `replace`는 리비전이 아니다. 기존 계보를 보존하고 새 `item_id`와 새 1..3회 attempt
  예산으로 별도 생성 계보를 시작한다.
- `delete`는 LLM을 0회 호출하고 학생 노출 대상에서만 제거한다. 원문·리비전·검증·행위
  기록은 감사 이력으로 보존하며 물리 삭제하지 않는다(`07_refine_policy.md` §1).

#### 2-19.9 evidence 응답과 “출처 확인됨” 배지

모든 evidence 항목은 `kind`·`ref`·`quote` 세 필드를 갖는다. `quote=null`은 논리 참조만
있고 인용 원문은 없다는 뜻이므로, BE는 **“출처 확인됨” 배지를 켜면 안 된다.**

v1 초기에는 지식층이 Fake라 `quote`가 `null`이다. 근거층 배선(2단계) 이후 실제 인용이
채워진다. BE는 `quote=null`을 정상 응답으로 처리하되 “출처 확인됨” 배지를 켜지 않는다.
빈 문자열을 확인 완료로 간주하거나 `ref` 존재만으로 배지를 추론하지 않는다.

#### 2-19.10 완료 알림 최소 payload `[v1 스펙 확정 · 구현 후속]`

완료 알림에는 `set_id`·`status`·`counts`·`stop_reason`만 싣는다. 문항 본문·지문·해설은
알림에 넣지 않고 §2-19.5~.6 조회로 회수한다.

```json
{
  "set_id": "5ac7a8c1-83cb-4c86-b224-f08e4b1ae8e9",
  "status": "partial_success",
  "counts": {
    "verified": 7,
    "needs_review": 2,
    "verification_unavailable": 1,
    "dropped": 0
  },
  "stop_reason": null
}
```

Kafka 토픽은 아직 확정되지 않았다. v1 전달 수단을 GET 폴링으로만 둘지 완료 알림까지
연결할지는 **BE 합의 항목**으로 남기며 이 문서에서 임의로 결정하지 않는다.

#### 2-19.11 약점 진단(Step1) 응답 요구 `[v1 스펙 확정 · 구현 후속]`

Step1 진단은 생성 API와 별도 응답면으로 등재한다. 각 영역×유형 칸은 최소한
`area_tag`·`type_tag`·판정(`verdict`)·푼 문항 수(`solved_item_count`)·판정 기준값을
함께 제공해야 한다. 판정 기준은 학생 자신의 평균 대비 값이므로 **정답률 백분율은
내리지 않는다.** 백분율을 약점 근거처럼 표시하면 상대 판정의 의미를 잘못 전달한다.

영역×유형 칸의 합계만으로는 “문법 × 개념 — 음운 변동” 같은 추천 근거를 만들 수 없다.
같은 칸 안에서도 `skill_node_id`별 `verdict`·`solved_item_count`·판정 기준값 집계가
필요하다는 것이 **B 확인 사항**이다. 구체 endpoint와 wire 필드명은 후속 구현에서
BE와 합의하되 이 정보 요구를 생략하지 않는다.

### 2-20. `PROBLEM_ITEM` 스키마 결손과 해소안 `[제안 · A 결정 대기]`

`ProblemItemStore` 계약은 `save(... set_id, slot_index ...)`와
`get(set_id, slot_index)`가 같은 `StoredProblemItem`을 무손실로 왕복할 것을 요구한다
(`problem_generation/application/ports.py:42-66`). 그러나 계약 값 객체
(`problem_generation/domain/models.py:53-58`)와 현행 ORM(`db/models.py:345-367`)을
대조하면 아래 12건이 맞지 않는다. 이 상태에서는 구현이 계약에 맞출 수 없으므로
`db/repositories/problem_store.py`는 A 결정 뒤로 미룬다.

| # | 계약·조회 요구 | 현행 `problem_item` | 영향·근거 |
| --- | --- | --- | --- |
| 1 | `slot_index` | 컬럼 없음 | `get(set_id, slot_index)`의 조회 키 자체가 없다. `StoredProblemItem.slot_index`는 `domain/models.py:55`, ORM 전체는 `db/models.py:345-367` |
| 2 | 테넌트 격리 | 직접 `tenant_id` 없음 | 직접 컬럼 결손은 차단이 아니다. 부모 `problem_set.tenant_id`가 `db/models.py:330`에 있어 조인으로 격리 가능 |
| 3 | `item=None`인 본문 없는 슬롯 | `area_tag`·`type_tag`·`item_format`·`stem`·`choices`·`answer`·`rationale`가 모두 NOT NULL | `StoredProblemItem.item`은 nullable(`domain/models.py:58`)인데 ORM 본문은 `db/models.py:353-360`에서 필수라 행을 만들 수 없다 |
| 4 | `result.difficulty_est: float \| None` | `difficulty_est` NOT NULL | 계약은 `contracts/problem_generation.py:286`, ORM은 `db/models.py:361` |
| 5 | 저장 시점에 없는 난이도 보정 버전 | `difficulty_calib_ver` NOT NULL | `StoredProblemItem`·`ItemResult`에는 대응 값이 없는데 ORM은 `db/models.py:363`에서 필수다 |
| 6 | `candidate_ref` | 컬럼 없음 | 최종 후보 포인터를 잃는다(`domain/models.py:57`) |
| 7 | `result.attempt_no` | 컬럼 없음 | 최종 시도 회차를 복원할 수 없다(`contracts/problem_generation.py:283`). `verification_result.attempt_no`는 게이트 이력의 회차라 최종본 정본을 대신하지 않는다 |
| 8 | `result.failure_detail` | 컬럼 없음 | 실패 상세를 무손실로 복원할 수 없다(`contracts/problem_generation.py:285`) |
| 9 | `result.difficulty_band` | 컬럼 없음 | 난이도 밴드를 복원할 수 없다(`contracts/problem_generation.py:287`) |
| 10 | `result.review_reason` | `review_badge` boolean만 있음 | 여러 `ReviewReason`을 boolean 하나로 되살릴 수 없다(`contracts/problem_generation.py:289`, `db/models.py:364`) |
| 11 | `item.evidence` | 컬럼 없음 | `GeneratedItem.evidence` 전문이 소실된다(`contracts/problem_generation.py:175-188`, `db/models.py:353-360`) |
| 12 | `result.failure_reason: ProblemFailureReason \| None` | `drop_reason`(폐기 사유 전용, `db/models.py:367`) | 폐기가 아닌 상태의 실패 사유를 폐기 사유 컬럼에 싣는 오버로딩이 된다. A안 채택 시 별도 결정이 필요하고, B안 채택 시 스냅숏이 정본이라 해소된다(`contracts/problem_generation.py:284`) |

#### 2-20.1 `ITEM_REVISION` 우회가 성립하지 않는 이유

1. 계약의 `ItemRevision.result_snapshot` 형식은 `GeneratedItem | None`이지
   `StoredProblemItem`이 아니다(`contracts/problem_generation.py:667-675`).
2. `RevisionKind`는 `ai_refine | teacher_direct | rollback` 3종뿐이며 최초 생성 저장을
   뜻하는 값이 없다(`contracts/problem_generation.py:613-616`). 임의 문자열을 넣으면
   계약을 우회한다.
3. `item_revision.item_id`는 먼저 존재해야 하는 `problem_item.id`의 FK다
   (`db/models.py:384-392`). 만들 수 없는 최종 행을 우회하기 위해 그 행을 전제하는
   순환이 된다.
4. `ItemRevision.validate_snapshot()`은 `verifications_passed=True`일 때
   `result_snapshot=None`을 거부한다(`contracts/problem_generation.py:681-684`). 따라서
   본문 없는 슬롯을 통과 리비전으로 위장할 수도 없다.

#### 2-20.2 A 결정 요청 3건

**결정 ① — 조회 키.** `problem_item`에 `slot_index`를 추가하고
`UNIQUE(set_id, slot_index)`를 둔다. `item_candidate`가 이미
`(tenant_id, set_id, slot_index, attempt_no)`를 유일 범위로 쓴다
(`db/models.py:417-423`). Protocol 조회 키를 구현하려면 다른 선택지가 없는 결손이다.

**결정 ② — 무손실 보존 방식.** 다음 두 선택지 중 A가 정한다.

- **A안 — 컬럼 전개:** `candidate_ref`·`attempt_no`·`failure_detail`·
  `difficulty_band`·`review_reason`·`evidence(JSONB)` 6개를 추가하고,
  `difficulty_est`·`difficulty_calib_ver`를 nullable로 바꾼다.
- **B안 — 전체 스냅숏 1컬럼:** `result_snapshot JSONB`에 `StoredProblemItem` 전문을
  보존하고 기존 컬럼은 조회·인덱싱용 파생 투영으로 둔다.

**B 권고는 B안이다.** `item_candidate`가 이미 `snapshot JSONB`를 정본으로 두고
`difficulty_est`를 파생 컬럼으로 함께 저장한다(`db/models.py:430-432`). 같은 형태면
계약 필드가 늘어도 개별 컬럼 누락으로 다시 무손실 왕복이 깨지지 않는다.

**결정 ③ — 본문 없는 슬롯 표현.** 다음 선택지 중 A가 정한다.

- **ⓐ 본문 컬럼 nullable:** `area_tag`부터 `rationale`까지 본문 투영을 nullable로 바꾼다.
- **ⓑ 행을 만들지 않음:** `get(set_id, slot_index)`가 해당 슬롯 결과를 반환하지 못해
  `ProblemItemStore` Protocol을 위반하므로 채택할 수 없다.
- **ⓒ 결정 ②의 B안과 결합:** 전체 스냅숏을 진실로 삼고 본문 파생 컬럼을 nullable로
  둔다.

변경량과 향후 계약 확장 위험을 함께 줄이는 조합은 **결정 ② B안 + 결정 ③ ⓒ**다.
이는 B의 권고이며 최종 선택은 A가 한다.

#### 2-20.3 테넌트 격리와 마이그레이션 경계

테넌트 격리는 이번 차단 원인이 아니다. 저장소 생성자에 `tenant_id`를 주입해 인스턴스를
테넌트 단위로 스코프하고, Protocol 시그니처를 바꾸지 않은 채 모든 조회에서 부모를
조인하면 된다.

```python
select(ProblemItem).join(
    ProblemSet,
    ProblemItem.set_id == ProblemSet.id,
).where(
    ProblemSet.tenant_id == self._tenant_id,
    ProblemItem.set_id == set_id,
    ProblemItem.slot_index == slot_index,
)
```

마이그레이션 파일은 양자 승인 목록이 아니라 `db/models.py` 모델 diff의 기계적 산출물이다.
따라서 A가 모델 선택지를 승인한 뒤 같은 모델 변경 PR에서 함께 리뷰하면 된다
(`docs/02_ownership.md:60`).

#### 2-20.4 최초 저장 경합 전수 조사 `[2026-08-10]`

`SELECT`로 부재를 확인한 뒤 `INSERT`하는 최초 저장 경로를 저장소 전수에서 다시 대조했다.
같은 형태는 셋이며 계약에 따라 처리가 갈린다.

| 저장소 | 판정 | 처리 |
| --- | --- | --- |
| `problem_store.py` | 같은 슬롯의 동시 최초 저장에서 진 트랜잭션이 23505로 새어 `ImmutableStoreConflict` 계약을 위반 | **이번 처리** — `problem_item\x1f{tenant_id}\x1f{set_id}\x1f{slot_index}` 자문 잠금으로 읽기 전부터 직렬화 |
| `inquiry_class_store.py` | 같은 최초 저장 경합 | ✅ **해소(2026-08-12 · PR #210)** — `inquiry_class\x1f{tenant_id}\x1f{inquiry_ref}` 자문 잠금. 아래 후속 참조 |
| `idempotency.py` | 저장 실패를 요청 실패로 올리지 않는 fail-open 계약 | **대상 아님** — 같은 처방을 강제하지 않음 |

`problem_item`은 결정론 UUID PK와 `UNIQUE(set_id, slot_index)`에 같은 자연키가 두 번
적힌다. `ON CONFLICT DO NOTHING`은 지정한 arbiter만 투기적 삽입으로 보호하므로,
자연키를 arbiter로 둔 동시 4쓰기 20회 중 2회는 PK 23505가 먼저 걸렸다
(실측 2026-08-10). 예외를 성공으로 번역하지 않고 `pg_advisory_xact_lock`으로 충돌 자체를
없애는 이유다.
이 수치는 폐기된 `ON CONFLICT` 접근의 실측이며, 현재 브랜치에는 같은 실험을 재현하는 코드가
남아 있지 않다.

**별건 등재 — `problem_set`.** 저장소 전체에 `problem_set`을 `INSERT`하는 코드는 0건이고
`problem_store.py`는 읽기·조인만 하므로 지금은 무해하다. 향후 쓰기 경로를 여는 시점에는
자연키와 최초 저장 경합을 함께 설계해야 한다. 이번에는 고치지 않는다.

**후속 확인 — `inquiry_class_store.py` 해소 `[2026-08-12]`.** A가 PR #210에서 같은 처방으로
닫았다 — 읽기 전 `pg_advisory_xact_lock(hashtextextended(:key, 0))`이고, 키는 `lock_key()`
한 함수가 만든다(`inquiry_class` 네임스페이스 + 자연키 두 축 · 구분자 `\x1f`). 형태·구분자
근거가 `problem_store.py`와 같으므로 이 표의 처방이 저장소 둘에 같은 모양으로 서 있다.

B가 실 PG에서 재현했다 — 경합 검사 9건 통과. 뒤집기 둘로 검사의 검출력도 확인했다:
잠금을 **둘 다** 빼면 5건 실패(최초 저장 3건 + 배리어 + 갱신 경합), **`apply_confirmation`
쪽만** 빼면 1건 실패다. 후자가 이번 건의 핵심이다 — 자문 잠금은 협력형이라 **한쪽만
잠그는 것은 안 잠근 것**이고, 그때 남는 행은 오류 없이 **순차 실행 두 갈래 어디에도 없는
값**이 된다.

🔴 **이 표의 판정 축은 「최초 INSERT」였다.** #42(기존 행 갱신 경합)는 8/10 전수 조사가
보지 않은 축이고, A가 별건으로 등재·해소했다. 다음 전수 조사는 **갱신 경합도 같이 센다** —
같은 자연키에 협력 writer가 둘 이상이면 전원이 같은 키에 서는지가 판정 대상이다.

8/10 이후 신설된 저장소도 다시 대조했다. `pack_store.py`는 SELECT 후 INSERT 형태지만 키가
랜덤 UUID라 같은 키가 두 번 오는 경로가 없고(재시도 고아 행 축은 #24로 별도 추적),
`probe_stores.py`는 선행 SELECT 없는 단순 INSERT다 ⇒ 이 표에 새로 들어올 저장소는 없다.

**제안만 남김 — 잠금 SQL 문면 공용화.** 같은 `_LOCK_SQL` 문면이 이제 셋에 있다
(`problem_store.py`·`inquiry_class_store.py`·`counsel_read_model.py`). 공용 경계로 올리면
A·B 소유 파일을 함께 건드리므로 이번에도 제안으로만 남긴다 — 아래 SQLSTATE 항목과 같은 결이다.

**제안만 남김 — SQLSTATE 추출 공용화.** 향후 여러 저장소에서 예외 번역이 실제로 필요해지면
`agent_job.py`의 `_sqlstate`와 같은 드라이버 차이 흡수 헬퍼를 공용 경계로 올린다. 지금은
A 소유 파일을 리팩터링하거나 이 PR에 공용 헬퍼를 만들지 않는다.

**제안 — 동시성 절단 가드의 프록시 검증.** `problem_store`와 `counsel_read_model`의 절단
가드는 모두 맨 코루틴으로 `asyncio.Barrier` 자체만 확인한다. 따라서 `_BarrierSession.execute`
에서 `barrier.wait()`가 빠져 프록시가 망가져도 절단 가드는 green을 유지한다. 두 파일 공통
축이므로 B 테스트만 다르게 고치지 않고 A와 함께 프록시를 실제로 통과하는 가드로 올릴지
판정한다. 다만 이번 브랜치에서는 자문 잠금 제거 시 동시성 테스트가 **5/5 결정론으로 red**가
되어 프록시가 실제 경합을 만들고 있다는 실증은 이미 있다.

✅ **해소(#198 · #200).** #198에서 counsel·problem_item 두 절단 가드를 공용 프록시
`tests/ai/fakes/first_sql_barrier.py`로 합쳤고, 이제 `asyncio.Barrier` 자체가 아니라 실제
세션 프록시를 지난다. #200은 party 1 배리어가 두 번째 `wait()`도 즉시 통과시켜 프록시가
**첫 SQL에서만** 서는지를 놓치던 미탐을 party 부족으로 잡았다. 제안 당시 5/5 red는
프록시가 **동작했다**는 실증이고, 두 후속은 그 동작을 회귀 테스트가 **단정하게** 만든다.
두 PR 모두 test-only 해소이며 프로덕션 코드 변경은 없다.

---

### 2-21. Step 1 그리드 축과 v1 출제 범위 `[2026-08-08]`

#### 2-21.1 y축은 과목 4행 `[B 확정]`

이 화면의 수능 체제는 **2028학년도 체제를 배제하고 2027학년도까지**로 한정한다. 공통
2과목과 선택 1과목 구조다. 학교 현장의 2022 개정 교육과정은 수능 체제와 **별개 축**이며,
둘을 하나의 버전이나 분류로 묶지 않는다.

| 그리드 행 | `AreaTag` | 체제 |
| --- | --- | --- |
| 독서 | `reading` | 공통 |
| 문학 | `literature` | 공통 |
| 화법과 작문 | `speech_writing` | 선택 |
| 언어와 매체 | `language` + `media` | 선택 |

**측정은 5, 표시는 4**다. `area_tag`는 `taxonomy.md` §1의 출제 규격 축이고 과목은 화면을
구성하는 별도 축이다. 같은 출제 규격을 쓰는 `speech`·`writing`은 `speech_writing`으로
합쳤지만, 출제 규격이 다른 `language`·`media`는 계속 별도 영역으로 둔다.

🔴 **(2026-08-09) 이 절의 판정이 뒤집혔다 — 수를 고친 것이 아니다.** v4.0(8/8)은 여기에
*"`AreaTag` 6개를 병합하거나 `contracts/taxonomy.py`를 바꾸지 않는다"* 고 **못 박았고**
*"네 소비처 영향은 0"* 이라고 적었다. 둘 다 지금은 거짓이다 — 축을 「과목 편제」에서
**「출제 단위」**로 옮기면서 두 값을 병합했고 소비처가 실제로 바뀌었다.
⚠ **`taxonomy.md` §2 경계 사례 ③(화법+작문 통합형)도 같이 사라졌다** — 갈라 태깅할 이유가
없어져 `speech_writing` 단일 판정이 됐다. **지우기만 하면 다음 사람이 *"원래 이랬나"* 를
다시 판단한다**(㊩) — 그래서 뒤집힌 사실을 여기 남긴다.

남은 협의는 `area_tag → 선택과목` 매핑의 계약 위치다. 현재 매핑은
`contracts/taxonomy.py`의 docstring에만 있고 `SubjectTrack`은 `COMMON`·`ELECTIVE` 두 값이라
어느 선택과목인지 구분하지 못한다. `SubjectTrack` 확장은 양자 승인 제안이며, 값 구성은
A의 감지 R6 소비를 확인한 뒤 정한다.

#### 2-21.2 x축은 평가원 행동 영역 `[해소 · 2026-08-09 · 정본은 policies/taxonomy.md §3]`

🔴 **교체 요청은 철회했다. 처방이 「dict 하나 교체」가 아니라 「매핑 둘」이었다.**

진단은 맞았다 — *"어휘가 두 곳에 사는 것이 아니라 소비 목적이 둘이다"*(① AI 근거 ②
화면 표시). 그런데 그다음이 **A 소유 매핑 하나를 바꿔 달라**였고, 하나를 바꾸면 **두 목적이
같이 바뀐다.** 화면에 좋은 라벨이 학부모 문장 안 근거 팩트로도 좋은지는 **별개 판정**이고
자동으로 같지 않다(A 지적 · 8/9).

⚠ 그리고 요청 표의 `어휘·개념`에 **가운뎃점이 있었다.** `_r6_facts`가 `f"{area}·{type_}"`로
조립하므로 `"문학·어휘·개념"`이 된다 — 우리가 「적용·창의」를 짧은 형으로 줄인 바로 그
이유를 **우리 표에서 어겼다.** 목적을 나누면 이 함정이 따라서 사라진다: 긴 형은
**조립되지 않는 자리**(화면)에만 산다.

**확정 · 정본은 `policies/taxonomy.md` §3의 두 열 표다** — 여기 값을 복제하지 않는다.

| 축 | 소유 | 형태 | 근거 |
| --- | --- | --- | --- |
| AI 근거(`_TYPE_KO`) | A(`composition/briefing_context.py`) | **짧은 형**(사실·추론·비판·개념·적용) | R6 근거 팩트로 프롬프트에 실린다 · 구분자 충돌 회피 |
| 화면 표시(Step 1 그리드) | **클라이언트(FE/BE)** | 긴 형(평가원 행동 영역) | 🔴 **강사가 고치는 값**이라 클라이언트가 전 값의 라벨을 갖고 있어야 피커를 그린다 |

🔴 `display_label` 선례와 **반대인 이유가 그 축이다** — `display_label`은 강사가 **못 고치는**
값이라 라벨이 데이터에 붙어 다니는 게 맞지만, `type_tag`는 고칠 수 있는 값이라 우리가 실어
보내면 같은 enum에 매핑이 둘이 되고 그중 하나(피커)는 **우리 CI가 못 본다.** ⚠ 드리프트
걱정은 여기선 성립하지 않는다 — 두 매핑의 **값이 서로 다르다**(짧은 형 ≠ 긴 형). 같은
문자열의 복제가 아니라 **다른 목적의 다른 값**이다.

출처는 [2025학년도 수능 국어 행동 영역 보도](https://news.nate.com/view/20241114n16679),
[평가원 행동 영역 자료](https://orbi.kr/00012100098),
[국어과 행동 영역 연구](https://www.kci.go.kr/kciportal/landing/article.kci?arti_id=ART002536706)다.

#### 2-21.3 TypeTag 「적용·창의」 예약 — 소비처 5곳

이 확장은 A가 contracts 초안을 맡고 B는 contracts를 수정하지 않는다. **다섯 소비처가 서로
다른 답을 내는 것이 예약 규약의 전부**다. 하나로 뭉치면 강사 사실 기록을 잘못 막거나
브리핑에 영문 라벨이 샌다.

| # | 소비처 | v1 처리 |
| --- | --- | --- |
| ① | 분류기 산출 | 스키마에서 배제해 산출하지 않는다. 태깅 골든셋 부재로 검증할 수 없다(㊛) |
| ② | 출제 요청 `ProblemRequest.type_tags` | 400 `type_tag_not_supported`로 거부한다 — **잡을 만들기 전**이다(`enqueue()` 최상단 · 2026-08-09 구현) |
| ③ | 강사 수정 `/v1/confirmations` | 200으로 허용한다. 강사 판단은 사실 기록이며 ㊛의 재료다 |
| ④ | `difficulty_weights` 완전성 검증 | A가 `policy.py`의 기계적 파급으로 처리한다 |
| ⑤ | 브리핑 표시 라벨 | 예약 태그도 한글 라벨을 가진다. 표시 어휘와 산출 허용은 별개 축이다 |

⑤의 도달 경로는 실재한다. `_TYPE_KO.get(top.type, top.type.value)`는 라벨이 없으면
KeyError가 아니라 enum 값 `"apply"`를 반환한다. `week.cells`가 BE의 `LearningEvent`에서
생성되므로 ③이 값을 받는 순간 브리핑까지 흐르며, `_SYMBOL_RE`는 영문을 막지 않는다.
A가 `_AREA_KO`·`_TYPE_KO` 완전성 테스트를 자기 PR에 추가한다.

PG 전수 결과, ②의 구현 위치는 요청 경계다. `ProblemRequest.model_validate()`의 enum 검증만
있던 동안에는 A가 enum을 확장하면 `apply`도 자동 통과했다. **구현 완료(2026-08-09)** —
거절은 `problem_generation/enqueue.py::reject_unsupported_type_tags()`에 있고, 라우터가
아니라 **`enqueue()` 최상단**이다. 지키려는 불변식이 「400이 난다」가 아니라 **「미지원
태그로는 잡이 만들어지지 않는다」**이기 때문이다 — 워크플로에서 거절하면 400은 나지만 잡이
만들어졌다가 실패로 수렴해 **실패 원장만 남는다**(99 #01). 계약 테스트는
`tests/ai/contract/test_reserved_type_tag_request_door.py`이며 400·사유 코드·「잡 이전
거절」·「문과 가중치 키 집합 일치」 넷을 고정한다.

> ⚠ **8/9 이전에 이 절이 단정형이었다.** *"400으로 거부한다"* 만 적혀 있었고 그때 실제로
> 나는 것(HTTP 500 + 고아 잡)은 어디에도 없었다. 미구현 계약은 **약속한 동작과 현재 동작을
> 함께** 적는다(99 ㊩). 아래 표 ②의 옛 문장도 같은 이유로 현재형으로 고쳤다.

| 조사 대상 | 현행 4종 전제와 영향 |
| --- | --- |
| 생성 프롬프트 | `type_tag`를 “요청 값 그대로” 보존할 뿐 4종 열거가 없다. 변경 불요 |
| `domain/rules.py` | 생성 결과와 요청 태그의 동일성만 검사한다. 4종 열거가 없다 |
| `domain/difficulty.py` | ~~`weights.type_tag[item.type_tag]`를 조회하므로 설정 키 완전성에 의존한다~~ → **해소(8/9)**. 조회를 `DifficultyWeights.weight_for()`로 옮겼다 — 완전성 검사와 인덱싱이 같은 클래스에 산다. 예약 태그는 `ReservedTypeTagWeight`(`ValueError` 계열)로 **명시적 오류**다(`.get(tag, 0.0)` 아님 · 가산 0은 없는 사실이다) |
| `verify_config.yaml` | `fact`·`concept`·`infer`·`critic` 네 가중치를 명시한다 |
| `domain/policy.py` | `set(TypeTag)`와 설정 키의 완전 일치를 강제한다. A가 enum 확장의 기계적 파급으로 처리한다 |
| 라우터 요청 검증 | 현재 enum 파싱뿐이라 예약 태그 추가 후 명시적 v1 거부가 필요하다 |
| PG 테스트 | `infer`·`concept` 중심이며 4종 전수·예약 태그 거부 테스트가 없다 |
| 튜플·길이 가정 | 4 고정 가정은 없다. workflow는 요청 `type_tags` 길이에 따라 순환한다 |

`verify_config.yaml`에 `apply: 0.0`을 미리 넣지 않는다. 0은 “영향 없음”이라는 사실을
만들지만 아직 그런 근거가 없다. 예약 태그의 출제 자체를 막는 동안 없는 값을 지어내지
않는다.

#### 2-21.4 v1 잠금 구조와 로드맵

현재 지원 경계는 셋이며 선후 관계를 섞지 않는다.

1. **자료 조달 게이트:** `language + 자료 없음`, `reading + PassageRequest`,
   `literature + WorkSelection`만 통과한다. 세 조합 밖의 요청은 400이다.
2. **커리큘럼 그래프:** 현행 33노드가 전부 language라 자동 목표만 막는다. 수동
   `TEACHER_MANUAL` 경로는 `self._diagnosis()`를 호출하지 않으므로 그래프와 무관하다.

T2 본문은 지문 생성 노드를 거쳐 문항 생성으로 이어진다. 그러나 이 노드만으로 전 영역이
열리지는 않는다. T4·T5는 `PassageRequest.area_tag` 다영역화와 영역별 생성 입력 계약이
선행돼야 한다. T3는 LLM 생성 대상이 아니며, 보호기간이 끝난 동봉 작품 풀에서는 이미
결정론 선택으로 열린다. 살아 있는 작품만 BE의 라이선스 확인 원문 풀에 계속 종속된다.
그래프 5영역 확장은 **자동 목표의 선행 조건**이며 이 요청 계약 작업과 독립이다.

셀과 노드도 다른 필드다. `contracts/diagnosis.py`의 `cells`는 이벤트 기반 필수 필드이고
`nodes`는 그래프 기반 선택 필드다. 셀 생성 함수는 그래프 인자를 받지 않는다. 따라서 독서
이벤트가 오면 독서 셀이 생기고 판정까지 나며 **영구 UNKNOWN이 아니다.** 실제 불일치는
그리드가 “독서 추론 약함”을 판정한 뒤 출제하려면 BE가 `PassageRequest`를 구성해야 한다.
생성 규격 없이 `reading`만 보내면 400이고, 유효한 요청이면 T2 지문 생성 경로로 들어간다.

그러므로 「v1 미지원」 표시는 셀 전체나 독서 영역 전체가 아니라 **유효한 생성 요청을 만들 수
없는 출제 액션**에만 붙인다. T4·T5, `PassageRequest` 없는 독서, `WorkSelection` 없는 문학
요청이 그 대상이다.

| 우선순위 | 작업 | 의미 |
| --- | --- | --- |
| ✅ 완료 | `reading + PassageRequest` T2 본문 경로 | 지문 생성 뒤 같은 ContextPack 계열로 문항 생성 |
| ✅ 완료 | `literature + WorkSelection` T3 만료 작품 경로 | 리비전·해시·만료 검증 뒤 LLM 없이 작품·오프셋 선택 |
| P1 | `PassageRequest` 다영역화 | T4·T5 요청 계약과 영역별 생성 입력을 먼저 확정 |
| P1 | T3 생존 작품 라이선스 원문 풀 계약 | LLM 원문 생성 없이 BE의 `license_ref`·버전이 있는 풀만 추가 사용 |
| P2 | 커리큘럼 그래프 5영역 확장 | 자동 목표의 선행이며 P1과 독립 |

#### 2-21.5 대외 프레이밍

대외 설명은 “GPT로 문법 문항 생성”이 아니라 **“자료를 지어내지 않는 문항만 만든다”**다.
문법이라서 가능한 것이 아니라 지문을 만들지 않아서 가능하며, 이는 evidence 없는 산출물을
금지한 불변식 2의 직접 결과다. **못 하는 것이 아니라 안 하기로 한 것**이다. 근거 없는
지문을 LLM이 지어내는 경로를 처음부터 금지했고, v1은 국립국어원의 실재하는 어문 규범
근거가 있는 영역부터 열었다.

#### 2-21.6 와이어프레임 불일치

와이어프레임은 3열 × 3행이지만 계약은 행동 영역 4열 × 과목 4행이다. 특히 `critic` 열
누락은 단순 축약으로 보기 어렵다. FE는 표시 축을 계약과 맞추거나, 축약 근거와 누락된 행동
영역의 접근 경로를 별도로 결정해야 한다.

#### 2-21.7 결정 요약

| 항목 | 상태 | 결정 주체 | 계약 변경 | 비용 |
| --- | --- | --- | --- | --- |
| y축 과목 4행 | B 확정 | B | 없음 — `AreaTag` 6개 유지 | 화면 매핑 |
| TypeTag 행동 영역 라벨 | A 소유 파일 변경 요청 | A | 없음 | 매핑·완전성 테스트 |
| `SubjectTrack` 선택과목 구분 | 양자 승인 제안 | A+B | 있음 | contracts·소비처 검토 |
| 「적용·창의」 예약 | A contracts 초안 | A+B | 있음 | 소비처 5곳별 처리 |
| v1 미지원 표시 | 출제 버튼에 표기 | FE+제품 | 없음 | 계약 비용 0 |
| 생성 노드 | P1 | B | 내부 실행 경로 | 전 영역 출제 개방 |
| 그래프 5영역 | P2 | B | 그래프 데이터 버전 | 자동 목표 개방 |
| 3×3 와이어프레임 | 계약과 불일치 | FE+제품+B | 결정에 따라 다름 | 4×4 정합 필요 |

---

### 2-22. pg 그래프 super-step 상한 — 결정 기록 `[B 확정 · 2026-08-09 · #156]`

🔴 **PR 본문은 grep이 안 된다.** #156의 판단 근거가 커밋 메시지와 PR 본문에만 있어 다음
사람이 못 찾는다 — 결정 로그 87(*"철회는 산출물이 아니라 산출하는 코드에 적는다"*)과 같은
형태라 여기 남긴다.

#### 2-22.1 실측 — 위험의 방향이 등재문과 달랐다

99 `#08` ⓑ가 *"라이브러리 기본값에 의존하고 버전마다 다르다"* 로 적혔는데, 실측하면 위험이
반대쪽이다.

```
langgraph 1.2.9  _internal/_config.py:32
DEFAULT_RECURSION_LIMIT = int(getenv("LANGGRAPH_DEFAULT_RECURSION_LIMIT", "10007"))
```

- ⓐ **사실상 무한**이라 불변식 6의 방어가 **실제로 없다**(「상한을 세웠다」는 선언만 있다 — ㊺ 부류)
- ⓑ **환경변수로 덮인다** — 상한이 저장소 밖에서 바뀐다
- ⚠ 종전 langgraph는 이 값이 **25**였다. 핀이 되돌아가면 계약 최대(`count=20` → 유도값 162)가
  기본값을 넘어 **정상 요청이 `GraphRecursionError`로 죽는다**

#### 2-22.2 유도식과 그 재료

```
graph_recursion_limit = count × (item_attempt_limit + difficulty_regen_max) × 2 + 2
```

| 재료 | 출처 | 왜 거기 |
| --- | --- | --- |
| `count` | `ProblemRequest`(계약 `ge=1, le=20`) | 요청마다 다르다 |
| `item_attempt_limit`·`difficulty_regen_max` | `verify_config.yaml` | 임계값은 설정 파일 |
| **`_STEPS_PER_ATTEMPT = 2`** | **코드** | 🔴 그래프 모양에서 나오는 값이다 — 노드를 늘리면 같이 늘려야 하고, 안 늘리면 상한이 **정상 실행을 자른다.** 설정 파일로 빼면 「값이 바뀌면 코드 diff가 생긴다」의 반대가 된다 |

⚠ **이 값은 루프를 막는 장치가 아니다.** 진짜 상한은 상태기계(cursor 단조 증가 ·
`item_attempt ≤ item_attempt_limit`)가 든다. 여기는 **그게 깨졌을 때 걸리는 마지막 그물**이라
「정상 최대치보다 크고 폭주보다는 작게」가 기준이다.

#### 2-22.3 counsel·probe에 그대로 복사하면 안 된다

pg는 `count`가 **계약에 상한이 있어서**(≤20) 요청만 보고 유도할 수 있다. 다른 그래프는 재료가
다르다 — counsel은 실행 시점 번들 크기, probe는 `import_probe_loop_max`. **유도식이 아니라
「계약이 정한 최대 반복 수를 찾아 쓴다」는 규칙이 이식 대상이다.**

#### 2-22.4 배선까지 테스트한다

유도 함수를 최소값으로 바꿔치면 `GraphRecursionError`가 나야 한다. 계산만 하고 `ainvoke`
config에 안 실으면 **선언만 있고 소비가 0**인 상태(㊺)가 되는데, 값을 검사하는 테스트만으로는
그걸 못 잡는다.

---

### 2-23. `LexiconLookup` 소비처가 0이다 `[B 관측 · 2026-08-09 · 미해소]`

`infrastructure/stdict.py`가 표준국어대사전 오픈 API 어댑터를 **실측 기반으로 구현**해 뒀고
(`view.do`의 XML 전용 응답·2단 조회 등 문서와 다른 실제 동작까지 주석에 남아 있다),
`application/ports.py`에 `LexiconLookup` 포트도 서 있다. 🔴 **그런데 워크플로·규칙 검증에서
그 포트를 쓰는 자리가 0건이다**(전수 8/9).

⇒ `05` §1.0의 **대조 트랙 두 축 중 「어휘 표제어 실존」이 파이프라인에 안 물려 있다.** 문법
(어문 규범 CSV)만 `GrammarNormGraphContextService`로 연결돼 있다.

⚠ **이제 도달 가능한 잔여다.** T2 본문 생성 경로는 열렸지만 `LexiconLookup` 소비자는
여전히 0건이다. 따라서 지문 생성이 어휘 표제어 실존 대조까지 해결했다고 간주하면 안 된다.
어휘 대조가 필요한 문항을 지원한다고 선언하기 전에 이 포트를 R-1 경로에 배선해야 하며,
그 전에는 생성 노드만으로 대조 근거를 대신하거나 게이트를 완화하지 않는다.

#### 2-23.1 v1 잠금 상태 `[B 구현 · 2026-08-12]`

프로덕션 전수 확인에서 `DICT_ENTRY` 근거를 생산해 워크플로에 넣는 경로는 여전히 0건이다.
따라서 생성 문항이 `DICT_ENTRY` 근거를 요구하면 조용히 통과하거나 폐기하지 않고
`verification_unavailable + source_unverified`로 끝내며, 상세에
`LexiconLookup 미배선`을 명시한다. 이는 게이트 완화가 아니라 미구현 축의 fail-closed 잠금이다.

`LexiconLookup` 포트와 `stdict.py` 어댑터는 삭제하지 않는다. 어휘 문항을 v1 지원 범위로
열려면 R-1이 표제어·의미 코드를 조회하고 승인된 `DICT_ENTRY` 앵커를 생산하는 경로를 먼저
배선해야 하며, 그때 이 잠금을 실제 사전 대조 판정으로 교체한다.

---

### 2-24. `POST /v1/problems` 202가 `status`를 싣는다 `[B 구현 · 2026-08-09 · 04 개정 요청]`

A의 런북 §2가 규칙을 하나 세웠다 — *"202의 `status`가 종단이면 통지를 기다리지 말고 바로
GET한다."* 🔴 **그 규칙을 `/v1/problems`에는 적용할 수 없었다.** pg의 202가 `job_id` 하나만
싣고 있었기 때문이다(counsel은 `{job_id, status}`).

| 엔드포인트 | 종전 202 `data` | 지금 |
| --- | --- | --- |
| `POST /v1/counsel/drafts` | `{job_id, status}` | 그대로 |
| `POST /v1/problems` | `{job_id}` | **`{job_id, status}`** |

#### 2-24.1 왜 필요했나 — 두 경로가 실재한다

pg는 POST 안에서 워커를 동기 실행하지만 **`run_next()`가 자기 잡을 처리한다는 보장이 없다**
(우선순위·aging 순으로 다음 잡 하나). 그래서 202가 나가는 시점의 상태가 두 갈래다.

- **종단**(대부분) — 이미 끝났으므로 **통지를 기다리면 안 된다**
- **`queued`** — 앞선 잡이 처리된 경우. 내 잡은 큐에 남는다

`job_id`만 실어 보내면 BE가 **어느 쪽인지 응답만으로는 알 수 없다.** 통지를 기다리면 종단인
잡을 두고 무한정 기다리고, 안 기다리면 아직 안 끝난 잡을 종단으로 오해한다.

#### 2-24.2 🔴 함께 알아야 하는 것 — 배경 드레인 루프가 없다

`run_next()`의 프로덕션 호출처는 **라우터 두 곳뿐**이다(`counsel.py`·`problem.py` — 전수
8/9). 즉 **잡을 도는 유일한 계기가 새 요청**이다.

⇒ 큐에 남은 잡은 **다음 POST가 올 때까지 안 돈다.** 트래픽이 끊기면 마지막 잡은 그대로
남는다. Kafka 통지도 아직 뼈대뿐이라 **BE가 기다릴 대상이 없다.**

⚠ **이건 v1 인라인 실행의 성질이지 결함 신고가 아니다** — 다만 BE가 「넣으면 곧 돈다」로
읽으면 틀린다. 202의 `status`가 그 오독을 막는 최소 장치이고, 진짜 해소는 워커 루프
(BE-5 Kafka)와 같이 온다.

> **구현 갱신 `[B 확정 · 2026-08-12]` — 위 제안 당시 문면을 보존하고 현재 상태를 덧붙인다.**
> BE 미진행으로 API·이벤트 문서 축의 상위 결정권이 B로 왔다. 기다릴 Kafka 소비자·워커가
> 없으므로 `api/routers/problem.py`의 router startup/shutdown과
> `problem_generation/application/drain.py`에 **인프로세스 배경 드레인**을 붙여 이 공백을
> 닫았다. POST가 enqueue한 tenant를 알리고, 드레인은 기존
> `ProblemGenerationRunner.run_next(tenant_id=…)`만 호출하므로 lease·fencing·테넌트 격리를
> 우회하지 않는다. 한 사이클 잡 수에 상한이 있고, 유휴 대기·연속 실패 백오프·종료 시
> 사이클 정리와 태스크 cancel/await를 Settings로 제어한다. 기본은 켜짐이다. 꺼짐을 기본으로
> 두면 운영 설정 누락만으로 #21이 그대로 재발하기 때문이다.
>
> 이 구현은 **Kafka 워커 루프를 대체하지 않는다.** 별도 워커·프로세스 간 wake-up·terminal
> 이벤트 전달은 여전히 Kafka 축의 책임이다. 이번 드레인은 외부 워커가 없는 v1 프로세스에서
> 마지막 queued 잡이 다음 POST 없이도 종단되게 하는 최소 실행 보장이다. `docs/99_open_items.md`
> #21은 A 축이므로 **A 갱신 필요**다.

#### 2-24.3 A에게 요청

`04_api_contract.md` §3.11의 예시 줄이 종전 형태다 — 개정 부탁드린다.

```
// 종전: 202 { "data": { "job_id": "8e94ceac-…" }, … }
// 지금: 202 { "data": { "job_id": "8e94ceac-…", "status": "succeeded" }, … }
```

⚠ 런북에 §2-24.2(드레인 공백)를 한 행 넣어 주시면 BE가 「queued인데 왜 안 도나」를 안
묻는다. 04 §3.9의 counsel 규칙과 **같은 문장으로** 덮인다.

---

### 상담 읽기 모델 영속 스키마 판정 ⓑ `[번호 미부여 · B 확정 · 2026-08-10]`

#### 판정 — 전용 테이블 하나와 독립 스냅숏 둘

선택지는 **ⓑ `COUNSEL_DRAFT_VIEW` 전용 테이블 + JSONB 스냅숏**으로 확정한다. GET 뷰
캐시와 refine 초안 캐시는 키가 `(tenant_id, job_id)`로 같으므로 한 행에 두되,
`view_snapshot`과 `draft_snapshot`은 각각 nullable인 정본으로 분리한다. 두 캐시는 채워지고
축출되는 시점이 달라 한쪽만 남은 상태가 실제로 존재하기 때문이다.

형태는 `㉕`·`PROBLEM_ITEM`의 **스냅숏 정본 + 조회용 파생 투영** 선례를 따른다. 조건도
같이 건다. ① 파생 투영은 단일 함수가 캐시 키와 스냅숏에서 유도하며 호출자가 따로 정하지
않는다. ② 저장값이 유도값과 갈리면 테스트가 red다. ③ ORM 코드 옆에 스냅숏이 정본임을
남긴다. 계약 필드가 늘어도 스냅숏이 전문을 받아, 컬럼 누락 때문에 무손실 왕복이 다시
깨지는 일을 피한다.

다른 선택지는 다음 이유로 택하지 않았다.

- **ⓐ:** A가 적은 대로, 기존 `draft`·`draft_revision`을 늘려도 `_CachedView` 셋은 남는다.
  양자 승인 컬럼 여덟을 열고도 `㉿`를 닫지 못한다.
- **ⓒ:** 도메인 행 `draft`에 응답 뷰를 얹으면 「학생에게 보낸 초안」과 「강사 화면 뷰」가
  한 행에 앉아 서로 다른 축이 섞인다.
- **ⓓ:** 강사가 다시 생성 버튼을 눌러 초안이 둘 생기는 경우를 v1 한계라고 못 박을 근거가
  없으므로 안전한 영속 규약이 아니다.

#### A가 물은 세 가지에 대한 답

1. **선택지는 ⓑ다.** 읽기 모델 전용 테이블에 두 캐시 전문을 분리 저장한다.
2. **`draft.content`가 없는 것은 의도가 아니라 결손이다.** 본문을 테이블 밖에 두기로 한
   결정 기록을 B도 찾지 못했다. `0001`부터 쓰는 사람이 0명이어서 드러나지 않았던
   `#22`·`ItemCandidate`와 같은 형태이며, ⓑ의 `draft_snapshot`이 본문을 보존하면 닫힌다.

   > 🔴 **(2026-08-12 · 지시서 73으로 갱신) 뒷문장은 더 이상 유효하지 않다.** 본문 정본은
   > `draft_snapshot`이 **아니라 `DRAFT.content`**(0010)로 확정됐다. 이유: `COUNSEL_DRAFT_VIEW`는
   > **조회 캐시**라 정리 배치가 비우는 대상이고, 본문을 거기 두면 **캐시를 비우는 순간
   > 산출물이 사라진다.** 최초 본문은 `DRAFT.content`, refine 이력은 계속 `DRAFT_REVISION`이
   > 축이다. ⚠ 이 문단을 지우지 않고 남긴다 — 지우면 다음 사람이 *"원래 어느 쪽이었나"* 를
   > 다시 판단한다(99 ㊩).
3. **기존 두 테이블의 프로덕션 쓰기가 0건인 줄은 몰랐다.** 이 관측은 오히려 ⓑ의 근거를
   보탠다. 아무도 쓰지 않는 테이블에 컬럼 여덟을 늘리기보다 실제 읽기 모델 소비자가 쓸
   형태를 새로 세우는 편이 정직하다.

#### 이번 변경이 닫지 않는 것

이번 PR은 스키마 **자리만 만든다.** `_view_cache`·`_drafts`를 새 테이블에 배선하는 일은
A의 축이며, 그 전까지 `㉿`·`㉻`·`㉬`의 나머지 절반은 **닫히지 않는다.** 따라서 99에
완료 표시를 하지 않는다.

`docs/06_erd.md`는 `㉕` 선례대로 모델 변경과 같은 PR에 넣었다. 문서가 코드를 앞서 미래
상태를 현재형으로 선언하지 않게 하고, ORM·마이그레이션·ERD parity를 한 변경에서 함께
검증하기 위해서다.

---

### AGENT_RUN 실행 신원과 AI_RUN 물리 FK 분리 `[판정 ③ 구현 · 2026-08-10]`

`AGENT_RUN`은 모델 실행 전 `queued` 단계에서 먼저 생기며, 모델 호출 없이 끝나는 경로도
있다. 따라서 `run_id`는 `WorkerJob.execution_id`인 **잡 실행 신원**으로 NOT NULL 유지하되,
`AI_RUN` 선행 존재를 강제하던 `fk_agent_run_run_id_ai_run` 물리 FK만 제거한다. `AI_RUN`이
존재하는 경우에는 같은 ID로 논리 결합한다.

`SIGNAL`·`DRAFT`·`WEAKNESS_MAP`·`LLM_CALL`·`PROBLEM_SET`의 AI_RUN FK 다섯은 유지한다.
이 행들은 실행 뒤에 생기는 산출물·관측 축이므로 실제 AI_RUN을 부모로 요구하는 제약이
생애주기와 충돌하지 않는다.

생애주기별 원장 완전성 판정과 PG 점검은 A 소유이며 이 PR 범위 밖이다. G1 뒤에는
`test_pg_ledger_audit.py::test_an_orphan_agent_run_is_flagged`의 ORM 메타데이터 기반 조건이
풀려, 기존 strict xfail이 실제 실 PG 검증으로 전환된다.

---

## §3. OPEN 총괄 표 (잔여만 — 해소분은 §0)

| 번호 | 항목 | B 권고안 | 담당 | 관련 part_b |
| --- | --- | --- | --- | --- |
| B-3 잔여 | taxonomy 경계 사례 7건 판정 | 태깅 골든셋 시드와 동시 확정 | A+B | 04·06 §5 |
| Open-12 | F17 OCR 실명→alias·OCR 소유 (P2) | 스캔·매칭·마스킹=BE 유지, 판독 소유는 벤더 선정과 함께 | BE(+A·B) | 02 §1-C |
| B-2 | 공용 계약 리뷰·구현·14항목 승인 완료 — **문서 동기화 3/7 완료, 4건 잔여(§2-10)** | 잔여 4건 소유자 반영 요청 | A+B | 02 §5 |
| B-5 / D-06 | ✅ PR #15로 로컬 OpenAI 호환·Gemma 계열 공급자와 어댑터 확정 — verifier 폴백 패밀리만 잔여. **B-5 정리 PR 할 일 2건** `[2026-07-28]`: ① `gateway.py`의 `TODO(B-5)` 제거(provider 1개일 때 패밀리 강제가 우회되는 현행 동작) ② **`problem_generation/provider.py` 조립부 가드 + `test_provider.py`**(§1-10 "가드 위치") | 폴백 패밀리 확보 후 generator/verifier 패밀리 분리 강제. 두 항목 모두 role 키 설정 구조를 공유하므로 같은 PR에서 처리 | A+B | 06 §2·§3 · §1-10 |
| B-7 | ✅ **해소** — evidence resolver 시그니처 A 승인·**PR #37 구현 완료** · B 사후 검증 완료(조건 5개 전량 충족) | 경계 3단 확정: 그래프 내부 검증(B)·근거 해소(A)·R-1 이중 통과. 잔여는 R-1 연동 구현 시 B-13 | A+B | §2-12-② · `11` §5 |
| **B-8ⓑ** | **GraphRAG `VersionSet` 3필드 확장** — `content_graph_version`·`graph_index_version`·`retrieval_config_version`. **`graph_version` 재사용 금지** | B-8ⓐ capability별 validator와 병합. ⓐ·ⓑ 모두 `contracts/execution.py`(양자) + `04_api_contract.md` §2.2 + `06_erd.md` AI_RUN 동시 개정이 필요해 PR 단위가 같다. A-5→B-7 병합과 대칭 | A+B | §2-12-① · `10` §4.2 |
| **B-12** | ✅ **해소 — A 승인·반영 완료(PR #49 · `02_ownership` v5 + `CLAUDE.md`, 12→13곳).** 반영처는 A가 추가로 찾아낸 :7의 v3→v4 이력 정정까지 포함한 5곳 | `graphrag.py` 공용 계약 등록과 생산(B)·소비(A) 분리 사유까지 정본에 반영 완료 | A+B | §2-14 · `11` §0 |
| **B-13** | ✅ **해소** — A가 (가)안을 채택해 `_BLIND_PAYLOAD_MODULES`로 조립 지점 한 곳만 검사하고, 현재 6키 화이트리스트의 정확 일치를 강제했다. **PR #40 머지 완료** | R-1 GraphRAG 확장 착수 가능. 잔여는 FMT-6 자료 블록 추가 시 화이트리스트 동시 갱신 | A+B | §2-15 · `06` §1 · `11` §5 |
| **B-14** `[신규]` `[P1]` | **LangGraph counsel_pack 노드 state의 `emphasis_points`에 근거 라벨·수치·`record_id`가 실측 등재된다.** alias와 논리 참조라 실명·연락처는 없어 **불변식 3 해소 판정은 유지**하지만, 학습 정보의 IP·프라이버시 노출면은 남는다. 프롬프트는 트레이스에 실리지 않고 briefing은 span 0건이며, mapping_probe와 P2 실적용 결과는 미확인이다 | **`TRACING=false` 유지가 전제.** P1 ✅ PR #48 · 동의어 4종 확장 머지 완료(#59 · `5d8e1a4`) — **develop 가드는 더 이상 `LANGCHAIN_TRACING_V2` 등으로 우회되지 않는다** · P1' ✅ PR #51 · 부속 실측 ✅ PR #51 · 판정 소스 단일화 ✅ (A-11 PR #61 + B의 OR 제거) · **P2 ☐ serde·클라이언트 은닉만 미완.** 넷 중 하나라도 미완이면 TRACING을 켜지 않는다 | A+B | §2-16 · `01` §5 · `langgraph_state` §1.2·§2.4 |
| **B-9** `[신규]` | 난이도 사유 재생성 시 **이전 검증본 보존 규칙** — 검증 통과 문항이 미검증 문항으로 대체될 수 있는 미정의 동작 | `07` §4의 "마지막 검증본 유지"를 생성 경로에 대칭 적용 제안. 확정 전 `difficulty_regen_enabled=false` 유지 | B 초안 → A+B | `10` §4.1 C3 |
| **BE-11** `(구 B-10)` | ✅ **A 판정 완료 — RLS 구현 부재는 문서 표현을 앱 계층 격리로 정정해 해소.** RLS 실도입 여부는 백엔드 합의 안건으로 이관 | 도입 시 `db/session.py`·`db/store_factory.py` 연결·역할 설계와 함께 기존 26+B 8테이블에 일괄 적용. 현재 B 8테이블은 기존 패턴 준수 | **BE** | §2-4.5 |
| **W1** `[신규]` | **다중 목표·다중 measured area 세트** — M2 와이어프레임 Step 1은 셀 여러 개를 담고 개수를 각각 지정하나, `05` §4.1은 **v1 단일 영역 제한** | 요청 분할 vs 요청 형식 확장 중 택일. 협업설명서도 "회의 결정 필요"로 등재 | A+B+제품 | `05` §4.1 · `10` §6 |
| **W2** `[신규]` | 화면이 **셀에 `suspect`를 표시**하나 `04` §4의 셀 verdict는 `unknown\|weak\|ok` 3종이고 `suspect`는 **노드** verdict | 셀 verdict 확장 vs 화면이 노드 verdict를 셀에 투영 중 택일 | B(+FE) | `04` §4·§5.1 |
| **W3** `[신규]` | 완료 알림 payload — 화면 문서는 수량(통과·검토·폐기)을 알림에 싣고, §2-1은 `result_ref` 조회로 얻는다 | §2-1 유지 권고(알림 경량화) | BE+B | §2-1 |
| **W4** `[신규]` | 문항 상세 응답에 **`available_actions`·`current_revision_no`** 포함 요구(협업설명서) | B 단독 신설 가능 — 계약 확정 후 FE 통보 | B(+FE) | `07` §1 |
| **W5** `[신규]` | `needs_review` 문항의 **"강사 확인 완료로 표시"** 액션이 `ItemAction` 5종에 없음 | 확인 기록 소유가 BE일 가능성 — AI는 발행 플래그 미반환 원칙 유지(U14) | BE+제품+B | `03` U14 · `07` §1 |
| **W6** `[신규]` | **반 단위 출제** — 화면에 반 카드가 있으나 `DiagnosisInput.student_ref`는 단수, 반 집계 진단 규격 없음 | 구성원 기준시점·집계·alias 입력 규격 선결 | BE+제품+B | `04` §3.1 · `10` §6 |
| **W7** `[신규]` | **교육과정 개정판** — 2026년에 고2·고3 동시 지원 시 2022 개정·2015 개정 정본 2벌 필요 | 지원 학년 확정 선결. `curriculum_graph.yaml` 작성 범위가 갈림 | 기획+B | `04` §2 · `10` §7 |
| **W8** `[신규]` | **M2 수능형 포맷 적합성 총괄** — 상세는 [`12`](12_suneung_format_alignment.md) FMT-1~11 | 개별 FMT 안건을 이 표에 중복하지 않고 `12` 정본에서 오너별로 추적 | B(+각 오너) | `12` §5 |
| **W9** `[신규]` | **`type_tag` 4종이 실제 문항 유형의 절반을 못 담음** | 능력 범주 확장 방향을 §2-13에서 제안하고 실제 어휘·경계는 양자 승인 | A+B | §2-13 · `12` §3 |
| **W10** `[신규]` | **롤백 재검증이 FIX-09와 충돌** — `07` 정정 | 롤백 본문 복원 후 게이트 ①②③ 전체 재검증으로 정합화 | B 단독 | `07` §1 · `10` §3 FIX-09 |
| **W11** `[신규]` | **크로스 플랫폼 정합성** — `ci.yml`이 `ubuntu-latest` 단독이라 Windows 경로 결함을 구조적으로 잡지 못한다. 실제 결함은 `test_composition_redaction.py:46` 한 줄로 축소됐다. 문학 풀 원문은 `.gitattributes`의 `-text`로 바이트를 고정했지만 저장소 전체 개행 정책은 아직 없다 | ① `.as_posix()` ✅ `17fecfb`·PR #50 ② `windows-latest` ✅ `e5cd95f`·PR #50 · A가 제안 밖에서 OS 무관 회귀 `b994017` 추가 · ③ 문학 풀 `.txt` 바이트 고정 ✅ · 저장소 전체 `.gitattributes` 정책은 A+B 잔여 | A(+B) | §2-17 · `ci.yml` |
| **W12** `[신규]` | **난이도 밴드가 실측 정답률과 단조 대응하지 않는다** — [`part_a/13`](../part_a/13_threshold_validation.md) §4-2 AI Hub 국어 8,572건 실측에서 **NORMAL 34.6% < HARD 49.2%**다. A는 "강사 입력도 같은 종류의 주관 라벨이라 같은 문제를 재생산한다"고 판정했고, `requested_difficulty`(`contracts/problem_generation.py:102`)가 바로 그 강사 입력이다. **현행 영향은 제한적이다** — `DIFFICULTY_BAND_MISMATCH`는 `ProblemFailureReason`이 아니라 **`ReviewReason`**(`:253`)이라 문항을 폐기하지 않고 `needs_review`로 보내며(`workflow.py:746`), `verify_config.yaml:11 difficulty_regen_enabled: false`라 재생성 루프도 돌지 않는다. **실제 비용은 강사 주의 예산이다** — 예측력이 검증되지 않은 기준으로 검토 요청을 만들고 있고, 이는 §1-8 경보 상한 철학과 충돌한다 | **`difficulty_regen_enabled=true`로 가기 전 선결 조건으로 둔다** — 켜는 순간 미검증 기준 위에서 재생성 루프가 돈다(B-9가 같은 플래그에 걸려 있다). ① 플래그 `false` 유지(현행) ② 파일럿에서 M2 문항의 실제 정답률을 수집해 밴드 정의를 재검토 ③ 밴드 판정을 주관 라벨이 아니라 실측 기대 정답률로 옮기는 경로 검토 — `passage_ref`(`05` [A 확정 통보 8/3]) 수신과 `PASSAGE_TYPE_STAT` 누적에 종속(`part_a/13` §4-3-4) | **B** | `part_a/13` §4-2·§4-3-4 · §3 B-9 · `05` §4 · `06_quality_gates` |
| **W13** `[신규]` | **셀 판정만 원시 정답률에 남았다** — `diagnoser.py`의 `cell_delta_pp = cell_acc − student_overall_acc`는 문항 난이도가 균일하다고 가정하는데, [`part_a/13`](../part_a/13_threshold_validation.md) §4-3-3 실측상 **난이도 변동의 84%가 같은 영역×유형 안에서** 발생한다. 셀 정답률이 낮은 것이 약점 때문인지 어려운 지문을 뽑아서인지 구분되지 않는다([`04_curriculum_graph.md`](04_curriculum_graph.md) §4 오차 주석). A는 `03b0397`로 R1을 잔차로 이관해 **원시 정답률을 판정에 그대로 쓰는 곳은 이제 B의 셀 판정뿐**이다 | 기대치 잔차로 이관한다 — A가 만든 `PASSAGE_TYPE_STAT`(지문×유형 실측 누적, `db/models.py`)과 `passage_ref` 수신(B 구현 완료)이 재료다. **선결 조건 3**: ① 백엔드가 `passage_ref`를 실제로 보내기 시작할 것 ② 조합당 `expectation_min_n` 충족 ③ `part_a/13` §4-3-5 단서 확인 — EdNet 태그가 293종이라 국어 `type_tag` 4종에서는 회수율이 67%보다 낮을 수 있고, 재사용률이 낮으면 실효가 떨어진다. 이관 시 `config_version` 인상 + 골든 재생성 + `04` §4 오차 주석의 해제 여부 재판정 | **B** | `part_a/13` §4-3 · `04` §4 · `06_erd` PASSAGE_TYPE_STAT · `05` [A 확정 통보 8/3] |
| **W14** `[신규]` | **골든·데모 픽스처의 `area × type` 분포가 현실을 대표하지 않는다** — 실측(2026-08-04 · JSON 파싱): `diagnosis_cases.json` 123건이 **3셀**(`language×concept` 37 · `language×fact` 36 · `reading×fact` 50 — `critic`·`infer` 0건), `diagnosis_seed_cases.json` 14건이 **2셀**, A의 `part_a/examples/detect_demo_request.json` 973건이 **2셀**(`infer` 하나뿐). **24셀 중 합집합 5셀이다.** 두 가지가 따라온다 — ⓐ **B**: 셀이 늘었을 때 `unknown` 라우팅과 `overall_low` 분모 규칙이 골든으로 검증되지 않는다 ([`04`](04_curriculum_graph.md) §4 ③) ⓑ **A**: 셀이 2개면 두 셀의 오답 비율 합이 1이므로 **오답이 정확히 50:50이 아닌 한 `cell_error_share ≥ 0.5`가 필연 충족**이라, 현재 데모에서 R6는 사실상 `n ≥ 10 AND cell_acc < 0.5` 두 조건만으로 발화한다 — 편중 판정이 작동한다는 근거가 없다 | 파일럿이 아니라 **지금 항목**이다. ⓐ B가 셀 4단계 PR에 코퍼스 대표성 한계를 명시하고 합성 케이스 의존을 기록한다 ⓑ A가 발표 데모 시드를 보정한다(99 D ㊵ ㉠). 코퍼스 확충 자체는 실제 학습 로그 확보에 종속되므로 v1에서는 **한계를 정직하게 적는 것까지**가 범위다 | **A+B** | `04` §4 ③ · `part_a/13` §5-4 · `detection/features.py:100` · 99 D ㊵ ㉠ |
| **W15** `[신규]` | **기준 자료의 출처 표시 페이지가 없다** — 어문 규범 자료가 **공공누리 제1유형(출처표시)**이라 상업 이용·변형은 되지만 **출처 표시가 의무**다(발행연도·기관명·홈페이지 URL 포함). 사전 자료는 라이선스가 다를 수 있고 우리말샘은 CC BY-SA로 알려져 있어 **동일조건변경허락**이 붙는다 — 자료마다 조건이 달라 한곳에 모아야 누락이 없다 | **서비스 소개·라이선스 페이지에 모아 표시**한다(`05` §1.1.2 확정). 문항 화면에는 띄우지 않는다 — 공공누리가 위치를 지정하지 않고, 문항마다 띄우면 강사 화면이 법적 고지로 오염된다. **FE가 페이지를 만들고 AI는 자료별 표시 문구를 `SOURCE.txt`로 제공**한다. 자료가 추가될 때마다 이 목록을 갱신하는 절차도 함께 정한다 | **FE + B** | `05` §1.1.2 · `kogl.or.kr/info/licenseType1.do` |
| **W16** `[P3 — 비상업 전제로 내림 2026-08-05]` `[신규]` `[P1]` | **표준국어대사전 뜻풀이의 CC BY-SA 전파 여부가 미확인이다** — 저작권 정책이 상업 이용을 허용하면서 동시에 "변경하여 새로운 저작물을 만들 때, 그 저작물도 동일한 라이선스로 배포해야 합니다"를 명시한다(동일조건변경허락). 사전 뜻풀이를 문항에 실으면 **그 문항이 CC BY-SA 대상인지** 불분명하고, 상용 SaaS 산출물이라 그대로 두면 위험이 남는다. 또 **출전이 붙은 용례는 원저작자 별도 허락이 필요**하다고 정책이 따로 적고 있다 | **회피가 먼저 적용돼 있다** — `dict_entry` 근거는 `ref`만 남기고 `quote`는 `null`이라 뜻풀이를 산출물에 복제하지 않는다(`05` §1.1.3). 참조는 대조 근거이지 2차 저작물 작성이 아니다. 출전 용례도 쓰지 않는다. **다만 이건 회피책이지 판정이 아니다** — 국립국어원 질의나 법무 확인으로 닫아야 한다. 확인 전까지 뜻풀이 복제 경로를 열지 않는다 | **기획(법무) + B** | `05` §1.1.3 · `stdict.korean.go.kr/join/copyrightPolicy.do` |
| **W18** `[P3 — 비상업 전제로 내림 2026-08-05]` | **AI Hub `71857` 국어 교과 지문형 문제 데이터 — 상업 이용에만 구축기관 협의가 필요하다.** 확보해 `local_data/`에 뒀고 라벨을 전량 실측했다(문항 1,822 · 정답·오답·해설 라벨 · 2022 성취기준 30종 · 지문 2,630개 · PNG+Bounding_Box). **`9.1` curriculum_graph 근거 · `9.4` 교사 검수 골든셋 · `C-15` R-8 외부 대조 코퍼스 · `F17` OCR 학습셋을 한 번에 닫을 수 있는 자료다** — 지금까지 전부 "경로가 없다"로 적어둔 항목들이다. AI Hub 이용정책이 협의를 요구하는 대상은 **상업적 이용·판매**이고 **비상업적 연구개발은 허용**한다 — 이 프로젝트는 부트캠프 대회 출제용이므로 후자다 | **비상업 전제에서 사용한다.** ① **상용 전환 시 이 항목이 P1으로 되살아난다** — 그 시점에 구축기관 협의가 선행 조건이고, 협의가 안 되면 ② 협의가 안 되면 `9.1`·`9.4`는 사람 작업으로 돌아가고 `C-15`는 계속 빈다 ③ 쓰게 되어도 중등 성취기준 655건은 제외하고 고등 19종 1,489건만 쓴다(CLAUDE.md 중등 분기 금지) ④ 난이도 라벨은 하가 71%로 치우쳐 밴드 검증에 그대로 못 쓴다 | **기획 + B** | `05` §1.1.4 · §8.3 · `08` §9.1·§9.4 · `aihub.or.kr/intrcn/guid/usagepolicy.do` |
| **W17** `[신규]` `[P1]` | **어휘 대조에 자료 버전이 없다 — 재현성 갭(불변식 8).** R-1 어휘 축은 표준국어대사전 API를 조회하는데 **API가 자료 버전을 노출하지 않는다.** 어문 규범은 폴더명이 곧 버전이라 `VersionSet`에 적을 값이 있지만 사전은 적을 값 자체가 없다. 사전이 개정되면 **같은 문항이 어제는 통과하고 오늘은 실패**할 수 있고, 과거 문항의 재검증이 재현되지 않는다 | 전체 덤프(`local_data/`, 표준국어대사전 657 MB · 우리말샘 1.74 GB · 기초사전 369 MB)는 날짜가 곧 버전이라 이 갭을 닫는다. 다만 색인이 99.8 MB라 동봉 선을 넘고 뜻풀이 재배포 문제가 겹쳐(W16) **A-1 저장 계층에서 적재하는 쪽으로 미뤘다**(`05` §1.1.3). 그때까지는 갭을 감수하고, **AI_RUN에 조회 시각이라도 남길지**를 A-1과 함께 정한다 | **A + B** | `05` §1.1.3 · `contracts/execution.py` VersionSet |
| D-03 | ✅ **자료 확보 완료 `[2026-08-05]`** — 문법은 어문 규범 CSV 8종 패키지 동봉(`05` §1.1.1), 어휘는 표준국어대사전 오픈 API 실 왕복 확인(`05` §1.1.3) | 잔여는 자료가 아니라 ① 대조 스냅숏 영속화(A-1과 함께) ② 라이선스 전파 확인(W16) ③ 어휘 후보 풀 전수(A3 이후) | B+기획 | 05 §1.1 |
| D-04 / C-14 `[P0]` | T2 사실성 보장 수단 | 승인 자료 기반+source_ref, 수단 없으면 `source_unverified` 차단 | B+기획 | 05 §2.1 |
| C-15 `[P0]` | 외부 표절·유사도 + injection 코퍼스 | 05 §8.2·§8.3, 08 §8 예약 | B+기획 | 05·08 |
| D-09 | PDF Phase·소유자 | B 조판 P2 확정 표기, MVP 단순 내보내기는 별도 결정 | 기획+BE+FE+B | 05 §9 |
| D-10 | B API·Kafka 이벤트 편입(약점 지도 API 형태 포함) | §2-1 초안으로 BE 리뷰 | BE+B | 02 §1 |
| — | 승인된 문항 재수정 시 승인 철회·재검수 절차 | 철회 전 수정 거부 `[제안]` | BE | 07 §8 |
| — | 리비전 보존 기간(개수 무제한 `[잠정]`) | BE 보존 정책과 함께 | BE+B | 07 §6 |
| — | 지문 수정 허용·공유 문항 연쇄 재검증 | v1 비허용 `[잠정]` | B+기획 | 07 §7 |
| — | refine 쿼터 차감 단위(백엔드 집행 설계) | 정적 차단 턴 미차감 권장 | BE | 07 §5 |
| — | ✅ T4·T5 생성 요청 계약 | `PassageRequest`는 reading 전용으로 유지하고 화법과작문·매체 형제 요청 모델과 생성 자료 게이트를 구현 | B | 05 §1.2 · 아래 「5영역 약점→출제 연결」 |
| — | T3 생존 작품 라이선스·코퍼스 확충 | 만료 작품 5편은 개방 완료. 생존 작품은 BE의 `license_ref`·버전 계약 뒤 추가하며 LLM 원문 생성은 금지 | BE+기획 | 05 §3 |
| — | 프론트 라벨 문구·대화 UI 형태 | §2-2 라벨 사전 기준 | FE+기획 | 06 §0 |
| Open-9 잔여 | 전국 백분위 출처(A 소관 — 참고) | — | BE+A | — |
| 7/22 감지 리뷰 | 기존 크로스체크 A 판정·구현 완료 · ongoing 상한 제외 후 요약 동기화·병합 lifecycle·회귀/데모 잔여 | 원본의 신규 `[PART_B 크로스체킹 요청]` 검토 후 A·BE 회신 | A+BE(+B 리뷰) | §1-6·§1-7·§1-8 |
| 7/22 API 리뷰 | `api/` 소유 ✅ · 실패 meta/검증/tracing 일부 반영 · VersionSet 양자 협의·LLM adapter·민감 detail·D-② 멱등·ongoing 응답 의미 잔여 | 원본의 `[PART_B 크로스체킹 요청]` 검토 후 공통 wire 확정 | A+B+BE | §1-7·§1-8·§2-11 |
| 7/22 B HTTP 경계 | 공통 헤더와 내부 command의 중복 | ✅ 외부 HTTP DTO와 내부 command 분리 확정 | B(+A·BE 편입 리뷰) | 05 §4.1 · 07 · §2-1·§2-11 |

---

## Windows 로컬 PR 검증의 Proactor 영구 실패 `[A 처리 요청 · 2026-08-12]`

### 소유권과 실측

`docs/02_ownership.md` §2는 `src/ai/evaluation/pre_pr_verify.py`를 박진희(A) 단독
소유로 지정한다. 실패 테스트는 A 소유 `import_mapping/`의 프로덕션 대칭 테스트이므로
같은 문서 §1·§4의 규칙에 따라 A 소유다. B는 두 파일을 수정하지 않았다.

Windows에서 `docker start checkon-ai-db-1` 후 정본 명령
`uv run python -m ai.evaluation.pre_pr_verify`를 실행했다. Ruff·mypy·offline pytest는
통과했지만 PostgreSQL integration 79건 중
`test_postgres_saver_checkpoint_survives_reopen` 한 건이 `psycopg.InterfaceError`로
실패했다. Windows 기본 `ProactorEventLoop`에서는 psycopg async 연결을 사용할 수 없다는
오류이며, 실제 uv 인터프리터는 `C:\verith\.venv\Scripts\python.exe`다.

### 판정과 최소 처방

선택지는 **ⓐ SelectorEventLoop에서 해당 테스트를 실행**하는 것이 맞다. 같은
`_checkpoint_scenario()`를 Python 3.12의
`asyncio.run(..., loop_factory=asyncio.SelectorEventLoop)`로 실행한 실측 결과는
`selector_checkpoint_outcome=ok`였다. 실제 PostgreSQL 저장·재개 검증을 그대로 수행하므로
검증 범위를 줄이지 않는다.

A에게 요청하는 최소 변경은
`tests/ai/integration/test_probe_pg_roundtrip.py`의 해당 테스트에서 Windows일 때만
`loop_factory=asyncio.SelectorEventLoop`를 넘기는 것이다. Linux에서는 지금과 같은 기본
이벤트 루프로 실제 테스트를 계속 실행한다. `pre_pr_verify.py`에 플랫폼별 skip을 넣거나
허용 skip 집합을 넓히지 않는다. 테스트가 자기 실행 전제만 명시하면 게이트는 플랫폼 중립인
채로 유지된다.

### A 변경 후 합격 기준

1. `docker start checkon-ai-db-1`
2. `uv run python -m ai.evaluation.pre_pr_verify`를 Windows에서 **3회 연속 exit 0**
3. integration skip은 기존 실 LLM 보호 3건만 유지
4. Linux에서도 동일 PostgreSQL 체크포인트 테스트가 skip 없이 계속 통과

현재는 소유권 규칙에 따라 처방만 기록한 상태이므로 3회 초록은 아직 달성되지 않았다.

---

## 5영역 약점 분류 → 출제 연결 `[B 구현 · 2026-08-11]`

### 착수 전 실측과 원인

`src/ai/diagnosis/data/curriculum_graph.yaml`은 `curriculum-grammar-v1`·33노드였고 전부
`area_tag=language`였다. `diagnoser.py`는 그래프에 없는 `skill_node_id`를 명시적으로
거절하므로, `SUPPORTED_AREAS`와 무관하게 자동 약점 목표가 language 밖으로 나갈 수 없었다.
reading·literature의 기존 출제 경로도 수동 목표로만 도달 가능했다.

### 그래프 확장 결과

- 그래프 버전: `curriculum-five-area-v1`
- 노드 수: language 기존 33개 유지 + reading 6 + literature 6 + speech_writing 6 +
  media 6 = 총 57개
- 출처 단계: 신규 24개 전부 ① `area_specs.yaml`의 영역별 `measures`를
  `taxonomy.md` §3 행동영역과 교차한 최소 축이다. 각 노드의 `source_stage=area_specs`와
  `source_refs`에 위치를 기록했다.
- ② 공개 고시·평가원 자료는 이번 최소 범위에 추가로 필요하지 않아 가져오지 않았다.
  ③ 근거 부족으로 지어낸 노드도 없다.
- 지위: 신규 4영역은 **교과 정본이 아닌 v1 초안이며 전문가 검수 대기**다. 그래프 meta의
  `review_status`·`scope_note`와 각 노드 `desc`에 같은 지위를 기록했다.
- 신규 간선: 0개. `area_specs.yaml`은 측정 대상과 발문 규격의 근거이지 선수 관계의 근거가
  아니므로, 근거 없는 `requires`·`builds_on`을 만들지 않았다. 전문가 검수 전에는 셀·직접
  근거로 `suspect`·`weak_confirmed`를 산출하고 역전파 후보는 만들지 않는다.

### 문학 풀 대조

**literature 노드 6개 중 현재 풀로 출제 가능한 것 6개다.** 고전시가 2편·현대시 1편·
현대소설 2편으로 표현·구성·화자·서술자·정서와 주제·외적 준거 축을 감당한다. 극·수필은
현재 풀에 갈래가 없어 노드로 만들지 않았다. 후속으로 해당 노드를 열려면 저작권 만료 또는
사용 승인된 극·수필 원문과 고정 리비전·해시가 먼저 필요하다.

### 출제 경로 개방과 검증

`speech_writing`은 발표·초고·수집 자료, `media`는 단일·쌍 자료의 형제 요청 모델을 쓴다.
독서 전용 `PassageRequest`의 `Literal[reading]`과 산문 파라미터는 바꾸지 않았다.
`_SOURCE_REQUEST_SHAPES` allow-list에 두 영역의 명시적 행을 추가했으며 조건식으로 바꾸지
않았다. 자료 생성은 `area_specs.yaml` 규격 블록을 프롬프트에 싣고 승인 evidence가 없거나
미승인 ref를 쓰면 문항 생성 전에 실패 닫힘한다.

실제 57노드 그래프와 결정론 진단기로 `speech_writing.writing.material` 및
`media.reception.credibility`를 `weak_confirmed`로 산출한 뒤, 그 노드가 자료 생성 → 문항
생성 → 규칙 게이트 → blind 교차 풀이 → 저장을 완주하는 FakeProvider E2E를 고정했다.
