"""Check-ins at day 30, 60 and 90 (scaled with the program's length), and the onboarding experience survey.

A check-in has two halves: the hire's and the manager's, each a few fixed questions. Both see both: a check-in is a
conversation, not a file kept on someone. One per day and author; writing again replaces it.

The survey is separate and anonymous. A response carries the team, the cohort (the quarter the hire started) and the day,
never a person id; the program only remembers which days the hire answered, so nobody answers twice and one response is one
person for one survey day. Results are shown per (team, survey day) and only where SURVEY_MIN_GROUP or more distinct people
answered, and each question's mean needs that many answers too; below that the summary says "not enough data", never a
number. (Counting answers across days would let three hires answering on days 30, 60 and 90 pass a floor of five.)"""
from __future__ import annotations

from datetime import timedelta
from statistics import mean

from app.lifecycle import templates as T
from app.lifecycle.context import Ctx, HireCtx
from app.ports import CheckinReview, SurveyResponse

SURVEY_MIN_GROUP = 5
QUESTIONS = {
    "hire": [
        {"id": "going_well", "text": "What is going well?"},
        {"id": "hardest", "text": "What has been hardest?"},
        {"id": "need", "text": "What do you need from your manager or mentor?"},
        {"id": "confidence", "text": "How confident do you feel working on your own (1-5)?", "scale": True},
    ],
    "manager": [
        {"id": "strengths", "text": "Where is the hire strongest so far?"},
        {"id": "focus_next", "text": "What should they focus on next?"},
        {"id": "support", "text": "What will you do to support them?"},
        {"id": "on_track", "text": "Are they on track for this stage (1-5)?", "scale": True},
    ],
}
SURVEY = [
    {"id": "clarity", "text": "I knew what was expected of me."},
    {"id": "support", "text": "I got help when I was stuck."},
    {"id": "docs", "text": "The documentation and KT calls answered my questions."},
    {"id": "tools", "text": "My access and tools were ready when I needed them."},
    {"id": "belonging", "text": "I feel part of the team."},
]


def clean_answers(role: str, answers: dict) -> dict:
    """Known questions only: text up to 2000 characters, scales from 1 to 5."""
    out = {}
    for q in QUESTIONS[role]:
        v = answers.get(q["id"])
        if v is None:
            continue
        if q.get("scale"):
            if type(v) is int and 1 <= v <= 5:
                out[q["id"]] = v
        else:
            out[q["id"]] = str(v)[:2000]
    return out


def state(hc: HireCtx) -> dict:
    days = T.checkin_days(hc.program.duration_days)
    have = {(c.day, c.author_role) for c in hc.checkins}
    due = [{"day": d, "author_role": r, "due_date": hc.ob.start_date + timedelta(days=d), "overdue": hc.day > d + 7}
           for d in days for r in ("hire", "manager") if d <= hc.day and (d, r) not in have]
    return {"days": days, "questions": QUESTIONS, "reviews": [c.model_dump() for c in hc.checkins], "due": due}


def save(hc: HireCtx, day: int, role: str, answers: dict) -> CheckinReview:
    row = hc.ctx.lc.save_checkin(CheckinReview(id="", onboarding_id=hc.ob.id, day=day, author_role=role, author_id=hc.ctx.user.id,
                                               answers=clean_answers(role, answers), created_at=hc.ctx.clock.now()))
    hc.forget("checkins")
    other = hc.ob.manager_id if role == "hire" else hc.ob.new_hire_id
    hc.ctx.store.notify(other, "checkin", f"Day-{day} check-in written by {hc.ctx.user.name}.",
                        f"/program/{hc.ob.id}?tab=checkins&project={hc.ob.project_id}")
    return row


def cohort(hc: HireCtx) -> str:
    d = hc.ob.start_date
    return f"{d.year}-Q{(d.month - 1) // 3 + 1}"


def submit_survey(hc: HireCtx, day: int, answers: dict) -> bool:
    """One response per onboarding per survey day. The day is claimed first, atomically (the program remembers which days a
    hire answered, never the answers), so a second submission, even a simultaneous one, finds the day taken: False, and
    nothing is stored twice. That is what makes a response mean one person in the floor below."""
    lc = hc.ctx.lc
    if not lc.claim_survey_day(hc.ob.id, day):
        return False
    clean = {q["id"]: answers[q["id"]] for q in SURVEY if type(answers.get(q["id"])) is int and 1 <= answers[q["id"]] <= 5}
    try:
        lc.add_survey_response(SurveyResponse(id="", org_id=hc.program.org_id, team_id=hc.program.team_id, cohort=cohort(hc), day=day,
                                              answers=clean))
    except Exception:
        current = lc.get_program(hc.ob.id)  # nothing was stored: give the day back so the hire can answer
        lc.update_program(hc.ob.id, survey_days=[d for d in (current.survey_days if current else []) if d != day])
        raise
    hc.program = lc.get_program(hc.ob.id) or hc.program
    return True


