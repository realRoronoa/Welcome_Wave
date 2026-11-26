# HTTP API

The API the web app uses, grouped by area. It is the same API in every mode. The route list below was generated from the
running app (`MODE=demo`, `GET /api/openapi.json`, 2026-11-14: 130 routes); the "Who" column summarises
[PERMISSIONS.md](PERMISSIONS.md), which `tests/test_permissions_matrix.py` holds the app to. Request and response fields of the
lifecycle routes are in [specs/](specs/lifecycle.md).

**Status.** These routes are built for the web app and may change between releases. A public, versioned API with scoped
tokens is **planned, not built**: the `api_tokens` table exists (migration 010) but no route issues, accepts or revokes a token.

## Basics

| | |
|---|---|
| Base path | `/api` (webhooks under `/webhooks`, scheduler jobs under `/internal`, health at `/health`) |
| Format | JSON in and out, except uploads (`multipart/form-data`), CSV downloads, the environment-check script and `GET /api/live` (server-sent events) |
| Schema | Demo mode serves `/api/openapi.json` and Swagger UI at `/api/docs`. Production does not serve them. |
| Time | ISO 8601. Lifecycle days and due dates use the organisation's lifecycle clock (the demo clock in demo mode). |

### Signing in

| Mode | Send |
|---|---|
| `self_hosted`, `gcp` | `Authorization: Bearer <Firebase ID token>` from Identity Platform (Firebase Auth). The API verifies it on every request. A verified login with no account answers 403 with `code` `invitation_pending` (and the invitations), `no_account` or `email_not_verified`. |
| `demo` | `POST /api/demo/login {email, password}` returns the user; send `X-Demo-User: <user id>` on every later call. The header alone is the session, which is why demo mode refuses to start with `ENV=prod`. |

Roles: `admin`, `manager`, `expert`, `new_hire`, `executive`. Admins pass every role check, except on routes about one
person's own data. Executives are refused every non-GET request (403 "Executives have read-only access") except
`POST /api/me/leave` and `POST /api/notifications/read`. Project routes also need project membership. A hire's data is
visible to the hire, their mentor and the project's managers; anyone else gets 404.

