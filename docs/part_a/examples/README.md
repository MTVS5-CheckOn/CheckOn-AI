# 데모 스냅숏 — 실물 산출

이 폴더의 JSON은 **손으로 지은 예시가 아니라 실제로 받은 응답**이다. 지어낸 예시는 계약이
바뀔 때 조용히 거짓이 되므로, 계약을 설명할 때 이 파일을 근거로 쓴다.

## 파일

| 파일 | 무엇 | 산출 방법 |
| --- | --- | --- |
| `detect_demo_request.json` | `POST /v1/detect` 바디(학생 10명 × 10주) | `python -m ai.evaluation.demo_snapshot` — **자동·재현 가능** |
| `detect_demo_response.json` | 그 응답 | 〃 |
| `counsel_demo_request.json` | `POST /v1/counsel/drafts` 바디 | **수동**(아래) |
| `counsel_demo_response.json` | 그 202 응답 | 〃 |
| `counsel_demo_get_response.json` | `GET /v1/counsel/drafts/{job_id}` 200 | 〃 |
| `counsel_demo_refine_response.json` | `POST …/{job_id}/refine` 200 (턴 1) | 〃 |

## 🔴 실행마다 달라지는 필드

아래 셋은 **매 실행 값이 다르다.** 바이트 비교로 회귀를 잡을 때 제외한다(감지 축 선례 ⑳-a:
*"execution_id 제외 sha 3자 일치"*).

| 필드 | 어디 | 왜 |
| --- | --- | --- |
| `meta.execution_id` | 모든 응답 | 요청마다 `uuid4` |
| `data.job_id` | counsel POST·GET | 잡마다 `uuid4` |
| `data.result.generated_at` | counsel GET | 생성 시각(UTC) |

그 밖의 필드는 같은 입력 + 같은 버전 세트에서 **바이트 동일**해야 한다(불변식 8).

## counsel 스냅숏 재생성 (수동)

`demo_snapshot.py`에 상담 축을 **아직 넣지 않았다.** 이유는 두 가지다:
① 이 스냅숏은 `TestClient`가 아니라 **별도 프로세스로 띄운 uvicorn**에 `curl`로 때려 받았다 —
   기동 순서(`create_app` → `include_router` → startup 훅 → `bootstrap_counsel_provider`)를
   실제로 지나는 것이 확인의 목적이었기 때문이다(99 ㉨·#52).
② refine 축은 **턴마다 다른 본문**을 내는 관측 대역이 있어야 의미가 생긴다(기본 Fake는 매번
   같은 문장을 낸다 — 아래 참조). 그걸 자동화하려면 러너에 대역 주입 seam이 필요하다.

재생성 절차:

```bash
uv run uvicorn ai.api.app:app --port 8000        # LLM_PROVIDER=fake · STORE_BACKEND=memory
# 헤더: X-Tenant-Id · X-Request-Id · Idempotency-Key(POST만)
# 바디: tests/ai/integration/test_counsel_router.py 의 _REQUEST 를 그대로 쓴다
```

⚠ **바디를 새로 짓지 마라** — 스모크 러너(`evaluation/counsel_llm_smoke.py`)도 그 `_REQUEST`를
쓴다. 두 곳이 갈리면 "같은 입력"이라는 말이 깨진다.

## ⚠ Fake provider에서는 본문이 안 바뀐다

`LLM_PROVIDER=fake`(기본)에서 `FakeCounselLlmProvider`는 write 호출에 **항상 같은 문장**을
돌려준다. 그래서 `counsel_demo_refine_response.json`의 `text`는 최초 초안과 **글자가 같다** —
`applied: true`인데 본문이 그대로다.

🔴 **다듬기가 고장 난 것이 아니다.** 누적 배선은 `previous_text`(직전 본문)를 다음 턴
프롬프트에 싣는 방식이고, 턴마다 다른 본문을 내는 대역으로 재보면 실제로 실린다(8/6 실측:
턴1 프롬프트 ⊃ POST 결과 · 턴2 프롬프트 ⊃ 턴1 결과). Fake는 그 차이를 만들지 않을 뿐이다.

⚠ **시연 주의** — Fake로 시연하면 "다듬기를 눌러도 본문이 그대로"로 보인다.
