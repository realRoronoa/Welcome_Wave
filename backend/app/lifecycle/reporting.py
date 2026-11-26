"""The leadership report (GET /api/reports/onboarding, .csv): onboarding outcomes for the organisation, or the projects a
manager can see, with a "how" for every figure and the previous cohort next to the current one.

Cohorts are rolling: the current cohort started in the last 90 days (on the lifecycle clock), the previous one in the 90
days before that. Floors keep thin data from looking like a result: a median needs MIN_MEDIAN values, a rate MIN_RATE
cases, a survey average SURVEY_MIN_GROUP people (per team and survey day); below that a figure says "not enough data" (value null), never 0.

Senior time saved is an estimate and says so: (questions answered from sources without a person + reuses of verified
answers and KT clips) x minutes per avoided interruption (15 by default; admins change it on the setup page)."""
from __future__ import annotations

import csv
import io
from collections import Counter
from datetime import date, datetime, timedelta
from statistics import median

from app.lifecycle import checkins as C
from app.lifecycle import templates as T
from app.lifecycle.clock import business_days
from app.lifecycle.context import Ctx
from app.ports import norm_question

WINDOW_DAYS = 90
MIN_MEDIAN, MIN_RATE = 3, 5
GAP_CONFIDENCE = 0.55
NOT_ENOUGH = "not enough data"


def _hires(ctx: Ctx, project_ids: list[str]) -> list[tuple]:
    out = []
    for pid in project_ids:
        for p in ctx.lc.programs(ctx.store.get_project(pid).org_id, pid):
            ob = ctx.store.get_onboarding(p.onboarding_id)
            if ob:
                out.append((ob, p))
    return out


def _cohorts(rows, today: date) -> dict[str, list]:
    cur = [r for r in rows if today - timedelta(days=WINDOW_DAYS) < r[0].start_date <= today]
    prev = [r for r in rows if today - timedelta(days=2 * WINDOW_DAYS) < r[0].start_date <= today - timedelta(days=WINDOW_DAYS)]
    return {"current": cur, "previous": prev}


def _figure(key: str, label: str, unit: str, how: str, values: dict) -> dict:
    """values: {"current": (value | None, n), "previous": (...)}. None means below the floor."""
    def one(v):
        value, n = v
        return {"value": value, "n": n, "display": NOT_ENOUGH if value is None else f"{value}{unit and ' ' + unit}"}
    return {"key": key, "label": label, "unit": unit, "how": how, **{k: one(v) for k, v in values.items()}}


def _median_days(ctx: Ctx, cohort, milestone_key: str):
    days = []
    for ob, p in cohort:
        m = next((m for m in ctx.lc.milestones(ob.id) if m.key == milestone_key and m.done_at), None)
        if m:
            days.append((m.done_at.date() - ob.start_date).days)
    return (round(median(days), 1) if len(days) >= MIN_MEDIAN else None, len(days))


def _on_time(ctx: Ctx, cohort, today: date):
    due = met = 0
    for ob, _ in cohort:
        for m in ctx.lc.milestones(ob.id):
            d = ob.start_date + timedelta(days=m.due_day)
            if d > today or m.status == "skipped":
                continue
            due += 1
            met += bool(m.done_at and m.done_at.date() <= d)
    return (round(100 * met / due) if due >= MIN_RATE else None, due)


def _blocker_days(ctx: Ctx, cohort):
    days = [business_days(a.opened_at, a.resolved_at) for ob, _ in cohort for a in ctx.lc.blockers(ob.id) if a.resolved_at]
    return (round(median(days), 1) if len(days) >= MIN_MEDIAN else None, len(days))


def _checkins_on_time(ctx: Ctx, cohort, today: date):
    due = met = 0
    for ob, p in cohort:
        written = {(c.day, c.author_role): c.created_at for c in ctx.lc.checkins(ob.id)}
        for d in T.checkin_days(p.duration_days):
            deadline = ob.start_date + timedelta(days=d + 7)
            if deadline > today:
                continue
            for role in ("hire", "manager"):
                due += 1
                at = written.get((d, role))
                met += bool(at and at.date() <= deadline)
    return (round(100 * met / due) if due >= MIN_RATE else None, due)


def _questions(ctx: Ctx, cohort) -> tuple[list[dict], list[str]]:
    by_project: dict[str, list[str]] = {}
    for ob, _ in cohort:
        by_project.setdefault(ob.project_id, []).append(ob.new_hire_id)
    turns, askers = [], []
    for pid, users in by_project.items():
        for uid, rows in ctx.store.chat_histories(pid, users, limit=500).items():
            turns += rows
            askers += [uid] * len(rows)
    return turns, askers


