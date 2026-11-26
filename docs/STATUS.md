# Status

Checked against the working tree on 2026-11-18 (evening). Where this page and the code disagree, the code wins. The plan is in
[PLAN.md](PLAN.md).

## Legal basics (2026-11-19)

Terms of service, privacy policy, acceptable use, recording notice, data-processing agreement and sub-processor list are in
the product ([LEGAL.md](LEGAL.md), [PLAN.md §13](PLAN.md#13-legal-round-2026-11-19-terms-privacy-data-processing-recording-consent)).
**The texts are drafts written by an AI. No lawyer has read them, they are not legal advice, and they should not be shown to
customers as final.** Each public page says "Draft" until `LEGAL_REVIEW.status` is set to `"reviewed"`.

- **Built:** the documents' pages and Markdown copies (generated; `node docs/legal/build.mjs --check`), a data inventory
  that every data statement points to, acceptance records (`legal_acceptances`, append-only), the organisation's DPA
  acceptance, recording consent per session (migrations 019 and 020), removal requests, and the `LEGAL_*` settings. The sign-up
  screens carry the agreement and the web app asks again when a version changes.
- **Reviewed:** three independent reviews (claims, security, UX). Their security findings are fixed with tests; the wording
  was corrected to what the code does (erasure, what Gemini receives, row-level security exceptions, the audit log, Drive scope).
- **Privacy items:** PRIV-5 (the Gemini Files copy of a recording is deleted when analysis ends) and PRIV-9 (recordings
  removed on request, and by `RECORDING_RETENTION_DAYS` self-hosted; 0 keeps them) are in place and tested. The documents say
  what erasure does not reach (audit entries, transcripts, verified answers, backups, the sign-in account).
- **Open:** a lawyer's review; the six `LEGAL_*` company details (empty means neutral wording); governing law and courts;
  `LEGAL_ENFORCE` stays off until the web app that shows the prompt is deployed. The web app and the API must be released
  together, or sign-up answers 422 `legal_required`.

## Mentor round (2026-11-18)

The mentor's notes ([PLAN.md §12](PLAN.md#12-mentor-round-2026-11-18-board-audit-log-handover-brand-agents-layout-docker-and-kubernetes))
are built: the milestone board, editable milestones for admins and hires' managers, the audit log's order, dates, sort and
paging, the handover's transfer set and attachments, a product Home, the Ink + Teal brand with navy panels, the agents'
folder layout with a LangGraph guide, and both Dockerfiles with Kubernetes overlays. Three independent reviews (security, UX,
release) followed; every confirmed high and medium is fixed with a test, and so are the cheap lows.

**Release readiness: ready for a self-hosted or Kubernetes release, and for Google Cloud once the pipeline runs green in
your project.** No blocker or high is open. Not checked here: a real Cloud Run or GKE deploy, Terraform `plan`, and the
model ids on Vertex AI (as in the release round below).

### Checks (final, after the fixes)

| Check | Result |
|---|---|
| `cd backend && .venv/bin/python -m pytest -q` | 1239 passed, 84 skipped (the skipped need Postgres); nothing written into `backend/` |
| Postgres: the 15 Postgres test files plus the board, handover, home and permission-matrix files, on a scratch server | 249 passed, none skipped |
| `.venv/bin/ruff check .` | clean |
| `MODE=demo .venv/bin/python -m eval.run_eval` | every target met (demo crew R@5 0.94, nDCG@10 0.88; self crew R@5 0.78) |
| `npx tsc --noEmit -p .`, `npx vite build` | clean |
| `docker build backend`, `docker build frontend` | both build (API 550 MB, web 205 MB); the API image boots in demo mode, answers `/health/ready` and runs as uid 10001 |
| `kubectl kustomize infra/k8s/overlays/{production,staging,self-hosted}` | render: 48, 48 and 20 objects |
| Relative links in every Markdown file | 919 links, none broken |
| Browser, demo mode | Home (hire, manager, admin), board keyboard moves, audit paging and dates, the milestones dialog at 390 wide, popovers, light and dark: no console errors |
| Migrations | 001–018 (`018_board.sql`: board milestones, placements and moves, each with `org_id` and forced row-level security) |

### Review findings and what was done

| Finding | Severity | Done |
|---|---|---|
| A manager's Home activity showed other projects' events (document titles, repositories, "started a handover for …") to a manager not on them | high | Fixed: the feed is scoped by where an event happened (its `project_id`, onboarding or target), not by who did it; an event that cannot be placed is left out for managers; admins keep the organisation's feed. Board entries now carry `project_id` |
| A hire whose own role is manager could remove, re-date and re-milestone their own tasks through the board's manager routes | medium | Fixed in `_hire`: nobody is the manager of their own onboarding, which closes the older checkpoint, access, pre-boarding, first-task and completion routes too; the board refuses as well |
| `PATCH /api/handovers/{id}` with `reassign` and `POST …/complete` handed work to a stored successor without re-checking them | medium | Fixed: every path that moves work checks the successor is an active expert, manager or admin of the project, before anything is written; PATCH with `reassign` records `handover.transferred {to, moved}` |
| Completing with `deactivate` ignored the leaver's work in other projects | medium | Fixed: 409 `held_elsewhere` naming the projects while they still manage or mentor an active hire, or hold questions, access items or feedback there |
| Home never showed board activity; board rows in the audit log were raw ids and status codes | high (UX), medium | Fixed: board moves and edits read as the board ("moved 'X' to In progress on Vijay's board"), quick runs collapse into one line; the log names the task and links to the board, and shows statuses and milestones in the board's words |
| "Milestone" meant two things; an overdue checkpoint was shown as due | medium | Fixed: program checks are "checkpoints" on Home, hire rows and Templates (**+ Checkpoint**); an overdue one says "overdue since 8 Nov" and moves up |
| Alt+Shift+Arrow lost keyboard focus; a stale refocus could jump later | medium | Fixed: focus stays on the neighbouring card (or the milestone tab it went to), and a refocus is tried once |
| Popovers ("Why this?", "Keys") were not on the navy panel | medium | Fixed: they join the panel scope |
| Ticked handover keys were unbounded free text | low | Fixed: only item and gap keys of that handover's transfer set, 1,200 characters at most; anything else is 422 |
| Paging the log repeated a row when its own views were shown | low | Fixed: `as_of` snapshot on every later page and the CSV |
| Placeholders looked like values in navy panels; the overdue badge failed contrast in dark; titles cut off at 390; sort arrows hidden on desktop; dates sent for half-typed years; the log used the KT colour for selection; Home copy slips (blocker shown twice, "In review" vs "Waiting for sign-off", "change(s)", "Seats") | low | Fixed |
| Cloud Build's test step fails: `python:3.12-slim` has no git for one release test | blocker (release) | Fixed: that test skips where git is missing; GitHub CI still runs it |

### Still open from this round

| Item | Severity | Why |
|---|---|---|
| `question.routed` audit targets are still raw ids | low | Needs a lookup of expert requests in the audit naming; not part of the board fixes |
| A link to `#/audit?…` opened while already on the audit page does not re-read the address | low | The page reads the address when it opens; a hash listener is a small follow-up |
| The release review's other findings | n/a | Only its verdict line (the Cloud Build blocker above) reached the finisher; its notes are in the review's scratch folder |

## Release readiness (2026-11-18, morning)

The release round's audit confirmed 117 findings across eight lenses (4 blockers, 26 high). Every blocker and high is fixed
and has a test; a final pass rechecked each one in the code. What follows is what changed, what is still open, the defaults
taken for you to confirm, and the steps to release.

### Checks

| Check | Result |
|---|---|
| `cd backend && .venv/bin/python -m pytest -q` | 1098 passed, 76 skipped (the skipped need Postgres) |
| Postgres suites, 13 files, on a scratch server | 143 passed |
| `.venv/bin/ruff check .` | clean |
| `MODE=demo .venv/bin/python -m eval.run_eval` | 10 of 10 targets met; self crew R@5 0.776 against 0.75, now that the working-status docs stay out of its corpus ([eval/README.md](../backend/eval/README.md#ci-gate)) |
| `npx tsc --noEmit -p .`, `npx vite build` | clean; the sign-in screen loads 52.5 kB of JS and 70.9 kB of CSS (was 548 kB and 300 kB) |
| Docker images | ww-api 549 MB, runs as uid 10001; ww-web 205 MB, nginx 1.30.6 as uid 101, gzip, full security headers |
| Browser pass, every role, light and dark | 34 pages: no console errors but the deliberate missing-review 404, no layout shift, no sideways scroll, no third-party requests; axe 0 violations (was 294) |
| Migrations | 001–017 |

Not checked: a real Cloud Run deploy (the Google Cloud fixes are proven by code and tests only), Terraform `plan`, and the
Gemini model ids on Vertex AI.

### Fixed, by area

- **Security and config.** Model ids that exist (DEPS-1). A self-signed-up organisation can no longer read the server's
  private repositories (AUTH-1, INPUT-03). Webhooks refuse public default secrets (AUTH-2, INPUT-01, OPS-03, E2E-9). Sign-in
  survives a Google certificate hiccup (REL-07, AUTH-3). OAuth state is bound to the browser (AUTH-4, INPUT-08). Rate limits
  use an address the client cannot choose (INPUT-05, OPS-04, AUTH-9). Body size limits, limits on agent routes, stricter
  startup checks, demo and membership edge cases (INPUT-06, INPUT-13, OPS-14, REL-16, E2E-5, E2E-13, AUTH-6 to AUTH-8,
  AUTH-10, INPUT-10, INPUT-12).
- **Reliability and data.** Crashing jobs give up; one bad repository no longer stops the watcher; repository size caps;
  timeouts on every database connection; no half-written claims or re-indexes (REL-03 to REL-06, REL-09, REL-10, REL-12,
  REL-13, REL-15, REL-17, REL-19, E2E-12). Migrations take a lock and checksums, and an older build cannot deploy last (REL-14).
- **Privacy.** KT notes are redacted in every field (PRIV-2). Deleting a document deletes its file (PRIV-3). The audit log is
  append-only for the app (PRIV-7, AUTH-5). No question text in database errors or logs (PRIV-1, OPS-09). The export leaves out
  index copies; a private review's record is disclosed to the hire; document parsing has limits; Calendly bookings are
  attributed safely (PRIV-11, PRIV-13, INPUT-11, INPUT-09). A person can
  be erased, a recording removed, and the hire's visibility page says who reads what (PRIV-8, PRIV-9, PRIV-4).
  A recording is taken in only with a recorded consent confirmation, invitations carry the recording notice, and anyone on
  the project can ask a manager to remove one (`app/services/recordings.py`). Self-hosted, `RECORDING_RETENTION_DAYS`
  removes raw recordings after that many days (0, the default, keeps them) and keeps the notes (PRIV-9). The copy of a
  recording uploaded to the Gemini Files API is deleted when its analysis finishes or fails (PRIV-5).
- **Self-hosted release.** `./scripts/selfhost.sh release` serves the production images behind nginx with the full headers,
  restarts them, answers only to its own host names, and has backup and restore (E2E-1, FE-03, INPUT-04, OPS-01, REL-01, OPS-05,
  PRIV-14, OPS-15, OPS-12, OPS-16, OPS-21, FE-10).
- **Supply chain.** pypdf 6.21.4, certifi 2026.10.23, nginx 1.30.6, cloud-sql-proxy 2.28.0 by digest; npm from the public
  registry; CI audits block; pinned actions and Dependabot (DEPS-2 to DEPS-5, DEPS-7, DEPS-11, OPS-13, FE-05).
- **Google Cloud.** Recordings upload straight to Cloud Storage, and Drive imports stream there (INPUT-02, OPS-02, REL-02,
  E2E-4, FE-04). Email reaches the workers (E2E-8, OPS-06). Alerts on stuck queues and worker errors (OPS-07). Credential URLs
  kept out of platform logs (OPS-08). The load balancer cannot be bypassed (OPS-10). Room for live streams (OPS-11, REL-08,
  E2E-14). Workers keep their replicas across deploys (OPS-20). Docs and Terraform format (OPS-18), structured logs (OPS-17).
- **Web app.** Signing out clears the previous person's data (FE-01). Hidden tabs give up their live connection (FE-02).
  Contrast passes WCAG AA (FE-08). Fonts are served by the app (DEPS-9, PRIV-10, FE-13). Pages load on demand (FE-11). Unknown
  routes say so (FE-14). Accessible names (FE-15). Safe links (INPUT-07). Notification links (E2E-10). Live stream timeout (FE-09). Crash reports reach the server: `POST /api/client-errors` takes
  them without a sign-in, 10 a minute per address and 16 KB at most, and logs one redacted line without storing it (FE-07).
- **Product.** Settings can start a program for an existing onboarding (E2E-3), manage teams (E2E-6), set a GitHub username
  (E2E-7) and invite an executive (E2E-11).
- **The Guide.** An honest answer when the AI is off, and "Ask a person" always works (E2E-2). One consistent label for a
  quoted answer, code shown as code, real timings (UI-1 to UI-3, UI-5 to UI-11). Readable trace lines (UI-4). Request paths
  have a time budget (REL-11). Answered: demo 0.47 → 0.65, unseen 0.47 → 0.64, with every demo unanswerable still refused
  ([specs/rag-crew.md](specs/rag-crew.md#measured)).
  Without the Supervisor's model (offline, or every attempt failed), Ask sends a question about the code, documents or KT to
  the Guide; only an explicit request for a plan, a first task or coaching goes to the Planner, Scout or Coach (Q1;
  `orchestrator.rule_route`, `tests/test_release_routing.py`). The 47 fresh questions: 45 → 47 to the Guide, requests unchanged.
- **Scrolling.** Native scrolling in the app and the landing; no style work at rest; landing clips play at 1x while on
  screen. Desktop frames at 4x CPU: p95 50–66 ms → 33 ms, long tasks gone.

### Still open

None is a blocker or high, from the release audit or the Guide review (Q1 is fixed, above).

| Id | Severity | What | Why it is open |
|---|---|---|---|
| UI-1 | medium | The Agents page says "AI writes the wording" when only the offline writer runs (`registry.ai_available`) | `registry.py` was outside the fixer's files |
| FE-02 | medium | Hidden tabs give up their stream; several visible tabs over HTTP/1.1 can still use up the browser's connections | Leader election across tabs is design work |
| PRIV-6 | medium | Pre-boarding links carry their token in the URL. Our logs and Google Cloud's are redacted (OPS-08) | Moving it to the URL fragment changes the portal |
| DEPS-6 | medium | Development runs Vite 5's dev server (never used to serve people's work) | Vite 6 is a major bump and needs your approval |
| DEPS-8 | medium | CI audits block, but Cloud Build has no pip-audit, npm audit or image scan | Not added to `cloudbuild.yaml` |
| F1 | medium | On the free tier, indexing can use the day's 1,000 embedded texts, and questions then fail until midnight Pacific | Needs a daily reserve for questions; billing avoids it |
| F2 | medium | The embedder trusts the number of vectors it gets back; `gemini-embedding-2` merges a batch into one | Not fixed in `embeddings.py`; the configured `gemini-embedding-001` batches correctly |
| F3 | medium | The 0.55 similarity gate is not calibrated for real embeddings | Measuring `gemini-embedding-001` waits on the daily quota |
| F4, F5 | medium | On the free tier the fallback chain can take 50 s; one live false refusal under time pressure | A paid tier shortens both; prompt and budget changes not made |
| U2 | medium | One unseen near-miss ("default timeout for an endpoint") is answered | No general offline signal separates it without losing answerable questions |
| Q6 | medium | CODEOWNERS is not indexed, so "who owns X" is refused offline | Ingestion and the people tools were outside this round's files |
| D3 | low | Live, the Guide missed an `if False` guard in the demo webhook | Needs a sentence in `GUIDE_VERIFY` |
| OPS-19 | low | Cloud SQL has a public IP (IAM connector required, no authorised networks) | A VPC migration that cannot be checked without a project |
| OPS-17 | low | No distributed tracing | OpenTelemetry needs new dependencies |
| REL-18 | low | Program and business days use the server's local date; no time zone per organisation | Needs a migration and a setting |
| DEPS-10, DEPS-12 | low | No `--require-hashes`; no `.terraform.lock.hcl`, and Terraform checks run in Cloud Build only | Hashes need your decision; the lock needs a real `terraform init` |
| FE-12 | low | A recording's signed link expires after 60 minutes and is not refreshed | `Meetings.tsx` was outside the fixer's files |
| INPUT-07 | low | The web app renders only http(s) PR links, but the server does not check `pr_url` | Server half not done |
| E2E-14 | low | Live steps rely on session affinity across instances | The durable fix (Postgres NOTIFY in `app/live.py`) is not built |
| — | low | `migrate.verify()` does not check that definer functions belong to `ww_definer` (restore does) | Small follow-up in `migrate.py` |
| — | low | The landing's cover costs about 165 ms/s of style work at 4x CPU on phones | Grain and blur savings may change the look |
| F7, F8, F10 | low | A golden-set gap, a half-true docstring in the sample repo, a quota message that talks about indexing | Small follow-ups |

Also not done: the old `POST /projects/{id}/onboardings` still creates onboardings without a program (Enrol adopts them);
`mailing.invitation` acknowledges an event it cannot send when the worker has no mailer; `docs/USER_GUIDE.md` still says
export and deletion are API only, though they are on **Settings > Organisation** (PRIV-12; SECURITY and ADMIN_GUIDE agree
with the code).

### Defaults taken, for you to confirm

| Default | Choice | Alternative |
|---|---|---|
| Intro | Plays its 5 s on every reload, skippable by click, key or scroll | Play once per session |
| Quiet text | A new `--ink-muted` (#707070 light, #808080 dark) to pass WCAG AA | Keep the lighter grey and fail AA |
| Fonts | Geist and Geist Mono served by the app (OFL licence in `frontend/public/fonts`) | Google Fonts, a third party |
| Answer without a model | Labelled "Quoted from the sources"; "Why this?" says no AI; questions default to "Ask a person" when the AI is off | Hide offline answers |
| Erasing a person | Their Identity Platform or Firebase login stays (one login can serve several organisations) | Delete it automatically |
| Rate-limit address on Google Cloud | `RATE_LIMIT_TRUSTED_HOPS=2`; check with DEPLOY.md after the first deploy | Set it per trigger |
| Model ids | `gemini-3.5-flash-lite` (fast), `gemini-3.5-flash` (smart), then `gemini-3.1-flash-lite`, `gemini-2.5-flash` | Run `scripts.check_models` and set your own |
| Model check in Cloud Build | Not run (the build account has no Vertex AI rights) | Grant `roles/aiplatform.user` and add the step |
| Eval targets | Raised to the new measured values minus 0.03 | Keep the old targets |
| Old onboarding route | Kept; Enrol adopts what it creates | Retire it (410) once its tests move |

### Release checklist

**Self-hosted** ([ADMIN_GUIDE.md §15](ADMIN_GUIDE.md#15-self-hosted-release)):

1. A Gemini key from a project with billing on. The free tier may use what is sent, and embeds 1,000 texts a day.
2. `./scripts/selfhost.sh setup`, then in `backend/selfhost.env`: `PUBLIC_URL` (the HTTPS address; `http://localhost:8000` on
   one machine, not `:5173`), `FIREBASE_PROJECT_ID`, `FIREBASE_API_KEY`, `GEMINI_API_KEY`; optional SMTP, GitHub token,
   Google OAuth, Calendly ([INTEGRATIONS.md](INTEGRATIONS.md)).
3. Firebase **Authorized domains**: add the host of `PUBLIC_URL`.
4. `./scripts/selfhost.sh release`, then `./scripts/selfhost.sh check`.
5. An HTTPS reverse proxy in front; `WW_WEB_HOSTS` for any other host name.
6. `./scripts/selfhost.sh backup` once, and keep the file off the machine; rehearse `restore` on another machine.
7. Open `PUBLIC_URL`, sign in, create the organisation, connect a repository, and ask one question.

**Google Cloud** ([DEPLOY.md](DEPLOY.md)):

1. `terraform apply` (steps 0–1), third-party setup and secrets (steps 2–3), cluster bootstrap (step 4).
2. Fill `build_substitutions` in `<env>.tfvars`, including `_SMTP_*` if email is wanted, and connect the trigger (step 5).
3. Run `python -m scripts.check_models` against the project and set the model ids it confirms (step 6).
4. Push to `main`: tests, the eval gate, Terraform checks, then the deploy.
5. Set `api_host` and `web_host`, apply again, and check that the run.app URLs no longer answer.
6. Check the rate-limit address (DEPLOY.md), upload one recording over 32 MiB, and sign in (step 7).
7. Watch the alerts for stuck queues and worker errors for the first day.

### Behind flags and settings

| Switch | Default | Covers |
|---|---|---|
| `FEATURES` | `all` | `preboarding`, `routing`, `first_task`, `presubmit`, `blockers`, `areas`, `summaries`, `feedback`, `checkins`, `surveys`, `completion`, `handover`, `reports`, `demo_clock`. Off: the routes answer 404. Not flags: enrolment, programs, homes, visibility. |
| `MODE=demo` | `demo` | `/api/demo/*`, `/api/docs` and `/api/openapi.json` exist only in demo mode |
| `LANGSMITH_TRACING` | off | Agent traces to LangSmith |
| `GOOGLE_OAUTH_CLIENT_ID` + `_SECRET` | empty (off) | Google Calendar, Meet links, Drive imports |
| `CALENDLY_WEBHOOK_SIGNING_KEY` | empty (off) | Calendly bookings coming back |
| `GITHUB_APP_*` / `GITHUB_TOKEN` | empty | GitHub App (Google Cloud) / private repositories by URL (self-hosted) |
| `GEMINI_API_KEY` | empty | Self-hosted AI; without it the app runs and says the AI is off |
| `SIGNUP_ENABLED`, `REQUIRE_VERIFIED_EMAIL` | on | Self-serve sign-up; verified email to join |
| `RATE_LIMIT_ENABLED` | on | All rate limits |

### Deferred, and why

| Item | State in the code | Why |
|---|---|---|
| SSO (SAML, OIDC with a customer's identity provider) and SCIM | Not built; sign-in is Identity Platform email or Google | Deferred by the founders; needs a customer to integrate against |
| Public API and scoped API tokens | `api_tokens` table only (migration 010); no route issues or accepts a token | Second in PLAN §6's deferral order |
| Slack, Teams, Jira, Linear, HRIS | Not built; notifications are in-app and email | PLAN §1 decision 4 |
| Frontend tests in the repository | None; the browser checks above ran outside the repository | New dependencies need the founders' approval |
| Speaker identification in recordings | Speakers are matched to members by name or GitHub login only | Needs a voice or attendee approach |
| Enforced MFA | Not built | Planned in SECURITY.md |

### Known product gaps

Carried from 2026-11-14 and not fixed since.

| Gap | Where |
|---|---|
| Approving a review ticks "First change merged" before GitHub's merge event | `progress.py` `first_pr_merged` |
| The AI draft sent with an escalation to the domain owner is stored in a metric event only; the owner does not see it | `routing.py` `hand_to` |
| After the domain owner answers, the mentor's copy of the question stays open | routing escalation |
| A new pre-boarding link for Arjun in the demo replaces `/welcome/demo-welcome-arjun` until restart | `seed.py` |

## History: enterprise round 1 (2026-11-14)

### At a glance, 2026-11-14

| Check | Result | Baseline (start of round) |
|---|---|---|
| `cd backend && .venv/bin/python -m pytest -q` | **488 passed, 53 skipped** (541 collected, about 45 s). The 53 skipped need Postgres (`TEST_PG_ADMIN_URL`). | 341 passed, 43 skipped |
| `MODE=demo .venv/bin/python -m eval.run_eval` | recall@5 **1.00** (target 0.80) on 17 questions; answer metrics need a real model | the same 17 questions |
| `cd frontend && npx tsc --noEmit -p .` | passes | passes |
| Frontend tests | none (tooling awaits approval) | none |
| HTTP routes (demo mode, `/api/openapi.json`) | 130, of which 5 are demo-only | `routes.py` held 50 |
| Migrations | 001–013 (`010_lifecycle`, `011_scheduling`, `012_audit_retention`, `013_members` are new) | 001–009 |

`docs/PLAN.md` names Phase 0 and "Phases 1 to 5" without listing them one by one. This page maps them to the round's tracks,
plus Phase 6 for documentation; correct the mapping if the master prompt numbers them differently. Test counts are the test
files that cover each phase (many files were changed this round, so these are not "new tests" counts).

### What landed, by phase

#### Phase 0: plan and baseline

- [PLAN.md](PLAN.md): decisions taken by default, track ownership, data model, API contract, the ranking and detection rules.
- [CURRENT_STATE.md](CURRENT_STATE.md): the baseline assessment.

#### Phase 1: foundations (Track F)

| Landed | Where |
|---|---|
| Repository rules for agents and people | `CLAUDE.md` |
| Packaging from git-known files only, refusing secrets | `scripts/package.sh` |
| CI on GitHub Actions: in-memory tests plus the retrieval eval and a check that tests left nothing in `backend/`; Postgres tests (fail if skipped); frontend type-check, build and a CSP check of the bundle; dependency audit (report only); both Docker images | `.github/workflows/ci.yml` |
| Content-Security-Policy and security headers for the web app and the API | `frontend/nginx.conf.template`, `app/main.py` |
| Rate limits per person and per address, seven groups | `app/ratelimit.py` |
| Audit-log API, CSV export, retention purge (30-day floor in the database) | `app/api/audit.py`, `adapters/audit_store.py`, `012_audit_retention.sql`, Terraform job `ww-audit-purge` |
| Prompt-injection fixtures (README, PR description, diff comment, KT transcript) | `tests/fixtures/injection/` |
| Tests write into a temp folder, never `backend/` | `tests/conftest.py` |
| LangSmith tracing opt-in only | `app/container.py configure_tracing`, `cloudbuild.yaml` |
| Demo mode ignores `backend/.env` for the main settings | `app/config.py` (two exceptions under "Known issues") |
| Security overview | `docs/SECURITY.md` |

Tests: `test_ratelimit` 19, `test_injection` 13, `test_audit` 9 (1 needs Postgres), `test_config` 4.

#### Phase 2: landing page (Track A)

- A 3D office scroll story in six chapters (`frontend/src/site/Story.tsx`, `site/office/*`), with three.js loaded only after the
  hero has painted and never in the app's bundle; without WebGL a drawn still with the same words.
- Less text; a "Ready for your security review" grid. The landing page no longer claims SSO or SCIM: both are deferred (below).

Tests: none beyond the type-check and CI's CSP check of the build. Lighthouse has not been measured.

#### Phase 3: scheduling (Track B)

- LiveKit is gone (code, dependencies, infrastructure). KT sessions are booked on Google Calendar with a Meet link, through a
  person's Calendly link, or held anywhere (`app/services/scheduling.py`, `adapters/google_calendar.py`, `adapters/calendly.py`,
  `app/api/integrations.py`, `011_scheduling.sql`).
- Recordings come back from Google Drive (button and poller; `POST /internal/recordings/poll`, Terraform job
  `ww-recordings-poll`) or by upload. The demo uses a built-in calendar that always "finds" a recording.
- Google refresh tokens are stored encrypted (Fernet, `TOKEN_ENCRYPTION_KEY`); the OAuth state is signed and expires in 10
  minutes. Calendly webhooks are signature-checked.
- Settings > Integrations; booking from the KT planner and KT sessions pages.

Tests: `test_scheduling` 31.

#### Phase 4: lifecycle API (Track C)

- Programs and versioned templates (16 defaults), teams, one-step enrolment with a drafted plan through Approvals, milestones
  that scale with the program's length, access checklists, pre-boarding links (hashed tokens, 14 days) with acknowledgements
  and an environment-check script.
- Tiered answer routing (cited answer, verified answers, mentor, domain owner) with interruption budgets, digests and
  office hours; verified answers that later answers reuse.
- The first-task shortlist with scored reasons and exclusion of single-owner critical areas; private pre-submit reviews.
- Progress by area, blocker rules with escalation (hire, mentor, manager), weekly summaries counted from records,
  documentation feedback routed to owners, 30/60/90 check-ins, an anonymous survey with a floor of five, completion review,
  offboarding handovers.
- The visibility page and its registry (managers and mentors see exactly what the hire sees), role homes, setup, the
  leadership report with a "how" for every figure and "not enough data" floors.
- The `executive` role (read-only by construction), members administration (role changes, deactivation;
  `013_members.sql`, `app/api/members.py`), organisation export and deletion, the demo clock, and feature flags.
- `docs/PERMISSIONS.md` (every route × role) and `docs/specs/` (one spec per capability).

Tests: `test_lifecycle_api` 41, `test_lifecycle_rules` 11, `test_lifecycle_postgres` 6 (need Postgres), `test_visibility` 5,
`test_members` 4, `test_permissions_matrix` 3 (one of them calls every route of the app as each demo user).

#### Phase 5: lifecycle UI (Track D)

- Role homes (`#/home`) for all five roles; every role now lands there.
- Setup, Enrol, Team, the per-hire Program page (timeline plus accordion sections), the pre-boarding portal (`/welcome/<link>`,
  its own chunk), What your manager sees, Reports with CSV export, Templates, Handovers, People, Audit log, Feedback, expert
  Preferences, the demo clock control.
- Rail per role with `g` shortcuts; ⌘K actions (enrol, pre-boarding link, export report, demo clock, settings).

Tests: none; `tsc` passes.

#### Phase 6: documentation and this checkpoint

- New: [USER_GUIDE.md](USER_GUIDE.md), [ADMIN_GUIDE.md](ADMIN_GUIDE.md), [API.md](API.md), [DEMO_SCRIPT.md](DEMO_SCRIPT.md) (verified
  against the demo API, with its gaps), this page; README updated.
- Landing this round, not verified here: the multi-agent restructure (one Supervisor and eight named specialists, an Agents
  page, `GET /api/agents`, approval kinds `rerecord`, `doc_edit`, `template_change`), documented in [AGENTS.md](AGENTS.md) by its
  own track.

### Update — 2026-11-14 (late): agentic model, live-action landing, real-mode verification

- **Agentic RAG multi-agent model.** Every specialist (Guide, Scout, Planner, Coach, Keeper, Reviewer, plus the
  Supervisor's free-form router) runs a LangGraph think → tool → observe loop on Gemini function calling over read-only
  tools (`app/agents/runtime.py`, `app/agents/tools/`). Rules remain only as guardrails and as the no-AI fallback.
  Steps stream live (`agent:<run_id>` over SSE) and are kept privacy-filtered in run records. See docs/AGENTS.md.
- **Live-action landing** (docs/landing/CINEMATIC_BLUEPRINT.md): six scenes of real footage (stock until a custom shoot —
  docs/landing/PRODUCTION.md), scroll scrubbing, real React product scenes, adaptive dark → light → dark. three.js removed.
- **Real mode verified end to end** (self-hosted, real Firebase, Gemini free tier, GitHub token, public repo
  VijayabaskarR-06/bhume-boundary-takehome): sign-up, indexing (paced under the free tier's 100 embeddings/min), agentic
  answers with live steps, the citation guard, the off-topic refusal, organisation deletion. Fixed on the way:
  Firebase token verification without Google Cloud credentials (`app/firebase_tokens.py`), embedding pacing and 429 waits,
  a citation check calibrated on real answers (+ one repair turn), install/build files indexed (requirements*.txt, *.toml,
  Dockerfile, Makefile), a fallback-model chain (`MODEL_FALLBACK` comma-separated) with one retry on a 503.
- **Tests:** 677 passed, 60 skipped (the 60 need Postgres; earlier Postgres runs passed). Frontend type-check and build pass.
- **Known limits:** on the Gemini free tier answers take ~10–50 s and Pro models are unavailable (billing fixes both);
  footage is licensed stock (Pexels, cottonbro studio) until a custom shoot; the landing is checked in Chrome only
  (Safari/Firefox/iOS and Lighthouse not yet measured); public/media is 54 MB (serve from Cloud Storage/CDN in production);
  live steps stream in-process (enable session affinity on Cloud Run with several instances).

### Update — 2026-11-14 (night): brand and film
- **New identity** (docs/brand/BRAND.md): the W of four identical capsules with hairline seams ("explain once, reused
  everywhere"), Geist wordmark, lockups, favicon/app icons; the old spiral is removed everywhere (incl. the hero's full stop,
  now one teal capsule). New interactive intro ("Crowd"): strokes draw, a ripple runs through a stroke field that leans toward
  the pointer; skippable; reduced-motion still.
- **Landing as one continuous film**: smoothed scroll progress drives every shot (all clips scrubbed), motivated transitions
  (push-in match cuts, moving dissolves), layered captions; scenes 4–6 are a black CSS-3D product world with teal threads routed
  under the panels; 60 fps measured in Chrome.
- Checks: 677 backend tests pass; frontend type-check and build pass; a 36-page, 5-role browser sweep shows no console errors.
