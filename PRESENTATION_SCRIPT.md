# Presentation Script — Agentic Database Handler

**Format:** 2 presenters · 14–15 minutes · live demo · Q&A
**Presenter A:** problem, solution, architecture, AI trust, results
**Presenter B:** product tour, demo, safety limits, roadmap

> **Presenter-only note (delete before sharing):** the current repository contains the frontend, the analysis/agent/job modules, and the database models, but it does **not** contain `backend/app/main.py`, `backend/serve.py`, `backend/requirements.txt`, or `backend/migrations/`, even though `README.md` references them. Run the start-up check below before you present. If the API will not start, do not promise a live demo — show the screenshots/artifacts you prepared and present the architecture instead.

---

## 1. The 30-second version (memorise this)

> **A:** Most teams only find out what is wrong with their database when something breaks.
>
> **B:** We built a local workbench that scans a database into a versioned snapshot, finds structural problems with deterministic rules, generates documentation, and lets you ask questions about the schema — with every answer tied back to evidence.
>
> **A:** The important part is what it refuses to do: it does not change your database, and the AI is optional, read-only, and always reviewed by a human.

---

## 2. Before you present — 10-minute checklist

- [ ] `docker compose up -d` — two containers: `app-db` on **5432**, `demo-db` on **5433**.
- [ ] `cp .env.example .env`; set `DBH_ENCRYPTION_SECRET` to any unique value.
- [ ] Backend runs on `127.0.0.1:8000`; frontend runs on `127.0.0.1:5173`.
- [ ] Demo connection: host `127.0.0.1`, port `5433`, database `demo`, user `demo_read`, password `demo_read`, schema `public`.
- [ ] Run **Test → Scan → Analyze** once before the class, so a snapshot and findings already exist.
- [ ] Generate one documentation report and open one chat answer, so you have a fallback.
- [ ] Take screenshots of: Overview, Explorer + diagram, Findings, Documentation, Design lab, Query lab, chat answer.
- [ ] Keep two browser tabs open: the app and `http://127.0.0.1:8000/docs`.
- [ ] Close Slack/notifications; zoom the browser to 110–125%.
- [ ] Have a second terminal ready for `docker compose ps` if the connection fails.

**If the backend will not start:** skip the live clicks, show the screenshots, and say: “The demo database and UI are prepared; the API layer in this repository is the part we are still packaging, so I will walk through the recorded run.” Do not improvise claims.

**macOS/Linux commands**

```bash
cp .env.example .env
docker compose up -d
python3 -m venv .venv && source .venv/bin/activate
pip install -r backend/requirements.txt        # file is currently missing in the repo
python -m backend.serve --host 127.0.0.1 --port 8000

# second terminal
npm install --prefix frontend
npm run dev --prefix frontend
```

**Windows PowerShell:** same steps, but activate with `.\.venv\Scripts\Activate.ps1` and run `.\.venv\Scripts\python.exe -m backend.serve --host 127.0.0.1 --port 8000`.

---

## 3. Timing map

| Time | Section | Speaker | Visual |
|---|---|---|---|
| 0:00 | Title + hook | A | Title slide |
| 0:40 | Problem statement | A | Four pain points |
| 2:00 | Solution in one sentence | A | Snapshot → analyze → document → ask |
| 2:50 | What the product does | B | Six workspaces |
| 4:00 | **Demo 1:** connect, scan, explore, diagram | B | Live app |
| 6:15 | Architecture | A | Architecture diagram |
| 7:45 | Technology stack | A | Stack table |
| 8:45 | **Demo 2:** findings, docs, design, query, chat | B | Live app |
| 11:30 | AI with guardrails | A | Trust pipeline |
| 12:30 | Limits, testing, what we learned | B | Honest slide |
| 13:40 | Roadmap + close | A + B | Roadmap slide |
| 14:30 | Q&A | Both | — |

If you are cut to **6 minutes**, use the short version in section 10.

---

## 4. Full script

### Slide 1 — Title · A · 0:00

