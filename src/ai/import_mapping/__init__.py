"""Import(스마트 데이터 이전) capability — 결정론 골격.

10_import_spec §6.3(A 단독 확정) 범위: 계약 모델·상태기계·결정론 프로파일링·reused 시그니처
· FakeMappingProvider(LLM 실패 폴백 포함). **변환기·output_url은 후속이 아니라 범위 밖**이다
— 전체 행 변환은 백엔드 소유(§4, 2026-07-30). 조사 에이전트 실행은 후속(스텁).
소유: 박진희(member-A).
"""
