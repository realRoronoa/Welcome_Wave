"""The milestone board: one hire's tasks by milestone (Day 15, 30, 45, 60, 90 ...) and by state (To do, In progress, Done).

Milestones
  A board milestone is a day of the program and a title ("Day 30 · First change shipped"). In the code it is a *phase*, so it
  never mixes with the checkpoints of `onboarding_milestones` (pre-boarding done, first pull request merged ...), which the
  board shows inside the milestone whose days they fall in. A program gets its template's milestones the first time its board
  is read (templates.phases_of); a manager or admin adds, renames, re-dates, reorders and deletes them at any time.

Where a task sits
  In the milestone a person put it in (task_placements), else in the first milestone, by day, due on or after the task's due
  date (after the last one: the last one; no due date: the first one). Re-dating a milestone re-flows the tasks placed by date.

Columns are the task's own status, so a pull request that moves a task moves its card too:
      To do        approved
      In progress  in_progress, changes_requested (marked "Changes requested")
      Done         submitted (marked "Waiting for sign-off", or "In review" with a pull request), done
  Proposed tasks wait in Approvals and are not on the board (managers see how many); rejected (removed) ones are gone.

Moved
  A task a manager moved to another milestone keeps where it came from, when and by whom (task_moves): its card says
  "Moved from Day 15", and the milestone it left lists it under "Moved out", so the history reads from both sides.

Who (checked in app/api/board.py, docs/PERMISSIONS.md)
  The hire: their own board; moves their own cards between columns. Done means submitted: their manager signs it off, as with
  PATCH /api/tasks, and only a manager reopens a done task.
  Managers of the project and admins: every hire's board, and every change (columns, sign-off, milestones of a task, the
  milestones themselves, adding, editing and removing tasks). Nobody else: a mentor reads the program page, not the board.
Every change is audited (`board.*`), and the other side is told when it concerns them."""
from __future__ import annotations

import re
from collections import defaultdict
from datetime import date, timedelta

from app.agents.core.schemas import ProposedTask
from app.lifecycle import progress
from app.lifecycle import templates as T
from app.lifecycle.context import HireCtx
from app.ports import Phase, TaskMove, TaskPlacement, TaskRow, User

COLUMNS = (("todo", "To do"), ("in_progress", "In progress"), ("done", "Done"))
COLUMN_OF = {"approved": "todo", "in_progress": "in_progress", "changes_requested": "in_progress", "submitted": "done", "done": "done"}
MAX_DAY = 730


class BoardError(Exception):
    """A change the board refuses: `status` is the HTTP status the API answers with."""

    def __init__(self, status: int, message: str, code: str | None = None):
        super().__init__(message)
        self.status, self.message, self.code = status, message, code


# ---------------------------------------------------------------- milestones
def phases(hc: HireCtx) -> list[Phase]:
    """The hire's board milestones, left to right; the template's (or the defaults) the first time."""
    lc = hc.ctx.lc
    rows = lc.phases(hc.ob.id)
    if rows:
        return rows
    template = lc.get_template(hc.program.template_id)
    specs = sorted(T.phases_of(template, hc.program.duration_days), key=lambda p: int(p["due_day"]))
    return lc.ensure_phases(hc.ob.id, [Phase(onboarding_id=hc.ob.id, key=p["key"], title=p["title"], due_day=int(p["due_day"]), position=i)
                                       for i, p in enumerate(specs)])


def _by_day(ps: list[Phase]) -> list[Phase]:
    return sorted(ps, key=lambda p: (p.due_day, p.position, p.key))


def _window(ps: list[Phase], p: Phase) -> tuple[int, int]:
    """The program days a milestone covers: the day after the one before it (by day) to its own."""
    ordered = _by_day(ps)
    i = ordered.index(p)
    return (ordered[i - 1].due_day + 1 if i else 0, p.due_day)


