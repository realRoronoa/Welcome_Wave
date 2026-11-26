# Documentation feedback

**Problem.** Confusing docs, code and KT calls were noticed by newcomers and fixed by nobody.

**Users.** Any member flags; the target's owner works their queue; managers see all.

**Data.** `documentation_feedback` (target_kind doc|code|meeting|answer, target_ref, kind unclear|outdated, note, owner_id,
status open|resolved|dismissed, created_by).

**API.** `POST /api/feedback {project_id, target_kind, target_ref, kind, note?}` → 201 `{…feedback, why}`;
`GET /api/feedback?project_id=` → managers and executives: all; others: what they sent and their queue;
`PATCH /api/feedback/{id} {status}` (its owner or a manager; the sender is notified).

**Logic.** Owner: code → the CODEOWNERS owner (a person, not a team); doc → the uploader; meeting → whoever booked it; answer →
whoever verified it; none → the project's first manager. The target must exist in the project. The note is text.

**The Keeper** (`feedback.created`, published by `POST /api/feedback`). When a target has open unclear or outdated flags
from 2 or more different people (or 1 outdated flag on a KT call), the Keeper proposes:
- a `doc_edit` (documents, files, verified answers): the current excerpt and drafted wording for the owner, checked, or
  the readers' points as a list with the AI off;
- or a `rerecord` (a KT call): one per (call, speaker).

It waits in Approvals. Approving a doc_edit stores the wording (the manager may edit it) in `doc_edits`: an accepted edit
for a document (`GET /api/documents/{id}` → `accepted_edits`), or a suggestion for the owner of code
(`GET /api/code/explanations` → `suggested_edits`). **Nothing is written to a repository.** Every open flag on the target is
resolved, and its sender and the owner are told. One pending fix per target.

**The agent.** Once a target has enough flags, the Keeper thinks: it reads `feedback_target` (the current text and the
readers' notes, as data), checks what is true now (read_file, search_knowledge, kt_moments, verified_answers) and drafts the
wording (checked; never written to a repository). The doc_edit payload adds `agent: {run_id, steps}` and `rationale`. After a
push, the Keeper reads `kt_changes` (per link: the diff since the session and what the session said) and judges whether each
explanation is outdated by what changed, not by the share of lines; a removed file or symbol is always outdated, and only
links of the push can be judged. Without a loop: the 30% rule and the checked writer.

**Tests.** `test_lifecycle_api.py::test_documentation_feedback_goes_to_the_owner`, `test_agents_model.py` (thresholds, the `test_thinking_agents.py` (semantic judgement over the 30% rule, planted notes as data).
doc_edit's decision), `test_injection.py` (planted notes).
