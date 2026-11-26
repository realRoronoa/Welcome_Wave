"""Demo-only stand-ins for the outside services production signs in and reviews through, and the demo clock. Registered
only when MODE=demo.

- Sign-in needs Identity Platform: here every member of the demo organisation signs in with their email and one shared
  password (settings.demo_password, shown on the demo sign-in page). POST /demo/login answers with the member, and the web
  app sends their id as X-Demo-User from then on; signing out forgets it.
- Joining from an invitation needs a Firebase sign-in: here the sign-in page lists everyone invited and not yet joined.
  Accepting (POST /demo/invitations/accept) joins them, as their first sign-in would in production, and the page then signs
  them in with their email and the demo password, which keeps working for them like for any other member. Until they
  accept, their email does not sign in: like every unknown email it gets the same 401.
- A pull request needs the GitHub App: here a new hire opens one for their code task, and the same webhook path GitHub
  would drive (pull_request opened / synchronize) matches it to the task and runs the Review agent on the demo diff.
"""
from __future__ import annotations

import hashlib
import hmac
import threading
import time
from collections import deque
from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.api.auth import container, refuse_deactivated, require
from app.ports import User
from app.seed import ORG

router = APIRouter(prefix="/demo")

# Not a pseudo-person anyone signs in as: it only asks the store for every project of the demo organisation.
_ORG_READER = User(id="demo:org", org_id=ORG, email="", name="", role="admin")


def _org_projects(c):
    """Every project of the demo organisation (the demo has one organisation and no tenancy to cross)."""
    return c.store.projects_for(_ORG_READER)


@router.get("/people")
def people(c=Depends(container)):
    """Who can sign in to the demo: every member of the organisation's projects, then everyone invited and not yet joined.
    No sign-in needed: the demo sign-in page lists them."""
    members, invited = {}, {}
    for p in _org_projects(c):
        for m in c.store.members(p.id):
            if m.status == "active":  # a deactivated member cannot sign in
                members.setdefault(m.id, {"id": m.id, "name": m.name, "email": m.email, "role": m.role})
        for i in c.store.invitations(p.id):
            invited.setdefault(i["email"], {"invitation_id": i["id"], "project_id": p.id, "email": i["email"], "name": i.get("name"),
                                            "role": i["role"]})
    return {"members": sorted(members.values(), key=lambda m: m["name"]), "invited": list(invited.values())}


class _Attempts:
    """Sign-in attempts per key in a sliding window, in this process only (the demo runs one). Keys are an email and a
    client address; idle keys are dropped once there are many, so made-up emails cannot grow it without bound."""

    def __init__(self, limit: int, window_s: float):
        self.limit, self.window_s = limit, window_s
        self.hits: dict[str, deque] = {}
        self.lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        with self.lock:
            if len(self.hits) > 10_000:
                self.hits = {k: q for k, q in self.hits.items() if q and now - q[-1] <= self.window_s}
            q = self.hits.setdefault(key, deque())
            while q and now - q[0] > self.window_s:
                q.popleft()
            if len(q) >= self.limit:
                return False
            q.append(now)
            return True


LOGIN_ATTEMPTS_PER_MINUTE = 10
_login_attempts = _Attempts(LOGIN_ATTEMPTS_PER_MINUTE, 60.0)
BAD_LOGIN = "That email and password do not match a demo account."


class LoginIn(BaseModel):
    email: str = Field(max_length=320)
    password: str = Field(max_length=256)


@router.post("/login")
def login(body: LoginIn, request: Request, c=Depends(container)):
    """Sign in to the demo as a member of its organisation: their email (any case) and the demo password. A wrong password
    and an email that is not a member get the same 401, so the answer never tells whether an email exists. Every attempt
    counts towards a small limit per email and client (429 beyond it)."""
    email = body.email.strip().lower()
    client = request.client.host if request.client else "unknown"
    if not _login_attempts.allow(f"{email}|{client}"):
        raise HTTPException(429, "Too many sign-in attempts. Wait a minute and try again.", headers={"Retry-After": "60"})
    password_ok = hmac.compare_digest(body.password.encode(), c.settings.demo_password.encode())
    user = c.store.user_by_email(ORG, email) if email else None  # looked up either way: both failures take the same path
    if not password_ok or not user:
        raise HTTPException(401, BAD_LOGIN)
    refuse_deactivated(user)  # as every signed-in call would
    c.store.audit(user.org_id, user.id, "demo.signed_in", user.id)
    return {"user": user}


class AcceptIn(BaseModel):
    project_id: str
    invitation_id: str


@router.post("/invitations/accept")
def accept(body: AcceptIn, c=Depends(container)):
    """Join as the invitee, as their first sign-in would in production. Returns the new member; their email and the demo
    password sign them in from then on. No sign-in needed: the invitee is not a member yet."""
    try:
        project = c.store.get_project(body.project_id)
    except Exception:  # unknown id
        project = None
    if not project or project.org_id != ORG:
        raise HTTPException(404, "Project not found")
    inv = next((i for i in c.store.invitations(body.project_id) if i["id"] == body.invitation_id), None)
    if not inv:
        raise HTTPException(404, "That invitation has been accepted or revoked")
    joined = c.store.accept_invitations(f"demo:{inv['id']}", inv["email"], inv.get("name") or "", ORG)
    if not joined:
        raise HTTPException(409, "That invitation can no longer be accepted")
    c.store.audit(ORG, joined.id, "invitation.accepted", inv["id"], {"demo": True})
    return joined