def _phase_for_day(ps: list[Phase], day: int | None) -> Phase:
    ordered = _by_day(ps)
    if day is None:
        return ordered[0]
    return next((p for p in ordered if p.due_day >= day), ordered[-1])


def _day_of(hc: HireCtx, t: TaskRow) -> int | None:
    return (t.due_date - hc.ob.start_date).days if t.due_date else None


def _slug(title: str, taken: set[str]) -> str:
    base = re.sub(r"[^a-z0-9]+", "_", title.lower()).strip("_")[:34] or "milestone"
    key, n = base, 2
    while key in taken:
        key, n = f"{base}_{n}", n + 1
    return key


# ---------------------------------------------------------------- where each task sits
def _placed(hc: HireCtx, ps: list[Phase], tasks: list[TaskRow]) -> dict[str, tuple[Phase, int | None, bool]]:
    """task id -> (its milestone, its position in its column or None, placed by a person)."""
    by_key = {p.key: p for p in ps}
    rows = {r.task_id: r for r in hc.ctx.lc.placements(hc.ob.id)}
    out = {}
    for t in tasks:
        r = rows.get(t.id)
        explicit = by_key.get(r.phase_key) if r and r.phase_key else None
        out[t.id] = (explicit or _phase_for_day(ps, _day_of(hc, t)), r.position if r else None, explicit is not None)
    return out


def _on_board(hc: HireCtx) -> list[TaskRow]:
    return [t for t in hc.tasks if t.status in COLUMN_OF]


def _order_key(t: TaskRow, position: int | None):
    return (position is None, position if position is not None else 0, t.due_date or date.max, t.title.lower(), t.id)


# ---------------------------------------------------------------- what each person may do with a card
def target_status(t: TaskRow, column: str, manage: bool, review_pending: bool, changes: bool = False,
                  sign_off: bool = False) -> str | None:
    """The status a move to `column` gives the task, None when its status does not change (a new place in the same column).
    Raises BoardError when the caller may not make it. Managers move anything (a review waiting in Approvals is decided there),
    ask for changes, and sign off a submitted task (`sign_off`: it stays in Done); the hire works through To do, In progress and
    Done, where Done is "submitted" for their manager to sign off."""
    now = COLUMN_OF[t.status]
    if column not in COLUMN_OF.values():
        raise BoardError(422, "Unknown column")
    if (changes or sign_off) and not manage:
        raise BoardError(403, "Only a manager signs off or asks for changes", "manager_only")
    if sign_off:
        column = "done"
    if column == now and not (changes and t.status != "changes_requested") and not (sign_off and t.status != "done"):
        return None
    if review_pending:
        raise BoardError(409, "A review of its pull request waits in Approvals: decide it there.", "review_pending")
    if manage:
        return {"todo": "approved", "in_progress": "changes_requested" if changes else "in_progress", "done": "done"}[column]
    if t.status == "done":
        raise BoardError(403, "Your manager reopens a done task.", "done")
    if column == "todo":
        if t.status == "in_progress":
            return "approved"
        raise BoardError(409, "Changes were asked for, or it waits for sign-off: move it to In progress instead.", "not_back")
    if column == "in_progress":
        if t.status == "submitted" and t.pr_number:
            raise BoardError(409, "Its pull request is in review: the review moves it.", "in_review")
        return "in_progress"
    return "submitted"  # Done: for the manager to sign off


def allowed_columns(t: TaskRow, manage: bool, review_pending: bool) -> list[str]:
    out = []
    for column, _ in COLUMNS:
        try:
            target_status(t, column, manage, review_pending)
            out.append(column)
        except BoardError:
            pass
    return out


def _review_pending(hc: HireCtx, tasks: list[TaskRow]) -> set[str]:
    with_pr = [t.id for t in tasks if t.pr_number]
    if not with_pr:
        return set()
    store = hc.ctx.store
    return {tid for tid in with_pr if any(a.status == "pending" for a in store.review_approvals(tid))}


