# Welcome Wave — build plan (enterprise edition, round 1)

Status: Phase 0 written 2026-11-14. The founders asked to proceed without waiting, so the open questions below were
answered with defaults. Each default is reversible; change any of them and the affected track is re-planned.

## 1. Decisions taken (override any)

| # | Question (master prompt §14) | Default taken |
|---|---|---|
| 1 | Email provider | No provider chosen. A `Mailer` port: SMTP (Python stdlib) for self-hosted and GCP, an in-memory mailer in demo and tests. Built in the final review round (see section 7). |
| 2 | Error tracking, frontend test tooling | No error tracker added; structured JSON logs with request ids (already there). Vitest, Playwright and axe are **not** added to the project until approved; QA screenshots use scratchpad tooling. |
| 3 | Executive: role or permission | A separate read-only role, `executive`. |
| 4 | Integrations at launch | **Google Calendar (with Google Meet) and Calendly**, replacing LiveKit (founders' decision, 2026-11-14). Slack/Teams, Jira/Linear, HRIS: deferred. |
| 5 | Program duration | 90 days by default, set per template (milestones at 30/60/90 scale with it). |
| 6 | Who may view recordings | Project members only. |
| 7 | Billing and seats | Out of scope. The admin home shows the seat count only. |
| 8 | Deadline | Unknown. Order: landing page and never-defer items first. |

New dependencies: **three.js** (the landing page's 3D story; loaded lazily, never in the app bundle). It was approved by the
request for 3D rendering. The alternative considered was hand-written WebGL: more code, a worse result. **Removed:**
`livekit-api`, `livekit-client`, `@livekit/components-react`, `@livekit/components-styles`.

Commits: none made. The repository has no commits yet; the first commit is the founders' call.

## 2. Round 1 tracks and file ownership

The tracks run in parallel, so each owns its files. A track never edits another track's files.

| Track | Scope | Owns |
|---|---|---|
| A Landing | 3D office scroll story, enterprise narrative, less text | `frontend/src/site/**` |
| B Scheduling | LiveKit out; Google Calendar + Meet and Calendly in; recordings from Drive or upload | meeting routes in `api/routes.py`, `api/webhooks.py`, `services/livekit.py` → `services/scheduling.py`, `adapters/google_calendar.py`, `adapters/calendly.py`, `config.py`, `selfhost.py`, `kt_planner.py` booking, `011_scheduling.sql`, `Meeting` in `ports.py`, the meeting parts of `memory.py`/`postgres.py`, `pages/Meetings.tsx`, `pages/Call.tsx`, KT booking in `pages/KtPlanner.tsx`, `frontend/src/api.ts`, `vite.config.ts`, LiveKit lines in `package.json`, `infra/**`, `scripts/selfhost.sh`, `docker-compose.yml`, `cloudbuild.yaml`, tests that mention LiveKit |
| C Lifecycle API | Programs, enrolment, plans, pre-boarding, routing, first contribution, progress, blockers, summaries, feedback, check-ins, completion, handover, visibility, reporting, demo clock, permission matrix | `010_lifecycle.sql`, new `app/lifecycle/**`, `api/lifecycle.py`, `api/demo.py`, `container.py`, appended protocols in `ports.py`, `seed.py` (append), `docs/specs/**`, `docs/PERMISSIONS.md`, new tests |
| D Lifecycle UI | Role homes, setup, enrolment, timeline, portal, visibility, reports, decluttered pages | after C: `frontend/src/lifecycle/**`, `Workspace.tsx`, `CommandPalette.tsx`, app pages not owned by B |
| E Current state | `docs/CURRENT_STATE.md` | that file only |
| F Foundations | Repo hygiene, `CLAUDE.md`, packaging, CSP, rate limits, audit-log API, injection fixtures, CI, `SECURITY.md` | `CLAUDE.md`, `scripts/package.sh`, `.github/**`, `frontend/nginx.conf.template`, `api/audit.py`, middleware in `main.py`, `tests/conftest.py` (temp dirs), `tests/fixtures/injection/**`, `docs/SECURITY.md` |

`main.py` is shared by C (one `include_router` line) and F (middleware); both make small, separate edits.

## 3. Data model — migration `010_lifecycle.sql`

All tables are `org_id`-scoped under forced row-level security, sit behind `ports.py` (`LifecycleStore`), have a
memory adapter and demo data, and are covered by organisation export and deletion.

- `teams` (id, org_id, project_id, name)
- `program_templates` (id, org_id, key, version, role_track, level, duration_days, milestones jsonb, acknowledgements jsonb, access_items jsonb, active); a change makes a new version.
- `onboarding_programs`, 1:1 with `onboardings`: mentor_id, team_id, focus_area, template_id + template_version, duration_days, preboarding_token_hash, preboarding_expires_at, access_granted_at, completed_at, completion jsonb. A side table keeps the agents' `Onboarding` model stable.
- `onboarding_milestones` (onboarding_id, key, title, due_day, status, done_at, evidence jsonb)
- `access_items` (onboarding_id, system, owner_id, status requested/granted/not_needed, granted_at)
- `acknowledgements` (onboarding_id, doc_key, title, acknowledged_at, ip_hash)
- `environment_checks` (onboarding_id, ran_at, ok, results jsonb)
- `area_progress` (onboarding_id, area, evidence jsonb, self_level)
- `blocker_alerts` (onboarding_id, rule, evidence jsonb, stage hire/mentor/manager, opened_at, resolved_at, resolved_by)
- `documentation_feedback` (project_id, target_kind, target_ref, kind unclear/outdated, note, owner_id, status, created_by)
- `verified_answers` (project_id, question, answer, citations jsonb, verified_by, source_request_id, reuse_count)
- `checkin_reviews` (onboarding_id, day 30/60/90, author_role, answers jsonb)
- `survey_responses` (org_id, team_id, cohort, day, answers jsonb): no person id, reported only in aggregate
- `progress_summaries` (onboarding_id, week_start, body jsonb with citations)
- `handovers` (org_id, project_id, user_id, last_day, plan jsonb, status)
- `expert_preferences` (user_id, max_interruptions_per_day, office_hours jsonb, digest)

Track B's `011_scheduling.sql` adds: meeting scheduling columns (provider, join_url, calendar_event_id, starts_at, ends_at,
attendees), `calendar_connections` (org-level Google OAuth, encrypted refresh token) and `scheduling_links` (user_id,
provider, url: a person's Calendly link).
- `api_tokens` (org_id, name, scopes, token_hash, created_by, last_used_at, revoked_at)
- `metric_events` (org_id, project_id, kind, subject_id, at, props jsonb)
- `users.role` also allows `executive`.

## 4. API contract (C implements, D consumes; all under `/api`)

| Route | Who | Purpose |
|---|---|---|
| `GET /home` | any | Role home: `{role, focus: [...actions], figure: {label, value, how}, sections: {...}}` |
| `GET/PATCH /setup` | admin | Setup steps with status; complete or skip a step |
| `GET/POST/PATCH /teams` | admin, manager | Teams |
| `GET /programs/templates`, `POST`, `PATCH /{id}` (new version) | manager reads, admin writes | Program templates |
| `POST /onboardings/enrol` | manager, admin | Invitation + program + drafted 30/60/90 plan → approval |
| `GET /onboardings/{id}/program` | the hire, their mentor, managers | Timeline: milestones, plan items, access, acknowledgements, environment checks |
| `PATCH /onboardings/{id}/milestones/{key}` | manager | Milestone status |
| `GET/POST/PATCH /onboardings/{id}/access` | manager, item owner | Access checklist |
| `POST /onboardings/{id}/preboarding-link` | manager | Expiring, scoped link (token returned once) |
| `GET /preboarding/{token}`, `POST …/acknowledge`, `GET …/env-check.sh`, `POST …/env-check` | link holder, no sign-in | Pre-boarding portal |
| `GET /onboardings/{id}/first-task/shortlist`, `POST …/first-task` | manager | Proposed first tasks with reasons; selection makes the task brief |
| `POST /tasks/{id}/presubmit-review` | the hire only | Private review of their branch, never shown to the manager |
| `POST /ask/route` | any member | Tiered routing of a question (§5.2) |
| `POST /verified-answers`, `GET /verified-answers` | expert, manager | Verify an answer; reused by later answers |
| `GET /onboardings/{id}/areas`, `PATCH …/areas/{area}` (self-assessment) | hire, mentor, manager | Progress by area |
| `GET /onboardings/{id}/blockers`, `POST /blockers/{id}/resolve` | hire, mentor, manager | Blocker alerts with evidence |
| `GET /onboardings/{id}/summaries`, `POST …/summaries` | hire, manager | Weekly progress summaries |
| `POST /feedback`, `GET /feedback` | members; owners see their queue | Documentation feedback |
| `GET/POST /onboardings/{id}/checkins` | hire, manager | 30/60/90 check-ins |
| `POST /surveys`, `GET /surveys/summary` | hire submits; managers see aggregates (n ≥ 5) | Onboarding experience survey |
| `GET/POST /onboardings/{id}/completion` | manager signs off | Completion review |
| `GET/POST /handovers`, `PATCH /handovers/{id}` | manager, admin | Offboarding handover plan |
| `GET /onboardings/{id}/visibility` | the hire, and managers | Every manager-visible data point about the hire |
| `GET /reports/onboarding`, `GET /reports/onboarding.csv` | manager, admin, executive | Leadership report with a "how" for every figure |
| `POST /demo/advance` | demo only | Move the seeded organisation through days 0–90 |
| `GET /audit`, `GET /audit.csv` | admin | Audit log (Track F) |

## 5. Ranking, routing and detection logic (proposed, for your approval)

Each rule is documented in `docs/specs/` and returned with its result as `why: [..]`, so the UI can show its reasons.

**First-task shortlist.** The candidates are open issues labelled for newcomers, plus agent-proposed code tasks in the focus area.

- Score = 0.30 in focus area + 0.25 explained (the share of related files with KT links) + 0.20 small (≤ 3 files) + 0.15 tested (related tests exist) + 0.10 quiet (no commits by others in 14 days).
- Files in a single-owner critical area are excluded.
- The top 3 are shown, and every factor that scored is listed as a reason.

**Tiered answer routing.**

| Tier | When it applies | What happens |
|---|---|---|
| 1. AI answer | Retrieval confidence ≥ 0.55 and the citation check passes: every claim maps to a cited source | The answer is shown with its citations. Otherwise: "not enough evidence", with escalation offered. |
| 2. Related answered questions | A similar question (≥ 0.80) has a verified answer or an answered expert request | Shown de-identified |
| 3. Mentor | The AI tier is insufficient, or the hire asks for a person | The question goes to the mentor |
| 4. Domain owner | Escalated past the mentor | Owner = the CODEOWNERS of the cited paths, else whoever explained them most recently. The AI draft is attached. |

Expert time: questions that are not urgent go into the owner's daily digest or office hours. Each person gets at most 3 interruptions a day by default. The response target is 24 h per organisation.

**Knowledge gaps.** A question becomes a gap when any of these hold:

- the answer's confidence is below 0.55;
- it got a thumbs-down;
- it was escalated;
- the same normalised question was asked 3 or more times by 2 or more people within 30 days.

**Blocker detection.** Each rule carries its evidence:

- the same topic (top cited module) asked about 3 or more times in 5 days;
- a task in progress with no activity for 3 business days;
- 3 or more failed CI runs on the hire's pull request within 48 h;
- an environment check failing for more than 24 h;
- review findings unresolved for more than 3 days.

Escalation: the hire on day 0, the mentor after 1 unresolved business day, the manager after 2 more.

**Progress by area.** Evidence per area: path steps done, explanations watched, questions resolved, merged files. The levels are *not started*, *exploring* (one kind of evidence), *working* (two or more kinds) and *contributed* (a merged change). There are no scores and no ranking.

**Possibly outdated explanation.** At least 30% of the linked chunk's lines have changed since the recording, or the linked symbol is gone.

**Senior time saved (estimate).** (Answers resolved without a senior + reuses of explanations and verified answers) × minutes per avoided interruption. The default is 15 minutes; admins can edit it, and the assumptions are shown next to the figure.

**Reporting floors.** Survey results are shown for a team and survey day in which 5 or more distinct people answered (and each question needs 5 answers too). Below that, and wherever data is too thin, the report says "not enough data" rather than showing 0. The same floors apply inside each team's figures.

## 6. Phase status after round 1

- **Phase 0:** this plan, plus `docs/CURRENT_STATE.md`.
- **Phases 1 to 5:** delivered as far as tracks A–F reach.
- **Rest:** `docs/STATUS.md`, written at the checkpoint, lists what landed, what is behind flags, and what moves to round 2. Deferral order, if needed: integrations beyond calendar → public API → knowledge search → outdated detection → surveys.

## 7. What was built in the final review round (backend)

A short record of the fixes that followed the independent review; details are in the files named.

- **Privacy.** The survey floor counts people per team and survey day, with one response per onboarding and day claimed atomically
  (`lifecycle/checkins.py`); the export holds floored survey aggregates only (`lifecycle/orgdata.py`). Executives get feedback counts,
  never a hire's note (`lifecycle/feedback.py`). The hire's visibility page lists the questions sent to a person, the audit trail of
  their actions, reviews of their pull requests and their feedback notes, with who reads each (`lifecycle/visibility.py`).
- **Deletion and logs.** `DELETE /api/org` also removes stored files, LangGraph checkpoints, outbox events and the demo's calendar and
  Calendly stores. Pre-boarding tokens, signed links and OAuth codes are redacted from every log line (`app/logsafe.py`). Portal
  actions are audited as the hire (`via: preboarding`).
- **Safety checks.** The migrator refuses tables without forced row-level security and a policy, with an explicit list of global
  tables (`app/migrate.py`); the permission-matrix tests also try every `own` row as an unrelated hire and expert.
- **Reports.** `GET /api/reports/onboarding` (and the CSV) groups figures by team, takes `team_id`, and counts single-owner critical
  areas for executives without names. The dashboard is a KT coverage view on the lifecycle's clock, with no per-hire activity.
- **Email.** A `Mailer` port with SMTP (stdlib `smtplib`; STARTTLS on 587, SSL on 465) and an in-memory mailer. Sent: the invitation after
  plan approval (an outbox event, ids only), the pre-boarding link when a manager asks (after the response, from memory, never
  stored), the expert digest (a count, no questions) and the weekly summary (from the daily tick). Nothing is sent without SMTP and
  `/api/config` says `email: false`.
- **Integrations.** `GET /api/integrations/status` (admins) and `./scripts/selfhost.sh check` test each integration against its real
  service without printing a secret (`services/integration_status.py`); `docs/INTEGRATIONS.md` has the click paths.
- **Housekeeping.** Verified answers saved from an expert's answer are one indexed source; the rate limiter and audit settings ignore
  `.env` in demo mode; `cloudbuild.yaml` deploys with `--update-env-vars`; the demo seed has questions, an expert request with a draft,
  a Calendly link and an audit history.

## 8. Track R: the Guide's research crew (multi-agent RAG), 2026-11-17

The Guide's answers depended on one search of the question as asked. Track R puts a crew in front of its writer: a Planner
(intent, the names the question gives, its parts, rewrites), four Retrievers (code, documents, KT calls, verified answers, in
one batched pass), Fusion, and a Judge (a lexical cross-scorer always; the model's 0–3 grades when a model can think). The
writer, VERIFY, the retrieval gate and the citation guard are unchanged and keep the last word. The contract is
[specs/rag-crew.md](specs/rag-crew.md). No new dependency.

| Part | Owns |
|---|---|
| Core | `backend/app/rag/**`; `Index.search_grid` in `ports.py`, `MemoryIndex` and `PgIndex`; the crew's schemas and prompts (appended); `rag_*` settings; `tests/test_rag_core.py`, `tests/test_rag_postgres.py` |
| Offline writer | `OfflineLLM` in `app/agents/llm.py`; `tests/test_offline_writer.py` |
| Integration | `agents/guide.py`, `agents/qa.py`, `lifecycle/routing.py`, `agents/tools/knowledge.py`, the Guide's crew in `agents/registry.py` and `api/agents.py`; `tests/test_rag_integration.py`, `tests/test_injection.py` |
| Evaluation | `backend/eval/rag_eval.py`, `eval/golden_demo.jsonl`, `eval/golden_self.jsonl`, `eval/README.md`, the CI gate in `eval/run_eval.py` |
| Web app | the Ask page (`pages/Chat.tsx`, `chat-parts.tsx`), traces (`lifecycle/Trace.tsx`, `agentModel.ts`), the Guide's crew on `lifecycle/Agents.tsx` |

## 9. Release round: scrolling and production readiness, 2026-11-17

Scrolling felt laggy. Both the app and the landing move to native scrolling, with compositor-only polish (transform, opacity and
clip-path). The landing stops intercepting the wheel and stops seeking video on scroll. Its clips play at natural speed while their
shot is on screen. This follows the user's earlier call that video scrubbing and custom wheel smoothing felt laggy on a real Mac.
The look does not change. A read-only audit across eight lenses checks release readiness: auth and tenancy, untrusted input,
reliability, operations, frontend, end-to-end completeness, privacy, and dependencies. Every finding goes to a verifier before
anything is fixed.

| Part | Owns |
|---|---|
| Scroll: app | `frontend/src/**` except `frontend/src/site/**` (style and performance changes only) |
| Scroll: landing | `frontend/src/site/**` (backup of the earlier version kept outside the repo until the user signs off) |
| Audit | nothing (read-only); fixes are assigned after verification |
**Status (2026-11-18): done.** The app and the landing scroll natively; landing clips play at 1x while on screen. The audit's
findings were verified, then fixed in section 11.


## 10. Track R, round 2: prove and harden, 2026-11-17

This round checks the Guide in three ways. The first is a codebase nobody tuned on: encode/starlette, cloned outside the repo,
with questions written by someone who never saw the ranker. The second is the real stack: Postgres, real Gemini embeddings and
the live model. The user approved about 300 model calls; the key is loaded into each command and never printed. The third is 45
new questions sent through the API, offline and live, each one judged by hand. An adversarial review across five lenses checks
the whole Track R change. Fixes follow general rules: tuned on dev, accepted only when holdout and unseen don't drop, and never
when an unanswerable question gets answered.

| Part | Owns |
|---|---|
| Unseen corpus | `backend/eval/golden_unseen.jsonl`, the unseen corpus in `eval/rag_eval.py` |
| Live stack | `backend/eval/live_eval.py` |
| Fix: retrieval core | `app/rag/**`, `search_grid` in `ports.py` and both adapters, `rag_*` settings, crew schemas/prompts, `GeminiLLM` and the `LLM` protocol, crew call accounting in `runtime.py`, `tests/test_rag_core.py`, `tests/test_rag_postgres.py` |
| Fix: answer path | `OfflineLLM`, `routing.py`, `guide.py`, `qa.py`, `tools/knowledge.py`, `registry.py`, `api/agents.py`, `seed.py`, their tests |
| Measure | the targets in `eval/run_eval.py` |
| Docs | `docs/specs/rag-crew.md`, `docs/AGENTS.md`, `docs/PROJECT_GUIDE.md`, `docs/DEMO_SCRIPT.md`, `backend/eval/README.md` |
**Status (2026-11-18): done.** Unseen corpus, live stack and 45-question checks ran; nDCG held or rose on holdout and unseen. Crew nDCG
offline: demo 0.88, self 0.58, unseen 0.70; the CI gate's targets were raised. Numbers: [specs/rag-crew.md](specs/rag-crew.md#measured).


## 11. Release round, 2026-11-17 (afternoon)

The read-only audit (section 9) confirmed 117 of 118 findings: 4 blockers, 26 high, 45 medium and 43 low, many of them the same
issue seen through different lenses. The earlier rounds were cut off by a usage limit; this round picks them up. Everything here
is fixed with tests. Each fixer returns a status for every finding id, and a final pass rechecks every blocker and high in the code.

| Part | Owns |
|---|---|
| Security + config | `config.py` (not `rag_*`), `api/routes.py` (not the recording upload/import), `webhooks.py`, `auth.py`, `firebase_tokens.py`, `integrations.py`, `ratelimit.py`, `main.py` (shared), `members.py`, `api/lifecycle.py`, `api/demo.py`, `lifecycle/reporting.py`, the env examples |
| Reliability + data | `selfhost.py`, `agents/ingestion.py`, `agents/meeting.py`, `services/embeddings.py`, `adapters/lifecycle_pg.py`, `postgres.py` connection setup, `services/scheduling.py`, `agents/supervisor.py`, `lifecycle/visibility.py`, `lifecycle/clock.py`, `services/redaction.py`, `migrate.py`, new migrations |
| Self-host release + supply chain | `scripts/selfhost.sh`, `scripts/package.sh`, the compose files, both Dockerfiles, `frontend/nginx.conf.template`, the requirements files, `frontend/package-lock.json`, `ci.yml`, `docs/ADMIN_GUIDE.md`, `docs/INTEGRATIONS.md`, `docs/SECURITY.md` |
| GCP | `adapters/gcp.py`, the new `api/uploads.py`, the recording upload/import, `workers/**`, `cloudbuild.yaml`, `infra/**`, `docs/DEPLOY.md` |
| Scroll | the app's `frontend/src/**` performance, then `frontend/src/site/**` (backups kept outside the repo) |
| Frontend production (after Scroll) | `frontend/src/**` except the landing, `pages/settings/**`, `lifecycle/Setup.tsx`, `pages/Meetings.tsx`; `index.html`; self-hosted fonts in `public/fonts` |
| Product completeness (after Scroll) | `pages/settings/**`, `lifecycle/Setup.tsx`, `pages/Meetings.tsx` and the backend routes those findings need |
| RAG prove/review/fix | as in section 10 |
| Docs | the RAG docs; a "Release readiness" section in `docs/STATUS.md` |
**Status (2026-11-18): done.** Every blocker and high finding is fixed and tested; the open mediums and lows, and the defaults to
confirm, are in [STATUS.md](STATUS.md#release-readiness-2026-11-18-morning).



## 12. Mentor round, 2026-11-18: board, audit log, handover, brand, agents layout, Docker and Kubernetes

The founders' mentor reviewed the product after sign-in. Their notes, and what each became:

| Mentor's note | What was built |
|---|---|
| Milestones configurable (15, 30, 45, 60, 90 days), editable at any time, visible to admin and hire | Board milestones per template and per hire, edited by managers and admins; both sides see them on Home and the board |
| A Kanban board per milestone, task status (completed, in progress, moved), for user and admin | `#/board`: To do, In progress, Done per milestone; moves between milestones keep their history ("Moved from Day 15") |
| Handover transfers all documents and knowledge, documents linked via GitHub | The live transfer set in nine sections, attachments (repository file with GitHub link, uploaded document, link), transfer, completion with typed confirmation |
| Audit log newest first, from/to dates, column sort, 25/30/50 rows, a correct total | Server-side sort on every column, time ranges in the viewer's time zone, rows per page, "Showing 1–25 of N", a page snapshot |
| Home reads like an ad | A product home per role: figures, the 30/60/90, the current milestone's tasks, next actions, decisions waiting, recent activity |
| One colour; primary and secondary needed; no black-on-black forms | The **Ink + Teal** system: Ink base, Teal primary, Navy secondary for every form, dialog and popover ([brand/BRAND.md §6](brand/BRAND.md#6-colour-ink--teal)) |
| Backend folder structure for a multi-agent system; be clear on LangGraph | `app/agents/{supervisor, specialists/<key>, core, tools}` with a one-way dependency rule, held by a test; [LANGGRAPH.md](LANGGRAPH.md) |
| Docker incomplete; Kubernetes too | Both Dockerfiles documented and built in CI; Kustomize base with GKE, staging, production and any-cluster overlays ([KUBERNETES.md](KUBERNETES.md)) |

### Tracks and what each owned

| Track | Owned |
|---|---|
| Kanban (board) | Migration `018_board.sql` (`program_templates.phases`, `onboarding_phases`, `task_placements`, `task_moves`, each with `org_id` and forced RLS, memory adapter, demo data, export and deletion), `app/lifecycle/board.py`, `app/api/board.py`, `frontend/src/lifecycle/board/**`, template milestones in `lifecycle/templates.py` and `Templates.tsx`, `tests/test_board*.py` |
| Audit log | `adapters/audit_store.py`, `api/audit.py`, `lifecycle/Audit.tsx`, `audit.css`, `auditActions.ts`, the audit types in `types.ts`, `tests/test_audit.py` |
| Handover | `lifecycle/handover.py`, the handover routes in `api/lifecycle.py`, `Handovers.tsx` and its CSS, `docs/specs/handover.md`, `tests/test_handover*.py` (no migration: the plan is JSON on the existing row) |
| Home | `lifecycle/home.py`, `lifecycle/Home.tsx`, `tests/test_home_product.py` |
| Brand | the colour tokens and panel scope in `styles.css`, `docs/brand/**` |
| Agents layout | `app/agents/**` (moved with plain `mv`, every import updated, no shims), `app/agents/README.md`, `docs/LANGGRAPH.md`, `tests/test_agents_layout.py` |
| Docker and Kubernetes | both Dockerfiles, `docker-compose.yml`, `infra/k8s/**`, `docs/KUBERNETES.md`, `tests/test_release_k8s.py` |
| Review (security, UX, release) and finish | read-only reviews with failing tests; the finisher fixed what they confirmed and wrote these docs |

### Decisions

- **Ink + Teal.** Black and white stay the base. Teal is the primary (buttons, links, selection, focus); a deep navy is the
  secondary and the surface of every form, dialog and popover. Teal no longer means only "explained in a recorded session":
  KT keeps its own teal marker plus a small **KT** badge, so that meaning survives next to the brand colour.
- **Milestone and checkpoint.** "Milestone" on screen is the board's Day 15/30/45/60/90; the program's automatic checks
  (first PR merged, check-ins) are "checkpoints". In the code a board milestone is a *phase* and a checkpoint is an
  `onboarding_milestone`, so the two never mix.
- **The board's columns are task statuses.** No separate card state: a pull request that moves a task moves its card, and
  `PATCH /api/tasks` and the board agree. A hire's Done is "waiting for sign-off"; a manager signs it off.
- **Nobody manages their own onboarding**, whatever their role (security review).
- **The agents layout.** `supervisor/` (the LangGraph router: graph, state, orchestrator, tick), `specialists/<roster key>/`
  (one package per agent, with its own helpers), `core/` (deps, llm, prompts, schemas, registry, runtime, citations; imports no
  agent), `tools/`. Imports point one way: supervisor → specialists → core; tools → core. Prompts and schemas stay in `core`,
  so one place shows that every prompt carries `UNTRUSTED` and every output is a validated schema.
- **Activity is scoped by project**, not by who acted (security review): a manager sees only what happened in their projects.
- **Audit pages hold a snapshot** (`as_of`), because every read of the log writes an entry.
- No new dependencies: native HTML5 drag and drop, plain CSS.

**Status (2026-11-18): done.** The review findings and what was fixed are in [STATUS.md](STATUS.md#mentor-round-2026-11-18).

## 13. Legal round, 2026-11-19: terms, privacy, data processing, recording consent

The readiness check found no terms of service, privacy policy or data-processing agreement in the product, and no record of
recording consent. All four are now in the product. **The texts are first drafts written by an AI, not legal advice: no
lawyer has read them.** [LEGAL.md](LEGAL.md) lists what to fill in, what to decide with a lawyer and what to check before
selling.

| Track | Built | Files |
|---|---|---|
| Content | Six documents as structured data, a data inventory with file pointers, Markdown mirrors | `frontend/src/legal/**`, `docs/legal/**`, `docs/LEGAL.md` |
| Consent records | `legal_acceptances` (append-only), status and accept routes, the organisation's DPA, the `LEGAL_*` settings, `LEGAL_ENFORCE` | migration `019_legal.sql`, `app/legal.py`, `app/api/legal.py`, `docs/specs/legal-consent.md`, `tests/test_legal*.py` |
| Recording | Consent before any recording is taken in, removal requests, self-hosted retention, deletion of the Gemini Files copy | migration `020_recording_consent.sql`, `app/services/recordings.py`, `app/api/recordings.py`, `tests/test_recording_consent*.py` |
| Web app | The documents' pages and footer links, the agreement on the sign-up screens, the consent screen, the recording notice, Settings → Organisation → Legal | `frontend/src/site/Legal.tsx`, `legal.css`, `frontend/src/legal/paths.ts`, `frontend/src/auth/Agree.tsx`, `frontend/src/consent/**`, `frontend/src/pages/settings/Legal.tsx`, `tests/test_legal_pages.py` |

### Decisions

- **The operator's details are settings, never text.** An unset one reads as a neutral phrase; nothing is made up, and
  nothing is required to start the server.
- **One source of words.** The pages, the consent screen, the recording notice card and `docs/legal/*.md` all read
  `frontend/src/legal/`. A version is a date, held in two places and checked by a test.
- **Public pages say "Draft"** until `LEGAL_REVIEW.status` is set to `"reviewed"`, after a lawyer has read them.
- **Legal pages open without the intro.** They are opened from a checkbox, a calendar invitation or a contract, to be read.
- **Boxes are never ticked for the person,** and a missed box says so next to itself rather than disabling the button.
- **The consent screen blocks the workspace, not the exits.** Signing out, leaving, and an admin's export and deletion stay
  reachable on it. If the status cannot be read and `LEGAL_ENFORCE` is off, the workspace opens.
- **The recording notice is an acknowledgement, not consent to a recording.** Consent is confirmed per session, by whoever
  adds the recording, in the words the API records.
- **The demo's one-click invitation carries no agreement,** so the demo shows the consent screen to a new invitee.
- No new dependencies: the documents are rendered from structured data, with no Markdown library.

**Status (2026-11-19): built; the texts await a lawyer.** What is open is in [STATUS.md](STATUS.md#legal-basics-2026-11-19) and [LEGAL.md](LEGAL.md).
