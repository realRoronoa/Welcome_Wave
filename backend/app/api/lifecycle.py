"""The onboarding lifecycle API (docs/PLAN.md §4; docs/specs/). Thin: validation, permissions, then app/lifecycle.

Who may call what is checked here, on the server, for every route (docs/PERMISSIONS.md is the matrix, and
tests/test_permissions_matrix.py holds every route to it). For a hire's data the caller must be related to that
onboarding: the hire, their mentor, or a manager of the project (admins included). Anyone else gets 404, as if it did not
exist; a related person whose role may not call the route gets 403. Executives read reports only (and app/api/auth.py
refuses them every write)."""
from __future__ import annotations

import json
from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, Response
from pydantic import AfterValidator, BaseModel, EmailStr, Field, StringConstraints

from app.api.auth import _set_tenant, check_project, container, current_user, require
from app.api.outbox import publish
from app.api.routes import chat_allowed, guide_slot
from app.lifecycle import blockers as B
from app.lifecycle import checkins as CI
from app.lifecycle import (completion, enrolment, feedback, first_task, flags, handover, hires, home, orgdata, preboarding, presubmit,
                           progress, reporting, visibility)
from app.lifecycle import templates as T
from app.lifecycle.context import Ctx, HireCtx
from app.agents.core.schemas import Chunk, ProposedTask
from app.ports import AccessItem, ExpertPreferences, ProgramTemplate, User, VerifiedAnswer
from app.ratelimit import request_address
from app.services import mailing

router = APIRouter()
HIRE, MENTOR, MANAGER = "hire", "mentor", "manager"
DEMO_ORG = "org-demo"  # app/seed.py ORG: the one organisation of demo mode
PERSON_ROLES = frozenset({"expert", "manager", "admin"})


def feature(name: str):
    """A route of a feature that FEATURES can turn off (app/lifecycle/flags.py): 404 while it is off."""
    def check():
        if not flags.is_on(name):
            raise HTTPException(404, f"This feature ({name}) is turned off on this server.")
    return Depends(check)


def _ctx(c, user: User) -> Ctx:
    return Ctx.of(c, user)


def _agent(c, event: dict, unavailable: str) -> dict:
    """Run a synchronous event through the Supervisor (its chat graph: never paused, never checkpointed) and return the
    agent's result. A failed run was recorded and escalated: 503."""
    out = c.handle(event)
    result = out.get("result") or {}
    if result.get("escalated") or out.get("error"):
        raise HTTPException(503, unavailable)
    return result



def _hire(c, user: User, onboarding_id: str, *allowed: str) -> HireCtx:
    """The onboarding, if the caller is related to it in one of the `allowed` ways."""
    hc = hires.load(_ctx(c, user), onboarding_id)
    if not hc or c.store.org_of_project(hc.ob.project_id) != user.org_id:
        raise HTTPException(404, "Onboarding not found")
    member = c.store.is_member(hc.ob.project_id, user.id)
    relations = set()
    if hc.ob.new_hire_id == user.id and member:
        relations.add(HIRE)
    if hc.program.mentor_id == user.id and member:
        relations.add(MENTOR)
    if user.role in {"manager", "admin"} and (user.role == "admin" or member) and hc.ob.new_hire_id != user.id:
        relations.add(MANAGER)  # never of their own onboarding: a hire who is a manager by role is still the hire here
    if not relations:
        raise HTTPException(404, "Onboarding not found")
    if not relations & set(allowed):
        raise HTTPException(403, f"Only the {' or '.join(allowed)} may do this")
    return hc


def _audit(c, user: User, action: str, target: str, detail: dict | None = None) -> None:
    c.store.audit(user.org_id, user.id, action, target, detail or {})


def _member(c, user: User, project_id: str, user_id: str | None, roles=PERSON_ROLES) -> User | None:
    if not user_id:
        return None
    u = c.store.get_user(user_id)
    if not u or u.org_id != user.org_id or u.role not in roles or not c.store.is_member(project_id, u.id):
        raise HTTPException(422, "That person is not a member of this project who can take this role")
    return u


# ---------------------------------------------------------------- configuration and homes
@router.get("/lifecycle/config")
def lifecycle_config(c=Depends(container)):
    """What the web app needs to know about the lifecycle before sign-in: which features are on, the floors, and the
    lifecycle's today (in demo mode, the demo organisation's clock: offset_days ahead of the real date)."""
    from app.lifecycle.clock import Clock

    offset = c.lifecycle.org_settings(DEMO_ORG).demo_day_offset if c.settings.is_demo and c.lifecycle is not None else 0
    return {"today": Clock(offset).today(), "offset_days": offset, "features": flags.enabled(), "survey_min_group": CI.SURVEY_MIN_GROUP, "report_min_median": reporting.MIN_MEDIAN,
            "report_min_rate": reporting.MIN_RATE, "checkin_days": list(T.CHECKIN_DAYS), "preboarding_link_days": preboarding.LINK_DAYS,
            "tracks": list(T.TRACKS), "levels": list(T.LEVELS)}


@router.get("/home")
def role_home(user: User = Depends(current_user), c=Depends(container)):
    return home.home(_ctx(c, user))


class SetupIn(BaseModel):
    step: str | None = Field(None, pattern=r"^[a-z_]{1,40}$")
    action: Literal["complete", "skip", "reopen"] | None = None
    minutes_per_interruption: int | None = Field(None, ge=1, le=240)


@router.get("/setup")
def get_setup(user: User = Depends(require("admin")), c=Depends(container)):
    ctx = _ctx(c, user)
    return {"steps": home.setup_steps(ctx), "minutes_per_interruption": c.lifecycle.org_settings(user.org_id).minutes_per_interruption}


@router.patch("/setup")
def patch_setup(body: SetupIn, user: User = Depends(require("admin")), c=Depends(container)):
    settings = c.lifecycle.org_settings(user.org_id)
    fields: dict = {}
    if body.step:
        if body.step not in {k for k, _, _ in home.SETUP} or not body.action:
            raise HTTPException(422, "Give a known step and an action")
        if body.step in home.CHECKED_ONLY and body.action == "complete":
            raise HTTPException(409, detail={"code": "checked_from_data", "message": "This step is done once the agreement is accepted: "
                                             "Settings, Organisation, Legal."})
        skipped, completed = set(settings.setup.get("skipped", [])) - {body.step}, set(settings.setup.get("completed", [])) - {body.step}
        if body.action == "skip":
            skipped.add(body.step)
        elif body.action == "complete":
            completed.add(body.step)
        fields["setup"] = {"skipped": sorted(skipped), "completed": sorted(completed)}
    if body.minutes_per_interruption is not None:
        fields["minutes_per_interruption"] = body.minutes_per_interruption
    if fields:
        c.lifecycle.update_org_settings(user.org_id, **fields)
        _audit(c, user, "setup.updated", user.org_id, body.model_dump(exclude_none=True))
    return get_setup(user, c)


# ---------------------------------------------------------------- teams and templates
class TeamIn(BaseModel):
    project_id: str
    name: str = Field(min_length=1, max_length=120)


class TeamPatch(BaseModel):
    name: str = Field(min_length=1, max_length=120)


@router.get("/teams")
def list_teams(project_id: str, user: User = Depends(require("manager")), c=Depends(container)):
    check_project(c, user, project_id)
    return c.lifecycle.teams(user.org_id, project_id)


@router.post("/teams", status_code=201)
def create_team(body: TeamIn, user: User = Depends(require("manager")), c=Depends(container)):
    check_project(c, user, body.project_id)
    t = c.lifecycle.create_team(user.org_id, body.project_id, body.name.strip())
    _audit(c, user, "team.created", t.id, {"name": t.name})
    return t


