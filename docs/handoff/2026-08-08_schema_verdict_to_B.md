# `PROBLEM_ITEM` 스키마 결손 — A 판정 (09 §2-20 결정 ①②③ + ㉕)

**2026-08-08**(`date` 확인) · A(박진희) → B(염준영)
대상: `docs/part_b/09_integration_proposals.md` §2-20 `[제안 · A 결정 대기]`

> 🔴 **판정 문서다. `db/models.py`는 열지 않았다**(양자 · 읽기만) — 구현은 B가 같은 모델
> 변경 PR에서 한다(§2-20.3 규약 그대로).
> ⚠ **결손 12건 표는 §2-20이 정본이다** — 여기 복제하지 않는다. 판정만 적고 가리킨다.

---

## 0. 대조 결과 — **12건 전부 실측 일치. 다른 것 0건**

🔴 **표를 인용으로 받지 않고 네 소스를 직접 열어 대조했다**(행번호가 아니라 심볼로 — 행번호는
머지 한 번에 밀린다):

```
계약 값 객체   problem_generation/domain/models.py    StoredProblemItem  (6 필드)
Protocol      problem_generation/application/ports.py ProblemItemStore.save/get
결과 계약      contracts/problem_generation.py         ItemResult(9) · GeneratedItem(9)
현행 ORM      db/models.py                            ProblemItem       (18 컬럼 · 읽기만)
```

대표 확인 몇 개:

| # | 계약 | 현행 ORM | |
| --- | --- | --- | --- |
| 1 | `slot_index` **필수** | **컬럼 없음** | ✅ |
| 3 | `item` **옵셔널** | `stem`·`choices`·`answer`·`rationale` **전부 NOT NULL** | ✅ |
| 4 | `difficulty_est` 옵셔널 | **NOT NULL** | ✅ |
| 10 | `review_reason` 옵셔널(enum) | `review_badge` **boolean NOT NULL** | ✅ |
| 12 | `failure_reason` 옵셔널 | `drop_reason` nullable(**폐기 사유 전용**) | ✅ |

**⇒ 판정의 전제가 흔들리지 않는다.** 승인으로 넘어간다.

---

## 1. 결정 ① — 조회 키 · **승인 · 선택지 없음**

> ✅ **`problem_item`에 `slot_index` 추가 + `UNIQUE(set_id, slot_index)`.**

**왜 선택지가 없나:** `ProblemItemStore.get(set_id, slot_index)`가 **Protocol 시그니처로**
그 키를 요구한다(`ports.py`). 컬럼이 없으면 **구현 자체가 불가능**하다 — 설계 취향이 아니다.

### 🔴 `UNIQUE`에 `tenant_id`가 빠진 것 — 근거를 적는다

`item_candidate`는 `UNIQUE(tenant_id, set_id, slot_index, attempt_no)`인데 여기는 셋이다.
**중복이 아니라 정상**이고 이유는:

- `problem_item.set_id` → `problem_set.id` **FK**이고 `problem_set.tenant_id`가 있다 ⇒
  **`set_id`가 이미 테넌트에 종속**이다. `(set_id, slot_index)`만으로 전역 유일하다.
- ⚠ **그럼 `item_candidate`는 왜 넣었나** — 거긴 `tenant_id`가 **직접 컬럼**으로도 있어서
  복합 인덱스가 그 컬럼을 태우는 편이 조회에 낫다. **같은 이유가 여기엔 없다**(직접 컬럼이 없다).

🔴 **이 문단을 적는 이유:** 안 적으면 다음 사람이 *"왜 여긴 `tenant_id`가 없지"* 를 **다시
묻는다.** 두 테이블이 다른 것이 실수처럼 보인다.

---

## 2. 결정 ② — 무손실 보존 · 🔴 **B안 승인 · 단 조건 셋**

> ✅ **B안(전체 스냅숏 1컬럼 + 파생 투영). 다만 아래 조건 셋을 만족하는 형태로만.**

### 🔴 먼저 — 근거로 드신 선례를 쟀습니다

