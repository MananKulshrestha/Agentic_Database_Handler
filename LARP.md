# Agentic Database Handler: five-minute project briefing

Use this as a speaking sheet for the `dev` branch. The confident version is also the accurate version: describe what the system does, point to evidence, and be clear about its limits.

## The 30-second pitch

“We built a local database workbench that turns a database schema into a saved, explainable investigation. You connect a database, scan its metadata into a snapshot, run repeatable design checks, explore relationships and findings, and generate documentation or ask grounded questions. The system helps a person decide what to investigate; it does not silently change the target database.”

## The mental model that explains everything

There are **two databases with different jobs**. The **target** is the business database being inspected. The supplied demo is PostgreSQL on port **5433**, with a read-only `demo_read` account. The **workbench store** holds connections, snapshots, findings, runs, artifacts, chat, and review history. Compose supplies PostgreSQL for that store on **5432**; the backend can also use local SQLite.

The normal path is **connect → scan → snapshot → analyze → review → rescan and compare**. A snapshot is a saved point-in-time view, so answers based on it can become stale. Live read queries are a separate, explicitly requested feature. Long operations are persisted as *runs* with progress events, and their outputs become *artifacts*.

## What happens under the hood

- **React + TypeScript + Vite** provide the Overview, Schema explorer, Findings, Documentation, Query lab, Design lab, and chat interface (`frontend/src/App.tsx`).
- **FastAPI** exposes `/api/v1` routes, validates requests, applies optional roles, and reports structured errors (`backend/app/main.py`).
- **Connectors** inspect PostgreSQL and MariaDB metadata; MongoDB inspection uses validators and bounded observed shapes (`backend/app/connectors.py`). Saved records and Alembic migrations live in `backend/app/database.py` and `backend/migrations/`.
- **Background jobs** perform scans, analysis, documentation, queries, profiling, comparisons, and experiments (`backend/app/jobs.py`). They run in the API process, not a separate worker fleet.
- **Deterministic rules** flag candidates such as missing primary keys, foreign keys without leading indexes, timestamps without time zones, and missing comments (`backend/app/analysis.py`). A finding is evidence plus a recommendation, not proof that the schema is wrong.
- **The chat agent** can use saved objects, findings, and business rules to answer with checked references (`backend/app/agent.py`). Open-ended OpenAI mode requires both an API key and explicit schema-sharing opt-in; otherwise it uses grounded local fallback answers.
- **Jev on `dev`** is an optional advisory layer (`backend/app/jev.py`). With a TypeSafe key and separate schema-sharing opt-in, a user can request confidence-bearing ratings for finding priority and triage, schema changes, proposal readiness, or run attention. These ratings do not execute database changes.

## The demo story to tell

The shop schema has `customers → orders → order_items`, plus standalone `event_log`. Scan it, show the relationship diagram, then open the findings. `event_log` has no primary key and uses a timestamp without a time zone: these are review prompts, not automatic defects. `order_items` keeps purchase-time product name and price; that duplication can be intentional historical truth. Then ask a chat question about a table, show its cited snapshot evidence, run a bounded `SELECT`, and show that a write query is rejected. A second scan can be compared with the first to reveal schema changes.

## Answers that sound informed because they are precise

| If asked… | Say… |
| --- | --- |
| “What makes it agentic?” | “The assistant can choose bounded, read-oriented inspection tools and explain findings with validated references. The core checks are deterministic; AI helps interpret them.” |
| “Does it fix the database?” | “No. Findings, migration inventories, approvals, and Jev ratings are review artifacts. A human makes changes outside the workbench.” |
| “Can it prove a table is normalized?” | “Only under confirmed business dependencies. It can reason about 2NF, 3NF, and BCNF from supplied dependencies; metadata alone cannot prove 1NF, 4NF, or 5NF.” |
| “Are suggested indexes benchmarks?” | “No. Workload candidates are conservative. The default experiment uses synthetic SQLite data; an optional PostgreSQL sandbox uses temporary synthetic tables. Neither predicts production performance.” |
| “Is it safe to deploy publicly?” | “It is a local college project. Optional accounts and roles exist, but auth, operations, backups, and the in-process job system need production hardening.” |

## Current proof and honest boundaries

The recorded local verification on **7 October 2026** exercised the PostgreSQL demo: four tables scanned, six findings, documentation, a cited chat answer, a bounded read query and plan, and rejection of a write query. Backend and frontend suites passed at that point (20 and 5 tests), as did the frontend build (`IMPLEMENTATION_STATUS.md`). That record predates or does not establish a live Jev provider check. MariaDB/MongoDB integration, advanced browser flows, auth roles, schedules, sandbox experiments, and external AI still need broader runtime verification.

**If you remember one sentence:** “We preserve evidence first, make recommendations second, and leave schema changes to a human.”
