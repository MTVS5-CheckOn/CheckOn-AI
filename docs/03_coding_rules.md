# 11. 코드 작업 규칙 — 사람도 에이전트도 동일 적용

> 리뷰어(사람·에이전트)는 이 문서를 기준으로 PR을 반려할 수 있다. 아래 규칙은 "취향"이 아니라 이 프로젝트의 구조적 요구(교체 가능성·재현성·감사 가능성)에서 나온 것이다.

---

## 1. 하드코딩 금지 — "값은 코드 밖에"

| 값의 종류 | 두는 곳 | 예 |
| --- | --- | --- |
| 감지 임계값 | DB `threshold_config` (버전 관리) | R1 drop 15%p를 코드에 쓰면 반려 — 과거 경보 재현이 깨진다 |
| 톤·완충 규칙 | `tone_map.yaml` · `buffer_lexicon.yaml` | 프롬프트 문자열 안에 규칙 서술 금지 |
| 마스킹 패턴 | `redaction_patterns.yaml` | |
| 프롬프트 | `llm/prompts/templates/` + `registry.yaml` (버전 필수) | f-string으로 코드 안에 조립 금지 |
| 환경 의존 값 (DB URL·토큰·백엔드 주소) | `.env` → `pydantic-settings` Settings 객체 | `os.environ` 직접 접근 금지 |
| enum (수능 영역·상태값) | `contracts/taxonomy.py` 등 계약 모듈 | 문자열 리터럴 산재 금지 — `"reading"`을 12곳에 쓰지 말고 enum 참조 |
| 매직 넘버 | 이름 있는 상수 (근거 주석 필수) | `RETRY_MAX = 3  # 블록 재생성 상한 — CLAUDE.md 불변식 6` |

**리트머스:** "이 값이 바뀌면 코드 diff가 생기는가?" — 정책 값인데 코드 diff가 생기면 위치가 틀린 것.

## 1b. 바퀴 재발명 금지 — 라이브러리 우선

**직접 구현하기 전에 찾는다.** 순서: ① 파이썬 표준 라이브러리 → ② 이미 설치된 의존성(pyproject 확인 — 있는 걸 두고 새로 깔지 않는다) → ③ 검증된 외부 라이브러리 → ④ 그래도 없을 때만 직접 구현(PR에 "찾아봤는데 없었다" 근거 명시).

**직접 구현 금지 목록 (이 프로젝트에서 이미 답이 정해진 것):**

| 문제 | 쓰는 것 | 직접 구현하면 반려 |
| --- | --- | --- |
| 재시도·백오프 | `tenacity` | while+sleep 루프 |
| 스키마 검증·직렬화 | `pydantic` | 수제 validate 함수 |
| 설정 로딩 | `pydantic-settings` | os.environ 파싱 |
| HTTP 클라이언트 | `httpx` | urllib 수제 래퍼 |
| DB 마이그레이션 | `alembic` | 수제 DDL 스크립트 |
| 엑셀 파싱 | `openpyxl`/`pandas` | zip+xml 직접 파싱 |
| 에이전트 상태·체크포인트 | `langgraph` + PostgresSaver | 수제 상태 머신 |
| 날짜·시간대 | `zoneinfo`(표준) — KST 처리 | 수동 +9h 연산 |
| 해시·멱등키 | `hashlib` + canonical json | 수제 문자열 조합 |
| ID 생성 | `uuid`(표준) | 타임스탬프 조합 ID |

**새 라이브러리 추가 기준 (PR에 아래 3줄 답변 첨부):**
1. 유지보수 — 최근 1년 내 릴리스, 이슈 응답 존재
2. 무게 — 기능 하나 쓰자고 무거운 프레임워크 금지(트랜지티브 의존 확인). 10줄이면 되는 걸 라이브러리로 대체하지도 않는다 — 반대 방향의 과잉도 금지
3. 라이선스 — MIT/BSD/Apache 계열. GPL 계열은 팀 논의

**추가 절차:** `uv add`(dev는 `--dev`) → `uv.lock` 커밋 포함 → PR 본문에 선정 사유. **예외 경로:** 개인정보를 다루는 코드(masking·redaction)에 데이터를 외부로 보내는 라이브러리·SaaS SDK 금지 — 로컬 연산만. LLM 벤더 SDK는 **B-5 확정(7/23)으로 설치 해제** — 단 `openai` SDK는 **`llm/providers/` 안에서만** import 허용(capability·contracts에서 직접 import 0건 = grep 게이트, 벤더 독립 유지).

## 2. 모듈화 — 스파게티 방지의 구조 규칙