```
ItemCandidate   정의        db/models.py     snapshot JSONB + gate_summary JSONB + difficulty_est(파생)
                마이그레이션  0003_problem_generation_schema.py  ← 🔴 **있다. 테이블은 실제로 생성된다**
                테스트       tests/ai/db/test_erd_model_parity.py (UNIQUE 제약 대조)
                리포지토리    db/repositories/ 에 **없음**
                🔴 프로덕션 소비                                    **0건**
```

⚠ **A 지시서가 「마이그레이션 없음」이라 적었는데 그건 A의 오측입니다** — 저장소 루트에서
`migrations/`·`alembic/`를 찾고 실제 위치인 `src/ai/db/migrations/versions/`를 못 봤습니다.
`0003`이 실제로 만듭니다. 🔴 **§2-20은 그런 말을 한 적이 없습니다**(전수 확인: §2-20에서
「마이그레이션」은 §2-20.3의 *"모델 diff의 기계적 산출물"* 한 줄뿐). **소비 0인 것만 맞습니다.**

🔴 **그래서 이 선례는 「형태 선례」이지 「유지 증명」이 아닙니다.**
「같은 형태가 이미 있다」는 **참**이고, 「A안이 결손 12건을 낳은 형태 그 자체」라는 지적도
**참**입니다. 다만 **「그 형태가 실제로 갈리지 않고 유지된다」의 증명은 아닙니다** —
**아무도 안 쓰니 갈릴 기회가 없었을 뿐**입니다. (99 #22로 등재했습니다. ㊺ 부류 **넷째** —
`snapshot_hash` · `fallback_text` · `LexiconLookup` 다음입니다.)