def _gaps(turns: list[dict], askers: list[str], today: date) -> int:
    """PLAN §5: low confidence, a thumbs-down, handed to a person, or the same question 3+ times by 2+ people in 30 days."""
    recent = Counter()
    people: dict[str, set] = {}
    since = today - timedelta(days=30)
    for t, who in zip(turns, askers):
        at = t.get("created_at")
        if isinstance(at, datetime) and at.date() >= since:
            q = norm_question(t.get("question") or "")
            recent[q] += 1
            people.setdefault(q, set()).add(who)
    repeated = {q for q, n in recent.items() if n >= 3 and len(people[q]) >= 2}
    return sum(1 for t in turns if (t.get("confidence") or 0) < GAP_CONFIDENCE or t.get("feedback") == -1
               or t.get("status") == "not_enough_information" or norm_question(t.get("question") or "") in repeated)


def _figures(ctx: Ctx, rows: list[tuple], today: date) -> list[dict]:
    """The report's figures for these hires (an organisation's, a project's or one team's), each floored."""
    co = _cohorts(rows, today)
    org = ctx.user.org_id
    minutes = ctx.lc.org_settings(org).minutes_per_interruption

    qs = {id(v): _questions(ctx, v) for v in co.values()}  # each cohort's chats, read once

    def per(fn):
        return {k: fn(v) for k, v in co.items()}

    def asked(cohort):
        turns, _ = qs[id(cohort)]
        n = len(turns)
        return (round(100 * sum(t.get("status") == "answered" for t in turns) / n) if n >= MIN_RATE else None, n)

    def saved(cohort):
        turns, _ = qs[id(cohort)]
        answered = sum(t.get("status") == "answered" for t in turns)
        clips = sum(len(t.get("kt_clips") or []) for t in turns if t.get("status") == "answered")
        reuses = sum(m.subject_id in {ob.new_hire_id for ob, _ in cohort} for m in ctx.lc.metrics(org, ["answer.reused"]))
        n = answered + clips + reuses
        return (round(n * minutes / 60, 1) if turns else None, n)

    def gaps(cohort):
        turns, askers = qs[id(cohort)]
        return (_gaps(turns, askers, today) if turns else None, len(turns))

    def open_blockers(cohort):
        active = [ob for ob, _ in cohort if ob.status == "active"]
        return (sum(len(ctx.lc.blockers(ob.id, open_only=True)) for ob in active) if active else None, len(active))

    def completed(cohort):
        days = [(p.completed_at.date() - ob.start_date).days for ob, p in cohort if p.completed_at]
        return (round(median(days), 1) if len(days) >= MIN_MEDIAN else None, len(days))

    return [
        _figure("hires", "Hires in the cohort", "", "Programs whose start date falls in the cohort's 90 days.",
                {k: (len(v) if v else None, len(v)) for k, v in co.items()}),
        _figure("days_to_first_pr", "Median days to the first pull request", "days",
                f"Start date to the 'first pull request opened' milestone; at least {MIN_MEDIAN} hires.",
                per(lambda c: _median_days(ctx, c, "first_pr_opened"))),
        _figure("days_to_first_merge", "Median days to the first merged change", "days",
                f"Start date to the 'first change merged' milestone; at least {MIN_MEDIAN} hires.",
                per(lambda c: _median_days(ctx, c, "first_pr_merged"))),
        _figure("milestones_on_time", "Milestones done on time", "%",
                f"Milestones done by their due date, of those due so far (skipped ones left out); at least {MIN_RATE}.",
                per(lambda c: _on_time(ctx, c, today))),
        _figure("checkins_on_time", "Check-ins written on time", "%",
                f"Hire and manager halves written within 7 days of day 30, 60 and 90; at least {MIN_RATE} due.",
                per(lambda c: _checkins_on_time(ctx, c, today))),
        _figure("blocker_days", "Median business days to resolve a blocker", "days",
                f"Opened to resolved, in business days; at least {MIN_MEDIAN} resolved blockers.", per(lambda c: _blocker_days(ctx, c))),
        _figure("open_blockers", "Open blockers", "", "Open alerts of the cohort's active hires.", per(open_blockers)),
        _figure("answered_from_sources", "Questions answered from sources", "%",
                f"The hires' questions answered with cited sources (tiers 1 and 2), of all they asked; at least {MIN_RATE}.",
                per(asked)),
        _figure("senior_hours_saved", "Senior time saved (estimate)", "hours",
                "(Questions answered without a person + verified answers and KT clips reused) "
                f"x {minutes} minutes per avoided interruption. An estimate: it assumes each would otherwise have interrupted "
                "a senior once.", per(saved)),
        _figure("knowledge_gaps", "Knowledge gaps", "",
                "Questions with confidence below 0.55, a thumbs-down, handed to a person, or asked 3+ times by 2+ people in 30 days.",
                per(gaps)),
        _figure("days_to_complete", "Median days to completion", "days",
                f"Start date to the manager's sign-off; at least {MIN_MEDIAN} completed programs.", per(completed)),
    ]


