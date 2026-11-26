# HTTP API

The API the web app uses, grouped by area. It is the same API in every mode. The route list below was checked against the
running app (`MODE=demo`, `GET /api/openapi.json`, 2026-11-18: 162 routes, every one listed here); the "Who" column summarises
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
`POST /api/me/leave`, `POST /api/notifications/read` and `POST /api/legal/accept`. Project routes also need project membership. A hire's data is
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
| client_errors | `POST` `/api/client-errors` | address | 10 |
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
| GET | `/api/config` | public | What the web app needs before sign-in: mode, Firebase config, AI on, GitHub App, Google Calendar, Calendly, `email` (true only when SMTP is set up: say "Emailed" only then), and `legal`: `{operator, address, contact_email, privacy_email, governing_law, courts, versions: {terms, privacy, dpa, recording, subprocessors}, enforced}`, where each detail is null until its `LEGAL_*` setting is set |
| POST | `/api/client-errors` | public | A crash report from the web app (`frontend/src/report.ts`, sent with `sendBeacon`): `{id, kind: render\|error\|rejection, name, message, stack, component, page, build, at}`, nothing else. Text is cut to length; queries, emails and tokens are removed; it is written as one log line and never stored. 204; 422 for anything else; at most 16 KB and 10 a minute per address |
| GET | `/api/lifecycle/config` | public | Feature flags, reporting floors, check-in days, link lifetime, template tracks and levels |

## Auth and account

| Method | Path | Who | Purpose |
|---|---|---|---|
| POST | `/api/signup` | Firebase login | Create an organisation, its first project and its admin `{name, org_name, project_name, accept: ["terms", "privacy"]}`. `accept` is the person's acceptance of the current terms and privacy policy, recorded in the same request (`"recording"` and, for the new organisation, `"dpa"` may be added); without both, 422 `legal_required` and nothing is created |
| POST | `/api/invitations/accept` | Firebase login | Join the organisation that invited this email `{org_id, accept: ["terms", "privacy"]}`; the acceptance is recorded in the same request (`"recording"` may be added); without both, 422 `legal_required` and nobody joins |
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
| POST | `/api/meetings/{meeting_id}/recording/import` | managers, experts, the booker | Import the recording from Google Drive `{consent?: true}`; 202; 409 `no_recording_yet`; 422 `consent_required` when nobody has confirmed the session's recording consent and the request does not (nothing is fetched) |
| POST | `/api/meetings/{meeting_id}/recording/upload` | managers, experts, the booker | Step 1 of an upload: `{filename, size, duration_s, consent?: true}` → an `upload_id` and where and how to send the file (a signed Cloud Storage URL on Google Cloud; this API with an `X-Upload-Token` self-hosted). No file is stored yet. `consent: true` is the caller's confirmation that everyone in the session was told it is recorded and agreed: it is recorded on the session and audited; without it (and with none on the session) 422 `consent_required`. 404 in demo mode |
| POST | `/api/meetings/{meeting_id}/recording` | managers, experts, the booker | Self-hosted step 2: the file as the raw body with `X-Upload-Token` (204). Without the token, the older one-request upload (multipart `file`, `duration_s`, `consent=true`; 202; 422 `consent_required` without the consent, and nothing is stored). 404 in demo mode |
| POST | `/api/meetings/{meeting_id}/recording/complete` | the person who started the upload | Step 3 `{upload_id}`: the file has arrived; it is checked and handed to the Note-taker, as any recording. 202; 422 `consent_required` (and the file is deleted) if the session has no recording consent; 404 in demo mode |
| GET | `/api/meetings/{meeting_id}/recording` | members | A short-lived signed link to the video |
| POST | `/api/meetings/{meeting_id}/recording/consent` | managers, experts, the booker | `{confirm: true}`: confirm that everyone in the session was told it is recorded and agreed. Answers `{meeting, statement}`; the session's `recording_consent` is `{by, by_name, at, notice_version}` (the first confirmation is kept; `notice_version` is the recording notice's version). A Google Calendar session's recording is imported by the poller only after this. Audited as `recording.consent_confirmed`. 409 for a cancelled session |
| POST | `/api/meetings/{meeting_id}/recording/removal-requests` | members | `{reason?}` (1,000 characters at most): ask for the session's recording to be removed. 201 `{request, created}`; the project's managers are notified and nothing is removed. One open request per person and session (`created: false` returns it). 409 `nothing_recorded`. Audited as `recording.removal_requested` (never the reason) |
| GET | `/api/recording-removal-requests` | members | `?project_id=&meeting_id=&status=open\|removed\|declined` → `{requests: [{id, meeting_id, requested_by, requested_by_name, reason, status, decided_by, decided_by_name, decided_at, note, created_at}]}`: all of the project's for managers and admins, the caller's own for anyone else |
| POST | `/api/meetings/{meeting_id}/recording/removal-requests/{request_id}/decline` | managers | `{note?}`: decline one open request; whoever asked is notified with the note. 409 once decided. Removing the recording (`DELETE …/recording`) closes every open request of the session as `removed` and tells whoever asked |
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
| GET | `/api/approvals?project_id=&status=` | admin, manager | What agents proposed and wait for a person: `task_plan`, `review`, `kt_followup`, and the Keeper's and Planner's proposals `rerecord`, `doc_edit`, `template_change` (admins only decide a template change) |
| POST | `/api/approvals/{approval_id}/decide` | admin, manager | Decide. Plans: `{approve: [task ids], edits: {id: {title?, description?, due_date?}}}`. Reviews: `{decision: "approved" | "changes_requested"}`. KT follow-ups: `{approve: [session keys]}`. 409 if already decided or out of date. |

