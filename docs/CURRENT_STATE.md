# Welcome Wave: current state (Phase 0 baseline)

> Historical: written at the start of round 1 (2026-11-14). It describes a tree that has since changed a great deal (LiveKit is gone, the lifecycle, the agents and email now exist). For today's behaviour read `docs/STATUS.md`, `docs/PERMISSIONS.md`, `docs/AGENTS.md` and `docs/INTEGRATIONS.md`; where this page disagrees with the code, the code wins.

Assessed 2026-11-14. This describes the working tree at the **start** of enterprise round 1. Tracks A to F in
[PLAN.md](PLAN.md) are changing it in parallel: LiveKit is being replaced by Google Calendar/Meet and Calendly (B), a
lifecycle API (C) and UI (D) are being added, the landing page is becoming a 3D story with three.js (A), and repo
hygiene, CI and security docs are in progress (F). Where this page and the code disagree after round 1, the code wins;
`docs/STATUS.md` records what landed.

Sources: a read of `backend/`, `frontend/`, `infra/`, `cloudbuild.yaml`; the demo API driven as each role (section 2); the
test and build numbers in section 4.

## 1. Architecture

### 1.1 Run modes (`MODE`, `app/config.py`, `app/container.py`)

| | `demo` | `self_hosted` | `gcp` |
|---|---|---|---|
| Store / index | `MemoryStore` (wrapped by `LiveStore`), `MemoryIndex` + `HashEmbedder` | Postgres + pgvector (`PgStore`, `PgIndex`) | Cloud SQL Postgres 16 + pgvector |
| LLM | `OfflineLLM`; `GeminiLLM` if `GEMINI_API_KEY` | Gemini by API key; otherwise `NoLLM`, and AI routes return 503 `AI_OFF` | Vertex AI Gemini (`gemini-3.1-flash`/`-pro`, `gemini-embedding-001`, 768-d) |
| Sign-in | `X-Demo-User` header (trusted) | Firebase Auth (free project) | Identity Platform (email/password) |
| Files | `LocalBlobStorage(".blobs")`, relative to the process cwd | Disk under `DATA_DIR`, signed links via `/api/blobs/{token}` | GCS (signed URLs) |
| Events | `InlineBus`: handled synchronously in the request | `LocalQueue` threads pumping the outbox table | Pub/Sub (3 queues + dead letters), GKE workers, Scheduler outbox flush |
| Repos | `LocalRepoSource(sample_repo)`, demo issues and PR diff | By URL (`GitHubByUrl`, optional `GITHUB_TOKEN`), polled by `RepoWatcher` every 120 s; GitHub App if configured | GitHub App (read-only), webhooks |
| Checkpoints | `MemorySaver` | `PostgresSaver` | `PostgresSaver` |
| Calls | LiveKit if keys are present, never recorded | Recording uploaded by hand | LiveKit Cloud + Egress to GCS |
| Extras | Seeded org; `/api/docs` and `/api/demo/*` on | Single process; `scripts/selfhost.sh` | `ENV=prod` refuses demo mode, the emulator and dev secrets (`production_problems`) |

`Settings` reads `backend/.env` in **every** mode, so local keys change demo behaviour. In this assessment `/api/config`
reported `livekit: true` and `POST /meetings` returned a LiveKit token.

### 1.2 Backend layers