# ---------------------------------------------------------------- the board as the web app reads it
def view(hc: HireCtx, manage: bool) -> dict:
    """The board for one caller: milestones with their counts, checkpoints and moved-out tasks, and every card with the
    columns this caller may move it to."""
    ctx = hc.ctx
    ps = phases(hc)
    tasks = _on_board(hc)
    placed = _placed(hc, ps, tasks)
    pending = _review_pending(hc, tasks)
    moves: dict[str, list[TaskMove]] = defaultdict(list)
    for m in ctx.lc.task_moves(hc.ob.id):
        moves[m.task_id].append(m)
    today, start = ctx.clock.today(), hc.ob.start_date
    active = hc.ob.status == "active"
    titles = {p.key: p.title for p in ps}
    days = {p.key: p.due_day for p in ps}

    cards = []
    for t in sorted(tasks, key=lambda t: _order_key(t, placed[t.id][1])):
        phase, _, pinned = placed[t.id]
        column = COLUMN_OF[t.status]
        history = moves.get(t.id, [])
        last = history[-1] if history else None
        moved = None
        if last and last.to_phase == phase.key:
            moved = {"from_key": last.from_phase, "from_title": titles.get(last.from_phase or "", last.from_title),
                     "from_day": days.get(last.from_phase or ""), "at": last.at, "by": ctx.person(last.actor_id)}
        badge = ({"submitted": "In review" if t.pr_number else "Waiting for sign-off", "changes_requested": "Changes requested"}
                 .get(t.status))
        cards.append({
            "id": t.id, "title": t.title, "description": t.description, "kind": t.kind, "status": t.status, "column": column,
            "badge": badge, "milestone": phase.key, "pinned": pinned, "due_date": t.due_date,
            "due_day": _day_of(hc, t), "overdue": bool(column != "done" and t.due_date and t.due_date < today),
            "kt": t.origin == "meeting", "origin": t.origin, "pr_number": t.pr_number, "pr_url": t.pr_url,
            "acceptance_criteria": t.acceptance_criteria, "review_pending": t.id in pending, "moved": moved,
            "history": [{"from_key": m.from_phase, "from_title": m.from_title, "to_key": m.to_phase, "to_title": m.to_title,
                         "from_due": m.from_due, "to_due": m.to_due, "at": m.at, "by": ctx.person(m.actor_id)} for m in history],
            "moves": allowed_columns(t, manage, t.id in pending) if (manage or active) else [],
        })

    checkpoints = progress.milestone_view(hc)
    current = next((p for p in _by_day(ps) if hc.day <= p.due_day), None) if hc.day >= 0 else _by_day(ps)[0]
    out_ps = []
    for p in ps:
        lo, hi = _window(ps, p)
        mine = [c for c in cards if c["milestone"] == p.key]
        count = {col: sum(c["column"] == col for c in mine) for col, _ in COLUMNS}
        done = sum(c["status"] == "done" for c in mine)
        due = start + timedelta(days=p.due_day)
        overdue = sum(c["overdue"] for c in mine)
        complete = bool(mine) and done == len(mine)
        state = ("complete" if complete else "overdue" if due < today and mine else "current" if current is p
                 else "past" if due < today else "upcoming")
        # left this milestone for another, and not back: the latest move out of it per task
        gone, seen = [], set()
        for c in mine:
            seen.add(c["id"])
        for tid, history in moves.items():
            out_of = [m for m in history if m.from_phase == p.key]
            card = next((c for c in cards if c["id"] == tid), None)
            if out_of and card and tid not in seen:
                m = out_of[-1]
                gone.append({"task_id": tid, "title": card["title"], "to_key": card["milestone"], "to_title": titles.get(card["milestone"]),
                             "at": m.at, "by": ctx.person(m.actor_id)})
        out_ps.append({
            "key": p.key, "title": p.title, "due_day": p.due_day, "due_date": due, "from_day": lo, "position": p.position,
            "state": state, "total": len(mine), "done": done, "awaiting": sum(c["status"] == "submitted" for c in mine),
            "overdue": overdue, "counts": count,
            "checkpoints": [{"key": m["key"], "title": m["title"], "due_day": m["due_day"], "state": m["state"]}
                            for m in checkpoints if lo <= m["due_day"] <= hi or (p is _by_day(ps)[-1] and m["due_day"] > hi)],
            "moved_out": sorted(gone, key=lambda g: g["at"], reverse=True),
        })
    hire = hc.hire
    return {
        "onboarding_id": hc.ob.id, "project_id": hc.ob.project_id, "status": hc.ob.status,
        "hire": {"id": hire.id, "name": hire.name} if hire else None, "role_title": hc.ob.role_title,
        "start_date": start, "today": today, "day": hc.day, "duration_days": hc.program.duration_days,
        "columns": [{"key": k, "title": title} for k, title in COLUMNS],
        "milestones": out_ps, "tasks": cards, "current": current.key if current else None,
        "waiting_approval": sum(t.status == "proposed" for t in hc.tasks),
        "can": {"move": manage or active, "reschedule": manage, "edit": manage},
    }


