# Agentic Database Handler

For a conceptual tour of the architecture, demo schema, agentic workflow, and current limits, read [PROJECT_PRIMER.md](PROJECT_PRIMER.md).

See [IMPLEMENTATION_STATUS.md](IMPLEMENTATION_STATUS.md) for verified scope and remaining production/runtime work.

A local database workbench for exploring schemas, saving snapshots, reviewing findings, generating Markdown documentation, and asking questions through a contextual chat sidebar. PostgreSQL is the primary demo target. MariaDB and MongoDB metadata scans are also supported. This is a college project and is designed to run on one computer.

## What is implemented

- Saved connections, schema snapshots, table and collection explorer, and relationship diagram.
- PostgreSQL and MariaDB metadata scanning; MongoDB validator and bounded observed-shape scanning.
- Deterministic checks for key, index, timestamp, identifier, and documentation issues.
- Markdown documentation and export, saved findings, business rules, and chat history.
- AI-assisted chat when `OPENAI_API_KEY` is configured and schema sharing is explicitly enabled. Without that opt-in, the sidebar still answers basic questions using saved schema facts.
- Background operations with status and server-sent progress events.
- SQL drafting, bounded read queries, estimated plans, profiling, normalization checks, provisional suitability scoring, detailed schema diffs, inferred relationships, workload inspection, source-context line references, and index experiments.
- Uploaded SQL workload fingerprinting and conservative index candidates. A separately registered PostgreSQL sandbox can run a temporary-table synthetic experiment; the default remains an in-memory SQLite simulation.
- Structured API errors with request IDs, source-linked fallback chat answers, and API/frontend automated tests.

The default local version is single-user. Optional `AUTH_REQUIRED=true` mode adds local accounts and viewer/analyst/admin project roles; set a strong `AUTH_BOOTSTRAP_SECRET` before enabling it. Scheduled scans, review decisions, and an audit trail are available, but this is not yet a production security or operations stack. There is no automatic schema change execution or distributed worker. Queued jobs resume after a local restart; jobs interrupted while running require a new submission. Migration output is a review inventory, not a fully converted migration script. Higher normal form checks require user-supplied dependencies; the application does not claim to infer 5NF from metadata.

## Requirements

- Python 3.11 or newer.
- Node.js 20 or newer and npm.
- Docker Desktop for the supplied PostgreSQL application and demonstration databases. The backend can alternatively use its default local SQLite application store, but the demonstration target still needs PostgreSQL.

## Set up and run later

These commands are instructions for when you want to run the application. The services are not started as part of this implementation.

From this folder in PowerShell:

```powershell
Copy-Item .env.example .env
docker compose up -d
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
npm install --prefix frontend
```

Set `DBH_ENCRYPTION_SECRET` to a unique value in `.env` before saving connections. Set `OPENAI_API_KEY` and `OPENAI_ALLOW_SCHEMA_SHARING=true` to enable open-ended AI analysis. This explicitly allows schema names, column names, and finding summaries to be sent to the configured model provider. Leave either unset for local fallback chat.

To try shared-project sign-in, set `AUTH_REQUIRED=true` and a long random `AUTH_BOOTSTRAP_SECRET` in `.env`. On first load, the frontend asks for that secret once to create the administrator. Administrators can create viewer, analyst, or administrator accounts from the overview page. Tokens are kept in the browser session and expire after 24 hours. This local account system has not been hardened with rate limiting, external identity, or a security review; do not expose it publicly as-is.

Start the backend and frontend in separate terminals:

```powershell
.\.venv\Scripts\python.exe -m backend.serve --host 127.0.0.1 --port 8000
```

```powershell
npm run dev --prefix frontend
```

Open `http://127.0.0.1:5173`. The API reference is at `http://127.0.0.1:8000/docs`.

The connection form is prefilled for the demo target: `127.0.0.1:5433`, database `demo`, user `demo_read`, password `demo_read`. The demo user has read access. Save the connection, test it, scan it, and then run analysis.

Docker initializes the demo database only when its data directory is first created. If the demo volume already existed before this project was added, use a fresh volume or connect to another PostgreSQL database; do not delete an existing volume unless you intend to remove its data.

## API and jobs

Every API route starts with `/api/v1`. FastAPI serves the current route list and request schemas at `/docs` and `/openapi.json`.

Long-running tasks use `POST /api/v1/runs` and return a run ID. Retrieve status from `GET /api/v1/runs/{id}` or events from `GET /api/v1/runs/{id}/events`. Chat messages create a chat run via `POST /api/v1/conversations/{id}/messages`. Reports are stored as artifacts and can be exported in Markdown or JSON.

The available run kinds are returned by `GET /api/v1/capabilities`. A few examples:

```json
{"kind":"scan","connection_id":"<connection-id>"}
```

```json
{"kind":"analyze","snapshot_id":"<snapshot-id>"}
```

```json
{"kind":"normalization","snapshot_id":"<snapshot-id>","object_id":"<table-object-id>","requested_level":"3NF"}
```

```json
{"kind":"experiment","snapshot_id":"<snapshot-id>","object_id":"<table-object-id>","column":"customer_id","row_count":3000}
```

The default synthetic experiment runs entirely in a temporary SQLite database. Its timing is educational and does not predict performance on the connected database. To try the optional PostgreSQL-native experiment, save a separate PostgreSQL connection that permits temporary-table writes, register it in the Design lab as a sandbox, and select it before running the experiment. It uses only synthetic rows in a session-local temporary table; results still do not predict the source table's performance.

## Project layout

- `backend/app/main.py`: HTTP API and request validation.
- `backend/app/database.py`: application storage tables.
- `backend/app/connectors.py`: target database inspection.
- `backend/app/analysis.py`: deterministic and formal analysis helpers.
- `backend/app/agent.py`: contextual chat.
- `backend/app/jobs.py`: background operations and artifacts.
- `backend/app/auth.py`: optional local-account password hashing and session tokens.
- `backend/migrations/`: versioned application-store schema migrations. Startup upgrades the app store and adopts databases created by the earlier local table-initialization code.
- `frontend/src/App.tsx`: workbench interface.
- `demo/postgres/init.sql`: demonstration schema and read-only role.

## Practical limits

The schema scan defaults to a 60-second budget. Query results are limited to 500 rows and 1 MB of serialized data, and target statements have a 5-second timeout. The data profiler reads up to 1,000 rows per selected table and labels that sample method. A backend restart resumes queued work and marks in-progress jobs as interrupted. The initial connector is designed for databases the user is authorized to inspect. Use a read-only database account for ordinary connections; SQL parsing alone cannot make arbitrary database functions safe.

For manual application-store migration checks, run `.\.venv\Scripts\alembic.exe current` and `.\.venv\Scripts\alembic.exe check` from the project root with `APP_DATABASE_URL` configured. Take a backup before upgrading an existing shared database.

For a shared deployment, add authentication, stronger secret management, host restrictions, and a durable worker before exposing this application outside localhost.
