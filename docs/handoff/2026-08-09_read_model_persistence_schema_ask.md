# 읽기 모델 영속 — 스키마 결정 요청 (A → B)

**작성 2026-08-09 · A(member-A) · 🔴 A가 고르지 않았다 — ㉕와 같은 형태의 협의 안건**

## 0. 왜 멈췄나

㉿·㉻·㉬ 나머지 절반의 여는 조건이 *"읽기 모델 영속(06 §7 `DRAFT_REVISION`)"* 이라
적혀 있었고, 테이블은 **이미 있다**(`draft`·`draft_revision` — `0001_initial_schema.py`부터).
⇒ *"신설이 아니라 배선"* 으로 착수했다.

🔴 **모양이 안 맞는다.** 배선만으로는 안 되고 **컬럼을 늘려야 한다** — 그건 `db/models.py`
(양자) + 마이그레이션이라 A 단독 범위가 아니다. ⇒ **목록만 내고 멈췄다.**

## 1. 안 들어가는 것 — 전수 (2026-08-09 실측)

### ⓐ 기존 `draft://` 저장소부터 이미 안 맞는다

| | 수 | 안 들어가는 것 |
| --- | --- | --- |
| `CounselPackResultRecord`의 형제 `DraftRecord` | 필드 **12** | 🔴 **`content`** — 초안 **본문**이다 |
| `draft` 테이블 | 컬럼 **11** | |

⇒ **`draft` 테이블에 본문 컬럼이 없다.** `InMemoryDraftResultStore`가 `content`를 들고 있고
`draft://`가 그것을 가리키는데, PG로 옮기면 **본문이 앉을 자리가 없다.**
⚠ 이건 이 PR이 만든 문제가 아니라 **이미 있던 불일치**다(테이블도 레코드도 그대로였다).

### ⓑ `_CachedView` (3) — GET 응답 뷰

| 필드 | 갈 곳 |
| --- | --- |
| `view: CounselDraftJobView` | 🔴 없음 — `job_id`·`status`·`result`(본문·인용·라벨·사유·시각)를 담은 **응답 뷰**다 |
| `execution_id: UUID \| None` | 🔴 없음 |
| `correlation_id: UUID` | 🔴 없음 |

⚠ **`draft`는 도메인 행이고 `_CachedView`는 응답 뷰다** — 축이 다르다.
`draft`에는 `job_id`도 없다(`agent_run_id`가 nullable로 있다).

### ⓒ `_DraftState` (5) — refine 상태

| 필드 | 갈 곳 |
| --- | --- |
| `context: DraftContext` | **부분만** — 아래 ⓓ |
| `citations` | 🔴 없음(`draft_revision.result_blocks` JSONB가 **후보**이나 의미가 다르다) |
| `text` | 🔴 없음 — ⓐ와 같은 자리 |
| `snapshot_hash` | 🔴 없음(refine 원장의 `input_snapshot_hash` 출처다 · 99 ㉭) |
| `emphasis` | 🔴 없음(99 ㉮ — 없으면 다듬기마다 강조점이 사라진다) |

### ⓓ `DraftContext` 하위 (8) — 셋만 들어간다

```
student_ref · guardian_ref · label_snapshot   → draft ✅
facts · evidence_summaries · period_label
fallback_text · inquiry_text                  → 🔴 없음  (5/8)
```

⚠ `facts`·`evidence_summaries`는 **게이트 재통과에 필요하다**(허용 숫자·금칙·길이 상한이 전부
거기서 나온다) — 빠지면 refine이 성립하지 않는다.

### ⓔ 그리고 두 테이블에 **프로덕션 쓰기가 0건**이다

`draft`·`draft_revision`을 언급하는 프로덕션 코드는 `db/models.py` **하나**다(전수).
⇒ **정의만 있고 소비 0** — 99 #22(`ItemCandidate`)와 **같은 형태**이고, 그래서 *"모양이
맞는지"* 를 아무도 확인할 기회가 없었다.