## Lifecycle

### Homes, setup, teams and templates

| Method | Path | Who | Purpose |
|---|---|---|---|
| GET | `/api/home` | any | The caller's role home: `{role, focus: [{key, priority, text, link, why, explain?}], figure: {label, value, how}, sections}`. A new hire's sections: `program`, `phases` (the board's milestones, each `{key, title, due_day, due_date, state: done \| current \| upcoming, tasks_done, tasks_total}`), `current` (the current milestone with up to 8 tasks `{id, title, kind, status, due_date, pr_number}`), `first_task`, `questions`. A manager's: `hires`, `approvals {count, items}`, `activity`; an admin's also `setup` and the figure "People on projects". `activity` is up to 8 product events, newest first, `{id, at, action, text, link, actor: {id, name}}`: board moves and edits (`link` `/board/<onboarding id>`; a run of moves by one person on one board within 10 minutes is one line, "moved 3 tasks on Vijay's board"), enrolments, plans decided, KT sessions and recordings, check-ins, handovers started, documents and repositories. Admins get the organisation's events; a manager only events that happened in their own projects (placed by the entry's `project_id`, its onboarding or its target; an event that cannot be placed is left out). Security events (sign-ins, exports, role changes, reads of the audit log) never appear. Reading the home writes nothing. |
| GET | `/api/setup` | admin | Setup steps and their status; minutes per avoided interruption |
| PATCH | `/api/setup` | admin | `{step, action: complete | skip | reopen}` or `{minutes_per_interruption}` |
| GET | `/api/teams?project_id=` | admin, manager | Teams |
| POST | `/api/teams` | admin, manager | Create a team `{project_id, name}` |
| PATCH | `/api/teams/{team_id}` | admin, manager | Rename `{name}` |
| GET | `/api/programs/templates?include_inactive=` | admin, manager | Program templates (16 defaults on first read) |
| POST | `/api/programs/templates` | admin | Create a template `{key, title, role_track, level, duration_days?, milestones?, phases?, …}`. `milestones` are the program's checkpoints (pre-boarding done, first PR merged …); `phases` are the board's milestones `[{key?, title, due_day}]`, at most 12, distinct days (empty: days 15, 30, 45, 60 and 90) |
| PATCH | `/api/programs/templates/{template_id}` | admin | Change a template, `phases` included: makes a new version. A program takes its template's board milestones the first time its board is read; later changes to the template do not touch running boards |

### Enrolment and the program

| Method | Path | Who | Purpose |
|---|---|---|---|
| POST | `/api/onboardings/enrol` | admin, manager | Invitation + program + drafted plan in one step `{project_id, email, name, start_date, template_id, github_login?, team_id?, mentor_id?, focus_area?, role_title?}`; 201 `{onboarding, program, invitation, plan}` |
| GET | `/api/onboardings/{onboarding_id}/program` | the hire, their mentor, managers | Profile, plan status, milestones, plan items, access, acknowledgements, environment checks, pre-boarding, first task |
| PATCH | `/api/onboardings/{onboarding_id}/milestones/{key}` | admin, manager (never the hire themselves, whatever their role) | A checkpoint: `{status: pending | done | skipped, note?}` |
| GET | `/api/onboardings/{onboarding_id}/access` | the hire, their mentor, managers, item owners | The access checklist |
| POST | `/api/onboardings/{onboarding_id}/access` | admin, manager | Request access `{system, owner_id?}` |
| PATCH | `/api/onboardings/{onboarding_id}/access/{item_id}` | the item's owner, managers | `{status: requested | granted | not_needed}` |

### The milestone board

One hire's tasks by milestone (Day 15, 30, 45, 60, 90 by default) and by column (To do, In progress, Done), for the hire and
the project's managers and admins ([specs/lifecycle.md](specs/lifecycle.md), `app/lifecycle/board.py`). The columns are the
task's own status: To do = `approved`; In progress = `in_progress`, `changes_requested`; Done = `submitted` (waiting for
sign-off, or in review with a pull request) and `done`. Proposed tasks wait in Approvals and are not on the board. A mentor
gets 403; anyone unrelated, 404. A hire is never the manager of their own board, whatever their role (403). Every write
answers with the whole board, and is audited (`board.task.moved`, `board.task.rescheduled`, `board.task.added`,
`board.task.changed`, `board.task.removed`, `board.milestone.added`, `board.milestone.changed`, `board.milestone.removed`,
`board.milestones.reordered`; each entry's detail names the onboarding and the project, and a task entry the task's title).

