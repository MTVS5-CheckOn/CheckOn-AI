# 문제출제 T1 실 LLM 스모크 보고

**2026-08-05 · `feat/pg-real-provider` · 프롬프트·게이트 무변경 실측**

> 목적은 현재 프롬프트가 실 모델에서 `GeneratedItem`을 만드는지 확인하는 것이다.
> 통과를 만들기 위해 파서·규칙·임계값을 완화하지 않았고, 실패를 그대로 기록한다.

## 1. 실행 조건

| 항목 | 값 |
| --- | --- |
| 호출 시각 | `2026-08-05T12:41:42.137666+09:00` |
| generator endpoint / model | `http://mtvs2026.work/v1` / `balanced-q4-k-m-mtp` |
| verifier endpoint / model | `http://mtvs2026.work/v1` / `balanced-q4-k-m-mtp` |
| provider 식별자 | `pg-local-generator` / `pg-local-verifier` |
| verifier 전용 설정 | 미설정 — 로컬 fallback |
| 외부 tracing | 비활성 |
| 요청 | T1 문법 · `language` × `infer` · MCQ 1문항 |
| 소요 시간 | 10.874초 |
| 호출 / 토큰 | generator 3회 · 입력 1,919 · 출력 1,986 · 합계 3,905 |

API 키는 기록하지 않았다. 토큰 수는 provider 응답의 usage를 합산했으며, 현재
어댑터는 로컬 호출 비용을 `0.0 USD`로 기록한다.

현재 실측 endpoint는 HTTPS가 아닌 HTTP다. 실제 비밀 자격증명을 사용할 경우 전송
구간 보호가 없으므로 endpoint TLS 전환이 필요하다. 이번 작업에서는 환경을 변경하지 않았다.

## 2. 생성 문항 원문 1개

세 번째이자 마지막 generator 응답이다. JSON 디코딩에는 성공했지만 아래 §3의
스키마 검증에서 실패했다.

```json
{
  "item_format": "mcq",
  "area_tag": "language",
  "skill_node_id": "grammar.sentence-structure",
  "type_tag": "infer",
  "stem": "다음 중 문장의 성분과 구조에 대한 설명으로 가장 적절한 것은?",
  "options": [
    {
      "option_no": 1,
      "text": "주어와 서술어의 호응이 일치하지 않는 문장은 문법적으로 완벽한 문장이다.",
      "why_wrong": "주어와 서술어는 문장의 골격을 이루는 핵심 성분으로, 서로 논리적·문법적으로 호응해야 올바른 문장이 된다."
    },
    {
      "option_no": 2,
      "text": "목적어는 서술어가 나타내는 동작이나 작용의 대상이 되는 성분이다.",
      "why_wrong": "정답. 목적어는 타동사의 대상이 되는 성분으로서 문장 구조상 필수적인 역할을 수행한다."
    },
    {
      "option_no": 3,
      "text": "부속 성분은 문장의 필수 성분 중 하나로, 문장에서 반드시 있어야 하는 성분이다.",
      "why_wrong": "부속 성분(관형어, 부사어)은 문장의 내용을 풍부하게 하지만, 생략되더라도 문장의 기본 구조를 유지할 수 있는 수식 성분이다."
    },
    {
      "option_no": 4,
      "text": "서술어는 문장에서 주어의 움직임이나 상태를 나타내며, 반드시 체언 뒤에 조사가 붙어야 한다.",
      "why_wrong": "서술어는 용언(동사, 형용사)뿐만 아니라 '이다'와 같은 서술격 조사에 의해 형성될 수도 있으며, 반드시 체언 뒤에 조사가 붙는 형태만은 아니다."
    },
    {
      "option_no": 5,
      "text": "관형어는 서술어를 수식하여 동작의 양상을 구체화하는 역할을 한다.",
      "why_wrong": "관형어는 서술어가 아닌 체언(명사, 대명사, 수사)을 수식하여 그 내용을 한정하는 역할을 한다."
    }
  ],
  "answer": 2,
  "rationale": "grammar:rule-1 (문장의 기본 구조와 성분의 역할: 주어, 목적어, 서술어의 정의와 각 성분의 기능적 관계 참조)",
  "difficulty": "medium"
}
```