> “Good morning. We are [name] and [name], and our project is called **Agentic Database Handler**.
>
> It is a local database workbench: you point it at a database, it takes a read-only snapshot, explains the structure, finds design problems, writes documentation, and answers questions about the schema.
>
> I will cover the problem, the architecture, and how we keep the AI trustworthy. [B] will show the product and be honest about what it does not do yet.”

*[Click to the problem slide.]*

---

### Slide 2 — The problem · A · 0:40

> “Here is the problem we started with.
>
> **One:** the schema is the part of an application everyone depends on and nobody documents. The knowledge lives in senior developers’ heads, old wiki pages, or nowhere.
>
> **Two:** reviewing a database is manual. Someone opens a tool like pgAdmin, clicks through tables, and tries to remember the checklist: primary keys, foreign-key indexes, timestamp types, comments.
>
> **Three:** documentation goes stale the moment the schema changes. There is no easy answer to ‘what changed since last month?’ that is tied to a version.
>
> **Four:** generic AI chat is tempting, but pasting a schema into a chatbot gives you an answer with no evidence, and it may hallucinate a table that does not exist. Worse, an ‘agent’ that can run SQL can damage the database.
>
> So the real problem is not ‘we need another database GUI’. It is: **how do we make schema review fast, repeatable, explainable, and safe?**”

*[Transition: “That question became our design brief.”]*

---

### Slide 3 — The solution · A · 2:00

> “Our answer is a snapshot-based workbench.
>
> The flow is: **scan → snapshot → analyze → document → ask → review.**
>
> A scan reads metadata only — tables, columns, keys, indexes, comments — and stores it as a **versioned snapshot**. For MongoDB, where there is no fixed schema, it reads a bounded sample of up to 100 documents to observe the shape. A snapshot is important because it gives every later action a fixed context. Findings, documentation, chat answers, and comparisons all reference one specific version, not a moving target.
>
> On top of that snapshot we have three layers.
>
> **First, deterministic analysis.** Rules check the snapshot and produce findings with severity, evidence, assumptions, and certainty. No AI is involved, so the same schema always produces the same result.
>
> **Second, human workflows.** Findings can be accepted or dismissed, reports can be reviewed and approved, business rules can be confirmed, and every action is recorded.
>
> **Third, an optional assistant.** It can explain findings and answer questions, but it only sees selected snapshot context, it can call a small set of read-only tools, and its citations are checked against real IDs.
>
> The principle is: **deterministic first, AI second, human always.**”

*[Hand over: “Let me show you what that looks like in the product.”]*

---

### Slide 4 — What it does · B · 2:50

> “There are six workspaces.
>
> **Overview** shows the snapshot, recent findings, recent jobs, and the audit trail.
>
> **Schema explorer** lists tables, views, and collections with columns, primary keys, indexes, and an interactive relationship diagram.
>
> **Findings** is the review queue. Each card has a severity, a category, evidence, and a recommendation. You can accept or dismiss it.
>
> **Documentation** generates Markdown reports from the snapshot and exports them.
>
> **Design lab** is for deeper investigations: normalization checks, profiling, database suitability, inferred relationships, schema diffs, index experiments, and migration inventories.
>
> **Query lab** drafts SQL, shows an estimated plan, and runs bounded read queries.
>
> And there is a contextual chat sidebar on every page. The assistant knows which snapshot and which table you have selected.”

---

### Demo 1 — Connect, scan, explore · B · 4:00

*[Show the left sidebar. Speak while clicking, not before.]*

> “On the left we have connections and snapshots. The form is already prefilled for the demo database.
>
> Two PostgreSQL instances are running in Docker: the application store on 5432, and the demo target on 5433. The demo target has a read-only role called `demo_read`. That is deliberate: even if something goes wrong, the application cannot write to the source.”

**[Click Test]**

> “Test runs a real connection check and reads the server version. This is not a fake ‘saved’ message.”

**[Click Scan]**

> “A scan is a background job, not a blocking HTTP request. It goes into the run queue, the UI receives server-sent events, and it polls as a fallback if the stream drops. That matters because schema scans can take a while.”

