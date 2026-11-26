"""Program templates: one per role track and level (Backend, Frontend, SRE, Data; Junior to Staff), versioned. A template
says what a program contains: milestones, documents to acknowledge, access to request, and the plan drafted when no
model is set up. Its days are days of a 90-day program; a template of another length scales them, so the 30/60/90
check-ins of a 120-day program fall on days 40/80/120.

An organisation with no templates at all gets the default set the first time templates are listed, so a new
organisation can enrol someone without writing one first."""
from __future__ import annotations

from app.agents.schemas import ProposedTask
from app.ports import Acknowledgement, AccessItem, Milestone, ProgramTemplate

TRACKS = ("Backend", "Frontend", "SRE", "Data")
LEVELS = ("Junior", "Mid", "Senior", "Staff")
BASE_DAYS = 90
CHECKIN_DAYS = (30, 60, 90)

# Milestones whose evidence the product records itself (app/lifecycle/progress.py checks them); others a manager marks.
AUTO_MILESTONES = frozenset({"preboarding_complete", "environment_ready", "access_ready", "first_pr_opened", "first_pr_merged",
                             "checkin_30", "checkin_60", "checkin_90", "area_working", "area_contributed"})

_COMMON_MILESTONES = [
    {"key": "preboarding_complete", "title": "Pre-boarding documents acknowledged", "due_day": 0},
    {"key": "environment_ready", "title": "Development environment passes its check", "due_day": 2},
    {"key": "access_ready", "title": "Every access item granted", "due_day": 3},
    {"key": "meet_team", "title": "Met the mentor and the team", "due_day": 5},
    {"key": "first_pr_opened", "title": "First pull request opened", "due_day": 14},
    {"key": "first_pr_merged", "title": "First change merged", "due_day": 21},
    {"key": "checkin_30", "title": "30-day check-in (hire and manager)", "due_day": 30},
    {"key": "area_working", "title": "Working independently in one area", "due_day": 45},
    {"key": "checkin_60", "title": "60-day check-in (hire and manager)", "due_day": 60},
    {"key": "area_contributed", "title": "A merged change in the focus area", "due_day": 75},
    {"key": "checkin_90", "title": "90-day check-in and completion review", "due_day": 90},
]
_TRACK_MILESTONES = {
    "Backend": [{"key": "service_traced", "title": "Traced one request through the service end to end", "due_day": 20}],
    "Frontend": [{"key": "ui_shipped", "title": "Shipped a visible UI change behind review", "due_day": 35}],
    "SRE": [{"key": "oncall_shadow", "title": "Shadowed one on-call shift", "due_day": 40}],
    "Data": [{"key": "pipeline_run", "title": "Ran the main pipeline end to end", "due_day": 20}],
}
_LEVEL_MILESTONES = {
    "Junior": [{"key": "pairing_sessions", "title": "Three pairing sessions with the mentor", "due_day": 25}],
    "Mid": [],
    "Senior": [{"key": "design_review", "title": "Took part in a design review", "due_day": 60}],
    "Staff": [{"key": "design_review", "title": "Led a design review", "due_day": 55},
              {"key": "tech_direction", "title": "Wrote down one technical direction for the team", "due_day": 85}],
}
_ACKS = [
    {"doc_key": "code_of_conduct", "title": "Code of conduct", "summary": "How we work together and how to raise a concern."},
    {"doc_key": "security_policy", "title": "Security policy", "summary": "Secrets, devices, and reporting an incident."},
]
_TRACK_ACKS = {
    "SRE": [{"doc_key": "oncall_policy", "title": "On-call policy", "summary": "Rotation, escalation and handover."}],
    "Data": [{"doc_key": "data_handling", "title": "Data handling policy", "summary": "Personal data, retention and access."}],
}
_ACCESS = [{"system": "GitHub organisation", "owner": "manager"}, {"system": "CI dashboards", "owner": "mentor"},
           {"system": "Chat workspace", "owner": "admin"}]
