import asyncio
import hashlib
import json
import re
import sqlite3
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlglot import exp, parse

from .agent import answer, draft_sql
from .analysis import (
    evaluate_object,
    index_candidates,
    normalization_report,
    profile_rows,
    simple_suitability,
    validate_read_query,
)
from .config import ai_enabled, settings
from .connectors import connection_args, connection_url, estimated_plan, scan_connection
from .database import (
    Artifact,
    AuditEvent,
    BusinessRule,
    Connection,
    ContextDocument,
    ExperimentRecord,
    Finding,
    Message,
    Relationship,
    Run,
    RunEvent,
    Sandbox,
    Schedule,
    SchemaObject,
    SessionLocal,
    Snapshot,
    utcnow,
)
from .diff import compare_snapshots
from .error_messages import describe_error


async def add_event(run_id: str, event_type: str, payload: dict):
    async with SessionLocal() as db:
        latest = (
            await db.scalar(
                select(func.max(RunEvent.sequence)).where(RunEvent.run_id == run_id)
            )
            or 0
        )
        db.add(
            RunEvent(
                run_id=run_id,
                sequence=latest + 1,
                event_type=event_type,
                payload=payload,
            )
        )
        await db.commit()


async def stage(run_id: str, value: str):
    async with SessionLocal() as db:
        run = await db.get(Run, run_id)
        run.stage = value
        await db.commit()
    await add_event(run_id, "stage.changed", {"stage": value})


async def artifact(
    run_id: str,
    kind: str,
    title: str,
    content: dict,
    markdown: str,
    connection_id=None,
    snapshot_id=None,
):
    async with SessionLocal() as db:
        item = Artifact(
            run_id=run_id,
            kind=kind,
            title=title,
            content=content,
            markdown=markdown,
            connection_id=connection_id,
            snapshot_id=snapshot_id,
        )
        db.add(item)
        await db.commit()
        await db.refresh(item)
        artifact_id = item.id
    await add_event(
        run_id, "artifact.created", {"artifact_id": artifact_id, "kind": kind}
    )
    return artifact_id


async def _connection(connection_id: str) -> Connection:
    async with SessionLocal() as db:
        item = await db.get(Connection, connection_id)
        if not item or item.archived:
            raise ValueError("Connection not found")
        return item


async def _snapshot(snapshot_id: str):
    async with SessionLocal() as db:
        snapshot = await db.get(Snapshot, snapshot_id)
        if not snapshot:
            raise ValueError("Snapshot not found")
        objects = (
            await db.scalars(
                select(SchemaObject).where(SchemaObject.snapshot_id == snapshot_id)
            )
        ).all()
        findings = (
            await db.scalars(select(Finding).where(Finding.snapshot_id == snapshot_id))
        ).all()
        return snapshot, objects, findings


async def _scan(run_id: str, data: dict):
    connection = await _connection(data["connection_id"])
    await stage(run_id, "inspecting database")
    objects, edges, version, warnings = await scan_connection(connection)
    payload = json.dumps(objects, sort_keys=True, default=str)
    schema_hash = hashlib.sha256(payload.encode()).hexdigest()
    async with SessionLocal() as db:
        snap = Snapshot(
            connection_id=connection.id,
            schema_hash=schema_hash,
            status="partial" if warnings else "complete",
            coverage={"warnings": warnings, "object_count": len(objects), "schema_scope": connection.schema_scope, "source": "live_metadata_scan", "engine": connection.engine},
            engine_version=version,
        )
        db.add(snap)
        await db.flush()
        names = {}
        for item in objects:
            row = SchemaObject(
                snapshot_id=snap.id,
                kind=item["kind"],
                schema_name=item["schema"],
                name=item["name"],
                definition=item["definition"],
            )
            db.add(row)
            await db.flush()
            names[(row.schema_name, row.name)] = row.id
        for edge in edges:
            source, target = (
                names.get(tuple(edge["source"])),
                names.get(tuple(edge["target"])),
            )
            if source and target:
                db.add(
                    Relationship(
                        snapshot_id=snap.id,
                        source_object_id=source,
                        target_object_id=target,
                        columns=edge["columns"],
                        declared=edge["declared"],
                    )
                )
        await db.commit()
        return {
            "snapshot_id": snap.id,
            "object_count": len(objects),
            "warnings": warnings,
        }


async def _analyze(run_id: str, data: dict):
    snap, objects, _ = await _snapshot(data["snapshot_id"])
    connection = await _connection(snap.connection_id)
    selected = set(data.get("options", {}).get("rule_ids") or [])
    await stage(run_id, "evaluating schema rules")
    findings = [
        f
        for obj in objects
        for f in evaluate_object(obj, connection.engine)
        if not selected or f["rule_id"] in selected
    ]
    async with SessionLocal() as db:
        existing = (
            await db.scalars(select(Finding).where(Finding.snapshot_id == snap.id))
        ).all()
        keys = {(f.rule_id, tuple(f.object_ids), f.observation) for f in existing}
        added = 0
        for f in findings:
            key = (f["rule_id"], tuple(f["object_ids"]), f["observation"])
            if key not in keys:
                db.add(Finding(snapshot_id=snap.id, **f))
                added += 1
        await db.commit()
    return {
        "snapshot_id": snap.id,
        "finding_count": len(findings),
        "new_findings": added,
    }


