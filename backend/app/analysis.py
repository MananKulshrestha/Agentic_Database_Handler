import itertools
import json
from collections import Counter

from sqlglot import exp, parse

RULES = [
    {
        "id": "missing_primary_key",
        "category": "integrity",
        "description": "Tables without a declared primary key",
        "certainty": "observed",
    },
    {
        "id": "foreign_key_index",
        "category": "performance",
        "description": "Foreign key columns without a matching leading index",
        "certainty": "candidate",
    },
    {
        "id": "overlapping_indexes",
        "category": "performance",
        "description": "Indexes with identical leading columns",
        "certainty": "candidate",
    },
    {
        "id": "timestamp_without_timezone",
        "category": "types",
        "description": "Timestamp columns without time zone information",
        "certainty": "candidate",
    },
    {
        "id": "missing_table_comment",
        "category": "documentation",
        "description": "Tables without a database comment",
        "certainty": "observed",
    },
    {
        "id": "wide_text_identifier",
        "category": "types",
        "description": "Identifier-like columns stored as free-form text",
        "certainty": "candidate",
    },
]


def _finding(
    obj, rule, severity, observation, recommendation, evidence, assumptions=None
):
    return {
        "rule_id": rule,
        "category": next(r["category"] for r in RULES if r["id"] == rule),
        "severity": severity,
        "object_ids": [obj.id],
        "observation": observation,
        "recommendation": recommendation,
        "evidence": {**evidence, "rule_version": 1, "source_object_id": obj.id},
        "assumptions": assumptions or [],
        "certainty": next(r["certainty"] for r in RULES if r["id"] == rule),
    }


def evaluate_object(obj, engine: str) -> list[dict]:
    if obj.kind != "table":
        return []
    d = obj.definition
    name = f"{obj.schema_name}.{obj.name}"
    findings = []
    pk = d.get("primary_key") or {}
    indexes = d.get("indexes") or []
    if not pk.get("constrained_columns"):
        findings.append(
            _finding(
                obj,
                "missing_primary_key",
                "medium",
                f"{name} has no declared primary key.",
                "Confirm whether rows need a stable unique identity; add a primary key if they do.",
                {"primary_key": pk},
            )
        )
    indexed = [list(i.get("column_names") or []) for i in indexes]
    indexed += [list(pk.get("constrained_columns") or [])]
    indexed += [
        list(u.get("column_names") or []) for u in d.get("unique_constraints") or []
    ]
    for fk in d.get("foreign_keys") or []:
        cols = list(fk.get("constrained_columns") or [])
        if cols and not any(index[: len(cols)] == cols for index in indexed):
            findings.append(
                _finding(
                    obj,
                    "foreign_key_index",
                    "medium",
                    f"Foreign key {cols} on {name} has no matching leading index.",
                    "Inspect joins and parent deletes before deciding whether an index would help.",
                    {"foreign_key": fk, "available_indexes": indexed},
                    ["The workload may not need an additional index."],
                )
            )
    seen = set()
    for index in indexes:
        cols = tuple(index.get("column_names") or [])
        if cols and cols in seen:
            findings.append(
                _finding(
                    obj,
                    "overlapping_indexes",
                    "low",
                    f"{name} has indexes with identical column lists: {cols}.",
                    "Compare predicates, uniqueness, and usage before removing any index.",
                    {"columns": cols},
                    ["Index predicates and included columns may differ."],
                )
            )
        seen.add(cols)
    for col in d.get("columns") or []:
        ctype = str(col.get("type", "")).upper()
        cname = col.get("name", "")
        if engine == "postgresql" and "TIMESTAMP WITHOUT TIME ZONE" in ctype:
            findings.append(
                _finding(
                    obj,
                    "timestamp_without_timezone",
                    "low",
                    f"{name}.{cname} uses a timestamp without time zone.",
                    "Confirm whether this value represents a local wall time or an absolute instant.",
                    {"column": cname, "type": ctype},
                    ["Local wall times may intentionally omit a time zone."],
                )
            )
        if cname.endswith("_id") and any(t in ctype for t in ("TEXT", "VARCHAR")):
            findings.append(
                _finding(
                    obj,
                    "wide_text_identifier",
                    "low",
                    f"{name}.{cname} is an identifier-like text column.",
                    "Confirm that the type matches the referenced identity and expected values.",
                    {"column": cname, "type": ctype},
                    ["The name alone does not establish that the type is wrong."],
                )
            )
    comment = d.get("comment") or {}
    if not comment.get("text"):
        findings.append(
            _finding(
                obj,
                "missing_table_comment",
                "info",
                f"{name} has no database comment.",
                "Add a short explanation if this table's purpose is not obvious to its users.",
                {"comment": comment},
            )
        )
    return findings


