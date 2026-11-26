# Blocker detection and escalation

**Problem.** A stuck newcomer often says nothing; the signals are in the data.

**Users.** The hire first, then the mentor, then the manager.

**Data.** `blocker_alerts` (rule, subject, evidence, stage, notified_stage, opened_at, resolved_at, resolved_by): one open
alert per rule and subject.

**API.** `GET /api/onboardings/{id}/blockers` → `{blockers: [{id, rule, subject, text, stage, opened_at,
business_days_open, resolved_at, evidence, why, explain}]}` (hire, mentor, managers). `POST /api/blockers/{id}/resolve {note?}` → the
blocker.

**Logic.** Rules, each with what it saw:
- `repeated_topic`: the same module (first code source of an answer, or a routed question's best match) asked about 3+
  times in 5 days. Evidence counts the questions (ids, times), never quotes them.
- `stalled_task`: in progress with no activity for 3 business days (Postgres: `tasks.updated_at`; memory: creation).
- `failing_ci`: 3+ failed check runs within 48 h on the newest reviewed commit of the hire's PR.
- `environment`: the environment check failing for more than 24 h (since the first failure of the current streak).
- `unresolved_review`: a needs-work review with blocker or major findings, not approved, older than 3 days.
Escalation by business days since opening: hire (0), mentor (1), manager (3: "2 more"); no mentor → the manager. Each stage
is notified once. An alert closes itself when its condition clears; a person resolving it keeps it quiet until the
condition clears once. Detection runs on reads (blockers, program, homes) and in the Coach's daily tick (`docs/AGENTS.md`).
The text counts its durations when it is read, in days (or business days), never a stored count: "has failed for 3 days
(docker)". The Coach sends the hire one private nudge per alert (notification kind `nudge`): a kind next step and who can
help, written by the model from the rule's evidence and checked, or written by the rules. On the hire's home, an open
environment blocker replaces the "fix your environment check" item, so the problem is shown once.

**UI states.** Open alerts with stage badge and evidence; resolved history.

**Edge cases.** Weekends do not count; detection stops for completed programs; resolving twice → 409.

**The agent.** On the daily tick the Coach thinks for each active hire, once a day (metric `coach.assessed`): it reads
`hire_signals` (de-identified: question topics counted, never quoted; task activity; CI runs; the environment check; review
findings; the open alerts with `latest_stage_allowed_now`; milestones) and decides whether the hire is stuck, why, the next
step, a nudge, and for each alert whether it is blocking and who should know now; it may add a concern tied to a signal.
Code keeps only the hire's open alerts and real signals, sends at most one nudge a day (to the hire, checked), and turns the
stage into a cap (metric `coach.decision`, honoured by `detect` for 2 days through `coach_caps`/`capped`): a stage only after
its business-day delay, in the order hire → mentor → manager, never below what was told. "Not blocking" holds the alert where
it is; a blocking alert the nudge does not cover still gets the rule's nudge. The Coach's reason shows in the alert's `why`
("The Coach judged it ..."). Without a loop: the rules above.

**Tests.** `test_lifecycle_api.py` (each rule, escalation order, quiet after resolve), `test_lifecycle_rules.py` (business days); `test_thinking_agents.py` (alert and signal checks, one nudge, the cap and the hold, de-identified signals).
