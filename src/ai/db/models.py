"""ERD 26테이블 ORM — 06_erd.md 정본.

소유: 공통 계약 (⚠ B 확인 대기 — 양자 11곳). B의 문제·진단 테이블도 여기 있으므로 공용.

**실명·연락처 컬럼 절대 없음**(불변식 3) — alias(student_ref 등)만.
enum성 컬럼(signal_type·lifecycle·status류)은 PG enum 대신 **varchar + 값 검증은 앱 계층**
(enum 확장 시 마이그레이션 부담 회피 — D-② 확정).

이 커밋(chore: alembic 환경)에는 테이블 정의를 두지 않는다 — Base만 노출하고,
26테이블 정의는 다음 커밋(feat: 마이그레이션)에서 채운다. env.py가 이 모듈을 import해
Base.metadata에 테이블을 등록한다.
"""

from __future__ import annotations

from ai.db.base import Base

__all__ = ["Base"]
