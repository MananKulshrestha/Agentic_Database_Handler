import httpx
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.app import main
from backend.app.auth import hash_password, verify_password
from backend.app.database import Base, Project, get_session


def test_password_hash_is_salted_and_verifiable():
    first, second = hash_password("a-good-test-password"), hash_password("a-good-test-password")
    assert first != second
    assert verify_password("a-good-test-password", first)
    assert not verify_password("incorrect", first)


@pytest.mark.asyncio
async def test_optional_auth_bootstrap_roles_and_logout(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'auth.db'}")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with sessions() as db:
        db.add(Project())
        await db.commit()

    async def session_override():
        async with sessions() as db:
            yield db

    main.app.dependency_overrides[get_session] = session_override
    monkeypatch.setattr(main, "SessionLocal", sessions)
    monkeypatch.setattr(main.settings, "auth_required", True)
    monkeypatch.setattr(main.settings, "auth_bootstrap_secret", "bootstrap-test-secret")
    try:
        transport = httpx.ASGITransport(app=main.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            assert (await client.get("/api/v1/connections")).status_code == 401
            bootstrap = await client.post("/api/v1/auth/bootstrap", headers={"X-Bootstrap-Secret": "bootstrap-test-secret"}, json={"username": "admin", "password": "a-good-test-password"})
            assert bootstrap.status_code == 201
            login = await client.post("/api/v1/auth/login", json={"username": "admin", "password": "a-good-test-password"})
            assert login.status_code == 200
            admin_header = {"Authorization": f"Bearer {login.json()['access_token']}"}
            project = await client.get("/api/v1/project", headers=admin_header)
            assert project.status_code == 200
            project_id = project.json()["id"]
            viewer = await client.post(f"/api/v1/projects/{project_id}/members", headers=admin_header, json={"username": "viewer", "password": "viewer-test-password", "role": "viewer"})
            assert viewer.status_code == 201
            events = await client.get("/api/v1/audit-events", headers=admin_header, params={"resource_type": "user", "resource_id": viewer.json()["user_id"]})
            assert any(event["action"] == "member.created" and event["actor"] == bootstrap.json()["id"] for event in events.json())
            viewer_login = await client.post("/api/v1/auth/login", json={"username": "viewer", "password": "viewer-test-password"})
            viewer_header = {"Authorization": f"Bearer {viewer_login.json()['access_token']}"}
            assert (await client.get("/api/v1/connections", headers=viewer_header)).status_code == 200
            assert (await client.post("/api/v1/connections", headers=viewer_header, json={})).status_code == 403
            assert (await client.post("/api/v1/auth/logout", headers=viewer_header)).status_code == 204
            assert (await client.get("/api/v1/connections", headers=viewer_header)).status_code == 401
    finally:
        main.app.dependency_overrides.clear()
        await engine.dispose()
