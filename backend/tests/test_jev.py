import json

import httpx
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.app import jev, main
from backend.app.database import (
    Artifact,
    Base,
    Finding,
    Run,
    SchemaObject,
    Snapshot,
    get_session,
)


@pytest.mark.asyncio
async def test_official_systemone_request_and_response(monkeypatch):
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["authorization"] = request.headers["authorization"]
        seen["body"] = request.read().decode()
        return httpx.Response(200, json={"model": "jev-latest", "usage": {"input_tokens": 10, "output_tokens": 2},
                                          "answers": {"triage": {"type": "choice", "choice": "review_now", "confidence": 0.9, "probabilities": {"review_now": 0.9, "later": 0.1}}}})

    original_client = httpx.AsyncClient
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(jev.httpx, "AsyncClient", lambda **kwargs: original_client(transport=transport, **kwargs))
    monkeypatch.setattr(jev.settings, "typesafe_api_key", "test-key")
    result = await jev.decide({"observation": "example"}, {"triage": jev.choice("Pick queue", {"review_now": "Urgent", "later": "Later"})})
    assert seen["url"] == "https://api.typesafe.ai/v1/systemone"
    assert seen["authorization"] == "Bearer test-key"
    assert json.loads(seen["body"])["model"] == "jev-latest"
    assert result["decisions"]["triage"] == {"value": "review_now", "confidence": 0.9}


@pytest.mark.asyncio
async def test_all_p0_decision_routes_use_saved_evidence(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'jev.db'}")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async def session_override():
        async with sessions() as session:
            yield session

    seen = []

    async def fake_decide(state, questions):
        seen.append((state, set(questions)))
        decisions = {}
        for name, question in questions.items():
            value = 3.0 if question["type"] == "score" else next(iter(question["criteria"]))
            decisions[name] = {"value": value, "confidence": 0.9}
        return {"model": "jev-latest", "decisions": decisions}

    monkeypatch.setattr(main, "jev_enabled", lambda: True)
    monkeypatch.setattr(main, "decide", fake_decide)
    main.app.dependency_overrides[get_session] = session_override
    try:
        async with sessions() as db:
            before = Snapshot(connection_id="connection-1")
            after = Snapshot(connection_id="connection-1")
            db.add_all([before, after])
            await db.flush()
            db.add_all([
                SchemaObject(snapshot_id=before.id, kind="table", schema_name="public", name="orders", definition={"columns": [{"name": "old", "type": "INT"}]}),
                SchemaObject(snapshot_id=after.id, kind="table", schema_name="public", name="orders", definition={"columns": []}),
                Finding(snapshot_id=after.id, rule_id="missing_primary_key", category="integrity", severity="medium", observation="Missing key", recommendation="Review key", evidence={"primary_key": {}}, assumptions=["May be intentional"]),
                Finding(snapshot_id=after.id, rule_id="missing_table_comment", category="documentation", severity="info", observation="Missing comment", recommendation="Review comment", evidence={"comment": {}}, assumptions=[]),
            ])
            run = Run(kind="redesign", status="failed", stage="failed")
            db.add(run)
            await db.flush()
            proposal = Artifact(run_id=run.id, connection_id="connection-1", snapshot_id=after.id, kind="redesign", title="Redesign", content={"proposals": [{"change_to_review": "Add key"}]})
            db.add(proposal)
            await db.commit()

        transport = httpx.ASGITransport(app=main.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            findings = await client.get("/api/v1/jev/findings", params={"snapshot_id": after.id})
            first_page = await client.get("/api/v1/jev/findings", params={"snapshot_id": after.id, "limit": 1})
            second_page = await client.get("/api/v1/jev/findings", params={"snapshot_id": after.id, "limit": 1, "cursor": 1})
            diff = await client.get(f"/api/v1/jev/snapshots/{after.id}/diff", params={"against": before.id})
            readiness = await client.get(f"/api/v1/jev/artifacts/{proposal.id}/readiness")
            activity = await client.get("/api/v1/jev/runs")
        assert all(response.status_code == 200 for response in (findings, diff, readiness, activity))
        assert set(findings.json()["items"][0]["decisions"]) == {"priority", "triage", "relevance", "missing_evidence"}
        assert first_page.json()["has_more"] is True
        assert second_page.json()["has_more"] is False
        assert first_page.json()["items"][0]["id"] != second_page.json()["items"][0]["id"]
        assert diff.json()["items"][0]["decisions"]["priority"]["value"] == 3.0
        assert readiness.json()["decisions"]["readiness"]["value"] == "ready_for_review"
        assert activity.json()["items"][0]["decisions"]["attention"]["value"] == 3.0
        assert {name for _, names in seen for name in names} == {"priority", "triage", "relevance", "missing_evidence", "readiness", "attention"}
    finally:
        main.app.dependency_overrides.clear()
        await engine.dispose()
