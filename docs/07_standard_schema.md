# 07. 체크온 표준 스키마 정의서 v0 — 3자 일치의 원본 (BE-7)

> **7/15 합의된 원칙:** ① F1 업로드 템플릿 ② 스마트 이전(Import)의 변환 목적지 ③ 야간 스냅숏 필드 — **이 셋은 같은 스키마의 세 얼굴이다.** 여기서 정의한 표준 필드가 유일한 원본이고, 셋 중 하나를 바꾸려면 이 문서를 먼저 바꾼다(양측 승인).
> 작성 A · 리뷰 백엔드. 타겟: **수능 대비 고등 국어**(7/15 협소화 — 중등 필드 없음).

---

## 1. 세 얼굴의 관계 — 어디까지 실명이고 어디부터 alias인가

```
① 업로드 템플릿 (강사가 작성 — 실명 포함)     ┐
② Import 변환 산출물 (타사 엑셀 → 표준화)     ┼─→ 백엔드 DB (원본·실명 — vault)
                                              ┘        │ 실명 → alias 치환 (BE-2 확정)
                                                       ▼
③ AI 스냅숏 (alias only — 04_api_contract §4.1)   AI는 여기부터만 본다
```

- 실명·연락처는 ①②에만 존재하고 백엔드에서 끝난다. ③에는 **필드 자체가 없다**(7/15 BE-2 확정).
- Import 산출물(②)은 스토리지 URL로 백엔드에 전달되고(Open-3), 백엔드가 기존 F1 경로로 반영한다(BE-9).

## 2. 학생(명부) 표준 필드

| 표준 필드 | 타입 | 필수 | ①템플릿 컬럼(한글) | ③스냅숏 | 비고 |
| --- | --- | --- | --- | --- | --- |
| `student_name` | string | ✅ | 이름 | **없음** — `student_ref`(alias)로 치환 | 백엔드 vault 전유 |
| `grade` | enum `H1·H2·H3` | ✅ | 학년 | (피처에 간접 반영) | 고등만 — 중등 값 없음(7/15) |
| `class_name` | string | ✅ | 반 | `class_ref`(alias) | |
| `enrolled_at` | date | ✅ | 등원일 | `enrolled_weeks`(환산) | 관찰 중 판정·15일 규칙의 원천 |
| `status` | enum `enrolled·paused·returned` | ✅ | 재원 상태 | 동일 | 기본값 enrolled |
| `guardian_name` / `guardian_phone` | string | ✅/✅ | 보호자명 / 연락처 | **없음** — `guardian_ref` | 백엔드 전유 |
| `consent` | enum `granted·pending·revoked` | ✅ | 개인정보 동의 | 동일 | granted 외 이벤트 폐기 |
| `subject_track` | enum `common·speech_writing·language_media` | ⬜ | 선택과목 | 동일 | 미정 학생은 common |

## 3. 학습 기록(learning_event) 표준 필드 — 스냅숏 §4.1과 1:1

| 표준 필드 | 타입 | 필수 | ①템플릿 컬럼 | 비고 |
| --- | --- | --- | --- | --- |
| `occurred_at` | datetime | ✅ | 날짜 | 시간 없으면 그날 23:59로 정규화 `[제안]` |
| `event_type` | enum `solve·submit·attend·consult` | ✅ | 구분 | 템플릿 기본값 solve |
| `assignment_title` | string | ✅(solve·submit) | 과제/시험명 | **태깅 제안 입력(Open-4b 확정)** |
| `area_tag` | enum 6영역 | ⬜ | 영역 | 비우면 태깅 제안이 채움(강사 확정 후 반영) |
| `type_tag` | enum `fact·infer·critic·concept` | ⬜ | 유형 | 〃 |
| `item_format` | enum — **v1 값은 `mcq`뿐** | ⬜ | (템플릿 비노출 `[제안]`) | 7/15: 컬럼은 예약하되 v1 템플릿에선 숨김 — 전부 mcq 기본값 |
| `correct` | bool | solve만 | 정오 | O/X·1/0·맞음/틀림 허용(변환기가 정규화) |
| `score` / `max_score` | number | ⬜ | 점수/만점 | 주간 테스트용 |
| `duration_sec` | int | ⬜ | 풀이시간(초) | 없으면 R4 미적용 — 템플릿에 "권장" 표시 |
| `passage_word_count` | int | ⬜ | 지문 어절수 | R4 어절 정규화 핵심 — 문제 생성분은 자동 산출 |
| `passage_ref` | string | ⬜ | 지문/자료 묶음 ID | **기대치 층 입력**(05 [A 확정 통보 8/3]). 같은 지문이면 같은 값이기만 하면 되므로 **강사가 적는 자유 문자열도 된다**(예: `6월모의_비문학3`). 비우면 전체 평균 폴백 — **보정 없음과 동치라 안전**하다 |
| `source` | enum `trackA·trackB·studentHome` (+`paper_scan` 예약) | ✅ | (자동) | 입력 경로가 자동 기록 |

## 4. Import(②) 변환 규칙 — MappingSpec의 목적지가 바로 위 표

- 타사 컬럼 → **표준 필드명**으로만 매핑(자유 필드명 금지). 매핑 불가 컬럼은 `unmapped`(정직 명시).
- 개인정보성 컬럼(연락처·주소 류)은 매핑 대상에서 자동 제외 — "자동 이전 대상 아님" 사유 고정.
- 값 정규화는 결정론 변환기 소유: 날짜 형식 통일, 정오 표기 통일, 학년 표기(`고1`→`H1`) 등 — 변환 규칙표는 `import_mapping/transformer` 구현과 함께 확정.

## 5. 버전·변경 규칙

- 이 문서 = `schema_version: std-1`. 필드 추가는 마이너(양측 통보), 삭제·의미 변경은 메이저(양측 승인) — 계약 §5 하위호환 규칙과 동일.
- short·essay·paper_scan·중등 학년은 **예약값** — v1 코드에서 분기 금지(CLAUDE.md §3), 스키마에만 존재.

## 6. 백엔드 리뷰 체크리스트 (이것만 답하면 확정)

- [ ] §2·§3 필드에 백엔드 DB 기존 컬럼과 충돌·누락 없는가
- [ ] F1 업로드 템플릿(xlsx)을 이 표대로 백엔드가 생성 가능한가 (`item_format` 숨김 포함)
- [ ] `enrolled_at`→`enrolled_weeks` 환산 주체 = 백엔드 맞는가
- [ ] 학년 enum H1~H3 표기 합의