*[While it runs, point at the stage text.]*

> “You can see the stage change. Every run is stored with a status: queued, running, succeeded, failed, cancelled, or interrupted. If the backend restarts, queued jobs are resumed and running jobs are marked interrupted instead of silently disappearing.”

**[Overview appears]**

> “Here is the snapshot. Four objects in the demo: `customers`, `orders`, `order_items`, and `event_log`.
>
> `event_log` is intentionally flawed. It has no primary key and its timestamp has no time zone. `order_items` is intentionally unusual: it stores a product name and sale price at purchase time, and it has a database comment explaining why. That contrast is the whole point of the project — a tool can flag something, but a human decides whether it is a real problem.”

**[Explorer → select `customers`]**

> “The explorer shows the columns, nullability, primary key, and indexes. This is read from the snapshot, so it is fast and stable even if the live database changes.”

**[Scroll to Relationships]**

> “The diagram is generated with React Flow and laid out with dagre. Solid edges are declared foreign keys. Later, the design lab can also *suggest* relationships from column names, and those are marked as inferred, not declared.”

---

### Slide 5 — Architecture · A · 6:15

> “Under the UI there are four parts.
>
> **The API layer** exposes everything under `/api/v1`: connections, snapshots, findings, artifacts, conversations, runs, schedules, and approvals. It validates requests and returns structured errors with a code, a message, and a hint.
>
> **The job engine** is an in-process asyncio queue with a small worker pool. A request creates a `Run` row and returns immediately. Workers execute the job, write stage events, and store the result as an **artifact**. That is why the UI can show progress and survive a page reload.
>
> **The analysis layer** is pure logic: rule evaluation, SQL validation, normalization, profiling, index candidates, and snapshot comparison. It has no dependency on the web layer, which makes it testable.
>
> **The connectors** talk to PostgreSQL and MariaDB through SQLAlchemy’s async inspector, and to MongoDB through a bounded document sample. Passwords are encrypted with Fernet before they are stored.
>
> On the side, there are two optional external services: OpenAI for the assistant, and TypeSafe Jev for advisory triage. Both are off unless a key is configured and schema sharing is explicitly enabled.”

*[Point at the data flow arrows.]*

> “Notice the direction: the target database is read-only, the application store is separate, and the AI never gets a write path.”

---

### Slide 6 — Technology · A · 7:45

> “Quickly, the stack and why we chose it.
>
> **Frontend:** React 18 with TypeScript and Vite. TanStack Query handles server state, caching, and pagination. React Flow plus dagre draws the schema diagram. React Markdown renders artifacts and chat answers. Lucide provides icons. Vitest covers the API client and safe Markdown rendering.
>
> **Backend:** Python 3.11+, FastAPI and Uvicorn for the HTTP API, Pydantic settings for configuration, SQLAlchemy 2.0 async for both the application store and the target connections. Alembic is configured for application-schema migrations.
>
> **SQL safety:** `sqlglot` parses every query before it runs. That is how we allow only one read-only statement and reject DDL, DML, locks, and risky functions. It is also used to fingerprint uploaded workloads and to propose conservative index candidates.
>
> **Storage:** PostgreSQL 16 for the app store in the Docker setup, with SQLite as the zero-setup default. The demo target is a separate PostgreSQL container.
>
> **Security:** Fernet encryption for stored connection passwords, scrypt for optional local accounts, SHA-256 digests for session tokens.
>
> **Optional AI:** the OpenAI async SDK with function calling, or the TypeSafe Jev API for narrow advisory decisions. Both fail closed: if the service is unavailable, the rest of the product still works.”

---

### Demo 2 — Findings, docs, design, query, chat · B · 8:45

**[Findings tab]**

