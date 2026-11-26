# Deep dive, part 1: the backend and its data

This part explains the backend's skeleton: how a request and an event travel through it, where data lives, and what keeps
one organisation's data away from another's. It was written from the code as it stands. Where an older document
disagrees, the code is right.

The agents, the Guide's search and the web app are in the later parts. This part stops at the door of
`Container.handle`, which is where an event is handed to the assistant.

## Read this first

| If you want to know | Read |
|---|---|
| What the pieces are and how they connect | [1. The shape](#1-the-shape) |
| What happens when the server starts | [2. Start-up](#2-start-up-mainpy) |
| What `MODE` changes | [3. Settings](#3-settings-configpy), [4. The container](#4-the-container-containerpy) |
| Why agents cannot touch the database directly | [5. Ports](#5-ports-portspy), [6. Adapters](#6-adapters) |
| How one organisation is kept from another | [7. The database](#7-the-database) |
| Who may call which route | [8. The API layer](#8-the-api-layer) |
| How limits and logs are made safe | [9. Limits and logs](#9-limits-and-logs) |
| How background work runs and is retried | [10. Events](#10-events-the-outbox-the-buses-and-the-workers) |
| What the audit log records | [11. The audit log](#11-the-audit-log) |
| How data is exported, deleted and erased | [12. Export, deletion and erasure](#12-export-deletion-and-erasure) |
| How an onboarding moves from enrolment to sign-off | [13. The lifecycle package](#13-the-lifecycle-package) |
| Three journeys, step by step | [14. Worked examples](#14-worked-examples) |
| Numbers that matter | [15. Constants](#15-constants-and-what-breaks-if-they-change) |
| What goes wrong and what happens then | [16. Failure paths](#16-failure-paths) |
| Which tests guard which rule | [17. Tests](#17-how-the-tests-hold-it-in-place) |
| How to add a table, a route or an event | [18. Extending it](#18-extending-it) |

Terms used throughout, each defined once:

- **Organisation**: one customer. Called *tenant* in code comments. Every business row carries its `org_id`.
- **Port**: a Python `Protocol` that says what the outside world can do for the app (store a row, read a repository).
- **Adapter**: a class that implements a port, in memory or against a real service.
- **Container**: the object that holds the adapters chosen for the current mode.
- **Event**: a small dictionary (`{"type": "repo.pushed", ...}`) that describes work to do in the background.
- **Outbox**: a table where an event is written before it is sent, so a failed send is retried and never lost.
- **Dedupe key**: a string on an event (`event["dedupe"]`). The same key is handled once.
- **Lease**: a claim on a dedupe key that expires unless its holder keeps renewing it.
- **Row-level security (RLS)**: a PostgreSQL feature. A policy on a table decides which rows a query can see.
- **Definer function**: a SQL function that runs with its owner's rights, not the caller's (`SECURITY DEFINER`).
- **Checkpoint**: the saved state of an assistant run that paused to wait for a person.
- **KT session**: a recorded knowledge-transfer session. The code and tables call it a `meeting`.

---

## 1. The shape

There are two ways into the backend: a request and an event.

```
Browser ──HTTP──▶ main.py middleware ──▶ auth.py (who, which organisation) ──▶ a route
                                                                                 │
                                    reads and small writes ◀── store ────────────┤
                                                                                 │
                                    work for later ──▶ outbox.publish ──▶ bus ───┘
                                                                           │
GitHub / Calendly webhooks, the repository watcher, the daily tick ────────┤
                                                                           ▼
                                                  Container.handle(event) ──▶ the assistant
```

A request is answered straight away. Anything slow (reading a repository, writing a KT session's notes, drafting a plan,
reviewing a pull request) becomes an event. The route writes the event to the outbox and returns `202`. A worker
picks it up.

Three ideas hold the whole design together:

1. **Agents see only ports.** `Deps` in [deps.py](../../backend/app/agents/core/deps.py) lists everything an agent may use. There is
   no database handle and no HTTP client in it. That is why the same agents run in the demo (in memory) and in production.
2. **The database enforces the organisation boundary, not the code.** Every query runs as a role that cannot see past
   the row-level security policies. A forgotten `WHERE org_id = ...` returns nothing extra.
3. **Every event is recorded first and handled once.** The outbox makes sending reliable. The dedupe key and its lease
   make handling happen once, even when a message arrives twice or a process dies half-way.

---

## 2. Start-up: main.py

[main.py](../../backend/app/main.py) builds the FastAPI app. `create_app()` runs at import; `lifespan()` runs when the server
starts and stops.

### What `lifespan` does, in order

1. `setup_logging()` installs `JsonFormatter`: one JSON object per log line, with `severity`, `request_id` and, on Google
   Cloud, the trace id so a request's lines group together. uvicorn's own access log is silenced, because it repeats the
   request line in plain text with the client's address.
2. Outside the demo it initialises `firebase_admin`. This is used only for the local Firebase emulator; real tokens are
   checked without it (see [8.1](#81-sign-in-authpy-and-firebase_tokenspy)).
3. It raises the thread limit for blocking work to `max(40, API_THREADS)` (default 100). FastAPI runs every plain `def`
   route on this pool. With the default of 40, a few slow answers would leave sign-in waiting for a thread.
4. It creates the `LiveHub` (live updates, [8.7](#87-live-updates-livepy-and-streampy)) and binds it to the event loop.
5. `build_container(settings, live=hub)` wires the adapters ([section 4](#4-the-container-containerpy)). **If the
   settings are unsafe it raises here and the server does not start.**
6. Outside the demo it starts `PgListener`, a thread that listens for database change announcements.
7. In self-hosted mode it starts the background threads of [selfhost.py](../../backend/app/selfhost.py).
8. `audit.on_start()` prepares the in-memory audit log, or starts the daily purge loop in self-hosted mode.

On shutdown each background object's `stop()` is called and the checkpoint connection pool is closed.

### Middleware order

Starlette wraps middleware inside out: the last one added runs first. The order a request meets them is:

| Order | Middleware | Job |
|---|---|---|
| 1 (outermost) | `request_log` | Request id, trace, timing, security headers, one log line, turns an unhandled error into a `500` with the id |
| 2 | `CORSMiddleware` | Only when `CORS_ORIGINS` is set (or `*` in the demo) |
| 3 | `RateLimitMiddleware` | `429` when a caller is over its limit |
| 4 (innermost) | `BodyLimitMiddleware` | `413` when a body is over its route's limit |

The order is deliberate. The rate limiter sits inside CORS so that a `429` still carries the CORS headers and the browser
can read it. The body limit is innermost so nothing reads an oversized body.

`request_log` also sets five security headers on every response (`SECURITY_HEADERS`) and a `Content-Security-Policy` of
`default-src 'none'; frame-ancestors 'none'`. The API returns JSON and files, so nothing in a response may load, run or
frame anything. The one exception is `/api/docs`, which exists only in the demo and loads Swagger UI.

The path in the log line passes through `redact()` from [logsafe.py](../../backend/app/logsafe.py), because some paths are
credentials ([9.3](#93-keeping-credentials-out-of-logs-logsafepy)).

### Health

- `GET /health` returns `{"ok": true}` and touches nothing. Uptime checks call it every minute.
- `GET /health/ready` calls `store.ping()`. It answers `503` when the database does not.

### Routers

`create_app` includes: `routes` (the main API), `uploads`, `stream`, `lifecycle`, the three insight views, `webhooks`,
`outbox`, `audit`, `members`, `agents` and `client_errors`. `integrations` is included from the bottom of
[routes.py](../../backend/app/api/routes.py). The `demo` router is registered only when `MODE=demo`, so its sign-in shortcut
does not exist in production.

---

## 3. Settings: config.py

[config.py](../../backend/app/config.py) holds one `Settings` class. Every field is an environment variable of the same name
in upper case.

### Two switches

- `MODE`: `demo`, `self_hosted` or `gcp`. It decides which adapters are wired.
- `ENV`: `dev` or `prod`. It decides how strict the start-up checks are.

### The `.env` rule

Outside the demo, settings are also read from `.env` in the working directory. In the demo, `.env` is skipped
completely. `settings_customise_sources` replaces the usual `.env` source with `dotenv_unless_demo`.

Why: a developer's `backend/.env` holds real credentials for self-hosted work. The demo must behave the same on every
machine, with no keys. To use a real key in the demo, put it in the command's own environment.

The two other settings classes (`RateLimitSettings`, `AuditSettings`) use the module-level `dotenv_unless_demo` helper, so
a `.env` cannot change the demo's limits or retention either. [test_config.py](../../backend/tests/test_config.py) holds this
rule.

### Small validators with large effects

- `_unused_is_unset`: the deploy guide has people enter `unused` for a secret they do not need yet. The validator turns
  `unused` into an empty string for four secrets. Without it, `unused` would become a real, guessable key.
- `_blank_is_default`: `SIGNUP_ENABLED=` (empty) means "use the mode's default", not an error.

### Derived properties the rest of the code reads

| Property | True when | Used for |
|---|---|---|
| `ai_ready` | A Gemini key is set, or the mode is `gcp` or `demo` | Routes that need the model answer `503` with a plain sentence when it is false |
| `signup_first_only` | Self-hosted and `SIGNUP_ENABLED` unset | Sign-up creates the first organisation only |
| `signup_open` | `SIGNUP_ENABLED=true`, or unset on `gcp` | Any verified login may create an organisation |
| `github_secret_ready` | The demo, or the webhook secret is not in `WEAK_SECRETS` | Signing the GitHub install link |
| `github_webhooks_ready` | Secret ready, and not (self-hosted without a GitHub App) | `POST /webhooks/github` answers `404` otherwise |
| `email_ready` | `SMTP_HOST` and a sender are set | The web app says "Emailed" only then |
| `google_calendar_ready` | An OAuth client id and secret are set | The Google connection routes |

`WEAK_SECRETS` is `{"", "dev-secret", "change-me", "changeme", "secret"}`: the code's default and the placeholders in the
example env files. Anyone can read those, so they are never accepted as a key outside the demo.

### `production_problems()`: the start-up gate

`build_container` calls `settings.production_problems(serves_http)` first and raises `RuntimeError("Unsafe
configuration: ...")` if the list is not empty. Workers pass `serves_http=False`, which skips the checks that only matter
for a process that answers HTTP.

| Check | Why |
|---|---|
| `MODE` and `ENV` are known values | A typo would silently pick the wrong wiring |
| No `MODE=demo` with `ENV=prod` | The demo trusts an `X-Demo-User` header |
| No Firebase emulator with `ENV=prod` | The emulator's tokens are not signed |
| No `CORS_ORIGINS=*` with `ENV=prod` | Name the web app's origin |
| `PUBLIC_URL` set and `https://` with `ENV=prod` (plain `http` only for localhost on self-hosted) | Sign-in tokens travel with every request; emails and OAuth redirects need the real address |
| A GitHub App is set up → the webhook secret is strong (not weak, 16+ characters) | The secret signs webhook deliveries and the install link |
| Self-hosted: Firebase project, Firebase API key (or emulator), database URL | Sign-in and storage cannot work without them |
| Self-hosted: not (`SIGNUP_ENABLED=true` with `GITHUB_TOKEN` and `GITHUB_ALLOWED_OWNERS`) | Every organisation anyone creates could connect those owners' private repositories |
| `gcp`, serving HTTP: `OUTBOX_FLUSH_AUDIENCE` and `OUTBOX_FLUSH_INVOKER` | Without the scheduled flush, an event whose first send failed waits for the next GitHub webhook |
| `gcp`: project, bucket, database URL and the three model names | Required services |
| `gcp` with Google Calendar: `TOKEN_ENCRYPTION_KEY` | Instances share no disk to keep a generated key on |

`get_settings()` is cached with `lru_cache`. Tests that change the environment call `get_settings.cache_clear()`.

---

## 4. The container: container.py

[container.py](../../backend/app/container.py) does two jobs. `build_container` picks the adapters. The `Container` class
runs events and applies manager decisions.

### What each mode wires

| Port | `demo` | `self_hosted` | `gcp` |
|---|---|---|---|
| Model (`llm`) | `GeminiLLM` if a key is in the environment, else `OfflineLLM` | `GeminiLLM` if `GEMINI_API_KEY`, else `NoLLM` | `GeminiLLM` on Vertex AI |
| Search index (`index`) | `MemoryIndex` + `HashEmbedder` | `PgIndex` + `VertexEmbedder`, or `NoEmbedder` without a key | `PgIndex` + `VertexEmbedder` |
| Store (`store`) | `MemoryStore`, wrapped in `LiveStore` | `PgStore` | `PgStore` |
| Lifecycle store | `MemoryLifecycleStore` | `PgLifecycleStore` | `PgLifecycleStore` |
| Repositories (`repos`) | `LocalRepoSource` on `sample_repo/` | `RepoRouter(GitHubByUrl, GitHubApp or none)` | `GitHubApp` |
| Files (`blobs`) | `LocalBlobStorage(".blobs")` | `ServedLocalStorage(DATA_DIR)` | `GcsStorage` |
| Events (`bus`) | `InlineBus` | `LocalQueue` | `PubSubBus` |
| Checkpoints | `MemorySaver` | `PostgresSaver` | `PostgresSaver` |
| Mail | `mailer_for(settings)` in all three: SMTP when configured, else a mailer that sends nothing |

Three differences are worth remembering:

- `OfflineLLM` (demo) gives canned answers. `NoLLM` (self-hosted without a key) raises `AIUnavailable` with the fixed
  sentence `AI_OFF`. The self-hosted server still runs, and says the AI is off where it is needed.
- The demo seeds its data (`seed(...)`) and loses it on restart.
- `_postgres()` refuses to continue when `store.role_can_bypass_rls()` is true. If someone points the app at the owner
  role by mistake, row-level security would protect nothing, so the app does not start.

Two graphs are built from the same assistant: `graph` with a checkpointer, and `chat_graph` without one. A chat answer
never pauses for a person, so it has nothing to save.

`configure_tracing` turns LangSmith tracing on only when `LANGSMITH_TRACING=true`. It calls LangSmith's process-wide
switch, which overrides every `LANGSMITH_*` and `LANGCHAIN_*` variable. Tracing sends prompts to a third party, so off
has to mean off.

### `Container.handle(event)`: once per dedupe key

This is the one entry point for every event, whichever bus delivered it.

```
handle(event)
 ├─ type in SYNC_EVENTS?        → run now on chat_graph, inside call_budget(SYNC_CALL_S); return
 ├─ no dedupe key?              → run with a random thread id; return
 ├─ claim_event(key, lease)
 │    ├─ "done"                 → {"skipped": "duplicate"}
 │    ├─ "busy"                 → raise EventBusy
 │    └─ "new"                  → _Heartbeat keeps the lease alive → _start(event, key)
 │                                  ├─ raised  → release_event(key); re-raise
 │                                  └─ ok      → finish_event(key)
```

- **Synchronous events** (`SYNC_EVENTS` in [supervisor/graph.py](../../backend/app/agents/supervisor/graph.py): `chat.question`,
  `ask.route`, `ask.free` and five more) are answered while the caller waits. They are not deduplicated or checkpointed.
- **The lease.** `claim_event` writes the key with status `processing`. `_Heartbeat` is a thread that calls `touch_event`
  every `max(5, EVENT_LEASE_S / 3)` seconds. If the process dies, the heartbeat stops, the lease lapses after
  `EVENT_LEASE_S` (300 s), and the next delivery may claim it. A missed heartbeat risks a duplicate run, never a lost one.
- **The thread id is the dedupe key.** `_start(event, key)` uses the key as the LangGraph thread id. This is why an
  approval's `thread_id` can resume the right run later, and why the lifecycle code can ask `event_status("ob:<id>")` to
  learn whether a plan is being drafted.

`_start` looks at what an earlier delivery left on that thread before running anything:

| State of the thread | What `_start` does | Why |
|---|---|---|
| Paused at an approval (`interrupts`) | Returns `{"skipped": "waiting for approval"}` | Deciding the approval resumes it |
| Stopped mid-graph (`state.next`) | `graph.invoke(None, cfg)`: continues from the last checkpoint | Never restart on top of saved state |
| Finished but its cleanup failed (`state.values`) | Deletes the thread, then runs clean | Leftover state would corrupt the new run |
| Nothing | Runs the event | The normal case |

`mail.*` events skip the assistant altogether: `_mail` hands them to the mailing service.

`_forget_if_finished` deletes a run's checkpoints once it ends. Checkpoints exist only so a run can pause and resume.

### `Container.decide(approval, decision)`: one decision per approval

A manager's click resumes a paused run. The risks are a double click, a stale approval, and a click that arrives before the
run has reached its pause. `decide` handles each:

| Situation | Result | HTTP answer (from `routes.decide`) |
|---|---|---|
| The run has not paused yet (`state.next`, or its event is still `busy`) | `ApprovalNotReady` | `409 still_preparing` |
| `claim_approval` fails and the approval was closed by the system | `ApprovalStale` | `409`, "out of date" |
| `claim_approval` fails otherwise | `ApprovalBusy` | `409`, "already being applied" |
| Claimed, but the run is not waiting on this approval | The approval is marked rejected; `ApprovalStale` | `409` |
| Claimed and waiting | `resume(...)` with the **stored** decision | `200` |
| `resume` raises | `release_approval`; the error propagates | `500` |

The claim stores the first decision it was given. A retry always re-applies that stored decision, because LangGraph
replays the first resume value if the run failed after receiving it. A retry with a different decision would be reported
as applied and not be.

---

## 5. Ports: ports.py

[ports.py](../../backend/app/ports.py) is the contract between the app and everything outside it. It has two kinds of
content: data models (pydantic classes) and protocols.

### The protocols

| Protocol | What it offers | Used by |
|---|---|---|
| `Index` | Store and search pieces of text; look up code by path and symbol | Agents and the insight views |
| `Store` | People, projects, repositories, KT sessions, tasks, approvals, reviews, chat, notifications, runs, and event claims | Agents |
| `AppStore(Store)` | Everything else the API needs: accounts, invitations, members, documents, installations, the outbox, scheduling | The API only |
| `LifecycleStore` | Programs, templates, milestones, blockers, check-ins, surveys, feedback, handovers, metrics, and organisation export and deletion | `app/lifecycle` and the lifecycle agents |
| `RepoSource` | Read a repository. **It has no write methods.** | Agents |
| `Scheduler` | Create and cancel a calendar event; find and download its recording | The scheduling service |
| `BlobStorage` | `get`, `put`, `delete` | Agents and the API |
| `Mailer` | `send`, and `enabled` | The mailing service |
| `EventBus` | `publish` | The outbox |

The split between `Store` and `AppStore` is a safety line. An agent holds a `Store`. It cannot create an organisation,
change a role or bind a GitHub installation, because those methods are not on the protocol it was given.

### Models worth knowing

- `User.role` is one of `manager`, `new_hire`, `expert`, `admin`, `executive`. `status` is `active` or `deactivated`.
- `Onboarding` is the core row linking a hire, a manager and a project. `OnboardingProgram` is a side table keyed by the
  same id. It was added later so that `Onboarding`, which the agents use, did not have to change.
- `Approval.kind` is one of six: `task_plan`, `review`, `kt_followup`, `rerecord`, `doc_edit`, `template_change`.
  `Approval.decision` is the first decision claimed for it.
- `Meeting.status`: `scheduled` → `processing` → `ready`, or `ended` (held, no recording came), `failed`, `cancelled`.
  `live` remains for old rows; nothing sets it for new sessions.
- `SurveyResponse` has no person id, by design.

### Small shared rules

- `norm_question()` decides when two questions count as the same. `PgStore` spells the same rule in SQL.
- `link_matches()` decides when a notification link names a target. It never matches a longer id that starts the same.
- `CODE_CHUNK_CAP = 50_000`: the most pieces of code one read returns, cut at a whole file.

### The exceptions that carry meaning

| Exception | Meaning | Who reacts |
|---|---|---|
| `AIUnavailable` | No model is set up. Retrying will not help | The self-hosted queue gives the event up at once |
| `RepoBusy` | Another indexing run holds this repository | The worker hands the event back for later |
| `LastAdmin` | The change would leave no active admin | The members API answers `409 last_admin` |
| `SourceTooLarge` | A repository is over an indexing limit | Indexing stops before anything is embedded |

---

## 6. Adapters

### 6.1 In memory: memory.py and lifecycle_memory.py

[memory.py](../../backend/app/adapters/memory.py) implements every port with dictionaries. It serves the demo and almost every
test. [lifecycle_memory.py](../../backend/app/adapters/lifecycle_memory.py) does the same for the lifecycle store and reads a
few of the core store's dictionaries directly.

| Class | Port | Notes |
|---|---|---|
| `MemoryIndex` | `Index` | Hybrid search with a hashing embedder and BM25, fused the same way as Postgres |
| `MemoryStore` | `AppStore` | Includes the outbox and event claims, so the demo exercises the same paths |
| `LocalRepoSource` | `RepoSource` | Reads `sample_repo/`. `head_sha` is a hash of the folder's contents |
| `LocalBlobStorage` | `BlobStorage` | `_path` resolves the real path and refuses one that escapes the storage root |
| `ServedLocalStorage` | `BlobStorage` | Adds signed, expiring links (`signed_url`, `verify`) served at `GET /api/blobs/<token>` |
| `MemoryScheduler` | `Scheduler` | Stable stand-in Meet links; in the demo every past session "has" a recording |
| `InlineBus` | `EventBus` | Calls the handler at once, in the same thread |

The memory adapters are not second-class. They must behave like Postgres, because the tests of the agents run on them.
Where a rule lives in SQL (a unique index, a lock), the memory adapter imitates its outcome.

`signing_key()` returns `BLOB_SIGNING_KEY`, or generates a key once into `DATA_DIR/.signing-key` with file mode `0600`.

### 6.2 Postgres: postgres.py

[postgres.py](../../backend/app/adapters/postgres.py) implements `Index` (`PgIndex`) and `AppStore` (`PgStore`).

**How the organisation reaches a query.** `current_org` is a `ContextVar`. Sign-in, a worker, or a webhook sets it.
`_Db.tx()` opens a transaction and runs:

```sql
SELECT set_config('app.org_id', :org, true)
```

The `true` makes the setting local to that transaction. Every policy compares a row's `org_id` to this setting. So
the organisation is set per transaction, and a pooled connection never carries one request's organisation into the next.

When `current_org` is `None`, nothing is set, the policies compare against `NULL`, and the query sees no rows. Forgetting
to set the organisation fails closed.

**`reading()`.** A page often makes ten reads. Inside `with store.reading():` every statement that thread makes runs on one
connection, in one `REPEATABLE READ, READ ONLY` transaction. That is one pool checkout and one snapshot instead of ten.
The pin is recorded per engine and per thread, so other threads never share it. Keep only fast reads inside it: the
connection is held until the block ends.

**Session limits.** `session_options()` sets three PostgreSQL timeouts on every app connection:

| Setting | Default | Effect |
|---|---|---|
| `statement_timeout` | 30 s | A slow query is cancelled |
| `lock_timeout` | 5 s | A wait for a lock held by a migration fails fast |
| `idle_in_transaction_session_timeout` | 60 s | A transaction left open is closed |

Together they stop one slow query or one migration's lock from holding every pooled connection.

**Quiet errors.** `make_engine` passes `hide_parameters=True`, and `quiet_errors()` trims PostgreSQL's error to its
primary message. A default error quotes the row (`Key (email)=(new.hire@example.com) already exists`). That text would
otherwise reach a log line, a run record or a manager's notification.

**Malformed ids.** The `_ids` decorator and `_is_uuid` turn a bad id from a URL into "not found" instead of a database
error.

**Decisions made in SQL.** Several rules are single statements so that two processes cannot both win:

| Method | What the statement guarantees |
|---|---|
| `claim_event` | Insert the key, or take it over only if its lease has lapsed. One statement, so one winner |
| `claim_approval` | Sets `claimed_at` and stores the decision only while `pending` and unclaimed (or claimed over 5 minutes ago) |
| `create_onboarding` | `ON CONFLICT ... WHERE status = 'active' DO NOTHING`: one active onboarding per person and project |
| `create_repo` | `ON CONFLICT (project_id)`: one repository per project |
| `create_meeting` | `ON CONFLICT DO NOTHING`: one KT session per planner session and per calendar event |
| `supersede_review_approvals` | Closes every pending review of a task except the newest pull request state's |
| `outbox_due` | `FOR UPDATE SKIP LOCKED`: two flushers never take the same row |
| `ingest_lock` | `pg_try_advisory_lock`: one indexing run per repository; a second raises `RepoBusy` instead of waiting |
| `leave_organization`, `_change_member` | Lock the organisation's member rows first, so two admins cannot remove each other at once |

`ingest_lock` does not wait on purpose. A worker waiting for a large repository's index would hold one of its few message
slots and a connection for the whole time.

**Search.** `_hybrid` runs a vector search (top 50) and a keyword search (top 50) and fuses the two ranks as
`1 / (60 + rank)` each. `keyword_query` joins the question's words with OR, because a question's words rarely all appear
in one piece of text. The retrieval design is covered in a later part.

### 6.3 Postgres for the lifecycle: lifecycle_pg.py

[lifecycle_pg.py](../../backend/app/adapters/lifecycle_pg.py) is `PgLifecycleStore`. It shares `_Db`, so every statement runs
under the same row-level security. Its `org_id` arguments only fill in new rows.

Its three notable parts are `_org_tables`, `export_org` and `delete_org`, explained in
[section 12](#12-export-deletion-and-erasure). `program_by_token` and `lifecycle_orgs` call definer functions, because they
run before any organisation is known.

### 6.4 Google Cloud: gcp.py

[gcp.py](../../backend/app/adapters/gcp.py) holds `GcsStorage` and `PubSubBus`.

- `TOPICS` maps an event type to one of three topics: `ingestion`, `meetings`, `reviews`. Each topic has its own worker
  deployment. **An event type missing from `TOPICS` raises `KeyError` on publish.**
- `PubSubBus.publish` waits at most 8 seconds. It is short on purpose: the event is safe in the outbox, and GitHub gives a
  webhook only 10 seconds.
- `signed_upload` makes a signed URL the browser uploads one file to. The signature binds the content type, the exact
  size and "create only". The URL cannot store anything else, or replace a file that is there.
- `open_writer` streams a large file in 8 MiB parts and never replaces an existing object.
- On Cloud Run there is no key file. `_signing()` has the service account sign through IAM.

### 6.5 GitHub: github.py and github_url.py

[github.py](../../backend/app/adapters/github.py) is the GitHub App client. [github_url.py](../../backend/app/adapters/github_url.py)
reads repositories connected by URL in self-hosted mode. **Neither has a method that writes to GitHub.**

| Piece | What it does |
|---|---|
| `GitHubApp._token` | An installation token, cached until two minutes before it expires |
| `read_tarball` | Streams a repository's tarball to a temporary file, then keeps indexable text. Stops at 500 MB compressed, and with `SourceTooLarge` at twice the indexing limits |
| `verify_signature` | HMAC-SHA256 of the raw body, compared in constant time |
| `events_from_webhook` | Turns a GitHub payload into events. A push to a non-default branch yields nothing. A malformed pull request payload yields nothing, never a `500` |
| `oauth_user` | Swaps GitHub's one-time code for the identity of the person in the browser. The token is used once and never stored |
| `installation_account`, `user_admin_of` | Ask GitHub who an installation belongs to and whether a user administers that account |
| `GitHubByUrl._cached` | Sends the ETag of the last answer. GitHub answers `304`, which does not count against the API limit |
| `GitHubByUrl.compare` | The paths changed between two commits, or `None` when GitHub cannot say (a force-push, or 300+ files). `None` means "index everything" |
| `GitHubLimited` | The API limit is used up. It carries the reset time |
| `RepoRouter` | Sends each call to the App client when the repository has an installation id, else to the URL client |
| `parse_repo` | Accepts `owner/repo` or a GitHub URL; refuses anything else |

Why "twice the limits" in `read_tarball`: the exact limits count only what is actually indexed, which is decided later.
Reading stops at double so a repository inside the limits is never refused early, while memory stays bounded.

### 6.6 Calendars: google_calendar.py and calendly.py

[google_calendar.py](../../backend/app/adapters/google_calendar.py) implements `Scheduler` over plain HTTPS.

- `auth_url` asks for `access_type=offline` and `prompt=consent`, so Google returns a refresh token every time.
- `exchange_code` refuses a grant that lacks the Calendar or Drive scope.
- `event_body` uses the session's id as the Meet `requestId`. A retried insert never makes a second conference.
- `_call` retries once with a fresh token on a `401`. `GoogleAuthError` means "connect it again in Settings".
- `find_recording` reads the video Meet attached to the calendar event. `download` streams it and stops past `max_bytes`.
- Only the refresh token is stored, and encrypted. Access tokens live in memory, keyed by a digest.

[calendly.py](../../backend/app/adapters/calendly.py) handles a different flow. Booking happens on Calendly's page, so the app
learns of it from a signed webhook.

- `booking_link` tags the link with `utm_source=welcome-wave`, the project in `utm_campaign` and the planner session in
  `utm_content`. Calendly returns those in the webhook. That is how a booking finds its project.
- `parse_webhook` returns `None` for a booking without those tags: someone booked the person directly, and it is not a KT
  session.
- `verify_signature` refuses a signature more than `TOLERANCE_S` (180 s) old, so a captured delivery cannot be replayed.
- `clean_link` accepts only `calendly.com` hosts and strips the query.

### 6.7 Mail: mailer.py

[mailer.py](../../backend/app/adapters/mailer.py) has `SmtpMailer` and `OutboxMailer`.

- Port 465 opens TLS from the start. Any other port connects and upgrades with STARTTLS before logging in.
- `build` refuses a header value with a line break. That closes header injection.
- A failure is logged as its error type and the recipient's domain. The subject, body, links and address are never logged.
- `OutboxMailer` keeps messages in a list and has `enabled = False` by default. Callers check `enabled` and send nothing.

### 6.8 Reading the audit log: audit_store.py

[audit_store.py](../../backend/app/adapters/audit_store.py) is a set of functions that take the store, not store methods. That
let the audit API be added without changing the adapters. See [section 11](#11-the-audit-log).

---

## 7. The database

### 7.1 Three roles

| Role | Can log in | Rights | Used by |
|---|---|---|---|
| The owner | Yes | Owns the tables; creates and alters them | The migrator only (`DATABASE_URL_OWNER`) |
| `ww_app` | Yes | Read and write rows. Cannot bypass row-level security, create tables, create temporary tables, read `schema_migrations`, or change or delete audit rows | The API and the workers |
| `ww_definer` | No | Owns the definer functions. Has its own policy that sees every organisation | Nobody directly |

### 7.2 Row-level security

Every table with an `org_id` gets the same three statements:

```sql
ALTER TABLE t ENABLE ROW LEVEL SECURITY;
ALTER TABLE t FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON t
    USING      (org_id = nullif(current_setting('app.org_id', true), '')::uuid)
    WITH CHECK (org_id = nullif(current_setting('app.org_id', true), '')::uuid);
```

- `USING` filters what a query can read, update or delete.
- `WITH CHECK` refuses a row written for another organisation.
- `FORCE` applies the policy to the table's owner too. Without it the owner role would pass straight through.
- `nullif(..., '')` turns "not set" into `NULL`. A comparison with `NULL` is never true, so no rows are visible.

Two tables differ slightly. `agent_runs` and `audit_log` allow `org_id IS NULL` in `WITH CHECK`, because a row can be
written before the organisation is known. Such a row is still unreadable by any organisation. `organizations` is keyed
by `id` instead of `org_id`.

### 7.3 Definer functions

Some questions must be asked before the organisation is known: who is this login, which organisation owns this
repository, which sessions are waiting for a recording. Row-level security would hide every row at that moment.

The answer is a small set of `SECURITY DEFINER` functions owned by `ww_definer`. Each returns as little as possible,
usually ids.

| Function | Added in | Asked by | Returns |
|---|---|---|---|
| `auth_lookup(uid)` | 001, redefined 013 | Sign-in | The user row for a login, with `deactivated_at` |
| `project_org(project)` | 001 | Workers, the local queue, access checks | An organisation id |
| `repo_by_full_name(name)` | 001, redefined 002 | The GitHub webhook | Repository, organisation, project and installation ids |
| `installation_org(id)` | 002 | Connecting a repository | An organisation id |
| `bind_installation(...)` | 002 | The GitHub callback | Whether this organisation now owns the installation |
| `create_organization(...)` | 002 | Sign-up | New user, organisation and project ids |
| `pending_invitations(email)` | 003, redefined 004 | First sign-in | One row per organisation that invited the person |
| `accept_invitations(uid, email, name, org)` | 003, redefined 005 | Joining | The user id, or `NULL` |
| `unbind_installation(id)` | 003 | GitHub's "uninstalled" webhook | Nothing |
| `watched_repositories()` | 009 | The repository watcher | Repositories connected by URL |
| `preboarding_lookup(hash)` | 010 | The pre-boarding portal | Onboarding id, organisation id, expiry |
| `meetings_awaiting_recording(...)` | 011 | The recording poller | Ids and times of ended sessions |
| `purge_audit_log(before)` | 012 | The retention purge | Rows removed per organisation |
| `lifecycle_orgs()` | 014 | The daily tick | Organisation ids with a program in progress |
| `delete_org_audit(org)` | 015 | Organisation deletion | Rows removed |
| `organization_exists()` | 016 | The self-hosted sign-up gate | One boolean |

Four rules keep these functions from becoming a hole:

1. **`SET search_path = public, pg_temp`.** Without `pg_temp` named last, PostgreSQL looks in the caller's temporary
   schema first. The app could create a temporary table called `invitations` and have a definer function read it.
   Migration 004 fixed every function, and `migrate.verify()` refuses to finish if one lacks the setting.
2. **`ww_app` cannot create temporary tables** (`revoke_temp`). A second lock on the same door.
3. **`EXECUTE` is revoked from `PUBLIC`** and granted to `ww_app` by the migrator from `DEFINER_FUNCTIONS`.
4. **Guards inside the function.** `purge_audit_log` never deletes anything younger than 30 days, whatever it is asked.
   `delete_org_audit` works only for the current organisation, and only after its `organizations` row is gone.

`organization_exists()` is the one exception to rule 3. It returns a single boolean and nothing about any organisation,
so `EXECUTE` stays with `PUBLIC` and it is not in `DEFINER_FUNCTIONS`.

One thing to know before adding a definer function. `ww_definer` sees every organisation through a policy named
`definer_all`. Migration 002 created that policy on the tables that had row-level security at the time; 010 added it for
`onboarding_programs`. A table added later does not have it. A new definer function over a newer table needs its own
`GRANT` and `CREATE POLICY definer_all ... TO ww_definer`, as 010 does, or it will see no rows.

### 7.4 The 17 migrations

All are in [backend/db/migrations/](../../backend/db/migrations/). An applied file is never edited.

| File | What it adds |
|---|---|
| [001_init.sql](../../backend/db/migrations/001_init.sql) | The `vector` and `pgcrypto` extensions, 12 enums, 24 tables (people, projects, repositories, documents, `chunks`, KT sessions and their notes, `kt_links`, tasks, approvals, reviews, chat, notifications, runs, `processed_events`, `audit_log`). Row-level security on 20 of them. `auth_lookup`, `project_org`, `repo_by_full_name` |
| [002_production.sql](../../backend/db/migrations/002_production.sql) | Leases on `processed_events` (`status`, `claimed_at`). `approvals.claimed_at`. `invitations`, `github_installations`. Row-level security on the tables 001 left open (`agent_runs`, `audit_log`, `organizations`). Sign-up and invitation functions. The `ww_definer` role and its `definer_all` policies |
| [003_review_fixes.sql](../../backend/db/migrations/003_review_fixes.sql) | Invitations accepted explicitly, per organisation. One repository per project (existing rows reconciled, then a unique index). `github_installation_senders` and `unbind_installation` |
| [004_round3.sql](../../backend/db/migrations/004_round3.sql) | `search_path = public, pg_temp` on every definer function. `processed_events.lease_s`. `approvals.decision`. One active onboarding per person and project. The `outbox` table |
| [005_rejoin.sql](../../backend/db/migrations/005_rejoin.sql) | Someone who left can be invited back; the newest invitation sets their role |
| [006_insights.sql](../../backend/db/migrations/006_insights.sql) | Session status `cancelled`. `meetings.kt_session_key` with one live booking per planner session. An index to read code in path order |
| [007_journeys.sql](../../backend/db/migrations/007_journeys.sql) | Session status `ended` |
| [008_kt_review.sql](../../backend/db/migrations/008_kt_review.sql) | Approval kind `kt_followup` |
| [009_live.sql](../../backend/db/migrations/009_live.sql) | The `ww_live_notify()` trigger function and triggers on 15 tables. `watched_repositories()` |
| [010_lifecycle.sql](../../backend/db/migrations/010_lifecycle.sql) | Role `executive`. 19 lifecycle tables (settings, teams, templates, programs, milestones, access items, acknowledgements, environment checks, area progress, blockers, summaries, check-ins, surveys, feedback, verified answers, expert preferences, handovers, metric events, API tokens), each with row-level security. Live triggers. `preboarding_lookup` |
| [011_scheduling.sql](../../backend/db/migrations/011_scheduling.sql) | Removes the LiveKit columns and lookup. Adds `provider`, `join_url`, `calendar_event_id`, times and attendees to sessions. `calendar_connections`, `scheduling_links`. `meetings_awaiting_recording` |
| [012_audit_retention.sql](../../backend/db/migrations/012_audit_retention.sql) | Two indexes for reading the audit log. `purge_audit_log` with its 30-day floor |
| [013_members.sql](../../backend/db/migrations/013_members.sql) | `users.deactivated_at`. `auth_lookup` returns it. A live trigger on `users` |
| [014_agents.sql](../../backend/db/migrations/014_agents.sql) | Approval kinds `rerecord`, `doc_edit`, `template_change`. `org_settings.agent_modes`. `expert_requests.draft`. `onboarding_programs.completion_draft`. `doc_edits`. `lifecycle_orgs` |
| [015_audit_append_only.sql](../../backend/db/migrations/015_audit_append_only.sql) | Revokes `UPDATE` and `DELETE` on `audit_log` from `ww_app`. `delete_org_audit` |
| [016_signup_gate.sql](../../backend/db/migrations/016_signup_gate.sql) | `organization_exists` |
| [017_chat_writer.sql](../../backend/db/migrations/017_chat_writer.sql) | `chat_messages.writer` and `chat_messages.crew` |

Several unique indexes are the real guard behind an API rule. If you remove one, the API check above it becomes a race:

| Index | Rule it enforces |
|---|---|
| `repositories_one_per_project` | One repository per project |
| `onboardings_one_active` | One active onboarding per person and project |
| `meetings_one_live_kt_session` | One live booking per planner session |
| `meetings_calendar_event` | One session per calendar event (a redelivered webhook makes one) |
| `blocker_alerts_one_open` | One open alert per rule and subject |
| `program_templates_one_active` | One active version per template key |
| `reviews (task_id, head_sha)` | The same commit is never reviewed twice |
| `outbox.dedupe` | The same event is recorded once |

### 7.5 The migrator: migrate.py

[migrate.py](../../backend/app/migrate.py) runs as the owner, once per deploy. `run()` does four things under one advisory
lock (`RUN_LOCK`), so two deploys started together apply each file once.

1. **`apply_migrations`.** Each new file runs in its own transaction with `lock_timeout` set to
   `MIGRATION_LOCK_TIMEOUT` (10 s). A migration stuck behind a long query fails and the job is run again. It does not
   queue every later query of the site behind itself. Each file's SHA-256 is recorded. **An applied file whose content
   changed stops the run.** That is what makes "never edit an applied migration" a fact and not a habit.
2. **`setup_checkpoints`.** Creates LangGraph's checkpoint tables. The app role cannot create tables, so the app cannot do
   this itself.
3. **`ensure_app_role`.** Creates or updates `ww_app`, then `grant_app` grants data rights on every table, revokes
   everything on `schema_migrations`, revokes `UPDATE`, `DELETE` and `TRUNCATE` on the append-only tables, and grants
   `EXECUTE` on each definer function.
4. **`verify`.** Refuses to finish when any of these is true:
   - `ww_app` can bypass row-level security or is a superuser.
   - A table has an `org_id` but no forced row-level security, or forced security with no policy.
   - A table has no `org_id` and is not listed in `GLOBAL_TABLES`.
   - A definer function lacks `search_path = public, pg_temp`.
   - `ww_app` can change or remove audit rows.

`GLOBAL_TABLES` names the tables that belong to no organisation, each with a reason: `schema_migrations`, `outbox`,
`processed_events`, `github_installation_senders`, and LangGraph's four checkpoint tables. They hold ids only. A new
table is either organisation-scoped or added to this list with a reason a reviewer can check. There is no third option.

---

## 8. The API layer

### 8.1 Sign-in: auth.py and firebase_tokens.py

[auth.py](../../backend/app/api/auth.py) answers "who is calling, and for which organisation".

**`current_user`** is the dependency almost every route uses. Its steps:

1. `_set_tenant(None)`: clear any organisation left on this task.
2. `_resolve(...)` in the thread pool: verify the token and find the user.
3. `_set_tenant(user.org_id)`: from here every query runs under that organisation's policies.
4. `request.state.user = user`: the request log and the rate limiter read it.
5. Refuse a write from an `executive`.

It is `async` on purpose. FastAPI runs a plain `def` dependency inside a *copy* of the request's context. An organisation
set there would vanish before the route ran, and row-level security would hide every row. An `async` dependency runs
on the request's own task, so every later dependency and the route inherit the setting.

**`_resolve`** in the demo reads the `X-Demo-User` header. Elsewhere it verifies the bearer token, then:

| Finding | Answer |
|---|---|
| A user row is bound to this login | The user (or `403 deactivated`) |
| No row, and the email is not verified | `403 email_not_verified` |
| No row, but live invitations exist for the email | `403 invitation_pending`, with the invitations |
| No row and no invitation | `403 no_account`; the web app offers sign-up |

A person is never joined to an organisation silently. They see who invited them and choose
(`POST /api/invitations/accept`). A join can be undone (`POST /api/me/leave`).

Three small sets define the exceptions:

- `READ_ONLY_ROLES = {"executive"}`: every method outside `SAFE_METHODS` is refused, for every route, whatever the route
  itself allows.
- `READ_ONLY_ALLOWED`: an executive may still leave and mark their own notifications read.
- `DEACTIVATED_ALLOWED = {"/api/me/leave"}`: the one thing a deactivated member may do.

**`require(*roles)`** lets the named roles through, and always an `admin`.

**`check_project`** returns `404` when the project is in another organisation *or* the caller is not a member. A
non-member cannot tell whether the project exists. A database error is not caught: it is a `500`, not a `404`.

**[firebase_tokens.py](../../backend/app/firebase_tokens.py)** verifies an ID token without Google Cloud credentials. The
Admin SDK's own verifier needs credentials that a Mac or a self-hosted server does not have, so every sign-in failed
there. A Firebase token is an RS256 JWT signed with one of Google's published keys, and checking it needs no credentials.
`verify()` checks the algorithm, signature, audience, issuer, subject, expiry and `auth_time`.

`_Certs` caches Google's certificates for as long as Google's `Cache-Control` allows. Its design choices:

- One thread downloads at a time; the others keep using the cached certificates. A slow Google never queues sign-ins.
- A failed download keeps the last good certificates and retries after `RETRY_AFTER_FAILURE_S` (30 s).
- An unknown key id triggers a download at most once per `UNKNOWN_KID_REFETCH_S` (60 s). Made-up key ids cannot make the
  server download on every request.

### 8.2 The main API: routes.py

[routes.py](../../backend/app/api/routes.py) is deliberately thin: validate, check permission, then call the store or publish
an event. Its groups and the decisions worth knowing:

| Group | Routes | The decision that matters |
|---|---|---|
| Public | `GET /api/config` | What the web app needs before sign-in. Nothing secret |
| Accounts | `/signup`, `/invitations/accept`, `/me`, `/me/leave` | `_signup_allowed`: on self-hosted, sign-up creates the first organisation only. If the check itself fails, the answer is "closed" |
| Projects and invitations | `/projects...` | Managers invite; inviting the same email to the same project again refreshes the open invitation instead of adding one |
| Erasure | `POST /org/people/erase` | See [section 12](#12-export-deletion-and-erasure) |
| GitHub App | `/github/install-url`, `/github/callback`, `/github/installations`, `/github/repositories` | Who may bind an installation (below) |
| Repositories | `POST /projects/{id}/repositories` | One repository per project; private repositories by URL need an allowed owner |
| Documents | upload, view, file, delete | Only `.pdf`, `.docx`, `.md`, `.markdown`, `.txt` (`ALLOWED_DOCS`) |
| Onboardings | `POST /projects/{id}/onboardings` | An older entry point; enrolment ([13.4](#134-enrolment-and-the-plan-enrolmentpy)) is the current one |
| Ask | `/chat`, `/chat/history`, `/chat/{id}/feedback` | Two limits (below) |
| KT sessions | `/meetings...` | Book, cancel, import a recording, read notes, delete a recording |
| Code | `/code/files`, `/code/file`, `/code/explanations`, `/coverage` | Read from the index |
| Tasks | `GET /tasks`, `PATCH /tasks/{id}` | Who sees and moves what (below) |
| Approvals | `GET /approvals`, `POST /approvals/{id}/decide` | Validate, then `Container.decide` |
| Experts | `/experts/requests...` | A person answers a question sent to them |
| Other | `/dashboard`, `/runs`, `/notifications...` | Manager views and the inbox |

**Binding a GitHub installation (`github_callback`).** The risk: a person who merely collaborates on a repository connects
a company's private code to their own organisation. The checks, in order:

1. The `state` is an HMAC of project, user and expiry, keyed from the webhook secret. It expires after an hour.
2. An installation already bound to another organisation is refused.
3. The person in the browser must be the GitHub user who installed the app. GitHub's signed `installation.created`
   webhook records who that was (`github_installation_senders`).
4. If that webhook was never recorded, the person must administer the GitHub account the installation is on.
5. If neither is known and the installation is under five minutes old, the answer is `409 installation_unconfirmed` and
   the browser retries. GitHub's code works once, so everything that can still change is checked before it is spent.

Every refusal is the same `403 not_installer`. It never says who installed the app or which organisation has it.

**Connecting by URL (`connect_repo`, self-hosted).** A private repository is accepted only when its owner is in
`GITHUB_ALLOWED_OWNERS`. Otherwise the answer is exactly the "no public repository" `404`, so nobody learns what the
server's token can read. `valid_branch` allows only characters that cannot change the API path the name is put into.

**Two limits on questions.**

- `chat_allowed`: a sliding window of `CHAT_RATE_PER_MINUTE` (12) per person. The window belongs to the container, so two
  apps in one test process do not share it.
- `guide_slot`: at most `CHAT_CONCURRENCY` (8) questions answered at once in this process. One more is refused with `429`
  straight away. It is never queued, because a waiting question would hold a thread that sign-in also needs.

**Tasks.** `list_tasks` shows managers everything. Anyone else sees only approved tasks, and only of onboardings they are
related to: a hire their own, a mentor their mentees'. Two tables define who moves a task:

```python
HIRE_MOVES    = {"approved": {"in_progress"}, "in_progress": {"submitted"},
                 "changes_requested": {"in_progress", "submitted"}}
MANAGER_MOVES = {"submitted": {"done", "changes_requested"}}
```

A manager cannot mark a submitted task done while a pull request review of it waits in Approvals. They decide it there.

**Deciding an approval (`decide`).** The route validates the body against the approval's own payload: task ids must be
ones the plan proposed, milestone keys must be ones it planned. A `template_change` needs an admin. The audit entry
records `reapplied: true` when a retry re-applied the first decision instead of this request's.

### 8.3 The lifecycle API: lifecycle.py

[lifecycle.py](../../backend/app/api/lifecycle.py) is the HTTP face of `app/lifecycle`. Two helpers carry its rules.

**`_hire(c, user, onboarding_id, *allowed)`** works out how the caller relates to an onboarding: `hire`, `mentor` or
`manager`. Then:

- No relation at all → `404`, as if the onboarding did not exist.
- Related, but not in one of the `allowed` ways → `403`.

So a mentor who tries a manager-only action learns they may not. A stranger learns nothing.

**`feature(name)`** returns `404` while a feature flag is off ([13.11](#1311-flags-flagspy)).

The pre-boarding routes (`/api/preboarding/{token}/...`) take no sign-in. `_portal` resolves the token, sets the
organisation from it, and loads the onboarding. Unknown, replaced and expired links all give the same `404`.

### 8.4 Members: members.py

[members.py](../../backend/app/api/members.py) lets admins list members, change a role, deactivate and reactivate.

- A member is a user with a login bound. Someone who left, or was enrolled and has not joined, is not one (`404`).
- A change that would leave no active admin answers `409 last_admin` and changes nothing. The store enforces this under a
  row lock, so two admins demoting each other at the same moment cannot both succeed.
- An admin cannot deactivate themselves (`409 self`).
- Deactivating returns `needs_reassignment`: the person's open questions and the onboardings they mentor. Nothing is
  moved automatically. It lists ids and dates, never what a question says.
- Every change is audited and the member is notified.

### 8.5 Recording uploads: uploads.py

[uploads.py](../../backend/app/api/uploads.py) moves a recording of hundreds of megabytes without passing it through the API
on Google Cloud. A request through Cloud Run is capped at 32 MiB.

| Step | Call | What happens |
|---|---|---|
| 1 | `POST /api/meetings/{id}/recording/upload` | Checks who and which session, then returns where to upload and a signed **upload id**. Nothing is stored |
| 2 | `PUT` to Cloud Storage, or `POST /api/meetings/{id}/recording` with `X-Upload-Token` on self-hosted | The file itself. No sign-in header |
| 3 | `POST /api/meetings/{id}/recording/complete` | Checks the file, claims the session, publishes `meeting.recording_ready` |

The upload id is the session, the person, the stored path, the type, the size, the length and an expiry, signed with
HMAC. `make_upload_id` and `read_upload_id` are the pair. The key is derived from `TOKEN_ENCRYPTION_KEY`, which is why
`gcp` mode refuses step 1 without it: step 3 may land on another instance.

`accept()` is where a file is trusted. It checks that the file exists, is not empty, is under the limit, has the size that
was signed, and **starts like a video of its type** (`looks_like_video`: an EBML header for WebM and MKV, an ISO box for
MP4 and MOV). A file that fails any check is deleted. Then `claim_meeting_import` moves the session to `processing` in
one statement. If another upload or a Drive import got there first, this file is deleted and the answer is `409`.

On self-hosted, `_receive` opens the destination with mode `xb`. An upload can never replace a stored file. A dropped
connection deletes the partial file.

### 8.6 Webhooks: webhooks.py

[webhooks.py](../../backend/app/api/webhooks.py) receives GitHub and Calendly deliveries. Both arrive before the organisation
is known.

**GitHub.** The route answers `404` while `github_webhooks_ready` is false. Then:

1. Verify the signature over the raw body.
2. `installation` events: record who installed the app, or unbind on `deleted`.
3. For other events, `repos_by_full_name` finds every project connected to that repository.
4. **A repository connected by URL is skipped.** It has no installation id. A delivery naming no installation would
   otherwise match it, because both sides would be empty.
5. Set the organisation, build events with `events_from_webhook`, add the repository id to each dedupe key, and `publish`.
6. The pull request link is built here from the repository name and number. It is never taken from the payload.

The event is recorded in the outbox before the `202`. GitHub does not redeliver a delivery it believes succeeded. After
answering, the route flushes up to three older outbox rows as a small catch-up between scheduled flushes.

**Calendly.** `404` without a signing key, `401` on a bad or old signature. A delivery that is not a tagged booking is
acknowledged and ignored, because Calendly retries anything not answered with a `2xx`.

### 8.7 Live updates: live.py and stream.py

[live.py](../../backend/app/live.py) makes pages stay current without reloading. The rule: **what travels is the fact of a
change, never the data.** A message is `{"t": topic, "p": project id}`. The browser then reloads that topic through the
normal API, which applies every permission check as usual.

| Piece | Job |
|---|---|
| `ww_live_notify()` trigger (migration 009) | After any insert, update or delete on a watched table, `pg_notify('ww_live', ...)` with organisation, topic, project and, for notifications, the recipient |
| `PgListener` | A thread that runs `LISTEN ww_live` on its own connection and passes each message to the hub. After a reconnect it tells every browser to reload everything, because what changed meanwhile is unknown |
| `LiveStore` | Demo only. Wraps the memory store; a method whose name starts with a write verb announces a topic guessed from its name (`topic_of`) |
| `LiveHub` | Holds the connected browsers. `publish` may be called from any thread; it hops onto the event loop with `call_soon_threadsafe` |
| `Subscriber` | One browser: organisation, user, a queue of at most `QUEUE_MAX` (200) |

`_dispatch` filters each message: the organisation must match, and a `notifications` message goes only to its recipient.
A browser whose queue is full has its backlog replaced with one "reload everything".

The triggers fire whichever process made the change: the API, a worker or a webhook. PostgreSQL sends identical payloads of
one transaction once, so a bulk update is one message.

Agent steps are the single exception that carries data. They are published on `agent:<run_id>`, in the API process, to
the run's own user only, and only to a stream that asked for that topic.

[stream.py](../../backend/app/api/stream.py) serves `GET /api/live` as Server-Sent Events. The stream ends after `STREAM_S`
(15 minutes) so the browser reconnects with a fresh sign-in token; Firebase tokens last an hour. It sends a comment line
every `PING_S` (20 s) so proxies keep the idle connection open. `GET /api/blobs/{token}` serves a self-hosted recording
behind a signed link. The link is the permission.

### 8.8 Integrations: integrations.py

[integrations.py](../../backend/app/api/integrations.py) connects an organisation's Google account and stores each person's
Calendly link.

The Google callback arrives without the app's sign-in header, so three things together prove it is genuine:

1. The `state` is signed and names the organisation and person. It lasts ten minutes.
2. A cookie (`ww_google_state`) set by `/integrations/google/start` must match a hash of the state. Without it, a manager
   could send their consent link to someone at another company and store that person's calendar grant as their own
   organisation's.
3. `_UsedStates` lets each state complete once.

The user is re-read in the callback and must still be a manager or admin of that organisation. The refresh token is
encrypted before it is stored.

`GET /api/integrations/status` (admins) reports whether each integration is set up and works. It never returns a key.

### 8.9 Audit and crash reports

[audit.py](../../backend/app/api/audit.py) is covered in [section 11](#11-the-audit-log).

[client_errors.py](../../backend/app/api/client_errors.py) accepts the web app's crash reports. The browser sends them with
`sendBeacon`, so no sign-in arrives. In its place: a rate limit per address, a 16 KB body limit and a strict schema that
refuses unknown fields. `clean()` removes query strings, emails, keys and credential paths. The report becomes one log
line and is never stored. The address and user agent are not logged.

### 8.10 Internal routes

Four routes are for Cloud Scheduler: `/internal/outbox/flush`, `/internal/recordings/poll`, `/internal/audit/purge` and
`/internal/lifecycle/tick`. Each is guarded by `_scheduler_only`: the caller must present a Google-signed OIDC token whose
audience is `OUTBOX_FLUSH_AUDIENCE` and whose email is `OUTBOX_FLUSH_INVOKER`. Without those settings the routes answer
`404`. Self-hosted mode does the same four jobs with in-process loops instead.

---

## 9. Limits and logs

### 9.1 Rate limits: ratelimit.py

[ratelimit.py](../../backend/app/ratelimit.py) puts a token bucket in front of every route. A bucket holds one minute's worth
of requests and refills continuously, so a burst up to the limit is allowed.

`GROUPS` is checked in order; the first match wins.

| Group | Routes | Charged to | Per minute |
|---|---|---|---|
| `auth` | Sign-in, sign-up, joining | Address | 10 |
| `chat` | `/api/chat`, `/api/ask/...`, a private review | Person | 20 |
| `agents` | Routes that run an agent inside the request | Person | 20 |
| `preboarding` | `/api/preboarding/*` | Address | 30 |
| `webhooks` | `/webhooks/*` | Address | 60 |
| `client_errors` | `/api/client-errors` | Address | 10 |
| `default` | Everything else | Person | 120 |

**How the limiter knows the person.** The limiter runs before sign-in, so it cannot know who is calling. It learns from
the response instead. When a request's credential was verified, `send_and_learn` maps a hash of that credential to the
user id. The next request with the same credential is charged to the person. A made-up token never gets its own bucket,
so it cannot be used to escape the per-address limit.

**The client's address.** `RATE_LIMIT_TRUSTED_HOPS` says how many proxies in front of the API append to
`X-Forwarded-For`. The address is the entry that many places from the right. Entries the client wrote itself sit on the
left and are never used. On Google Cloud the value is 2.

**Limits are per process.** With several API instances each enforces its own. The same is true of the chat window, the
chat slots and the used Google states.

### 9.2 Body limits

`BodyLimitMiddleware` refuses a body over its route's limit with `413` before anything reads it whole. It checks a
declared `Content-Length` first and counts a chunked body as it arrives. FastAPI parses JSON before the sign-in
dependency runs, so without this a client could stream gigabytes to a route that would only then refuse it.

`body_limit()` gives each route its ceiling:

| Route | Limit |
|---|---|
| Document upload | `MAX_UPLOAD_MB` (25) + 1 MB |
| Recording upload (self-hosted) | `MAX_RECORDING_MB` (2048) + 1 MB |
| `/webhooks/*` | 26 MB (GitHub sends up to 25) |
| `/api/client-errors` | 16 KB |
| Everything else | `MAX_JSON_KB` (1024) |

### 9.3 Keeping credentials out of logs: logsafe.py

[logsafe.py](../../backend/app/logsafe.py) exists because some URLs *are* credentials: the pre-boarding link
(`/welcome/<token>`, `/api/preboarding/<token>/...`), a signed file link (`/api/blobs/<token>`) and an OAuth callback's
`code` and `state`. A log line naming one would let anyone who reads the logs use it.

`redact()` replaces those parts with `[redacted]`. Every place that logs a path goes through it: the request log, the
rate limiter and `AccessLogFilter`, which guards uvicorn's access log should a handler ever be added back.

---

## 10. Events: the outbox, the buses and the workers

### 10.1 Publishing: outbox.py

[outbox.py](../../backend/app/api/outbox.py) has one rule: record, then send.

```python
def publish(c, topic, event):
    _send(c, c.store.outbox_add(topic, event), topic, event)
```

- `outbox_add` writes the row. It is idempotent on the dedupe key. If this fails, `publish` raises and the route fails:
  the event was not accepted.
- `_send` tries once and **never raises**. A failed send leaves the row due and records the error.

A new row's `next_attempt_at` is one minute ahead, so a flusher does not race the request's own send. Each claim by
`outbox_due` moves the next attempt forward: 30 seconds, doubling, at most one hour. Sent rows are deleted after 7 days.

A row can be sent twice: a timeout that actually delivered, or two flushers. That is safe because the consumer
deduplicates on the event's key.

### 10.2 Three buses

| Mode | Bus | How an event reaches `Container.handle` |
|---|---|---|
| `demo` | `InlineBus` | `publish` calls `handle` in the same thread, before the route returns |
| `self_hosted` | `LocalQueue` | A worker thread in the API process |
| `gcp` | `PubSubBus` | Pub/Sub delivers to a worker pod |

`LocalQueue` sets `owns_outbox = True`. `_send` then hands the row over with `bus.submit` and does **not** mark it sent.
The queue marks it sent only when its handler succeeds. On Google Cloud the row is marked sent as soon as Pub/Sub has
it, because Pub/Sub then owns the retries.

### 10.3 Dedupe keys

The key decides what "the same event" means. Change one and you change what is retried and what is skipped.

| Key | Event | Meaning |
|---|---|---|
| `connect:<repo>:<random>` | `repo.connected` | Every connect or re-index runs |
| `watch-index:<repo>:<head>` | `repo.connected` from the watcher | One full index per head commit |
| `push:<sha>:<repo>` | `repo.pushed` | One update per pushed commit |
| `pr:<number>:<head sha>:<repo>` | `pr.opened`, `pr.updated` | One review per commit of a pull request |
| `merged:<number>:<repo>` | `pr.merged` | Once per pull request |
| `ob:<onboarding>` | `onboarding.started` | One plan per onboarding |
| `doc:<document>` | `document.uploaded` | Once per document |
| `rec:<meeting>:<random>` | `meeting.recording_ready` | Each recording added is processed |
| `feedback:<id>` | `feedback.created` | Once per note |
| `completed:<onboarding>` | `program.completed` | Once per sign-off |
| `mail:invitation:<id>` | `mail.invitation` | One email per invitation |
| `tick:<org>:<day>` | `lifecycle.tick` | One daily tick per organisation |

Handled keys are forgotten after `EVENTS_KEPT_DAYS` (90), except `pr:` keys. The self-hosted watcher lists every open
pull request each round, and that key is what says a head was already reviewed.

### 10.4 Self-hosted: selfhost.py

[selfhost.py](../../backend/app/selfhost.py) replaces Pub/Sub, the worker pods, Cloud Scheduler and GitHub's webhooks with
threads inside the API process.

**`LocalQueue`** has `LOCAL_WORKERS` (3) worker threads and one pump thread.

- `submit` puts a row on the in-memory queue unless it is already in flight or waiting.
- `pump_once` runs every `PUMP_EVERY_S` (5 s). It claims due rows from the outbox for the free workers.
- `run` handles one event. It first asks `org_of_project` which organisation the event belongs to and sets it.

What `run` does with each outcome:

| Outcome | Action |
|---|---|
| Success | Mark the row sent |
| Unknown project | Mark the row sent and log it; nothing can ever handle it |
| `EventBusy` or `RepoBusy` | Wait `BUSY_RETRY_S` (30 s) in memory. Not counted as an attempt |
| `AIUnavailable` | Give up at once. Retrying cannot help until an admin adds a key |
| Any other error | Record the error's **type**, never its text. After `MAX_ATTEMPTS` (6), give up |

A row claimed more than `MAX_ATTEMPTS` times without ever finishing killed the process each time. `pump_once` gives it up
when it comes round again, so one poisonous event cannot crash the server forever.

`failed()` is what "give up" means to a person. It marks the repository, session or document `failed` and tells the
project's managers in a fixed sentence. A repository left at `indexing` would say so forever.

**`RepoWatcher`** polls GitHub every `REPO_POLL_S` (120 s) for repositories connected by URL and publishes what webhooks
would have.

- `_branch`: if the head moved, ask `compare` for the changed paths and publish `repo.pushed`. If GitHub cannot list
  them, publish `repo.connected` for a full index.
- `_pulls`: open pull requests, and ones merged within `MERGED_WITHIN` (7 days), go through the same
  `events_from_webhook` as a real webhook.
- `_publish` skips a key this process already sent or the store already knows.
- One unreadable repository is skipped and the rest are still polled. After `UNREADABLE_ROUNDS` (3) rounds of `401`,
  `404`, `410` or `451` it is marked failed and its managers are told once.
- `GitHubLimited` stops the round, because every repository would hit it. The loop sleeps until the limit resets.

**`RecordingPoller`** (every `RECORDING_POLL_S`, 300 s) imports recordings of ended Google sessions and marks sessions
whose recording never came as `ended`.

**`TickLoop`** (every `TICK_EVERY_S`, 3600 s) offers each organisation its daily tick. The dedupe key makes it run once a
day.

Every loop catches every error and tries again next round. A background thread never dies from one failure.

### 10.5 Google Cloud: worker.py

[worker.py](../../backend/workers/worker.py) is one image deployed three times, once per queue (`WORKER_QUEUE`).

`_handle_message` decides the fate of one delivery:

| Situation | Action | Why |
|---|---|---|
| Unparseable message | Ack | It can never be handled |
| Database unreachable while finding the organisation | Nack | Pub/Sub retries |
| Unknown project | Ack | Nothing to do |
| Handled | Ack | |
| `EventBusy` after waiting | Nack | Another pod holds the lease |
| `RepoBusy` | Extend the deadline to `REPO_BUSY_RETRY_S` (300 s) and drop the message | A nack would come back in seconds; indexing may take minutes |
| Any other error | Nack | After `MAX_DELIVERY_ATTEMPTS` (5) the message goes to the dead-letter topic |

`_handle_waiting_out_busy` holds a message whose lease is busy instead of nacking it. A nack would spend one of only five
delivery attempts. On the last attempt it also waits out a busy repository instead of handing the message back.

The `meetings` queue runs one message per pod with a lease of up to two hours, because the model watches a whole video.
The other queues run four at a time.

On `SIGTERM` the worker stops pulling and gives running handlers `SHUTDOWN_GRACE_S` (570 s) to finish. A small HTTP
server answers `/healthz` while the subscriber stream is alive.

[secrets.py](../../backend/app/secrets.py) loads secrets from Secret Manager into the environment before settings are read.
An explicit environment value wins.

---

## 11. The audit log

**Writing.** One call: `store.audit(org_id, actor_id, action, target, detail)`. Routes call it after a security-relevant
action. Some definer functions insert directly (`organization.created`, `invitation.accepted`).

`detail` holds counts and ids, never content. Erasing a person records how many rows of each kind were removed, and
nothing of what they were.

**Append-only.** Migration 015 revokes `UPDATE` and `DELETE` on `audit_log` from `ww_app`. The migrator re-applies that on
every run and `verify()` refuses to finish otherwise. A compromised API process or a leaked `ww_app` password cannot
rewrite history. Rows leave in two ways only: `purge_audit_log` (never younger than 30 days) and `delete_org_audit`
(only after the organisation itself is deleted).

**Reading.** [audit.py](../../backend/app/api/audit.py) serves admins:

- `GET /api/audit`: one page, newest first. Filters: `action` (exact, or a prefix ending in `*`), `actor` (id or email),
  `target`, `since`, `until`. A date-only `until` includes that day.
- `GET /api/audit.csv`: the same filters, up to `AUDIT_EXPORT_MAX_ROWS` (100,000). `_cell` prefixes a cell that starts
  with `=`, `+`, `-` or `@` with an apostrophe, so a spreadsheet does not run it as a formula.

Reading the log is itself recorded (`audit.viewed`), as is every export and purge.

**Retention.** `purge_expired` deletes entries older than `AUDIT_RETENTION_DAYS` (365, never below 30) in every
organisation and writes `audit.purged` in each one that lost rows. Self-hosted runs it daily through `RetentionLoop`.

**In memory.** `MemoryStore.audit` appends rows with no id or time. `prepare_memory` in
[audit_store.py](../../backend/app/adapters/audit_store.py) wraps the writer so every row gets both.

---

## 12. Export, deletion and erasure

[orgdata.py](../../backend/app/lifecycle/orgdata.py) holds the logic. The routes are `GET /api/org/export` and
`DELETE /api/org`, both for admins.

### How the tables are found

`PgLifecycleStore._org_tables` reads PostgreSQL's catalogue for every table with an `org_id` column. There is no
hand-kept list. A table added by a later migration is exported and deleted automatically. This is the reason for the
rule "every new table has an `org_id`".

### Export

One JSON document, table by table. Left out on purpose:

- **Derived search data**: the whole `chunks` table and the `embedding`, `tsv` and `search_text` columns. It is rebuilt
  from sources that are exported.
- **Credentials**: `firebase_uid`, `ip_hash`, and any column whose name contains `token` or `secret`.
- **Survey rows.** They carry a team and a day. A program's `survey_days` says who answered on which day. Joined, they
  could identify a person. The export holds floored averages per team and day instead, and no `survey_days`.

### Deletion

"Everything" is more than rows. `orgdata.delete` works in a fixed order:

1. **Before the rows go**, collect what the rows name: the stored files' paths, the LangGraph thread ids of the
   organisation's approvals, and the decrypted Google refresh token.
2. `delete_org` removes the rows **in one transaction**.
3. Revoke the Google grant. Deleting the encrypted row alone would leave the app's access live in the customer's Google
   account.
4. Delete the files, one by one.
5. Delete the checkpoint threads, one by one.

A failure in steps 3 to 5 is logged and counted (`stored_files_failed`, `checkpoint_threads_failed`). It never undoes
the rest.

Inside `delete_org`:

- Outbox rows are found by the project ids in their events, because the outbox has no `org_id`.
- Dedupe keys are found by matching the UUIDs inside them against the organisation's ids.
- Each table is emptied in turn inside a savepoint. A table that another still points at waits for the next round. So the
  order needs no list and covers future tables. If a round makes no progress, the whole deletion fails and nothing is
  removed.
- The organisation row goes after its tables, then its audit entries through `delete_org_audit`.

The route asks for the organisation's name typed exactly. The final audit entry is written with `org_id = None`, because
the organisation no longer exists.

### Erasing one person

`POST /api/org/people/erase` (in [routes.py](../../backend/app/api/routes.py)) anonymises one person on request.

- The email must be typed twice.
- An admin cannot erase themselves, and the last active admin cannot be erased.
- `erase_person` runs in one transaction with the user row locked, so a sign-in cannot bind it meanwhile. It deletes the
  person's chat history, the questions they sent to a person, their notifications, scheduling links and invitations. It
  replaces name, email and GitHub username and unbinds the login.
- **Ids stay.** An onboarding, a review and the audit log still hold together.

The answer lists what is kept (`ERASE_KEEPS`): the organisation's own records under the erased name, the audit log,
backups until they expire, and the sign-in account at the identity provider.

---

## 13. The lifecycle package

[backend/app/lifecycle/](../../backend/app/lifecycle/) covers an onboarding from enrolment to sign-off. Its stated
principles ([\_\_init\_\_.py](../../backend/app/lifecycle/__init__.py)):

- Every rule is deterministic and returns its reasons as `why: [...]`.
- Every feature works with the AI off.
- "Today" always comes from the clock.

### 13.1 The clock: clock.py

[clock.py](../../backend/app/lifecycle/clock.py) gives the lifecycle its "today". Program days, due dates, escalation and
report windows are computed from it, never from `date.today()`.

- In the demo, `POST /api/demo/advance` stores a day offset for the organisation (at most `MAX_OFFSET_DAYS`, 365). This
  lets a demo move through 90 days in a minute.
- Outside the demo the offset is always 0.
- **Security decisions use the real time.** A pre-boarding link's expiry never reads this clock.

`program_day(start, today)` is the number of days since the start date; day 0 is the start. `business_days(since, until)`
counts weekdays after `since`, up to and including `until`: a blocker opened on Friday has waited one business day on
Monday. `week_start` returns the Monday.

### 13.2 Context: context.py and hires.py

[context.py](../../backend/app/lifecycle/context.py) defines two small objects.

- `Ctx`: the container, the caller and the clock.
- `HireCtx`: one onboarding seen through one request. Its fields are `cached_property`, so each read happens at most once
  however many rules use it. `forget(...)` drops a cached read after a write.

[hires.py](../../backend/app/lifecycle/hires.py) loads a `HireCtx` and has one important function, `refresh`: it marks
automatic milestones done and runs blocker detection. It runs **when someone reads the page**. No scheduler is needed, and
an alert is never older than the last time anyone looked.

### 13.3 Templates: templates.py

[templates.py](../../backend/app/lifecycle/templates.py) defines what a program contains: milestones, documents to
acknowledge, access to request, and a plan for when no model is set up.

- There are 16 defaults: four tracks (`Backend`, `Frontend`, `SRE`, `Data`) by four levels (`Junior` to `Staff`).
  `list_for` creates them the first time an organisation lists its templates.
- A template's days are days of a 90-day program (`BASE_DAYS`). `scale()` adjusts them for another length, so the
  check-ins of a 120-day program fall on days 40, 80 and 120.
- A change makes a new version; the old one becomes inactive. A program remembers the version it was made from.
- `AUTO_MILESTONES` names the milestones the product checks itself. The rest a person marks.

### 13.4 Enrolment and the plan: enrolment.py

[enrolment.py](../../backend/app/lifecycle/enrolment.py) has two halves, with a manager's approval between them.

**`enrol()` makes a draft.**

1. Find the person by email, or keep an unbound user row for them (`ensure_user`).
2. Create the onboarding and a draft program: the documents to acknowledge, and nothing else. No milestones, no access
   requests, no invitation yet.
3. Return a `plan_event` (`onboarding.started`, key `ob:<id>`) for the route to publish. The Planner drafts one 30/60/90
   plan, which waits in Approvals as one `task_plan` approval.

**`finalize()` runs when the manager approves.** The assistant calls it from the resumed run. It creates the milestones
the manager kept, the access requests (each owner is notified), the mentor's notice and the invitation. It then sets
`plan_approved_at`. It is idempotent: a program whose plan was approved already is left alone.

Why the split: nothing reaches the hire, the mentor or an access owner until a person has approved the plan. That is the
product's rule, "agents propose, people decide", applied to enrolment.

`plan_status()` reads the plan's state from the approvals and from `event_status("ob:<id>")`:
`approved`, `awaiting_approval`, `drafting` or `not_started`.

`_forget_finished_plan` handles an onboarding that was started without a program. Its first plan must be decided and its
event finished before enrolment can reuse the key, or the new plan would be dropped as a duplicate (`PlanInProgress`).

### 13.5 Pre-boarding: preboarding.py

[preboarding.py](../../backend/app/lifecycle/preboarding.py) serves a hire before day one, without a sign-in.

- `issue` makes a 256-bit token, returns it once and stores only its SHA-256. A new link replaces the old one. It expires
  after `LINK_DAYS` (14) on the real clock.
- `resolve` gives the same answer for an unknown, replaced or expired link.
- `portal` shows names, dates, documents and access *status* only. No code, no recordings, no chat.
- `ip_hash` records which address acknowledged a document as an HMAC keyed with the token. The token is not stored, so
  the hash can be compared within one link and never read.
- `script` produces a shell script that asks each tool for its version and posts the results. It installs nothing.
  `record` cleans what comes back: names must match a short pattern, details are cut to 200 characters. What the hire
  sends is stored as data and shown back. It is never interpreted.

### 13.6 Progress: progress.py

[progress.py](../../backend/app/lifecycle/progress.py) describes progress by area. An area is one of the project's code
modules.

There are four kinds of evidence: `steps`, `watched`, `questions`, `merged`. `level_for` turns them into a level:

| Evidence | Level |
|---|---|
| A merged change | `contributed` |
| Two or more other kinds | `working` |
| One kind | `exploring` |
| None | `not_started` |

There are no scores and no ranking between people, by design. The hire's own assessment is shown next to the level,
never instead of it.

`evaluate_milestones` marks an automatic milestone done the first time its evidence is seen, and stores that evidence.
`milestone_view` shows the `environment_ready` milestone as pending again if the latest environment check fails, so the
milestone and the open blocker agree.

### 13.7 Blockers: blockers.py

[blockers.py](../../backend/app/lifecycle/blockers.py) detects when a hire may be stuck. Five rules, each returning what it
saw:

| Rule | Fires when |
|---|---|
| `repeated_topic` | The same module asked about 3+ times within 5 days. The evidence counts questions, never quotes them |
| `stalled_task` | A task in progress with no activity for 3 business days |
| `failing_ci` | 3+ failed CI runs on the hire's pull request within 48 hours |
| `environment` | The environment check failing for more than 24 hours |
| `unresolved_review` | Serious review findings unresolved for more than 3 days |

`detect()` runs all five, then:

- **Opens** an alert for each finding. A partial unique index allows one open alert per rule and subject, so detection can
  run again and again.
- **Closes** an alert whose condition cleared (`resolved_by = "system"`).
- **Stays quiet** when a person resolved an alert while its condition still held. It opens again only after the
  condition clears once.
- **Escalates** by business days open: the hire on day 0, the mentor after 1 (`MENTOR_AFTER`), the manager after 3
  (`MANAGER_AFTER`). Each stage is told once (`notified_stage`).

`capped()` applies the Coach agent's judgement: it may hold an alert at a lower stage for `COACH_HOLD_DAYS` (2). It can
never go below a stage already told. Nobody is "untold".

`describe()` counts durations when the alert is read. A stored count would freeze.

### 13.8 Check-ins and surveys: checkins.py

[checkins.py](../../backend/app/lifecycle/checkins.py) holds two separate things.

**Check-ins** at day 30, 60 and 90 (scaled). Each has a hire's half and a manager's half. Both see both: a check-in is a
conversation, not a file kept on someone. One per day and author; writing again replaces it. `clean_answers` keeps known
questions only. A half is overdue seven days after its day.

**The survey** is anonymous. Three mechanisms make that real:

1. A `SurveyResponse` has no person id.
2. `claim_survey_day` records atomically that an onboarding answered a day's survey. A second submission, even a
   simultaneous one, finds the day taken. So one response is one person.
3. `SURVEY_MIN_GROUP = 5`. Results are shown per team and survey day, and only where five or more people answered. Each
   question's mean needs five answers too. Below that the answer is "not enough data", never a number.

Answers are never pooled across days to reach the floor. Three hires answering on days 30, 60 and 90 would otherwise pass
a floor of five.

### 13.9 Summaries and completion

[summaries.py](../../backend/app/lifecycle/summaries.py) writes a weekly summary from records, never with a model. Every line
cites what it says: a task, a milestone, a blocker. Questions are counted, never quoted. A week runs Monday to Sunday;
writing the same week again replaces it.

[completion.py](../../backend/app/lifecycle/completion.py) lists five criteria for sign-off, each with `met`, `evidence` and
`why`: every milestone done or skipped, the final check-in written by both, no open blockers, a merged change in at least
one area, and access requests settled.

`sign_off` records the manager's decision with a snapshot of the unmet criteria. A manager may sign off with criteria
unmet; the record keeps that. `complete` sets the onboarding to `completed`, which removes it from the active lists.
`extend` adds days. The route refuses a person signing off their own program.

### 13.10 Reporting and visibility

[reporting.py](../../backend/app/lifecycle/reporting.py) builds the leadership report.

- Cohorts are rolling: the current one started in the last `WINDOW_DAYS` (90), the previous in the 90 days before.
- Every figure carries a `how` sentence.
- Floors keep thin data from looking like a result: a median needs `MIN_MEDIAN` (3) values, a rate `MIN_RATE` (5) cases.
  Below the floor the value is `null` and the display says "not enough data", never 0.
- "Senior time saved" is an estimate and says so: answers without a person, plus reuses, times
  `minutes_per_interruption` (15 by default, editable by admins).
- `safe_cell` protects the CSV from formula injection, as the audit export does.

[visibility.py](../../backend/app/lifecycle/visibility.py) is a privacy guarantee expressed as code. `FIELDS` is one registry
of everything anyone can see about a hire. Each `HireField` has a label, who sees it, where it comes from, how it is
worked out, and the function that builds it.

- Every manager-facing or mentor-facing payload about a hire is built from this registry by `view(hc, keys)`.
- The hire's own `GET /api/onboardings/{id}/visibility` returns **every** field, through the same builders.

So whatever a manager can read about a hire, the hire can read too, with the same values. A test fails if a
manager-facing endpoint returns a field this payload lacks.

### 13.11 Flags: flags.py

[flags.py](../../backend/app/lifecycle/flags.py) reads `FEATURES` from the environment on every check. `all` is the default; a
leading `-` turns one off (`all,-surveys`).

Enrolment, programs, homes, visibility and the reporting floors are not flags. They are the product, and visibility must
never be switched off.

### 13.12 The rest of the package, briefly

| File | What it does |
|---|---|
| [first_task.py](../../backend/app/lifecycle/first_task.py) | Scores candidate first tasks on focus, explanation, size, tests and quiet files, with reasons |
| [presubmit.py](../../backend/app/lifecycle/presubmit.py) | A private review of the hire's own change. The result goes to the hire only and is not stored |
| [feedback.py](../../backend/app/lifecycle/feedback.py) | Notes on a document, file, KT session or answer, sent to its owner with the reason they were chosen |
| [handover.py](../../backend/app/lifecycle/handover.py) | A checklist of what a departing person holds alone |
| [home.py](../../backend/app/lifecycle/home.py) | The few things a person should do next, built from the visibility registry |
| [simulation.py](../../backend/app/lifecycle/simulation.py) | Demo only: what the seeded people do on each day the demo clock crosses |
| [routing.py](../../backend/app/lifecycle/routing.py) | Sends a question to the cheapest tier that can answer it honestly. Covered with the Guide in a later part |

The daily tick ([tick.py](../../backend/app/agents/supervisor/tick.py)) runs the scheduled side of the lifecycle once per organisation
and day. `run_all` asks `lifecycle_orgs()` which organisations have a program in progress and runs each under its own
organisation. One organisation's failure does not stop the others.

---

## 14. Worked examples

The names below are the demo's seeded ones ([seed.py](../../backend/app/seed.py)): Priya (Manager) is `u-priya`, Ravi
(Senior) is `u-ravi`, the project is `proj-demo` "Mini Policy Engine" and its repository is `demo/mini-policy-engine`.

### Example 1: Priya opens the task list (`GET /api/tasks?project_id=...`) on a self-hosted server

1. **`request_log`** assigns a request id and starts the timer.
2. **`RateLimitMiddleware`** computes `group_for("GET", "/api/tasks")` → `default`. Priya's token has been seen before,
   so `_person` returns her user id and the bucket `default:u:<id>` gives up one token.
3. **`BodyLimitMiddleware`** has nothing to count.
4. **`current_user`** clears the organisation, then `_resolve` runs in the thread pool. `firebase_tokens.verify` checks the
   token against cached certificates, with no network call. `user_by_firebase` calls the definer function `auth_lookup`,
   because no organisation is set yet. Back on the request task, `_set_tenant(org_id)` sets `current_org`.
5. **`list_tasks`** calls `check_project`. `get_project` opens a transaction; `_Db.tx` runs
   `set_config('app.org_id', ...)`; the policy on `projects` lets the row through.
6. Priya is a manager, so `store.tasks(project_id=...)` returns every task. Each of these reads repeats the
   `set_config` in its own transaction.
7. The response passes back out. The limiter's `send_and_learn` refreshes the token-to-person mapping. `request_log` adds
   the security headers and writes one line with path, status, latency, user id and organisation id.

If Priya asked for a project of another organisation, step 5's `SELECT` would return no row. `get_project` would return `None`
and `check_project` would answer `404`. Not because the code compared organisation ids first, but because the database never
returned the row.

### Example 2: Priya enrols a new hire, and approves the plan

**The request.** `POST /api/onboardings/enrol` with an email, a start date and the template `backend-mid`.

1. `require("manager")` and `check_project` pass.
2. The route checks the template is active and belongs to this organisation, the team is in this project, and the mentor
   (say Ravi) is a member who may take that role and is not the hire.
3. `enrolment.enrol`: the email is not in the organisation, so `ensure_user` keeps an unbound user row and `invite` is
   true. `create_onboarding` inserts the onboarding. `create_program` inserts the draft program with the template's
   acknowledgements.
4. The route publishes the `plan_event`: type `onboarding.started`, key `ob:<onboarding id>`.
   `outbox_add` writes the row.
5. It audits `onboarding.enrolled` and returns `201` with the plan's status.

**The event.** What step 4's send does depends on the mode:

| Mode | What happens next |
|---|---|
| `demo` | `InlineBus` calls `Container.handle` before the route returns. The approval already exists in the `201` |
| `self_hosted` | `LocalQueue.submit` queues the row. A worker thread picks it up within moments |
| `gcp` | `PubSubBus` publishes to `ww-reviews`. The reviews worker receives it |

In every mode `Container.handle` then does the same: `claim_event("ob:<id>", 300)` returns `new`, the heartbeat starts,
and the assistant runs the Planner on thread `ob:<id>`. The Planner drafts the plan, saves a `task_plan` approval and
pauses. `finish_event` marks the key done. While this runs, `plan_status` reports `drafting`; afterwards,
`awaiting_approval`.

The insert into `approvals` fires the `ww_live` trigger. `PgListener` relays it, and Priya's browser reloads Approvals.

**The decision.** `POST /api/approvals/{id}/decide` with the task ids to approve.

1. The route checks every id is one the plan proposed.
2. `Container.decide` reads the thread's state, sees it waiting on this approval, and calls `claim_approval`, which
   stores the decision.
3. `resume` continues the run. The assistant approves the tasks and calls `enrolment.finalize`: the milestones, the
   access requests (Ravi is notified of his), the mentor's notice, and the invitation.
4. `_forget_if_finished` deletes the thread's checkpoints.
5. The route audits `approval.task_plan.decided`. If SMTP is set up, `_mail_invitations` publishes `mail.invitation` with
   key `mail:invitation:<id>`.
6. The answer includes the invitation's sign-in link for Priya to share.

If Priya double-clicks, the second request finds the approval no longer `pending` and answers `409 Already decided`. If it
arrives while the first is still applying, `claim_approval` fails and the answer is `409`, "already being applied".

### Example 3: a commit is pushed to a repository connected by URL (self-hosted)

1. **`RepoWatcher.poll`** wakes after `REPO_POLL_S`. `watched_repositories()` (a definer function) lists repositories with
   no installation id, across organisations. For each it sets that organisation.
2. **`_branch`**: the repository is `ready`. `branch_head` sends the stored ETag. GitHub answers `200` with a new commit.
3. `compare(last_indexed_sha, head)` returns three changed paths.
4. **`_publish`**: the key is `push:<head>:<repo id>`. It is not in `self.sent` and `event_status` says `none`, so
   `publish` writes the outbox row and `LocalQueue.submit` queues it.
5. **A worker thread** takes the job. `run` asks `org_of_project` (a definer function), sets the organisation and calls
   `Container.handle`.
6. `claim_event` returns `new`. The assistant runs indexing. Indexing takes `ingest_lock(repo)`; if a full index of the
   same repository were still running, `RepoBusy` would be raised and the event would wait 30 seconds in memory.
7. On success `finish_event` marks the key done and `_done` marks the outbox row sent.
8. The update to `repositories` fires the `ww_live` trigger. The Settings page of anyone in that organisation reloads.

Two minutes later the watcher polls again. `branch_head` answers `304` and costs nothing against GitHub's limit. The head
equals `last_indexed_sha`, so nothing is published.

**If the server is killed during step 6:** the outbox row is still unsent and the lease stops being renewed. After a
restart `pump_once` finds the row due. If the lease has not lapsed yet, `handle` raises `EventBusy` and the row waits 30
seconds. Once it lapses, `claim_event` takes it over and `_start` continues the run from its last checkpoint.

---

## 15. Constants and what breaks if they change

| Constant | Where | Value | If you change it |
|---|---|---|---|
| `event_lease_s` | [config.py](../../backend/app/config.py) | 300 s | Lower: a slow but healthy handler whose heartbeat is delayed gets a duplicate run. Higher: a crashed run waits longer before anyone retries it |
| Heartbeat interval | [container.py](../../backend/app/container.py) | `max(5, lease / 3)` | Must stay well under the lease, or healthy runs lose their claim |
| Approval claim age | [postgres.py](../../backend/app/adapters/postgres.py) `claim_approval` | 5 minutes | A decision whose process died blocks retries for this long |
| Outbox first delay | [004_round3.sql](../../backend/db/migrations/004_round3.sql) | 1 minute | Lower: a flusher races the request's own send and sends twice more often |
| Outbox backoff | `outbox_due` | 30 s doubling, 1 hour cap | Shorter hammers a broker that is down |
| `EVENTS_KEPT_DAYS` | [postgres.py](../../backend/app/adapters/postgres.py) | 90 | Lower than a redelivery window lets an old event run twice. `pr:` keys must stay exempt |
| `MAX_ATTEMPTS` | [selfhost.py](../../backend/app/selfhost.py) | 6 | Lower gives up on brief outages; higher keeps a poisonous event alive longer |
| `BUSY_RETRY_S` | [selfhost.py](../../backend/app/selfhost.py) | 30 s | Only how soon a waiting event is tried again |
| `MAX_DELIVERY_ATTEMPTS` | [worker.py](../../backend/workers/worker.py) | 5 | **Must match the dead-letter policy in `infra/terraform/events.tf`.** The "last attempt" logic depends on it |
| `REPO_BUSY_RETRY_S` | [worker.py](../../backend/workers/worker.py) | 300 s | How long a busy repository's event stays away |
| `TOPICS` | [gcp.py](../../backend/app/adapters/gcp.py) | 11 event types | A missing type raises `KeyError` on publish in `gcp` mode only |
| `embedding_dim` | [config.py](../../backend/app/config.py) | 768 | **Must match `vector(768)` in 001.** Changing it needs a migration and a full re-index |
| DB timeouts | [config.py](../../backend/app/config.py) | 30 s, 5 s, 60 s | Export and deletion raise theirs locally (`_BULK`); a new bulk job must too |
| `MIGRATION_LOCK_TIMEOUT` | [migrate.py](../../backend/app/migrate.py) | 10 s | Higher lets a stuck migration queue the whole site behind it |
| `WEAK_SECRETS` | [config.py](../../backend/app/config.py) | 5 values | Add any new placeholder you put in an example env file |
| `MIN_RETENTION_DAYS` | [audit_store.py](../../backend/app/adapters/audit_store.py) | 30 | The SQL function enforces 30 too. Change both or the lower one wins |
| `STREAM_S` | [stream.py](../../backend/app/api/stream.py) | 15 min | Must stay under the sign-in token's hour |
| `QUEUE_MAX` | [live.py](../../backend/app/live.py) | 200 | A slower browser gets "reload everything" sooner or later |
| `START_S`, `COMPLETE_S` | [uploads.py](../../backend/app/api/uploads.py) | 15 min, 12 h | `COMPLETE_S` covers a 2 GB file on a slow line |
| `TOLERANCE_S` | [calendly.py](../../backend/app/adapters/calendly.py) | 180 s | Higher widens the replay window |
| `UNKNOWN_KID_REFETCH_S` | [firebase_tokens.py](../../backend/app/firebase_tokens.py) | 60 s | Lower lets made-up key ids trigger downloads |
| `LINK_DAYS` | [preboarding.py](../../backend/app/lifecycle/preboarding.py) | 14 | How long a link without sign-in works |
| `SURVEY_MIN_GROUP` | [checkins.py](../../backend/app/lifecycle/checkins.py) | 5 | **A privacy floor.** Lowering it can identify people |
| `MIN_MEDIAN`, `MIN_RATE` | [reporting.py](../../backend/app/lifecycle/reporting.py) | 3, 5 | Lower shows figures from too little data |
| `MENTOR_AFTER`, `MANAGER_AFTER` | [blockers.py](../../backend/app/lifecycle/blockers.py) | 1, 3 business days | When a mentor and a manager are told |
| `BASE_DAYS`, `CHECKIN_DAYS` | [templates.py](../../backend/app/lifecycle/templates.py) | 90; 30, 60, 90 | Every template's days are written against 90 |

---

## 16. Failure paths

| What fails | What happens | Where |
|---|---|---|
| Unsafe settings at start | The server does not start; the message lists each problem | `build_container` |
| The app is connected as a role that bypasses row-level security | The server does not start | `_postgres` |
| The organisation is not set before a query | The query sees no rows | The policies |
| A route raises unexpectedly | `500` with a request id to quote; the stack is logged | `request_log` |
| The database is down | `/health/ready` answers `503`; `/health` still answers | [main.py](../../backend/app/main.py) |
| The outbox row cannot be written | The route fails; the event was not accepted | `publish` |
| The send fails after the row is written | The row stays due and is retried with backoff | `_send`, `outbox_due` |
| A message is delivered twice | The second is `{"skipped": "duplicate"}` | `claim_event` |
| Two workers get the same event at once | One runs; the other gets `EventBusy` and waits or nacks | `Container.handle` |
| A process dies mid-run | The lease lapses; the next delivery continues from the last checkpoint | `_start` |
| An event keeps killing the process (self-hosted) | Given up after `MAX_ATTEMPTS` claims; managers are told | `pump_once` |
| No model is set up | The event is given up at once with a plain sentence; routes answer `503` | `AIUnavailable`, `_need_ai` |
| A repository is being indexed already | The second event waits; it is not counted as a failure | `RepoBusy` |
| A manager double-clicks | The second click answers `409` | `claim_approval` |
| A resume fails | The claim is released; a retry re-applies the first decision | `Container.decide` |
| GitHub's API limit is used up | The watcher sleeps until the reset | `GitHubLimited`, `wait_hint` |
| GitHub stops showing a repository | After 3 rounds it is marked failed and managers are told once | `RepoWatcher._refused` |
| Google's certificates cannot be downloaded | The cached ones keep working; with none cached, sign-in fails until the retry | `_Certs.get` |
| The Google grant was revoked | `GoogleAuthError`: "connect it again in Settings" | [google_calendar.py](../../backend/app/adapters/google_calendar.py) |
| The live listener loses its connection | It reconnects with backoff, then tells browsers to reload everything | `PgListener` |
| An uploaded file is not a video | The file is deleted; `415` | `accept` |
| Mail cannot be sent | `MailError` with no message text; the event is retried | [mailer.py](../../backend/app/adapters/mailer.py) |
| A file cannot be deleted during organisation deletion | Logged and counted; the rest still goes | `orgdata.delete` |
| An applied migration file was edited | The migrator stops and names the file | `apply_migrations` |
| A new table lacks protection | The migrator refuses to finish | `verify` |

One demo-only gap to know: in the demo, if the inline handler raises, the outbox row stays unsent and nothing flushes it,
because the flush route answers `404` there and no pump runs. This affects only the in-memory demo.

---

## 17. How the tests hold it in place

The tests live in [backend/tests/](../../backend/tests/). [conftest.py](../../backend/tests/conftest.py) turns rate limits off
and points all file storage at a temporary folder, so tests never write into `backend/`. Postgres tests run only when
`TEST_PG_ADMIN_URL` is set.

| Rule | Test file |
|---|---|
| The demo reads no `.env` | [test_config.py](../../backend/tests/test_config.py) |
| Unsafe settings stop start-up | [test_release_security.py](../../backend/tests/test_release_security.py), [test_selfhost.py](../../backend/tests/test_selfhost.py) |
| Token verification and the certificate cache | [test_firebase_tokens.py](../../backend/tests/test_firebase_tokens.py) |
| Sign-in, invitations, leaving | [test_auth_flow.py](../../backend/tests/test_auth_flow.py) |
| Row-level security, table protection, the role check | [test_postgres.py](../../backend/tests/test_postgres.py), [test_release_security_postgres.py](../../backend/tests/test_release_security_postgres.py) |
| Event claims, leases, the outbox | [test_round3_store.py](../../backend/tests/test_round3_store.py), [test_round3_api.py](../../backend/tests/test_round3_api.py) |
| Approval claims and stale approvals | [test_round3_runtime.py](../../backend/tests/test_round3_runtime.py), [test_review_fixes.py](../../backend/tests/test_review_fixes.py) |
| The Pub/Sub worker's ack and nack decisions | [test_round3_runtime.py](../../backend/tests/test_round3_runtime.py) |
| The local queue and the repository watcher | [test_selfhost.py](../../backend/tests/test_selfhost.py), [test_release_reliability.py](../../backend/tests/test_release_reliability.py) |
| Migration checksums | [test_release_reliability.py](../../backend/tests/test_release_reliability.py) |
| Rate limit groups and learning the person | [test_ratelimit.py](../../backend/tests/test_ratelimit.py) |
| Body limits | [test_release_security.py](../../backend/tests/test_release_security.py) |
| Credentials redacted from logs | [test_review_round.py](../../backend/tests/test_review_round.py) |
| Crash reports | [test_release_client_errors.py](../../backend/tests/test_release_client_errors.py) |
| The audit API, retention and the purge | [test_audit.py](../../backend/tests/test_audit.py) |
| The audit log's append-only rule and deletion | [test_release_privacy.py](../../backend/tests/test_release_privacy.py) |
| Export and deletion | [test_org_data.py](../../backend/tests/test_org_data.py), [test_org_data_postgres.py](../../backend/tests/test_org_data_postgres.py) |
| Erasing a person | [test_release_product.py](../../backend/tests/test_release_product.py) |
| Members and the last-admin rule | [test_members.py](../../backend/tests/test_members.py) |
| Signed uploads | [test_release_gcp.py](../../backend/tests/test_release_gcp.py) |
| Calendar, Calendly signatures, scheduling | [test_scheduling.py](../../backend/tests/test_scheduling.py) |
| Every route against the permissions matrix | [test_permissions_matrix.py](../../backend/tests/test_permissions_matrix.py) |
| A hire can see everything a manager sees about them | [test_visibility.py](../../backend/tests/test_visibility.py) |
| The survey floor | [test_survey_floor.py](../../backend/tests/test_survey_floor.py) |
| Lifecycle rules (blockers, business days, levels) | [test_lifecycle_rules.py](../../backend/tests/test_lifecycle_rules.py), [test_lifecycle_api.py](../../backend/tests/test_lifecycle_api.py) |
| The lifecycle store on Postgres | [test_lifecycle_postgres.py](../../backend/tests/test_lifecycle_postgres.py) |
| Untrusted text cannot steer the assistant | [test_injection.py](../../backend/tests/test_injection.py) |

Three of these are structural, and worth knowing by name:

- `migrate.verify()` is a test that runs in production. A deploy cannot finish with an unprotected table.
- [test_permissions_matrix.py](../../backend/tests/test_permissions_matrix.py) holds every route to `docs/PERMISSIONS.md`. A
  new route without an entry fails.
- [test_visibility.py](../../backend/tests/test_visibility.py) fails when a manager-facing endpoint returns a field about a
  hire that the hire's own visibility payload lacks.

---

## 18. Extending it

### A new table

1. A new numbered file in [backend/db/migrations/](../../backend/db/migrations/). Never edit an applied one.
2. An `org_id` column, `ENABLE` and `FORCE ROW LEVEL SECURITY`, and the `tenant_isolation` policy. Copy the block from
   [014_agents.sql](../../backend/db/migrations/014_agents.sql).
3. If the web app shows it, a `ww_live` trigger with a topic.
4. Methods on the right protocol in [ports.py](../../backend/app/ports.py), then in both the memory and Postgres adapters.
5. Demo data in [seed.py](../../backend/app/seed.py).
6. Export and deletion need nothing: `_org_tables` finds the table. Check that no column holds a credential under a name
   that lacks `token` or `secret`; if one does, add it to `_EXPORT_SKIP`.

`migrate.verify()` will tell you if step 2 is missing.

### A query that crosses organisations

Write a `SECURITY DEFINER` function with `SET search_path = public, pg_temp`. Give it to `ww_definer`, revoke `EXECUTE`
from `PUBLIC`, and add it to `DEFINER_FUNCTIONS`. Return ids, not content. If its table was added after migration 002,
also grant `ww_definer` the rights it needs and create a `definer_all` policy for it.

### A new route

1. Depend on `current_user` or `require(...)`. Call `check_project` or `_hire` before touching data.
2. If it is expensive or takes no sign-in, add a `Group` in [ratelimit.py](../../backend/app/ratelimit.py).
3. If its body can be large, add a case to `body_limit`.
4. Call `store.audit(...)` when it changes roles, access, tasks, reviews or data.
5. Add it to `docs/PERMISSIONS.md`; the matrix test requires it.

### A new event type

1. Choose a dedupe key that says what "the same event" means ([10.3](#103-dedupe-keys)).
2. Add the type to `TOPICS` in [gcp.py](../../backend/app/adapters/gcp.py), or `gcp` mode will raise on publish.
3. Publish it with `outbox.publish`, never with the bus directly.
4. If giving it up should mark something failed, add a case to `failed()` in
   [selfhost.py](../../backend/app/selfhost.py).
5. If its key contains an id from a table not in `_EVENT_ID_TABLES`
   ([lifecycle_pg.py](../../backend/app/adapters/lifecycle_pg.py)), add the table, so deleting an organisation removes the
   key.

### A new capability for agents

Add a protocol to [ports.py](../../backend/app/ports.py), a memory adapter and a real one, a field on `Deps`, and wire it in
all three branches of `build_container`. Agents then use it through `Deps` and stay testable in memory.