| Layer | Files | Notes |
|---|---|---|
| Ports | `app/ports.py` (326 lines) | Pydantic rows; `Index`, `Store`/`AppStore` (108 methods), `RepoSource`, `BlobStorage`, `EventBus`. Optional capabilities found with `getattr` (`put_file`, `signed_url`, `verify`, `installation_account`). |
| Adapters | `memory.py` (925 lines), `postgres.py` (950), `github.py`, `github_url.py`, `gcp.py` | Every store method is written twice: once in memory, once in SQL. |
| Agents | `agents/` ingestion, qa, meeting, task, review, llm, prompts, schemas | Agents see only ports. The GitHub client has no write methods. |
| Supervisor | `agents/supervisor.py` | LangGraph. `route` sends each event to one node. `guarded` retries a node, records it in `agent_runs`, and escalates on failure. |
| Services | `services/` knowledge (1,197 lines), code_tour (1,155), kt_planner (545), knowledge_risk, kt_review, kt_linker, coverage, dashboard, chunking, codeowners, diff, embeddings, redaction, livekit | `knowledge.py` is the shared core behind knowledge risk, the KT planner and the code tour. |
| API | `api/routes.py` (951 lines, 50 routes), `auth.py`, `demo.py`, `stream.py`, `outbox.py`, `webhooks.py`, plus one module each for `knowledge_risk`, `kt_planner` and `code_tour` | Each handler validates, checks permissions, then calls the store or the Supervisor. `require(*roles)` always lets admin through. `check_project` returns 404 to non-members. |
| Runtime | `main.py`, `live.py`, `selfhost.py`, `migrate.py`, `secrets.py`, `workers/worker.py` | JSON logs with a request id, security headers, `/health` and `/health/ready`. |

**Supervisor routes:**

| Event | Path through the graph |
|---|---|
| `repo.connected`, `repo.pushed`, `document.uploaded` | ingest |
| `chat.question` | qa (runs on a graph with no checkpointer, so it never pauses) |
| `meeting.recording_ready` | meeting → `plan_tasks` if there are action items and active onboardings |
| `onboarding.started` | `plan_tasks` → **interrupt `task_plan`** |
| `pr.opened`, `pr.updated`, `pr.merged` | `pr_status` → review (code tasks only) → **interrupt `review`** |

- The `kt_followup` approval (from migration 008) is not a graph pause: `kt_review.decide` books the follow-up sessions directly.
- `Container.decide` claims the approval, stores the first decision and resumes the paused thread. It raises `ApprovalBusy`, `ApprovalStale` or `ApprovalNotReady` (each returns 409).
- When a run finishes, its checkpoints are deleted.

**Reliability:**

- Every event is first written to the `outbox` table (`api/outbox.py:publish`), then sent.
- `Container.handle` claims the event's dedupe key with a lease, keeps the lease alive with a heartbeat thread, and marks it done only on success.
- A run that stopped mid-graph continues from its last checkpoint.
- GCP: Cloud Scheduler calls `POST /internal/outbox/flush` with an OIDC token. Pub/Sub moves a message to the dead-letter topic after 5 attempts.
- Self-hosted: `LocalQueue` retries with backoff, up to 6 attempts.

**Live updates (`app/live.py`, `GET /api/live`, SSE):**

- The payload is `{t: topic, p: project}` only. The browser re-fetches through the normal, permission-checked API.
- Postgres: triggers from 009 `NOTIFY ww_live`, and `PgListener` relays them to the `LiveHub` in each API process.
- Demo: `LiveStore` works out the topic from the store method's name with a regex.
- The stream closes after 15 minutes, so the browser reconnects with a fresh token. It pings every 20 s.

### 1.3 Data model by migration (`backend/db/migrations`, applied by `app/migrate.py`, forward-only)

| # | Adds |
|---|---|
| 001 | Enums. Tables: `organizations`, `users` (one org-wide `role`: manager, new_hire, expert or admin), `projects`, `project_members`, `onboardings` (status active/completed/paused), `repositories` (+`codeowners` text), `documents`, `chunks` (vector HNSW + tsvector), `meetings`, `meeting_participants`, `transcript_segments`, `screen_moments`, `meeting_notes`, `action_items`, `kt_links`, `tasks`, `approvals`, `reviews`, `chat_messages` (+`feedback`), `expert_requests`, `notifications`, `agent_runs`, `processed_events`, `audit_log`. Forced RLS on the 20 business tables. |
| 002 | Event leases; approval claims; `invitations` (14-day expiry); `github_installations`; `documents.status`; `meetings.egress_id`. RLS on invitations, installations, `organizations`, `agent_runs`, `audit_log`. `SECURITY DEFINER` functions owned by `ww_definer`. |
| 003 | Invitations accepted explicitly, per organisation. `github_installation_senders` (installer proof). One repository per project (unique index). More `FORCE RLS`. |
| 004 | `search_path` hardening. One active onboarding per (project, hire). `outbox`. `approvals.decision`. `processed_events.lease_s`. |
| 005 | Rejoin after leaving: the user row is kept and rebound by a new invitation. |
| 006 | `meetings.kt_session_key` + one live booking per session. Status `cancelled`. `chunks_code_order` index. |
| 007 | Meeting status `ended`. |
| 008 | Approval kind `kt_followup`. |
| 009 | `ww_live_notify` triggers on 15 tables. `watched_repositories()`. |

