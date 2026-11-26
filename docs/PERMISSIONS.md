# Permissions: every route × role

The server checks every route itself (`app/api/auth.py`: `current_user`, `require(...)`, `check_project`; the lifecycle
routes also check how the caller is related to the onboarding). This file is the matrix those checks implement, and
`backend/tests/test_permissions_matrix.py` holds the app to it: it lists every route of the running app, fails if one is
missing here, and calls each route as each demo user, expecting a refusal (401, 403 or 404) for `no` and anything but a
refusal for `yes` and `own`.

**Cells.** `yes`: the role may call it. `no`: refused. `own`: only on the caller's own data or data they are related to
(their own onboarding, the hire they mentor, an access item or feedback item they own, their own chat message).
`public`: no sign-in (the route's own token or signature, if any, is the permission). `n/a`: not signed in with a role at
all (webhooks with signatures, Cloud Scheduler with OIDC, Firebase sign-up).

**Roles.** Admins pass every `require(...)`, but routes about one person's own data (a hire's self-assessment, survey,
pre-submit review, demo pull request) stay with that person. Executives are read-only: `current_user` refuses them
every non-GET request except leaving the organisation, marking their own notifications read and accepting the legal
documents for themselves (`POST /api/legal/accept`); they read
organisation-level reports, not individual hires' programs. Mentors are experts (or managers) named on a program; the
demo's expert, Ravi, mentors Vijay, whose onboarding the test uses for `own`.