async def _document(run_id: str, data: dict):
    snap, objects, findings = await _snapshot(data["snapshot_id"])
    selection = set(data.get("object_ids") or [])
    if selection:
        objects = [o for o in objects if o.id in selection]
    lines = [
        "# Database structure",
        "",
        f"Snapshot: `{snap.id}`",
        f"Captured: {snap.created_at.isoformat()}",
        "",
    ]
    for obj in sorted(objects, key=lambda x: (x.schema_name, x.name)):
        d = obj.definition
        lines += [
            f"## {obj.schema_name}.{obj.name}",
            "",
            f"Kind: {obj.kind}",
            "",
            "| Column | Type | Nullable |",
            "|---|---|---|",
        ]
        lines += [
            f"| `{c.get('name')}` | `{c.get('type')}` | {'yes' if c.get('nullable') else 'no'} |"
            for c in d.get("columns", [])
        ]
        pk = d.get("primary_key", {}).get("constrained_columns") or []
        lines += ["", f"Primary key: {', '.join(pk) if pk else 'none declared'}", ""]
        for fk in d.get("foreign_keys") or []:
            lines.append(
                f"- Foreign key: {', '.join(fk.get('constrained_columns') or [])} → {fk.get('referred_table')}"
            )
        lines.append("")
    related = [
        f
        for f in findings
        if not selection or any(i in selection for i in f.object_ids)
    ]
    lines += ["## Findings", ""] + [
        f"- **{f.severity}**: {f.observation}" for f in related
    ]
    markdown = "\n".join(lines)
    aid = await artifact(
        run_id,
        "documentation",
        "Database structure",
        {"object_ids": [o.id for o in objects], "finding_ids": [f.id for f in related]},
        markdown,
        snap.connection_id,
        snap.id,
    )
    return {"artifact_id": aid}


async def _query_draft(run_id: str, data: dict):
    snap, objects, _ = await _snapshot(data["snapshot_id"])
    connection = await _connection(snap.connection_id)
    if connection.engine == "mongodb":
        raise ValueError("SQL drafting is unavailable for MongoDB collections")
    question = data.get("question", "")
    if not question:
        raise ValueError("A question is required")
    selected = [o for o in objects if o.id in data.get("object_ids", [])]
    if not selected:
        selected = objects[:25]
    if not selected:
        raise ValueError("Snapshot has no tables")
    obj = selected[0]
    marker = "`" if connection.engine == "mariadb" else '"'
    quote = lambda name: marker + name.replace(marker, marker + marker) + marker
    sql = f"SELECT * FROM {quote(obj.schema_name)}.{quote(obj.name)} LIMIT 100"
    explanation = "A starter query for the selected table."
    assumptions = ["The selected object is the intended data source."]
    if ai_enabled():
        generated = await draft_sql(question, connection.engine, selected)
        if generated:
            sql = generated["sql"]
            explanation = generated["explanation"]
            assumptions = generated["assumptions"]
    aid = await artifact(
        run_id,
        "query_draft",
        "SQL draft",
        {
            "sql": sql,
            "question": question,
            "explanation": explanation,
            "assumptions": assumptions,
        },
        f"# SQL draft\n\nQuestion: {question}\n\n```sql\n{sql}\n```\n\n{explanation}\n\nReview the query before execution.",
        snap.connection_id,
        snap.id,
    )
    return {"artifact_id": aid, "sql": sql}


async def _chat(run_id: str, data: dict):
    snap, _, _ = await _snapshot(data["snapshot_id"])

    async def emit(name, payload):
        await add_event(run_id, name, payload)

    response, evidence = await answer(
        data["question"], snap.id, snap.connection_id, data.get("object_ids", []), emit
    )
    async with SessionLocal() as db:
        message = Message(
            conversation_id=data["conversation_id"],
            role="assistant",
            content=response,
            snapshot_id=snap.id,
            run_id=run_id,
            evidence=evidence,
        )
        db.add(message)
        await db.commit()
        await db.refresh(message)
        return {"message_id": message.id, "evidence": evidence}


async def _live_read(run_id: str, data: dict, mode: str):
    connection = await _connection(data["connection_id"])
    if connection.engine == "mongodb":
        raise ValueError("SQL operations are unavailable for MongoDB")
    dialect = "postgres" if connection.engine == "postgresql" else "mysql"
    sql = validate_read_query(data["sql"], dialect)
    if mode == "query_plan":
        result = await estimated_plan(connection, sql)
        aid = await artifact(
            run_id,
            "query_plan",
            "Estimated query plan",
            {"plan": result, "sql": sql},
            "# Estimated query plan\n\n```json\n"
            + json.dumps(result, indent=2)
            + "\n```",
            connection.id,
        )
        return {"artifact_id": aid}
    engine = create_async_engine(
        connection_url(connection), pool_size=1, max_overflow=0,
        connect_args=connection_args(connection),
    )
    try:
        async with asyncio.timeout(8):
            async with engine.connect() as db:
                if connection.engine == "postgresql":
                    await db.execute(text("SET TRANSACTION READ ONLY"))
                    await db.execute(text("SET LOCAL statement_timeout = 5000"))
                result = await db.execute(text(sql), data.get("parameters") or {})
                columns = list(result.keys())
                rows, used, truncated = [], 0, False
                while True:
                    values = result.fetchone()
                    if values is None:
                        break
                    if len(rows) >= 500:
                        truncated = True
                        break
                    encoded = json.dumps(dict(zip(columns, values)), default=str, ensure_ascii=False)
                    size = len(encoded.encode("utf-8"))
                    if used + size > 1_000_000:
                        truncated = True
                        break
                    rows.append(json.loads(encoded))
                    used += size
        aid = await artifact(
            run_id,
            "query_result",
            "Query result",
            {"columns": columns, "rows": rows, "truncated": truncated, "sql": sql, "response_bytes": used},
            "# Query result\n\nReturned up to 500 rows.",
            connection.id,
        )
        return {"artifact_id": aid, "row_count": len(rows), "truncated": truncated}
    finally:
        await engine.dispose()