**Tenancy:**

- Every tenant row carries `org_id`. The policy keys on `current_setting('app.org_id')`, which `auth.current_user` sets on the request task.
- The app connects as `ww_app` (`NOBYPASSRLS`); the API refuses to start if that role can bypass RLS.
- Lookups that run before the tenant is known go through definer functions.
- Not under RLS: `outbox`, `processed_events`, `github_installation_senders`, `schema_migrations` and LangGraph's checkpoint tables.

### 1.4 Frontend (`frontend/src`, React 18 + Vite 5, no router library, no state library)

The app has two lazily loaded surfaces (`main.tsx`, `surface.ts`):

- **Site.** `site/`: `/` landing, `/about` and a 404. `Site.tsx` (595 lines), Nav, Footer, Scrubber, Coverage, PlanAxis, and `site.css` (81 KB). It never downloads the app; `handover.ts` prefetches the app when a visitor hovers "Log in".
- **App.** `AppEntry` → `App.tsx`:
  - In demo mode it shows `DemoSession`. Otherwise `AuthGate` (`auth/`: Entry, SignIn, Setup, Invited, `firebase.ts`, and the `terrain.ts` animated background at 729 lines).
  - Then the `Workspace.tsx` shell (479 lines): rail nav with g-key shortcuts, project switcher, breadcrumbs, live status, `CommandPalette.tsx` (441), `Notifications.tsx` and `live.ts` (SSE).
  - Routing is in the hash (`/app#/<page>`).

| Hash route | Page | Who sees it | Main API |
|---|---|---|---|
| `#/chat` (default) | Chat "Ask the project" | all | `/chat`, `/chat/history`, `/chat/{id}/feedback` |
| `#/code-tour` | CodeTour (812 lines) | all; new-hire landing page | `/tour` |
| `#/code` | Code "Code and KT" | all | `/code/files`, `/code/explanations`, `/code/file`, `/coverage` |
| `#/meetings[/id]` | Meetings (574) + Call (LiveKit) | all | `/meetings*` |
| `#/documents/id`, `#/reviews/id` | Document, Review | all (permission-checked) | `/documents/{id}`, `/reviews/{id}` |
| `#/tasks` | Tasks ("My tasks" / "Team tasks") | all | `/tasks`, `PATCH /tasks/{id}`, `/demo/pull-request` |
| `#/inbox` | Inbox "Expert inbox" | expert, manager, admin; expert landing page | `/experts/requests*` |
| `#/kt-planner` | KtPlanner (597) | expert ("My KT queue"); manager, admin | `/kt-plan`, `/kt-plan/schedule` |
| `#/approvals[/id]` | Approvals | manager, admin | `/approvals*`, `/meetings/{id}/followups` |
| `#/dashboard` | Dashboard "Onboarding health" | manager, admin; their landing page | `/dashboard` |
| `#/knowledge-risk` | KnowledgeRisk (588) | manager, admin | `/knowledge-risk` |
| `#/settings?tab=` | Settings: people, repository, documents, onboarding, account | manager, admin: all tabs; expert: documents and account; new hire: account (outside demo only) | `/projects/*`, `/github/*`, invitations, onboardings |
| `#/projects`, `/github/callback` | NewProject, GitHubCallback | manager, admin | `POST /projects`, `/github/callback` |

Styles: `styles.css` plus 13 sheets in `styles/` (about 300 KB of CSS source).

