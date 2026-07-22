"""평가 (프로덕션 격리) — 골든셋 소비·평가자·평가 전용 픽스처.

이 구역의 코드는 detection·composition 등 프로덕션 capability가 import하지 않는다
(02_ownership.md §5 "평가 (프로덕션 격리)"). 소비자는 evaluation/ 내부와 tests/뿐이다.
"""