## 3. 파싱·게이트 판정

| 단계 | 결과 |
| --- | --- |
| provider 왕복 | 3회 모두 `ok` |
| JSON 디코딩 | 3회 모두 성공 |
| `GeneratedItem` 파싱 | **3회 모두 실패** — `FieldMissing` |
| 최종 세트 상태 | `failed` |
| 문항 상태 | `dropped` · attempt 3 |
| 실패 사유 | `generation_exhausted` |
| 실패 상세 | `생성 시도 소진: FieldMissing` |
| R-N 규칙 | 해당 없음 — R-1~R-7 진입 전 스키마 검증에서 종료 |

마지막 응답은 `choices` 대신 `options`, 선지 `no` 대신 `option_no`,
`answer: {"correct_no": N}` 대신 숫자 `answer`, 계약에 없는 `difficulty`를 반환했다.
필수 `evidence`도 누락했다. 앞선 두 응답도 `item` 래퍼와 `metadata`,
`answer_index` 등 계약 밖 구조를 사용했다.

## 4. 교차 풀이

verifier 호출은 **0회**다. 생성 결과가 규칙 게이트 이전의 `GeneratedItem` 파싱을
통과하지 못했으므로 교차 풀이 결과도 없다.

또한 이번 배선은 두 provider 인스턴스와 식별자를 분리했지만, 실제 endpoint와
model은 양쪽 모두 동일하다. 따라서 verifier가 호출됐더라도 이번 실측은
**서로 다른 모델 패밀리의 blind 교차 풀이가 아니다.** GPT verifier 설정이 들어온
뒤에야 06 §2의 패밀리 분리가 성립한다.

## 5. 프롬프트 개선 후보 — 이번 작업에서는 미수정

1. `GeneratedItem 스키마`라는 이름만 쓰지 말고 평면 필드와 중첩 구조를 명시해야 한다.
   특히 `choices[].no/text/why_wrong`, `answer.correct_no`,
   `evidence[].kind/ref/quote`의 정확한 JSON 형태가 필요하다.
2. `item`·`metadata` 래퍼, `options`, `answer_index`, `difficulty`처럼 모델이 자주 만든
   계약 밖 필드를 금지하거나 정확한 최소 예시로 대체해야 한다.
3. 재시도 문맥에는 현재 `generator:FieldMissing`만 들어간다. 어느 필드가 누락·초과됐는지
   구조화 검증 오류를 전달해야 같은 유형의 잘못된 스키마 반복을 줄일 수 있다.
4. 현재 ContextPack에는 허용 ref만 있고 `grammar:rule-1`의 실제 규범 내용이 없다.
   승인 근거만으로 rationale을 쓰라는 규칙을 지키려면 검증 가능한 앵커 내용이 필요하다.

## 6. GPT verifier 연결 시 변경량

코드 변경 없이 `.env`의 **필수 3줄**을 채우면 별도 verifier가 선택된다.

```dotenv
VERIFIER_LLM_BASE_URL = https://api.openai.com/v1
VERIFIER_LLM_API_KEY = <비밀값>
VERIFIER_LLM_MODEL = gpt-5.4-mini
```

`VERIFIER_LLM_TIMEOUT_S`는 기본 15초를 바꿀 때만 추가하는 선택 1줄이다. 세 필수값 중
일부만 설정하면 조립 단계에서 실패하도록 구성했다. 설정 getter가 프로세스 안에서
캐시되므로 `.env`를 채운 뒤 프로세스를 재시작해야 하며 코드 변경은 없다.

현 어댑터는 외부 verifier에도 `cost_usd=0.0`을 기록한다. GPT 호출 비용 회계를
정확히 하려면 provider별 가격 계산을 별도 작업으로 연결해야 한다.

## 7. 결론

두 provider 역할의 조립은 성공했고, 실서버 왕복은 generator에서 확인했다.
verifier는 생성 파싱 실패로 호출되지 않았다. 현재 로컬 모델은 `GeneratedItem` 계약을
3회 연속 충족하지 못했고, 실제 문항은 `dropped`됐다. 통합 스모크가 이 문제를
실패로 드러내며, 프롬프트·재시도 피드백 개선은 별도 작업이다.