async def _profile(run_id: str, data: dict):
    snap, objects, _ = await _snapshot(data["snapshot_id"])
    connection = await _connection(snap.connection_id)
    if connection.engine == "mongodb":
        selected = set(data.get("object_ids") or [])
        profiles = [
            {
                "object_id": obj.id,
                "sample_size": obj.definition.get("sample_size", 0),
                "method": "bounded observed-shape sample saved in the snapshot",
                "columns": {
                    col.get("name"): {"observed_types": col.get("observed_types", {}), "observed_in": col.get("observed_in", 0), "missing_in_sample": col.get("missing_in_sample", 0), "nullable_from_validator": col.get("nullable", True)}
                    for col in obj.definition.get("columns", [])
                },
            }
            for obj in objects
            if obj.id in selected and obj.kind == "collection"
        ][:10]
        if not profiles:
            raise ValueError("Select at least one collection")
        aid = await artifact(
            run_id,
            "profile",
            "MongoDB observed shapes",
            {"profiles": profiles},
            "# MongoDB observed shapes\n\nThis profile describes the bounded documents sampled during the schema scan; it is not a full collection census.\n\n```json\n" + json.dumps(profiles, indent=2) + "\n```",
            snap.connection_id,
            snap.id,
        )
        return {"artifact_id": aid, "collections": len(profiles)}
    wanted = set(data.get("object_ids") or [])
    objects = [o for o in objects if o.id in wanted and o.kind == "table"][:10]
    if not objects:
        raise ValueError("Select at least one table")
    engine = create_async_engine(
        connection_url(connection), pool_size=1, max_overflow=0,
        connect_args=connection_args(connection),
    )
    profiles = []
    try:
        async with asyncio.timeout(30):
            async with engine.connect() as db:
                if connection.engine == "postgresql":
                    await db.execute(text("SET statement_timeout = 5000"))
                preparer = engine.dialect.identifier_preparer
                for obj in objects:
                    fields = [c["name"] for c in obj.definition.get("columns", [])][:30]
                    if not fields:
                        continue
                    query = (
                        "SELECT "
                        + ", ".join(preparer.quote(f) for f in fields)
                        + " FROM "
                        + preparer.quote(obj.schema_name)
                        + "."
                        + preparer.quote(obj.name)
                        + " LIMIT 1000"
                    )
                    result = await db.execute(text(query))
                    rows = [dict(zip(fields, row)) for row in result.fetchall()]
                    profiles.append(
                        {
                            "object_id": obj.id,
                            "sample_size": len(rows),
                            "method": "first 1000 rows, not random",
                            "columns": profile_rows(rows, fields),
                        }
                    )
        aid = await artifact(
            run_id,
            "profile",
            "Data profile",
            {"profiles": profiles},
            "# Data profile\n\nThis profile uses the first 1,000 rows per selected table. Interpret distributions as sample observations.",
            snap.connection_id,
            snap.id,
        )
        return {"artifact_id": aid, "tables": len(profiles)}
    finally:
        await engine.dispose()


async def _normalization(run_id: str, data: dict):
    snap, objects, _ = await _snapshot(data["snapshot_id"])
    obj = next((o for o in objects if o.id == data.get("object_id")), None)
    if not obj:
        raise ValueError("Choose a table from this snapshot")
    async with SessionLocal() as db:
        rules = (
            await db.scalars(
                select(BusinessRule).where(
                    BusinessRule.connection_id == snap.connection_id,
                    BusinessRule.status == "confirmed",
                )
            )
        ).all()
    deps = [
        r.dependency
        for r in rules
        if r.dependency and r.object_name in (obj.name, f"{obj.schema_name}.{obj.name}")
    ]
    deps += data.get("dependencies", [])
    columns = [c["name"] for c in obj.definition.get("columns", [])]
    report = normalization_report(columns, deps, data.get("requested_level", "3NF"))
    markdown = f"# Normalization: {obj.schema_name}.{obj.name}\n\nStatus: **{report['status']}**\n\nThe assessment uses only confirmed or supplied dependencies; it is not a proof about unrecorded business rules.\n\n"
    if report.get("candidate_keys"):
        markdown += "## Candidate keys under supplied dependencies\n\n" + "\n".join(f"- {', '.join(key)}" for key in report["candidate_keys"]) + "\n\n"
    for violation in report.get("violations", report.get("functional_dependency_violations", [])):
        markdown += f"- **Potential violation:** {', '.join(violation['determinant'])} → {', '.join(violation['dependent'])}. {violation['reason']}\n"
    if report.get("explanation"):
        markdown += f"\n{report['explanation']}\n"
    markdown += "\n## Structured analysis\n\n```json\n" + json.dumps(report, indent=2) + "\n```"
    aid = await artifact(
        run_id,
        "normalization",
        f"Normalization: {obj.name}",
        report,
        markdown,
        snap.connection_id,
        snap.id,
    )
    return {"artifact_id": aid, "status": report["status"]}


