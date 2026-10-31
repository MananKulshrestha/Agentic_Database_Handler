import httpx
import pytest
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.app.database import Base, get_session
from backend.app.error_messages import describe_error
from backend.app.jobs import public_error
from backend.app.main import app


@pytest.mark.parametrize(
    ("driver_message", "code", "phrase"),
    [
        ("password authentication failed for user agentic_inspector", "DATABASE_AUTH_FAILED", "username or password"),
        ("Tenant or user not found", "DATABASE_POOLER_USER_NOT_FOUND", "pooler"),
        ("FATAL: (ENOIDENTIFIER) no tenant identifier provided", "DATABASE_POOLER_PROJECT_MISSING", "Supabase project"),
        ("FATAL: Circuit breaker open", "DATABASE_POOLER_THROTTLED", "temporarily"),
        ("could not translate host name", "DATABASE_HOST_NOT_FOUND", "host name"),
        ("Network is unreachable", "DATABASE_NETWORK_UNREACHABLE", "network"),
        ("connection refused", "DATABASE_CONNECTION_REFUSED", "refused"),
        ("SSL certificate verify failed", "DATABASE_SSL_FAILED", "secure connection"),
        ("permission denied for schema public", "DATABASE_PERMISSION_DENIED", "denied access"),
    ],
)
def test_driver_failures_have_actionable_safe_messages(driver_message, code, phrase):
    error = OperationalError("connect", None, Exception(f"{driver_message}; password=TOP_SECRET"))
    result = describe_error(error)
    assert result.code == code
    assert phrase in result.text()
    assert "TOP_SECRET" not in result.text()
    assert "TOP_SECRET" not in public_error(error)


def test_unclassified_errors_do_not_expose_driver_text():
    result = describe_error(RuntimeError("connection URL has password=TOP_SECRET"))
    assert result.code == "OPERATION_FAILED"
    assert "TOP_SECRET" not in result.text()
    assert "TOP_SECRET" not in describe_error(ValueError("password=TOP_SECRET")).text()
    assert describe_error(RuntimeError("authentication failed for another service")).code == "OPERATION_FAILED"
    assert describe_error(TimeoutError()).code == "OPERATION_TIMEOUT"
    assert describe_error(TimeoutError(), connection_test=True).code == "DATABASE_TIMEOUT"


@pytest.mark.asyncio
async def test_connection_test_returns_error_code_and_hint_without_secret(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'errors.db'}")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    async def session_override():
        async with sessions() as session:
            yield session

    async def failed_test(_connection):
        raise OperationalError("connect", None, Exception("password authentication failed; password=TOP_SECRET"))

    monkeypatch.setattr("backend.app.main.test_connection", failed_test)
    app.dependency_overrides[get_session] = session_override
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            created = await client.post(
                "/api/v1/connections",
                json={"name": "Test", "engine": "postgresql", "host": "example.org", "port": 5432, "database": "postgres", "username": "reader", "password": "TOP_SECRET"},
            )
            assert created.status_code == 201
            response = await client.post(f"/api/v1/connections/{created.json()['id']}/test")
        assert response.status_code == 422
        body = response.json()
        assert body["code"] == "DATABASE_AUTH_FAILED"
        assert "password" in body["message"]
        assert "Edit connection" in body["details"]["hint"]
        assert body["request_id"] == response.headers["X-Request-ID"]
        assert "TOP_SECRET" not in response.text
    finally:
        app.dependency_overrides.pop(get_session, None)
        await engine.dispose()