def summary(hc: HireCtx) -> dict:
    """One line of the hire picker: progress and where the hire is."""
    ps = phases(hc)
    tasks = _on_board(hc)
    today = hc.ctx.clock.today()
    current = next((p for p in _by_day(ps) if hc.day <= p.due_day), None) if hc.day >= 0 else _by_day(ps)[0]
    hire = hc.hire
    return {"onboarding_id": hc.ob.id, "name": hire.name if hire else None, "role_title": hc.ob.role_title, "day": hc.day,
            "duration_days": hc.program.duration_days, "status": hc.ob.status, "total": len(tasks),
            "done": sum(t.status == "done" for t in tasks),
            "overdue": sum(1 for t in tasks if COLUMN_OF[t.status] != "done" and t.due_date and t.due_date < today),
            "current": {"key": current.key, "title": current.title, "due_day": current.due_day} if current else None}


# ---------------------------------------------------------------- moving a card
def _audit(hc: HireCtx, user: User, action: str, target: str, detail: dict) -> None:
    """Every board entry names its onboarding and project: the audit log links it to the board, and the home's activity feed
    shows it only to people on that project."""
    hc.ctx.store.audit(user.org_id, user.id, action, target, {**detail, "onboarding_id": hc.ob.id, "project_id": hc.ob.project_id})


def _tell(hc: HireCtx, user_id: str | None, user: User, text: str) -> None:
    if user_id and user_id != user.id:
        hc.ctx.store.notify(user_id, "board", text, f"/board/{hc.ob.id}?project={hc.ob.project_id}")


def _card(hc: HireCtx, task_id: str) -> TaskRow:
    t = next((t for t in hc.tasks if t.id == task_id), None)
    if t is None:
        raise BoardError(404, "Task not found")
    if t.status == "proposed":
        raise BoardError(409, "This task waits for approval: decide it in Approvals.", "awaiting_approval")
    if t.status not in COLUMN_OF:
        raise BoardError(404, "Task not found")
    return t


def _phase(ps: list[Phase], key: str) -> Phase:
    p = next((p for p in ps if p.key == key), None)
    if p is None:
        raise BoardError(404, "Milestone not found")
    return p


def _fit_due(hc: HireCtx, ps: list[Phase], p: Phase, due: date | None) -> date:
    """A task moved into a milestone is due inside it: its own date when that falls in the milestone's days, else the
    milestone's day."""
    lo, hi = _window(ps, p)
    day = (due - hc.ob.start_date).days if due else None
    if day is not None and lo <= day <= hi:
        return due
    return hc.ob.start_date + timedelta(days=p.due_day)