async def _suitability(run_id: str, data: dict):
    snap, _, _ = await _snapshot(data["snapshot_id"])
    connection = await _connection(snap.connection_id)
    report = simple_suitability(data.get("workload_description", ""), connection.engine)
    aid = await artifact(
        run_id,
        "suitability",
        "Database suitability",
        report,
        "# Database suitability\n\n"
        + "\n".join(
            f"- **{o['option']}**: {o['benefit']} Tradeoff: {o['tradeoff']}"
            for o in report["options"]
        )
        + "\n\nThis is provisional until workload and migration evidence are supplied.",
        connection.id,
        snap.id,
    )
    return {"artifact_id": aid}


async def _schema_diff(run_id: str, data: dict):
    before, old, _ = await _snapshot(data["before_snapshot_id"])
    after, new, _ = await _snapshot(data["after_snapshot_id"])
    if before.connection_id != after.connection_id:
        raise ValueError("Snapshots must belong to the same connection")
    report = compare_snapshots(old, new)
    report["before_snapshot_id"] = before.id
    report["after_snapshot_id"] = after.id
    aid = await artifact(
        run_id,
        "schema_diff",
        "Schema differences",
        report,
        "# Schema differences\n\n```json\n" + json.dumps(report, indent=2) + "\n```",
        after.connection_id,
        after.id,
    )
    return {"artifact_id": aid}


async def _synthesis(run_id: str, data: dict, kind: str):
    snap, objects, findings = await _snapshot(data["snapshot_id"])
    connection = await _connection(snap.connection_id)
    if kind == "redesign":
        selected = set(data.get("finding_ids") or [])
        items = [f for f in findings if not selected or f.id in selected]
        report = {
            "proposals": [
                {
                    "finding_id": f.id,
                    "current_observation": f.observation,
                    "change_to_review": f.recommendation,
                }
                for f in items
            ],
            "note": "These are design proposals, not applied changes.",
        }
    else:
        target = data.get("target_engine", "postgresql")
        tables = [obj for obj in objects if obj.kind in ("table", "collection")]
        inventory = [
            {
                "source": f"{obj.schema_name}.{obj.name}",
                "kind": obj.kind,
                "columns": len(obj.definition.get("columns") or []),
                "has_primary_key": bool((obj.definition.get("primary_key") or {}).get("constrained_columns")),
                "foreign_keys": len(obj.definition.get("foreign_keys") or []),
                "indexes": len(obj.definition.get("indexes") or []),
                "types_to_review": sorted({str(col.get("type", "unknown")) for col in obj.definition.get("columns") or []}),
            }
            for obj in tables
        ]
        report = {
            "source_engine": connection.engine,
            "target_engine": target,
            "objects": len(objects),
            "inventory": inventory,
            "status": "draft",
            "review_items": [
                "Compare column types and defaults",
                "Review indexes, stored routines, and application SQL",
                "Plan data copy and validation",
                "Measure representative workload on both engines",
                "Create a backup and tested rollback procedure before any change",
            ],
            "unsupported_automatic_conversion": target != connection.engine,
            "automatic_change_applied": False,
        }
    markdown = f"# {kind.replace('_', ' ').title()}\n\n```json\n{json.dumps(report, indent=2)}\n```"
    aid = await artifact(
        run_id,
        kind,
        kind.replace("_", " ").title(),
        report,
        markdown,
        connection.id,
        snap.id,
    )
    return {"artifact_id": aid}


async def _infer_relationships(run_id: str, data: dict):
    snap, objects, _ = await _snapshot(data["snapshot_id"])
    tables = [o for o in objects if o.kind == "table"]
    by_name = {o.name.lower(): o for o in tables}
    suggestions = []
    async with SessionLocal() as db:
        present = (
            await db.scalars(
                select(Relationship).where(Relationship.snapshot_id == snap.id)
            )
        ).all()
        existing = {
            (r.source_object_id, r.target_object_id, tuple(str(c) for c in r.columns))
            for r in present
        }
        for source in tables:
            for col in source.definition.get("columns", []):
                name = col.get("name", "")
                if not name.endswith("_id"):
                    continue
                stem = name[:-3].lower()
                target = (
                    by_name.get(stem)
                    or by_name.get(stem + "s")
                    or by_name.get(stem + "es")
                )
                if not target or target.id == source.id:
                    continue
                pk = (target.definition.get("primary_key") or {}).get(
                    "constrained_columns"
                ) or []
                if len(pk) != 1:
                    continue
                target_col = next((item for item in target.definition.get("columns", []) if item.get("name") == pk[0]), None)
                source_type = str(col.get("type", "")).lower().split("(")[0]
                target_type = str((target_col or {}).get("type", "")).lower().split("(")[0]
                compatible = source_type == target_type or (
                    "int" in source_type and "int" in target_type
                )
                if not compatible:
                    continue
                mapping = [name, pk[0]]
                fingerprint = (source.id, target.id, (str(mapping),))
                if fingerprint in existing:
                    continue
                db.add(
                    Relationship(
                        snapshot_id=snap.id,
                        source_object_id=source.id,
                        target_object_id=target.id,
                        columns=[mapping],
                        declared=False,
                    )
                )
                suggestions.append(
                    {
                        "source": f"{source.schema_name}.{source.name}",
                        "column": name,
                        "target": f"{target.schema_name}.{target.name}",
                        "target_column": pk[0],
                        "confidence": "candidate",
                        "basis": ["column name matches a table", "target has a single-column primary key", "column types are compatible"],
                    }
                )
        await db.commit()
    aid = await artifact(
        run_id,
        "relationship_suggestions",
        "Possible relationships",
        {"suggestions": suggestions},
        "# Possible relationships\n\n"
        + "\n".join(
            f"- {s['source']}.{s['column']} → {s['target']}.{s['target_column']} (inferred from names)"
            for s in suggestions
        ),
        snap.connection_id,
        snap.id,
    )
    return {"artifact_id": aid, "suggestion_count": len(suggestions)}