In the tables: **any** = any signed-in member; **members** = members of the project; **own** = only the caller's own data or
data they are related to; **public** = no sign-in (the route's own token or signature is the permission).

### Errors

| Status | Body | When |
|---|---|---|
| 400 | `{"detail": "…"}` | A request that cannot apply here (for example a Firebase call in demo mode) |
| 401 | `{"detail": "…"}` | Not signed in, or the token is invalid or expired |
| 403 | `{"detail": "Requires role: manager"}`, or `{"detail": {"code", "message", …}}` | Wrong role; `deactivated`; `invitation_pending`, `no_account`, `email_not_verified` |
| 404 | `{"detail": "… not found"}` | Unknown, in another organisation, or not yours to see; also a feature turned off: "This feature (name) is turned off on this server." |
| 409 | `{"detail": "…"}` or `{"detail": {"code", "message"}}` | State conflicts: already decided, already enrolled, `last_admin`, `self`, `no_recording_yet`, `still_preparing` |
| 422 | `{"detail": "…"}`, or FastAPI's `{"detail": [{"type", "loc", "msg", "input"}]}` | Invalid input |
| 429 | `{"detail": "Too many requests. Try again in N s.", "retry_after": N}` and `Retry-After` | Rate limit |
| 503 | `{"detail": "…"}` | The AI is not set up (self-hosted without `GEMINI_API_KEY`), or the assistant is unavailable |
| 500 | `{"detail": "Something went wrong. Quote this id if you report it.", "request_id": "…"}` | An unexpected error |

Every response carries `x-request-id` (send your own `X-Request-Id` to set it), `Cache-Control: no-store`,
`Content-Security-Policy: default-src 'none'; frame-ancestors 'none'`, HSTS, `nosniff` and `X-Frame-Options: DENY`.

### Rate limits

Per minute, per process (`app/ratelimit.py`; settings in [ADMIN_GUIDE.md §12](ADMIN_GUIDE.md#12-rate-limits)):

| Group | Routes | Charged to | Limit |
|---|---|---|---|
| auth | `POST` `/api/demo/login`, `/api/signup`, `/api/invitations/accept`, `/api/demo/invitations/accept` | address | 10 |
| chat | `POST` `/api/chat`, `/api/ask/route`, `/api/tasks/{id}/presubmit-review` | person | 20 |
| preboarding | `/api/preboarding/*` | address | 30 |
| webhooks | `/webhooks/*` | address | 60 |
| default | everything else | person (address before sign-in) | 120 |

Questions also count against `CHAT_RATE_PER_MINUTE` (12 per person, chat and routing together). `/health` and
`/health/ready` are never limited.

### Feature flags

Routes marked with a flag answer 404 while `FEATURES` turns it off ([ADMIN_GUIDE.md §11](ADMIN_GUIDE.md#11-feature-flags)).

---

## Health and configuration

| Method | Path | Who | Purpose |
|---|---|---|---|
| GET | `/health` | public | Liveness |
| GET | `/health/ready` | public | Readiness: the database answers (503 if not) |
| GET | `/api/config` | public | What the web app needs before sign-in: mode, Firebase config, AI on, GitHub App, Google Calendar, Calendly, and `email` (true only when SMTP is set up: say "Emailed" only then) |
| GET | `/api/lifecycle/config` | public | Feature flags, reporting floors, check-in days, link lifetime, template tracks and levels |

## Auth and account

| Method | Path | Who | Purpose |
|---|---|---|---|
| POST | `/api/signup` | Firebase login | Create an organisation, its first project and its admin `{name, org_name, project_name}` |
| POST | `/api/invitations/accept` | Firebase login | Join the organisation that invited this email `{org_id}` |
| GET | `/api/me` | any | The caller, their organisation and projects |
| POST | `/api/me/leave` | any | Leave the organisation (the login is freed for another invitation) |

## Projects, invitations and documents

| Method | Path | Who | Purpose |
|---|---|---|---|
| GET | `/api/projects` | any | The caller's projects |
| POST | `/api/projects` | admin, manager | Create a project `{name, description?}` |
| GET | `/api/projects/{project_id}` | members | Project, members, onboardings, repositories, documents, invitations |
| POST | `/api/projects/{project_id}/invitations` | admin, manager | Invite `{email, role, name?, github_login?}`; only admins invite admins and executives |
| DELETE | `/api/projects/{project_id}/invitations/{invitation_id}` | admin, manager | Revoke an invitation |
| POST | `/api/projects/{project_id}/documents` | admin, manager, expert | Upload a document (multipart `file`, `title?`); 202, read in the background |
| GET | `/api/documents/{document_id}` | members | A document's pages for the viewer |
| GET | `/api/documents/{document_id}/file` | members | The original file |
| DELETE | `/api/projects/{project_id}/documents/{document_id}` | admin, manager, expert | Delete a document (audited) |
| POST | `/api/projects/{project_id}/onboardings` | admin, manager | Start an onboarding for an existing member `{new_hire_id, start_date, role_title?}` (older path; enrolment below does more) |

## Repository and GitHub

| Method | Path | Who | Purpose |
|---|---|---|---|
| GET | `/api/github/install-url?project_id=` | admin, manager | The GitHub App install link (with signed state) |
| POST | `/api/github/callback` | admin, manager | Bind an installation after GitHub's redirect `{code, installation_id, state}` |
| GET | `/api/github/installations` | admin, manager | Installations bound to the organisation |
| GET | `/api/github/repositories?installation_id=` | admin, manager | Repositories an installation can read |
| POST | `/api/projects/{project_id}/repositories` | admin, manager | Connect the project's repository `{full_name, installation_id?, default_branch?}`; 202, read in the background |

## Chat and ask

| Method | Path | Who | Purpose |
|---|---|---|---|
| POST | `/api/ask/route` | admin, manager, expert, new hire | Ask with tiered routing `{project_id, question, ask_person?, urgent?}`, or escalate `{project_id, escalate_request_id}`. Tier 1 cited answer, tier 2 related verified answers, tier 3 mentor, tier 4 domain owner. Flag `routing`. |
| POST | `/api/chat` | admin, manager, expert, new hire | Ask without routing `{project_id, question}`: a cited answer or "not enough information" handed to a person |
| GET | `/api/chat/history?project_id=` | any | The caller's own questions and answers |
| POST | `/api/chat/{message_id}/feedback` | own | Rate an answer `{value: 1, -1 or 0}` |
| POST | `/api/verified-answers` | admin, manager, expert | Save a verified answer `{project_id, question, answer, citations?, source_request_id?}`; later answers reuse it |
| GET | `/api/verified-answers?project_id=` | admin, manager, expert | Verified answers, with reuse counts |
| GET | `/api/experts/requests` | admin, manager, expert | Questions routed to the caller |
| POST | `/api/experts/requests/{request_id}/answer` | the expert it went to, or a manager | Answer `{answer}`; the asker is told |
| GET | `/api/experts/preferences` | admin, manager, expert | The caller's interruption limit, office hours, digest |
| PATCH | `/api/experts/preferences` | admin, manager, expert | Change them `{max_interruptions_per_day?, office_hours?, digest?}` |

## Code and knowledge

| Method | Path | Who | Purpose |
|---|---|---|---|
| GET | `/api/code/files?project_id=` | members | The repository's files |
| GET | `/api/code/file?project_id=&path=` | members | One file's content |
| GET | `/api/code/explanations?project_id=&path=` | members | A file's symbols and the KT moments that explain them |
| GET | `/api/coverage?project_id=` | members | The KT coverage map by module |
| GET | `/api/tour?project_id=&order=` | members | The code tour: chapters and stops in execution or reading order, with KT clips |
| GET | `/api/knowledge-risk?project_id=` | admin, manager | Files only one person holds, ranked by risk; people and their exposure |

## KT sessions and scheduling

| Method | Path | Who | Purpose |
|---|---|---|---|
| GET | `/api/meetings?project_id=` | members | The project's KT sessions |
| POST | `/api/meetings` | admin, manager, expert, new hire | Schedule a session `{project_id, title, starts_at?, duration_min?, attendees?, provider?, join_url?}` |
| POST | `/api/meetings/{meeting_id}/schedule` | managers, the booker, attendees | Give an unscheduled session its time and place |
| POST | `/api/meetings/{meeting_id}/cancel` | managers, the booker | Cancel (and its calendar event) |
| POST | `/api/meetings/{meeting_id}/recording/import` | managers, experts, the booker | Import the recording from Google Drive; 202; 409 `no_recording_yet` |
| POST | `/api/meetings/{meeting_id}/recording` | managers, experts, the booker | Upload a recording (multipart `file`, `duration_s`); 404 in demo mode |
| GET | `/api/meetings/{meeting_id}/recording` | members | A short-lived signed link to the video |
| GET | `/api/meetings/{meeting_id}/notes` | members | Summary, decisions, action items, links to code; the transcript for managers only |
| GET | `/api/meetings/{meeting_id}/followups` | admin, manager | Follow-up sessions proposed after a recorded session |
| GET | `/api/kt-plan?project_id=&presenter=` | admin, manager, expert | The KT planner: sessions that would close the gaps, with agendas and reasons; experts see their own |
| POST | `/api/kt-plan/schedule` | admin, manager, expert | Book a planner session `{project_id, key, starts_at?, duration_min?, attendees?, provider?, join_url?}`; experts only their own |
| GET | `/api/scheduling/links?project_id=` | any | Members' Calendly links and what to prefill |

## Integrations

| Method | Path | Who | Purpose |
|---|---|---|---|
| GET | `/api/integrations` | any | Google Calendar connection, the Calendly webhook, the caller's Calendly link |
| GET | `/api/integrations/status` | admin | Is each integration set up and working: `{items: [{key, name, configured, ok, detail}], checked_at}` for gemini, firebase, github, google_calendar, calendly, smtp, langsmith. `ok` is true or false after a live call, null when there is nothing to call. No secret in it; live calls cached five minutes, `?refresh=true` repeats them ([INTEGRATIONS.md](INTEGRATIONS.md)) |
| GET | `/api/integrations/google/start` | admin, manager | Google's consent URL (signed state, 10 minutes); 404 in demo mode |
| GET | `/api/integrations/google/callback` | public (signed state) | Google's redirect; ends at `#/settings?tab=integrations&google=…`; 404 in demo mode |
| DELETE | `/api/integrations/google` | admin, manager | Disconnect Google Calendar |
| PUT | `/api/integrations/calendly/link` | any except executive | Save the caller's Calendly link `{url}` |
| DELETE | `/api/integrations/calendly/link` | any except executive | Remove it |

## Tasks and reviews

| Method | Path | Who | Purpose |
|---|---|---|---|
| GET | `/api/tasks?project_id=` | any | Managers: every task, proposed ones included. Others: approved tasks of their own active onboarding or a mentee's |
| PATCH | `/api/tasks/{task_id}` | the task's hire; managers | Move a task `{status}`; managers decide submitted ones |
| POST | `/api/tasks/{task_id}/presubmit-review` | the task's hire only | Private review of a diff or draft PR `{diff?}` or `{pr_number?}`; never stored or shown to anyone else. Flag `presubmit`. |
| GET | `/api/reviews/{review_id}` | the task's hire, their mentor, managers | A pull request review: recommendation, findings with evidence, questions |

## Approvals

| Method | Path | Who | Purpose |
|---|---|---|---|
| GET | `/api/approvals?project_id=&status=` | admin, manager | What agents proposed and wait for a person: `task_plan`, `review`, `kt_followup` (new kinds `rerecord`, `doc_edit`, `template_change` are landing this round) |
| POST | `/api/approvals/{approval_id}/decide` | admin, manager | Decide. Plans: `{approve: [task ids], edits: {id: {title?, description?, due_date?}}}`. Reviews: `{decision: "approved" | "changes_requested"}`. KT follow-ups: `{approve: [session keys]}`. 409 if already decided or out of date. |

## Lifecycle

### Homes, setup, teams and templates

| Method | Path | Who | Purpose |
|---|---|---|---|
| GET | `/api/home` | any | The caller's role home: `{role, focus, figure, sections}` |
| GET | `/api/setup` | admin | Setup steps and their status; minutes per avoided interruption |
| PATCH | `/api/setup` | admin | `{step, action: complete | skip | reopen}` or `{minutes_per_interruption}` |
| GET | `/api/teams?project_id=` | admin, manager | Teams |
| POST | `/api/teams` | admin, manager | Create a team `{project_id, name}` |
| PATCH | `/api/teams/{team_id}` | admin, manager | Rename `{name}` |
| GET | `/api/programs/templates?include_inactive=` | admin, manager | Program templates (16 defaults on first read) |
| POST | `/api/programs/templates` | admin | Create a template `{key, title, role_track, level, duration_days?, milestones?, …}` |
| PATCH | `/api/programs/templates/{template_id}` | admin | Change a template: makes a new version |

### Enrolment and the program

| Method | Path | Who | Purpose |
|---|---|---|---|
| POST | `/api/onboardings/enrol` | admin, manager | Invitation + program + drafted plan in one step `{project_id, email, name, start_date, template_id, github_login?, team_id?, mentor_id?, focus_area?, role_title?}`; 201 `{onboarding, program, invitation, plan}` |
| GET | `/api/onboardings/{onboarding_id}/program` | the hire, their mentor, managers | Profile, plan status, milestones, plan items, access, acknowledgements, environment checks, pre-boarding, first task |
| PATCH | `/api/onboardings/{onboarding_id}/milestones/{key}` | admin, manager | `{status: pending | done | skipped, note?}` |
| GET | `/api/onboardings/{onboarding_id}/access` | the hire, their mentor, managers, item owners | The access checklist |
| POST | `/api/onboardings/{onboarding_id}/access` | admin, manager | Request access `{system, owner_id?}` |
| PATCH | `/api/onboardings/{onboarding_id}/access/{item_id}` | the item's owner, managers | `{status: requested | granted | not_needed}` |

### Pre-boarding (flag `preboarding`)

| Method | Path | Who | Purpose |
|---|---|---|---|
| POST | `/api/onboardings/{onboarding_id}/preboarding-link` | admin, manager | A new link `{token, url, expires_at, emailed}` (14 days); the token is in this response only and replaces the old link. Optional body `{email: true}` also emails it to the hire when SMTP is set up and `PUBLIC_URL` is set (after the response, from memory: the token is never stored or logged); `emailed` says it was handed to the mail sender |
| GET | `/api/preboarding/{token}` | public (the link) | The portal: first name, team, dates, manager, mentor, documents, access status, environment, and (when there is one) `first_week: [{date, title, text?, done?, kind?}]` from the template's items due by day 5 and the KT sessions the hire is invited to in their first five working days (title and day only) |
| POST | `/api/preboarding/{token}/acknowledge` | public (the link) | Acknowledge a document `{doc_key}` |
| GET | `/api/preboarding/{token}/env-check.sh` | public (the link) | The environment-check script (reads tool versions, changes nothing) |
| POST | `/api/preboarding/{token}/env-check` | public (the link) | The script's results `{results: [{name, ok, detail}]}` |

Unknown, replaced, expired and over-long tokens all answer the same 404.

### First task (flag `first_task`)

| Method | Path | Who | Purpose |
|---|---|---|---|
| GET | `/api/onboardings/{onboarding_id}/first-task/shortlist` | admin, manager | Top 3 candidates with scores, factors and reasons; excluded ones with why |
| POST | `/api/onboardings/{onboarding_id}/first-task` | admin, manager | Select `{candidate_id: "issue:<n>" | "task:<id>"}`; returns the brief |

### Progress, blockers and summaries

| Method | Path | Who | Purpose | Flag |
|---|---|---|---|---|
| GET | `/api/onboardings/{onboarding_id}/areas` | the hire, their mentor, managers | Level per area from kinds of evidence | `areas` |
| PATCH | `/api/onboardings/{onboarding_id}/areas/{area}` | the hire only | Self-assessment, a recorded step, a clip watched `{self_level?, step?, watched?}` | `areas` |
| GET | `/api/onboardings/{onboarding_id}/blockers` | the hire, their mentor, managers | Blocker alerts with evidence and stage | `blockers` |
| POST | `/api/blockers/{blocker_id}/resolve` | the hire, their mentor, managers | Resolve `{note?}` | `blockers` |
| GET | `/api/onboardings/{onboarding_id}/summaries` | the hire, managers | Weekly summaries with citations | `summaries` |
| POST | `/api/onboardings/{onboarding_id}/summaries` | the hire, managers | Write this week's (replaces it) | `summaries` |

### Feedback, check-ins, surveys and completion

| Method | Path | Who | Purpose | Flag |
|---|---|---|---|---|
| POST | `/api/feedback` | admin, manager, expert, new hire | Flag a document, file, KT moment or answer `{project_id, target_kind, target_ref, kind: unclear | outdated, note?}`; goes to its owner | `feedback` |
| GET | `/api/feedback?project_id=` | any | Managers: all; experts and hires: what they sent and their queue; executives: counts only `{counts_only, total, counts: [{target_kind, kind, open, resolved, dismissed}]}`, never a note or an author | `feedback` |
| PATCH | `/api/feedback/{feedback_id}` | the owner, managers | `{status: open | resolved | dismissed}` | `feedback` |
| GET | `/api/onboardings/{onboarding_id}/checkins` | the hire, managers | Questions, written halves, what is due | `checkins` |
| POST | `/api/onboardings/{onboarding_id}/checkins` | the hire, managers | Write the caller's half `{day, answers}` | `checkins` |
| POST | `/api/surveys` | the hire only | Anonymous survey answers `{onboarding_id, day, answers}`; 204 | `surveys` |
| GET | `/api/surveys/summary?project_id=` | admin, manager, executive | Team averages for groups of 5 or more; "not enough data" below | `surveys` |
| GET | `/api/onboardings/{onboarding_id}/completion` | the hire, managers | Criteria, evidence, readiness, sign-off | `completion` |
| POST | `/api/onboardings/{onboarding_id}/completion` | admin, manager | `{decision: complete | extend, note?, extend_days?}` | `completion` |

### Handovers, visibility and reports

| Method | Path | Who | Purpose | Flag |
|---|---|---|---|---|
| GET | `/api/handovers?project_id=` | admin, manager | Handover plans | `handover` |
| POST | `/api/handovers` | admin, manager | Draft one `{project_id, user_id, last_day}` | `handover` |
| PATCH | `/api/handovers/{handover_id}` | admin, manager | `{status?, done?, undone?, successor_id?, reassign?}` | `handover` |
| GET | `/api/onboardings/{onboarding_id}/visibility` | the hire, managers | Every data point others can read about the hire, with who reads it (`visible_to`: hire, mentor, manager, `expert`, `owner`, `admin`) and the same values: now including `requests` (questions sent to a person), `reviews`, `feedback` (their notes) and `audit` (their own actions) | |
| GET | `/api/reports/onboarding?project_id=&team_id=` | admin, manager, executive | The leadership report: figures for this cohort and the previous one, each with how it is worked out; `teams` repeats the figures per team with the same floors; `team_id` narrows it to one team; `single_owner_critical_areas` is a count of code areas one person alone owns (no names); `surveys` is floored per team and survey day (five or more people) | `reports` |
| GET | `/api/reports/onboarding.csv?project_id=&team_id=` | admin, manager, executive | The same as CSV, with team rows | `reports` |

## Manager views, notifications and live updates

| Method | Path | Who | Purpose |
|---|---|---|---|
| GET | `/api/dashboard?project_id=` | admin, manager | KT coverage: `coverage` by module with KT minutes, `kt_gaps`, `repeated_questions` (asked by two or more hires), `pending_approvals`, and `people` (name, day, on-track status and first-PR status from the lifecycle clock; no activity counts) |
| GET | `/api/runs?project_id=` | admin, manager | Agent runs (with `trace_id` when LangSmith tracing is on) |
| GET | `/api/notifications` | any | The caller's notifications |
| POST | `/api/notifications/read` | any | Mark them read `{ids?}` (all when omitted) |
| GET | `/api/live` | any | Server-sent events `{t: topic, p: project}`; re-fetch through the normal API. Pings every 20 s, closes after 15 minutes. |
| GET | `/api/blobs/{token}` | public (signed link) | A file behind a short-lived signed link (self-hosted storage) |

## Agents (landing this round)

The multi-agent restructure (one Supervisor, eight specialists; [AGENTS.md](AGENTS.md)) adds these routes. They are not in the
route list above and not yet in [PERMISSIONS.md](PERMISSIONS.md).

| Method | Path | Who | Purpose |
|---|---|---|---|
| GET | `/api/agents` | admin, manager (landing this round) | The agents and their state |
| GET | `/api/agents/runs` | admin, manager (landing this round) | Recent agent runs |
| PATCH | `/api/agents/{key}` | admin (landing this round) | Set an agent's mode: `ai` or `rules` |
| POST | `/api/agents/tick` | (landing this round) | Run the agents' scheduled work now |

## Members

| Method | Path | Who | Purpose |
|---|---|---|---|
| GET | `/api/org/members` | admin | Members with role, status and projects |
| PATCH | `/api/org/members/{user_id}` | admin | Change a role `{role}`; 409 `last_admin` |
| POST | `/api/org/members/{user_id}/deactivate` | admin | Deactivate; returns `needs_reassignment` (open questions, mentees); 409 `self` or `last_admin` |
| POST | `/api/org/members/{user_id}/reactivate` | admin | Reactivate |

## Audit

| Method | Path | Who | Purpose |
|---|---|---|---|
| GET | `/api/audit?action=&actor=&target=&since=&until=&limit=&offset=` | admin | One page of the log, newest first: `{items, total, limit, offset, next_offset, retention_days}`. `action` may end in `*`. Audited (`audit.viewed`). |
| GET | `/api/audit.csv?action=&actor=&target=&since=&until=` | admin | Every matching entry as CSV (up to `AUDIT_EXPORT_MAX_ROWS`). Audited (`audit.exported`). |

## Organisation data

| Method | Path | Who | Purpose |
|---|---|---|---|
| GET | `/api/org/export` | admin | Every organisation-scoped table as one JSON download, plus the organisation row. Search data and credentials are left out; the survey is `survey_aggregates` (floored, per team and day), never response rows, and programs carry no `survey_days`. Audited. |
| DELETE | `/api/org` | admin | `{confirm: "<organisation name>"}`: deletes every organisation table in one transaction, then the stored documents and recordings, the LangGraph checkpoints of its paused runs and its queued outbox events. Returns `{deleted: {<table or kind>: n}}`, with `stored_files`, `checkpoint_threads` and `outbox`, and `…_failed` counts if something could not be removed. 422 if the name does not match. |

## Webhooks and scheduled jobs

| Method | Path | Who | Purpose |
|---|---|---|---|
| POST | `/webhooks/github` | GitHub (`X-Hub-Signature-256`) | Installations, pushes and pull requests; processed once per delivery |
| POST | `/webhooks/calendly` | Calendly (`Calendly-Webhook-Signature`) | `invitee.created` and `invitee.canceled` for bookings made from Welcome Wave; 404 without a signing key |
| POST | `/internal/outbox/flush` | Cloud Scheduler (Google OIDC) | Re-send events whose publish failed; 503 while some are still waiting |
| POST | `/internal/recordings/poll` | Cloud Scheduler (Google OIDC) | Import ended Google sessions' recordings; mark long-silent ones ended |
| POST | `/internal/audit/purge` | Cloud Scheduler (Google OIDC) | Delete audit entries older than `AUDIT_RETENTION_DAYS` |

The `/internal` routes accept only a Google-signed token for `OUTBOX_FLUSH_AUDIENCE` from `OUTBOX_FLUSH_INVOKER`; without those
settings they are off.

## Demo

Registered only when `MODE=demo`; production never has them.

| Method | Path | Who | Purpose |
|---|---|---|---|
| GET | `/api/demo/people` | public | Who can sign in, and pending invitations |
| POST | `/api/demo/login` | public | `{email, password}` → `{user}`; a wrong password and an unknown email get the same 401; 10 tries a minute per email and address |
| POST | `/api/demo/invitations/accept` | public | Join as an invitee `{project_id, invitation_id}` |
| POST | `/api/demo/pull-request` | new hire (own code task) | Stands in for GitHub: opens a pull request (or pushes a commit) for `{task_id}` through the real webhook handler; the Review agent reviews the demo diff |
| POST | `/api/demo/advance` | admin, manager | The demo clock: `{days: 1..90}` moves every program forward, runs milestones and blocker rules. Flag `demo_clock`. |

## Example (demo)

```bash
B=http://127.0.0.1:8080
curl -s -X POST $B/api/demo/login -H 'Content-Type: application/json' \
  -d '{"email":"vijay@demo.dev","password":"welcome-wave"}'          # {"user": {"id": "u-vijay", ...}}
curl -s -X POST $B/api/ask/route -H 'X-Demo-User: u-vijay' -H 'Content-Type: application/json' \
  -d '{"project_id":"proj-demo","question":"Why does validation never raise?"}'
# {"tier": 1, "status": "answered", "citations": [{"label": "docs/CONTRIBUTING.md:L1-L6", ...},
#  {"label": "KT #1: How validation works at 01:38", ...}], "why": ["Every sentence maps to a cited source.", ...]}
```