_TRACK_ACCESS = {
    "Backend": [{"system": "Staging database (read-only)", "owner": "mentor"}],
    "Frontend": [{"system": "Design files", "owner": "manager"}],
    "SRE": [{"system": "Cloud console", "owner": "manager"}, {"system": "Pager rota", "owner": "mentor"}],
    "Data": [{"system": "Data warehouse", "owner": "manager"}],
}
_PLAN = [
    {"title": "Set up your development environment", "kind": "learn", "due_day": 2,
     "description": "Run the environment check from your pre-boarding page until every tool passes.",
     "acceptance_criteria": ["The environment check passes"]},
    {"title": "Meet your mentor and the team", "kind": "meet", "due_day": 3,
     "description": "A 30-minute introduction: how the team works, where to ask, what good looks like.",
     "acceptance_criteria": ["Introduction held"]},
    {"title": "Read the README and the architecture notes", "kind": "learn", "due_day": 5,
     "description": "Read them, then ask the assistant about anything unclear.",
     "acceptance_criteria": ["Three questions asked or answered"]},
    {"title": "Watch the recorded KT calls for your focus area", "kind": "learn", "due_day": 7,
     "description": "Open the code view and play the clips linked to the files of your focus area.",
     "acceptance_criteria": ["Clips of the focus area watched"]},
    {"title": "Choose your first task with your manager", "kind": "meet", "due_day": 8,
     "description": "Go through the first-task shortlist and its reasons together.", "acceptance_criteria": ["First task selected"]},
    {"title": "Open your first pull request", "kind": "code", "due_day": 14,
     "description": "Run a private review of your branch first; then open the pull request.",
     "acceptance_criteria": ["Pull request opened and linked to the task", "Tests added or updated"]},
]
_TRACK_PLAN = {
    "Backend": {"title": "Trace one request through the service", "kind": "learn", "due_day": 10,
                "description": "Follow the code tour from the entry point to the response.", "acceptance_criteria": ["Notes shared"]},
    "Frontend": {"title": "Run the UI locally and change one component", "kind": "code", "due_day": 10,
                 "description": "A small visual change, reviewed by your mentor.", "acceptance_criteria": ["Change reviewed"]},
    "SRE": {"title": "Shadow an on-call shift", "kind": "meet", "due_day": 20,
            "description": "Join the on-call engineer for one shift.", "acceptance_criteria": ["Shift shadowed"]},
    "Data": {"title": "Run the main pipeline end to end", "kind": "learn", "due_day": 10,
             "description": "Locally, on the sample data.", "acceptance_criteria": ["Pipeline output checked"]},
}


def default_templates(org_id: str) -> list[ProgramTemplate]:
    out = []
    for track in TRACKS:
        for level in LEVELS:
            milestones = sorted(_COMMON_MILESTONES + _TRACK_MILESTONES[track] + _LEVEL_MILESTONES[level], key=lambda m: m["due_day"])
            out.append(ProgramTemplate(
                id="", org_id=org_id, key=f"{track.lower()}-{level.lower()}", title=f"{level} {track} Engineer", role_track=track,
                level=level, duration_days=BASE_DAYS, milestones=milestones, acknowledgements=_ACKS + _TRACK_ACKS.get(track, []),
                access_items=_ACCESS + _TRACK_ACCESS[track], plan_items=sorted(_PLAN + [_TRACK_PLAN[track]], key=lambda p: p["due_day"])))
    return out


def list_for(lc, org_id: str, include_inactive: bool = False) -> list[ProgramTemplate]:
    """The organisation's templates; an organisation that never had any gets the defaults first."""
    if not lc.templates(org_id, include_inactive=True):
        for t in default_templates(org_id):
            lc.create_template(t)
    return lc.templates(org_id, include_inactive)


def scale(day: int, duration_days: int) -> int:
    return round(day * duration_days / BASE_DAYS)


def checkin_days(duration_days: int) -> list[int]:
    return [scale(d, duration_days) for d in CHECKIN_DAYS]


def milestones_for(t: ProgramTemplate, onboarding_id: str, duration_days: int) -> list[Milestone]:
    return [Milestone(onboarding_id=onboarding_id, key=m["key"], title=m["title"], due_day=scale(int(m["due_day"]), duration_days))
            for m in t.milestones]


def acknowledgements_for(t: ProgramTemplate, onboarding_id: str) -> list[Acknowledgement]:
    return [Acknowledgement(onboarding_id=onboarding_id, doc_key=a["doc_key"], title=a["title"]) for a in t.acknowledgements]


def access_for(t: ProgramTemplate, onboarding_id: str, owners: dict[str, str | None]) -> list[AccessItem]:
    """`owners` maps the template's owner roles (manager, mentor, admin) to people of this onboarding."""
    return [AccessItem(id="", onboarding_id=onboarding_id, system=a["system"],
                       owner_id=owners.get(a.get("owner", "manager")) or owners.get("manager")) for a in t.access_items]


def plan_tasks(t: ProgramTemplate, duration_days: int) -> list[ProposedTask]:
    """The plan the Task agent's place takes when no model is set up: the template's items, as proposals for approval."""
    return [ProposedTask(title=p["title"], description=p.get("description", ""), kind=p.get("kind", "learn"),
                         due_in_days=max(1, min(60, scale(int(p.get("due_day", 7)), duration_days))),
                         acceptance_criteria=p.get("acceptance_criteria", []), source="plan")
            for p in t.plan_items]