async def _workload_analysis(run_id: str, data: dict):
    connection = await _connection(data["connection_id"])
    schema_objects = []
    if data.get("snapshot_id"):
        snapshot, schema_objects, _ = await _snapshot(data["snapshot_id"])
        if snapshot.connection_id != connection.id:
            raise ValueError("Workload snapshot must belong to the selected connection")
    if data.get("document_id"):
        async with SessionLocal() as db:
            document = await db.get(ContextDocument, data["document_id"])
        if not document or document.connection_id != connection.id:
            raise ValueError("Workload document does not belong to this connection")
        dialect = "postgres" if connection.engine == "postgresql" else "mysql"
        counts = {}
        source_queries = []
        for statement in parse(document.content, read=dialect):
            if statement is None:
                continue
            source_queries.append({"query": statement.sql(dialect=dialect), "calls": 1})
            fingerprint = statement.transform(
                lambda node: exp.Placeholder() if isinstance(node, exp.Literal) else node
            ).sql(dialect=dialect)
            counts[fingerprint] = counts.get(fingerprint, 0) + 1
        rows = [
            {"fingerprint": query, "calls": count}
            for query, count in sorted(counts.items(), key=lambda item: -item[1])[:100]
        ]
        candidates = index_candidates(source_queries, schema_objects, dialect)
        aid = await artifact(
            run_id,
            "workload",
            "Uploaded query workload",
            {"queries": rows, "index_candidates": candidates, "source_document_id": document.id, "method": "SQL literal fingerprinting"},
            "# Uploaded query workload\n\nStatements are grouped by structure; literal values are masked.\n\n"
            + "\n".join(f"- {row['calls']} × `{row['fingerprint']}`" for row in rows)
            + "\n\n## Index candidates to review\n\n"
            + ("\n".join(f"- `{item['table']}.{item['column']}` appeared in {item['observed_calls']} equality predicates; validate selectivity and write cost." for item in candidates) or "No conservative candidates found."),
            connection.id,
            data.get("snapshot_id"),
        )
        return {"artifact_id": aid, "query_count": len(rows)}
    if connection.engine != "postgresql":
        raise ValueError("Live workload statistics require PostgreSQL; upload a SQL workload file for this engine")
    engine = create_async_engine(
        connection_url(connection), pool_size=1, max_overflow=0,
        connect_args=connection_args(connection),
    )
    try:
        async with asyncio.timeout(8):
            async with engine.connect() as db:
                await db.execute(text("SET statement_timeout = 5000"))
                available = await db.scalar(
                    text(
                        "SELECT EXISTS (SELECT 1 FROM pg_extension WHERE extname='pg_stat_statements')"
                    )
                )
                if not available:
                    raise ValueError(
                        "pg_stat_statements is not installed on the target database"
                    )
                result = await db.execute(
                    text(
                        "SELECT query, calls, total_exec_time, mean_exec_time FROM pg_stat_statements ORDER BY total_exec_time DESC LIMIT 20"
                    )
                )
                rows = json.loads(
                    json.dumps(
                        [dict(row) for row in result.mappings().all()], default=str
                    )
                )
        candidates = index_candidates(rows, schema_objects, "postgres")
        aid = await artifact(
            run_id,
            "workload",
            "Query workload",
            {"queries": rows, "index_candidates": candidates},
            "# Query workload\n\nTop statements by cumulative execution time. Query text may contain application details.\n\n## Index candidates to review\n\n"
            + ("\n".join(f"- `{item['table']}.{item['column']}`: {item['observed_calls']} calls. Validate with a plan and sandbox experiment." for item in candidates) or "No conservative candidates found."),
            connection.id,
            data.get("snapshot_id"),
        )
        return {"artifact_id": aid, "query_count": len(rows)}
    finally:
        await engine.dispose()


