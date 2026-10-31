import httpx
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.app.database import (
    Artifact,
    Base,
    Finding,
    Run,
    SchemaObject,
    Snapshot,
    get_session,
)
from backend.app.main import app


@pytest.mark.asyncio
async def test_connection_findings_and_diff_api(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'api.db'}")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    async def session_override():
        async with sessions() as session:
            yield session

    app.dependency_overrides[get_session] = session_override
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/api/v1/connections",
                json={"name": "Test", "engine": "postgresql", "host": "localhost", "port": 5432, "database": "demo", "username": "reader", "password": "secret"},
            )
            assert response.status_code == 201
            assert "password_encrypted" not in response.json()
            connection_id = response.json()["id"]
            schedule = await client.post("/api/v1/schedules", json={"name": "Hourly", "connection_id": connection_id, "interval_minutes": 60})
            assert schedule.status_code == 201
            paused = await client.patch(f"/api/v1/schedules/{schedule.json()['id']}", json={"enabled": False})
            assert paused.json()["enabled"] is False
            async with sessions() as db:
                before = Snapshot(connection_id=connection_id)
                after = Snapshot(connection_id=connection_id)
                db.add_all([before, after])
                await db.flush()
                old = SchemaObject(snapshot_id=before.id, kind="table", schema_name="public", name="orders", definition={"columns": [{"name": "id", "type": "INT"}, {"name": "note", "type": "TEXT"}]})
                new = SchemaObject(snapshot_id=after.id, kind="table", schema_name="public", name="orders", definition={"columns": [{"name": "id", "type": "INT"}]})
                db.add_all([old, new])
                await db.flush()
                for number in range(205):
                    db.add(Finding(snapshot_id=after.id, rule_id="test", category="test", severity="low", object_ids=[new.id] if number % 2 else [old.id], observation=str(number), recommendation="review", evidence={}))
                proposal_run = Run(kind="redesign")
                db.add(proposal_run)
                await db.flush()
                proposal = Artifact(run_id=proposal_run.id, connection_id=connection_id, snapshot_id=after.id, kind="redesign", title="Proposal", content={}, markdown="# Proposal")
                db.add(proposal)
                await db.commit()

            diff = await client.get(f"/api/v1/snapshots/{after.id}/diff", params={"against": before.id})
            assert diff.status_code == 200
            assert diff.json()["changed"][0]["columns"][0]["change"] == "removed"
            page = await client.get("/api/v1/findings", params={"snapshot_id": after.id, "object_id": new.id, "cursor": 100, "limit": 20})
            assert page.status_code == 200
            assert len(page.json()) == 2
            approval = await client.post("/api/v1/change-approvals", json={"artifact_id": proposal.id, "note": "Please review"})
            assert approval.status_code == 201
            decision = await client.patch(f"/api/v1/change-approvals/{approval.json()['id']}", json={"status": "approved", "note": "Reviewed"})
            assert decision.json()["status"] == "approved"
            audit = await client.get("/api/v1/audit-events", params={"resource_type": "change_approval", "resource_id": approval.json()["id"]})
            assert {event["action"] for event in audit.json()} == {"approval.created", "approval.reviewed"}
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()