| Method | Path | Who | Purpose |
|---|---|---|---|
| GET | `/api/board/hires?project_id=` | admin, manager, the hire | The hire picker `{hires: [{onboarding_id, name, role_title, day, duration_days, status, total, done, overdue, current}], can_manage}`: every active hire for managers, their own row for a hire |
| GET | `/api/onboardings/{onboarding_id}/board` | the hire, managers | `{onboarding_id, project_id, status, hire, role_title, start_date, today, day, duration_days, columns, milestones, tasks, current, waiting_approval, can: {move, reschedule, edit}}`. Each milestone: `{key, title, due_day, due_date, from_day, position, state: complete \| overdue \| current \| past \| upcoming, total, done, awaiting, overdue, counts, checkpoints, moved_out}`. Each card: `{id, title, description, kind, status, column, badge, milestone, pinned, due_date, due_day, overdue, kt, pr_number, pr_url, acceptance_criteria, review_pending, moved, history, moves}` (`moves`: the columns this caller may move it to) |
| POST | `/api/onboardings/{onboarding_id}/board/tasks/{task_id}/move` | the hire (own cards, columns only), managers | `{column?: todo \| in_progress \| done, milestone?: <key>, before_id?, changes_requested?, sign_off?}`. The hire's Done is `submitted` (their manager signs it off); only managers move between milestones, sign off, ask for changes and reopen a done task. 403 `manager_only`/`done`, 409 `awaiting_approval` (still in Approvals), `in_review` (a pull request decides it), `inactive`; 422 when `before_id` is not on the board |
| POST | `/api/onboardings/{onboarding_id}/board/tasks` | admin, manager | Add a task `{title, description?, kind?: learn \| code \| meet, milestone?, due_date?}` (default: the current milestone, due on its day); 201 |
| PATCH | `/api/onboardings/{onboarding_id}/board/tasks/{task_id}` | admin, manager | `{title?, description?, due_date?}` |
| DELETE | `/api/onboardings/{onboarding_id}/board/tasks/{task_id}` | admin, manager | Remove it from the board (the task is rejected; its history and reviews stay). 409 `review_pending` while a review waits in Approvals |
| POST | `/api/onboardings/{onboarding_id}/board/milestones` | admin, manager | Add a milestone `{title, due_day: 1..730}`; 201. 409 `day_taken`, `too_many` (12) |
| PATCH | `/api/onboardings/{onboarding_id}/board/milestones/{milestone_key}` | admin, manager | Rename or re-date `{title?, due_day?}`; tasks placed by date re-flow |
| PUT | `/api/onboardings/{onboarding_id}/board/milestones/order` | admin, manager | `{keys: [...]}`: those first, in that order, the rest after |
| DELETE | `/api/onboardings/{onboarding_id}/board/milestones/{milestone_key}?move_to=` | admin, manager | Remove a milestone; its tasks move to `move_to` (default: the next one by day), each move recorded. 409 `last_one` |