> “This is the review queue produced by the deterministic rules. On our four-table demo it produces six findings.
>
> `event_log` has no primary key, uses a timestamp without a time zone, and has no comment. `orders.customer_id` is a foreign key without a matching leading index. `customers` and `orders` also lack comments.
>
> Each finding carries its evidence and its assumptions. The timestamp finding says ‘confirm whether this is a local wall time’ — it does not pretend the type is always wrong. That wording is intentional: we want a review, not a false accusation.”

**[Click Accept on one finding]**

> “Accepting or dismissing a finding is a human decision, and it is recorded. The tool never applies a schema change.”

**[Documentation tab → Generate documentation]**

> “Documentation is generated from the same snapshot, not from the live database. It becomes an artifact — a stored report with Markdown and structured JSON. You can export it or attach it to a review.”

**[Design lab → choose `order_items` → add a rule]**

> “Now the interesting case. `order_items` stores `product_name` and `sale_price`. If we confirm the business rule ‘product_id determines product_name’, the normalization check reports a potential 3NF violation, because `product_id` is not a superkey of the order line.
>
> But the table has a comment saying these are historical snapshots at purchase time. So the correct conclusion is: **the finding is a prompt for a conversation, not an automatic redesign.** That is exactly why we require the user to supply functional dependencies instead of guessing them from column names.”

**[Query lab → run `SELECT * FROM public.customers LIMIT 10;`]**

> “Every query goes through the validator first. Exactly one statement, SELECT/UNION/INTERSECT/EXCEPT only, no DDL, no DML, no locks, no risky functions. On PostgreSQL it also runs inside a read-only transaction with a five-second statement timeout. Results are capped at 500 rows and one megabyte.”

**[Chat sidebar → ask: “Which tables lack a primary key?”]**

> “The assistant is grounded in the selected snapshot. It does not receive the whole database as a prompt. It has three read-only tools: get object details, get findings, and get business rules.
>
> The answer includes a citation to the actual finding ID, and the UI turns that into a clickable link. If no AI key is configured, the sidebar still answers this class of question from saved schema facts. So the product degrades gracefully instead of breaking.”

---

### Slide 7 — AI with guardrails · A · 11:30

> “This is the part we expect questions about, so let me be precise about the trust model.
>
> **One: opt-in.** Schema sharing is off by default. Nothing leaves the machine unless `OPENAI_ALLOW_SCHEMA_SHARING=true` is set, and the README tells the user exactly what will be sent.
>
> **Two: limited context.** The assistant gets the selected snapshot, selected objects, and relevant findings — not credentials, not arbitrary rows, not the whole database.
>
> **Three: limited tools.** There are three read-only tools and hard limits: at most eight tool calls per round and four rounds. There is no tool that executes SQL or changes anything.
>
> **Four: verified citations.** The model is asked to cite `[object:<id>]` or `[finding:<id>]`. Before the answer is shown, we check each ID against the snapshot. Unknown IDs are replaced with ‘unverified reference’, and only verified IDs become clickable evidence.
>
> **Five: a deterministic fallback.** Primary-key questions, finding summaries, and object descriptions are answered from saved facts even without AI.
>
> **Six: advisory AI.** The optional Jev integration only produces triage labels and confidence values. Responses are validated for type, allowed choices, and a confidence between zero and one; invalid responses are rejected. It never becomes an action.
>
> So the agentic part is not ‘AI with root access’. It is **AI inside a review workflow**.”

---

### Slide 8 — Limits and what we learned · B · 12:30

> “We want to be honest about scope, because this is a college project, not a production platform.
>
> **It is local and single-user by default.** There is optional local-account mode with viewer, analyst, and admin roles, but it has no rate limiting, no external identity provider, and no security review. It should not be exposed to the internet as-is.
>
> **The worker is in-process.** Queued jobs resume after a restart, but running jobs are marked interrupted. There is no distributed queue.
>
> **Analysis has deliberate limits.** Profiling reads the first 1,000 rows, not a random sample. The schema scan has a 60-second budget. Query results are capped at 500 rows and 1 MB. The SQLite index experiment is educational; its timings do not predict production performance.
>
> **Normalization is honest.** 1NF cannot be proven from a column list. 4NF and 5NF need multivalued or join dependencies that we do not infer. We only claim a result under the dependencies the user confirms.
>
> **Migrations are an inventory, not an execution.** We list what to review; we do not rewrite your schema.
>
> **Testing is partial.** We have Vitest coverage for the API client and Markdown rendering. Backend test coverage is a known gap, and one of the first things we would add.
>
> What we learned: the hard part was not calling a model. It was making the system **explainable** — job states, artifact provenance, evidence links, and failure messages that a human can act on.”