@router.patch("/teams/{team_id}")
def update_team(team_id: str, body: TeamPatch, user: User = Depends(require("manager")), c=Depends(container)):
    t = c.lifecycle.get_team(team_id)
    if not t or t.org_id != user.org_id:
        raise HTTPException(404, "Team not found")
    check_project(c, user, t.project_id)
    t = c.lifecycle.update_team(team_id, name=body.name.strip())
    _audit(c, user, "team.renamed", team_id, {"name": t.name})
    return t


class MilestoneSpec(BaseModel):
    key: str = Field(pattern=r"^[a-z0-9_]{1,40}$")
    title: str = Field(min_length=1, max_length=200)
    due_day: int = Field(ge=0, le=365)


class AckSpec(BaseModel):
    doc_key: str = Field(pattern=r"^[a-z0-9_]{1,40}$")
    title: str = Field(min_length=1, max_length=200)
    summary: str = Field("", max_length=1000)


class AccessSpec(BaseModel):
    system: str = Field(min_length=1, max_length=120)
    owner: Literal["manager", "mentor", "admin"] = "manager"


class PlanItemSpec(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str = Field("", max_length=2000)
    kind: Literal["learn", "code", "meet"] = "learn"
    due_day: int = Field(7, ge=1, le=365)
    acceptance_criteria: list[str] = Field(default_factory=list, max_length=10)


class PhaseSpec(BaseModel):
    """A board milestone of a template (app/lifecycle/board.py): exact days of the template's length."""
    key: str = Field(pattern=r"^[a-z0-9_]{1,40}$")
    title: str = Field(min_length=1, max_length=120)
    due_day: int = Field(ge=1, le=730)


def _distinct_phases(v: list[PhaseSpec]) -> list[PhaseSpec]:
    if len({p.key for p in v}) != len(v) or len({p.due_day for p in v}) != len(v):
        raise ValueError("each board milestone needs its own key and its own day")
    return v


Phases = Annotated[list[PhaseSpec], AfterValidator(_distinct_phases)]


class TemplateIn(BaseModel):
    key: str = Field(pattern=r"^[a-z0-9-]{2,60}$")
    title: str = Field(min_length=1, max_length=120)
    role_track: str = Field(min_length=1, max_length=40)
    level: str = Field(min_length=1, max_length=40)
    duration_days: int = Field(90, ge=14, le=365)
    milestones: list[MilestoneSpec] = Field(default_factory=list, max_length=40)
    acknowledgements: list[AckSpec] = Field(default_factory=list, max_length=20)
    access_items: list[AccessSpec] = Field(default_factory=list, max_length=30)
    plan_items: list[PlanItemSpec] = Field(default_factory=list, max_length=30)
    phases: Phases = Field(default_factory=list, max_length=T.MAX_PHASES)  # empty: the default board milestones


class TemplatePatch(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=120)
    duration_days: int | None = Field(None, ge=14, le=365)
    milestones: list[MilestoneSpec] | None = Field(None, max_length=40)
    acknowledgements: list[AckSpec] | None = Field(None, max_length=20)
    access_items: list[AccessSpec] | None = Field(None, max_length=30)
    plan_items: list[PlanItemSpec] | None = Field(None, max_length=30)
    phases: Phases | None = Field(None, max_length=T.MAX_PHASES)


@router.get("/programs/templates")
def list_templates(include_inactive: bool = False, user: User = Depends(require("manager")), c=Depends(container)):
    return T.list_for(c.lifecycle, user.org_id, include_inactive)


@router.post("/programs/templates", status_code=201)
def create_template(body: TemplateIn, user: User = Depends(require("admin")), c=Depends(container)):
    if any(t.key == body.key for t in T.list_for(c.lifecycle, user.org_id, include_inactive=True)):
        raise HTTPException(409, "A template with this key exists; change it to make a new version")
    t = c.lifecycle.create_template(ProgramTemplate(id="", org_id=user.org_id, **body.model_dump()))
    _audit(c, user, "template.created", t.id, {"key": t.key})
    return t


@router.patch("/programs/templates/{template_id}")
def new_template_version(template_id: str, body: TemplatePatch, user: User = Depends(require("admin")), c=Depends(container)):
    old = c.lifecycle.get_template(template_id)
    if not old or old.org_id != user.org_id:
        raise HTTPException(404, "Template not found")
    t = c.lifecycle.new_template_version(template_id, **body.model_dump(exclude_none=True))
    if not t:
        raise HTTPException(409, "Only the active version can be changed")
    _audit(c, user, "template.versioned", t.id, {"key": t.key, "version": t.version})
    return t


# ---------------------------------------------------------------- enrolment and the program
class EnrolIn(BaseModel):
    project_id: str
    email: EmailStr
    name: str = Field(min_length=1, max_length=120)
    github_login: str | None = Field(None, max_length=39, pattern=r"^[A-Za-z0-9-]*$")
    start_date: date
    template_id: str
    team_id: str | None = None
    mentor_id: str | None = None
    focus_area: str | None = Field(None, max_length=200)
    role_title: str | None = Field(None, max_length=120)


@router.post("/onboardings/enrol", status_code=201)
def enrol(body: EnrolIn, user: User = Depends(require("manager")), c=Depends(container)):
    """A draft program and ONE drafted 30/60/90 plan, which waits in Approvals; approving it creates the milestones, the access
    requests and (for someone not in the organisation yet) the invitation, whose link the decision returns. No email is sent."""
    check_project(c, user, body.project_id)
    template = c.lifecycle.get_template(body.template_id)
    if not template or template.org_id != user.org_id or not template.active:
        raise HTTPException(422, "Choose an active program template")
    if body.team_id:
        team = c.lifecycle.get_team(body.team_id)
        if not team or team.project_id != body.project_id:
            raise HTTPException(422, "That team is not in this project")
    mentor = _member(c, user, body.project_id, body.mentor_id)
    if mentor and mentor.email.lower() == str(body.email).lower():
        raise HTTPException(422, "A hire cannot be their own mentor")
    try:
        out = enrolment.enrol(_ctx(c, user), body.project_id, str(body.email), body.name.strip(), body.github_login or None,
                              body.start_date,
                              template, body.team_id, body.mentor_id, (body.focus_area or "").strip().strip("/") or None, body.role_title)
    except enrolment.PlanInProgress as e:
        raise HTTPException(409, detail={"code": "plan_in_progress", "message": str(e)}) from None
    except enrolment.EnrolConflict as e:
        raise HTTPException(409, f"{e} already has an active onboarding in this project") from None
    ob = out["onboarding"]
    event = out.pop("plan_event")
    publish(c, "reviews", event)  # the Planner drafts the 30/60/90 plan; approving it creates the invitation
    _audit(c, user, "onboarding.enrolled", ob.id, {"template": template.key, "version": template.version,
                                                   "invite_on_approval": event["enrolment"]["invite"],
                                                   **({"adopted": True} if out.get("adopted") else {})})
    hc = hires.load(_ctx(c, user), ob.id)
    return {**out, "plan": visibility.FIELDS["plan"].build(hc)}


@router.get("/onboardings/{onboarding_id}/program")
def program(onboarding_id: str, user: User = Depends(current_user), c=Depends(container)):
    hc = hires.refresh(_hire(c, user, onboarding_id, HIRE, MENTOR, MANAGER))
    return visibility.view(hc, visibility.PROGRAM)


class MilestonePatch(BaseModel):
    status: Literal["pending", "done", "skipped"]
    note: str = Field("", max_length=1000)


@router.patch("/onboardings/{onboarding_id}/milestones/{key}")
def update_milestone(onboarding_id: str, key: str, body: MilestonePatch, user: User = Depends(current_user), c=Depends(container)):
    hc = _hire(c, user, onboarding_id, MANAGER)
    if not any(m.key == key for m in hc.milestones):
        raise HTTPException(404, "Milestone not found")
    m = c.lifecycle.update_milestone(onboarding_id, key, status=body.status, done_at=hc.ctx.clock.now() if body.status == "done" else None,
                                     evidence={"by": user.id, "note": body.note} if body.status != "pending" else {})
    _audit(c, user, "milestone.updated", f"{onboarding_id}:{key}", {"status": body.status})
    c.store.notify(hc.ob.new_hire_id, "milestone", f"Milestone '{m.title}' marked {body.status}.",
                   f"/program/{onboarding_id}?project={hc.ob.project_id}")
    return m


# ---------------------------------------------------------------- access
class AccessIn(BaseModel):
    system: str = Field(min_length=1, max_length=120)
    owner_id: str | None = None


class AccessPatch(BaseModel):
    status: Literal["requested", "granted", "not_needed"]


def _access_ctx(c, user: User, onboarding_id: str) -> HireCtx:
    """Managers, the hire, the mentor, and anyone who owns one of its access items."""
    try:
        return _hire(c, user, onboarding_id, HIRE, MENTOR, MANAGER)
    except HTTPException:
        hc = hires.load(_ctx(c, user), onboarding_id)
        if hc and c.store.org_of_project(hc.ob.project_id) == user.org_id and any(a.owner_id == user.id for a in hc.access):
            return hc
        raise


@router.get("/onboardings/{onboarding_id}/access")
def list_access(onboarding_id: str, user: User = Depends(current_user), c=Depends(container)):
    return visibility.view(_access_ctx(c, user, onboarding_id), ["access"])


@router.post("/onboardings/{onboarding_id}/access", status_code=201)
def add_access(onboarding_id: str, body: AccessIn, user: User = Depends(current_user), c=Depends(container)):
    hc = _hire(c, user, onboarding_id, MANAGER)
    owner = _member(c, user, hc.ob.project_id, body.owner_id) or user
    item = c.lifecycle.add_access_item(AccessItem(id="", onboarding_id=onboarding_id, system=body.system.strip(), owner_id=owner.id,
                                                  requested_at=hc.ctx.clock.now()))
    if owner.id != user.id:
        c.store.notify(owner.id, "access_request", f"Grant {item.system} to {(hc.hire.name if hc.hire else 'a new hire')}.",
                       f"/program/{onboarding_id}?tab=access&project={hc.ob.project_id}")
    _audit(c, user, "access.requested", item.id, {"system": item.system, "owner_id": owner.id})
    return item


@router.patch("/onboardings/{onboarding_id}/access/{item_id}")
def update_access(onboarding_id: str, item_id: str, body: AccessPatch, user: User = Depends(current_user), c=Depends(container)):
    hc = _access_ctx(c, user, onboarding_id)
    item = c.lifecycle.get_access_item(item_id)
    if not item or item.onboarding_id != onboarding_id:
        raise HTTPException(404, "Access item not found")
    is_manager = user.role == "admin" or (user.role == "manager" and c.store.is_member(hc.ob.project_id, user.id))
    if item.owner_id != user.id and not is_manager:
        raise HTTPException(403, "Only the item's owner or a manager may change it")
    now = hc.ctx.clock.now()
    item = c.lifecycle.update_access_item(item_id, status=body.status, granted_at=now if body.status == "granted" else None)
    if all(a.status != "requested" for a in c.lifecycle.access_items(onboarding_id)) and not hc.program.access_granted_at:
        c.lifecycle.update_program(onboarding_id, access_granted_at=now)
    _audit(c, user, "access.updated", item_id, {"status": body.status})
    return item


# ---------------------------------------------------------------- pre-boarding (no sign-in: the link is the permission)
class PreboardingLinkIn(BaseModel):
    email: bool = False  # also email the link to the hire (only when SMTP is set up and PUBLIC_URL is, so the link is a whole address)


@router.post("/onboardings/{onboarding_id}/preboarding-link", status_code=201, dependencies=[feature("preboarding")])
def preboarding_link(onboarding_id: str, background: BackgroundTasks, body: PreboardingLinkIn | None = None,
                     user: User = Depends(current_user), c=Depends(container)):
    """A new link replaces the previous one. The token is in this response only: it is stored hashed. With `email: true` and SMTP
    set up (GET /api/config `email`), the link is also emailed to the hire, after this response is sent and from memory: the
    token is never written to the outbox, and a mail failure never fails this request. `emailed` says it was handed on."""
    hc = _hire(c, user, onboarding_id, MANAGER)
    token, expires = preboarding.issue(hc)
    base = c.settings.public_url.rstrip("/") if c.settings.public_url else ""
    url = f"{base}/welcome/{token}"
    emailed = bool(body and body.email and base and hc.hire and hc.hire.email and mailing.enabled(c.deps))
    if emailed:
        background.add_task(mailing.send_preboarding, c.deps, c.settings, hc.hire.email, hc.hire.name, c.store.get_project(hc.ob.project_id).name,
                            url, expires)
    _audit(c, user, "preboarding.link_created", onboarding_id, {"expires_at": expires.isoformat(), "emailed": emailed})
    return {"token": token, "url": url, "expires_at": expires, "emailed": emailed}


def _portal(c, token: str) -> HireCtx:
    found = preboarding.resolve(c.lifecycle, token)
    if not found:
        raise HTTPException(404, "This link has expired or was replaced. Ask your manager for a new one.")
    onboarding_id, org_id = found
    _set_tenant(org_id)  # everything after the link's lookup runs under row-level security for its organisation
    hc = hires.load(Ctx.of(c, None, org_id), onboarding_id)
    if not hc or hc.ob.status != "active":
        raise HTTPException(404, "This link has expired or was replaced. Ask your manager for a new one.")
    return hc


@router.get("/preboarding/{token}", dependencies=[feature("preboarding")])
def preboarding_portal(token: str, c=Depends(container)):
    return preboarding.portal(_portal(c, token))


class AckIn(BaseModel):
    doc_key: str = Field(pattern=r"^[a-z0-9_]{1,40}$")


@router.post("/preboarding/{token}/acknowledge", dependencies=[feature("preboarding")])
def preboarding_acknowledge(token: str, body: AckIn, request: Request, c=Depends(container)):
    hc = _portal(c, token)
    ack = preboarding.acknowledge(hc, body.doc_key, token, request_address(request.scope))
    if not ack:
        raise HTTPException(404, "No such document to acknowledge")
    # the hire acted, through the link that is their proof: the entry names them, and how (no sign-in), never "System"
    c.store.audit(hc.program.org_id, hc.ob.new_hire_id, "preboarding.acknowledged", hc.ob.id, {"doc_key": body.doc_key, "via": "preboarding"})
    return {"doc_key": ack.doc_key, "title": ack.title, "acknowledged_at": ack.acknowledged_at}


@router.get("/preboarding/{token}/env-check.sh", dependencies=[feature("preboarding")])
def preboarding_script(token: str, request: Request, c=Depends(container)):
    hc = _portal(c, token)
    base = c.settings.public_url.rstrip("/") if c.settings.public_url else str(request.base_url).rstrip("/")
    return Response(preboarding.script(hc, f"{base}/api/preboarding/{token}/env-check"), media_type="text/x-shellscript",
                    headers={"Content-Disposition": 'attachment; filename="env-check.sh"'})


class EnvResult(BaseModel):
    name: str = Field(max_length=40)
    ok: bool
    detail: str = Field("", max_length=400)


class EnvIn(BaseModel):
    results: list[EnvResult] = Field(max_length=preboarding.MAX_RESULTS)


@router.post("/preboarding/{token}/env-check", dependencies=[feature("preboarding")])
def preboarding_env(token: str, body: EnvIn, c=Depends(container)):
    hc = _portal(c, token)
    check = preboarding.record(hc, [r.model_dump() for r in body.results])
    c.store.audit(hc.program.org_id, hc.ob.new_hire_id, "preboarding.env_checked", hc.ob.id, {"ok": check.ok, "via": "preboarding"})
    return check


# ---------------------------------------------------------------- the first task and the private pre-submit review
@router.get("/onboardings/{onboarding_id}/first-task/shortlist", dependencies=[feature("first_task")])
def first_task_shortlist(onboarding_id: str, run_id: str | None = Query(None, max_length=64), user: User = Depends(current_user),
                         c=Depends(container)):
    """The Scout's shortlist (first_task.requested): the same builder as the hire's visibility payload. The Scout thinks only when
    the candidates changed since its last picks (watch it on topic agent:<run_id>); its picks carry their run's shared steps."""
    hc = _hire(c, user, onboarding_id, MANAGER)
    result = _agent(c, {"type": "first_task.requested", "project_id": hc.ob.project_id, "onboarding_id": onboarding_id,
                        "user_id": user.id, **_watch(c, run_id)}, "The Scout is unavailable right now; try again in a moment.")
    return {"first_task_shortlist": result}


class FirstTaskIn(BaseModel):
    candidate_id: str = Field(pattern=r"^(issue:\d{1,9}|task:[\w-]{1,64})$")
    run_id: str | None = Field(None, max_length=64)  # as in RouteIn: the Scout's brief streams on topic agent:<run_id>


def _watch(c, run_id: str | None) -> dict:
    """{"run_id": ...} when the client named a run to watch (a fresh uuid: _run_id checks it), else nothing."""
    return {"run_id": _run_id(c, run_id)} if run_id else {}


@router.post("/onboardings/{onboarding_id}/first-task", dependencies=[feature("first_task")])
def select_first_task(onboarding_id: str, body: FirstTaskIn, user: User = Depends(current_user), c=Depends(container)):
    """Selection makes the brief: an issue becomes an approved code task for the hire; a proposed task stays in Approvals."""
    hc = _hire(c, user, onboarding_id, MANAGER)
    found = next((x for x in first_task.shortlist(hc, limit=None)["candidates"] if x["id"] == body.candidate_id), None)
    if not found:
        raise HTTPException(422, "Choose a candidate from the shortlist")
    brief = _agent(c, {"type": "first_task.requested", "project_id": hc.ob.project_id, "onboarding_id": onboarding_id, "user_id": user.id,
                       "candidate_id": body.candidate_id, **_watch(c, body.run_id)}, "The Scout is unavailable right now; try again in a moment.")
    if brief.get("error"):
        raise HTTPException(422, "Choose a candidate from the shortlist")
    steps = brief.pop("steps", None)  # the asker's own trace: returned, never stored with the program (brief.agent keeps the shared one)
    files = brief.get("files") or found["files"]  # the Scout's checked files (indexed, outside single-owner areas), else the rules'
    if found["kind"] == "issue":
        task = ProposedTask(title=f"First PR: {found['title']}", description=" ".join(found["why"]), kind="code", due_in_days=14,
                            related_paths=files, source="first_pr", issue_number=found["issue"],
                            acceptance_criteria=brief["acceptance_criteria"])
        [row] = c.store.create_tasks(onboarding_id, [task], max(hc.ob.start_date, hc.ctx.clock.today()))
        c.store.update_task(row.id, status="approved")
        brief["task_id"] = row.id
    else:
        brief["task_id"] = found["task_id"]
        task = c.store.get_task(found["task_id"])
        if task and not task.related_paths and files:  # so its merged change counts towards areas and completion
            c.store.update_task(task.id, related_paths=files)
        if found["status"] == "proposed":
            brief["manager_next"] = "Approve this task in Approvals; then the hire sees it."
    brief.update(selected_at=hc.ctx.clock.now().isoformat(), selected_by=user.id)
    c.lifecycle.update_program(onboarding_id, first_task=brief)
    c.store.notify(hc.ob.new_hire_id, "first_task", f"Your first task: {found['title']}",
                   f"/program/{onboarding_id}?tab=first-task&project={hc.ob.project_id}")
    _audit(c, user, "first_task.selected", onboarding_id, {"candidate": body.candidate_id})
    return {**brief, "steps": steps} if steps is not None else brief


class PresubmitIn(BaseModel):
    diff: str | None = Field(None, max_length=presubmit.MAX_DIFF)
    pr_number: int | None = Field(None, ge=1)
    run_id: str | None = Field(None, max_length=64)  # as in RouteIn: the private review streams to the hire alone


@router.post("/tasks/{task_id}/presubmit-review", dependencies=[feature("presubmit")])
def presubmit_review(task_id: str, body: PresubmitIn, user: User = Depends(current_user), c=Depends(container)):
    """The hire's own task only. Not stored, not audited, never shown to anyone else."""
    t = c.store.get_task(task_id)
    ob = c.store.get_onboarding(t.onboarding_id) if t else None
    if not t or not ob or ob.new_hire_id != user.id:
        raise HTTPException(404, "Task not found")
    if t.kind != "code":
        raise HTTPException(409, "Only a code task has a change to review")
    if not body.diff and not body.pr_number:
        raise HTTPException(422, "Paste your branch's diff (git diff main...HEAD) or give a draft pull request's number")
    if body.pr_number and not body.diff and body.pr_number != t.pr_number:
        raise HTTPException(422, "Give this task's own pull request number, or paste your branch's diff")
    # the Reviewer, privately: its run record says only that a private review ran
    return _agent(c, {"type": "presubmit.requested", "project_id": ob.project_id, "user_id": user.id, "task_id": t.id, "diff": body.diff,
                      "pr_number": body.pr_number, **_watch(c, body.run_id)}, "The private review could not finish; try again in a moment.")


# ---------------------------------------------------------------- tiered answer routing and verified answers
class RouteIn(BaseModel):
    project_id: str
    question: str = Field("", max_length=2000)
    ask_person: bool = False
    urgent: bool = False
    escalate_request_id: str | None = Field(None, max_length=64)
    message_id: str | None = Field(None, max_length=64)  # ask_person on a turn the Guide offered a person for: its draft goes along
    # The client's id for this run (a uuid it made): it subscribes to GET /api/live?topics=agent:<run_id> before asking, and
    # watches the steps arrive. Optional: without it the server makes one. Returned as `run_id`.
    run_id: str | None = Field(None, max_length=64)


def _run_id(c, run_id: str | None) -> str:
    """The run's id: the client's (a uuid, never one already used), else a new one."""
    import uuid

    if run_id is None:
        return str(uuid.uuid4())
    try:
        rid = str(uuid.UUID(run_id))
    except ValueError:
        raise HTTPException(422, "run_id must be a UUID") from None
    if c.store.get_run(rid):
        raise HTTPException(409, "That run_id is used already: make a new one.")
    return rid


@router.post("/ask/route", dependencies=[feature("routing")])
def ask_route(body: RouteIn, user: User = Depends(current_user), c=Depends(container)):
    """The Guide (app/agents/specialists/guide/agent.py): the response shape of docs/specs/answer-routing.md, plus `run_id` and `steps`, the
    trace (streamed live on topic agent:<run_id> to the asker)."""
    check_project(c, user, body.project_id)
    if not chat_allowed(c, user):
        raise HTTPException(429, "Too many questions in a minute; try again shortly.", headers={"Retry-After": "30"})
    event = {"type": "ask.route", "project_id": body.project_id, "user_id": user.id, "urgent": body.urgent, "ask_person": body.ask_person,
             "run_id": _run_id(c, body.run_id)}
    if body.escalate_request_id:
        req = c.store.get_expert_request(body.escalate_request_id)
        if not req or str(req.get("asked_by")) != user.id or str(req["project_id"]) != body.project_id:
            raise HTTPException(404, "Question not found")
        event["escalate_request_id"] = body.escalate_request_id
    else:
        if not body.question.strip():
            raise HTTPException(422, "Empty question")
        event["question"] = body.question.strip()
        if body.message_id and body.ask_person:
            event["message_id"] = body.message_id
    with guide_slot(c):
        out = _agent(c, event, "The Guide is unavailable right now; your manager has been notified.")  # the Guide
    _audit(c, user, "question.routed", out.get("message_id") or body.project_id, {"tier": out["tier"], "status": out["status"]})
    return out


class FreeIn(BaseModel):
    project_id: str
    question: str = Field(min_length=1, max_length=2000)
    run_id: str | None = Field(None, max_length=64)  # as in RouteIn


@router.post("/ask/free", dependencies=[feature("routing")])
def ask_free(body: FreeIn, user: User = Depends(current_user), c=Depends(container)):
    """A free-form request typed in Ask. The Supervisor decides who handles it (a model's decision with its own trace;
    app/agents/supervisor/orchestrator.py): {handled_by, why, result, run_id, steps}. The Guide's result is POST /ask/route's; for the
    Coach, the Planner and the Scout it is {proposal}: what that agent would propose (a person decides; nothing is done)."""
    check_project(c, user, body.project_id)
    if not body.question.strip():
        raise HTTPException(422, "Empty request")
    if not chat_allowed(c, user):
        raise HTTPException(429, "Too many questions in a minute; try again shortly.", headers={"Retry-After": "30"})
    event = {"type": "ask.free", "project_id": body.project_id, "user_id": user.id, "question": body.question.strip(),
             "urgent": False, "ask_person": False, "run_id": _run_id(c, body.run_id)}
    with guide_slot(c):
        out = c.handle(event)
    result, decision = out.get("result") or {}, out.get("decision") or {}
    if result.get("escalated") or out.get("error") or not decision:
        raise HTTPException(503, "The assistant is unavailable right now; your manager has been notified.")
    if decision["handled_by"] == "guide":
        _audit(c, user, "question.routed", result.get("message_id") or body.project_id, {"tier": result.get("tier"), "status": result.get("status")})
        return {"handled_by": "guide", "why": decision.get("why", ""), "result": result, "run_id": result.get("run_id"),
                "steps": result.get("steps", [])}
    return {"handled_by": decision["handled_by"], "why": decision.get("why", ""), "result": {"proposal": result.get("proposal", "")},
            "run_id": result.get("run_id"), "steps": result.get("steps", [])}


class VerifiedIn(BaseModel):
    project_id: str
    question: str = Field(min_length=3, max_length=2000)
    answer: str = Field(min_length=1, max_length=8000)
    citations: list[dict] = Field(default_factory=list, max_length=10)
    source_request_id: str | None = Field(None, max_length=64)


@router.post("/verified-answers", status_code=201)
def verify_answer(body: VerifiedIn, user: User = Depends(require("expert", "manager")), c=Depends(container)):
    """Stored, and indexed as a source, so later answers cite it and routing reuses it.

    One answer is one source. An expert's answer to a request is already indexed ('verified-<request id>'); saving it as a
    verified answer REPLACES that chunk (the same id, now carrying the verified answer's id) instead of adding 'va-<id>' next
    to it, so a question is never cited twice for one answer. Saving the same request's answer again returns the first."""
    check_project(c, user, body.project_id)
    saved = c.lifecycle.verified_answers(body.project_id)
    if body.source_request_id:
        req = c.store.get_expert_request(body.source_request_id)
        if not req or str(req["project_id"]) != body.project_id:
            raise HTTPException(422, "Unknown question")
        earlier = next((a for a in saved if a.source_request_id == body.source_request_id), None)
    else:  # the same question and answer saved again (a retry, a double click): the first one, not a second source
        earlier = next((a for a in saved if not a.source_request_id and _same_text(a.question, body.question)
                        and _same_text(a.answer, body.answer)), None)
    if earlier:  # indexed again: a retry is how an answer whose indexing failed gets into the index
        return {**earlier.model_dump(mode="json"), "indexed": _index_verified(c, earlier)}
    citations = [{k: str(v)[:300] for k, v in cit.items() if k in {"label", "path", "chunk_id", "url"}} for cit in body.citations]
    va = c.lifecycle.add_verified_answer(VerifiedAnswer(id="", project_id=body.project_id, question=body.question.strip(),
                                                        answer=body.answer.strip(),
                                                        citations=citations, verified_by=user.id, source_request_id=body.source_request_id,
                                                        created_at=Ctx.of(c, user).clock.now()))
    _audit(c, user, "answer.verified", va.id)  # stored and audited before indexing, which can fail without undoing either
    return {**va.model_dump(mode="json"), "indexed": _index_verified(c, va, user)}


def _same_text(a: str, b: str) -> bool:
    return " ".join(a.split()).casefold() == " ".join(b.split()).casefold()


def _index_verified(c, va: VerifiedAnswer, verifier: User | None = None) -> bool:
    """The verified answer as a source the Guide cites; False (logged) when indexing failed (routes.index_quietly)."""
    from app.api.routes import index_quietly

    who = verifier or c.store.get_user(va.verified_by)
    name = who.name if who else "an expert"
    chunk_id = f"verified-{va.source_request_id}" if va.source_request_id else f"va-{va.id}"
    ref = {"question": va.question, "expert_id": va.verified_by, "expert_name": name, "verified_answer_id": va.id}
    if va.source_request_id:
        ref["request_id"] = va.source_request_id
    return index_quietly(c, [Chunk(id=chunk_id, project_id=va.project_id, source_type="verified_answer", ref=ref,
                                   content=f"Q: {va.question}\nA (verified by {name}): {va.answer}")])


@router.get("/verified-answers")
def list_verified(project_id: str, user: User = Depends(require("expert", "manager")), c=Depends(container)):
    check_project(c, user, project_id)
    return c.lifecycle.verified_answers(project_id)


class PrefsIn(BaseModel):
    max_interruptions_per_day: int | None = Field(None, ge=0, le=50)
    office_hours: dict | None = None
    digest: bool | None = None


@router.get("/experts/preferences")
def get_preferences(user: User = Depends(require("expert", "manager")), c=Depends(container)):
    return c.lifecycle.expert_preferences(user.id)


@router.patch("/experts/preferences")
def set_preferences(body: PrefsIn, user: User = Depends(require("expert", "manager")), c=Depends(container)):
    hours = body.office_hours
    if hours is not None and (len(str(hours)) > 500 or not set(hours) <= {"days", "start", "end"}):
        raise HTTPException(422, "office_hours takes days, start and end")
    prefs = c.lifecycle.expert_preferences(user.id).model_copy(update=body.model_dump(exclude_none=True))
    out = c.lifecycle.save_expert_preferences(user.org_id, ExpertPreferences(**prefs.model_dump()))
    _audit(c, user, "expert.preferences", user.id, body.model_dump(exclude_none=True))
    return out


# ---------------------------------------------------------------- progress, blockers, summaries
@router.get("/onboardings/{onboarding_id}/areas", dependencies=[feature("areas")])
def areas(onboarding_id: str, user: User = Depends(current_user), c=Depends(container)):
    return visibility.view(_hire(c, user, onboarding_id, HIRE, MENTOR, MANAGER), ["areas"])


class WatchedIn(BaseModel):
    meeting_id: str = Field(max_length=64)
    at_s: float = Field(0, ge=0)


class AreaPatch(BaseModel):
    self_level: Literal["not_started", "exploring", "working", "contributed"] | None = None
    step: str | None = Field(None, min_length=1, max_length=200)
    watched: WatchedIn | None = None


@router.patch("/onboardings/{onboarding_id}/areas/{area:path}", dependencies=[feature("areas")])
def update_area(onboarding_id: str, area: str, body: AreaPatch, user: User = Depends(current_user), c=Depends(container)):
    """The hire's own record: a self-assessment, a reading-path step done, a KT clip watched."""
    hc = _hire(c, user, onboarding_id, HIRE)
    if area not in progress.project_areas(hc):
        raise HTTPException(404, "Area not found")
    if body.watched:
        m = c.store.get_meeting(body.watched.meeting_id)
        if not m or m.project_id != hc.ob.project_id:
            raise HTTPException(422, "Unknown KT call")
    progress.record(hc, area, body.self_level, body.step, body.watched.model_dump() if body.watched else None)
    _audit(c, user, "area.recorded", f"{onboarding_id}:{area}", body.model_dump(exclude_none=True, exclude={"step"}))
    return next(a for a in progress.areas(hc) if a["area"] == area)


@router.get("/onboardings/{onboarding_id}/blockers", dependencies=[feature("blockers")])
def list_blockers(onboarding_id: str, user: User = Depends(current_user), c=Depends(container)):
    hc = hires.refresh(_hire(c, user, onboarding_id, HIRE, MENTOR, MANAGER))
    return visibility.view(hc, ["blockers"])


class ResolveIn(BaseModel):
    note: str = Field("", max_length=1000)


@router.post("/blockers/{blocker_id}/resolve", dependencies=[feature("blockers")])
def resolve_blocker(blocker_id: str, body: ResolveIn, user: User = Depends(current_user), c=Depends(container)):
    alert = c.lifecycle.get_blocker(blocker_id)
    if not alert:
        raise HTTPException(404, "Blocker not found")
    hc = _hire(c, user, alert.onboarding_id, HIRE, MENTOR, MANAGER)
    if alert.resolved_at:
        raise HTTPException(409, "Already resolved")
    out = B.resolve(hc, alert, user.id)
    _audit(c, user, "blocker.resolved", blocker_id, {"rule": alert.rule, "note": body.note[:200]})
    return B.view(hc, [out])[0]


@router.get("/onboardings/{onboarding_id}/summaries", dependencies=[feature("summaries")])
def list_summaries(onboarding_id: str, user: User = Depends(current_user), c=Depends(container)):
    return visibility.view(_hire(c, user, onboarding_id, HIRE, MANAGER), ["summaries"])


@router.post("/onboardings/{onboarding_id}/summaries", status_code=201, dependencies=[feature("summaries")])
def write_summary(onboarding_id: str, run_id: str | None = Query(None, max_length=64), user: User = Depends(current_user),
                  c=Depends(container)):
    hc = _hire(c, user, onboarding_id, HIRE, MANAGER)
    s = _agent(c, {"type": "summary.requested", "project_id": hc.ob.project_id, "onboarding_id": onboarding_id, "user_id": user.id,
                   **_watch(c, run_id)},
               "The Coach is unavailable right now; try again in a moment.")  # the Coach node, through the Supervisor's graph
    _audit(c, user, "summary.written", onboarding_id, {"week_start": s["week_start"]})
    return s


# ---------------------------------------------------------------- documentation feedback
class FeedbackIn(BaseModel):
    project_id: str
    target_kind: Literal["doc", "code", "meeting", "answer"]
    target_ref: str = Field(min_length=1, max_length=500)
    kind: Literal["unclear", "outdated"]
    note: str = Field("", max_length=2000)


class FeedbackPatch(BaseModel):
    status: Literal["open", "resolved", "dismissed"]


@router.post("/feedback", status_code=201, dependencies=[feature("feedback")])
def submit_feedback(body: FeedbackIn, user: User = Depends(current_user), c=Depends(container)):
    check_project(c, user, body.project_id)
    try:
        out = feedback.submit(_ctx(c, user), body.project_id, body.target_kind, body.target_ref, body.kind, body.note.strip())
    except feedback.UnknownTarget:
        raise HTTPException(422, "Nothing in this project matches that target") from None
    _audit(c, user, "feedback.submitted", out["id"], {"target_kind": body.target_kind, "kind": body.kind})
    # the Keeper reads it (feedback.created): enough flags on one target and it drafts a fix, which waits in Approvals
    publish(c, "reviews", {"type": "feedback.created", "project_id": body.project_id, "feedback_id": out["id"],
                           "dedupe": f"feedback:{out['id']}"})
    return out


@router.get("/feedback", dependencies=[feature("feedback")])
def list_feedback(project_id: str, user: User = Depends(current_user), c=Depends(container)):
    """A list of flags for managers (all of them) and for everyone else (their own and their queue). An executive gets counts
    only: {counts_only: true, total, counts: [{target_kind, kind, open, resolved, dismissed}]}, never a note or an author."""
    check_project(c, user, project_id)
    if user.role == "executive":
        return feedback.counts(_ctx(c, user), project_id)
    return feedback.visible(_ctx(c, user), project_id)


@router.patch("/feedback/{feedback_id}", dependencies=[feature("feedback")])
def update_feedback(feedback_id: str, body: FeedbackPatch, user: User = Depends(current_user), c=Depends(container)):
    fb = c.lifecycle.get_feedback(feedback_id)
    if not fb:
        raise HTTPException(404, "Feedback not found")
    check_project(c, user, fb.project_id)
    if fb.owner_id != user.id and user.role not in {"manager", "admin"}:
        raise HTTPException(403, "Only its owner or a manager may change it")
    out = c.lifecycle.update_feedback(feedback_id, status=body.status)
    if body.status != "open" and fb.created_by and fb.created_by != user.id:
        c.store.notify(fb.created_by, "feedback", f"Your feedback on {fb.target_ref[:80]} was {body.status}.",
                       f"/feedback?project={fb.project_id}")
    _audit(c, user, "feedback.updated", feedback_id, {"status": body.status})
    return out


# ---------------------------------------------------------------- check-ins, surveys, completion
class CheckinIn(BaseModel):
    day: int = Field(ge=1, le=365)
    answers: dict = Field(default_factory=dict)


@router.get("/onboardings/{onboarding_id}/checkins", dependencies=[feature("checkins")])
def list_checkins(onboarding_id: str, user: User = Depends(current_user), c=Depends(container)):
    return visibility.view(_hire(c, user, onboarding_id, HIRE, MANAGER), ["checkins"])


@router.post("/onboardings/{onboarding_id}/checkins", status_code=201, dependencies=[feature("checkins")])
def write_checkin(onboarding_id: str, body: CheckinIn, user: User = Depends(current_user), c=Depends(container)):
    """The hire writes the hire's half; a manager writes the manager's."""
    hc = _hire(c, user, onboarding_id, HIRE, MANAGER)
    role = "hire" if hc.ob.new_hire_id == user.id else "manager"
    if body.day not in T.checkin_days(hc.program.duration_days) or body.day > hc.day:
        raise HTTPException(422, f"Check-ins are at days {T.checkin_days(hc.program.duration_days)}, once the day is reached")
    row = CI.save(hc, body.day, role, body.answers)
    _audit(c, user, "checkin.written", onboarding_id, {"day": body.day, "role": role})
    return row


class SurveyIn(BaseModel):
    onboarding_id: str
    day: int = Field(ge=1, le=365)
    answers: dict[str, int] = Field(default_factory=dict)


@router.post("/surveys", status_code=204, dependencies=[feature("surveys")])
def submit_survey(body: SurveyIn, user: User = Depends(current_user), c=Depends(container)):
    """Anonymous: the answer row has no person id, and nothing about it is audited but the fact that a survey was answered."""
    hc = _hire(c, user, body.onboarding_id, HIRE)
    if body.day not in T.checkin_days(hc.program.duration_days) or body.day > hc.day:
        raise HTTPException(422, "Not a survey day yet")
    if body.day in hc.program.survey_days or not CI.submit_survey(hc, body.day, body.answers):  # one response per onboarding and day
        raise HTTPException(409, "Already answered")


@router.get("/surveys/summary", dependencies=[feature("surveys")])
def survey_summary(project_id: str | None = None, user: User = Depends(require("manager", "executive")), c=Depends(container)):
    if project_id:
        check_project(c, user, project_id)
    return CI.survey_summary(_ctx(c, user), user.org_id, project_id)


class CompletionIn(BaseModel):
    decision: Literal["complete", "extend"]
    note: str = Field("", max_length=2000)
    extend_days: int = Field(0, ge=0, le=180)


@router.get("/onboardings/{onboarding_id}/completion", dependencies=[feature("completion")])
def get_completion(onboarding_id: str, run_id: str | None = Query(None, max_length=64), user: User = Depends(current_user),
                   c=Depends(container)):
    """The completion review with the Planner's drafted sign-off (written again only when the criteria change; then the Planner
    thinks, on topic agent:<run_id>, and the draft keeps its run's shared steps in `draft.agent`)."""
    hc = _hire(c, user, onboarding_id, HIRE, MANAGER)
    out = _agent(c, {"type": "completion.requested", "project_id": hc.ob.project_id, "onboarding_id": onboarding_id, "user_id": user.id,
                     **_watch(c, run_id)},
                 "The Planner is unavailable right now; try again in a moment.")  # the Planner node, through the Supervisor's graph
    return {"completion": out["completion"]}


@router.post("/onboardings/{onboarding_id}/completion", dependencies=[feature("completion")])
def sign_off(onboarding_id: str, body: CompletionIn, user: User = Depends(current_user), c=Depends(container)):
    hc = _hire(c, user, onboarding_id, MANAGER)
    if hc.ob.new_hire_id == user.id:
        raise HTTPException(403, "Nobody signs off their own program")
    if hc.program.completed_at:
        raise HTTPException(409, "Already completed")
    if body.decision == "extend" and not body.extend_days:
        raise HTTPException(422, "Say how many days to extend by")
    record = completion.sign_off(hc, body.decision, body.note.strip(), body.extend_days)
    _audit(c, user, f"completion.{body.decision}", onboarding_id, {"unmet": record["unmet"]})
    if body.decision == "complete":  # the Planner reads the cohort's completions for template improvements
        publish(c, "reviews", {"type": "program.completed", "project_id": hc.ob.project_id, "onboarding_id": onboarding_id,
                               "dedupe": f"completed:{onboarding_id}"})
    return record


# ---------------------------------------------------------------- offboarding handover
class HandoverIn(BaseModel):
    project_id: str
    user_id: str
    last_day: date
    run_id: str | None = Field(None, max_length=64)  # as in RouteIn: the Keeper's handover plan streams on topic agent:<run_id>


ItemKey = Annotated[str, StringConstraints(min_length=2, max_length=1200, pattern=r"^[a-z_]{2,20}(:|$)")]  # "<kind>:<ref>", or a gap's key


class HandoverPatch(BaseModel):
    status: Literal["draft", "in_progress", "done"] | None = None
    done: list[ItemKey] = Field(default_factory=list, max_length=500)  # keys of the transfer set's items (others: 422)
    undone: list[ItemKey] = Field(default_factory=list, max_length=500)
    successor_id: str | None = None
    reassign: bool = False


@router.get("/handovers", dependencies=[feature("handover")])
def list_handovers(project_id: str | None = None, user: User = Depends(require("manager")), c=Depends(container)):
    mine = {p.id for p in c.store.projects_for(user)}
    return [h for h in c.lifecycle.handovers(user.org_id) if h.project_id in mine and (not project_id or h.project_id == project_id)]


@router.post("/handovers", status_code=201, dependencies=[feature("handover")])
def create_handover(body: HandoverIn, user: User = Depends(require("manager")), c=Depends(container)):
    check_project(c, user, body.project_id)
    person = c.store.get_user(body.user_id)
    if not person or person.org_id != user.org_id or not c.store.is_member(body.project_id, person.id):
        raise HTTPException(422, "That person is not a member of this project")
    if any(h.user_id == person.id and h.project_id == body.project_id and h.status != "done" for h in c.lifecycle.handovers(user.org_id)):
        raise HTTPException(409, "A handover for this person is already open")
    h = _agent(c, {"type": "handover.requested", "project_id": body.project_id, "user_id": user.id, "person_id": person.id,
                   "last_day": body.last_day.isoformat(), **_watch(c, body.run_id)}, "The Keeper is unavailable right now; try again in a moment.")
    _audit(c, user, "handover.created", h["id"], {"user_id": person.id, "last_day": body.last_day.isoformat()})
    return h


def _handover(c, user: User, handover_id: str):
    """The handover, if it is this organisation's and the caller may see its project; else 404."""
    h = c.lifecycle.get_handover(handover_id)
    if not h or h.org_id != user.org_id:
        raise HTTPException(404, "Handover not found")
    check_project(c, user, h.project_id)
    return h


def _successor(c, user: User, h, successor_id: str) -> User:
    successor = _member(c, user, h.project_id, successor_id)
    if successor.id == h.user_id:
        raise HTTPException(422, "The successor must be someone else")
    if successor.status != "active":
        raise HTTPException(422, "That person's account is deactivated")
    return successor


def _handover_call(fn, *args):
    """Run a handover step; its HandoverError becomes the HTTP answer it names."""
    try:
        return fn(*args)
    except handover.HandoverError as e:
        raise HTTPException(e.status, e.detail and {**e.detail, "message": e.message} or e.message) from None


@router.patch("/handovers/{handover_id}", dependencies=[feature("handover")])
def update_handover(handover_id: str, body: HandoverPatch, user: User = Depends(require("manager")), c=Depends(container)):
    h = _handover(c, user, handover_id)
    if body.status == "done":
        raise HTTPException(422, "Complete the handover instead (POST /api/handovers/{id}/complete): it asks a person to confirm")
    if h.status == "done" and body.status != "in_progress" and (body.successor_id or body.reassign):
        raise HTTPException(409, "This handover is complete. Reopen it to change it.")
    sid = body.successor_id or ((h.plan or {}).get("successor_id") if body.reassign else None)
    to = _successor(c, user, h, sid) if sid else None  # the stored successor too: they may have left since they were chosen
    out, moved = _handover_call(handover.update, _ctx(c, user), h, body.status, body.done, body.undone, body.successor_id,
                                body.reassign)
    detail = body.model_dump(exclude_none=True, exclude={"done", "undone"})
    if body.done or body.undone:
        detail.update(ticked=len(body.done), unticked=len(body.undone))
    if h.status == "done" and body.status == "in_progress":
        detail["reopened"] = True
    _audit(c, user, "handover.updated", handover_id, detail)
    if moved is not None and to is not None:  # the same entry the transfer route writes: who received what
        _audit(c, user, "handover.transferred", handover_id, {"to": to.id, "user_id": h.user_id, "moved": moved})
    return out


@router.get("/handovers/{handover_id}/transfer", dependencies=[feature("handover")])
def handover_transfer_set(handover_id: str, user: User = Depends(require("manager")), c=Depends(container)):
    """Everything the leaver holds, what has moved and to whom, the gaps, completeness and the suggested CODEOWNERS change."""
    h = _handover(c, user, handover_id)
    out = _handover_call(handover.transfer_set, _ctx(c, user), h)
    out["can_deactivate"] = user.role == "admin" and h.user_id != user.id
    return out


class TransferIn(BaseModel):
    successor_id: str = Field(min_length=1, max_length=64)


@router.post("/handovers/{handover_id}/transfer", dependencies=[feature("handover")])
def transfer_handover(handover_id: str, body: TransferIn, user: User = Depends(require("manager")), c=Depends(container)):
    """Move what Welcome Wave holds to the successor (a person's decision: the Keeper only suggests one)."""
    h = _handover(c, user, handover_id)
    to = _successor(c, user, h, body.successor_id)
    out, moved = _handover_call(handover.transfer, _ctx(c, user), h, to)
    _audit(c, user, "handover.transferred", handover_id, {"to": to.id, "user_id": h.user_id, "moved": moved})
    return {"handover": out, "moved": moved}


class AttachmentIn(BaseModel):
    kind: Literal["repo", "document", "link"]
    ref: str | None = Field(None, max_length=1000)  # repo: the file's path; document: the uploaded document's id
    url: str | None = Field(None, max_length=2000)  # link: an https URL
    title: str | None = Field(None, max_length=200)
    note: str | None = Field(None, max_length=500)
    owner_id: str | None = Field(None, max_length=64)  # who owns it next; empty: the successor


@router.post("/handovers/{handover_id}/attachments", status_code=201, dependencies=[feature("handover")])
def add_handover_attachment(handover_id: str, body: AttachmentIn, user: User = Depends(require("manager")), c=Depends(container)):
    """Attach a document the successor needs and the handover did not find: a repository file, an uploaded document, a link."""
    h = _handover(c, user, handover_id)
    if body.owner_id:
        _successor(c, user, h, body.owner_id)
    out, item = _handover_call(handover.add_attachment, _ctx(c, user), h, body.kind, body.ref, body.url, body.title, body.note,
                               body.owner_id)
    _audit(c, user, "handover.attachment_added", handover_id,
           {"attachment_id": item["id"], "kind": item["kind"], "ref": item["ref"], "owner_id": item["owner_id"]})
    return {"handover": out, "attachment": item}


@router.delete("/handovers/{handover_id}/attachments/{attachment_id}", dependencies=[feature("handover")])
def remove_handover_attachment(handover_id: str, attachment_id: str, user: User = Depends(require("manager")), c=Depends(container)):
    h = _handover(c, user, handover_id)
    out, gone = _handover_call(handover.remove_attachment, _ctx(c, user), h, attachment_id)
    _audit(c, user, "handover.attachment_removed", handover_id, {"attachment_id": attachment_id, "kind": gone["kind"], "ref": gone.get("ref")})
    return {"handover": out}


class CompleteIn(BaseModel):
    confirm: str = Field(min_length=1, max_length=200)  # the leaver's name, typed by the person completing it
    accept_gaps: bool = False  # complete although gaps are open (they are recorded)
    deactivate: bool = False  # also deactivate the leaver's account (admins only)


@router.post("/handovers/{handover_id}/complete", dependencies=[feature("handover")])
def complete_handover(handover_id: str, body: CompleteIn, user: User = Depends(require("manager")), c=Depends(container)):
    """Finish the handover after a person confirmed it. Removing the leaver (deactivating their account) is an admin's call."""
    h = _handover(c, user, handover_id)
    if body.deactivate and user.role != "admin":
        raise HTTPException(403, "Only an admin may deactivate an account")
    person = c.store.get_user(h.user_id)
    if person is None:
        raise HTTPException(404, "The person this handover is for is no longer in the organisation")
    if not handover.confirms(body.confirm, person):
        raise HTTPException(422, f"Type their name to confirm: {handover.bare_name(person.name)}")
    out = _handover_call(handover.complete, _ctx(c, user), h, body.accept_gaps, body.deactivate)
    _audit(c, user, "handover.completed", handover_id,
           {"user_id": person.id, "to": out["handover"].plan.get("completed", {}).get("to"), "moved": out["moved"],
            "accepted_gaps": [g["key"] for g in out["accepted_gaps"]], "deactivated": out["deactivated"]})
    if out["deactivated"]:
        _audit(c, user, "member.deactivated", person.id, {"via": "handover", "handover_id": handover_id})
    return out


# ---------------------------------------------------------------- what a hire can see about themselves
@router.get("/onboardings/{onboarding_id}/visibility")
def hire_visibility(onboarding_id: str, user: User = Depends(current_user), c=Depends(container)):
    return visibility.visibility(hires.refresh(_hire(c, user, onboarding_id, HIRE, MANAGER)))


# ---------------------------------------------------------------- leadership reporting
def _report(c, user: User, project_id: str | None, team_id: str | None) -> dict:
    if project_id:
        check_project(c, user, project_id)
        projects = [project_id]
    else:
        projects = [p.id for p in c.store.projects_for(user)]
    if team_id:  # a team of the projects this report covers, else as if it did not exist
        team = c.lifecycle.get_team(team_id)
        if not team or team.org_id != user.org_id or team.project_id not in projects:
            raise HTTPException(404, "Team not found")
    return reporting.build(_ctx(c, user), projects, team_id)


@router.get("/reports/onboarding", dependencies=[feature("reports")])
def report(project_id: str | None = None, team_id: str | None = None, user: User = Depends(require("manager", "executive")),
           c=Depends(container)):
    """Figures for this cohort and the previous one, each with how it is worked out and its floor, plus `teams` (the same
    figures per team) and, over all teams, the count of single-owner critical areas. `team_id` narrows it to one team."""
    return _report(c, user, project_id, team_id)


@router.get("/reports/onboarding.csv", dependencies=[feature("reports")])
def report_csv(project_id: str | None = None, team_id: str | None = None, user: User = Depends(require("manager", "executive")),
               c=Depends(container)):
    return Response(reporting.to_csv(_report(c, user, project_id, team_id)), media_type="text/csv",
                    headers={"Content-Disposition": 'attachment; filename="onboarding-report.csv"'})


# ---------------------------------------------------------------- the organisation's data
class DeleteOrgIn(BaseModel):
    confirm: str = Field(max_length=200)


@router.get("/org/export")
def export_org(user: User = Depends(require("admin")), c=Depends(container)):
    """Every row of the organisation, table by table, as JSON. Derived search data and credentials are left out, and the
    anonymous survey is aggregates only (app/lifecycle/orgdata.py)."""
    data = orgdata.export(c, user.org_id)
    _audit(c, user, "organization.exported", user.org_id, {"tables": len(data)})
    return Response(content=json.dumps(data, default=str, ensure_ascii=False), media_type="application/json",
                    headers={"Content-Disposition": f'attachment; filename="welcome-wave-export-{date.today().isoformat()}.json"'})


@router.delete("/org")
def delete_org(body: DeleteOrgIn, user: User = Depends(require("admin")), c=Depends(container)):
    """Hard delete of everything the organisation has, after its name is typed to confirm: its rows, the outbox events of its
    projects, its stored documents and recordings, and the LangGraph checkpoints of its paused runs."""
    name = c.store.org_name(user.org_id)
    if not name or body.confirm.strip() != name:
        raise HTTPException(422, "Type the organisation's name exactly to confirm")
    counts = orgdata.delete(c, user.org_id)
    c.store.audit(None, user.id, "organization.deleted", user.org_id, {"rows": sum(counts.values())})
    return {"deleted": counts}
