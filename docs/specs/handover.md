# Offboarding handover

**Problem.** When a senior leaves, what only they knew leaves with them, and the replacement starts from scratch.

**Users.** Managers and admins plan, transfer and complete it; only an admin may also deactivate the leaver's account. The
departing person sees their items on their home.

**Data.** `handovers` (project, user, last_day, plan jsonb, status draft|in_progress|done). No table of its own beyond that:
attachments, ticks, the transfer record and the completion record live in `plan` (`attachments`, `ticked`, `transfer`,
`completed`).

**API.**

| Route | Who | What |
|---|---|---|
| `GET /api/handovers?project_id=` | manager, admin | The project's handovers |
| `POST /api/handovers {project_id, user_id, last_day, run_id?}` → 201 | manager, admin | The Keeper drafts the plan |
| `PATCH /api/handovers/{id} {status?, done?, undone?, successor_id?, reassign?}` | manager, admin | Ticks, status (draft, in_progress; `done` only through completion: 422), successor, `reassign` = transfer |
| `GET /api/handovers/{id}/transfer` | manager, admin | The live transfer set (below) |
| `POST /api/handovers/{id}/transfer {successor_id}` | manager, admin | Move what Welcome Wave holds to the successor |
| `POST /api/handovers/{id}/attachments {kind: repo\|document\|link, ref?, url?, title?, note?, owner_id?}` → 201 | manager, admin | Attach a document the handover did not find |
| `DELETE /api/handovers/{id}/attachments/{attachment_id}` | manager, admin | Remove an attachment |
| `POST /api/handovers/{id}/complete {confirm, accept_gaps?, deactivate?}` | manager, admin; `deactivate` admin only (403) | Finish after a person typed the leaver's name |

**The transfer set.** Read live, so it shows what is still on the person now and what has moved to whom. Nine sections, each
item with a state: `moves` (the transfer hands it over), `todo` (a person acts) or `done`.

| Section | What it lists | How it is handed over |
|---|---|---|
| Code they own | Files CODEOWNERS gives them, alone or with others, with KT coverage, a GitHub link (default branch) and a link into Code and KT | The successor becomes owner of record. A file only they hold that KT explains less than half of is `todo` until explained or ticked |
| Documents | Repository docs they own; repository docs their KT sessions showed or explained; documents they uploaded; suggested doc wording waiting on them; attachments | Owner of record: the successor (an attachment may name another owner) |
| KT recordings and explanations | Ready recordings they spoke in or booked; every clip they explained (file, symbol, moment) | Stays searchable; the successor becomes the person to ask. An explanation whose code changed since is `todo`: re-record |
| KT sessions | Calendar sessions they host (created or present); the sessions planned for them to record (the Keeper's, else the KT planner's) | A hosted session after their last day is `todo` (rebook); a planned session is `todo` until booked or ticked |
| Mentees and hires they manage | Programs they mentor, onboardings they manage, their own onboarding if they are a hire; each with its open tasks and milestones still to come | Mentor and manager move (a manager to the successor when they may manage, else another manager of the project) |
| Questions routed to them | Open expert requests | Reassigned to the successor's inbox |
| Approvals | Pending re-recording or doc-edit proposals that name them; pending approvals only they could decide | `todo`: decide them, or add a manager |
| Access to grant | Access items they still have to grant | Owner moves |
| Documentation feedback | Open feedback they own | Owner moves |

**Gaps** (what would leave the successor starting from scratch): no active successor (blocker); a module (folder) only they
own with no recorded KT at all; files only they hold explained less than half; explanations out of date; KT sessions still to
book; sessions they host after their last day; CODEOWNERS still naming them (with the replacement lines; it closes when the
repository's CODEOWNERS changes, or when a person ticks `codeowners`); no document for the code they own; hires who would have
no manager (blocker); approvals nobody else decides; decisions that name them; their own open onboarding.

**Completeness.** `done / total` items; `moves` and `todo` counted too. The page shows the share done and what the transfer
would add.

**Transfer.** Idempotent: run again, it moves only what has landed on the person since. The successor is told what moved,
each mentee is told who their mentor is now, questions arrive in the successor's inbox. `plan.transfer` records who moved
what, when, by whom.

**Completion.** The caller types the leaver's name (`Meera` or `Meera (Senior)`, any case). Open gaps refuse it (409
`{code: open_gaps, gaps}`) unless `accept_gaps`; accepted gaps are kept in `plan.completed` and the audit log. It runs the
transfer, then marks the handover done. With `deactivate` (admins only) the leaver's account is deactivated: refused for your
own account, for the organisation's last active admin, and while another handover for them is open. A complete handover
refuses transfers and attachments (409) until reopened (`PATCH {status: in_progress}`).

**Audit.** `handover.created`, `handover.updated` (ticks counted, `reopened`), `handover.transferred` (to, counts),
`handover.attachment_added`, `handover.attachment_removed`, `handover.completed` (to, counts, accepted gap keys,
deactivated), and `member.deactivated {via: handover}`.

**Read-only code access.** Welcome Wave never writes to a repository: the CODEOWNERS change is a suggestion to copy into a
pull request.

**Edge cases.** One open handover per person and project (409); the successor and an attachment's owner must be active members
who can take it over (expert, manager, admin; 422); a repository attachment must be a file of the indexed repository (422); a
link must be https with a title (422); a document they own or uploaded is already listed (409).

**The agent.** The Keeper thinks: it reads `person_footprint` (files they own alone or with others, files orphaned if
they leave, the KT moments they explained, the sessions planned for them, counts of what waits on them), and kt_moments,
read_file, kt_coverage, who_knows and code_owners, then plans the KT sessions to record (`plan.kt_sessions`, the most urgent
first, each with its files and agenda), a successor and the note. Code keeps only the person's own files and a successor a
people tool named (else the rules' successor); the checklist stays the rules'. `plan.agent` keeps the shared steps; `POST
/api/handovers {run_id}` streams them. Without a loop: the KT planner's sessions and the checked writer's note.

**Tests.** `test_handover.py` (the transfer set, transfer, attachments, completion, who may), `test_handover_postgres.py`
(the two store writes under row-level security), `test_lifecycle_api.py::test_handover_plan_and_reassignment`,
`test_thinking_agents.py` (footprint files only, the successor check).
