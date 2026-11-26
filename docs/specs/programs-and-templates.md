# Program templates and teams

**Problem.** Every hire's program was improvised. A program needs a repeatable shape per role and level that a company can
change without rewriting history.

**Users.** Admins write templates; managers pick one when enrolling; everyone sees the program it produced.

**Data.** `program_templates` (key, version, title, role_track, level, duration_days, milestones, acknowledgements,
access_items, plan_items, active): one active version per key (partial unique index). `teams` (project_id, name).

**API.**
- `GET /api/programs/templates?include_inactive=false` → `[ProgramTemplate]` (manager, admin). An organisation with no
  templates gets the 16 defaults first.
- `POST /api/programs/templates` `{key, title, role_track, level, duration_days?, milestones?, acknowledgements?,
  access_items?, plan_items?}` → 201 `ProgramTemplate` (admin). 409 if the key exists.
- `PATCH /api/programs/templates/{id}` `{title?, duration_days?, milestones?, acknowledgements?, access_items?, plan_items?}`
  → the new version (admin). 409 unless `{id}` is the active version.
- `GET /api/teams?project_id=` → `[Team]`; `POST /api/teams {project_id, name}` → 201; `PATCH /api/teams/{id} {name}`
  (manager, admin).

**Logic.** Defaults: Backend, Frontend, SRE, Data × Junior, Mid, Senior, Staff. Common milestones (pre-boarding, environment,
access, team, first PR opened and merged, check-ins at 30/60/90, an area at working, a merged change in the focus area)
plus one per track and per level (pairing for Juniors, design review for Seniors and Staff, a written technical direction
for Staff). Days are days of a 90-day program; another length scales them (`round(day × length / 90)`), so check-ins of a
120-day program fall on 40/80/120. A change makes a new version and leaves programs on the version they started with
(`template_version`). Why versions: a program must stay explainable after its template changes.

**Template improvements** (the Planner, `docs/AGENTS.md`). After a completion, rules over the template track's completed
programs (at least 3) and the survey (questions that 5 or more people of a team answered on one survey day, only) may propose a `template_change`
`{template_id, template_key, template_title, template_version, changes: [{field, from, to, reason}], evidence, decider: "admin",
explain}`. The rules:
- a blocker most of the cohort hit in its first 14 days → a plan item that prevents it;
- a milestone reached 5 or more days after its day → its day moved;
- programs that ran 7 or more days over → a longer program;
- a survey question averaging below 3.5 → a plan item.

The model writes each reason (checked). An admin approves it (403 for a manager), which makes a new version, exactly as the
PATCH does; running programs keep their version.

**UI states.** List by track and level; an inactive version greyed with its successor; editing shows "makes version N+1".

**Edge cases.** Duplicate key → 409. Changing an old version → 409. Duration 14 to 365 days. Milestone keys are snake case;
the auto-checked ones are fixed names (`templates.AUTO_MILESTONES`).

**The agent.** With at least 3 completed programs of the track, the Planner thinks: it reads `cohort_evidence` (blockers
hit in the first 14 days, when each milestone was reached, how long programs ran, and survey questions only from groups of 5
or more) and proposes changes, each citing evidence rows by id. Code keeps a change only on a field a template has (a new
plan item, a milestone's day, the length), inside its bounds, citing rows the tool gave; at most 4. The template_change
payload adds `agent: {run_id, steps}`. An admin approves. Without a loop: the template rules.

**Tests.** `test_lifecycle_api.py::test_teams_and_template_versions`, `test_lifecycle_rules.py::test_templates_scale_with_their_length`, `test_thinking_agents.py` (evidence, bounds and the admin's decision).
`test_lifecycle_postgres.py::test_every_method_round_trips`.
