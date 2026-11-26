# Progress by area

**Problem.** "How is the new hire doing" was answered by feel, or by counting tasks.

**Users.** The hire (and their self-assessment), the mentor, managers.

**Data.** Computed from tasks, KT links, transcripts, chat; `area_progress` keeps what only the hire can record (reading-path
steps, clips watched) and the self-assessment.

**API.** `GET /api/onboardings/{id}/areas` → `{areas: [{area, level, self_level, evidence: {steps, watched, questions,
merged}, why}]}` (hire, mentor, managers). `PATCH /api/onboardings/{id}/areas/{area}` `{self_level?, step?, watched?:
{meeting_id, at_s}}` → the area (the hire only).

**Logic.** Areas are the project's modules (coverage map, without root, docs and tests). Evidence kinds: steps (recorded
steps, done learn/meet tasks in the area), watched (recorded clips, KT calls about the area the hire spoke in), questions
(answered from the area's code, not marked wrong or stale), merged (done code tasks with a PR in the area). Levels:
contributed with a merged change; working with two or more kinds; exploring with one; not started. No scores, no ranking
between people: the levels describe evidence, so a hire cannot be compared to another, and the self-assessment sits next
to the level, never instead of it.

**UI states.** One row per area with a level chip, the hire's own chip, and expandable evidence.

**Edge cases.** Unknown area 404; a clip of another project's meeting 422; recorded lists keep the last 50 entries.

**Tests.** `test_lifecycle_api.py::test_area_levels_come_from_kinds_of_evidence`,
`test_lifecycle_rules.py::test_area_levels_count_kinds_of_evidence_not_amounts`.
