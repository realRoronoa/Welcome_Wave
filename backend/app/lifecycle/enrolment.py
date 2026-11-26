"""Enrolment and the 30/60/90 plan.

POST /api/onboardings/enrol takes a person (by email), a template and a start date, and makes a DRAFT:
  1. finds the person, or keeps an unbound user row for them (their acceptance of the invitation binds it later);
  2. starts the onboarding (the agents' `onboardings` row) and a draft program: the template's documents to acknowledge,
     and nothing else yet: no milestones, no access requests, no invitation;
  3. asks the Planner for ONE 30/60/90 plan through the Supervisor's `onboarding.started` event: phases Days 1-30, 31-60
     and 61-90 (thirds of the program's length), each with the template's milestones and the tasks that reach them (the
     Task agent's, the template's plan, and a task for each milestone a person marks). It waits in the manager's Approvals
     as one `task_plan` approval. With no model (or the Planner in "rules" mode) the template's plan stands in.
On approval (`finalize`, run by the Supervisor when the manager decides): the milestones the manager kept, the access requests
(their owners are told), the mentor's notice, the invitation, and the approved tasks. The invitation is created and its sign-in
link returned, for the manager to share; when SMTP is set up an email with the link follows (`email_queued`), sent by the outbox.

The plan's status is read back from that approval (`plan_status`), so approving it in Approvals is the plan approval.
"""
from __future__ import annotations

from datetime import date

from app.lifecycle import templates as T
from app.lifecycle.context import Ctx
from app.ports import Milestone, Onboarding, OnboardingProgram, ProgramTemplate

APPROVED = frozenset({"approved", "partially_approved"})


class EnrolConflict(ValueError):
    """The person already has an active onboarding in this project."""


def enrol(ctx: Ctx, project_id: str, email: str, name: str, github_login: str | None, start: date, template: ProgramTemplate,
          team_id: str | None, mentor_id: str | None, focus_area: str | None, role_title: str | None) -> dict:
    store, lc, manager = ctx.store, ctx.lc, ctx.user
    org = manager.org_id
    hire = store.user_by_email(org, email)
    invite = hire is None
    if hire:
        store.add_member(project_id, hire.id)
    else:  # not in the organisation yet: the approved plan creates their invitation, whose acceptance binds this row
        hire = lc.ensure_user(org, email, name, github_login)
    existing = next((o for o in store.onboardings(project_id) if o.new_hire_id == hire.id), None)
    adopted = existing is not None and lc.get_program(existing.id) is None
    if existing and not adopted:
        raise EnrolConflict(hire.name)
    if adopted:  # an onboarding started before programs existed (or by an older client): it gets its program now
        _forget_finished_plan(ctx, existing)
        ob = existing
    else:
        ob = store.create_onboarding(project_id, hire.id, manager.id, start, role_title or template.title)
    program = lc.create_program(
        OnboardingProgram(onboarding_id=ob.id, org_id=org, project_id=project_id, template_id=template.id,
                          template_version=template.version,
                          duration_days=template.duration_days, mentor_id=mentor_id, team_id=team_id, focus_area=focus_area or None),
        [], [], T.acknowledgements_for(template, ob.id))
    enrolment = {"invite": invite, "email": email, "name": name, "github_login": github_login, "manager_id": manager.id,
                 "mentor_id": mentor_id, "template_id": template.id}
    return {"onboarding": ob, "program": program, "invitation": None, "adopted": adopted,
            "next": "The 30/60/90 plan waits in Approvals. Approving it creates the milestones, the access requests and "
                    + ("the invitation (no email is sent: you share its link)." if invite else "the tasks.")
                    + (f" Their onboarding keeps its start date, {ob.start_date:%d %b %Y}." if adopted else ""),
            "plan_event": {"type": "onboarding.started", "project_id": project_id, "onboarding_id": ob.id, "dedupe": f"ob:{ob.id}",
                           "enrolment": enrolment,
                           "template_tasks": [t.model_dump() for t in T.plan_tasks(template, template.duration_days)]}}


class PlanInProgress(EnrolConflict):
    """An onboarding without a program is being planned right now (or its plan waits in Approvals): enrol it once that is
    settled, or the enrolment's plan would be a duplicate the event's dedupe key drops."""


def _forget_finished_plan(ctx: Ctx, ob: Onboarding) -> None:
    """Before an onboarding without a program is enrolled: its first plan run must be finished, and its plan decided, so the
    enrolment's 30/60/90 plan can run under the same key (`ob:<id>`), which plan_status reads."""
    store = ctx.store
    if any(p.get("onboarding_id") == ob.id for a in store.approvals(ob.project_id, "pending") if a.kind == "task_plan"
           for p in a.payload.get("plans", [])):
        raise PlanInProgress("A plan for this person waits in Approvals: decide it first, then enrol them")
    key = f"ob:{ob.id}"
    status = store.event_status(key)
    if status == "busy" or (status == "done" and not store.reset_event(key)):
        raise PlanInProgress("A plan for this person is still being drafted: try again in a minute")