The hire and the other side are notified when a change concerns them (`/board/<onboarding id>` links).

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
| PATCH | `/api/handovers/{handover_id}` | admin, manager | `{status?: draft \| in_progress, done?: [key], undone?: [key], successor_id?, reassign?}`. `done` and `undone` take item and gap keys of this handover's transfer set (`<kind>:<ref>`, or a gap's key such as `codeowners`; up to 500 each, 1,200 characters each): any other key is 422 and nothing is saved. `successor_id`, and with `reassign` the successor already stored on the plan, must be an active expert, manager or admin of the project other than the leaver (422 otherwise, before anything is written). `reassign` runs the transfer and records `handover.transferred {to, user_id, moved}` as well as `handover.updated`. `status: done` is 422: complete it instead | `handover` |
| GET | `/api/handovers/{handover_id}/transfer` | admin, manager | The live transfer set: `{handover_id, status, last_day, days_left, person, successor, new_owner, transfer, completed, repository, sections, gaps, completeness: {total, done, moves, todo, percent}, coverage, codeowners: {login, new_login, lines: [{before, after}]}, attachable: {repository, documents}, can_complete, can_deactivate, ticked, note}`. Nine sections (code, documents, kt, sessions, people, questions, approvals, access, feedback), each `{key, title, how, count, done, moves, todo, items (first 100), more}`; an item is `{key, title, state: moves \| todo \| done, detail, href, url, owner_id, kt, gap}` | `handover` |
| POST | `/api/handovers/{handover_id}/transfer` | admin, manager | Move what Welcome Wave holds to `{successor_id}`: mentees, hires they manage, open questions, access items and feedback. Run again, it moves what landed on them since. `{handover, moved: {mentee, managed, request, access, feedback}}`; 422 for a successor who may not take over; 409 once complete | `handover` |
| POST | `/api/handovers/{handover_id}/attachments` | admin, manager | Attach a document the handover did not find `{kind: repo \| document \| link, ref?, url?, title?, note?, owner_id?}`: a file of the indexed repository (with its GitHub link), a document uploaded to the project, or an https link (title required). 201 `{handover, attachment}`; 409 if already listed or attached (at most 100) | `handover` |
| DELETE | `/api/handovers/{handover_id}/attachments/{attachment_id}` | admin, manager | Remove an attachment `{handover}` | `handover` |
| POST | `/api/handovers/{handover_id}/complete` | admin, manager; `deactivate` admins only | `{confirm: "<the leaver's name>", accept_gaps?, deactivate?}`. Re-checks the stored successor (422), moves what is still on the leaver, and records accepted gaps. 409 `open_gaps` (with `gaps`) unless `accept_gaps`; with `deactivate`, 409 for oneself, another open handover for them, the last active admin, or `held_elsewhere` (with `projects`) while they still manage or mentor an active hire, or hold open questions, access items or feedback in another project. `{handover, moved, deactivated, accepted_gaps}` | `handover` |
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

## Agents

One Supervisor and eight specialists ([AGENTS.md](AGENTS.md); the code layout in [LANGGRAPH.md](LANGGRAPH.md)).

| Method | Path | Who | Purpose |
|---|---|---|---|
| GET | `/api/agents` | admin, manager, executive | The roster: each agent's mode, recent runs and pending proposals |
| GET | `/api/agents/runs` | admin, manager | Recent runs; summaries never quote a hire's question or a private review |
| GET | `/api/agents/runs/{run_id}` | own | One run and its shared trace: the asker's own run, any run of the project for its managers; a private run (pre-submit review) its own user alone |
| PATCH | `/api/agents/{agent_key}` | admin | `{mode: ai \| rules}` for the organisation; the Supervisor and the Librarian answer 409 |
| POST | `/api/agents/tick` | admin, manager | Demo and self-hosted: run the daily tick now (404 on Google Cloud, where Cloud Scheduler calls `/internal/lifecycle/tick`) |
| POST | `/api/ask/free` | any except executive | A free-form request: the Supervisor picks the Guide (an answer) or proposes what the Coach, Planner or Scout would do |

## Members

| Method | Path | Who | Purpose |
|---|---|---|---|
| GET | `/api/org/members` | admin | Members with role, status and projects |
| PATCH | `/api/org/members/{user_id}` | admin | Change a role `{role}`; 409 `last_admin` |
| POST | `/api/org/members/{user_id}/deactivate` | admin | Deactivate; returns `needs_reassignment` (open questions, mentees); 409 `self` or `last_admin` |
| POST | `/api/org/members/{user_id}/reactivate` | admin | Reactivate |
| POST | `/api/org/people/erase` | admin | `{email, confirm}` (the address typed again): replace the person's name, email and GitHub username, unbind their login, delete their chat history, questions, notifications and invitations; ids stay so the organisation's records hold together; the answer lists what is kept. Cannot be undone; 409 for oneself or the last active admin. Audited |
| PATCH | `/api/projects/{project_id}/members/{user_id}` | admin, manager | A project member's GitHub username `{github_login}`; audited, and the person is told |

## Audit

