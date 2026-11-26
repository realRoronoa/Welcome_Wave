# The onboarding lifecycle (Track C): index and shared rules

Programs, enrolment and the 30/60/90 plan, pre-boarding, tiered answer routing, the first task, progress, blockers,
summaries, feedback, check-ins, surveys, completion, handover, visibility, reporting, the demo clock and the permission
matrix. Code: `backend/app/lifecycle/` (services), `backend/app/api/lifecycle.py` (routes), `ports.LifecycleStore`
(`adapters/lifecycle_memory.py`, `adapters/lifecycle_pg.py`), migration `db/migrations/010_lifecycle.sql`.

| Capability | Spec |
|---|---|
| Templates, teams | [programs-and-templates.md](programs-and-templates.md) |
| Enrolment, the 30/60/90 plan, milestones, access | [enrolment-and-plan.md](enrolment-and-plan.md) |
| Pre-boarding portal | [preboarding.md](preboarding.md) |
| Tiered answer routing, verified answers, expert time | [answer-routing.md](answer-routing.md) |
| First task, pre-submit review | [first-task.md](first-task.md) |
| Progress by area | [progress-by-area.md](progress-by-area.md) |
| Blockers | [blockers.md](blockers.md) |
| Weekly summaries | [weekly-summaries.md](weekly-summaries.md) |
| Documentation feedback | [documentation-feedback.md](documentation-feedback.md) |
| Check-ins and surveys | [checkins-and-surveys.md](checkins-and-surveys.md) |
| Completion | [completion.md](completion.md) |
| Offboarding handover | [handover.md](handover.md) |
| Employee visibility | [visibility.md](visibility.md) |
| Leadership reporting | [reporting.md](reporting.md) |
| Role homes, setup | [homes-and-setup.md](homes-and-setup.md) |
| Demo clock, feature flags | [demo-clock-and-flags.md](demo-clock-and-flags.md) |
| The agents, the daily tick, proposals | [../AGENTS.md](../AGENTS.md) |
| Executive role, permissions, organisation export and deletion | [roles-and-org-data.md](roles-and-org-data.md) |

## Shared rules

- **Who sees a hire's data.** The caller must be related to the onboarding: the hire, their mentor (named on the
  program), or a manager of the project (admins included). Unrelated callers get 404; related callers whose relation may
  not use the route get 403. Every manager- or mentor-facing payload about a hire is built from the visibility registry.
- **Reasons.** Every ranked, routed or detected result carries `why: [...]` (and `evidence` where there is any): the UI
  shows them as they are.
- **Today.** Every program day, due date, escalation and report window uses `app/lifecycle/clock.py` (`Clock.today()` is
  the server's local date plus the demo offset, as `onboardings.start_date` and the dashboard count days). Program day 0
  is the start date. Link expiry uses the real clock.
- **AI off.** Nothing here needs the model: plans fall back to the template, routing goes to people, pre-submit reviews
  run the rule checks, summaries and reports are counted, not written.
- **Untrusted content.** Questions, notes, transcripts, README text and script output are stored and shown as data.
  Targets (mentor, CODEOWNERS owner), roles and approvals never come from them or from the model.
- **Live updates.** Postgres triggers announce topic `lifecycle` for the lifecycle tables; the in-memory store announces it
  through the same `LiveHub`. Notifications use `store.notify`; every write is audited with `store.audit`.

## Changes to the contract in docs/PLAN.md

| Where | Change | Why |
|---|---|---|
| §3 | Added `org_settings` (setup choices, minutes per avoided interruption, demo day offset) | Setup state, the editable estimate and the demo clock need a home per organisation |
| §3 | `program_templates` gains `title` and `plan_items` | The plan drafted when no model is set up comes from the template |
| §3 | `onboarding_programs` gains `plan_approved_at`, `first_task`, `survey_days`, `created_at` | Plan status, the selected brief, and "answered the survey" without linking answers to a person |
| §3 | `blocker_alerts` gains `subject` and `notified_stage` | One open alert per rule and subject; each escalation stage told once |
| §3 | `area_progress.evidence` holds the hire's own records (steps, clips watched) | Evidence the product cannot see by itself |
| §3 | `api_tokens` created, unused | The public API is deferred (PLAN §6 deferral order) |
| §4 | `PATCH /onboardings/{id}/access/{item_id}` instead of `PATCH …/access` | An item is changed by its owner; the path names it |
| §4 | Added `PATCH /feedback/{id}`, `GET/PATCH /experts/preferences`, `GET /lifecycle/config`, `GET /org/export`, `DELETE /org` | Owners close feedback; routing's interruption budget is per person; flags; no export or deletion existed |
| §4 | `GET /surveys/summary`, reports: admins and executives too | Executives read organisation-level aggregates |
| Agents | `supervisor.plan_tasks` proposes the event's `template_tasks` when the model raises `AIUnavailable` | The AI-off plan goes through the same `task_plan` approval as the Task agent's |
| Auth | `current_user` refuses executives every non-GET request (except leaving and reading notifications) | Read-only by construction, for every route, present and future |
| Routes | Admins may invite executives (`POST /projects/{id}/invitations`) | Otherwise the role could not be given |
| Migrator | `preboarding_lookup(text)` added to `DEFINER_FUNCTIONS` | The portal finds its link before the tenant is known |
| Seed | Vijay starts 12 days ago (was 2); an executive, three hires and a previous cohort are added | The brief's demo organisation; tests that assumed the old seed's shape were adjusted |
| Agents (round 2) | The lifecycle services run as the Supervisor's specialists (Guide, Planner, Scout, Coach, Keeper), with the daily tick, three new approval kinds, `explain` blocks, enrolment as a draft until its 30/60/90 plan is approved, and the review round's routing rules | `docs/AGENTS.md`, which lists every change to the API |
