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
