"""PG 잡의 실제 프로세스 사망·재발견·회수·결과 복원 E2E."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from pg_hint import pg_unavailable
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from ai.db.models import Base
from ai.db.settings import get_db_settings

pytestmark = pytest.mark.integration

_WORKER = Path(__file__).with_name("pg_restart_worker.py")
_ROOT = Path(__file__).resolve().parents[3]


def _child(mode: str) -> dict[str, object]:
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join((str(_ROOT / "src"), str(_ROOT / "tests/ai/fakes")))
    completed = subprocess.run(  # noqa: S603 — 고정된 로컬 helper와 mode만 실행한다
        [sys.executable, str(_WORKER), mode],
        cwd=_ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
        timeout=15,
    )
    payload = json.loads(completed.stdout.strip().splitlines()[-1])
    assert isinstance(payload, dict)
    return payload


def test_problem_job_survives_real_process_restart() -> None:
    database_url = get_db_settings().database_url

    async def prepare() -> bool:
        engine = create_async_engine(database_url, poolclass=NullPool)
        try:
            try:
                async with engine.connect() as connection:
                    await connection.execute(text("SELECT 1"))
            except Exception:  # noqa: BLE001 — 실 PG 부재는 기존 integration skip 규약
                return False
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.drop_all)
                await connection.run_sync(Base.metadata.create_all)
            return True
        finally:
            await engine.dispose()

    if not asyncio.run(prepare()):
        pytest.skip(pg_unavailable("PG 프로세스 재시작 E2E"))

    try:
        assert _child("seed")["phase"] == "queued"
        assert _child("lease-and-exit") == {
            "phase": "running",
            "request_id": "request-process-restart",
            "candidate_ref": ("item-candidate:00000000-0000-4000-8000-00000000d123:0:1"),
        }
        time.sleep(1.2)
        recovered = _child("recover")
        assert recovered["phase"] == "succeeded"
        assert recovered["recovery_count"] == 1
        assert recovered["request_id"] == "request-process-restart"
        assert _child("verify") == {
            "phase": "succeeded",
            "recovery_count": 1,
            "set_id": "00000000-0000-4000-8000-00000000d123",
        }
    finally:

        async def cleanup() -> None:
            engine = create_async_engine(database_url, poolclass=NullPool)
            try:
                async with engine.begin() as connection:
                    await connection.run_sync(Base.metadata.drop_all)
                    await connection.run_sync(Base.metadata.create_all)
            finally:
                await engine.dispose()

        asyncio.run(cleanup())
