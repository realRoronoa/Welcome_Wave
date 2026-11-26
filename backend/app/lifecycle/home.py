"""Role homes (GET /api/home): the few things this person should do next, one figure, and small sections.

  {role, focus: [{key, priority, text, link, why, explain?}], figure: {label, value, how}, sections: {...}}

A focus item an agent produced (a blocker the Coach found, a plan or completion draft of the Planner, a proposal of the
Keeper or the Planner, the Scout's brief, a handover plan) carries that agent's `explain` block (docs/AGENTS.md).

Focus items are ordered by priority (1 first), then by how long they have waited, and capped at MAX_FOCUS: a home is a
short list, not a dashboard. Hires' data in a manager's or mentor's sections is built from the visibility registry
(app/lifecycle/visibility.py), so the hire can see the same values. The setup checklist (admins) lives here too."""
from __future__ import annotations

from app.lifecycle import blockers as B
from app.lifecycle import checkins, enrolment, flags, hires, reporting, visibility
from app.lifecycle import templates as T
from app.lifecycle.context import Ctx, HireCtx

MAX_FOCUS = 6
RESPONSE_TARGET_H = 24


def _item(key: str, priority: int, text: str, link: str, why: str, explain: dict | None = None) -> dict:
    out = {"key": key, "priority": priority, "text": text, "link": link, "why": why}
    if explain:
        out["explain"] = explain
    return out


def _coach(alert, now=None) -> dict:
    from app.agents.coach import blocker_explain

    return blocker_explain(alert, [B.describe(alert, now) + "."])


def _explain(key: str, why: str, *sources) -> dict:
    from app.agents import registry as R

    return R.explain(key, [why], list(sources))


PROPOSAL_TEXT = {
    "rerecord": lambda p: f"Re-record '{p.get('meeting_title')}' with {(p.get('speaker') or {}).get('name') or 'its speaker'}?",
    "doc_edit": lambda p: f"Review the drafted fix for {str(p.get('title'))[:80]}",
    "template_change": lambda p: f"Review {len(p.get('changes', []))} proposed change(s) to the {p.get('template_title')} template",
}


def _proposals(ctx: Ctx, projects: list[str], kinds: set[str]) -> list[dict]:
    """The Keeper's and the Planner's pending proposals, each with the agent's explain block."""
    out = []
    for pid in projects:
        for a in ctx.store.approvals(pid):
            if a.kind in kinds:
                ex = a.payload.get("explain") or {}
                why = (ex.get("logic") or ["An agent proposed it; you decide."])[0]
                out.append(_item(f"proposal:{a.id}", 3, PROPOSAL_TEXT[a.kind](a.payload), f"/approvals/{a.id}?project={pid}", why, ex or None))
    return out


def _focus(items: list[dict]) -> list[dict]:
    return sorted(items, key=lambda i: i["priority"])[:MAX_FOCUS]


def _projects(ctx: Ctx) -> list[str]:
    return [p.id for p in ctx.store.projects_for(ctx.user)]


def _link(hc: HireCtx, tab: str = "") -> str:
    return f"/program/{hc.ob.id}?{'tab=' + tab + '&' if tab else ''}project={hc.ob.project_id}"


