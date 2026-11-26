# The demo clock and feature flags

**Demo clock.** `POST /api/demo/advance {days: 1..90}` → `{offset_days, today, programs: [{onboarding_id, day}]}` (managers,
admins; demo mode only). It stores a day offset for the organisation (`org_settings.demo_day_offset`). For each day it
crosses, it does two things:
- The seeded people do what they plausibly would (`app/lifecycle/simulation.py`). They acknowledge documents, grant access,
  fix a failing environment check the next day and finish tasks by their due date. The manager selects the Scout's first
  task on day 8; its pull request opens on day 14 and is merged on day 21. Milestones a person marks are marked on their day,
  and check-ins and surveys are written three days after theirs (on time). A program past its last day with every
  criterion met is signed off. A plan that waits for approval keeps waiting.
- That day's tick runs (`docs/AGENTS.md`): blockers and nudges, weekly summaries, completion drafts, the digest.

Then every active program is brought up to date at the new date. `POST /api/demo/reset` (managers, admins) rebuilds the
seeded organisation at day 0. `GET /api/lifecycle/config` also answers `today` and `offset_days`. Everything lifecycle
reads "today" from `app/lifecycle/clock.py`; link expiry uses the real clock. Moving forward only, capped at 365 days.

**Flags.** `FEATURES` (environment): `all` (default), or names separated by commas; `-name` turns one off
(`all,-surveys`). Names: preboarding, routing, first_task, presubmit, blockers, areas, summaries, feedback, checkins,
surveys, completion, handover, reports, demo_clock. A route of a feature that is off answers 404. Enrolment, programs,
homes and visibility are not flags. `GET /api/lifecycle/config` (no sign-in) → `{features, survey_min_group,
report_min_median, report_min_rate, checkin_days, preboarding_link_days, tracks, levels}`.

**Tests.** `test_lifecycle_api.py::test_the_demo_clock_moves_programs_to_their_check_ins`,
`test_agents_model.py::test_the_demo_clock_simulates_the_seeded_people_and_resets`, `::test_feature_flags_turn_routes_off`,
`test_lifecycle_rules.py::test_flags`.
