# Check-ins and the onboarding survey

**Problem.** The 30/60/90 conversations were not written down, and onboarding quality was never measured honestly.

**Users.** The hire and the manager each write their half of a check-in; the hire answers the survey anonymously; managers,
admins and executives see survey aggregates.

**Data.** `checkin_reviews` (day, author_role, author_id, answers): one per day and author. `survey_responses` (team, cohort,
day, answers): no person id; `onboarding_programs.survey_days` remembers which days the hire answered.

**API.** `GET /api/onboardings/{id}/checkins` → `{checkins: {days, questions, reviews, due: [{day, author_role, due_date,
overdue}]}}` (hire, managers); `POST …/checkins {day, answers}` → 201 (the caller's half). `POST /api/surveys {onboarding_id,
day, answers: {clarity, support, docs, tools, belonging: 1-5}}` → 204 (the hire); `GET /api/surveys/summary?project_id=` →
`{questions, min_group: 5, teams: [{team_id, team, n, status: ok|not_enough_data, scores | null, how, days: [{day, n, status, scores | null}]}]}`; `n` counts people (the most who answered on any one survey day), never answers.

**Logic.** Check-in days scale with the program (30/60/90 of 90). Answers are known questions only: text up to 2000
characters, scales 1-5. Both halves visible to both: a check-in is a conversation. Survey: one hire answers each survey day once (claimed
atomically), so a (team, day) group's size is a number of people. Averages are shown only for groups of 5 or more people, and a
question's average needs 5 answers too; the team figure pools only the days that passed. Below the floor the summary says not
enough data and shows no number, so nobody can be identified. Three hires answering on days 30, 60 and 90 are three people, not
nine answers. The organisation export holds these aggregates, never response rows.
Nothing about the answers is audited.

**Edge cases.** A day not reached or not a check-in day → 422; answering the survey twice → 409.

**Tests.** `test_lifecycle_api.py` (check-ins through the demo clock, surveys and the floor).
