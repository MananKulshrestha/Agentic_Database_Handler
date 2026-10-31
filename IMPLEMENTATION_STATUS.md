# Implementation status

This is a college-project database workbench, not a production-approved database administration service. This file distinguishes implemented code from work that still requires verification or a larger implementation.

## Implemented in this pass

- Versioned Alembic migrations for the application store, with startup adoption of the earlier local schema.
- Structured API errors with request IDs, run-kind input checks, server-side finding pagination, and a detailed snapshot-diff endpoint.
- Read-query length, row, response-byte, and timeout limits; non-SELECT and locking-query rejection; generic public errors rather than raw driver exceptions.
- More informative fallback chat with validated object/finding citations and clickable evidence; explicit opt-in before schema context is sent to an LLM.
- Workload file fingerprinting and conservative index candidates, more informative profiling, MongoDB observed-shape reports, context line references, normalization reports, and transparent provisional engine-suitability scoring.
- Optional PostgreSQL synthetic experiments using session-local temporary tables, registration of sandbox connections, experiment history, and an unchanged SQLite simulation fallback.
- Persisted local scheduled scans, queued-run recovery after restart, and cancellation events.
- Optional local accounts and project roles, proposal review records, and an audit trail for high-value actions. Review approval does not apply changes to a target database.
- Frontend controls for sign-in, members, schedules, sandboxes, experiment history, proposal review, paginated objects/findings, recent activity, and audit events.
- In-process API and unit tests, frontend tests, lint, build, and migration consistency checks.

## Verified on running services

On 2026-10-07, the app was started locally with PostgreSQL 17 on ports 5432 and 5433, the API on 8000, and the frontend on 5173. The browser flow saved and tested the demo connection, scanned four tables and their relationships, generated six findings, rendered Markdown documentation, answered a schema question with evidence, returned a bounded read query and estimated plan, and rejected a write query. The frontend had no captured browser console warnings or errors during this flow. Backend and frontend automated suites passed (20 and 5 tests respectively), and the frontend production build succeeded.

This run found and fixed a PostgreSQL timezone-type labeling issue and a Windows `localhost` connection-delay issue. The old snapshot and failed query runs remain in the local test database as history; the fresh snapshot and repeat query verified the fixes.

## Still to verify on running services

PostgreSQL sandbox experiments, schedule execution, cancellation, auth roles, and the advanced design flows still need browser-level checks. MariaDB and MongoDB need disposable integration fixtures. Docker Compose was not used in this run because Docker Desktop could not start without Windows service permissions; the locally installed PostgreSQL binaries were used instead. AI mode was not tested because no API key/schema-sharing opt-in was configured.

## Not yet production-ready

- The agent does not truly stream model tokens. Native SSE does not carry bearer headers, so authenticated mode currently uses status polling and displays the completed answer.
- Normal-form analysis can evaluate supplied functional dependencies for 2NF/3NF/BCNF, but 1NF and 4NF/5NF cannot be proven from metadata alone; multivalued/join dependencies and business meaning require human input.
- Migration proposals are review inventories, not executable cross-engine conversions. No approved schema change is applied automatically.
- The PostgreSQL sandbox uses synthetic rows on the same engine, not representative source data or workload. MariaDB-native sandbox experiments are not implemented.
- Auth lacks login rate limiting, password reset/rotation flows, external identity integration, and a security review. Browser tokens are stored in session storage. Do not expose this application publicly as-is.
- Jobs and schedules are persisted but run in one API process. There is no distributed worker, lease/heartbeat protocol, multi-instance scheduler coordination, or operational retry policy.
- No backup/restore workflow, secrets-manager integration, retention policy, performance/load evaluation, or production monitoring has been completed.
- Connector integration tests for MariaDB/MongoDB and broad browser end-to-end coverage remain; the single PostgreSQL demo flow above does not cover every feature.