def _cell_scores(rows: list[SurveyResponse]) -> dict:
    """Mean per question over one team and survey day, for the questions at least SURVEY_MIN_GROUP people answered."""
    out = {}
    for q in SURVEY:
        values = [r.answers[q["id"]] for r in rows if q["id"] in r.answers]
        if len(values) >= SURVEY_MIN_GROUP:
            out[q["id"]] = round(mean(values), 2)
    return out


def survey_cells(responses: list[SurveyResponse]) -> dict[tuple, list[SurveyResponse]]:
    """Responses grouped by (team, survey day). A hire answers once per day, so a cell's size is a number of PEOPLE; across days
    the same person answers again, so answers are never counted across cells to reach the floor."""
    cells: dict[tuple, list[SurveyResponse]] = {}
    for r in responses:
        cells.setdefault((r.team_id, r.day), []).append(r)
    return cells


def floored_scores(responses: list[SurveyResponse]) -> dict[str, tuple[float, int]]:
    """{question: (mean, answers)} over the cells (team, day) in which at least SURVEY_MIN_GROUP people answered that question;
    a question without such a cell is absent. For readers that aggregate over teams, such as the Planner."""
    pooled: dict[str, list[int]] = {}
    for rows in survey_cells(responses).values():
        for q in SURVEY:
            values = [r.answers[q["id"]] for r in rows if q["id"] in r.answers]
            if len(values) >= SURVEY_MIN_GROUP:
                pooled.setdefault(q["id"], []).extend(values)
    return {q: (round(mean(v), 2), len(v)) for q, v in pooled.items()}


def _team_rows(responses: list[SurveyResponse], teams: dict) -> list[dict]:
    by_team: dict = {}
    for (team_id, day), rows in survey_cells(responses).items():
        by_team.setdefault(team_id, {})[day] = rows
    out = []
    for team_id, days in sorted(by_team.items(), key=lambda kv: (teams[kv[0]].name if kv[0] in teams else "~")):
        cells = [{"day": d, "n": len(rows), "scores": _cell_scores(rows) or None} for d, rows in sorted(days.items())]
        for cell in cells:
            cell["status"] = "ok" if cell["scores"] else "not_enough_data"
        pooled = floored_scores([r for rows in days.values() for r in rows])
        people = max(c["n"] for c in cells)  # people, not answers: the busiest single survey day
        ok = bool(pooled)
        out.append({"team_id": team_id, "team": teams[team_id].name if team_id in teams else "No team", "n": people,
                    "status": "ok" if ok else "not_enough_data", "scores": {q: v[0] for q, v in pooled.items()} if ok else None,
                    "days": cells,
                    "how": (f"Average over the survey days when at least {SURVEY_MIN_GROUP} people answered, on a 1-5 scale. Each person "
                            "answers once a day, anonymously.") if ok else
                           (f"Not enough data: fewer than {SURVEY_MIN_GROUP} people answered on any one survey day, so nothing is "
                            "shown that could identify someone.")})
    return out


def survey_summary(ctx: Ctx, org_id: str, project_id: str | None = None, team_id: str | None = None) -> dict:
    """Per team and survey day, floored on distinct respondents (SURVEY_MIN_GROUP): below it a team or day says 'not enough
    data', and so does any single question fewer people answered. Optionally one project or team."""
    teams = {t.id: t for t in ctx.lc.teams(org_id, project_id)}
    responses = [r for r in ctx.lc.survey_responses(org_id)
                 if (not project_id or r.team_id in teams) and (not team_id or r.team_id == team_id)]
    return {"questions": SURVEY, "min_group": SURVEY_MIN_GROUP, "teams": _team_rows(responses, teams)}


def export_aggregates(responses: list[SurveyResponse], teams: dict) -> list[dict]:
    """What the organisation export holds of the survey: the floored aggregates per team and day, never a response row."""
    return [{"team_id": t["team_id"], "team": t["team"], "day": c["day"], "respondents": c["n"], "status": c["status"],
             "scores": c["scores"]} for t in _team_rows(responses, teams) for c in t["days"]]