# ---------------------------------------------------------------- new hire
def new_hire(ctx: Ctx) -> dict:
    mine = [hires.refresh(h) for h in hires.of_projects(ctx, _projects(ctx)) if h.ob.new_hire_id == ctx.user.id]
    if not mine:
        return {"role": "new_hire", "focus": [], "figure": None, "sections": {"program": None},
                "why": ["You have no onboarding program yet."]}
    hc = mine[0]
    items = []
    for a in hc.acknowledgements:
        if not a.acknowledged_at:
            items.append(_item(f"ack:{a.doc_key}", 1, f"Acknowledge the {a.title}", _link(hc, "preboarding"), "Part of pre-boarding."))
    last = hc.environment_checks[-1] if hc.environment_checks else None
    env_alert = any(a.rule == "environment" for a in ctx.lc.blockers(hc.ob.id, open_only=True))
    if last and not last.ok and not env_alert:  # an open environment blocker says it once, with its evidence
        failing = ", ".join(r["name"] for r in last.results if not r["ok"])
        items.append(_item("env", 1, f"Fix your environment check: {failing}", _link(hc, "preboarding"), "The last check failed."))
    for a in ctx.lc.blockers(hc.ob.id, open_only=True):
        items.append(_item(f"blocker:{a.id}", 2, B.describe(a, ctx.clock.now()), _link(hc, "blockers"), f"Open since {a.opened_at:%d %b}.",
                           _coach(a, ctx.clock.now())))
    for d in checkins.state(hc)["due"]:
        if d["author_role"] == "hire":
            items.append(_item(f"checkin:{d['day']}", 2, f"Write your day-{d['day']} check-in", _link(hc, "checkins"), "It is due."))
    if flags.is_on("surveys"):
        for d in T.checkin_days(hc.program.duration_days):
            if d <= hc.day and d not in hc.program.survey_days:
                items.append(_item(f"survey:{d}", 3, f"Answer the day-{d} onboarding survey (anonymous)", _link(hc, "survey"),
                                   "Anonymous: shown only as averages of five or more people."))
                break
    if hc.program.first_task and not any(t.pr_number for t in hc.tasks if t.kind == "code"):
        items.append(_item("first_task", 3, f"Start your first task: {hc.program.first_task['title']}", _link(hc, "first-task"),
                           "Your manager selected it from the shortlist.", hc.program.first_task.get("explain")))
    status = visibility.FIELDS["status"].build(hc)
    if status["next_milestone"]:
        m = status["next_milestone"]
        items.append(_item(f"milestone:{m['key']}", 4, f"Next milestone: {m['title']} (due {m['due_date']:%d %b})", _link(hc),
                           "Your plan's next step."))
    return {"role": "new_hire", "focus": _focus(items),
            "figure": {"label": "Day", "value": hc.day, "how": f"Days since your start date ({hc.ob.start_date:%d %b %Y}), of a "
                                                                f"{hc.program.duration_days}-day program."},
            "sections": {"program": visibility.view(hc, visibility.CARD), "first_task": hc.program.first_task}}


# ---------------------------------------------------------------- manager
def manager(ctx: Ctx) -> dict:
    projects = _projects(ctx)
    team = [hires.refresh(h) for h in hires.of_projects(ctx, projects)]
    items = []
    cards = []
    for hc in team:
        card = visibility.view(hc, visibility.CARD)
        cards.append(card)
        name = (card["profile"]["hire"] or {}).get("name") or "A new hire"
        for a in ctx.lc.blockers(hc.ob.id, open_only=True):
            if a.stage == "manager":
                items.append(_item(f"blocker:{a.id}", 1, f"Unblock {name}: {B.describe(a, ctx.clock.now())}", _link(hc, "blockers"),
                                   "Unresolved for three business days.", _coach(a, ctx.clock.now())))
        plan = enrolment.plan_status(ctx, hc.ob, hc.program)
        if plan["pending_approval_id"]:
            first = plan["status"] == "awaiting_approval"
            why = "The drafted plan waits for you." if first else "The Task agent proposed more tasks."
            items.append(_item(f"plan:{hc.ob.id}", 2, f"Approve {name}'s {'plan' if first else 'proposed tasks'}",
                               f"/approvals/{plan['pending_approval_id']}?project={hc.ob.project_id}", why,
                               _explain("planner", why, {"label": "Plan approval", "kind": "event",
                                                         "ref": {"approval_id": plan["pending_approval_id"]}})))
        for title in card["status"]["overdue_milestones"][:2]:
            items.append(_item(f"overdue:{hc.ob.id}:{title}", 3, f"{name}: '{title}' is overdue", _link(hc), "Past its due date."))
        for d in card["status"]["checkins_due"]:
            if d["author_role"] == "manager":
                items.append(_item(f"checkin:{hc.ob.id}:{d['day']}", 3, f"Write {name}'s day-{d['day']} check-in", _link(hc, "checkins"),
                                   "Overdue." if d["overdue"] else "It is due."))
        if hc.day >= hc.program.duration_days and not hc.program.completed_at:
            from app.agents.planner import completion_view

            items.append(_item(f"complete:{hc.ob.id}", 3, f"Review {name}'s completion", _link(hc, "completion"),
                               f"Day {hc.day} of {hc.program.duration_days}.", completion_view(hc)["explain"]))
    items += _owned_access(ctx, team) + _feedback_queue(ctx, projects) + _proposals(ctx, projects, {"rerecord", "doc_edit"})
    on_track = sum(c["status"]["on_track"] for c in cards)
    pending = sum(len([a for a in ctx.store.approvals(pid) if a.kind == "task_plan"]) for pid in projects)
    return {"role": "manager", "focus": _focus(items),
            "figure": {"label": "Hires on track", "value": f"{on_track} of {len(cards)}" if cards else None,
                       "how": "On track: no overdue milestone and no blocker unresolved for three business days."},
            "sections": {"hires": cards, "plans_awaiting_approval": pending}}