Baseline build chunks:

| Chunk | Size |
|---|---|
| index | 8 KB |
| react | 142 KB |
| Site | 59 KB JS + 58 KB CSS |
| AppEntry | 313 KB JS + 248 KB CSS |
| firebase | 173 KB |
| livekit | 696 KB (loaded only by Call) |

### 1.5 Infra and delivery

| Piece | Contents |
|---|---|
| `infra/terraform` | `main.tf` (APIs, variables). `iam.tf` (service accounts: api, worker, migrate, web, egress, keda, scheduler, build). `data.tf` (Cloud SQL, GCS). `events.tf` (Pub/Sub ingestion/meetings/reviews + dead letters, Scheduler flush). `compute.tf` (Artifact Registry, GKE Autopilot, Cloud Build triggers: main → dev, tag → prod). `secrets.tf`. `identity.tf` (email/password; Google sign-in needs a manual OAuth client). `lb.tf` (HTTPS LB, Cloud Armor, managed cert). `observability.tf` (uptime check; alerts for dead letters, 5xx and unsent outbox; budget). |
| Not in Terraform | Cloud Run `ww-api`, `ww-web` and the `ww-migrate` job are created by `gcloud` in `cloudbuild.yaml`. |
| `infra/k8s/workers.yaml` | Three worker Deployments (meetings, ingestion, reviews) with a Cloud SQL proxy sidecar. KEDA scales them to zero. |
| `cloudbuild.yaml` | postgres → backend-tests (ruff + pytest including Postgres) → eval gate → images (the web image runs `tsc`) → push → migrate job → deploy api/web → deploy workers → smoke test. LangSmith tracing is on by default. |
| Local | `docker-compose.yml` (demo api :8080, web :5173, optional pgvector). `scripts/selfhost.sh` (setup, start, migrate, check, backup). `docs/DEPLOY.md` runbook. |

## 2. Current workflows by role

**How roles work:**

- A role is per organisation (`users.role`), not per project. Project membership controls which projects a person can see.
- Admin passes every `require()` and sees every project in its org. There are no admin-only routes; the only admin-only rule is that only an admin can invite another admin.

**Demo sign-in (`api/demo.py`, `config.demo_password`):**

1. `POST /api/demo/login {email, password: "welcome-wave"}` returns the user. A wrong password and an unknown email get the same 401, and attempts are limited to 10 a minute per email and client.
2. Every later call carries `X-Demo-User: <id>`.
3. The header alone is the session: the password only guards the form.
4. Demo users: `u-admin` (Alex), `u-priya` (manager), `u-vijay` (new hire), `u-ravi` and `u-meera` (experts).

**Seed:**

- One org and one project, "Mini Policy Engine".
- One repo (`sample_repo`) with 7 indexed paths, 4 of them code.
- One ready KT call (`mtg-1`, 3 KT links).
- Vijay's onboarding, started 2 days ago.
- Two pending approvals:
  - a `task_plan` with 2 tasks: "Read webhooks", due in 5 days, and "First PR: issue #12", due in 14 days;
  - a `kt_followup`.

### 2.1 Access as observed (`MODE=demo`, port 8095, GET unless noted)

| Route | admin / manager | expert (Ravi) | new hire (Vijay) |
|---|---|---|---|
| `/me`, `/projects`, `/projects/{id}` | 200, plus invitations | 200 (members, all onboardings) | 200 (members, all onboardings) |
| `/chat/history` (own), `/meetings`, `/code/files`, `/coverage`, `/tour` | 200 | 200 | 200 |
| `/meetings/mtg-1/notes` | transcript `full` (7 utterances) | `managers_only` (0) | `managers_only` (0) |
| `/tasks` | all project tasks, including `proposed` drafts | the same, including drafts | own active onboarding, approved and later only (0 before approval) |
| `/approvals`, `/dashboard`, `/runs`, `/knowledge-risk`, `/meetings/{id}/followups`, `/github/*` | 200 | 403 | 403 |
| `/experts/requests` | 200 | 200 (own) | 403 |
| `/kt-plan` | scope `team`, 2 sessions, 9.5 min | scope `mine`, 1 session | 403 |
| `POST` invitations, projects, repositories, onboardings | allowed | 403 | 403 |
| `POST` documents, `DELETE` documents | allowed | allowed | 403 |
| `POST /meetings` (start a call) | allowed | allowed | allowed |