def move(hc: HireCtx, user: User, task_id: str, manage: bool, column: str | None = None, milestone: str | None = None,
         before_id: str | None = None, changes: bool = False, sign_off: bool = False) -> dict:
    """Move a card: to another column (its status), to another milestone (managers), and to a place in its column (before
    `before_id`, else at the end). Everything is checked before anything is written."""
    if not manage and hc.ob.status != "active":
        raise BoardError(409, "This program is not active.", "inactive")
    t = _card(hc, task_id)
    ps = phases(hc)
    tasks = _on_board(hc)
    placed = _placed(hc, ps, tasks)
    here, _, pinned = placed[t.id]
    to = _phase(ps, milestone) if milestone else here
    if to.key != here.key and not manage:
        raise BoardError(403, "Your manager moves tasks between milestones.", "manager_only")
    pending = t.id in _review_pending(hc, [t])
    status = target_status(t, column or COLUMN_OF[t.status], manage, pending, changes, sign_off)
    if before_id and before_id not in placed:
        raise BoardError(422, "before_id is not a card on this board")

    store, lc, now = hc.ctx.store, hc.ctx.lc, hc.ctx.clock.now()
    if status:
        store.update_task(t.id, status=status)
        _audit(hc, user, "board.task.moved", t.id, {"from": t.status, "to": status, "title": t.title})
        if user.id == hc.ob.new_hire_id:
            text = (f"{user.name} finished '{t.title}': sign it off on their board." if status == "submitted"
                    else f"{user.name} started '{t.title}'." if status == "in_progress" else None)
            if text:
                _tell(hc, hc.ob.manager_id, user, text)
        elif status in {"done", "changes_requested"}:
            _tell(hc, hc.ob.new_hire_id, user, f"{user.name} signed off '{t.title}'." if status == "done"
                  else f"{user.name} asked for changes to '{t.title}'.")
    if to.key != here.key:
        due = _fit_due(hc, ps, to, t.due_date)
        if due != t.due_date:
            store.update_task(t.id, due_date=due)
        lc.add_task_move(TaskMove(onboarding_id=hc.ob.id, task_id=t.id, from_phase=here.key, to_phase=to.key, from_title=here.title,
                                  to_title=to.title, from_due=t.due_date if due != t.due_date else None,
                                  to_due=due if due != t.due_date else None, actor_id=user.id, at=now))
        _audit(hc, user, "board.task.rescheduled", t.id, {"from": here.key, "to": to.key, "title": t.title, "from_title": here.title,
                                                          "to_title": to.title, "from_day": here.due_day, "to_day": to.due_day,
                                                          **({"due_date": due.isoformat()} if due != t.due_date else {})})
        _tell(hc, hc.ob.new_hire_id, user, f"{user.name} moved '{t.title}' to Day {to.due_day} · {to.title}.")
        pinned = True
    # its place in its column: renumber that column of that milestone with the card where it was dropped
    new_column = COLUMN_OF[status or t.status]
    line = [x for x in sorted(tasks, key=lambda x: _order_key(x, placed[x.id][1]))
            if x.id != t.id and placed[x.id][0].key == to.key and COLUMN_OF[x.status] == new_column]
    at = next((i for i, x in enumerate(line) if x.id == before_id), len(line))
    line.insert(at, t)
    lc.save_placements(hc.ob.id, [TaskPlacement(task_id=x.id, onboarding_id=hc.ob.id, position=i,
                                                phase_key=(to.key if (x.id == t.id and pinned) else
                                                           placed[x.id][0].key if placed[x.id][2] else None))
                                  for i, x in enumerate(line)])
    hc.forget("tasks")
    return view(hc, manage)