---

## 2. 선택지 — 🔴 A가 고르지 않았다

㉕에서 *"AGENT_RUN jsonb 흡수 vs 테이블 신설"* 을 협의로 정한 것과 **같은 형태**다.
그때 준영님이 근거 셋을 주셨고 **그중 둘은 A가 못 본 것**이었다.

| | 무엇 | 값 | 대가 |
| --- | --- | --- | --- |
| **ⓐ** | `draft`·`draft_revision`에 컬럼 추가(`content`·`snapshot_hash`·`emphasis`·컨텍스트 5) | 도메인 행이 자기 것을 다 든다 | 🔴 양자 + 마이그레이션 · **컬럼 여덟 이상** · `_CachedView`(응답 뷰)는 여전히 안 들어간다 |
| **ⓑ** | 읽기 모델 전용 테이블 신설(`counsel_draft_view` 류) + 스냅숏 JSONB | ㉕ 선례와 같은 형태(스냅숏 정본 + 파생 투영) · 응답 뷰가 그대로 앉는다 | 양자 + 마이그레이션 · **테이블 신설** |
| **ⓒ** | 기존 테이블 + `snapshot` JSONB 한 컬럼 | 컬럼 하나 | ⚠ 도메인 행에 응답 뷰를 얹는다 — 축이 섞인다 |
| **ⓓ** | 안 옮기고 **계약을 정직하게** — 04 §3.9에 *"축출 후 404"* 를 v1 한계로 못 박고 BE가 재요청하게 | 스키마 무변경 | 🔴 **제품 결함이 남는다** — BE가 202를 받고 GET하면 404이고 강사가 다시 눌러 초안이 2개 된다 |

⚠ **A의 관찰 둘**(판정은 아니다):
- `_CachedView`는 **도메인 테이블 어디에도 안 맞는다** — 응답 뷰라서다. ⓐ를 골라도 그 셋은
  남는다 ⇒ **ⓐ 단독으로는 ㉿가 안 닫힌다.**
- ⓐ의 컬럼 여덟 중 다섯(`DraftContext` 하위)은 **게이트 재통과용 입력**이고 산출이 아니다 —
  도메인 행에 넣는 것이 자연스러운지가 판단 지점이다.

---

## 3. 그동안 무엇이 보이게 됐나

고침이 스키마 결정 대기라 **트리거를 관측 가능하게** 했다(PR-μ):

```
view_cache_evicted   ← _view_cache 누적 축출 (GET 404의 원인)
draft_cache_evicted  ← _drafts    누적 축출 (refine 404의 원인)
```

⚠ **둘을 갈라 센다** — ㉿의 *"GET은 404인데 refine은 200"* 이 **두 캐시가 독립 축출**되기
때문이고, 합치면 그 비대칭이 리포트에서 사라진다.
⚠ **`None`이 아니라 항상 수다** — 이 캐시는 `store_backend`와 무관한 모듈 전역이라
`0`은 *"안 센다"* 가 아니라 **"축출이 없었다"** 다(`job_ledger_*`와 다른 성질).

**플립 직후 스모크에서 이 둘이 0이 아니면 ㉿가 실제로 밟히고 있다는 뜻이다.**

---

## 4. 준영님께 묻는 것

1. **선택지 ⓐ~ⓓ 중 무엇인가** — 또는 다섯째. ⚠ A는 고르지 않았다.
2. **`draft.content`가 없는 것은 의도인가** — 본문을 테이블 밖에 두기로 한 결정이
   어딘가에 있다면 그것이 ⓑ·ⓒ의 근거가 된다(A는 그 기록을 못 찾았다).
3. **`draft`·`draft_revision`의 프로덕션 쓰기가 0건인 것**을 알고 계셨는지 — ㉕ 때
   `item_candidate`가 같은 상태였고 그것이 #22가 됐다.
