import asyncio
import base64
import hashlib
import json
from collections import Counter
from urllib.parse import quote_plus

from cryptography.fernet import Fernet
from sqlalchemy import inspect, text
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import create_async_engine

from .config import settings
from .database import Connection


def _cipher() -> Fernet:
    digest = hashlib.sha256(settings.dbh_encryption_secret.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_password(password: str) -> str:
    return _cipher().encrypt(password.encode()).decode()


def decrypt_password(value: str) -> str:
    return _cipher().decrypt(value.encode()).decode()


def connection_url(connection: Connection) -> URL:
    driver = {"postgresql": "postgresql+psycopg", "mariadb": "mysql+asyncmy"}.get(
        connection.engine
    )
    if not driver:
        raise ValueError(f"No SQL driver for {connection.engine}")
    return URL.create(
        driver,
        username=connection.username,
        password=decrypt_password(connection.password_encrypted),
        host=connection.host,
        port=connection.port,
        database=connection.database,
    )


def connection_args(connection: Connection) -> dict:
    return {"connect_timeout": 5} if connection.engine == "postgresql" else {}


def _plain(value):
    return json.loads(json.dumps(value, default=str))


async def sql_test(connection: Connection) -> dict:
    engine = create_async_engine(
        connection_url(connection),
        pool_size=1,
        max_overflow=0,
        connect_args=connection_args(connection),
    )
    try:
        async with asyncio.timeout(8):
            async with engine.connect() as db:
                version = (await db.execute(text("SELECT version()"))).scalar_one()
        return {
            "connected": True,
            "engine_version": str(version)[:100],
            "schema": True,
            "query_plan": True,
            "workload_statistics": connection.engine == "postgresql",
        }
    finally:
        await engine.dispose()


async def sql_scan(
    connection: Connection,
) -> tuple[list[dict], list[dict], str, list[str]]:
    engine = create_async_engine(
        connection_url(connection),
        pool_size=1,
        max_overflow=0,
        connect_args=connection_args(connection),
    )
    objects, relationships, warnings = [], [], []
    try:
        async with asyncio.timeout(60):
            async with engine.connect() as db:
                if connection.engine == "postgresql":
                    await db.execute(text("SET statement_timeout = 5000"))
                version = str(
                    (await db.execute(text("SELECT version()"))).scalar_one()
                )[:100]

                def collect(sync_db):
                    inspector = inspect(sync_db)
                    scope = connection.schema_scope or (
                        ["public"]
                        if connection.engine == "postgresql"
                        else [connection.database]
                    )
                    for schema in scope:
                        try:
                            names = [
                                ("table", name)
                                for name in inspector.get_table_names(schema=schema)
                            ]
                            names += [
                                ("view", name)
                                for name in inspector.get_view_names(schema=schema)
                            ]
                        except Exception as exc:
                            warnings.append(f"Could not inspect schema {schema}: {exc}")
                            continue
                        for kind, name in names:
                            obj = {
                                "kind": kind,
                                "schema": schema,
                                "name": name,
                                "definition": {},
                            }
                            info = obj["definition"]
                            for key, method, fallback in [
                                ("columns", inspector.get_columns, []),
                                ("primary_key", inspector.get_pk_constraint, {}),
                                ("foreign_keys", inspector.get_foreign_keys, []),
                                ("indexes", inspector.get_indexes, []),
                                (
                                    "unique_constraints",
                                    inspector.get_unique_constraints,
                                    [],
                                ),
                                (
                                    "check_constraints",
                                    inspector.get_check_constraints,
                                    [],
                                ),
                                ("comment", inspector.get_table_comment, {}),
                            ]:
                                try:
                                    value = method(name, schema=schema)
                                    if key == "columns":
                                        # TypeEngine.__str__ omits PostgreSQL's timezone
                                        # flag; dialect compilation preserves it.
                                        value = [
                                            {
                                                **column,
                                                "type": str(
                                                    column["type"].compile(
                                                        dialect=sync_db.dialect
                                                    )
                                                ),
                                            }
                                            for column in value
                                        ]
                                    info[key] = _plain(value)
                                except Exception as exc:
                                    info[key] = fallback
                                    warnings.append(
                                        f"Could not inspect {schema}.{name} {key}: {exc}"
                                    )
                            objects.append(obj)

                await db.run_sync(collect)
        by_name = {(o["schema"], o["name"]): o for o in objects}
        for obj in objects:
            for fk in obj["definition"].get("foreign_keys", []):
                target = by_name.get(
                    (
                        fk.get("referred_schema") or obj["schema"],
                        fk.get("referred_table"),
                    )
                )
                if target:
                    relationships.append(
                        {
                            "source": [obj["schema"], obj["name"]],
                            "target": [target["schema"], target["name"]],
                            "columns": list(
                                zip(
                                    fk.get("constrained_columns", []),
                                    fk.get("referred_columns", []),
                                )
                            ),
                            "declared": True,
                        }
                    )
        return objects, relationships, version, warnings
    finally:
        await engine.dispose()


def _mongo_client(connection: Connection):
    from pymongo import MongoClient

    user = quote_plus(connection.username)
    password = quote_plus(decrypt_password(connection.password_encrypted))
    credentials = f"{user}:{password}@" if connection.username else ""
    uri = f"mongodb://{credentials}{connection.host}:{connection.port}/?authSource={quote_plus(connection.auth_source or connection.database)}"
    return MongoClient(uri, serverSelectionTimeoutMS=5000)


def _mongo_test_sync(connection: Connection) -> dict:
    client = _mongo_client(connection)
    try:
        info = client.admin.command("buildInfo")
        return {
            "connected": True,
            "engine_version": info.get("version", ""),
            "schema": True,
            "query_plan": False,
            "workload_statistics": False,
        }
    finally:
        client.close()


def _mongo_scan_sync(connection: Connection):
    client = _mongo_client(connection)
    objects, warnings = [], []
    try:
        database = client[connection.database]
        version = client.admin.command("buildInfo").get("version", "")
        for meta in database.list_collections():
            name = meta["name"]
            if connection.schema_scope and name not in connection.schema_scope:
                continue
            try:
                samples = list(database[name].find({}, limit=100))
                fields = {}
                present_counts = Counter()
                for document in samples:
                    for key, value in document.items():
                        fields.setdefault(key, Counter())[type(value).__name__] += 1
                        present_counts[key] += 1
                columns = [
                    {
                        "name": key,
                        "type": ", ".join(sorted(types)),
                        "observed_types": dict(types),
                        "observed_in": present_counts[key],
                        "missing_in_sample": len(samples) - present_counts[key],
                        "nullable": key
                        not in meta.get("options", {})
                        .get("validator", {})
                        .get("$jsonSchema", {})
                        .get("required", []),
                    }
                    for key, types in fields.items()
                ]
                indexes = _plain(list(database[name].list_indexes()))
                objects.append(
                    {
                        "kind": "collection",
                        "schema": connection.database,
                        "name": name,
                        "definition": {
                            "columns": columns,
                            "indexes": indexes,
                            "validator": _plain(
                                meta.get("options", {}).get("validator", {})
                            ),
                            "sample_size": len(samples),
                            "primary_key": {},
                            "foreign_keys": [],
                        },
                    }
                )
            except Exception as exc:
                warnings.append(f"Could not inspect collection {name}: {exc}")
        return objects, [], version, warnings
    finally:
        client.close()


async def test_connection(connection: Connection) -> dict:
    if connection.engine == "mongodb":
        return await asyncio.to_thread(_mongo_test_sync, connection)
    return await sql_test(connection)


async def scan_connection(connection: Connection):
    if connection.engine == "mongodb":
        return await asyncio.to_thread(_mongo_scan_sync, connection)
    return await sql_scan(connection)


async def estimated_plan(connection: Connection, sql: str):
    if connection.engine not in ("postgresql", "mariadb"):
        raise ValueError("Query plans are unavailable for this engine")
    engine = create_async_engine(
        connection_url(connection), pool_size=1, max_overflow=0,
        connect_args=connection_args(connection),
    )
    try:
        async with asyncio.timeout(8):
            async with engine.connect() as db:
                if connection.engine == "postgresql":
                    await db.execute(text("SET statement_timeout = 5000"))
                    rows = (
                        await db.execute(text("EXPLAIN (FORMAT JSON) " + sql))
                    ).all()
                else:
                    rows = (await db.execute(text("EXPLAIN " + sql))).mappings().all()
                return _plain(
                    [list(row) for row in rows]
                    if connection.engine == "postgresql"
                    else [dict(row) for row in rows]
                )
    finally:
        await engine.dispose()