def validate_read_query(sql: str, dialect: str = "postgres") -> str:
    if len(sql) > 20000:
        raise ValueError("Query exceeds the 20 KB limit")
    statements = parse(sql, read=dialect)
    if len(statements) != 1 or statements[0] is None:
        raise ValueError("Provide exactly one SQL statement")
    node = statements[0]
    if not isinstance(node, (exp.Select, exp.Union, exp.Intersect, exp.Except)):
        raise ValueError("Only read-only SELECT queries are supported")
    for forbidden in (
        exp.Insert,
        exp.Update,
        exp.Delete,
        exp.Create,
        exp.Drop,
        exp.Alter,
        exp.Command,
        exp.Into,
    ):
        if node.find(forbidden):
            raise ValueError("The query contains an unsupported operation")
    if node.find(exp.Lock):
        raise ValueError("Locking reads are not supported")
    risky = {"pg_sleep", "pg_read_file", "pg_ls_dir", "dblink", "load_file", "sleep", "sys_eval", "sys_exec"}
    for function in node.find_all(exp.Func):
        if function.sql_name().lower() in risky:
            raise ValueError("The query contains an unsupported function")
    return sql.strip().rstrip(";")


def closure(
    attributes: set[str], dependencies: list[tuple[set[str], set[str]]]
) -> set[str]:
    result = set(attributes)
    while True:
        updated = result | set().union(
            *(right for left, right in dependencies if left <= result)
        )
        if updated == result:
            return result
        result = updated


def normalization_report(
    columns: list[str], dependencies: list[dict], requested_level: str
) -> dict:
    attrs = set(columns)
    requested_level = requested_level.upper()
    if requested_level not in ("1NF", "2NF", "3NF", "BCNF", "4NF", "5NF"):
        raise ValueError("requested_level must be 1NF, 2NF, 3NF, BCNF, 4NF, or 5NF")
    if requested_level == "1NF":
        return {
            "status": "insufficient_information",
            "requested_level": "1NF",
            "columns": columns,
            "explanation": "A column list cannot prove that values are atomic for the application's intended domain. Inspect sample values and confirm the business meaning of any repeating groups or nested values.",
        }
    if len(attrs) > 12:
        return {
            "status": "analysis_limit_reached",
            "explanation": "Formal key search is limited to 12 columns. Select a smaller relation.",
        }
    valid = []
    for dep in dependencies:
        left, right = set(dep.get("determinant", [])), set(dep.get("dependent", []))
        if left and right and left | right <= attrs:
            valid.append((left, right))
    if not valid:
        return {
            "status": "insufficient_information",
            "explanation": "Confirm functional dependencies before assigning a normal form.",
            "columns": columns,
        }
    keys = []
    ordered = sorted(attrs)
    for size in range(1, len(attrs) + 1):
        for group in itertools.combinations(ordered, size):
            candidate = set(group)
            if any(key <= candidate for key in keys):
                continue
            if closure(candidate, valid) == attrs:
                keys.append(candidate)
    prime = set().union(*keys) if keys else set()
    violations = []
    for left, right in valid:
        nontrivial = right - left
        if not nontrivial:
            continue
        if requested_level == "2NF":
            partial_key = any(left < key for key in keys)
            nonprime = nontrivial - prime
            if partial_key and nonprime:
                violations.append(
                    {
                        "determinant": sorted(left),
                        "dependent": sorted(nonprime),
                        "reason": "A non-prime attribute depends on a proper subset of a candidate key.",
                    }
                )
            continue
        if closure(left, valid) == attrs:
            continue
        if requested_level == "3NF" and nontrivial <= prime:
            continue
        violations.append(
            {
                "determinant": sorted(left),
                "dependent": sorted(nontrivial),
                "reason": "The determinant is not a superkey.",
            }
        )
    if requested_level in ("4NF", "5NF"):
        return {
            "status": "insufficient_information",
            "candidate_keys": [sorted(k) for k in keys],
            "functional_dependency_violations": violations,
            "explanation": "Higher-normal-form assessment also requires confirmed multivalued or join dependencies.",
        }
    return {
        "status": "violation_identified"
        if violations
        else "verified_under_supplied_dependencies",
        "candidate_keys": [sorted(k) for k in keys],
        "violations": violations,
        "dependencies_used": dependencies,
        "requested_level": requested_level,
    }