def _owned_access(ctx: Ctx, team: list[HireCtx]) -> list[dict]:
    out = []
    for hc in team:
        for a in hc.access:
            if a.owner_id == ctx.user.id and a.status == "requested":
                name = (ctx.person(hc.ob.new_hire_id) or {}).get("name")
                out.append(_item(f"access:{a.id}", 4, f"Grant {a.system} to {name}", _link(hc, "access"),
                                 f"Requested {a.requested_at:%d %b}."))
    return out


def _feedback_queue(ctx: Ctx, projects: list[str]) -> list[dict]:
    if not flags.is_on("feedback"):
        return []
    n = sum(1 for pid in projects for f in ctx.lc.feedback(pid) if f.owner_id == ctx.user.id and f.status == "open")
    return [_item("feedback", 5, f"{n} documentation feedback item{'s' if n != 1 else ''} in your queue", "/feedback",
                  "People marked something you own as unclear or outdated.")] if n else []


# ---------------------------------------------------------------- expert
def expert(ctx: Ctx) -> dict:
    projects = _projects(ctx)
    now = ctx.clock.now()
    requests = sorted(ctx.store.expert_requests_for(ctx.user.id), key=lambda r: r["created_at"])
    digest_ids = {m.props.get("request_id") for m in ctx.lc.metrics(ctx.user.org_id, ["route.escalated"])
                  if m.props.get("target") == ctx.user.id and m.props.get("delivery") == "digest"}
    items = []
    for r in requests:
        hours = (now - r["created_at"]).total_seconds() / 3600
        late = hours > RESPONSE_TARGET_H
        items.append(_item(f"request:{r['id']}", 1 if late else 3, f"Answer: {r['question'][:120]}",
                           f"/experts?message={r.get('message_id')}&project={r['project_id']}",
                           f"Waiting {int(hours)} hours; the response target is {RESPONSE_TARGET_H}." if late else "Waiting for you."))
    mentees = [hires.refresh(h) for h in hires.of_projects(ctx, projects) if h.program.mentor_id == ctx.user.id]
    for hc in mentees:
        name = (ctx.person(hc.ob.new_hire_id) or {}).get("name")
        for a in ctx.lc.blockers(hc.ob.id, open_only=True):
            if a.stage in {"mentor", "manager"}:
                items.append(_item(f"blocker:{a.id}", 1, f"Help {name}: {B.describe(a, ctx.clock.now())}", _link(hc, "blockers"),
                                   "Unresolved for a business day.", _coach(a, ctx.clock.now())))
    for h in ctx.lc.handovers(ctx.user.org_id):
        if h.user_id == ctx.user.id and h.status != "done":
            left = sum(not i["done"] for i in h.plan.get("items", []))
            items.append(_item(f"handover:{h.id}", 2,
                               f"Your handover: {left} item{'s' if left != 1 else ''} left before {h.last_day:%d %b}",
                               f"/handovers?project={h.project_id}", "Hand over what only you hold.", h.plan.get("explain")))
    items += _owned_access(ctx, mentees) + _feedback_queue(ctx, projects)
    prefs = ctx.lc.expert_preferences(ctx.user.id)
    return {"role": "expert", "focus": _focus(items),
            "figure": {"label": "Questions waiting for you", "value": len(requests),
                       "how": f"Open questions routed to you; the response target is {RESPONSE_TARGET_H} hours."},
            "sections": {"digest": [{"id": r["id"], "question": r["question"][:200], "created_at": r["created_at"]} for r in requests
                                    if str(r["id"]) in digest_ids],
                         "mentees": [visibility.view(hc, visibility.CARD) for hc in mentees],
                         "preferences": prefs.model_dump()}}


