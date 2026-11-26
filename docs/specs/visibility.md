# Employee visibility

**Problem.** Hires do not know what their manager can see about them.

**Users.** The hire (and managers, to check what the hire sees).

**API.** `GET /api/onboardings/{id}/visibility` → `{onboarding_id, hire, statement, private: [..], data: {<field>: {label,
visible_to, source, how, value}}}`.

**Logic.** One registry (`app/lifecycle/visibility.py FIELDS`) builds every manager- and mentor-facing payload about a hire:
the program, access, areas, blockers, summaries, check-ins, completion, the first-task shortlist, home cards (profile,
status), and the dashboard's row and question counts. The visibility payload returns every field with the same builders,
so the values are identical. Kept from managers and said so: pre-submit reviews; survey answers (anonymous, aggregates of
five or more); a plan waiting for approval shows as a count.

**Tests.** `tests/test_visibility.py` finds every lifecycle GET route about an onboarding in the app and fails if a manager
or mentor gets a field the hire's payload lacks, or a different value.