---

### Slide 9 — Roadmap · A · 13:40

> “If we continued, we would do four things.
>
> **One:** finish the API packaging and end-to-end tests, so a fresh clone starts with one command.
>
> **Two:** move the worker to a durable queue and add real authentication hardening.
>
> **Three:** make migration proposals executable *with* an explicit approval step and a rollback plan — never automatically.
>
> **Four:** replace the synthetic index experiment with safe workload replay on a disposable copy, so the numbers mean something.
>
> The long-term idea is a **reviewable database assistant**: every suggestion has evidence, every change has an owner, and nothing happens without a human.”

### Close · A + B · 14:20

> **A:** “To summarise: we turned schema review into a repeatable workflow — snapshot, analyze, document, ask, review.”
>
> **B:** “And we kept the risky parts bounded: read-only connections, deterministic findings, optional AI, verified citations, and no automatic changes.”
>
> **A:** “Thank you. We are happy to take questions.”
>
> **B:** “If you want to try it, the demo database is already running locally.”

---

## 5. Q&A preparation

Answer in 2–4 sentences. If you do not know, say so and describe how you would find out.

**1. Why not just use pgAdmin, DBeaver, or DataGrip?**
> Those are excellent database clients, but they are built for inspecting one object at a time. They do not produce a versioned snapshot, a reviewable finding queue, generated documentation, or evidence-linked answers. We are not replacing them; we are adding a review layer on top of the same metadata.

**2. Does the AI modify the database?**
> No. The assistant has three read-only tools and no SQL execution tool. The only place a query runs is the Query lab, and that is restricted to a single read-only statement in a read-only transaction with a timeout and row cap. Schema changes are never applied.

**3. How do you stop the AI from hallucinating?**
> We limit what it can see, limit what it can call, and verify every citation against real object and finding IDs. Unverified references are marked. If the model is unavailable, deterministic fallback answers still work. It can still be wrong in wording, which is why the output is labelled as an assistant answer and requires human review.

**4. What exactly is a snapshot?**
> A row in the `snapshots` table plus the `schema_objects` and `relationships` captured at that moment, identified by a schema hash. Every finding, report, and chat message points to a snapshot, so results are reproducible and two scans can be diffed.

**5. Why PostgreSQL?**
> It exposes rich metadata — comments, constraints, indexes, `EXPLAIN`, and `pg_stat_statements` — which makes the analysis meaningful. It is also easy to run in Docker for a demo. The application store can fall back to SQLite, and MariaDB/MongoDB metadata scans are supported.

**6. Is the normalization check actually correct?**
> It implements attribute closure and candidate-key search, then checks 2NF/3NF/BCNF under user-supplied dependencies. It deliberately refuses to claim 1NF from a column list and refuses 4NF/5NF without multivalued or join dependencies. So it is correct relative to the dependencies you give it, not a proof about the business.

**7. How are database passwords protected?**
> Passwords are encrypted with Fernet using a key derived from `DBH_ENCRYPTION_SECRET`, and only decrypted in memory for a connection. We still recommend a read-only database account, because encryption at rest does not protect a compromised host.

**8. What happens if the backend crashes mid-job?**
> Queued jobs are re-submitted on startup. Jobs that were running are marked `interrupted` rather than pretending they succeeded. The UI receives a `run.interrupted` event. The user can submit the work again.

**9. How would this scale to a team?**
> The first bottleneck is the in-process worker, so we would move runs to a durable queue with separate workers. Then we would add proper authentication, per-project authorization, and audit retention. The data model already has projects, members, roles, and audit events, but the operations story is not production-ready.

