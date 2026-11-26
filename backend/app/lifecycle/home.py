"""Role homes (GET /api/home): the few things this person should do next, one figure, and small sections.

  {role, focus: [{key, priority, text, link, why, explain?}], figure: {label, value, how}, sections: {...}}

A focus item an agent produced (a blocker the Coach found, a plan or completion draft of the Planner, a proposal of the
Keeper or the Planner, the Scout's brief, a handover plan) carries that agent's `explain` block (docs/AGENTS.md).

Focus items are ordered by priority (1 first), then by how long they have waited, and capped at MAX_FOCUS: a home is a
short list, not a dashboard. Hires' data in a manager's or mentor's sections is built from the visibility registry
(app/lifecycle/visibility.py), so the hire can see the same values. The setup checklist (admins) lives here too.

The product sections (frontend/src/lifecycle/Home.tsx):
  new hire          phases: the program's milestones (the board's columns: days 15, 30, 45, 60 and 90 by default), each
                    with its tasks done of all and whether it is done, current or upcoming; current: the current
                    milestone and its tasks (the board's column); questions: what they asked and how it was answered.
  manager, admin    approvals: pending decisions (count and the oldest few); activity: what people did in the projects,
                    newest first, from the audit log's product events only (never its security events); admins also get
                    hires, the same cards a manager gets."""
from __future__ import annotations

import logging
from datetime import date, timedelta

from app import legal
from app.lifecycle import blockers as B
from app.lifecycle import checkins, enrolment, flags, hires, reporting, visibility
from app.lifecycle import templates as T
from app.lifecycle.context import Ctx, HireCtx

MAX_FOCUS = 6
RESPONSE_TARGET_H = 24
MAX_CURRENT_TASKS = 8  # the current milestone's tasks on a hire's home; the board has all of them
MAX_APPROVALS = 4
MAX_ACTIVITY = 8
ACTIVITY_SCAN = 300  # audit rows read, newest first, to find MAX_ACTIVITY product events

log = logging.getLogger("home")


def _item(key: str, priority: int, text: str, link: str, why: str, explain: dict | None = None) -> dict:
    out = {"key": key, "priority": priority, "text": text, "link": link, "why": why}
    if explain:
        out["explain"] = explain
    return out


def _coach(alert, now=None) -> dict:
    from app.agents.specialists.coach.agent import blocker_explain

    return blocker_explain(alert, [B.describe(alert, now) + "."])


def _explain(key: str, why: str, *sources) -> dict:
    from app.agents.core import registry as R

    return R.explain(key, [why], list(sources))