# ---------------------------------------------------------------- admin and setup
SETUP = [
    ("repository", "Connect a repository", "The assistant, the code view and the plans read it."),
    ("teams", "Add teams", "Programs, surveys and reports group hires by team."),
    ("templates", "Review the program templates", "Each role track and level has one; change them to fit."),
    ("people", "Invite managers and experts", "Someone has to approve plans and answer questions."),
    ("first_enrolment", "Enrol the first hire", "Invitation, program and drafted plan in one step."),
    ("estimate", "Check the senior-time estimate", "Minutes per avoided interruption: 15 unless you change it."),
]


def setup_steps(ctx: Ctx) -> list[dict]:
    org = ctx.user.org_id
    projects = _projects(ctx)
    settings = ctx.lc.org_settings(org)
    done = {
        "repository": any(ctx.store.repos_for(p) for p in projects),
        "teams": bool(ctx.lc.teams(org)),
        "templates": bool(ctx.lc.templates(org)),
        "people": any(len(ctx.store.members(p)) > 1 or ctx.store.invitations(p) for p in projects),
        "first_enrolment": bool(ctx.lc.programs(org)),
        "estimate": False,
    }
    skipped, completed = set(settings.setup.get("skipped", [])), set(settings.setup.get("completed", []))
    out = []
    for key, title, why in SETUP:
        status = "done" if done[key] or key in completed else "skipped" if key in skipped else "todo"
        out.append({"key": key, "title": title, "status": status, "why": why,
                    "how": "Checked from your data." if done[key] else "Marked by an admin." if status != "todo" else "Not done yet."})
    return out


def admin(ctx: Ctx) -> dict:
    steps = setup_steps(ctx)
    items = [_item(f"setup:{s['key']}", 1, s["title"], "/setup", s["why"]) for s in steps if s["status"] == "todo"]
    for h in ctx.lc.handovers(ctx.user.org_id):
        if h.status != "done":
            name = (ctx.person(h.user_id) or {}).get("name")
            items.append(_item(f"handover:{h.id}", 2, f"Handover for {name} before {h.last_day:%d %b}",
                               f"/handovers?project={h.project_id}",
                               f"{sum(not i['done'] for i in h.plan.get('items', []))} items left.", h.plan.get("explain")))
    items += _proposals(ctx, _projects(ctx), {"template_change"})  # templates are the admins' to change
    seats = {m.id for p in _projects(ctx) for m in ctx.store.members(p)}
    return {"role": "admin", "focus": _focus(items),
            "figure": {"label": "Seats", "value": len(seats),
                       "how": "People with access to at least one project. Billing is not part of Welcome Wave."},
            "sections": {"setup": steps}}


# ---------------------------------------------------------------- executive
EXEC_FIGURES = ("hires", "days_to_first_merge", "milestones_on_time", "senior_hours_saved", "knowledge_gaps")


def executive(ctx: Ctx) -> dict:
    report = reporting.build(ctx, _projects(ctx))
    figures = {f["key"]: f for f in report["figures"]}
    merge = figures["days_to_first_merge"]["current"]
    gaps = figures["knowledge_gaps"]["current"]
    items = [_item("report", 3, "Read the onboarding report", "/reports", "Every figure says how it is worked out.")]
    if gaps["value"]:
        items.insert(0, _item("gaps", 2, f"{gaps['value']} knowledge gaps in the current cohort", "/reports",
                              figures["knowledge_gaps"]["how"]))
    return {"role": "executive", "focus": _focus(items),
            "figure": {"label": "Median days to the first merged change",
                       "value": merge["value"] if merge["value"] is not None else reporting.NOT_ENOUGH,
                       "how": figures["days_to_first_merge"]["how"]},
            "sections": {"report": [figures[k] for k in EXEC_FIGURES], "as_of": report["as_of"]}}


def home(ctx: Ctx) -> dict:
    return {"new_hire": new_hire, "manager": manager, "expert": expert, "admin": admin, "executive": executive}[ctx.user.role](ctx)