- **의존 방향은 한쪽으로만:** `capability → contracts ← capability`. capability끼리 직접 import가 보이면 즉시 반려. 플랫폼 모듈(evidence·gates·llm·runtime)은 contracts에만 의존.
- **모든 LLM 호출은 `llm/gateway` 경유 — 예외 없음.** provider(벤더 어댑터) 직접 호출 금지(`llm/` 내부 제외). 직결하면 role 라우팅·원가 기록·redaction 훅이 소비자마다 갈라진다. **과도기:** 기존 브리핑 어댑터 직결(#22)은 gateway 이관 PR(`09_integration_proposals.md §1-10`의 ①+② 머지)로 종료 — 그때까지 **신규 직결 추가 금지**. 근거: `part_b/01_pipeline.md §5`(원 규칙 — B 소유, 참조만) · `part_b/09_integration_proposals.md §1-10`(A·B 공용 승격 합의).
- **1 파일 = 1 책임.** `features.py`가 베이스라인 계산까지 하기 시작하면 분리. 기준: 파일 설명을 "~와 ~를 한다"로 써야 하면 이미 두 개다.
- **함수는 한 화면(≤40줄) 안에.** 넘으면 단계별 함수로 추출 — 파이프라인 단계(수집→계산→판정→저장)가 함수 이름으로 읽혀야 한다.
- **순환 import는 설계 오류의 증상** — import 트릭으로 우회하지 말고 공용 타입을 contracts로 올려서 해소.
- **부작용 격리:** 계산(순수 함수)과 I/O(DB·HTTP·LLM)를 같은 함수에 섞지 않는다. 규칙 판정 함수는 스냅숏을 받아 결과를 반환할 뿐, DB를 만지지 않는다 — 이게 골든셋 테스트가 가능한 이유다.

## 3. 인터페이스 우선 — 교체 가능성

- 외부 의존(LLM·백엔드·시계)은 **인터페이스(Protocol/ABC) + 주입**으로. `DetectionEngine`(규칙→GRU 교체)·`LLMProvider`(FakeProvider)·evidence resolver가 이미 이 패턴 — 새 외부 의존도 동일하게.
- `datetime.now()` 직접 호출 금지 — `clock` 주입(테스트에서 시간 고정 필요: 주간 배치·자정 리셋 로직).
- 랜덤이 필요하면 seed 주입 — "동일 입력 = 동일 출력" 불변식.

## 4. 타입·데이터 규칙

- 전 함수 타입 힌트, `mypy` 통과 필수. `Any`는 사유 주석 없이 금지.
- 경계를 넘는 데이터(요청·응답·LLM 구조화 출력·state)는 전부 **Pydantic 모델** — dict로 돌려막기 금지. `dict[str, Any]`가 3단계 이상 전파되면 모델 누락 신호.
- 상태값은 `Literal`/`StrEnum` — 자유 문자열 상태 금지.
- LLM 출력은 `llm/structured.py` 파서를 거친 모델만 신뢰 — 원문 문자열을 직접 파싱하는 코드 금지.

## 5. 에러 처리

- `except Exception: pass` **절대 금지.** 도메인 예외(`runtime/errors.py`)로 변환하거나 전파.
- 게이트 거부는 예외가 아니라 **반환값(상태)** — 흐름 제어에 예외 사용 금지.
- 실패를 기본값으로 덮지 않는다: 마스킹 불확실 → 전송 중단(fail-closed), 판독 불가 → `unreadable`, 재시도 소진 → 섹션 비움+사유. "일단 진행"이 가장 위험한 코드다.
- 로그에 원문 텍스트 금지 — redaction 통과분만 (`LLM_PAYLOAD` 규칙과 동일).

## 6. 함수·이름 규칙

- 이름은 도메인 용어 그대로: `calc_normalized_time_ratio`, `apply_alert_cap`, `freeze_label_snapshot` — 문서(유스케이스·정책서)에 나오는 단어와 코드 식별자가 1:1이어야 리뷰가 빨라진다.
- bool 반환은 `is_/has_/should_`, 게이트는 `check_` 접두.
- 축약어 금지(`ctx`, `mgr`, `tmp` 등) — 예외: `id`, `db`, `llm`.

## 7. 테스트 규칙 (평가 계획서와 별개인 코드 레벨)

- **테스트 없는 로직 PR 금지.** 최소: 정상 1 + 경계 1 + 실패 1.
- 테스트가 어렵다면 코드 구조 문제(부작용 미격리·의존 미주입) — 테스트를 우회하지 말고 구조를 고친다.
- 실 LLM·실 DB 호출 테스트는 `integration/` 마커로 분리 — 기본 `pytest`는 오프라인으로 전부 통과해야 한다.
- 골든셋 기대값 수정이 포함된 PR은 제목에 `[golden]` — 리뷰어가 반드시 diff 사유를 확인.

## 8. PR·커밋 규칙

- PR은 **한 가지 일만** — "R4 구현 + 리팩토링 + 오타 수정" 금지, 3개로 쪼갠다. 목표 크기 ≤ 400줄(생성 파일 제외).
- 커밋 메시지: `[모듈] 요약` (한국어 OK) — 예: `[detection] R4 어절 정규화 시간 비율 계산 추가`.
- 미확정 안건에 걸린 코드는 `# TODO(Open-11):` 형식 — 안건 번호 없는 TODO 금지(추적 불가).
- `docs/02_ownership.md` §4의 양자 승인 12곳·상대 capability 파일을 건드리는 PR은 본문 첫 줄에 `⚠ 승인 필요: [파일명]` 명시.
- CI(ruff·mypy·pytest·redaction 코퍼스) 빨간 상태로 리뷰 요청 금지.

## 9. 에이전트(Claude/Codex) 추가 수칙

- 작업 전 해당 문서(CLAUDE.md §7 맵)를 읽고, PR 본문에 **참조한 문서·준수한 불변식 번호**를 명시한다.
- 기존 파일의 컨벤션을 따른다 — 새 스타일 도입은 별도 PR로 제안.
- "동작하게 만들기 위해" 규칙을 우회하는 코드(게이트 스킵 플래그, 마스킹 off 옵션, 테스트 skip)는 절대 생성하지 않는다.
- 확신이 없으면 구현하지 말고 PR 본문에 질문을 남긴다 — 추측 구현이 가장 비싼 낭비다.
