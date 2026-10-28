"""Actionable, credential-safe messages for database and task failures."""

from dataclasses import dataclass

from sqlalchemy.exc import DBAPIError

_SAFE_INPUT_PREFIXES = (
    "Connection not found", "Snapshot not found", "SQL drafting is unavailable",
    "A question is required", "Snapshot has no tables", "SQL operations are unavailable",
    "Select at least", "Choose a table", "Choose a column", "Snapshots must belong",
    "Workload snapshot must belong", "Workload document does not belong",
    "Live workload statistics require", "Uploaded document not found",
    "No CREATE TABLE statements were found", "The selected sandbox must use",
    "Sandbox not found", "Query exceeds", "Provide exactly one SQL statement",
    "Only read-only SELECT queries are supported", "The query contains an unsupported",
    "Locking reads are not supported", "requested_level must be",
    "No SQL driver for", "Query plans are unavailable", "row_count must be",
    "pg_stat_statements is not installed",
)


@dataclass(frozen=True)
class PublicError:
    code: str
    message: str
    hint: str

    def text(self) -> str:
        return f"{self.message} {self.hint}".strip()


def _exception_details(exc: Exception) -> tuple[str, set[str]]:
    """Inspect wrapped driver errors without returning their raw text to clients."""
    messages: list[str] = []
    sqlstates: set[str] = set()
    seen: set[int] = set()
    pending: list[BaseException] = [exc]
    while pending and len(seen) < 8:
        current = pending.pop(0)
        if id(current) in seen:
            continue
        seen.add(id(current))
        messages.append(str(current).lower())
        state = getattr(current, "sqlstate", None) or getattr(current, "pgcode", None)
        if isinstance(state, str):
            sqlstates.add(state)
        for nested in (getattr(current, "orig", None), current.__cause__, current.__context__):
            if isinstance(nested, BaseException):
                pending.append(nested)
    return " ".join(messages), sqlstates


def _is_database_exception(exc: Exception) -> bool:
    seen: set[int] = set()
    pending: list[BaseException] = [exc]
    while pending and len(seen) < 8:
        current = pending.pop(0)
        if id(current) in seen:
            continue
        seen.add(id(current))
        if isinstance(current, DBAPIError) or type(current).__module__.startswith(("psycopg", "pymongo", "asyncmy")):
            return True
        for nested in (getattr(current, "orig", None), current.__cause__, current.__context__):
            if isinstance(nested, BaseException):
                pending.append(nested)
    return False


def describe_error(exc: Exception, *, connection_test: bool = False) -> PublicError:
    message, sqlstates = _exception_details(exc)

    if not connection_test and not _is_database_exception(exc):
        if isinstance(exc, ValueError):
            raw = str(exc)
            if raw.startswith(_SAFE_INPUT_PREFIXES):
                return PublicError("INVALID_OPERATION", raw[:500], "")
            return PublicError("INVALID_OPERATION", "The operation input is invalid.", "Check the selected connection, snapshot, and form values.")
        if isinstance(exc, TimeoutError):
            return PublicError("OPERATION_TIMEOUT", "The operation timed out.", "Reduce the scope or try again.")
        return PublicError("OPERATION_FAILED", "The operation failed unexpectedly.", "Check the connection with Test, then review the server logs if it still fails.")

    if "28P01" in sqlstates or "password authentication failed" in message or "authentication failed" in message:
        return PublicError("DATABASE_AUTH_FAILED", "The database rejected the username or password.", "Check both in Edit connection. For a Supabase session pooler, the username includes the project reference after a dot.")
    if "no tenant identifier provided" in message:
        return PublicError("DATABASE_POOLER_PROJECT_MISSING", "The connection pooler could not identify the Supabase project.", "In Supabase Connect, copy the exact Session pooler host and username. Do not construct the host from the region.")
    if "tenant or user not found" in message or "unknown tenant" in message:
        return PublicError("DATABASE_POOLER_USER_NOT_FOUND", "The connection pooler could not find this database user or project.", "Check the exact pooler host and the full username, including the project reference.")
    if "circuit breaker open" in message:
        return PublicError("DATABASE_POOLER_THROTTLED", "The connection pooler is temporarily rejecting login attempts.", "Check the username and password, wait briefly, then retry once.")
    if "3D000" in sqlstates or "database does not exist" in message:
        return PublicError("DATABASE_NOT_FOUND", "The database name was not found on the server.", "Check the Database field in Edit connection; Supabase projects usually use postgres.")
    if "42501" in sqlstates or "permission denied" in message or "not authorized" in message:
        return PublicError("DATABASE_PERMISSION_DENIED", "The database connected but denied access to an object or operation.", "Grant the inspection role USAGE on the schema and SELECT on the tables being scanned.")
    if "42P01" in sqlstates or "3F000" in sqlstates:
        return PublicError("DATABASE_OBJECT_NOT_FOUND", "A table or schema used by this operation was not found.", "Refresh the schema snapshot and check that the selected object still exists.")
    if "read-only transaction" in message or "read only transaction" in message or "25006" in sqlstates:
        return PublicError("DATABASE_READ_ONLY", "This operation tried to write through a read-only connection.", "Use the inspection features with this role; write experiments need a separate writable database role.")
    if "ssl" in message or "certificate verify failed" in message:
        return PublicError("DATABASE_SSL_FAILED", "The database could not establish a secure connection.", "Check the server's SSL requirements and certificate settings.")
    if "could not translate host name" in message or "name or service not known" in message or "getaddrinfo failed" in message or "nodename nor servname provided" in message:
        return PublicError("DATABASE_HOST_NOT_FOUND", "The database host name could not be resolved.", "Check the Host field. Enter only the host name, without a URL prefix, port, or path.")
    if "network is unreachable" in message or "no route to host" in message:
        return PublicError("DATABASE_NETWORK_UNREACHABLE", "This computer cannot reach the database network.", "Check your network. If a Supabase direct host requires IPv6, use the session pooler endpoint instead.")
    if "connection refused" in message or "actively refused" in message:
        return PublicError("DATABASE_CONNECTION_REFUSED", "The database server refused the connection.", "Check the host and port, and confirm the database service is running and accepts remote connections.")
    if "57014" in sqlstates or "statement timeout" in message:
        return PublicError("DATABASE_QUERY_TIMEOUT", "The database stopped a query because it took too long.", "Reduce the scan scope or retry when the database is less busy.")
    if isinstance(exc, TimeoutError) or "timed out" in message or "timeout expired" in message:
        return PublicError("DATABASE_TIMEOUT", "The database did not respond before the connection timed out.", "Check the host, port, network access, and whether the database is running; then retry.")
    return PublicError("OPERATION_FAILED", "The operation failed unexpectedly.", "Check the connection with Test, then review the server logs if it still fails.")
