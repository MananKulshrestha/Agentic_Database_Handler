# Agentic Database Handler: a high-level project guide

*Current state: October 2026. This explains the project as it exists today, not a proposed production system.*

## The idea in one minute

The Agentic Database Handler is a local **database workbench**. It connects to a database you are allowed to inspect, records what its schema looks like, identifies possible design issues, generates readable documentation, and lets you ask questions about what it found. It can also draft queries and design proposals for a person to review.

Its central promise is **understanding before changing**. The normal workflow is to observe, explain, recommend, and compare—not to let an AI silently alter a database. The current project is suited to learning and demonstrating database-design ideas. It is not a production database administrator.

## The most important mental model: two databases, two purposes

The project uses the word *database* in two different ways:

| Role | What it contains | Why it exists |
| --- | --- | --- |
| **Target database** | The application's business tables or collections that you want to understand | This is what the workbench scans and analyzes. The supplied demo target is PostgreSQL on port 5433. |
| **Workbench store** | Saved connections, snapshots, findings, reports, conversations, and task history | This lets you revisit an analysis without repeatedly scanning the target. In the current local run it is PostgreSQL on port 5432; the backend also supports a local SQLite store. |

A **schema** is the structure of data: tables or collections, fields and types, keys, indexes, and relationships. A **snapshot** is a saved observation of that structure at one point in time. If the target database changes tomorrow, yesterday's snapshot and its report do not automatically change. You scan again and compare the two snapshots.

```text
Browser workbench (React)
        │ asks for scans, reports, queries, and chat answers
        ▼
API and background jobs (FastAPI)
        ├── inspect the target database ──► saved schema snapshot
        ├── evaluate known rules ────────► findings
        ├── use optional AI tools ────────► evidence-linked answer
        └── save results ─────────────────► workbench store
```

This separation matters. A chat answer normally uses **saved snapshot facts**; it is not a live inspection of every row. The Query lab is a separate, explicitly requested path for bounded read queries against the target database.

## The supplied demo schema

The demo target is a tiny shop-like database. It intentionally includes both normal relationships and a couple of things worth discussing.

```text
customers 1 ──── many orders 1 ──── many order_items

event_log  (standalone)
```

| Table | What it represents | Notable structure |
| --- | --- | --- |
| `customers` | People who place orders | An ID primary key, a unique email, and a creation timestamp with a time zone. |
| `orders` | One purchase event per order | An ID primary key and a `customer_id` foreign key pointing to `customers`. |
| `order_items` | Products recorded within an order | A composite primary key made from `order_id` and `product_id`; `order_id` points to `orders`. |
| `event_log` | A simple event record | No declared primary key and a timestamp without a time zone; these invite review rather than proving the design is wrong. |

The demo contains two customers, two orders, and one order item. The `order_items` table stores product name and sale price **as they were at purchase time**. That is an important modeling lesson: copying a value can be deliberate historical recordkeeping, not necessarily a normalization error. The meaning of the data matters as much as its column names.

The read-only demo account can inspect this target. The workbench's own store is separate; saving a report or conversation does not insert rows into these business tables.

## How an investigation flows

1. **Connect and test.** You save an engine, host, database, credentials, and a schema scope. A connection test checks access before a scan.
2. **Scan.** A connector reads database metadata: objects, columns, data types, keys, indexes, comments, and declared relationships. The result becomes a new snapshot in the workbench store. It is labeled partial if some inspection steps warn or fail.
3. **Analyze.** Predictable rules examine the saved snapshot. They can flag a missing primary key, a foreign key without a matching leading index, a timestamp without a time zone, or absent documentation. Each result is a **finding** with an observation, recommendation, evidence, assumptions, and a certainty label.
4. **Explore and ask.** You can browse the relationship diagram, read an object's details, generate Markdown documentation, or ask the chat sidebar a question. Answers should point back to the snapshot, objects, or findings that support them.
5. **Apply human judgment.** You accept or dismiss findings, add business rules for normalization analysis, or review redesign and migration proposals. These actions record decisions in the workbench; they do not automatically change the target schema.
6. **Rescan and compare.** After someone changes a target database outside this workbench, a fresh scan and snapshot comparison show what changed.