def simple_suitability(description: str, current_engine: str) -> dict:
    text = description.lower()
    signals = []
    relational = any(word in text for word in ("join", "transaction", "relational", "foreign key", "constraint"))
    document = any(word in text for word in ("document", "variable fields", "nested", "flexible schema"))
    small_local = any(word in text for word in ("embedded", "single user", "offline", "desktop"))
    if relational:
        signals.append("The description mentions relational operations or integrity constraints.")
    if document:
        signals.append("The description mentions document-shaped or changing records.")
    if small_local:
        signals.append("The description suggests a small embedded/local workload.")
    descriptions = {
        "postgresql": ("Rich relational constraints and SQL with JSON support.", "Migration may require SQL, type, and application changes."),
        "mariadb": ("Established relational SQL model with familiar operational tooling.", "Feature and SQL compatibility must be checked before migration."),
        "mongodb": ("Document-centered modeling and flexible record shapes.", "Cross-document relationships and transactions need careful design."),
        "sqlite": ("Simple embedded database with little operational overhead.", "Not intended as a drop-in multi-user server replacement."),
    }
    options = []
    for engine, (benefit, tradeoff) in descriptions.items():
        score = 1 if engine == current_engine else 0
        reasons = ["No migration needed"] if engine == current_engine else []
        if relational and engine in ("postgresql", "mariadb"):
            score += 2
            reasons.append("Relational workload signal")
        if document and engine in ("mongodb", "postgresql"):
            score += 2 if engine == "mongodb" else 1
            reasons.append("Document workload signal")
        if small_local and engine == "sqlite":
            score += 2
            reasons.append("Embedded workload signal")
        options.append({"option": engine, "score": score, "reasons": reasons, "benefit": benefit, "tradeoff": tradeoff, "migration_required": engine != current_engine})
    options.sort(key=lambda option: (-option["score"], option["migration_required"], option["option"]))
    return {
        "options": options,
        "signals": signals,
        "status": "provisional",
        "scoring_method": "Transparent workload-keyword heuristic; scores are not benchmarks.",
        "missing_evidence": [
            "Representative queries and their frequency",
            "Consistency requirements",
            "Migration and team constraints",
        ],
    }


def profile_rows(rows: list[dict], columns: list[str]) -> dict:
    result = {}
    for column in columns:
        values = [row.get(column) for row in rows]
        types = Counter(type(value).__name__ for value in values if value is not None)
        distinct = len({json.dumps(v, default=str, sort_keys=True) for v in values})
        text_lengths = [len(value) for value in values if isinstance(value, str)]
        nulls = sum(v is None for v in values)
        result[column] = {
            "sample_size": len(rows),
            "nulls": nulls,
            "null_rate": round(nulls / len(rows), 4) if rows else None,
            "observed_types": dict(types),
            "distinct_in_sample": distinct,
            "candidate_key_in_sample": bool(rows and not nulls and distinct == len(rows)),
            "minimum_text_length": min(text_lengths) if text_lengths else None,
            "maximum_text_length": max(text_lengths) if text_lengths else None,
        }
    return result


def index_candidates(queries: list[dict], objects: list, dialect: str) -> list[dict]:
    """Conservative one-table equality candidates; never an automatic index action."""
    by_name = {}
    for obj in objects:
        if obj.kind == "table":
            by_name.setdefault(obj.name.lower(), []).append(obj)
    counts = Counter()
    for item in queries:
        try:
            statements = parse(item.get("query") or item.get("fingerprint") or "", read=dialect)
        except Exception:
            continue
        for statement in statements:
            if not isinstance(statement, exp.Select):
                continue
            tables = list(statement.find_all(exp.Table))
            if len(tables) != 1:
                continue
            candidates = by_name.get(tables[0].name.lower(), [])
            if len(candidates) != 1:
                continue
            obj = candidates[0]
            fields = {col.get("name", "").lower() for col in obj.definition.get("columns", [])}
            indexed = [
                (idx.get("column_names") or [None])[0]
                for idx in obj.definition.get("indexes", [])
            ]
            indexed += (obj.definition.get("primary_key") or {}).get("constrained_columns") or []
            indexed = {str(name).lower() for name in indexed if name}
            for equal in statement.find_all(exp.EQ):
                sides = (equal.left, equal.right)
                column = next((side for side in sides if isinstance(side, exp.Column)), None)
                if not column or column.name.lower() not in fields or column.name.lower() in indexed:
                    continue
                if not any(isinstance(side, (exp.Literal, exp.Placeholder, exp.Parameter)) for side in sides):
                    continue
                counts[(obj.id, f"{obj.schema_name}.{obj.name}", column.name)] += int(item.get("calls") or 1)
    return [
        {"object_id": object_id, "table": table, "column": column, "observed_calls": calls, "certainty": "candidate", "reason": "Repeated equality predicate without a leading index in the snapshot", "caution": "Confirm selectivity and write overhead with an estimated plan or sandbox experiment."}
        for (object_id, table, column), calls in counts.most_common(20)
    ]