Other figures returned:

- Knowledge risk: 4 code files (2 critical, 1 high, 1 low), all single-owner.
- Tour: 9 stops in 2 chapters.
- Dashboard: Vijay on day 3, 0 tasks before approval.

### 2.2 Walkthrough (state changes through the API)

1. **Manager approves the plan:**
   - Priya `POST /approvals/{task_plan}/decide {approve: [both]}`.
   - Both tasks become `approved`, and Vijay gets two "New task" notifications.
2. **The new hire works:**
   - `PATCH /tasks/{code}` moves the task to `in_progress`.
   - `POST /chat` "Why does validation never raise?" is `answered` by the offline LLM, citing `docs/CONTRIBUTING.md`.
   - "On-call rota for billing?" comes back `not_enough_information` and is routed to Ravi "who owns engine/policy.py". That owner has nothing to do with the question.
3. **The expert answers:**
   - Ravi sees the request in `/experts/requests` and answers it.
   - The answer is indexed as a `verified_answer` chunk and appears in Vijay's chat history.
4. **First PR:**
   - Vijay `POST /demo/pull-request`.
   - The task becomes `submitted`; the Review agent returns `needs_work` (no test added; a new `raise` in validation).
   - A `review` approval waits for Priya. She decides `changes_requested`, and the task follows.
5. **Dashboard afterwards:**
   - `first_pr_status` = `changes_requested`.
   - `questions_unanswered` = 1 even though Ravi answered: a bug (section 3.3).
6. **KT follow-up:**
   - Priya approves 1 session on the `kt_followup`.
   - A `scheduled` call "KT with Ravi: engine/validate.py, engine/policy.py" is created, and Ravi gets a `kt_request` notification.
7. **Invitation:**
   - Priya invites `sam@demo.dev` as a new hire. `sign_in_url` is null (no `PUBLIC_URL`) and no email is sent.
   - The invitation is listed at `/demo/people`. An expert who tries to invite gets 403.

### 2.3 What each role can do today

| Role | Can do | Cannot do |
|---|---|---|
| Admin | Everything a manager can, across all projects of the org; invite admins | Org settings; remove or deactivate a member; change a role; read the audit log; export or delete data; see seats or billing |
| Manager | Create projects; connect one repo per project (GitHub App, or a URL when self-hosted); upload documents; invite by email; start an onboarding for an existing member (Task agent drafts a plan of up to 30 days → approve or edit or reject); decide PR reviews; review KT call transcripts and book follow-ups; book KT sessions; read the dashboard, knowledge risk and agent runs; mark submitted tasks done or ask for changes; answer any expert request | Set a mentor, team, template or milestones; complete or pause an onboarding; see per-hire evidence or alerts; get an org-level report |
| New hire | Ask with citations and KT clips; take the code tour (progress kept in the browser only); browse code and KT clips; watch call notes (no transcript); move own tasks approved → in_progress → submitted; open a PR (demo route) and read own reviews; start a call; get notifications | Pre-boarding; ask for a person directly; see what managers see about them; give feedback beyond a thumbs up or down; self-assess; take part in check-ins |
| Expert | Answer routed questions; see their own KT queue and book their sessions; upload and delete documents; see team tasks and any review | Set office hours or an interruption limit; get a digest; verify an AI answer they were not routed |

## 3. Gaps and rough edges

### 3.1 Against the enterprise lifecycle (targets in PLAN.md §3–5)