⚠ **스냅숏 + 파생 투영은 정의상 「같은 값이 두 곳에」인 구조**입니다. 이 저장소가 이번 주에
잡은 형태가 정확히 그것입니다(#02 사례 다섯 · `redaction_blocked` · `pipeline_version`).
**그래서 조건 없이 승인하지 않습니다.**

### 🔴 승인 조건 셋 — 이 셋을 만족하는 구현만 승인입니다

| | 조건 | 왜 |
| --- | --- | --- |
| **ⓐ** | **파생 투영은 스냅숏에서 유도한다.** 저장 시 **한 함수**가 스냅숏을 받아 파생 컬럼을 채운다 — 호출자가 스냅숏과 파생값을 **따로 넘기지 않는다** | 🔴 후자면 **갈린다.** 두 인자를 받는 순간 호출부마다 다른 값을 넣을 수 있다 |
| **ⓑ** | **갈렸을 때 red가 난다**(99 #04) — 왕복 테스트가 **「스냅숏에서 유도한 값 == 파생 컬럼」**을 단정한다 | ⓐ만으로는 다음 사람이 함수를 우회할 수 있다. **경계를 말했으면 그 자리에 red를 남긴다** |
| **ⓒ** | **어느 쪽이 정본인지가 코드 옆에 적힌다** — *"스냅숏이 정본, 파생 컬럼은 조회용"* 이 ORM 주석으로 선다 | 로그 89 — **근거가 테스트·PR에만 있으면 그 코드를 읽는 사람에게 안 보인다** |

⚠ **「하면 좋다」가 아니라 승인의 조건입니다** — 조건 없는 승인은 조건 없는 구현을 낳고
그때 A가 할 말이 없습니다.

### A안을 왜 안 고르나 — 그리고 A안의 장점

🔴 **양자 파일을 여는 비용.** A안(결손 12건을 컬럼으로 전개)은 **계약이 늘 때마다 컬럼을
더하고 그때마다 양자 승인**입니다. `contracts/problem_generation.py`는 B 소유인데
`db/models.py`는 양자라 **B가 자기 계약을 늘릴 때마다 A를 기다려야 합니다.** B안은 스냅숏이
먹으므로 그 왕복이 사라집니다.

⚠ **A안의 장점도 적습니다 — JSONB는 인덱싱·쿼리가 약합니다.** 파생 투영을 남기는 것이 그
답이고, 그래서 **어느 컬럼을 투영으로 남길지가 판정에 듭니다.** B는 `difficulty_est` 선례만
드셨는데, **조회·정렬에 실제로 쓰이는 축**을 기준으로 고르시면 됩니다. A가 보기에 후보는:

```
status            목록 화면이 상태로 거른다 (이미 컬럼으로 있다)
difficulty_est    난이도 정렬·분포 (B 선례)
review_badge      검토 필요 문항만 뽑기 (이미 있다)
slot_index        결정 ①로 들어온다
```

⚠ **`failure_reason`·`review_reason`·`difficulty_band`를 투영으로 낼지는 B 판단입니다** —
집계 화면이 그 축으로 거른다면 컬럼, 아니면 스냅숏 안에 두면 됩니다. **A는 「투영을 늘리려면
그때마다 양자」라는 사실만 알려 드립니다.**

---

## 3. 결정 ③ — 본문 없는 슬롯 · **ⓒ 승인 · 🔴 다만 「자동」입니다**

> ✅ **ⓒ(② B안과 결합 · 본문 파생 컬럼 nullable).**

🔴 **독립 결정이 아닙니다 — ②가 B안이면 ⓒ가 자동입니다.** 스냅숏이 정본이면 본문 컬럼은
투영이고, 투영은 원본이 없을 때 `NULL`이 되는 것이 정의입니다. **셋을 따로 뒤집을 수 있는
것처럼 적으면 다음 사람이 ②는 B안인데 ③은 ⓐ 같은 조합을 시도합니다.**

**ⓑ(행을 안 만든다)가 후보가 아닌 것도 확인했습니다** — `ProblemItemStore.get(set_id,
slot_index)`가 **`StoredProblemItem`을 반환하도록 선언**돼 있어(옵셔널이 아니다) 행이 없으면
**Protocol을 지킬 수 없습니다.** `StoredProblemItem.item`이 nullable인 것이 *"슬롯은 있고
본문은 없다"* 를 표현하는 자리입니다.

---

## 4. 🔴 B가 안 물은 것 넷 — 승인의 값은 여기서 납니다

### ⓐ 개인정보 수명주기 — **파기 경로가 아직 없습니다**

전수 확인 결과 **`src/ai`에 파기·보존기간 구현이 0건**입니다(`purge`·`retention_days`·
삭제 리포지토리 전부 없음. `lease_expires_at`은 워커 lease이지 데이터 파기가 아닙니다).

⇒ 🔴 **「스냅숏이 삭제 사각지대가 된다」가 아니라 「삭제 경로 자체가 아직 없다」**입니다.
**지금 B안이 새 위험을 만들지는 않습니다.**

⚠ **다만 파기를 만들 때 스냅숏 컬럼을 반드시 대상에 넣어야 합니다** — 그때 `item_candidate`·
`problem_item` **두 스냅숏**이 대상입니다. **A가 파기 경로를 설계할 때 이 문서를 봅니다.**
⚠ **문항 본문에 학생 관련 정보가 드는가** — `GeneratedItem`(area·type·stem·choices·answer·
rationale·evidence)에 학생 식별자가 들 자리는 없습니다. `evidence`가 지문·출처 참조라면
개인정보가 아닙니다. **B가 그 필드에 무엇을 담는지가 판정 재료**이니 다르면 알려 주십시오.

### ⓑ `06_erd.md` 갱신 순서 — 🔴 **이 PR에서 안 고쳤습니다**

ERD는 A 소유지만 **지금 고치면 문서가 코드보다 앞섭니다**(99 ㊩ — *"미구현 계약은 약속한
동작과 현재 동작을 함께 적는다"*). **B 구현 PR에서 같이 가는 것이 맞습니다.**

🔴 **B가 ERD를 기다리지 마십시오** — 모델 변경 PR에 `06_erd.md`를 같이 넣으시면 됩니다.
ERD가 A 소유라도 **그 PR에서 A가 리뷰하면 되고**, 순서를 뒤집으면 문서가 거짓인 기간이
생깁니다.

### ⓒ 🔴 대조 테스트 파급 — **쌍이 최대 셋 늘 수 있습니다**

PR-β에서 `06_erd.md` 값목록 쌍 **16개**를 잠갔습니다(44개 중). 새 컬럼에 enum 값 목록이
붙으면 쌍 후보입니다:

| 새 컬럼 후보 | enum | 쌍이 되나 |
| --- | --- | --- |
| `failure_reason` | `ProblemFailureReason` | 투영 컬럼으로 내면 **쌍** |
| `difficulty_band` | `DifficultyBand` | 〃 |
| `review_reason` | `ReviewReason` | 〃 (`review_badge` boolean을 대체하면) |

⇒ **투영으로 내는 것만 쌍이 됩니다.** 스냅숏 안에만 두면 대조 대상이 아닙니다.
🔴 **ERD를 고치실 때 `tests/ai/contract/test_doc_enum_parity.py`의 `_ERD_PAIRS`에 같이
넣어 주십시오** — 안 넣으면 값목록이 또 갈립니다(PR-β가 잡은 것이 정확히 그 형태입니다).
⚠ 표기는 ` — ` 꼬리 규약을 따라 주십시오(값 목록 뒤 설명은 그 구분자로 자릅니다).

### ⓓ 마이그레이션 경계 — **A가 더 승인할 것은 없습니다**

`02_ownership.md` §4-1이 *"마이그레이션 파일은 양자 목록에 넣지 않는다 — `db/models.py`의
기계적 산출물이므로 모델 diff가 포함된 PR에서 함께 리뷰되면 충분하다"* 로 이미 정해 뒀습니다.
⇒ **모델 변경 PR 하나에 마이그레이션이 같이 들어오면 그 PR의 양자 승인으로 끝납니다.**

### ⚠ 넷 말고 더 있나 — **없습니다**

`__table_args__` 제약·인덱스·FK 방향·`current_revision_no`와 `item_revision`의 관계를 봤고
이번 변경이 건드리는 자리가 아닙니다. **「안 본 것」이 아니라 「봤고 없다」입니다.**

---

## 5. ㉕ — `CounselPackResultRecord`의 자리 · 🔴 **같은 답이 아닙니다**

```
CounselPackResultRecord   id · tenant_id · class_ref · summary · results(StudentResult…) · created_at
                          + plan_outcome · plan_dropped  (8/8 · #122 · ㉲)
대응 ERD 테이블            06_erd.md · db/models.py 전수 → 0건
```

**§2-20과 같은 형태**입니다(계약 객체에 앉을 테이블이 없다). 그런데 🔴 **처방은 다릅니다:**

| | `PROBLEM_ITEM` | `CounselPackResultRecord` |
| --- | --- | --- |
| 행 수 | 슬롯당 1행 — **N개** | 🔴 **팩당 1행** |
| 조회 키 | `(set_id, slot_index)` — Protocol이 요구 | `result_ref` 문자열 하나 |
| `results` | 없음(슬롯이 곧 행) | **`tuple[StudentResult, ...]`가 안에 든다** — 그런데 counsel은 **N=1 축퇴**라 실질 1건 |

⇒ **`PROBLEM_ITEM`은 「행이 여러 개고 조회 키가 필요」**해서 컬럼(`slot_index`·UNIQUE)이
필수인데, **`CounselPackResultRecord`는 「팩당 1행 + 포인터 조회」**라 **스냅숏 1컬럼이면
끝입니다.** 파생 투영도 필요 없습니다(`summary`는 표시용이고 정렬·필터 축이 아닙니다).

🔴 **억지로 같은 모양으로 맞추지 않았습니다** — 근거는 위 셋입니다.

⚠ **㉕는 이 판정만으로 안 닫힙니다** — 테이블 신설이 `db/models.py`(양자)라 **모델 PR이
필요**합니다. 🔴 **B의 pg 모델 PR에 같이 얹으시면 `db/models.py`를 한 번만 엽니다** —
따로 가면 양자 파일을 두 번 여는 것이라 A가 두 번 승인해야 합니다. **얹으실지 정해 주십시오**
(A가 따로 내도 됩니다 — 다만 그게 두 번입니다).

---

## 6. B가 다음에 할 일

> **모델 변경 PR 하나**에 ① `slot_index` + `UNIQUE(set_id, slot_index)` · ② 스냅숏 컬럼
> (조건 ⓐⓑⓒ 포함) · ③ 본문 컬럼 nullable · 마이그레이션 · `06_erd.md` · `_ERD_PAIRS`(투영을
> 낸 경우)를 **같이** 넣으시면 됩니다. **A는 그 PR에서 양자 승인합니다.**
> ⚠ ㉕(counsel 팩 테이블)를 얹으실지만 알려 주십시오.
