# Leadership reporting

**Problem.** Leaders asked "is onboarding working?" and got anecdotes, or zeros from thin data.

**Users.** Managers (their projects), admins and executives (the organisation).

**API.** `GET /api/reports/onboarding?project_id=` → `{as_of, window_days, projects, figures: [{key, label, unit, how, current:
{value, n, display}, previous: {value, n, display}}], surveys, assumptions, why}`; `GET /api/reports/onboarding.csv`.

**Logic.** Cohorts: started in the last 90 days, and the 90 before. Figures: hires; median days to the first PR and to the
first merged change (milestones); milestones on time; check-ins on time; median business days to resolve a blocker; open
blockers; questions answered from sources; senior time saved; knowledge gaps; median days to completion. Floors: medians
need 3 values, rates 5 cases, survey averages 5 answers; below that `value` is null and `display` "not enough data", never 0.
Senior time saved = (questions answered without a person + verified answers and KT clips reused) × minutes per avoided
interruption (15, editable on the setup page), labelled an estimate with its assumption. Knowledge gaps: confidence below
0.55, a thumbs-down, handed to a person, or the same question 3+ times by 2+ people in 30 days.

**Tests.** `test_lifecycle_api.py::test_the_report_says_not_enough_data_instead_of_zero`, `test_lifecycle_rules.py` (floors, gaps).