PROPOSAL_TEXT = {
    "rerecord": lambda p: f"Re-record '{p.get('meeting_title')}' with {(p.get('speaker') or {}).get('name') or 'its speaker'}?",
    "doc_edit": lambda p: f"Review the drafted fix for {str(p.get('title'))[:80]}",
    "template_change": lambda p: (f"Review {len(p.get('changes', []))} proposed change{'' if len(p.get('changes', [])) == 1 else 's'} "
                                  f"to the {p.get('template_title')} template"),
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
    if status["next_milestone"]:  # the program's checkpoints; "milestone" on screen means the board's Day 15/30/45/60/90
        m = status["next_milestone"]
        late = m["due_date"] < ctx.clock.today()
        items.append(_item(f"milestone:{m['key']}", 2 if late else 4,
                           f"Next checkpoint: {m['title']} ({'overdue since' if late else 'due'} {m['due_date'].day} {m['due_date']:%b})",
                           _link(hc),
                           "Past its due date: finish it, or ask your manager to move it." if late else "Your plan's next step."))
    phases, current = _board(hc)
    return {"role": "new_hire", "focus": _focus(items),
            "figure": {"label": "Day", "value": hc.day, "how": f"Days since your start date ({hc.ob.start_date:%d %b %Y}), of a "
                                                                f"{hc.program.duration_days}-day program."},
            "sections": {"program": visibility.view(hc, visibility.CARD), "first_task": hc.program.first_task,
                         "phases": phases, "current": current, "questions": visibility.FIELDS["questions"].build(hc)}}


# ---------------------------------------------------------------- the hire's milestones (the board's columns)
def _phase_list(hc: HireCtx) -> list[dict]:
    """The hire's board milestones: the ones people set on their board, else the program's defaults for its length."""
    read = getattr(hc.ctx.lc, "phases", None)
    rows = read(hc.ob.id) if read else []
    if rows:
        return [{"key": p.key, "title": p.title, "due_day": p.due_day} for p in sorted(rows, key=lambda p: (p.due_day, p.position))]
    defaults = getattr(T, "default_phases", None)
    if defaults:
        return defaults(hc.program.duration_days)
    return [{"key": f"day_{d}", "title": f"Day {d}", "due_day": d} for d in T.checkin_days(hc.program.duration_days)]


def _board(hc: HireCtx) -> tuple[list[dict], dict | None]:
    """Every milestone with its tasks done of all, and the current one with its tasks. A task is in the milestone a person put
    it in on the board, else the first one due on or after its due date (no due date: the last); the board's own rule."""
    phases = _phase_list(hc)
    if not phases:
        return [], None
    start = hc.ob.start_date
    placed = {}
    read = getattr(hc.ctx.lc, "placements", None)
    if read:
        placed = {p.task_id: p.phase_key for p in read(hc.ob.id) if p.phase_key}
    keys = {p["key"] for p in phases}
    columns: dict[str, list] = {p["key"]: [] for p in phases}
    for t in hc.tasks:
        if t.status in visibility.HIDDEN_FROM_HIRE:
            continue
        key = placed.get(t.id)
        if key not in keys:
            due = t.due_date
            key = next((p["key"] for p in phases if due is not None and due <= start + timedelta(days=p["due_day"])), phases[-1]["key"])
        columns[key].append(t)
    today = hc.ctx.clock.today()
    out, current = [], None
    for p in phases:
        tasks = columns[p["key"]]
        done = sum(t.status == "done" for t in tasks)
        due: date = start + timedelta(days=p["due_day"])
        finished = (bool(tasks) and done == len(tasks)) or (not tasks and due < today)  # all its work done, or an empty one past
        state = "done" if finished else "current" if current is None else "upcoming"
        row = {"key": p["key"], "title": p["title"], "due_day": p["due_day"], "due_date": due, "state": state,
               "tasks_done": done, "tasks_total": len(tasks)}
        out.append(row)
        if state == "current":
            order = {"in_progress": 0, "changes_requested": 1, "submitted": 2, "approved": 3, "done": 4}
            ranked = sorted(tasks, key=lambda t: (order.get(t.status, 3), t.due_date or date.max, t.title))
            current = {**row, "tasks": [{"id": t.id, "title": t.title, "kind": t.kind, "status": t.status, "due_date": t.due_date,
                                         "pr_number": getattr(t, "pr_number", None)} for t in ranked[:MAX_CURRENT_TASKS]]}
    return out, current


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
            from app.agents.specialists.planner.agent import completion_view

            items.append(_item(f"complete:{hc.ob.id}", 3, f"Review {name}'s completion", _link(hc, "completion"),
                               f"Day {hc.day} of {hc.program.duration_days}.", completion_view(hc)["explain"]))
    items += _owned_access(ctx, team) + _feedback_queue(ctx, projects) + _proposals(ctx, projects, {"rerecord", "doc_edit"})
    on_track = sum(c["status"]["on_track"] for c in cards)
    pending = sum(len([a for a in ctx.store.approvals(pid) if a.kind == "task_plan"]) for pid in projects)
    return {"role": "manager", "focus": _focus(items),
            "figure": {"label": "Hires on track", "value": f"{on_track} of {len(cards)}" if cards else None,
                       "how": "On track: no overdue checkpoint and no blocker unresolved for three business days."},
            "sections": {"hires": cards, "plans_awaiting_approval": pending, "approvals": _approvals(ctx, projects),
                         "activity": _activity(ctx, team, projects=set(projects))}}


# ---------------------------------------------------------------- pending decisions (managers and admins)
APPROVAL_TITLE = {
    "task_plan": lambda ctx, a: "Approve " + " and ".join(str(p.get("new_hire") or "a new hire").replace(" (New hire)", "") + "'s plan"
                                                         for p in (a.payload.get("plans") or [{}])[:2]),
    "review": lambda ctx, a: "Review the pull request for " + (getattr(ctx.store.get_task(a.payload.get("task_id") or ""), "title", None)
                                                              or "a task"),
    "kt_followup": lambda ctx, a: f"Choose follow-ups from '{a.payload.get('title') or 'a KT session'}'",
    **{k: (lambda ctx, a, f=f: f(a.payload)) for k, f in PROPOSAL_TEXT.items()},
}


def _approvals(ctx: Ctx, projects: list[str]) -> dict:
    """What waits for a decision, oldest first (the longest wait leads), with a title and a link to it."""
    rows = sorted((a for pid in projects for a in ctx.store.approvals(pid)), key=lambda a: a.created_at)
    items = []
    for a in rows[:MAX_APPROVALS]:
        try:
            title = APPROVAL_TITLE[a.kind](ctx, a)
        except Exception:  # an older payload without the fields a title reads: the kind still says what it is
            title = a.kind.replace("_", " ").capitalize()
        items.append({"id": a.id, "kind": a.kind, "title": str(title)[:160], "created_at": a.created_at,
                      "link": f"/approvals/{a.id}?project={a.project_id}"})
    return {"count": len(rows), "items": items}


# ---------------------------------------------------------------- recent activity (managers and admins)
DECISION = {"approved": "approved", "rejected": "rejected", "partially_approved": "partly approved"}
# a task's status in the board's own words (app/lifecycle/board.py COLUMN_OF; the board shows these labels)
BOARD_STATUS = {"approved": "To do", "in_progress": "In progress", "changes_requested": "In progress",
                "submitted": "Done, waiting for sign-off", "done": "Done"}
COLLAPSE_S = 600  # moves by one person on one board within ten minutes read as one line: drag and drop never floods the feed
BOARD_MOVES = frozenset({"board.task.moved", "board.task.rescheduled"})


def _of(name: str) -> str:
    return name if name == "their" else f"{name}'s"


def _quoted(title) -> str:
    return f"'{str(title)[:80]}'" if title else "a task"


def _board_line(action: str, d: dict, board: str, link: str) -> tuple[str, str] | None:
    """What a board event says ("moved 'Fix the Docker check' to In progress on Vijay's board"), for app/lifecycle/board.py."""
    task = _quoted(d.get("title"))
    if action == "board.task.moved":
        to = str(d.get("to") or "")
        if to == "done" and d.get("from") == "submitted":
            return f"signed off {task} on {board}", link
        if to == "changes_requested":
            return f"asked for changes to {task} on {board}", link
        return f"moved {task} to {BOARD_STATUS.get(to, 'another column')} on {board}", link
    if action == "board.task.rescheduled":
        where = (f"Day {d['to_day']} · {str(d.get('to_title') or '')[:60]}".rstrip(" ·") if d.get("to_day")
                 else str(d.get("to_title") or "another milestone")[:60])
        return f"moved {task} to {where} on {board}", link
    if action == "board.task.added":
        return f"added {task} to {board}", link
    if action == "board.task.removed":
        return f"removed {task} from {board}", link
    if action == "board.task.changed":
        return f"edited {task} on {board}", link
    if action.startswith(("board.milestone.", "board.milestones.")):
        return f"updated the milestones on {board}", link
    return None


def _activity_line(row: dict, hire_of, person_of, hire_id_of=lambda ob_id: None) -> tuple[str, str | None] | None:
    """What an audit row says, as a phrase after the actor's name, and where it leads; None for rows that are not product
    events (sign-ins, exports, role changes, the audit log's own reads: the security log stays in GET /api/audit)."""
    action, target, d = row["action"], str(row.get("target") or ""), row.get("detail") or {}
    ob_id = str(d.get("onboarding_id") or target.split(":")[0])
    hire = hire_of(ob_id)
    if hire and hire_id_of(ob_id) == row.get("actor_id"):
        hire = "their"  # "Sara wrote their day-30 check-in", not "Sara wrote Sara's"
    program = f"/program/{ob_id}" if hire else None
    if action.startswith("board."):
        return _board_line(action, d, f"{_of(hire)} board" if hire else "a hire's board", f"/board/{ob_id}")
    decided = DECISION.get(str(d.get("decision") or "approved"), "decided")
    lines = {
        "invitation.accepted": ("joined the project", None),
        "onboarding.enrolled": (f"enrolled {hire}" if hire and hire != "their" else "enrolled a new hire", program),
        "onboarding.started": (f"started {_of(hire)} onboarding" if hire else "started an onboarding", program),
        "approval.task_plan.decided": (f"{decided} {_of(hire)} plan" if hire else f"{decided} a plan", program or "/approvals"),
        "approval.review.decided": (f"{decided} a pull request review", None),
        "approval.kt_followup.decided": ("chose follow-up KT sessions", "/kt-planner"),
        "approval.rerecord.decided": (f"{decided} a re-recording", None),
        "approval.doc_edit.decided": (f"{decided} a documentation fix", None),
        "approval.template_change.decided": (f"{decided} a template change", "/templates"),
        "meeting.scheduled": ("scheduled a KT session", f"/meetings?call={target}"),
        "kt_plan.scheduled": ("booked a KT session", f"/meetings?call={target}"),
        "meeting.recording_uploaded": ("added a KT recording", f"/meetings/{target}"),
        "meeting.recording_imported": ("added a KT recording", f"/meetings/{target}"),
        "handover.created": (f"started a handover for {person_of(d.get('user_id')) or 'someone'}", "/handovers"),
        "checkin.written": (f"wrote {_of(hire)} day-{d.get('day')} check-in" if hire else "wrote a check-in",
                            f"{program}?tab=checkins" if program else None),
        "blocker.resolved": ("resolved a blocker", None),
        "first_task.selected": (f"picked {_of(hire)} first task" if hire else "picked a first task", program),
        "completion.complete": (f"completed {_of(hire)} onboarding" if hire else "completed an onboarding", program),
        "expert.answered": ("answered a question", None),
        "document.uploaded": (f"added the document '{str(d.get('title') or 'untitled')[:80]}'", None),
        "repo.connected": (f"connected {str(d.get('full_name') or 'a repository')[:100]}", "/code"),
        "milestone.updated": (f"updated {_of(hire)} checkpoints" if hire else "updated a checkpoint", program),
        "task.moved": (f"moved a task to {BOARD_STATUS.get(str(d.get('to')), str(d.get('to') or 'another column').replace('_', ' '))[:60]}",
                       None),
        "preboarding.acknowledged": ("acknowledged a pre-boarding document", program),
        "answer.verified": ("verified an answer", None),
    }
    return lines.get(action)


class _Projects:
    """The project an activity row happened in: from its detail (`project_id`, `onboarding_id`; board events carry both) or
    its target, looked up once per target. None when it cannot be told; a manager's feed leaves such rows out."""

    def __init__(self, ctx: Ctx, projects: set[str]):
        self.ctx, self.projects, self.seen = ctx, projects, {}

    def _onboarding(self, ob_id) -> str | None:
        ob = self.ctx.store.get_onboarding(str(ob_id)) if ob_id else None
        return str(ob.project_id) if ob else None

    def _lookup(self, action: str, target: str, d: dict) -> str | None:
        store, lc = self.ctx.store, self.ctx.lc
        if d.get("onboarding_id"):
            return self._onboarding(d["onboarding_id"])
        if action.startswith("meeting.") or action == "kt_plan.scheduled":
            m = store.get_meeting(target)
            return str(m.project_id) if m else None
        if action == "document.uploaded":
            doc = store.get_document(target)
            return str(doc["project_id"]) if doc and doc.get("project_id") else None
        if action == "repo.connected":
            repo = store.get_repo(target)
            return str(repo.project_id) if repo else None
        if action == "handover.created":
            h = lc.get_handover(target)
            return str(h.project_id) if h else None
        if action == "task.moved":
            t = store.get_task(target)
            return self._onboarding(t.onboarding_id) if t else None
        if action == "blocker.resolved":
            b = lc.get_blocker(target)
            return self._onboarding(b.onboarding_id) if b else None
        if action == "expert.answered":
            q = store.get_expert_request(target)
            return str(q["project_id"]) if q and q.get("project_id") else None
        if action == "answer.verified":
            return next((pid for pid in self.projects if any(v.id == target for v in lc.verified_answers(pid))), None)
        if action.startswith("approval."):
            a = None if self._onboarding(target) else store.get_approval(target)  # the seed names the onboarding
            return str(a.project_id) if a else self._onboarding(target)
        return self._onboarding(target.split(":")[0])  # onboardings, and "<onboarding id>:<key>" for checkpoints

    def of(self, row: dict) -> str | None:
        d = row.get("detail") or {}
        if d.get("project_id"):
            return str(d["project_id"])
        action, target = row["action"], str(row.get("target") or "")
        key = (action, target, str(d.get("onboarding_id") or ""))
        if key not in self.seen:
            try:
                self.seen[key] = self._lookup(action, target, d)
            except Exception:  # noqa: BLE001 - a malformed or foreign id: the row cannot be placed, so a manager does not see it
                self.seen[key] = None
        return self.seen[key]


def _activity(ctx: Ctx, team: list[HireCtx], projects: set[str] | None = None) -> list[dict]:
    """The newest product events in the organisation (admins: projects=None) or in the manager's own projects (managers).
    A manager's feed is scoped by where an event happened, never by who did it: someone on two projects is seen only in the
    manager's. Runs of board moves by one person on one board within COLLAPSE_S read as one line."""
    from app.adapters import audit_store

    names = {hc.ob.id: (ctx.person(hc.ob.new_hire_id) or {}).get("name") for hc in team}
    hire_ids = {hc.ob.id: hc.ob.new_hire_id for hc in team}
    hire_of = lambda ob_id: (names.get(ob_id) or "").replace(" (New hire)", "") or None  # noqa: E731
    person_of = lambda uid: ((ctx.person(uid) or {}).get("name") or "").split(" (")[0] or None  # noqa: E731
    where = _Projects(ctx, projects or set())
    try:
        rows, _ = audit_store.page(ctx.store, audit_store.AuditFilter(org_id=ctx.user.org_id), ACTIVITY_SCAN, 0)
    except Exception:  # the home never fails because the log could not be read
        log.exception("home activity: the audit log could not be read")
        return []
    out: list[dict] = []
    for r in rows:  # newest first
        if not r.get("actor_id"):
            continue
        line = _activity_line(r, hire_of, person_of, hire_ids.get)
        if not line or (projects is not None and where.of(r) not in projects):
            continue
        text, link = line
        d = r.get("detail") or {}
        board = str(d.get("onboarding_id") or "")
        last = out[-1] if out else None
        if (last and r["action"] in BOARD_MOVES and last["action"] in BOARD_MOVES and last["actor"]["id"] == r["actor_id"]
                and last.get("_board") == board and abs((last["_first"] - r["created_at"]).total_seconds()) <= COLLAPSE_S):
            last["_n"] += 1
            last["_first"] = r["created_at"]
            hire = hire_of(board)
            whose = "their" if hire and hire_ids.get(board) == r["actor_id"] else f"{hire}'s" if hire else "a hire's"
            last["text"] = f"moved {last['_n']} tasks on {whose} board"
            continue
        if len(out) == MAX_ACTIVITY:
            break
        out.append({"id": str(r["id"]), "at": r["created_at"], "action": r["action"], "text": text, "link": link,
                    "actor": {"id": r["actor_id"], "name": (r.get("actor_name") or "Someone").split(" (")[0]},
                    "_board": board, "_first": r["created_at"], "_n": 1})
    for a in out:
        del a["_board"], a["_first"], a["_n"]
    return out


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
    # first: the agreement about the organisation's data, before its code, documents and recordings go in (app/legal.py)
    ("dpa", "Accept the Data Processing Agreement", "How Welcome Wave handles your organisation's personal data. An admin accepts it once."),
    ("repository", "Connect a repository", "The assistant, the code view and the plans read it."),
    ("teams", "Add teams", "Programs, surveys and reports group hires by team."),
    ("templates", "Review the program templates", "Each role track and level has one; change them to fit."),
    ("people", "Invite managers and experts", "Someone has to approve plans and answer questions."),
    ("first_enrolment", "Enrol the first hire", "Invitation, program and drafted plan in one step."),
    ("estimate", "Check the senior-time estimate", "Minutes per avoided interruption: 15 unless you change it."),
]


