# Weekly progress summaries

**Problem.** Weekly updates are written from memory and cite nothing.

**Users.** The hire and managers write and read them (mentors do not: the summary is the hire's report to the manager).

**Data.** `progress_summaries` (onboarding, week_start, body), one per week.

**API.** `GET /api/onboardings/{id}/summaries` → `{summaries: [{id, week_start, body: {week_start, day, lines: [{text,
citations: [{kind, ref, label}]}], why}, created_at}]}`; `POST /api/onboardings/{id}/summaries` → 201 the summary of the
current week (writing again replaces it).

**Logic.** The Coach writes them: on `POST .../summaries` (the current week, `summary.requested`) and in the daily tick, which
closes every week a hire has no summary of (up to 4 weeks back). The lines are counted from records, never written by a model: tasks finished, milestones reached, area levels, questions asked
(counted, not quoted), blockers opened and resolved, check-ins written, next week's milestones. Each line cites its records
(task, milestone, area, chat, blocker, check-in). `body.overview` is one line the model writes from those lines only (checked:
no number, link or address the lines lack), or the first lines themselves with the AI off; `body.explain` says which, and
`body.why` gains a line when a model wrote it. Weeks run Monday to Sunday on the lifecycle clock. Works with the AI off.

**UI states.** A list by week; each line expands to its sources. Empty week: "Nothing recorded this week."

**The agent.** For the newest closed week (and `POST .../summaries?run_id=`) the Coach writes the overview from the
numbered lines, citing them [n] (it may read hire_signals or program). Code keeps it only if every [n] is a line and the text
passes the checked writer; `body.agent` keeps the shared steps. Backfilled older weeks, and no loop: the checked writer.

**Tests.** `test_lifecycle_api.py::test_weekly_summaries_cite_their_sources`; `test_thinking_agents.py` (cited lines, an invented number refused).
