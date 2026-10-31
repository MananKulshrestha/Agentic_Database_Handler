import asyncio
from datetime import timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.app import jobs
from backend.app.database import Base, Connection, Run, Schedule, utcnow


@pytest.mark.asyncio
async def test_due_schedule_creates_persisted_scan_run(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'scheduler.db'}")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with sessions() as db:
        target = Connection(name="Target", engine="postgresql", host="localhost", port=5432, database="demo", username="reader", password_encrypted="encrypted")
        db.add(target)
        await db.flush()
        db.add(Schedule(name="Due", connection_id=target.id, interval_minutes=5, enabled=True, next_run_at=utcnow() - timedelta(minutes=1)))
        await db.commit()
    monkeypatch.setattr(jobs, "SessionLocal", sessions)
    monkeypatch.setattr(jobs.settings, "max_active_jobs", 0)
    manager = jobs.JobManager()
    try:
        await manager.start()
        await asyncio.sleep(0.1)
        async with sessions() as db:
            runs = (await db.scalars(select(Run))).all()
            assert len(runs) == 1
            assert runs[0].kind == "scan"
            assert runs[0].status == "queued"
    finally:
        await manager.stop()
        await engine.dispose()