class PullRequestIn(BaseModel):
    task_id: str
    merge: bool = False  # merge the task's open pull request (GitHub's pull_request closed + merged)


@router.post("/pull-request")
def pull_request(body: PullRequestIn, user: User = Depends(require("new_hire")), c=Depends(container)):
    """Open a pull request for one of my code tasks (or push a new commit to the one already open), through the GitHub
    webhook handler. Returns the task as it is afterwards and the review the Review agent wrote."""
    from app.agents.supervisor import OPEN_FOR_PR
    from app.api.webhooks import _github_event

    t = c.store.get_task(body.task_id)
    ob = c.store.get_onboarding(t.onboarding_id) if t else None
    if not t or not ob or ob.new_hire_id != user.id:
        raise HTTPException(404, "Task not found")
    if t.kind != "code" or t.status not in OPEN_FOR_PR:
        raise HTTPException(409, "Only an open code task takes a pull request")
    if body.merge and not t.pr_number:
        raise HTTPException(409, "Open the pull request first")
    repo = next(iter(c.store.repos_for(ob.project_id)), None)
    if not repo:
        raise HTTPException(409, "This project has no repository")
    number = t.pr_number or 100 + int(hashlib.sha1(t.id.encode()).hexdigest(), 16) % 900
    sha = hashlib.sha1(f"{t.id}:{time.time_ns()}".encode()).hexdigest()
    payload = {
        "action": "closed" if body.merge else "synchronize" if t.pr_number else "opened",
        "repository": {"full_name": repo.full_name, "default_branch": repo.default_branch},
        "pull_request": {
            "number": number, "html_url": f"https://github.com/{repo.full_name}/pull/{number}",
            "head": {"sha": sha, "ref": f"task-{t.id.replace('-', '')[:8]}"},
            "body": f"Closes #{t.github_issue_number}" if t.github_issue_number else "",
            "user": {"login": user.github_login or ""}, "updated_at": datetime.now(timezone.utc).isoformat(), "merged": body.merge,
        },
    }
    out = _github_event(c, "pull_request", payload)
    after = c.store.get_task(t.id)
    return {"events": out.get("events", 0), "task": after, "review_id": c.store.latest_reviews([t.id]).get(t.id)}


class AdvanceIn(BaseModel):
    days: int = Field(ge=1, le=90)


@router.post("/advance")
def advance(body: AdvanceIn, user: User = Depends(require("manager")), c=Depends(container)):
    """Move the demo organisation's clock forward (app/lifecycle/clock.py), so its programs, check-ins, blockers and reports
    move through days 0-90 without waiting. For each day the clock crosses, the seeded people do what they plausibly would
    (app/lifecycle/simulation.py: check-ins on time, tasks done, a first change merged, completion when every criterion is
    met), then the daily tick (app/agents/tick.py) runs as the real loop would have: the Coach's blocker rules and nudges, the weekly summaries, the Planner's completion drafts. Each
    day's tick runs once (its dedupe key), so moving back and forth never repeats one. Then every active program is brought
    up to date at the new date."""
    from datetime import timedelta

    from app.agents import tick
    from app.lifecycle import flags, hires, simulation
    from app.lifecycle.clock import advance as move
    from app.lifecycle.context import Ctx

    if not flags.is_on("demo_clock"):
        raise HTTPException(404, "This feature (demo_clock) is turned off on this server.")
    start = tick.today(c, user.org_id)
    clock = move(c, user.org_id, body.days)
    for n in range(1, (clock.today() - start).days + 1):  # each day crossed: what the seeded people do, then that day's tick
        simulation.day(c, user.org_id, start + timedelta(days=n))
        tick.run(c, user.org_id, day=start + timedelta(days=n))
    ctx = Ctx(c, clock, user)
    team = hires.of_projects(ctx, [p.id for p in _org_projects(c)])
    for hc in team:
        hires.refresh(hc)
    c.store.audit(user.org_id, user.id, "demo.clock_advanced", user.org_id, {"days": body.days, "offset": clock.offset_days})
    return {"offset_days": clock.offset_days, "today": clock.today(), "programs": [{"onboarding_id": hc.ob.id, "day": hc.day} for hc in team]}


@router.post("/reset")
def reset(request: Request, user: User = Depends(require("manager")), c=Depends(container)):
    """The demo organisation as it was seeded, at day 0: the in-memory data is built again (every change since is gone)."""
    from app.api import audit
    from app.container import build_container

    fresh = build_container(c.settings, live=getattr(request.app.state, "live", None))
    audit.on_start(fresh)
    request.app.state.container = fresh
    fresh.store.audit(ORG, user.id, "demo.reset", ORG, {})
    return {"offset_days": 0, "today": date.today()}
