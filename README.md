# Welcome Wave

A multi-agent, multimodal onboarding platform for engineering teams, by **Vijayabaskar** and **Aman Nautiyal**.

A senior explains the code once, on a recorded KT session. Welcome Wave watches that session (speech and the shared
screen), links every moment to the exact code that was on screen, answers a new engineer's questions with
citations and the matching clip, runs a 30/60/90-day program from pre-boarding to sign-off, plans a real first pull
request, reviews it with evidence, and shows managers and leaders who is stuck, which code only one person knows, and how
fast hires ramp up.

The work is done by a multi-agent system: one Supervisor routes every event to eight specialist agents (Librarian,
Note-taker, Guide, Planner, Scout, Reviewer, Coach and Keeper; see [docs/AGENTS.md](docs/AGENTS.md)). **Agents propose; people
decide**: plans, reviews and bookings wait for a person in Approvals, and managers and admins watch the agents on the Agents
page (`#/agents`).

**Headline feature — KT-to-Code Linking.** Open any file and see "explained by Ravi in KT #1 at 00:55". Because we
know what was explained, we also know what never was: the KT coverage map, the KT planner, and tasks that fill the gaps.

## Documentation

| Doc | For |
|---|---|
| [docs/USER_GUIDE.md](docs/USER_GUIDE.md) | Each role: what Home shows, the main tasks step by step, where things live |
| [docs/ADMIN_GUIDE.md](docs/ADMIN_GUIDE.md) | Setup, GitHub, Google Calendar, Calendly, people and roles, templates, audit log, export and deletion, retention, flags, rate limits, LangSmith |
| [docs/API.md](docs/API.md) | Every HTTP route by area, who may call it, errors and limits |
| [docs/DEMO_SCRIPT.md](docs/DEMO_SCRIPT.md) | A verified 5-minute walkthrough on demo data, no keys |
| [docs/AGENTS.md](docs/AGENTS.md) | The Supervisor and the eight specialist agents |
| [docs/STATUS.md](docs/STATUS.md) | What landed this round, what is behind flags, what was deferred |
| [docs/DEPLOY.md](docs/DEPLOY.md) | Google Cloud runbook and day-2 operations |
| [docs/SECURITY.md](docs/SECURITY.md) | Security controls and data processing |
| [docs/PERMISSIONS.md](docs/PERMISSIONS.md) | Every route × role, enforced by a test |
| [docs/specs/](docs/specs/lifecycle.md) | One spec per lifecycle capability, plus scheduling |
| [docs/PLAN.md](docs/PLAN.md), [docs/CURRENT_STATE.md](docs/CURRENT_STATE.md) | The round's plan and its baseline |
| [docs/VOICE.md](docs/VOICE.md) | How we write in this repository |

## Run modes

One codebase, three modes, chosen by `MODE` (settings in `backend/app/config.py`; every setting is an environment variable):

| Mode | What it is | Start |
|---|---|---|
| `demo` | In-memory data, offline model, the sample repo; no keys. Resets on restart. | Below, or `docker compose up` |
| `self_hosted` | The real app on one machine: Postgres in Docker, Firebase sign-in, Gemini by API key, files on disk | `./scripts/selfhost.sh setup`, then `start` |
| `gcp` | Cloud Run, Cloud SQL, Vertex AI, Cloud Storage, Pub/Sub, GKE workers | [docs/DEPLOY.md](docs/DEPLOY.md) |

## Run it in two minutes (no keys needed)

```bash
# backend: in-memory data, offline model, a small sample repo and one recorded KT session
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
MODE=demo .venv/bin/uvicorn app.main:app --port 8080

# frontend (second terminal)
cd frontend
npm install
npm run dev            # http://localhost:5173 ; sign in with a demo account (password: welcome-wave)
```

Demo mode reads no env file for its settings, so it runs the same everywhere. Put `GEMINI_API_KEY=...` in the command's
environment to get real written answers, session analysis and reviews in demo mode.

The demo organisation is seeded for every role: hires on days 3, 12, 45 and 88, a completed cohort before them, two teams,
one recorded KT session, a leaver with a handover. Every member signs in with their email and one shared password
(`DEMO_PASSWORD`, default `welcome-wave`, shown on the sign-in page), or with one click under **Demo accounts**:

| Person | Email | Role |
|---|---|---|
| Alex | `admin@demo.dev` | admin |
| Priya | `priya@demo.dev` | manager |
| Ravi, Meera | `ravi@demo.dev`, `meera@demo.dev` | experts (mentors, code owners) |
| Vijay | `vijay@demo.dev` | new hire, day 12 |
| Arjun, Sara, Tom | `arjun@`, `sara@`, `tom@demo.dev` | new hires, days 3, 45 and 88 |
| Elena | `elena@demo.dev` | executive (read-only) |

Every role lands on its own **Home**. A wrong password and an unknown email get the same answer, and ten tries a minute per
email is the limit.

The demo stands in for the services it runs without, through demo-only routes (`app/api/demo.py`, never registered in
production):

- **Pre-boarding**: Arjun's page is at `/welcome/demo-welcome-arjun` (no sign-in). Real links are random, shown once and
  expire after 14 days.
- **Demo clock**: managers and admins see a **Day N** control in the top bar (`POST /api/demo/advance`). It moves the whole
  organisation forward through the 90 days: milestones, check-ins, blocker escalation and reports follow.
- **GitHub App**: on a code task, the new hire's **Open a pull request** button sends a `pull_request` webhook through the
  real handler. Every demo pull request carries the same diff (`DEMO_PR_FILES` in `app/seed.py`), so the Review agent has
  real lines to judge: the rule checks and the model (offline line checks, or Gemini with a key) report findings with
  evidence from the diff.
- **Invitations**: someone invited under Settings > People, or enrolled, is listed on the sign-in page under **Invited**;
  accepting joins them, as their first sign-in would in production, and signs them in. From then on their email and the demo
  password sign them in like any other account (before accepting, their email does not sign in).
- **Google Calendar**: KT sessions are scheduled on a built-in demo calendar (`MemoryScheduler`): each gets a Meet-style
  link, and **Import from Google Drive** always finds a recording, so the whole flow (schedule, import, notes) plays without
  keys. Real Google Calendar, Meet and Calendly are set up in self-hosted and Google Cloud modes
  ([docs/specs/scheduling.md](docs/specs/scheduling.md)).

```bash
cd backend && .venv/bin/python -m pytest -q   # 488 passed, 53 skipped without Postgres (+ Postgres tests with TEST_PG_ADMIN_URL)
MODE=demo .venv/bin/python -m eval.run_eval   # retrieval recall@5 on eval/golden_set.jsonl (answer metrics with a real model)
cd ../frontend && npx tsc --noEmit -p .        # type-check (there are no frontend tests yet)
```

## Run it for real on one machine (self-hosted)

The demo above is made-up data that resets on every restart. Self-hosted mode (`MODE=self_hosted`) is the real
application on one Mac or server: real accounts, data kept in Postgres, real GitHub repositories, Gemini, and live
updates, without Google Cloud.

```bash
./scripts/selfhost.sh setup     # backend/selfhost.env with fresh passwords, Postgres in Docker (port 5433), migrations
# fill in backend/selfhost.env: FIREBASE_PROJECT_ID + FIREBASE_API_KEY (a free Firebase project), GEMINI_API_KEY
./scripts/selfhost.sh start     # the API on :8080 and the web app on http://localhost:5173
./scripts/selfhost.sh backup    # a dated dump of the database in backups/
```

| | Demo | Self-hosted | Google Cloud |
|---|---|---|---|
| Data | in memory, seeded, reset on restart | Postgres (Docker volume `ww-db-data`) | Cloud SQL |
| Sign-in | made-up accounts, one password | Firebase Authentication | Identity Platform |
| AI | offline stand-in | Gemini API key (off until one is added, and the app says so) | Vertex AI |
| Repository | the sample repo | any GitHub URL; watched for commits and pull requests every 2 min | GitHub App + webhooks |
| Background work | inline | worker threads in the API, retried from the outbox | Pub/Sub + GKE workers |
| KT sessions | a built-in demo calendar | Google Calendar + Meet, Calendly, or anywhere | Google Calendar + Meet, Calendly, or anywhere |
| Their recordings | one pre-recorded session; imports always "find" one | imported from Google Drive, or uploaded (MP4/WebM) | imported from Google Drive, or uploaded |
| Audit-log purge | none | daily, in the API | Cloud Scheduler job `ww-audit-purge` |
| Live updates | yes | yes (Postgres `LISTEN`) | yes (Postgres `LISTEN`) |