| Area | Today | Missing |
|---|---|---|
| Setup flow | `POST /signup` creates the org, its first project and the admin. Configuration is spread across the Settings tabs. | A guided checklist (`/setup`), teams, program templates, org settings, a seat count |
| Enrolment | Two steps: an invitation (no email; link only if `PUBLIC_URL` is set), then the manager starts an onboarding for a member who has already joined | One enrol action; mentor; team; template and version; focus area; enrolling before the account exists |
| 30/60/90 plans | One plan of up to 30 days (`plan_horizon_days=30`, ≤12 tasks of kind learn/code/meet). Tasks are added from KT action items. | Milestones at 30/60/90 that scale with duration; milestone status |
| Pre-boarding | None | Expiring link, acknowledgements, environment check, access checklist |
| Tiered routing | An AI answer (confidence ≥ 0.5) or a hand-off to one CODEOWNERS or committer owner. Open requests are deduplicated. Verified answers are indexed and reused. | Related-answers tier; mentor tier; escalation the hire asks for; interruption caps, digest and office hours; response target; a fallback when the owner match is irrelevant |
| First contribution | Task agent picks a good-first-issue; Review agent checks rules plus diff-evidenced LLM findings, then the manager approves; a merged PR closes the task | Scored shortlist with reasons; private pre-submit review; exclusion of single-owner critical files |
| Progress | Dashboard: tasks done/total, day N, first-PR status | Evidence levels per area; a view of progress for the hire |
| Blockers | `at_risk` boolean: overdue, more than one changes request, or ≥5 questions with >40% unanswered. No reasons shown. | Rule alerts with evidence; escalation hire → mentor → manager; resolution |
| Summaries | KT call summaries only | Weekly progress summaries with citations |
| Feedback | Chat thumbs up/down stored in `chat_messages.feedback`; nothing reads it | Documentation feedback queue; survey with n ≥ 5 floors |
| Check-ins | None | Day 30/60/90 check-ins |
| Completion | `onboardings.status` can be completed or paused, but no route or UI sets either | Completion review and sign-off |
| Offboarding | Knowledge risk shows each person's exposure; `POST /me/leave` lets a person leave by themselves | Handover plan; last day; admin removal, role change and deactivation (no API exists) |
| Visibility page | None. Hires are not told that the dashboard counts their questions, or that KT planner and knowledge-risk reasons quote their chat questions with their name (`knowledge.py` ~L1156 and ~L1169) to managers and experts. | `GET /onboardings/{id}/visibility` |
| Reporting | Per-project dashboard and insight views; `/runs` | Org-level report, CSV, `executive` role, a "how" for each figure |
| SSO/SCIM | Identity Platform email/password; sign-up open by default | SAML/OIDC SSO, SCIM provisioning and deprovisioning, domain capture |
| Audit | `store.audit` writes about 20 action types to `audit_log` | Read API and UI, CSV export, retention |
| Export/delete | Document delete only. Leaving keeps the user row. | Org and person export and deletion; retention for transcripts, recordings and chat |
| Email | None. Every notification is in-app only. | `Mailer` port (PLAN §1) |
| CI | Cloud Build, run only by GCP triggers | GitHub Actions; frontend lint and tests; a check of the package |
| Docs | README, `DEPLOY.md`, PRD PDF, Oct-15 diagrams (they show LiveKit), `PLAN.md` | `SECURITY.md`, `PERMISSIONS.md`, `docs/specs/`, a repo `CLAUDE.md`, an API reference outside demo (OpenAPI is served only in demo) |

### 3.2 Code quality and hygiene

**Large files:**

- Backend: `services/knowledge.py` (1,197 lines / 62 KB), `services/code_tour.py` (1,155 / 61 KB), `api/routes.py` (951 / 51 KB), `adapters/postgres.py` (950 / 59 KB), `adapters/memory.py` (925 / 44 KB).
- `routes.py` holds 12 unrelated areas (accounts through notifications).
- Frontend: `CodeTour.tsx` (812), `auth/terrain.ts` (729), `KtPlanner.tsx` (597), `Site.tsx` (595), `KnowledgeRisk.tsx` (588), `Meetings.tsx` (574).

**Duplicated logic:**