# ---------------------------------------------------------------- managers: tasks
def add_task(hc: HireCtx, user: User, title: str, description: str, kind: str, milestone: str | None, due: date | None) -> dict:
    ps = phases(hc)
    p = _phase(ps, milestone) if milestone else _phase_for_day(ps, max(hc.day, 0))
    due = _fit_due(hc, ps, p, due) if due else hc.ob.start_date + timedelta(days=p.due_day)
    store = hc.ctx.store
    days = max(1, min(365, (due - hc.ob.start_date).days))
    row = store.create_tasks(hc.ob.id, [ProposedTask(title=title, description=description or title, kind=kind, due_in_days=days,
                                                     acceptance_criteria=[], source="plan")], hc.ob.start_date)[0]
    store.update_task(row.id, status="approved", origin="manager", due_date=due)
    hc.ctx.lc.save_placements(hc.ob.id, [TaskPlacement(task_id=row.id, onboarding_id=hc.ob.id, phase_key=p.key)])
    _audit(hc, user, "board.task.added", row.id, {"title": title, "milestone": p.key, "to_title": p.title, "to_day": p.due_day})
    _tell(hc, hc.ob.new_hire_id, user, f"{user.name} added '{title}' to Day {p.due_day} · {p.title}.")
    hc.forget("tasks")
    return view(hc, True)


def edit_task(hc: HireCtx, user: User, task_id: str, fields: dict) -> dict:
    t = _card(hc, task_id)
    changes = {k: v for k, v in fields.items() if v is not None and getattr(t, k) != v}
    if changes:
        hc.ctx.store.update_task(t.id, **changes)
        _audit(hc, user, "board.task.changed", t.id, {"fields": sorted(changes), "title": changes.get("title", t.title)})
    hc.forget("tasks")
    return view(hc, True)


def remove_task(hc: HireCtx, user: User, task_id: str) -> dict:
    """Removed from the board: the task is rejected, as a plan's unwanted task is (its history and reviews stay)."""
    t = _card(hc, task_id)
    if t.id in _review_pending(hc, [t]):
        raise BoardError(409, "A review of its pull request waits in Approvals: decide it there.", "review_pending")
    hc.ctx.store.update_task(t.id, status="rejected")
    _audit(hc, user, "board.task.removed", t.id, {"title": t.title, "from": t.status})
    _tell(hc, hc.ob.new_hire_id, user, f"{user.name} removed '{t.title}' from your board.")
    hc.forget("tasks")
    return view(hc, True)


# ---------------------------------------------------------------- managers: the milestones themselves
def _check_day(ps: list[Phase], day: int, key: str | None = None) -> None:
    if not 1 <= day <= MAX_DAY:
        raise BoardError(422, f"A milestone's day is between 1 and {MAX_DAY}")
    other = next((p for p in ps if p.due_day == day and p.key != key), None)
    if other:
        raise BoardError(409, f"Day {day} already has a milestone: {other.title}.", "day_taken")


def _renumber(hc: HireCtx, ordered: list[Phase]) -> None:
    for i, p in enumerate(ordered):
        if p.position != i:
            hc.ctx.lc.update_phase(hc.ob.id, p.key, position=i)


def add_phase(hc: HireCtx, user: User, title: str, due_day: int) -> dict:
    ps = phases(hc)
    if len(ps) >= T.MAX_PHASES:
        raise BoardError(409, f"A board has at most {T.MAX_PHASES} milestones.", "too_many")
    _check_day(ps, due_day)
    key = _slug(title, {p.key for p in ps})
    ordered = list(ps)
    at = next((i for i, p in enumerate(ordered) if p.due_day > due_day), len(ordered))
    made = hc.ctx.lc.add_phase(Phase(onboarding_id=hc.ob.id, key=key, title=title, due_day=due_day, position=at))
    if made is None:
        raise BoardError(409, "That milestone exists already.")
    ordered.insert(at, made)
    _renumber(hc, ordered)
    _audit(hc, user, "board.milestone.added", f"{hc.ob.id}:{key}", {"title": title, "due_day": due_day})
    _tell(hc, hc.ob.new_hire_id, user, f"{user.name} added the milestone Day {due_day} · {title} to your board.")
    return view(hc, True)