**10. What is the role of TypeSafe Jev?**
> It is an optional advisory service for triage: how urgently a finding or schema change should be reviewed, and with what confidence. We validate every response against allowed values and a confidence range, and reject invalid responses. It never decides to change anything.

**11. How do you test it?**
> The analysis, diff, and normalization functions are pure and unit-testable. On the frontend we use Vitest for the API client and Markdown rendering. Backend and end-to-end coverage is the biggest testing gap, and we would add fixture databases and job-lifecycle tests next.

**12. What was the hardest part?**
> The job lifecycle. A naive version blocks the request and loses state on refresh. We had to design run records, ordered events, SSE plus polling, cancellation, and restart behaviour. The second hardest part was making the AI output auditable instead of just plausible.

**13. Why not let the AI generate the whole review?**
> Because a plausible review is not a reliable review. Deterministic rules give us repeatability and evidence. The model is useful for explanation and prioritisation, but it should not be the source of truth for schema facts.

**14. What is the most interesting finding in the demo?**
> `order_items` looks denormalized because it stores `product_name` and `sale_price`. But the comment explains that these are historical snapshots at purchase time. That is a great example of why the tool presents a finding and a human decides — the schema is not wrong, the context matters.

**15. Does it work offline?**
> Yes, for scanning, analysis, documentation, the explorer, and fallback chat. AI chat and Jev triage are the only features that need the network, and they are opt-in.

---

## 6. Demo runbook (exact click path)

Use this as a cue card. Do not read it on stage.

1. **Sidebar** → connection `Demo PostgreSQL` → **Test** → wait for “Connected”.
2. **Scan** → wait for stage messages → snapshot appears.
3. **Overview** → point at Objects / Findings / Snapshots counters.
4. **Schema explorer** → `public.customers` → columns, PK, indexes.
5. **Relationships** → point at `orders → customers`.
6. **Findings** → **Run analysis** (if needed) → point at `event_log` findings and `orders.customer_id`.
7. **Accept** one finding → show the status change.
8. **Documentation** → **Generate documentation** → show Markdown → **Download Markdown**.
9. **Design lab** → select `order_items` → add rule `product_id → product_name` → **Analyze normalization** → read the status.
10. **Query lab** → `SELECT * FROM public.customers LIMIT 10;` → **Run read query**.
11. **Chat** → “Which tables lack a primary key?” → point at the evidence link.
12. Optional: **Compare snapshots** if you have two scans; **Index experiment** on `customers.email`.

**If a click fails:** say “I will switch to the prepared run”, open the screenshot, and keep the narrative moving. Never debug live for more than 15 seconds.

---

## 7. Slide text you can copy

### Problem slide
- Schema knowledge is undocumented and concentrated in a few people.
- Manual review misses the same checks every time.
- Documentation is stale and not versioned.
- Ungrounded AI is plausible but unsafe.

### Solution slide
**Scan → Snapshot → Analyze → Document → Ask → Review**
- Deterministic findings with evidence.
- Versioned, reproducible context.
- Human accept/dismiss and approval.
- AI is optional, read-only, and cited.

### Architecture slide
```
React + TypeScript
        │  /api/v1
        ▼
FastAPI  ──►  Job manager (asyncio queue)
        │            │
        │            ├── connectors  ──► target DB (read-only)
        │            ├── analysis    ──► rules / SQL / diff
        │            └── agent       ──► OpenAI (opt-in)
        ▼
Application store (PostgreSQL / SQLite)
  runs · events · snapshots · findings · artifacts · audit
```

### Stack slide
| Layer | Choice | Why |
|---|---|---|
| UI | React 18, TypeScript, Vite | Fast, typed, componentised |
| Server state | TanStack Query | Caching, pagination, polling |
| Diagram | React Flow + dagre | Interactive ERD with layout |
| API | FastAPI, Pydantic | Typed routes, automatic docs |
| Data | SQLAlchemy 2 async, Alembic | One model for app and target DBs |
| SQL safety | sqlglot | Parse, validate, fingerprint |
| Jobs | asyncio queue, SSE | Progress without blocking |
| Security | Fernet, scrypt, SHA-256 | Encrypted secrets, optional auth |
| AI | OpenAI SDK, TypeSafe Jev | Optional, bounded, validated |

