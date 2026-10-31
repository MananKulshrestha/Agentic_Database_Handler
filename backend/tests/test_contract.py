from types import SimpleNamespace

import httpx
import pytest

from backend.app.agent import _grounded_fallback, _remove_unverified_citations
from backend.app.analysis import (
    index_candidates,
    profile_rows,
    simple_suitability,
    validate_read_query,
)
from backend.app.connectors import _mongo_client, encrypt_password
from backend.app.database import Connection
from backend.app.diff import compare_snapshots
from backend.app.main import RunCreate, app


def object_row(object_id, columns, *, name="orders"):
    return SimpleNamespace(
        id=object_id,
        schema_name="public",
        name=name,
        kind="table",
        definition={"columns": columns, "primary_key": {}, "indexes": []},
    )


def test_diff_marks_removed_columns_as_potentially_breaking():
    before = object_row("before", [{"name": "id", "type": "INT"}, {"name": "note", "type": "TEXT"}])
    after = object_row("after", [{"name": "id", "type": "INT"}])
    report = compare_snapshots([before], [after])
    assert report["summary"] == {"added": 0, "removed": 0, "changed": 1}
    assert report["changed"][0]["columns"][0]["impact"] == "potentially_breaking"


def test_run_input_requires_kind_specific_fields():
    with pytest.raises(ValueError, match="profile requires object_ids"):
        RunCreate(kind="profile", snapshot_id="snapshot")
    with pytest.raises(ValueError, match="query_execute requires sql"):
        RunCreate(kind="query_execute", connection_id="connection")


def test_suitability_is_explicitly_provisional():
    report = simple_suitability("many joins and transactions", "mariadb")
    assert report["status"] == "provisional"
    assert report["options"][0]["option"] in {"mariadb", "postgresql"}
    assert report["scoring_method"]


def test_fallback_chat_answers_missing_primary_key_question_with_evidence():
    table = object_row("table-id", [])
    finding = SimpleNamespace(id="finding-id", rule_id="missing_primary_key", object_ids=[table.id], observation="No primary key")
    answer, evidence = _grounded_fallback("Which tables lack a primary key?", [table], [finding], [])
    assert "public.orders" in answer
    assert evidence == [{"kind": "finding", "id": "finding-id"}]


def test_chat_removes_citations_not_present_in_snapshot():
    fake_id = "00000000-0000-0000-0000-000000000000"
    assert _remove_unverified_citations(f"Claim [finding:{fake_id}]", [], []) == "Claim [unverified reference]"


def test_mongodb_authentication_database_is_configurable(monkeypatch):
    import pymongo

    seen = {}
    monkeypatch.setattr(pymongo, "MongoClient", lambda uri, **kwargs: seen.setdefault("uri", uri))
    connection = Connection(name="Mongo", engine="mongodb", host="localhost", port=27017, database="sample", auth_source="admin", username="reader", password_encrypted=encrypt_password("password"))
    _mongo_client(connection)
    assert "authSource=admin" in seen["uri"]


def test_read_query_rejects_large_and_locking_queries():
    with pytest.raises(ValueError, match="20 KB"):
        validate_read_query("SELECT " + "x" * 20000)
    with pytest.raises(ValueError, match="Locking"):
        validate_read_query("SELECT * FROM orders FOR UPDATE")


def test_workload_index_candidate_uses_schema_evidence():
    table = object_row("table-id", [{"name": "customer_id", "type": "INT"}])
    candidates = index_candidates([{"query": "SELECT * FROM orders WHERE customer_id = 42", "calls": 12}], [table], "postgres")
    assert candidates[0]["object_id"] == table.id
    assert candidates[0]["observed_calls"] == 12


def test_profile_reports_sample_limitations_without_raw_values():
    profile = profile_rows([{"name": "Ada"}, {"name": None}], ["name"])["name"]
    assert profile["null_rate"] == 0.5
    assert profile["maximum_text_length"] == 3
    assert "Ada" not in str(profile)


@pytest.mark.asyncio
async def test_api_error_envelope_has_request_id():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/api/v1/runs", json={"kind": "profile", "snapshot_id": "snapshot"})
    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"
    assert response.json()["request_id"] == response.headers["X-Request-ID"]