def edit_phase(hc: HireCtx, user: User, key: str, title: str | None, due_day: int | None) -> dict:
    ps = phases(hc)
    p = _phase(ps, key)
    changes = {}
    if title is not None and title != p.title:
        changes["title"] = title
    if due_day is not None and due_day != p.due_day:
        _check_day(ps, due_day, key)
        changes["due_day"] = due_day
    if changes:
        hc.ctx.lc.update_phase(hc.ob.id, key, **changes)
        _audit(hc, user, "board.milestone.changed", f"{hc.ob.id}:{key}", {"from": {k: getattr(p, k) for k in changes}, "to": changes})
        if "due_day" in changes:
            _tell(hc, hc.ob.new_hire_id, user, f"{user.name} moved the milestone {changes.get('title', p.title)} to day {due_day}.")
    return view(hc, True)


def remove_phase(hc: HireCtx, user: User, key: str, move_to: str | None) -> dict:
    """Its tasks move to `move_to` (by default the next milestone by day, or the one before it when it is the last), each
    move recorded like any other."""
    ps = phases(hc)
    p = _phase(ps, key)
    if len(ps) == 1:
        raise BoardError(409, "A board keeps at least one milestone.", "last_one")
    ordered = _by_day(ps)
    i = ordered.index(p)
    to = _phase(ps, move_to) if move_to else (ordered[i + 1] if i + 1 < len(ordered) else ordered[i - 1])
    if to.key == p.key:
        raise BoardError(422, "Move its tasks to another milestone")
    tasks = _on_board(hc)
    placed = _placed(hc, ps, tasks)
    leaving = [t for t in tasks if placed[t.id][0].key == p.key]
    lc, now = hc.ctx.lc, hc.ctx.clock.now()
    lc.delete_phase(hc.ob.id, key, to.key)
    rest = [q for q in ps if q.key != key]
    for t in leaving:
        due = _fit_due(hc, rest, next(q for q in rest if q.key == to.key), t.due_date)
        if due != t.due_date:
            hc.ctx.store.update_task(t.id, due_date=due)
        lc.add_task_move(TaskMove(onboarding_id=hc.ob.id, task_id=t.id, from_phase=p.key, to_phase=to.key, from_title=p.title,
                                  to_title=to.title, from_due=t.due_date if due != t.due_date else None,
                                  to_due=due if due != t.due_date else None, actor_id=user.id, at=now))
    lc.save_placements(hc.ob.id, [TaskPlacement(task_id=t.id, onboarding_id=hc.ob.id, phase_key=to.key) for t in leaving])
    _renumber(hc, [q for q in sorted(ps, key=lambda q: q.position) if q.key != key])
    _audit(hc, user, "board.milestone.removed", f"{hc.ob.id}:{key}", {"title": p.title, "tasks_moved": len(leaving), "to": to.key})
    if leaving:
        _tell(hc, hc.ob.new_hire_id, user, f"{user.name} removed the milestone {p.title}; its tasks moved to {to.title}.")
    hc.forget("tasks")
    return view(hc, True)


def reorder_phases(hc: HireCtx, user: User, keys: list[str]) -> dict:
    """The named milestones first, in that order; the others after them in the order they had."""
    ps = sorted(phases(hc), key=lambda p: p.position)
    known = {p.key for p in ps}
    if len(set(keys)) != len(keys) or not set(keys) <= known:
        raise BoardError(422, "Name each milestone once, from this board")
    ordered = [next(p for p in ps if p.key == k) for k in keys] + [p for p in ps if p.key not in keys]
    _renumber(hc, ordered)
    _audit(hc, user, "board.milestones.reordered", hc.ob.id, {"order": [p.key for p in ordered]})
    return view(hc, True)
