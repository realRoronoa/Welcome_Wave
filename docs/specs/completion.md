# Completion review

**Problem.** Programs never ended; nobody signed anything off.

**Users.** Managers sign off; the hire sees the same review.

**Data.** `onboarding_programs.completed_at`, `completion` (decision, note, by, at, unmet, extend_days?); the onboarding's
status becomes `completed`.

**API.** `GET /api/onboardings/{id}/completion` → `{completion: {day, duration_days, criteria: [{key, label, met, evidence,
why}], ready, signed_off, completed_at, draft: {decision, extend_days, note, ai, fingerprint, unmet}, explain}}` (hire,
managers). `POST …/completion {decision: complete|extend, note?,
extend_days?}` → the record (managers).

**Logic.** Criteria: every milestone done or skipped; the final check-in by both; no open blockers; a merged change in an
area; access requests settled. The manager may sign off with criteria unmet: the record keeps which. Extending adds days to
the program. Completing ends detection and removes the hire from active lists and the agents' plans.

**The Planner.** The draft recommends completing when every criterion is met, else extending by 14 days. The model writes
the note (checked), or the rules do. It is stored with the program (`completion_draft`) with a fingerprint of the criteria,
and written again only when they change: by the daily tick in a program's last week (the manager is told once,
`program.completion_due`), or on `GET` (`completion.requested`). The manager decides. Signing off (`complete`) publishes
`program.completed`. The Planner then reads the completed programs of the template's track (3 or more) and survey
questions that 5 or more people of a team answered on one survey day, and may propose a `template_change` (`programs-and-templates.md`).

**Edge cases.** Signing off twice → 409; extend without days → 422; nobody signs off their own program.

**The agent.** When a draft is written, the Planner thinks (`planner.draft_completion`): it reads `program` behind the
counted criteria and recommends complete or extend, judging each unmet criterion (`draft.assessments`). Code keeps a
"complete" with a criterion unmet only with a judgement of it (else the rules' recommendation), caps an extension at 7 to 30
days, and checks the note. The draft adds `agent: {run_id, steps}`; `GET .../completion?run_id=` streams it. The manager
signs off. Without a loop: every criterion met → complete, else extend by 14 days.

**Tests.** `test_lifecycle_api.py::test_completion_review_and_sign_off`; `test_thinking_agents.py` (an unmet criterion needs a judgement; the extension cap).