**Who sees a hire's data.** The hire, their mentor and the project's managers (admins included). Anyone else gets 404, as
if it did not exist. Pre-submit reviews are the hire's alone; survey answers carry no person id and are shown only for
teams and survey days where five or more distinct people answered (a hire answers once per survey day, and each question's average
needs five answers too: three hires answering on days 30, 60 and 90 are three people, not nine). The hire's own visibility page
(`GET /api/onboardings/{id}/visibility`) lists every datum about them with who can read it: the hire, their mentor, the project's
managers, and where it goes to someone else, `expert` (whoever a question was routed to: they read it in full with the hire's name,
in their inbox), `owner` (whoever owns the document or file a feedback note is about) and `admin` (admins alone read the audit log,
which holds the hire's own actions). A plan awaiting approval is the managers' until it is approved: `GET /api/tasks` shows it to
managers only, and the KT planner describes it to experts as "planned work". Tasks and pull request reviews follow the
same rule as the rest of a hire's data: an expert sees those of the hires they mentor. New hires' chat questions reach the
KT planner and knowledge risk only as counts ("asked 3 times by new hires"), never with a name or the words.

**Nobody manages their own onboarding.** A hire whose own role is `manager` or `admin` (a new engineering manager being
onboarded) is still the hire on their own onboarding: every manager-only route about it (board tasks and milestones,
checkpoints, access requests, pre-boarding links, the first task, completion) answers 403 to them, as `PATCH /api/tasks/{id}`
already did. They read and move their own cards like any hire (`app/api/lifecycle.py` `_hire`; `tests/test_board.py`).

**Handover successors are checked on every path that moves work.** `POST /api/handovers/{id}/transfer`, `PATCH` with
`reassign` (including the successor already stored on the plan) and `POST …/complete` all require an active member of the
project who can hold the work (expert, manager or admin), not the leaver. Completing with `deactivate` (admins) is refused
with 409 `held_elsewhere` while the leaver still manages or mentors an active hire, has open questions, access items or
feedback in another project of the organisation.

**Agents' proposals** (docs/AGENTS.md). `rerecord` and `doc_edit` approvals are decided by managers (and admins) through
`POST /api/approvals/{id}/decide {decision: approved|rejected, text?}` like any approval; a `template_change` only by an admin
(403 for a manager), because it makes a new template version, which only admins may do. Approving a `doc_edit` never writes to
a repository: for code it is a suggestion its owner applies.

**Deactivated members** (`POST /api/org/members/{user_id}/deactivate`) are refused on every signed-in route with 403
`deactivated`, whatever their role; this matrix describes active members.

**Terms not accepted yet** ([specs/legal-consent.md](specs/legal-consent.md)). By default the API refuses nothing over it: the
web app asks at sign-in (`GET /api/legal/status`). With `LEGAL_ENFORCE=true`, a member who has not accepted the current terms
and privacy policy gets 403 `legal_required` on every signed-in route except `/api/me`, `/api/me/leave`, `/api/legal/*`,
`/api/notifications`, `/api/live`, and (admins) `/api/org/export`, `DELETE /api/org` and `/api/org/legal/*`; this matrix
describes members who have accepted, as everyone in the demo has.

**Notes column** (read by the test): `stream` is not called for allowed roles (it streams); `last` runs after every other
route (it changes who the caller is); `final` runs after those (it rebuilds the demo); `demo-off` routes answer 404 in demo mode by design, so the test only checks that
nobody gets through; `404-ok` means the demo has no such resource, so 404 counts as allowed.

| Method | Path | admin | manager | expert | new_hire | executive | Notes |
|---|---|---|---|---|---|---|---|
| GET | /health | public | public | public | public | public | liveness |
| GET | /health/ready | public | public | public | public | public | readiness |
| GET | /api/config | public | public | public | public | public | what the web app needs before sign-in |
| POST | /api/client-errors | public | public | public | public | public | the web app's crash reports (sendBeacon, so no sign-in): rate-limited per address, 16 KB, logged redacted, never stored; 204 |
| POST | /api/signup | n/a | n/a | n/a | n/a | n/a | Firebase identity; not used in demo mode; 422 `legal_required` unless the request accepts the terms and privacy policy |
| POST | /api/invitations/accept | n/a | n/a | n/a | n/a | n/a | Firebase identity; not used in demo mode; 422 `legal_required` unless the request accepts the terms and privacy policy |
| GET | /api/me | yes | yes | yes | yes | yes | |
| POST | /api/me/leave | yes | yes | yes | yes | yes | last |
| PATCH | /api/me | yes | yes | yes | yes | no | your own GitHub username (409 `github_taken` when someone else in the organisation has it); executives are read-only |
| GET | /api/projects | yes | yes | yes | yes | yes | the caller's projects |
| POST | /api/projects | yes | yes | no | no | no | |
| GET | /api/projects/{project_id} | yes | yes | yes | yes | yes | members only |
| POST | /api/projects/{project_id}/invitations | yes | yes | no | no | no | admins and executives are invited by admins only |
| DELETE | /api/projects/{project_id}/invitations/{invitation_id} | yes | yes | no | no | no | 404-ok |
| PATCH | /api/projects/{project_id}/members/{user_id} | yes | yes | no | no | no | a project member's GitHub username; audited, and the person is told |
| GET | /api/github/install-url | yes | yes | no | no | no | |
| POST | /api/github/callback | yes | yes | no | no | no | |
| GET | /api/github/installations | yes | yes | no | no | no | |
| GET | /api/github/repositories | yes | yes | no | no | no | |
| POST | /api/projects/{project_id}/repositories | yes | yes | no | no | no | |
| POST | /api/projects/{project_id}/documents | yes | yes | yes | no | no | |
| GET | /api/documents/{document_id} | yes | yes | yes | yes | yes | 404-ok |
| GET | /api/documents/{document_id}/file | yes | yes | yes | yes | yes | 404-ok |
| DELETE | /api/projects/{project_id}/documents/{document_id} | yes | yes | yes | no | no | 404-ok |
| POST | /api/projects/{project_id}/onboardings | yes | yes | no | no | no | |
| POST | /api/chat | yes | yes | yes | yes | no | |
| GET | /api/chat/history | yes | yes | yes | yes | yes | the caller's own history |
| POST | /api/chat/{message_id}/feedback | own | own | own | own | no | 404-ok (someone else's message is not found) |
| POST | /api/meetings | yes | yes | yes | yes | no | |
| POST | /api/meetings/{meeting_id}/schedule | yes | yes | no | no | no | managers, whoever booked it, or an attendee |
| POST | /api/meetings/{meeting_id}/cancel | yes | yes | no | no | no | managers or whoever booked it |
| POST | /api/meetings/{meeting_id}/recording/import | yes | yes | yes | no | no | managers, experts or whoever booked it; refused (422 `consent_required`) until the recording consent is confirmed |
| POST | /api/meetings/{meeting_id}/recording/upload | yes | yes | yes | own | no | demo-off; starts a signed upload for managers, experts or whoever booked the session; refused (422 `consent_required`) until the recording consent is confirmed |
| POST | /api/meetings/{meeting_id}/recording | yes | yes | yes | no | no | demo-off |
| POST | /api/meetings/{meeting_id}/recording/complete | yes | yes | yes | own | no | demo-off; completes an upload started by this person for this session |
| GET | /api/meetings | yes | yes | yes | yes | yes | |
| GET | /api/meetings/{meeting_id}/notes | yes | yes | yes | yes | yes | the transcript only for managers |
| GET | /api/meetings/{meeting_id}/followups | yes | yes | no | no | no | |
| GET | /api/meetings/{meeting_id}/recording | yes | yes | yes | yes | yes | |
| DELETE | /api/meetings/{meeting_id}/recording | yes | yes | no | no | no | the session's title typed to confirm; removes the video, transcript, notes, KT links and indexed windows; the session stays as held |
| POST | /api/meetings/{meeting_id}/recording/consent | yes | yes | yes | no | no | managers, experts or whoever booked it confirm that everyone in the session was told it is recorded and agreed; the first confirmation is kept; audited |
| POST | /api/meetings/{meeting_id}/recording/removal-requests | yes | yes | yes | yes | no | anyone on the project asks for the recording to be removed; the project's managers are told; nothing is removed by the request |
| POST | /api/meetings/{meeting_id}/recording/removal-requests/{request_id}/decline | yes | yes | no | no | no | 404-ok (a request that is not this session's is not found); whoever asked is told |
| GET | /api/recording-removal-requests | yes | yes | yes | yes | yes | managers and admins see the project's requests; everyone else only their own |
| GET | /api/code/files | yes | yes | yes | yes | yes | |
| GET | /api/code/explanations | yes | yes | yes | yes | yes | |
| GET | /api/code/file | yes | yes | yes | yes | yes | |
| GET | /api/coverage | yes | yes | yes | yes | yes | |
| GET | /api/tasks | yes | yes | yes | yes | yes | managers see every task, proposed ones included; others only approved tasks of their own active onboarding or a mentee's |
| PATCH | /api/tasks/{task_id} | yes | yes | no | own | no | the hire moves their own task; managers decide submitted ones |
| GET | /api/approvals | yes | yes | no | no | no | |
| POST | /api/approvals/{approval_id}/decide | yes | yes | no | no | no | |
| GET | /api/reviews/{review_id} | yes | yes | own | own | no | 404-ok; the task's hire, their mentor, managers |
| GET | /api/experts/requests | yes | yes | yes | no | no | |
| POST | /api/experts/requests/{request_id}/answer | yes | yes | yes | no | no | the expert it was routed to, or a manager |
| GET | /api/dashboard | yes | yes | no | no | no | KT coverage by module, KT gaps and the topics several hires asked; each hire's row is name, day and on-track status from the lifecycle clock, with no activity counts |
| GET | /api/runs | yes | yes | no | no | no | |
| GET | /api/notifications | yes | yes | yes | yes | yes | the caller's own |
| POST | /api/notifications/read | yes | yes | yes | yes | yes | the caller's own |
| GET | /api/live | yes | yes | yes | yes | yes | stream |
| GET | /api/blobs/{token} | public | public | public | public | public | the signed link is the permission |
| GET | /api/knowledge-risk | yes | yes | no | no | no | |
| GET | /api/kt-plan | yes | yes | yes | no | no | experts see their own sessions |
| POST | /api/kt-plan/schedule | yes | yes | yes | no | no | experts book only sessions they present |
| GET | /api/tour | yes | yes | yes | yes | yes | |
| GET | /api/audit | yes | no | no | no | no | |
| GET | /api/audit.csv | yes | no | no | no | no | |
| GET | /api/audit/facets | yes | no | no | no | no | the filter menus: each action and person in the log, with counts; recorded as `audit.viewed` |
| GET | /api/integrations | yes | yes | yes | yes | yes | |
| GET | /api/integrations/status | yes | no | no | no | no | is each integration set up and working; live checks cached five minutes; no secret in it |
| GET | /api/integrations/google/start | yes | yes | no | no | no | demo-off |
| GET | /api/integrations/google/callback | public | public | public | public | public | demo-off; the signed OAuth state is the permission |
| DELETE | /api/integrations/google | yes | yes | no | no | no | |
| PUT | /api/integrations/calendly/link | yes | yes | yes | yes | no | the caller's own link |
| DELETE | /api/integrations/calendly/link | yes | yes | yes | yes | no | the caller's own link |
| GET | /api/scheduling/links | yes | yes | yes | yes | yes | |
| POST | /webhooks/github | n/a | n/a | n/a | n/a | n/a | GitHub's signature |
| POST | /webhooks/calendly | n/a | n/a | n/a | n/a | n/a | Calendly's signature |
| POST | /internal/outbox/flush | n/a | n/a | n/a | n/a | n/a | Cloud Scheduler (OIDC) |
| POST | /internal/recordings/poll | n/a | n/a | n/a | n/a | n/a | Cloud Scheduler (OIDC) |
| POST | /internal/audit/purge | n/a | n/a | n/a | n/a | n/a | Cloud Scheduler (OIDC) |
| GET | /api/demo/people | public | public | public | public | public | demo mode only |
| POST | /api/demo/login | public | public | public | public | public | demo mode only |
| POST | /api/demo/invitations/accept | public | public | public | public | public | demo mode only; 404-ok |
| POST | /api/demo/pull-request | no | no | no | own | no | demo mode only: the hire's own code task |
| POST | /api/demo/advance | yes | yes | no | no | no | demo mode only: the demo clock |
| POST | /api/demo/reset | yes | yes | no | no | no | demo mode only: the seeded organisation again, at day 0; final |
| GET | /api/lifecycle/config | public | public | public | public | public | feature flags and floors |
| GET | /api/home | yes | yes | yes | yes | yes | a role-specific payload |
| GET | /api/setup | yes | no | no | no | no | |
| PATCH | /api/setup | yes | no | no | no | no | |
| GET | /api/teams | yes | yes | no | no | no | |
| POST | /api/teams | yes | yes | no | no | no | |
| PATCH | /api/teams/{team_id} | yes | yes | no | no | no | |
| GET | /api/programs/templates | yes | yes | no | no | no | |
| POST | /api/programs/templates | yes | no | no | no | no | |
| PATCH | /api/programs/templates/{template_id} | yes | no | no | no | no | makes a new version |
| POST | /api/onboardings/enrol | yes | yes | no | no | no | |
| GET | /api/onboardings/{onboarding_id}/program | yes | yes | own | own | no | the hire, their mentor, managers |
| PATCH | /api/onboardings/{onboarding_id}/milestones/{key} | yes | yes | no | no | no | |
| GET | /api/board/hires | yes | yes | no | yes | no | the milestone board's hire picker: every active hire of the project for managers and admins; a hire gets only their own row |
| GET | /api/onboardings/{onboarding_id}/board | yes | yes | no | own | no | the hire and the project's managers only: a mentor gets 403 |
| POST | /api/onboardings/{onboarding_id}/board/tasks/{task_id}/move | yes | yes | no | own | no | the hire moves their own cards between columns (Done = submitted for sign-off); managers also sign off, reopen and move between milestones; a task waiting in Approvals answers 409 |
| POST | /api/onboardings/{onboarding_id}/board/tasks | yes | yes | no | no | no | managers add a task to a milestone |
| PATCH | /api/onboardings/{onboarding_id}/board/tasks/{task_id} | yes | yes | no | no | no | a task waiting in Approvals answers 409 |
| DELETE | /api/onboardings/{onboarding_id}/board/tasks/{task_id} | yes | yes | no | no | no | removes it from the board (rejected); a task waiting in Approvals answers 409 |
| POST | /api/onboardings/{onboarding_id}/board/milestones | yes | yes | no | no | no | |
| PUT | /api/onboardings/{onboarding_id}/board/milestones/order | yes | yes | no | no | no | |
| PATCH | /api/onboardings/{onboarding_id}/board/milestones/{milestone_key} | yes | yes | no | no | no | 404-ok |
| DELETE | /api/onboardings/{onboarding_id}/board/milestones/{milestone_key} | yes | yes | no | no | no | 404-ok |
| GET | /api/onboardings/{onboarding_id}/access | yes | yes | own | own | no | the hire, their mentor, managers, item owners |
| POST | /api/onboardings/{onboarding_id}/access | yes | yes | no | no | no | |
| PATCH | /api/onboardings/{onboarding_id}/access/{item_id} | yes | yes | own | no | no | the item's owner or a manager |
| POST | /api/onboardings/{onboarding_id}/preboarding-link | yes | yes | no | no | no | the token is in this response only; body `{email: true}` also emails it when SMTP is set up (after the response, never stored) |
| GET | /api/preboarding/{token} | public | public | public | public | public | the link's token is the permission; it never appears in a log (app/logsafe.py); `first_week` comes from the template and the hire's own KT invitations |
| POST | /api/preboarding/{token}/acknowledge | public | public | public | public | public | the link's token is the permission |
| GET | /api/preboarding/{token}/env-check.sh | public | public | public | public | public | the link's token is the permission |
| POST | /api/preboarding/{token}/env-check | public | public | public | public | public | the link's token is the permission |
| GET | /api/onboardings/{onboarding_id}/first-task/shortlist | yes | yes | no | no | no | |
| POST | /api/onboardings/{onboarding_id}/first-task | yes | yes | no | no | no | |
| POST | /api/tasks/{task_id}/presubmit-review | no | no | no | own | no | the hire's own task; never shown to anyone else |
| POST | /api/ask/route | yes | yes | yes | yes | no | |
| POST | /api/ask/free | yes | yes | yes | yes | no | a free-form request: the Supervisor picks the Guide (an answer) or proposes what the Coach, Planner or Scout would do |
| POST | /api/verified-answers | yes | yes | yes | no | no | |
| GET | /api/verified-answers | yes | yes | yes | no | no | |
| GET | /api/experts/preferences | yes | yes | yes | no | no | the caller's own |
| PATCH | /api/experts/preferences | yes | yes | yes | no | no | the caller's own |
| GET | /api/onboardings/{onboarding_id}/areas | yes | yes | own | own | no | the hire, their mentor, managers |
| PATCH | /api/onboardings/{onboarding_id}/areas/{area} | no | no | no | own | no | the hire's own self-assessment |
| GET | /api/onboardings/{onboarding_id}/blockers | yes | yes | own | own | no | the hire, their mentor, managers |
| POST | /api/blockers/{blocker_id}/resolve | yes | yes | own | own | no | the hire, their mentor, managers |
| GET | /api/onboardings/{onboarding_id}/summaries | yes | yes | no | own | no | the hire and managers |
| POST | /api/onboardings/{onboarding_id}/summaries | yes | yes | no | own | no | the hire and managers |
| POST | /api/feedback | yes | yes | yes | yes | no | |
| GET | /api/feedback | yes | yes | yes | yes | yes | managers see all; experts and hires what they sent and their queue; executives counts only (by target kind, kind and status: never a note or an author) |
| PATCH | /api/feedback/{feedback_id} | yes | yes | own | no | no | the item's owner or a manager |
| GET | /api/onboardings/{onboarding_id}/checkins | yes | yes | no | own | no | the hire and managers |
| POST | /api/onboardings/{onboarding_id}/checkins | yes | yes | no | own | no | each writes their own half |
| POST | /api/surveys | no | no | no | own | no | the hire's own, anonymous |
| GET | /api/surveys/summary | yes | yes | no | no | yes | aggregates of five or more |
| GET | /api/onboardings/{onboarding_id}/completion | yes | yes | no | own | no | the hire and managers |
| POST | /api/onboardings/{onboarding_id}/completion | yes | yes | no | no | no | a manager signs off |
| GET | /api/handovers | yes | yes | no | no | no | |
| POST | /api/handovers | yes | yes | no | no | no | |
| PATCH | /api/handovers/{handover_id} | yes | yes | no | no | no | |
| GET | /api/handovers/{handover_id}/transfer | yes | yes | no | no | no | |
| POST | /api/handovers/{handover_id}/transfer | yes | yes | no | no | no | |
| POST | /api/handovers/{handover_id}/attachments | yes | yes | no | no | no | |
| DELETE | /api/handovers/{handover_id}/attachments/{attachment_id} | yes | yes | no | no | no | 404-ok |
| POST | /api/handovers/{handover_id}/complete | yes | yes | no | no | no | |
| GET | /api/onboardings/{onboarding_id}/visibility | yes | yes | no | own | no | the hire, and managers |
| GET | /api/reports/onboarding | yes | yes | no | no | yes | `team_id` narrows it to one team; figures per team use the same floors; the key-person figure is a count, no names |
| GET | /api/reports/onboarding.csv | yes | yes | no | no | yes | the same, with team rows |
| GET | /api/org/export | yes | no | no | no | no | JSON, no credentials; the survey as floored aggregates only (no response rows, no `survey_days`) |
| DELETE | /api/org | yes | no | no | no | no | the organisation's name must be typed to confirm; removes rows, stored files, LangGraph checkpoints and outbox events |
| POST | /api/org/people/erase | yes | no | no | no | no | erases one person's personal data (their email typed again to confirm); 409 for oneself or the last active admin |
| GET | /api/legal/status | yes | yes | yes | yes | yes | the caller's own acceptances: which current documents (terms, privacy policy, recording notice) they still need to accept |
| POST | /api/legal/accept | yes | yes | yes | yes | yes | records the caller's own acceptance of the current versions; executives too (it changes nothing of the organisation's data); audited `legal.accepted` |
| GET | /api/org/legal | yes | no | no | no | no | the organisation's data-processing agreement: accepted or not, by whom, when, which version |
| POST | /api/org/legal/dpa | yes | no | no | no | no | accepts the current data-processing agreement for the organisation; one record per version (a second click answers the first); audited `legal.dpa_accepted` |
| GET | /api/org/legal/dpa/record | yes | no | no | no | no | the acceptance as a text file (version, time, who); 404 `dpa_not_accepted` before it is accepted |
| GET | /api/org/members | yes | no | no | no | no | members with role, status and projects |
| PATCH | /api/org/members/{user_id} | yes | no | no | no | no | 409 when no active admin would be left |
| POST | /api/org/members/{user_id}/deactivate | yes | no | no | no | no | 409 for oneself or the last active admin |
| POST | /api/org/members/{user_id}/reactivate | yes | no | no | no | no | |
| GET | /api/agents | yes | yes | no | no | yes | the agent roster: modes, recent runs, pending proposals (docs/AGENTS.md) |
| GET | /api/agents/runs | yes | yes | no | no | no | run summaries never quote a hire's question or a private review |
| GET | /api/agents/runs/{run_id} | yes | yes | own | own | no | one run and its shared trace: the asker's own run, and any run of the project for its managers; a private run (pre-submit review) its own user alone |
| PATCH | /api/agents/{agent_key} | yes | no | no | no | no | ai or rules for the organisation; the Supervisor and the Librarian: 409 |
| POST | /api/agents/tick | yes | yes | no | no | no | demo and self-hosted: the daily tick now (404 on Google Cloud) |
| GET | /api/voice/config | yes | yes | yes | yes | yes | reports whether Voice is configured; a new hire needs an active onboarding in the selected project |
| GET | /api/voice/sessions | yes | yes | yes | yes | yes | returns only the caller's own voice history for a project they may access |
| GET | /api/voice/sessions/{session_id} | no | no | no | own | no | 404-ok; the hire's own session only |
| POST | /api/voice/sessions | no | no | no | yes | no | a new hire with an active onboarding may prepare a live session; 503 when Voice is unconfigured |
| POST | /api/voice/sessions/{session_id}/activate | no | no | no | own | no | 404-ok; starts the hire's prepared Agora session |
| POST | /api/voice/sessions/{session_id}/sync | no | no | no | own | no | 404-ok; refreshes provider state for the hire's session |
| POST | /api/voice/sessions/{session_id}/transcript | no | no | no | own | no | 404-ok; records a turn for the hire's session |
| POST | /api/voice/sessions/{session_id}/stop | no | no | no | own | no | 404-ok; stops the hire's session and saves its recap |
| POST | /api/voice/tools/{name} | n/a | n/a | n/a | n/a | n/a | Agora custom tool route: signed, short-lived, session-scoped server credential rather than browser sign-in |
| POST | /api/voice/actions/{action_id}/approve | no | no | no | own | no | 404-ok; explicit confirmation executes only the hire's pending proposal |
| POST | /api/voice/actions/{action_id}/decline | no | no | no | own | no | 404-ok; declines only the hire's pending proposal |
| POST | /internal/lifecycle/tick | n/a | n/a | n/a | n/a | n/a | Cloud Scheduler (OIDC) |