def phases(duration_days: int) -> list[dict]:
    """Days 1-30, 31-60, 61-90 of a 90-day program; thirds of any other length."""
    a, b = round(duration_days / 3), round(2 * duration_days / 3)
    return [{"key": "days_1_30", "title": f"Days 1–{a}", "from_day": 0, "to_day": a},
            {"key": "days_31_60", "title": f"Days {a + 1}–{b}", "from_day": a + 1, "to_day": b},
            {"key": "days_61_90", "title": f"Days {b + 1}–{duration_days}", "from_day": b + 1, "to_day": duration_days}]


def _invite_link(ctx: Ctx) -> str:
    base = (ctx.settings.public_url or "").rstrip("/") if ctx.settings else ""
    return f"{base}/login" if base else "/login"


def finalize(ctx: Ctx, plan: dict, approved_tasks: int, keep_milestones: list[str] | None) -> dict | None:
    """The approved 30/60/90 plan of an enrolment (`plan` is one entry of the approval's plans): milestones, access requests,
    the mentor's notice and the invitation. Idempotent: a program whose plan was approved already is left as it is."""
    store, lc = ctx.store, ctx.lc
    e = plan.get("enrolment")
    ob = store.get_onboarding(plan["onboarding_id"])
    program = lc.get_program(plan["onboarding_id"]) if ob else None
    if not e or not program or program.plan_approved_at:
        return None
    template = lc.get_template(e["template_id"]) or lc.get_template(program.template_id)
    keep = set(keep_milestones) if keep_milestones is not None else {m["key"] for m in plan.get("milestones", [])}
    milestones = [Milestone(onboarding_id=ob.id, key=m["key"], title=m["title"], due_day=int(m["due_day"]))
                  for m in plan.get("milestones", []) if m["key"] in keep]
    lc.set_milestones(ob.id, milestones)
    manager_id = e.get("manager_id") or ob.manager_id
    mentor_id = e.get("mentor_id")
    owners = {"manager": manager_id, "mentor": mentor_id or manager_id,
              "admin": next((m.id for m in store.managers(ob.project_id) if m.role == "admin"), manager_id)}
    hire = store.get_user(ob.new_hire_id)
    name = hire.name if hire else e.get("name")
    link = f"/program/{ob.id}?project={ob.project_id}"
    access = []
    for item in T.access_for(template, ob.id, owners) if template else []:
        access.append(lc.add_access_item(item))
        if item.owner_id and item.owner_id != manager_id:
            store.notify(item.owner_id, "access_request", f"Grant {item.system} to {name} before {ob.start_date:%d %b}.", link)
    if mentor_id and mentor_id != manager_id:
        store.notify(mentor_id, "mentor", f"You are {name}'s mentor from {ob.start_date:%d %b}.", link)
    invitation = None
    if e.get("invite"):
        inv = store.create_invitation(program.org_id, ob.project_id, e["email"], "new_hire", e.get("name"), e.get("github_login"),
                                      manager_id)
        from app.services import mailing

        queued = mailing.enabled(ctx.deps)  # an email follows only when SMTP is set up (the outbox sends it: routes.decide)
        invitation = {"id": inv["id"], "email": inv["email"], "expires_at": inv.get("expires_at"), "link": _invite_link(ctx),
                      "sent": False, "email_queued": queued,
                      "note": "Invitation created. An email with the link is on its way; the link is here too if you want to share it."
                              if queued else "Invitation created. No email is sent: share the link with them."}
    lc.update_program(ob.id, plan_approved_at=ctx.clock.now())
    return {"onboarding_id": ob.id, "milestones": len(milestones), "access_items": len(access), "tasks": approved_tasks,
            "invitation": invitation}


def plan_approvals(ctx: Ctx, ob: Onboarding) -> list:
    """The task_plan approvals that include this onboarding, oldest first."""
    rows = [a for a in ctx.store.approvals(ob.project_id, None) if a.kind == "task_plan"
            and any(p.get("onboarding_id") == ob.id for p in a.payload.get("plans", []))]
    return sorted(rows, key=lambda a: a.created_at)


def plan_status(ctx: Ctx, ob: Onboarding, program: OnboardingProgram) -> dict:
    """approved (a manager approved a plan for this hire, in Approvals or at enrolment), awaiting_approval, drafting (the
    Planner is writing it) or not_started. A further proposal (after a KT call) can wait while the plan is approved."""
    approvals = plan_approvals(ctx, ob)
    # a plan approved before the program existed (an onboarding started without one, then adopted by enrol) is not this
    # program's 30/60/90 plan: its milestones and access requests come only with the plan drafted at enrolment
    since = program.created_at
    decided = [a.decided_at for a in approvals if a.status in APPROVED and a.decided_at and (since is None or a.decided_at >= since)]
    approved_at = program.plan_approved_at or (min(decided) if decided else None)
    pending = next((a for a in reversed(approvals) if a.status == "pending"), None)
    if approved_at:
        status = "approved"
    elif pending:
        status = "awaiting_approval"
    else:
        status = "drafting" if ctx.store.event_status(f"ob:{ob.id}") == "busy" else "not_started"
    return {"status": status, "approved_at": approved_at, "pending_approval_id": pending.id if pending else None,
            "why": [{"approved": "A manager approved a plan for this hire.",
                     "awaiting_approval": "The drafted 30/60/90 plan waits in the manager's Approvals.",
                     "drafting": "The Planner is drafting the plan.",
                     "not_started": "No plan has been drafted yet."}[status]]}