Every page updates on its own as things change: a notification, a decision another manager made, a task the Review
agent moved, a session someone booked, a repository that finished indexing. Postgres triggers announce each change
(`db/migrations/009_live.sql`), the API streams it to signed-in browsers (`GET /api/live`), and the page reloads that
part through the normal API, so permissions apply as usual.

## Architecture

| Layer | What runs there |
| --- | --- |
| Cloud Run `ww-web` | React app (nginx), forwards `/api` to the API |
| Cloud Run `ww-api` | FastAPI: auth (Identity Platform), chat, the onboarding lifecycle, approvals, webhooks; runs the Supervisor for chat and approval resumes |
| GKE Autopilot | `meeting-worker`, `ingestion-worker`, `review-worker` — one image, scaled by KEDA on Pub/Sub backlog |
| LangGraph Supervisor | routes every event to one agent, chains agents, pauses for a person's approval, escalates failures |
| Agents | Today's modules: Ingestion, Project Q&A, Meeting, Task, Review (`backend/app/agents/`). This round they become eight named specialists under the Supervisor ([docs/AGENTS.md](docs/AGENTS.md)). |
| Lifecycle services | Programs, pre-boarding, routing, first task, progress, blockers, check-ins, completion, handovers, reports (`backend/app/lifecycle/`) |
| Cloud SQL Postgres 16 + pgvector | all app data, embeddings (HNSW), LangGraph checkpoints; row-level security per organisation |
| Cloud Storage, Pub/Sub, Vertex AI (Gemini), Secret Manager, Cloud Logging/Monitoring | files, events, models, secrets, operations |
| Google Calendar + Meet, Calendly | KT sessions are scheduled there; Meet recordings are imported from Google Drive |
| LangSmith | optional, off by default: a trace of every agent run (docs/SECURITY.md §13) |

Agents only see the interfaces in `app/ports.py`. In demo mode those are in-memory (`adapters/memory.py`,
`adapters/lifecycle_memory.py`); in production they are Cloud SQL, GitHub App, GCS and Pub/Sub (`adapters/postgres.py`,
`lifecycle_pg.py`, `github.py`, `gcp.py`). The GitHub client has no write methods at all: agents are read-only by construction.

## Folder structure