- Two sliding-window rate limiters: `routes._RateLimiter` and `demo._Attempts`. Both are per process, so the real limit is the limit times the number of instances.
- Two tenant setters: `auth._set_tenant` and `selfhost._set_org`.
- Upload checks (extension, size, the `store_it` closures) are copied between documents and recordings.
- `CallsNotConfigured` is mapped to 503 in two modules.
- `norm_question` exists in Python and again in SQL.
- Two dependency lists that drift: `pyproject.toml` (`fastapi>=0.115`) vs `requirements.txt` (`~=0.143`).
- Every store method is implemented in both memory and SQL; parity is checked only by the Postgres tests, which skip without a database.

**Fragile coupling:**

- Demo live updates depend on store method names matching the `_WRITES` and `_TOPICS` regexes in `live.py`.
- Postgres live updates need each table added to the trigger list in a migration.
- New tables also need RLS, a memory adapter and export/delete coverage.

**Test gaps:**

- No frontend tests or linter (yet the code has `eslint-disable` comments); `tsc` is the only check.
- 43 Postgres tests skip locally.
- Test files are named after review rounds (`test_round3_*`, `test_review_fixes`), not features.
- The eval set is 17 questions, retrieval recall only.
- No contract test between the hand-written types in `api.ts` and the API.

**Leftovers:**

- `backend/.blobs/local/docs/proj-demo/`: 219 files (`passwd.md`, `a.md`) left by demo-mode API tests. The demo blob store is relative to the cwd, and the upload tests write through it.
- `frontend/tsconfig.tsbuildinfo` (written by `tsc -b`), `frontend/dist/`, and `backend/.data/.signing-key` sit in the source tree. They are gitignored, but a zip made without git would include them.

**Docker:**

- There is no `frontend/.dockerignore`, so `COPY . .` copies the host's `node_modules` and `dist` over the `npm ci` output.
- `backend/.dockerignore` omits `.data/`, `selfhost.env` and `.ruff_cache`. They are not copied into the image, but they are sent as build context.

**Git:**

- `main` has no commits.
- The index holds an older partial snapshot: files marked `AM`, and about 80 untracked paths (several of them whole directories), including migrations 002–009.
- Committing without `git add -A` would record an inconsistent tree.

**Stale README:** the folder map lists only migration 001, three API modules and six pages.

### 3.3 Behaviour issues found

- **Dashboard bug:** `questions_unanswered` counts `status == not_enough_information` and ignores expert answers (`services/dashboard.py`).
- **Routing:** an off-topic question is handed to whichever owner retrieval lands on (seen in 2.2).
- **Visibility to experts:**
  - Experts see unapproved (`proposed`) tasks through `/tasks`.
  - `GET /reviews/{id}` restricts only new hires, so experts can read every hire's reviews.
  - New hires see every onboarding in the project through `GET /projects/{id}`.
- **Data model limits:**
  - A person cannot hold different roles in different projects.
  - A project has one repository.
  - Onboardings have no mentor.
- **Dates:** day counts and "overdue" use the server's local `date.today()`, not the organisation's time zone.
- **Demo data:** the organisation is named after its project. All dates are relative to "today", so the demo cannot show days 30, 60 or 90 (Track C adds `/demo/advance`).
- **Third-party data:** LangSmith tracing is on by default in Cloud Build, so code, transcripts and questions go to a third party. A data-processing decision is needed.

## 4. Test and build baseline (before any round-1 change)

| Check | Result |
|---|---|
| `cd backend && .venv/bin/pytest -q` (Python 3.11 venv; images use 3.12) | **341 passed, 43 skipped.** The skipped ones are the Postgres tests, which need `TEST_PG_ADMIN_URL`. |
| `ruff check app workers tests eval scripts` | Clean |
| `cd frontend && npm run build` (`tsc -b && vite build`) | Passes. Vite warns about a chunk over 500 kB: the lazy `livekit` chunk (696 kB minified). Track B's removal of LiveKit should clear it. |
| Frontend tests | None exist |
| `python -m eval.run_eval` | Retrieval recall@5 on `eval/golden_set.jsonl` (17 items); the Cloud Build gate |