CHECKED_ONLY = frozenset({"dpa"})  # steps that are done only when the data says so, never by an admin's "Done"


def setup_steps(ctx: Ctx) -> list[dict]:
    org = ctx.user.org_id
    projects = _projects(ctx)
    settings = ctx.lc.org_settings(org)
    done = {
        "dpa": any(r["version"] == legal.VERSIONS["dpa"] for r in ctx.store.legal_acceptances(org, document="dpa")),
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
        # the agreement is accepted or it is not: "Done" by hand would say it was (a signed copy elsewhere is "skipped")
        marked = key in completed and key not in CHECKED_ONLY
        status = "done" if done[key] or marked else "skipped" if key in skipped else "todo"
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
    projects = _projects(ctx)
    seats = {m.id for p in projects for m in ctx.store.members(p)}
    team = [hires.refresh(h) for h in hires.of_projects(ctx, projects)]
    return {"role": "admin", "focus": _focus(items),
            "figure": {"label": "People on projects", "value": len(seats),
                       "how": "Everyone with access to at least one project. People lists the whole organisation. Billing is "
                              "not part of Welcome Wave."},
            "sections": {"setup": steps, "hires": [visibility.view(hc, visibility.CARD) for hc in team],
                         "approvals": _approvals(ctx, projects), "activity": _activity(ctx, team)}}


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