async def _import_schema(run_id: str, data: dict):
    async with SessionLocal() as db:
        document = await db.get(ContextDocument, data["document_id"])
        if not document:
            raise ValueError("Uploaded document not found")
        connection = await db.get(Connection, document.connection_id)
    dialect = data.get("dialect", "postgres")
    statements = parse(document.content, read=dialect)
    objects = []
    for statement in statements:
        if (
            not isinstance(statement, exp.Create)
            or statement.args.get("kind", "").upper() != "TABLE"
        ):
            continue
        schema_expr = statement.this
        table = schema_expr.this if isinstance(schema_expr, exp.Schema) else schema_expr
        if not isinstance(table, exp.Table):
            continue
        cols = []
        primary_key = []
        if isinstance(schema_expr, exp.Schema):
            for item in schema_expr.expressions:
                if isinstance(item, exp.ColumnDef):
                    constraints = [
                        c.args.get("kind") or c
                        for c in item.args.get("constraints") or []
                    ]
                    if any(
                        isinstance(c, exp.PrimaryKeyColumnConstraint)
                        for c in constraints
                    ):
                        primary_key.append(item.this.name)
                    cols.append(
                        {
                            "name": item.this.name,
                            "type": item.args.get("kind").sql(dialect=dialect)
                            if item.args.get("kind")
                            else "unknown",
                            "nullable": not any(
                                isinstance(
                                    c,
                                    (
                                        exp.NotNullColumnConstraint,
                                        exp.PrimaryKeyColumnConstraint,
                                    ),
                                )
                                for c in constraints
                            ),
                        }
                    )
                elif isinstance(item, exp.PrimaryKey):
                    primary_key = [column.name for column in item.expressions]
        objects.append(
            {
                "kind": "table",
                "schema": table.db or "public",
                "name": table.name,
                "definition": {
                    "columns": cols,
                    "primary_key": {"constrained_columns": primary_key},
                    "foreign_keys": [],
                    "indexes": [],
                    "unique_constraints": [],
                    "check_constraints": [],
                    "comment": {},
                },
            }
        )
    if not objects:
        raise ValueError("No CREATE TABLE statements were found")
    digest = hashlib.sha256(document.content.encode()).hexdigest()
    async with SessionLocal() as db:
        snap = Snapshot(
            connection_id=connection.id,
            schema_hash=digest,
            status="partial",
            coverage={
                "object_count": len(objects),
                "warnings": ["Imported DDL has no live data or workload evidence"],
            },
            engine_version=f"offline {dialect}",
        )
        db.add(snap)
        await db.flush()
        for item in objects:
            db.add(
                SchemaObject(
                    snapshot_id=snap.id,
                    kind=item["kind"],
                    schema_name=item["schema"],
                    name=item["name"],
                    definition=item["definition"],
                )
            )
        await db.commit()
    return {"snapshot_id": snap.id, "object_count": len(objects)}


async def _analyze_context(run_id: str, data: dict):
    snap, objects, _ = await _snapshot(data["snapshot_id"])
    ids = data.get("document_ids") or []
    async with SessionLocal() as db:
        documents = (
            await db.scalars(
                select(ContextDocument).where(
                    ContextDocument.id.in_(ids),
                    ContextDocument.connection_id == snap.connection_id,
                )
            )
        ).all()
    references = []
    for document in documents:
        matches = []
        for line_number, line in enumerate(document.content.splitlines(), start=1):
            for obj in objects:
                if re.search(r"\b" + re.escape(obj.name) + r"\b", line, re.IGNORECASE):
                    matches.append({"object_id": obj.id, "object_name": f"{obj.schema_name}.{obj.name}", "line": line_number, "excerpt": line.strip()[:240]})
            if len(matches) >= 100:
                break
        references.append(
            {
                "document_id": document.id,
                "filename": document.filename,
                "matches": matches[:100],
                "basis": "textual name match with source line; not a confirmed business rule",
            }
        )
    aid = await artifact(
        run_id,
        "context_analysis",
        "Source context",
        {"documents": references},
        "# Source context\n\nObject references are simple text matches. Review code and migrations before relying on them.\n\n```json\n"
        + json.dumps(references, indent=2)
        + "\n```",
        snap.connection_id,
        snap.id,
    )
    return {"artifact_id": aid}


def _sqlite_experiment(columns: list[dict], chosen: str, rows: int):
    db = sqlite3.connect(":memory:")
    try:
        names = [c["name"] for c in columns][:30]
        quoted = lambda value: '"' + value.replace('"', '""') + '"'
        definitions = ", ".join(
            f"{quoted(name)} {'INTEGER' if name == chosen else 'TEXT'}"
            for name in names
        )
        db.execute(f"CREATE TABLE sample ({definitions})")
        placeholders = ", ".join("?" for _ in names)
        db.executemany(
            f"INSERT INTO sample VALUES ({placeholders})",
            [
                [i % 100 if name == chosen else f"value-{i % 30}" for name in names]
                for i in range(rows)
            ],
        )
        query = f"SELECT * FROM sample WHERE {quoted(chosen)} = ?"
        before_result = db.execute(query, (42,)).fetchall()
        start = time.perf_counter()
        for _ in range(30):
            db.execute(query, (42,)).fetchall()
        before_ms = (time.perf_counter() - start) * 1000 / 30
        db.execute(f"CREATE INDEX sample_idx ON sample ({quoted(chosen)})")
        after_result = db.execute(query, (42,)).fetchall()
        start = time.perf_counter()
        for _ in range(30):
            db.execute(query, (42,)).fetchall()
        after_ms = (time.perf_counter() - start) * 1000 / 30
        return {
            "engine": "SQLite in-memory synthetic sandbox",
            "synthetic_rows": rows,
            "filter_column": chosen,
            "before_ms": round(before_ms, 4),
            "after_ms": round(after_ms, 4),
            "same_results": before_result == after_result,
            "limitation": "This experiment does not predict performance on the target database.",
        }
    finally:
        db.close()


