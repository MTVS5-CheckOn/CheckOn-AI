# canonical 입력 벡터 — 백엔드(Java) 대조용

🔴 **이 폴더는 상위 `examples/README.md` 와 성격이 다르다.** 상위는 *"손으로 지은 예시가
아니라 실제로 받은 응답"* 인데, 여기 JSON 은 **대조용으로 지어낸 입력**이다. 상위 문장을
이 파일들에 적용해 읽지 마라.

## 파일

| 파일 | 찌르는 것 | 상태 |
| --- | --- | --- |
| `v00_realistic.json` | 실요청 모양 · passage_ref 가 실려도 해시에 안 들어간다 | ✅ |
| `v01_null_vs_absent.json` | 안 보낸 optional 필드가 null 키로 남는다 (§2-3) | ✅ |
| `v02_time_utc.json` | 한 payload 안에서 UTC 표기가 +00:00 과 Z 로 갈린다 (§2-1) | ✅ |
| `v03_time_fraction.json` | 소수 초 — 후행 0 이 보존된다 (§2-1 ㉡) | ✅ |
| `v04_nonascii.json` | 한글이 \uXXXX 가 아니라 원문 UTF-8 (§2 ②) | ✅ |
| `v05_empty_containers.json` | detection_evidence 만 키 자체가 없다 (§2-4) | ✅ |
| `v06_array_order.json` | 배열 입력 순서가 달라도 해시가 같다 (§1-2) | ✅ |
| `v07_escape.json` | `/` 를 이스케이프하지 않는다 (§2-5) — 제어문자·서로게이트는 안 다룬다 | ✅ |
| `v08_integers.json` | 정수에 후행 .0 이 없다 (§2-2) | ✅ |


## 🔴 해시는 여기 없다

백엔드와 *"규칙 문서를 심판으로 두고 각자 계산해 **동시 공개**"* 로 합의했다. 산출물에
우리 해시를 적으면 그 값이 정답이 되고, 합의가 무의미해진다.

재생성::

    WRITE_CANONICAL_VECTORS=1 python -m ai.evaluation.canonical_vectors

⚠ **정본은 `src/ai/evaluation/canonical_vectors.py` 의 파이썬 상수다.** 이 JSON 을 직접
고치면 `tests/ai/contract/test_canonical_vectors.py` 가 red 를 낸다.

## ⚠ 기존 정확 벡터 3종은 여기 없다

`tests/ai/contract/test_detection_evidence_contract.py` 의 `_HASH_LEGACY` ·
`_HASH_AGGREGATE` · `_HASH_TRANSITION` **상수**이고, 같은 파일이 canonical 문자열 전문도
고정한다. 이 폴더는 그것을 대체하지 않고 옆에 선다.