### Limits slide
- Local, single-user by default; auth is not hardened.
- In-process worker; no distributed queue.
- Samples and caps: 1,000 profile rows, 500 result rows, 1 MB, 5 s query timeout.
- Normalization only under confirmed dependencies.
- Migration output is an inventory, not an execution.
- Backend/end-to-end tests are still a gap.

---

## 8. If the teacher asks for a live coding explanation

Open these files in this order and explain one thing in each:

1. `backend/app/analysis.py` — the six rules and `validate_read_query`.
2. `backend/app/jobs.py` — `HANDLERS`, `execute_run`, `JobManager`.
3. `backend/app/agent.py` — `TOOL_DEFINITIONS`, `_verified_citations`, `_grounded_fallback`.
4. `backend/app/connectors.py` — `encrypt_password`, `sql_scan`, `estimated_plan`.
5. `backend/app/diff.py` — `compare_snapshots`.
6. `frontend/src/App.tsx` — `watch()` and the six views.
7. `frontend/src/Diagram.tsx` — dagre layout + React Flow.

Keep each explanation to one sentence: **what it does, what it prevents, why it is separate.**

---

## 9. Phrases to avoid / use instead

| Avoid | Use |
|---|---|
| “The AI manages the database.” | “The assistant explains the snapshot; the human decides.” |
| “It automatically fixes your schema.” | “It produces a reviewable recommendation; no change is applied.” |
| “It is production-ready.” | “It is a local college project with a clear production roadmap.” |
| “The AI is always correct.” | “The AI is constrained and cited; the output still requires review.” |
| “It supports every database.” | “PostgreSQL is the primary target; MariaDB and MongoDB metadata scans are supported.” |

---

## 10. Six-minute short version

Use this if the schedule slips.

**A (0:00–1:30):** “Schema review is manual, documentation goes stale, and ungrounded AI is unsafe. We built a local workbench that scans a database into a versioned snapshot, finds problems with deterministic rules, documents it, and answers questions with evidence. Deterministic first, AI second, human always.”

**B (1:30–3:30):** Test → Scan → Explorer. “Four tables, six findings. `event_log` has no primary key and no timezone; `order_items` is deliberately denormalized and documented. The tool flags; the human decides.” Show the diagram and one finding.

**A (3:30–5:00):** Architecture and stack. “React and TanStack Query on the front; FastAPI, async SQLAlchemy, sqlglot, and an asyncio job queue on the back; PostgreSQL in Docker; optional OpenAI with three read-only tools and verified citations; no write path.”

**B (5:00–6:00):** Limits and close. “Local, single-user, in-process worker, partial tests. Next: packaging, durable jobs, end-to-end tests, and approval-gated migrations. Thank you.”

---

## 11. One-page cue card (print this)

```
OPEN  : problem = manual + stale + unsafe AI
THESIS: snapshot → analyze → document → ask → review
KEY   : deterministic first, AI second, human always

DEMO 1: Test → Scan → Explorer → Diagram
DEMO 2: Findings → Accept → Docs → Normalization → Query → Chat
DATA  : 4 tables, 6 findings; event_log flawed; order_items intentional

ARCH  : React → FastAPI → JobManager → connectors/analysis/agent
        app store: PostgreSQL/SQLite; target: read-only
STACK : TS/React/Vite, TanStack, React Flow, FastAPI, SQLAlchemy,
        sqlglot, asyncio+SSE, Fernet, OpenAI (opt-in)

SAFETY: 1 statement, SELECT only, read-only txn, 5 s, 500 rows/1 MB
        citations verified; fallback answers; no auto changes
LIMITS: local, in-process, sampling, partial tests
CLOSE : explainable, reviewable, safe by default
```
