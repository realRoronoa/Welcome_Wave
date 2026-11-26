# The first task and the private pre-submit review

The Scout (`app/agents/scout.py`) runs both routes as the `scout` node of the Supervisor's chat graph; the pre-submit review is
the Reviewer's, privately (`presubmit.requested`).

**Problem.** A first task picked by hand often lands in code nobody explained, with no tests, owned by one busy person.

**Users.** Managers shortlist and select; the hire gets the brief and may review their own change privately.

**Data.** `onboarding_programs.first_task` (the brief); the selected task is a normal `tasks` row.

**API.**
- `GET /api/onboardings/{id}/first-task/shortlist` → `{first_task_shortlist: {focus_area, candidates: [{id, kind, title,
  task_id, issue, status, files, score, factors, why, evidence, explain}], excluded: [{…, why, explain}], weights, selected, explain}}`
  (managers).
- `POST /api/onboardings/{id}/first-task {candidate_id: "issue:<n>" | "task:<id>"}` → the brief `{candidate_id, title, issue,
  files, score, why, owners, tests, kt_clips, acceptance_criteria, next, summary, explain, task_id, selected_at, selected_by}`:
  `summary` is the Scout's brief (written by the model, checked; by the rules with the AI off or in "rules" mode).
- `POST /api/tasks/{id}/presubmit-review {diff? | pr_number?}` → `{task_id, private: true, files, recommendation, summary,
  findings, questions, dropped_findings, why, explain}` (the task's hire only). `pr_number` must be the task's own pull
  request (422 otherwise), or the hire pastes the diff.

**Logic.** Candidates: open issues labelled for newcomers and the hire's open code tasks (an issue they already have a task
for is that task). Files: a task's paths, else the code a search for the title finds (similarity ≥ 0.2). Score =
0.30 focus (every non-test file in the focus area) + 0.25 × share of code files a KT call explained + 0.20 small (≤ 3
files) + 0.15 tested (a test file for one of them) + 0.10 quiet (nobody else's open task touches them; the RepoSource port
has no commit history, so open work by others stands in for "no commits by others in 14 days"). Files of a single-owner
critical area (knowledge risk: band critical, one holder) exclude the candidate, with the reason. Top 3 shown, every
scoring factor listed. Selecting an issue creates an approved `first_pr` task; selecting a proposed task records the
brief and says to approve it in Approvals. Either way the candidate's files become the task's `related_paths`, so its
merged change counts towards its area and the completion review.

The pre-submit review runs the Review agent on the hire's diff (or a draft PR) through a private repo stand-in. Nothing is
stored, nothing goes to Approvals, nothing is audited, and the manager never sees it. With the AI off the rule checks still
run. Its run record says "A private review ran." and nothing else; a failure is never an incident for the managers.

**UI states.** Shortlist cards with score bars and reasons; excluded list with why; brief page with clips and owners;
pre-submit panel with findings.

**Edge cases.** A candidate not on the shortlist → 422. Non-code task → 409. No diff and no PR → 422. Someone else's task → 404.

**The agent.** The Scout thinks (`app/agents/thinking.py`; docs/AGENTS.md, "The thinking specialists"). For a shortlist it
reads `first_task_candidates` (the candidates and factors above, and the excluded ones), explores the promising ones
(read_file, tests_for, kt_moments, kt_coverage, recent_changes, code_owners, program, issues, search_knowledge) and picks at
most 3, each with its reason (`agent_reason`), its files and the sources it read. Code keeps only real candidates (an open
issue's number, the hire's own task id), never an excluded one, and only indexed files outside single-owner critical areas.
The picks are stored with a fingerprint of the candidates (metric `scout.picks`): the manager and the hire's visibility page
show the same shortlist, with `agent: {run_id, rationale, steps, at}`, and no model runs again until the candidates change.
For the selected candidate the Scout reads its files, tests and KT moments and writes the brief (checked) and `first_steps`;
the brief adds `agent` (the shared steps), and the response `run_id` and `steps`. The selected brief's `files` (the Scout's
when it picked them) become the task's `related_paths`. The pre-submit review is the Reviewer's loop (pr_diff, read_file,
tests_for, kt_moments...): findings anchored to real changed lines, the rule checks always; it streams to the hire alone and
its run record keeps only `{summary: "A private review ran.", user_id, visibility: "private"}`. Every route takes an optional
`run_id` (a fresh UUID) to watch the steps live. Without a loop (AI off, offline demo, rules mode): the rules above.

**Tests.** `test_lifecycle_api.py` (shortlist reasons and exclusion, issue selection, privacy and AI off), `test_thinking_agents.py` (the Scout's picks and their checks, the brief, the private review's record).
`test_lifecycle_rules.py` (weights, diff parsing).
