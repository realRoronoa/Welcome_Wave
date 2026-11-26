# Role homes and setup

**Problem.** Every role opened on the same dashboard and had to look for what was theirs.

**API.** `GET /api/home` → `{role, focus: [{key, priority, text, link, why}], figure: {label, value, how} | null, sections}`
(any signed-in person). `GET /api/setup` → `{steps: [{key, title, status: done|skipped|todo, why, how}],
minutes_per_interruption}`; `PATCH /api/setup {step?, action?: complete|skip|reopen, minutes_per_interruption?}` (admins).

**Logic.** Focus: at most 6, by priority (1 first). New hire: unacknowledged documents and a failing environment check (1),
blockers and due check-ins (2), the survey and the first task (3), the next milestone (4); figure: program day. Manager:
blockers at manager stage (1), plans or proposals awaiting approval (2), overdue milestones, due check-ins, completion
reviews (3), access to grant (4), the feedback queue (5); figure: hires on track; sections: hire cards from the visibility
registry. Expert: questions waiting past the 24 h target and mentees' blockers (1), handover items (2), other questions (3);
figure: questions waiting; sections: digest, mentee cards, preferences. Admin: setup steps (1), open handovers (2); figure:
seats (billing is out of scope). Executive: report figures; figure: median days to the first merged change. Setup steps are
checked from data (repository, teams, templates, people, first enrolment); an admin may complete or skip any.

**Tests.** `test_lifecycle_api.py::test_every_role_has_a_short_home`, `::test_setup_steps_and_the_estimate`.