async def _postgres_experiment(sandbox: Sandbox, rows: int) -> dict:
    connection = await _connection(sandbox.connection_id)
    if connection.engine != "postgresql":
        raise ValueError("The selected sandbox must use PostgreSQL")
    engine = create_async_engine(connection_url(connection), pool_size=1, max_overflow=0, connect_args=connection_args(connection))
    try:
        async with asyncio.timeout(30):
            async with engine.begin() as db:
                await db.execute(text("SET LOCAL statement_timeout = 10000"))
                await db.execute(text("CREATE TEMP TABLE dbh_experiment_sample (filter_value INTEGER, payload TEXT) ON COMMIT DROP"))
                await db.execute(text("INSERT INTO dbh_experiment_sample SELECT g % 100, 'synthetic-' || g::text FROM generate_series(1, :rows) AS g"), {"rows": rows})
                query = "SELECT * FROM dbh_experiment_sample WHERE filter_value = 42"
                before_raw = (await db.execute(text("EXPLAIN (ANALYZE, FORMAT JSON) " + query))).scalar_one()
                before = (json.loads(before_raw) if isinstance(before_raw, str) else before_raw)[0]
                await db.execute(text("CREATE INDEX dbh_experiment_filter_idx ON dbh_experiment_sample (filter_value)"))
                await db.execute(text("ANALYZE dbh_experiment_sample"))
                after_raw = (await db.execute(text("EXPLAIN (ANALYZE, FORMAT JSON) " + query))).scalar_one()
                after = (json.loads(after_raw) if isinstance(after_raw, str) else after_raw)[0]
                matches = await db.scalar(text("SELECT count(*) FROM dbh_experiment_sample WHERE filter_value = 42"))
        return {
            "engine": "PostgreSQL temporary synthetic sandbox",
            "synthetic_rows": rows,
            "matching_rows": matches,
            "before_ms": before.get("Execution Time"),
            "after_ms": after.get("Execution Time"),
            "before_plan": before.get("Plan", {}).get("Node Type"),
            "after_plan": after.get("Plan", {}).get("Node Type"),
            "same_results": True,
            "limitation": "Synthetic data on the selected engine is not a benchmark of the source table or workload.",
        }
    finally:
        await engine.dispose()


async def _experiment(run_id: str, data: dict):
    snap, objects, _ = await _snapshot(data["snapshot_id"])
    obj = next(
        (o for o in objects if o.id == data.get("object_id") and o.kind == "table"),
        None,
    )
    if not obj:
        raise ValueError("Choose a table for the synthetic experiment")
    column = data.get("column")
    fields = obj.definition.get("columns") or []
    if column not in [field["name"] for field in fields]:
        raise ValueError("Choose a column present in the selected table")
    count = min(max(int(data.get("row_count", 3000)), 100), 20000)
    if data.get("sandbox_id"):
        async with SessionLocal() as db:
            sandbox = await db.get(Sandbox, data["sandbox_id"])
        if not sandbox or sandbox.archived:
            raise ValueError("Sandbox not found")
        report = await _postgres_experiment(sandbox, count)
        report["sandbox_id"] = sandbox.id
    else:
        report = await asyncio.to_thread(_sqlite_experiment, fields, column, count)
    aid = await artifact(
        run_id,
        "experiment",
        "Index experiment",
        report,
        "# Index experiment\n\n```json\n"
        + json.dumps(report, indent=2)
        + "\n```",
        snap.connection_id,
        snap.id,
    )
    if data.get("sandbox_id"):
        async with SessionLocal() as db:
            db.add(ExperimentRecord(sandbox_id=data["sandbox_id"], run_id=run_id, artifact_id=aid, summary={"before_ms": report.get("before_ms"), "after_ms": report.get("after_ms"), "synthetic_rows": report.get("synthetic_rows")}))
            await db.commit()
    return {"artifact_id": aid}


async def _generate_demo_data(run_id: str, data: dict):
    snap, objects, _ = await _snapshot(data["snapshot_id"])
    obj = next(
        (o for o in objects if o.id == data.get("object_id") and o.kind == "table"),
        None,
    )
    if not obj:
        raise ValueError("Choose a table for demonstration data")
    count = min(max(int(data.get("row_count", 10)), 1), 100)
    columns = [
        c
        for c in obj.definition.get("columns", [])
        if c.get("name")
        not in (obj.definition.get("primary_key") or {}).get("constrained_columns", [])
    ]
    columns = [c for c in columns if not c.get("default")][:12]
    quote = lambda value: '"' + value.replace('"', '""') + '"'
    statements = []
    for index in range(count):
        values = []
        for column in columns:
            typ = str(column.get("type", "")).upper()
            if any(
                part in typ for part in ("INT", "NUMERIC", "DECIMAL", "FLOAT", "DOUBLE")
            ):
                values.append(str(index + 1))
            elif "BOOL" in typ:
                values.append("TRUE")
            elif "DATE" in typ or "TIME" in typ:
                values.append("'2026-01-01'")
            else:
                values.append(f"'demo-{index + 1}'")
        if columns:
            statements.append(
                f"INSERT INTO {quote(obj.schema_name)}.{quote(obj.name)} ({', '.join(quote(c['name']) for c in columns)}) VALUES ({', '.join(values)});"
            )
    sql = "\n".join(statements)
    report = {
        "sql": sql,
        "row_count": count,
        "limitation": "This is a draft. Foreign keys, unique constraints, and custom types may require edits.",
    }
    aid = await artifact(
        run_id,
        "demo_data",
        "Demonstration data draft",
        report,
        "# Demonstration data draft\n\nReview constraints before using this SQL.\n\n```sql\n"
        + sql
        + "\n```",
        snap.connection_id,
        snap.id,
    )
    return {"artifact_id": aid}