def _key_person_figure(ctx: Ctx, project_ids: list[str]) -> dict:
    """Single-owner critical areas: how many code areas (folders) hold a critical-risk file that one person alone owns
    (app/services/knowledge_risk.py). A count: no area, file or person is named, so an executive learns the key-person risk
    without reading the Knowledge risk page. It is the code as it stands now; nothing records it over time yet, so the
    previous window says 'not enough data'."""
    from app.services.knowledge_risk import build_knowledge_risk

    now = counted = 0
    for pid in project_ids:
        try:
            summary = build_knowledge_risk(ctx.store, ctx.index, pid, ctx.store.get_project(pid).org_id)["summary"]
        except Exception:  # noqa: BLE001 - a project whose code cannot be read counts as unknown, not as zero
            continue
        if summary["code_files"]:
            now += summary["single_owner_critical_areas"]
            counted += 1
    return _figure("single_owner_critical_areas", "Single-owner critical areas", "",
                   "Code areas (folders) with a critical-risk file that only one person owns, counted over the projects in this report, "
                   "as the code stands now. A count, never names: the people and files are on the Knowledge risk page, for managers. "
                   "It is not tracked over time yet, so the previous window has no figure.",
                   {"current": (now if counted else None, counted), "previous": (None, 0)})


def _team_groups(ctx: Ctx, rows: list[tuple], today: date, project_ids: list[str]) -> list[dict]:
    """The same figures per team (hires with no team form 'No team'), each with the same floors: a small team says 'not enough
    data' where a figure needs more people than it has. Only teams with a hire in either cohort are listed."""
    names = {t.id: t.name for pid in project_ids for t in ctx.lc.teams(ctx.user.org_id, pid)}
    co = _cohorts(rows, today)
    in_window = {id(r) for v in co.values() for r in v}
    by_team: dict = {}
    for r in rows:
        if id(r) in in_window:
            by_team.setdefault(r[1].team_id, []).append(r)
    return [{"team_id": tid, "team": names.get(tid, "No team"), "figures": _figures(ctx, group, today)}
            for tid, group in sorted(by_team.items(), key=lambda kv: (names.get(kv[0], "~"), str(kv[0])))]


def build(ctx: Ctx, project_ids: list[str], team_id: str | None = None) -> dict:
    today = ctx.clock.today()
    rows = _hires(ctx, project_ids)
    if team_id:
        rows = [r for r in rows if r[1].team_id == team_id]
    org = ctx.user.org_id
    minutes = ctx.lc.org_settings(org).minutes_per_interruption
    figures = _figures(ctx, rows, today)
    if not team_id:  # key-person risk belongs to a project's code, not to a team
        figures.append(_key_person_figure(ctx, project_ids))
    surveys = C.survey_summary(ctx, org, project_ids[0] if len(project_ids) == 1 else None, team_id)
    return {"as_of": today, "window_days": WINDOW_DAYS, "projects": project_ids, "team_id": team_id, "figures": figures,
            "teams": [] if team_id else _team_groups(ctx, rows, today, project_ids), "surveys": surveys,
            "assumptions": {"minutes_per_interruption": minutes, "min_median": MIN_MEDIAN, "min_rate": MIN_RATE,
                            "survey_min_group": C.SURVEY_MIN_GROUP},
            "why": ["Every figure says how it is worked out; below its floor it says 'not enough data', never 0.",
                    "Each team's figures use the same floors, so a small team often says 'not enough data'."]}


FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def safe_cell(value):
    """Spreadsheets run a text cell that starts with = + - @ (or a tab or carriage return) as a formula, and team names are
    typed by people: such text is prefixed with an apostrophe. Numbers are left as they are (a negative change stays a number)."""
    return "'" + value if isinstance(value, str) and value.startswith(FORMULA_START) else value


class _SafeWriter:
    def __init__(self, out):
        self._w = csv.writer(out)

    def writerow(self, row):
        self._w.writerow([safe_cell(v) for v in row])


def to_csv(report: dict) -> str:
    out = io.StringIO()
    w = _SafeWriter(out)
    w.writerow(["key", "label", "unit", "current", "current_n", "previous", "previous_n", "how"])

    def shown(v: dict):
        return v["display"] if v["value"] is None else v["value"]

    def rows(prefix: str, label: str, figures: list[dict]):
        for f in figures:
            w.writerow([prefix + f["key"], label + f["label"], f["unit"], shown(f["current"]), f["current"]["n"], shown(f["previous"]),
                        f["previous"]["n"], f["how"]])

    rows("", "", report["figures"])
    for t in report.get("teams", []):
        rows(f"team.{t['team_id'] or 'none'}.", f"{t['team']}: ", t["figures"])
    for t in report["surveys"]["teams"]:
        for q in C.SURVEY:
            value = t["scores"].get(q["id"]) if t["scores"] else NOT_ENOUGH
            w.writerow([f"survey.{q['id']}", f"{t['team']}: {q['text']}", "of 5", value if value is not None else NOT_ENOUGH, t["n"], "", "",
                        t["how"]])
        for day in t.get("days", []):  # each survey day on its own, with the same floors
            for q in C.SURVEY:
                value = (day["scores"] or {}).get(q["id"], NOT_ENOUGH)
                w.writerow([f"survey.{q['id']}.day{day['day']}", f"{t['team']}: {q['text']} (day {day['day']})", "of 5", value, day["n"],
                            "", "", t["how"]])
    return out.getvalue()