```
welcome-wave/
├── backend/
│   ├── app/
│   │   ├── agents/          supervisor.py (LangGraph), qa.py, meeting.py, task.py, review.py, ingestion.py,
│   │   │                    llm.py (Gemini / offline / off), prompts.py, schemas.py, deps.py
│   │   ├── lifecycle/       templates, enrolment, preboarding, routing, first_task, presubmit, progress, blockers,
│   │   │                    summaries, feedback, checkins, completion, handover, visibility, reporting, home, clock, flags
│   │   ├── services/        kt_linker.py ★, knowledge.py, code_tour.py, kt_planner.py, kt_review.py, knowledge_risk.py,
│   │   │                    scheduling.py, coverage.py, chunking.py, diff.py, codeowners.py, redaction.py, embeddings.py,
│   │   │                    dashboard.py
│   │   ├── adapters/        memory.py, lifecycle_memory.py (demo/tests); postgres.py, lifecycle_pg.py, audit_store.py;
│   │   │                    github.py, github_url.py, gcp.py, google_calendar.py, calendly.py
│   │   ├── api/             routes.py, lifecycle.py, members.py, audit.py, integrations.py, knowledge_risk.py,
│   │   │                    kt_planner.py, code_tour.py, webhooks.py, outbox.py, stream.py, auth.py, demo.py (demo only)
│   │   ├── ports.py         interfaces agents and services depend on
│   │   ├── container.py     wiring for demo, self-hosted and gcp
│   │   ├── config.py        settings (environment variables)
│   │   ├── ratelimit.py     per-person and per-address limits
│   │   ├── live.py          live updates (SSE, Postgres LISTEN)
│   │   ├── migrate.py       migrations, row-level-security checks
│   │   ├── selfhost.py      self-hosted background work
│   │   ├── seed.py          the demo organisation
│   │   └── main.py          FastAPI app, security headers, request logs
│   ├── workers/worker.py    Pub/Sub consumer for GKE
│   ├── db/migrations/       001_init.sql … 013_members.sql (forward-only, forced row-level security)
│   ├── eval/                golden_set.jsonl, run_eval.py
│   ├── load/locustfile.py   load test
│   ├── sample_repo/         small codebase used by the demo and tests
│   ├── scripts/             check_models.py
│   └── tests/               services, agents, lifecycle, permissions matrix, Postgres, injection fixtures
├── frontend/
│   ├── src/site/            the landing page and its 3D story (Story.tsx, office/ with three.js)
│   ├── src/auth/            sign-in (Firebase, demo accounts)
│   ├── src/lifecycle/       Home, Setup, Enrol, Team, Program, Reports, Templates, Handovers, People, Audit, Feedback,
│   │                        Visibility, DemoClock; portal/ (the pre-boarding page)
│   ├── src/pages/           Chat, CodeTour, Code ★, Meetings, Tasks, Review, Approvals, Inbox, KtPlanner, KnowledgeRisk,
│   │                        Dashboard, Document, Settings (+ settings/), NewProject, GitHubCallback
│   ├── src/                 App.tsx, Workspace.tsx (rail, g-keys), CommandPalette.tsx (⌘K), api.ts, live.ts, styles/
│   └── nginx.conf.template  the web app's CSP and headers
├── infra/terraform/         APIs, service accounts, Cloud SQL, GCS, Pub/Sub + dead letters, Scheduler jobs, GKE, secrets, alerts
├── infra/k8s/workers.yaml   worker deployments + KEDA scaling
├── infra/scripts/           bootstrap-cluster.sh, set-secrets.sh
├── scripts/                 selfhost.sh (self-hosted), package.sh (share the code without secrets)
├── docs/                    guides, API, specs, plan, status (see "Documentation")
├── .github/workflows/ci.yml tests (memory and Postgres), eval, type-check, build, CSP check, audit, images
├── cloudbuild.yaml          test → evaluate → build → deploy (Cloud Run + GKE)
└── docker-compose.yml       local demo, optional Postgres + pgvector
```

## Going to production

Step-by-step runbook (Terraform → secrets → GitHub App → CI/CD → first sign-in) and day-2 operations (rollback,
dead letters, restore, secret rotation): **[docs/DEPLOY.md](docs/DEPLOY.md)**. Admin tasks after the first sign-in:
**[docs/ADMIN_GUIDE.md](docs/ADMIN_GUIDE.md)**.

What production adds on top of the demo:

- **Sign-in and tenancy.** Identity Platform login; self-serve sign-up creates an organisation; managers invite people
  by email, and on first sign-in the person sees the invitation and chooses to join. Every business table is under
  forced row-level security and the services connect as `ww_app`, which cannot bypass it (the API refuses to start
  otherwise). The tenant is set in an async auth dependency so it reaches every endpoint (a sync one would lose it).
- **Safe repository access.** A GitHub App installation is bound to an organisation only by the GitHub user who
  installed it (GitHub's signed `installation` webhook names them; the OAuth code proves who is in the browser); the
  client has no write methods. One repository per project.
- **Exactly-once events.** Each event is claimed with a lease kept alive by a heartbeat and marked done only after it
  succeeds, so duplicates are skipped and a crashed worker's event is retried rather than lost. The API writes each
  event to an outbox table before publishing it, and a Cloud Scheduler job re-sends any whose publish failed.
- **Migrations** run as a Cloud Run job under the owner role (`python -m app.migrate`); LangGraph's checkpoint tables
  are created there, and a finished run's checkpoints are deleted.
- **Audit and limits.** Admins read and export the audit log (`GET /api/audit`, `/api/audit.csv`); entries older than
  `AUDIT_RETENTION_DAYS` are purged daily. Every route is rate-limited per person or address (`app/ratelimit.py`).
- **Tests:** unit, agent, lifecycle, golden-path and API tests run in memory; `tests/test_postgres.py` and
  `tests/test_lifecycle_postgres.py` run the golden path, tenant isolation, invitations, event leases, the lifecycle and the
  whole HTTP API in production auth mode against real PostgreSQL + pgvector set up like Cloud SQL
  (`TEST_PG_ADMIN_URL=postgresql://ww:ww@localhost:55432/postgres .venv/bin/python -m pytest`). CI runs both, and fails if the
  Postgres tests were skipped.