HANDLERS = {
    "scan": _scan,
    "analyze": _analyze,
    "document": _document,
    "query_draft": _query_draft,
    "chat": _chat,
    "profile": _profile,
    "normalization": _normalization,
    "suitability": _suitability,
    "schema_diff": _schema_diff,
    "infer_relationships": _infer_relationships,
    "workload_analysis": _workload_analysis,
    "import_schema": _import_schema,
    "analyze_context": _analyze_context,
    "experiment": _experiment,
    "generate_demo_data": _generate_demo_data,
}


def public_error(exc: Exception) -> str:
    return describe_error(exc).text()


async def execute_run(run_id: str):
    async with SessionLocal() as db:
        run = await db.get(Run, run_id)
        if not run or run.status != "queued":
            return
        run.status, run.stage = "running", "starting"
        await db.commit()
        kind, data = run.kind, run.input
    await add_event(run_id, "run.started", {"kind": kind})
    try:
        if kind in HANDLERS:
            result = await HANDLERS[kind](run_id, data)
        elif kind in ("query_plan", "query_execute"):
            result = await _live_read(run_id, data, kind)
        elif kind in ("redesign", "migration_proposal"):
            result = await _synthesis(run_id, data, kind)
        else:
            raise ValueError(f"Operation {kind} is not implemented")
        async with SessionLocal() as db:
            run = await db.get(Run, run_id)
            run.status, run.stage, run.result, run.finished_at = (
                "succeeded",
                "complete",
                result,
                datetime.now(timezone.utc),
            )
            db.add(AuditEvent(action="run.completed", resource_type="run", resource_id=run.id, actor="system_worker", details={"kind": kind}))
            await db.commit()
        await add_event(run_id, "run.completed", result)
    except asyncio.CancelledError:
        async with SessionLocal() as db:
            run = await db.get(Run, run_id)
            user_cancelled = run.status == "cancelling"
            run.status = "cancelled" if user_cancelled else "interrupted"
            run.stage = run.status
            run.finished_at = datetime.now(timezone.utc)
            await db.commit()
        await add_event(
            run_id, "run.cancelled" if user_cancelled else "run.interrupted", {}
        )
        raise
    except Exception as exc:
        message = public_error(exc)
        async with SessionLocal() as db:
            run = await db.get(Run, run_id)
            run.status, run.stage, run.error, run.finished_at = (
                "failed",
                "failed",
                message,
                datetime.now(timezone.utc),
            )
            db.add(AuditEvent(action="run.failed", resource_type="run", resource_id=run.id, actor="system_worker", details={"kind": kind}))
            await db.commit()
        await add_event(run_id, "run.failed", {"error": message})


class JobManager:
    def __init__(self):
        self.queue = asyncio.Queue()
        self.workers = []
        self.active = {}
        self.scheduler_task = None

    async def start(self):
        async with SessionLocal() as db:
            runs = (
                await db.scalars(
                    select(Run).where(Run.status.in_(["queued", "running"]))
                )
            ).all()
            queued = []
            interrupted = []
            for run in runs:
                if run.status == "queued":
                    queued.append(run.id)
                else:
                    run.status, run.stage = "interrupted", "interrupted by restart"
                    run.finished_at = datetime.now(timezone.utc)
                    interrupted.append(run.id)
            await db.commit()
        self.workers = [
            asyncio.create_task(self.worker()) for _ in range(settings.max_active_jobs)
        ]
        for run_id in queued:
            await self.submit(run_id)
        for run_id in interrupted:
            await add_event(run_id, "run.interrupted", {"reason": "backend restarted"})
        self.scheduler_task = asyncio.create_task(self.scheduler())

    async def stop(self):
        if self.scheduler_task:
            self.scheduler_task.cancel()
            await asyncio.gather(self.scheduler_task, return_exceptions=True)
        for worker in self.workers:
            worker.cancel()
        await asyncio.gather(*self.workers, return_exceptions=True)

    async def submit(self, run_id: str):
        await self.queue.put(run_id)

    def cancel(self, run_id: str):
        task = self.active.get(run_id)
        if task:
            task.cancel()

    async def scheduler(self):
        while True:
            due_ids = []
            async with SessionLocal() as db:
                due = (await db.scalars(select(Schedule).where(Schedule.enabled == True, Schedule.next_run_at <= utcnow()))).all()
                for schedule in due:
                    connection = await db.get(Connection, schedule.connection_id)
                    if not connection or connection.archived:
                        schedule.enabled = False
                        continue
                    run = Run(kind="scan", input={"connection_id": schedule.connection_id, "schedule_id": schedule.id})
                    db.add(run)
                    await db.flush()
                    db.add(AuditEvent(action="run.scheduled", resource_type="run", resource_id=run.id, actor="system_scheduler", details={"schedule_id": schedule.id, "kind": "scan"}))
                    due_ids.append(run.id)
                    schedule.next_run_at = utcnow() + timedelta(minutes=schedule.interval_minutes)
                await db.commit()
            for run_id in due_ids:
                await self.submit(run_id)
            await asyncio.sleep(15)

    async def worker(self):
        while True:
            run_id = await self.queue.get()
            try:
                task = asyncio.create_task(execute_run(run_id))
                self.active[run_id] = task
                try:
                    await task
                except asyncio.CancelledError:
                    if asyncio.current_task().cancelling():
                        task.cancel()
                        raise
            finally:
                self.active.pop(run_id, None)
                self.queue.task_done()


manager = JobManager()