Longer operations are saved as **runs**. A run has a status and progress events; it can produce an **artifact**, such as a documentation page, query result, normalization assessment, or migration inventory. This is why an analysis remains available after its progress indicator disappears.

## What is actually “agentic” here?

Not every feature uses AI. The project deliberately combines three kinds of reasoning:

| Kind | Example | How to interpret it |
| --- | --- | --- |
| **Deterministic check** | “This table has no declared primary key.” | A repeatable observation about the saved schema. Whether it needs fixing still depends on the application's purpose. |
| **Human-supplied meaning** | “These columns determine those columns” in a business rule | Domain knowledge the schema alone cannot reliably reveal. Normalization results are conditional on this input. |
| **Optional AI assistance** | A question about a table causes the assistant to inspect its details and findings before answering | A flexible explanation that may vary between runs. It must be checked against cited evidence. |

At present, the running demo has **external AI turned off**. The chat sidebar still works, but it uses a local, grounded response path for questions such as “Which tables lack a primary key?” or “What does `customers` contain?” SQL drafting similarly falls back to a simple starter `SELECT` when AI is off.

If a model key is configured **and** schema sharing is explicitly enabled, the assistant can receive a limited snapshot summary and choose from three read-oriented tools: inspect an object, read findings, or read user-supplied business rules. It can make several bounded tool calls, then return an answer with references to known objects or findings. The application checks citation IDs against the saved snapshot. The model does **not** receive a general-purpose tool to execute arbitrary SQL or apply schema changes. Enabling this mode sends selected schema context to the configured model provider, so it is an intentional privacy decision.

This is a useful distinction for describing the project: it is an **evidence-grounded assistant around a database workbench**, not an autonomous database operator.

## The questions each part of the interface answers

| Area | Main question it helps answer |
| --- | --- |
| **Overview** | What connection and snapshot am I looking at, and what has run recently? |
| **Schema explorer** | What objects, fields, types, keys, indexes, and relationships were observed? |
| **Findings** | What deserves investigation, and which suggestions have I reviewed? |
| **Documentation** | Can I read or export a human-friendly account of this snapshot? |
| **Query lab** | What would a read query return, and what is its estimated plan? |
| **Design lab** | What might normalization, workload, alternative engines, or an index experiment teach us? |
| **Chat sidebar** | Can I ask a plain-language question and trace the answer back to evidence? |

The Design lab also has snapshot comparison, inferred relationship suggestions, SQL workload inspection, uploaded source-context analysis, a draft migration inventory, and local scheduled scans. Synthetic index experiments are educational: a default experiment uses in-memory SQLite, while an optional registered PostgreSQL sandbox uses a temporary table. Neither measures the performance of your real production workload.

## Three important database-design concepts

### Keys and relationships

A **primary key** identifies a row; a **foreign key** records a relationship to another table. In the demo, `orders.customer_id` connects an order to a customer. An index on a foreign-key column can make some lookups or parent-row operations faster, but indexes also cost storage and slow some writes. The workbench calls a missing index a candidate for investigation, not an unconditional instruction.

### Normalization needs business meaning

Normalization asks whether a table mixes facts that belong to different logical entities. Some checks depend on **functional dependencies**: for example, whether knowing one set of columns always determines another. The application can evaluate supplied dependencies for 2NF, 3NF, and BCNF. It cannot prove 1NF, 4NF, or 5NF simply by reading column names and types. A plausible-sounding rule can be false when data has historical meaning—such as the purchase-time product name in `order_items`.

### Engine choice needs workload evidence

PostgreSQL, MariaDB, MongoDB, and SQLite support different data models and operational tradeoffs. The current suitability feature uses a transparent, provisional heuristic based on the workload description you enter. It is **not a benchmark** or an automatic migration recommendation. A serious switch needs representative queries, data shape, consistency requirements, application compatibility, migration effort, and rollback planning.

## Current implementation and verification

The frontend is built with React and Vite; it renders Markdown reports and a relationship diagram. A FastAPI backend exposes the workbench's API and runs local background jobs. SQLAlchemy handles application data and SQL database inspection; Alembic versions the workbench-store schema. PostgreSQL is the supplied end-to-end demo, with MariaDB and MongoDB metadata connectors also implemented.

