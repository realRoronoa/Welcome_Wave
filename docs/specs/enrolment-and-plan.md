# Enrolment and the 30/60/90 plan

**Problem.** Starting a hire took an invitation, a plan request and a checklist kept elsewhere. One step should do all of
it, and the plan should still be the manager's decision.

**Users.** Managers enrol and approve; the hire, the mentor and managers read the program.

**Data.** `onboarding_programs` (1:1 with `onboardings`), `onboarding_milestones`, `access_items`, `acknowledgements`.

**API.**
- `POST /api/onboardings/enrol` `{project_id, email, name, github_login?, start_date, template_id, team_id?, mentor_id?,
  focus_area?, role_title?}` → 201 `{onboarding, program, invitation: null, next, plan}` (manager, admin): a draft.
- The plan waits in Approvals as one `task_plan` approval whose `plans[]` entry has `phases` (`[{key, title, from_day,
  to_day, milestones: [keys], tasks: [task ids]}]`: Days 1–30, 31–60, 61–90, thirds of other lengths), `milestones` and
  `enrolment`. `POST /api/approvals/{id}/decide {approve: [task ids], edits?, milestones?: [keys to keep]}` returns
  `result.enrolments: [{onboarding_id, milestones, access_items, tasks, invitation: {id, email, expires_at, link, sent: false,
  note} | null}]`.
- `GET /api/onboardings/{id}/program` → `{profile, plan, milestones, plan_items, access, acknowledgements,
  environment_checks, preboarding, first_task}` (hire, mentor, managers).
- `PATCH /api/onboardings/{id}/milestones/{key}` `{status: pending|done|skipped, note?}` → `Milestone` (managers).
- `GET /api/onboardings/{id}/access` → `{access}` (hire, mentor, managers, item owners); `POST …/access
  {system, owner_id?}` → 201 `AccessItem` (managers); `PATCH …/access/{item_id} {status: requested|granted|not_needed}`
  (the item's owner or a manager).

**Logic.** Enrolment finds the person by email, or keeps an unbound user row for someone not in the organisation yet. It
starts the onboarding and a DRAFT program, which holds only the template's documents to acknowledge: no milestones, no access
requests, no invitation. It then publishes `onboarding.started` with the template's plan.

The Planner (the Supervisor's `planner` node) drafts ONE 30/60/90 plan:
- the template's milestones, scaled to the program;
- the Task agent's tasks (or the template's plan with no model, or in "rules" mode);
- the template's plan items the agent left out;
- a task for each milestone of the second and third phase.

On approval, the milestones the manager kept, the access requests (their owners are told), the mentor's notice and the
invitation are created, and `plan_approved_at` is set. **No email is sent**: the invitation is created, and its sign-in link
is returned for the manager to share. Rejecting every task leaves the draft as it is.

Plan status is read back from those approvals: `approved`, `awaiting_approval`, `drafting` or `not_started`. Auto milestones
are marked done, with their evidence, the first time it is seen. "First change merged" (and an area's merged evidence) needs
a merge: a `pr.merged` event, or the demo's merge. An approved review is not a merge. When the latest environment check
fails, "Development environment passes its check" is shown as pending, with `evidence.regressed`.

**UI states.** Enrolment form; program timeline (milestones with `state`: done, skipped, overdue, due_soon, upcoming); plan
banner (awaiting approval links to Approvals); access checklist with owners.

**Edge cases.** Already onboarding in this project → 409. Inactive or foreign template, a team of another project, a mentor
who is not an expert/manager member, or the hire as their own mentor → 422. Proposals waiting for approval are counted in
`plan.proposed_tasks`, never listed to the hire (the GET /tasks rule).

**The agent.** For an enrolment the Planner thinks (`planner.draft_plan`): it reads `program` (the template's milestones
and plan, scaled), researches the focus area (kt_coverage, search_knowledge, read_file, issues, tests_for, recent_changes,
code_owners) and drafts its tasks, each with acceptance criteria, a due day, its files, an open issue for a code task where
one fits, and the milestone it reaches. Code keeps due days inside the program, real paths and open issues only, at most 8
tasks of its own, and adds the template's milestones, the plan items it left out and a task per later milestone (the same
`plan_30_60_90`). The plan's entry in the task_plan approval adds `agent: {run_id, rationale, reasons: [{title, why,
milestone, sources}], steps}`. A manager approves it; nothing else does. Without a loop: the Task agent's or the template's
plan, as above.

**Tests.** `test_lifecycle_api.py`: enrolment (a draft, then the plan's approval), AI-off plan, milestones and access; `test_thinking_agents.py` (the Planner's plan: bounds, real issues, never approved by itself).
`test_agents_model.py::test_only_a_merge_completes_the_first_change`;
`test_lifecycle_postgres.py::test_enrolment_and_every_visible_field_on_postgres`.