| Method | Path | Who | Purpose |
|---|---|---|---|
| GET | `/api/audit` | admin | One page, newest first by default. Parameters: `sort` (time, action, actor, target), `order` (desc, asc), `action` (exact, or a prefix ending in `*`), `exclude_action` (the same syntax, left out; the web app sends `audit.viewed` unless "Show views of this log" is ticked), `actor` (user id, email, or `system`), `target` (exact), `since` / `until` (ISO date or date-time; `since` includes its instant, `until` excludes its own; a date-only `until` includes that UTC day; the web app sends the viewer's own local midnights), `limit` 1–200 (default 50), `offset`, `as_of`. Response `{items, total, limit, offset, next_offset, sort, order, page, pages, as_of, retention_days}`. Audited (`audit.viewed`). |
| GET | `/api/audit.csv` | admin | The same filters, `sort`, `order` and `as_of`, every match (up to `AUDIT_EXPORT_MAX_ROWS`) in that order; Postgres reads it from one snapshot. Audited (`audit.exported`, with the filters, and sort and order when not the default). |
| GET | `/api/audit/facets` | admin | The filter menus: `{actions: [{action, count}], actors: [{id, name, email, count}], system, first_at, last_at, retention_days}`. Audited (`audit.viewed {facets: true}`). |

- **Order.** Equal values break newest first (created_at desc, then id desc); for `sort=time` both follow the asked order. For
  `sort=actor`, entries with no person ("System") come last both ways. Text compares case-insensitively by code point, so
  Postgres and the in-memory store give the same pages.
- **Snapshot.** Each read writes an `audit.viewed` entry, which would be the newest row. `as_of` in a response is the newest
  entry's id when that page was read (before its own entry); send it back with the next pages and the CSV, and they hold only
  entries up to it, so paging never repeats or skips a row. The web app keeps it until a filter, the order or Refresh changes.
- **Items** `{id, created_at, actor_id, actor_name, actor_email, action, target, detail, target_kind, target_name, target_link}`.
  `target_kind` is person, onboarding (named for the hire; a board milestone's links to `/board/<id>`), meeting, project,
  organisation, access, handover, feedback, task (board and task moves: "<title> · <hire>'s board", linking to the board),
  or null. Only this organisation's records are named; a failed lookup leaves the raw id.
- **Errors.** 422 for an unknown sort or order, a malformed date, `since` not before `until`, or a negative `as_of`; 403 for
  non-admins; 401 signed out.

## Legal acceptances

Who accepted which version of which document ([specs/legal-consent.md](specs/legal-consent.md)). The documents are pages of
the web app; the API keeps their versions (`GET /api/config` → `legal.versions`) and the acceptances.

| Method | Path | Who | Purpose |
|---|---|---|---|
| GET | `/api/legal/status` | any | `{needs: ["terms", "privacy", "recording"], accepted: [{document, version, at}], versions, required: ["terms", "privacy"], enforced}`. `needs`: the personal documents whose current version the caller has not accepted (again after every version change) |
| POST | `/api/legal/accept` | any | `{documents: ["terms", "privacy", "recording"], versions?: {terms: "YYYY-MM-DD"}}`: record the current version of each for the caller. Answers the status plus `recorded` (the ones that were new). 422 `unknown_document`, or `dpa_is_organisation` for `"dpa"`; 409 `version_changed` when `versions` names one that is no longer current. Audited `legal.accepted` |
| GET | `/api/org/legal` | admin | `{dpa: {version, accepted, accepted_at, accepted_by: {id, name, email}, previous: [{version, accepted_at, accepted_by}]}, versions, operator}` |
| POST | `/api/org/legal/dpa` | admin | `{version?}`: accept the current data-processing agreement for the organisation. Answers `GET /api/org/legal` plus `recorded` (false when it was accepted already: the first acceptance stands). 409 `version_changed`. Audited `legal.dpa_accepted` |
| GET | `/api/org/legal/dpa/record` | admin | The acceptance as a text file (`?version=`, default the newest accepted): organisation, version, time, who. 404 `dpa_not_accepted` |

With `LEGAL_ENFORCE=true` (off by default), signed-in routes outside the short list in [PERMISSIONS.md](PERMISSIONS.md) answer 403
`{code: "legal_required", message, needs, versions}` until the caller has accepted the current terms and privacy policy.

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
| POST | `/internal/lifecycle/tick` | Cloud Scheduler (Google OIDC) | The agents' daily tick (blockers, milestones, nudges) |

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
| POST | `/api/demo/reset` | admin, manager | The seeded organisation again, at day 0 |

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
