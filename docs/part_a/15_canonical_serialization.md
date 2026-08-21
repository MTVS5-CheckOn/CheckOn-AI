# 15 · `snapshot_hash` canonical 직렬화 규칙 — **대조 계약**

> **소유:** member-A · **상대:** 백엔드(승우님) · **최초 합의 2026-08-20 · 정정 2026-08-21**
> 🔴 **이 문서가 심판이다.** 어느 쪽 구현도 아니다.

---

## 0. 지위 — 누가 만들고 누가 대조하나

`src/ai/detection/canonical.py` 첫 줄이 이렇게 적는다:

> *"🔴 **해시를 만드는 주체는 백엔드다.** 이 모듈은 「무엇을 해시 대상에 넣는가」를 코드로 못 박아 **Java 구현과 대조할 수 있게** 하는 참조다 — **AI 가 요청 해시를 재계산해서 검증하지 않는다**(내부 백엔드 전용 신뢰 · 04 §2.2)."*

```
만드는 쪽   🔴 백엔드 (Java)
AI 쪽       🔴 **대조용 참조 구현** — 정답이 아니라 「같은지 볼 수 있는 두 번째 눈」
심판        🔴 **이 문서**
```

⚠ 🔴 **이 문서가 저장소 밖에 있던 동안 §2-3 이 틀린 채로 살아 있었다**(2026-08-21 발견 · 99 #167). 그래서 저장소로 들였고, §7 의 가드가 코드와의 갈림을 잡는다.

---

## 1. 해시 대상 — payload 구성

**[읽음 `detection/canonical.py::canonical_snapshot_payload`]**

```jsonc
{
  "snapshot_meta":      { "week_start": <str>, "term_context": <str> },
  "students":           [ ... ],   // student_ref 오름차순
  "learning_events":    [ ... ],   // record_id 오름차순 · 🔴 passage_ref 제외
  "alert_context":      [ ... ],   // (student_ref, signal_type) 오름차순
  "detection_evidence": [ ... ]    // 🔴 비어 있으면 **키 자체가 없다**
}
```

### 1-1. 대상이 **아닌** 것

| | 왜 |
|---|---|
| `snapshot_meta.snapshot_hash` | 자기 참조 |
| `snapshot_meta.classes` | 04 부록 A 가 `week_start`·`term_context` 만 든다 |
| 🔴 `learning_events[].passage_ref` | §5 |

### 1-2. 🔴 배열은 **정렬해서** 넣는다

```
students            student_ref
learning_events     record_id
alert_context       (student_ref, signal_type)
detection_evidence  🔴 (kind, student_ref, at, source_table, record_id)  ← 5튜플
                       전부 **문자열로 변환한 뒤** 비교
```

⚠ 🔴 **JSON 직렬화가 아니라 payload 구성 단계다.** `sort_keys` 는 **객체의 키**만 정렬하고 **배열 원소**는 안 건드린다. Java 도 같은 키·같은 방향으로 정렬해야 한다.

⚠ `detection_evidence` 의 `at` 은 kind 에 따라 다르다 — 집계는 `week_start`, 전환은 `occurred_at` **[읽음 `canonical.py::_evidence_at`]**.

---

## 2. 직렬화 여덟 줄

**[읽음 `canonical.py::canonical_snapshot_hash`]**

```python
json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
→ .encode("utf-8") → sha256 → "sha256:" + hexdigest
```

| # | 규칙 | 값 | 상태 |
|---|---|---|---|
| ① | **키 정렬** | 🔴 **사전순**(`sort_keys=True`) · 중첩 포함 · 배열은 §1-2 로 별도 | ✅ 합의 |
| ② | **비ASCII** | 🔴 **원문 UTF-8**(`ensure_ascii=False`) — 한글이 `\uXXXX` 가 **아니다** | ✅ 합의 |
| ③ | **구분자 공백** | 🔴 **없음** — `","` · `":"` | ✅ 합의 |
| ④ | **시간 표현** | §2-1 | 🔴 **㉠ 미결 · ㉡ 닫힘** |
| ⑤ | **수 표현** | 정수만 · 후행 `.0` 금지 · 지수 금지 · `-0`→`0` | ✅ 합의 |
| ⑥ | **null 과 키 부재** | 🔴 **null 키를 유지한다** — §2-3 | ✅ 합의 (🔴 범위 정정 2026-08-21) |
| ⑦ | **빈 배열·객체** | 유지. 단 `detection_evidence` 만 특례 — §2-4 | ✅ 합의 |
| ⑧ | **이스케이프** | Python `json.dumps` 기본 — §2-5 | ✅ 합의 |
| — | **접두** | `"sha256:" + hex(소문자 64자)` | ✅ 합의 |

### 2-1. ④ 시간

**실측(2026-08-21 · 벡터 `v02_time_utc`) — 저희 payload 안에서 두 표기가 같이 나온다:**

```
"at":"2026-08-10T00:00:00+00:00"    ← detection_evidence[]  경로 A  .isoformat()
"resolved_at":"2026-08-05T00:00:00Z" ← 그 밖 전부           경로 B  pydantic model_dump
                                      🔴 UTC 일 때만 갈린다 — +09:00 이면 두 경로가 같다
```

#### ㉠ UTC 표기 — 🔴 **미결. 권고는 `Z`**

```
백엔드   이미 전부 "Z"
AI       경로 B 는 이미 "Z" · 🔴 경로 A(_evidence_at)만 "+00:00"
```

⇒ **`Z` 로 정하면 고칠 곳이 AI 의 `_evidence_at` 한 줄이다.** `+00:00` 으로 정하면 백엔드 포매터 전체가 바뀐다. **적게 고치는 쪽이 틀릴 여지도 적다.**

**확정되면 쓸 문면(미리 적어 둔다):**

```
🔴 UTC 는 "Z" 로 쓴다.
⚠ 명시 오프셋은 보존한다 — +09:00 은 +09:00 그대로다(정규화하지 않는다).
```

⚠ 🔴 **AI 쪽은 아직 안 고쳤다.** 고치면 표기를 일방적으로 정하는 것이 되고, 그건 상대가 피하고 싶다고 한 형태다. `tests/ai/contract/test_canonical_vectors.py::test_v02_shows_two_utc_shapes_in_one_payload` 가 *"지금은 이렇게 갈린다"* 를 단언하고 있어 **표기를 정하면 그 검사가 먼저 red 가 된다.**

#### ㉡ 소수 초 — ✅ **닫혔다 (2026-08-21)**

```
백엔드   0 이면 안 찍고, 있으면 **6자리 고정**
AI       6자리 고정 (후행 0 보존)
⇒ 🔴 양쪽이 이미 같다. 규칙: **6자리 고정 또는 미전송. 트림하지 않는다.**
```

⚠ 응답 축(`generatedAt`)에서 **트림이 실물로 관측됐다** — `.869920Z` → `.86992Z`(2026-08-21). 🔴 **해시 표면은 아니지만 같은 규칙을 응답 직렬화에도 적용한다.**

### 2-2. ⑤ 수 표현

```
① 정수는 정수로 — 후행 `.0` 금지            (1,  not 1.0)
② 🔴 부동소수는 payload 에 넣지 않는다
     불가피하면 → 십진 **문자열**("0.750") · 자릿수 고정 · 후행 0 유지
③ 지수 표기 금지                             (1e-3 → 0.001)
④ `-0` 은 `0` 으로
⑤ 정수는 64비트 부호 있는 범위 안
```

⚠ 현행 payload 에 부동소수는 **0개**다 **[읽음 `contracts/detection.py`]**. `enrolled_days`(소수) 대신 `enrolled_seconds`(정수)를 요청한 근거가 ②다.
⚠ `AxisConfidence`(classify 응답의 0.0~1.0)는 해당 없다 — 응답에만 실리고 해시 표면이 아니다. 🔴 **②는 「해시되는 입력」에만 건다.**

### 2-3. ⑥ null 과 키 부재 — 🔴 **범위 정정 (2026-08-21)**

AI 는 `model_dump(mode="json")` 을 쓰고 **`exclude_none` 을 쓰지 않는다.** 그래서 **안 보낸 optional 필드가 `null` 키로 남는다.**

#### 🔴 적용되는 배열 — **셋**

```
learning_events[]   correct · duration_sec · passage_word_count
                    · area_tag · subject_track · type_tag · item_format
                    · assignment_title_text            (passage_ref 는 §5 로 아예 제외)
alert_context[]     resolved_at
students[]          (nullable 필드가 현재 없다 — 생기면 여기에 적는다)
```

⚠ 🔴 **Jackson 이 `@JsonInclude(NON_NULL)` 이면 이 키들이 통째로 빠져 바이트가 갈린다.** `learning_events` 가 있는 **거의 모든 실요청**이 이 자리를 지난다.

#### 🔴 적용되지 **않는** 배열 — `detection_evidence`

**[읽음 `canonical.py::_evidence_row`]**

```python
def _evidence_row(item: DetectionEvidence) -> dict[str, Any]:
    """한 근거의 canonical dict — 🔴 **kind별 필드만** 담는다(거짓 조합 방지)."""
    row = {"kind", "student_ref", "at", "source_table", "record_id"}   # 공통 5키
    if isinstance(item, AssignmentWindowEvidence):
        row["expected_count"], row["submitted_count"] = …
    elif isinstance(item, WeeklyActivityEvidence):
        row["activity_count"] = …
    else:                                    # EnrollmentTransitionEvidence
        row["from_status"], row["to_status"] = …
```

🔴 **이 배열만 손으로 짓는다.** `weekly_activity` 행에 `expected_count` 가 없는 것은 **「null 이 빠진 것」이 아니라 「그 kind 에 그 필드가 없는 것」**이다 — `kind` 를 discriminator 로 갖는 union 이라 kind 마다 필드 집합이 다르다.

⚠ 🔴 **그래서 여기에 `NON_NULL` 을 떼면 오히려 갈린다.** 떼면 `weekly_activity` 행에 `"expected_count":null` 이 붙고, AI 쪽에는 그 키가 없다.

**대조 확인(2026-08-21 · 벡터 `v01`):** 양쪽 행이 글자 하나까지 같았다.

```
{"activity_count":0,"at":"2026-08-10","kind":"weekly_activity",
 "record_id":"swa_1","source_table":"student_week_activity","student_ref":"st_1"}
```

⚠ **종전 문면은 이 구분을 안 적었다** — *"대상 필드"* 로 `learning_events`·`alert_context` 만 나열했고, 그게 「나머지는 다르다」는 뜻이라는 걸 밝히지 않았다. 상대가 그 때문에 `NON_NULL` 을 떼려다 **물어보고 멈췄다.** 🔴 **물어봐 준 덕에 사고가 안 났다**(99 #167).

### 2-4. ⑦ 빈 컨테이너 — `detection_evidence` 만 특례

```
students · learning_events · alert_context   비어도  []  로 남는다
🔴 detection_evidence                        비면    **키 자체를 안 만든다**
```

**[읽음 `canonical.py`]** — 사유가 코드에 적혀 있다: *"기존 요청의 canonical payload 가 바뀌면 이미 발급된 멱등 키가 전부 어긋난다(하위 호환)."*

⚠ 🔴 **특례라는 것을 계약에 명시한다.** 안 적으면 다음 사람이 「빈 배열은 다 생략」으로 일반화한다.
⚠ **빈 배열을 명시해도 생략과 같은 해시다** — 모델이 둘 다 `()` 로 받아 *"클라이언트가 빈 배열을 명시했는가"* 를 복원할 수 없다. 복원 못 하는 구분을 해시에 넣으면 양쪽이 다른 값을 낼 수 있다(계약 확정 2026-08-12).

### 2-5. ⑧ 이스케이프

```
`/`         🔴 이스케이프 **안 함**
`"` `\`     `\"` · `\\`
제어문자     \u00XX (소문자 hex)
`\b \f \n \r \t`   짧은 형태
비ASCII      🔴 원문 그대로 (②)
서로게이트    ✅ **해소(2026-08-21)** — 원문 UTF-8 · 페어 미사용.
             🔴 입력 계약(`DetectRequest`)에 그런 문자가 들어올 자유 텍스트 필드가 없어
             **벡터로 검증하지 않는다.** 지어내서 넣으면 계약에 없는 입력을 계약처럼 만든다
```

---

## 3. 검증 절차

```
① 이 문서를 양쪽이 확정한다
② 벡터를 낸다 — `docs/part_a/examples/canonical_vectors/`
③ 각자 계산한다
④ 🔴 **서로 보기 전에 동시 공개** — 다르면 **이 문서가 심판**이다
```

**갈렸을 때 어느 벡터가 무엇을 말하나:**

| 벡터 | 갈리면 |
|---|---|
| `v01_null_vs_absent` | null 포함 설정 (§2-3) |
| `v02_time_utc` | 시각 표기 (§2-1 ㉠) |
| `v03_time_fraction` | 소수 초 자릿수 (§2-1 ㉡) |
| `v04_nonascii` | `ensure_ascii` (§2 ②) |
| `v05_empty_containers` | 빈 컨테이너 특례 (§2-4) |
| `v06_array_order` | 배열 정렬 키·방향 (§1-2) |
| `v07_escape` | `/` 이스케이프 (§2-5) |
| `v08_integers` | 정수 표현 (§2-2) |
| `v00_realistic` 만 | `passage_ref` 취급 (§5) |

⚠ 2026-08-21 1차 대조(v00·v01·v02·v06)에서 **넷 다 갈렸다.** v01 의 `detection_evidence` 행은 일치했으므로 §2-3 이 원인이 아니고, `learning_events[]` 키 집합과 §2-1 ㉠ 이 유력하다 **[추정 — 상대 회신 대기]**.

---

## 4. `passage_ref` — 해시 대상이 **아니다**

**[읽음 `canonical.py`]**

```python
"learning_events": [
    event.model_dump(mode="json", exclude={"passage_ref"})   # 🔴 canonical 에서 제외
    ...
]
```

코드에 사유까지 적혀 있다:

> *"`passage_ref` 는 **백엔드 요청 계약에 없는 AI 전용 필드**다(2026-08-13 대조). 백엔드 `AiDetectionRequest.LearningEventSnapshot` 에는 그 필드 자체가 없어 canonical 에 넣으면 `learning_events` 가 있는 **모든 실요청에서** Java 와 갈린다."*

⚠ 벡터 `v00_realistic` 은 **`passage_ref` 를 일부러 싣는다** — *"실려 있어도 해시에 안 들어간다"* 를 보이기 위해서다. 두 벌(넣은 판·뺀 판)을 만들 필요가 없다.
⚠ 나중에 넣기로 하면 **R1 입력 계약 + 해시 표면 동시 변경 안건**으로 따로 연다.

---

## 5. ⚠ AI 저장소 안에서도 직렬화 규칙이 갈려 있다

`hexdigest()` 자리 **16곳** 전수 **[실측 2026-08-20]**:

| 자리 | `sort_keys` | `ensure_ascii` | `separators` |
|---|---|---|---|
| 🔴 **`detection/canonical.py`** | ✅ | `False` | `(",", ":")` |
| `counsel/enqueue.py` · `routers/counsel.py` · `routers/problem.py` · `pg/identity.py` | ✅ | `False` | `(",", ":")` |
| ⚠ `routers/imports.py` | ✅ | `False` | **없음** ⇒ `", "` · `": "` |
| ⚠ `probe/enqueue.py` | ✅ | `False` | **없음** + `default=str` |

🔴 **BE 와 맞추는 축(`snapshot_hash`)은 `(",", ":")` 쪽이다.** `imports`·`probe` 는 내부 전용이라 지금 사고는 안 난다.
⚠ **이 절을 적는 이유:** §2 ③이 「Python 기본값이니까」가 아니라 **「detect 축에서 실제로 쓰는 값이니까」** 라는 것을 분명히 하기 위해서다.

---

## 6. 지금 열려 있는 것

```
🔴 ㉠  UTC 표기 — "Z" 권고. 확정되면 canonical.py::_evidence_at 한 줄 + v02 검사 갱신
◐     1차 대조 4벡터가 갈린 원인 — learning_events[] 키 집합이 유력 [추정 · 회신 대기]
◐     벡터 v03·v04·v05·v07·v08 전달 (파일은 저장소에 있다)
```

---

## 7. 🔴 이 문서와 코드가 갈리면 red 가 난다

`tests/ai/contract/test_canonical_serialization_doc.py` 가 **이 문서의 문면**과 **코드의 사실**을 대조한다. 문서를 고칠 때 코드를 안 고치면(또는 반대면) 거기서 잡힌다.

⚠ **값을 재는 검사가 아니다** — 「그 사실이 문면에 있는가」를 잰다(`test_timeout_budget_relations.py` 선례). 산문이 조용히 낡는 것을 막는 것이 목적이다.
