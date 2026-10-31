import pytest
from sqlalchemy import TIMESTAMP
from sqlalchemy.dialects import postgresql
from types import SimpleNamespace

from backend.app.analysis import evaluate_object, normalization_report, validate_read_query
from backend.app.connectors import decrypt_password, encrypt_password
from backend.app.jobs import _sqlite_experiment


def test_closure_and_3nf_violation():
    report = normalization_report(
        ["order_id", "product_id", "product_name"],
        [
            {"determinant": ["order_id", "product_id"], "dependent": ["product_name"]},
            {"determinant": ["product_id"], "dependent": ["product_name"]},
        ],
        "3NF",
    )
    assert report["status"] == "violation_identified"
    assert report["candidate_keys"] == [["order_id", "product_id"]]
    assert report["violations"][0]["determinant"] == ["product_id"]


def test_missing_dependencies_and_higher_normal_forms_are_inconclusive():
    assert normalization_report(["a"], [], "1NF")["status"] == "insufficient_information"
    assert (
        normalization_report(["a", "b"], [], "3NF")["status"]
        == "insufficient_information"
    )
    assert (
        normalization_report(
            ["a", "b"], [{"determinant": ["a"], "dependent": ["b"]}], "5NF"
        )["status"]
        == "insufficient_information"
    )


def test_read_query_validation():
    assert validate_read_query("SELECT 1;") == "SELECT 1"
    with pytest.raises(ValueError):
        validate_read_query("DELETE FROM users")
    with pytest.raises(ValueError):
        validate_read_query("SELECT 1; SELECT 2")


def test_credential_encryption_round_trip():
    encrypted = encrypt_password("example-password")
    assert encrypted != "example-password"
    assert decrypt_password(encrypted) == "example-password"


def test_postgres_timestamp_timezone_findings_are_precise():
    dialect = postgresql.dialect()
    columns = [
        {"name": "created_at", "type": str(TIMESTAMP(timezone=True).compile(dialect=dialect))},
        {"name": "local_at", "type": str(TIMESTAMP(timezone=False).compile(dialect=dialect))},
        {"name": "old_snapshot_at", "type": "TIMESTAMP"},
    ]
    obj = SimpleNamespace(
        id="table-1",
        kind="table",
        schema_name="public",
        name="events",
        definition={
            "columns": columns,
            "primary_key": {"constrained_columns": ["created_at"]},
            "comment": {"text": "Test table"},
        },
    )
    findings = evaluate_object(obj, "postgresql")
    timestamp_findings = [f for f in findings if f["rule_id"] == "timestamp_without_timezone"]
    assert [f["evidence"]["column"] for f in timestamp_findings] == ["local_at"]


def test_synthetic_experiment_preserves_results():
    result = _sqlite_experiment(
        [{"name": "customer_id"}, {"name": "label"}], "customer_id", 1000
    )
    assert result["same_results"] is True
    assert result["synthetic_rows"] == 1000