The latest live demo verification found four target tables and six findings. It exercised a connection test, schema scan, analysis, Markdown report, evidence-linked fallback chat answer, bounded read query, estimated plan, and rejection of a write query. Automated backend and frontend tests and the frontend build passed. There are two demo snapshots in the local store: the newer one reflects a corrected PostgreSQL timestamp-type scan; the older one preserves two false time-zone findings from before that fix.

Some features exist but **have not yet had the same live end-to-end verification**: PostgreSQL sandbox experiments, scheduled execution, cancellation, optional sign-in roles, advanced design flows, and MariaDB/MongoDB fixtures. AI mode has not been exercised in this local run because no model key and schema-sharing opt-in are configured.

## Boundaries and honest limitations

- The normal connection is meant to be read-only. The Query lab limits output and rejects non-`SELECT` statements, but SQL parsing is not a substitute for database permissions. Use a read-only account on any real target.
- Reports, findings, approvals, and migration inventories are **review artifacts**, not applied changes. An “approved” proposal is still only a recorded decision.
- The assistant should distinguish an observed fact from an inference. Citations improve traceability; they do not guarantee that every recommendation fits the business context.
- Jobs and schedules run in one local API process. The optional account system is not hardened for public exposure. There is no production backup/restore, distributed worker, or automatic cross-engine migration path yet.
- A saved snapshot can become stale. If the target has changed, scan again before relying on an old answer or report.

The sensible next engineering steps would be broader connector and browser tests, stronger safeguards around shared deployment, representative workload measurements, and only then carefully designed migration execution. Those are future directions, not capabilities to assume today.

## What is worth learning personally

You do not need to memorize every package or endpoint to understand this project. These are the concepts that help you judge its output and direct future work:

| Tier | Learn item | Simple meaning | Why it matters here | Agentic coding angle | Learn depth |
| --- | --- | --- | --- | --- | --- |
| Critical Learn | Target vs. workbench store | The database being inspected is not the database holding reports. | Prevents confusion about where a write or failure happened. | Ask an agent which database each operation touches. | Concept + practice |
| Critical Learn | Snapshots and evidence | An answer is tied to what one scan observed. | Old reports can be stale or incomplete. | Require snapshot IDs and source evidence in claims. | Concept + practice |
| Critical Learn | Keys, dependencies, and normalization | Structure alone does not tell the whole business story. | Avoids treating every copied field as a design mistake. | Challenge an agent's assumed functional dependencies. | Concept + practice |
| Critical Learn | Read-only boundaries | Analysis should not imply permission to change the target. | A UI check is weaker than a database permission. | Ask for the exact effect and credential scope of a proposed action. | Concept + practice |
| Major Learn | Background jobs | Slow work has a saved status and result instead of blocking the page. | Explains progress, cancellation, and persisted reports. | Test success, failure, restart, and duplicate submissions. | Concept first |
| Major Learn | Query plans and indexes | The database chooses a route for a query; an index changes possible routes. | A plausible index suggestion needs workload evidence. | Request measurements before accepting optimization claims. | Concept first |
| Major Learn | Grounded AI tools | The optional model can request a small set of known facts. | Limits unsupported claims and makes answers auditable. | Inspect tool scope, budgets, and citation checks. | Concept first |
| Minor Learn | Specific React or FastAPI APIs | Libraries implement the interface and server. | Useful when editing code, less important for the system's idea. | Let coding tools handle syntax; review data flow and behavior. | Recognition only |

## Next 3 learning moves

1. **Trace one question end to end:** scan the demo, select `event_log`, read its finding, ask the sidebar about it, and identify which snapshot and evidence the answer used.
2. **Test a design assumption:** read the comment on `order_items` and explain why purchase-time `product_name` might intentionally differ from a current product catalog name. Then try a normalization rule only if it is truly valid.
3. **Compare observation with recommendation:** run a read query and view its estimated plan; explain why a suggested index still needs representative usage data before anyone adds it.

For hands-on operation, see [README.md](README.md). For a precise list of what has and has not been verified, see [IMPLEMENTATION_STATUS.md](IMPLEMENTATION_STATUS.md).
