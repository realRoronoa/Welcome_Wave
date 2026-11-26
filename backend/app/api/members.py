"""Members administration: who is in the organisation, their role, and whether they may sign in (admins only).

GET  /api/org/members                          every member: {id, name, email, role, status, projects: [{id, name}]}
PATCH /api/org/members/{user_id} {role}         change a member's role
POST /api/org/members/{user_id}/deactivate      they can no longer sign in or call the API; their data stays
POST /api/org/members/{user_id}/reactivate      they can sign in again

A member is a user with a login bound: someone who left, or who was enrolled and has not joined yet, is not one (404).
A change that would leave the organisation without an active admin answers 409 `last_admin` and changes nothing; an admin
cannot deactivate themselves (409 `self`). Asking for what is already so changes nothing and answers the member. Every
change is audited and the member is told. Deactivating lists what was waiting on the person (`needs_reassignment`: their
open expert requests and the active onboardings they mentor), so an admin can hand it to someone else; nothing is moved
automatically."""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.api.auth import container, require
from app.ports import LastAdmin, User

router = APIRouter(prefix="/org/members")

ROLE_WORDS = {"admin": "admin", "manager": "manager", "expert": "expert", "new_hire": "new hire", "executive": "executive"}
LAST_ADMIN = {"code": "last_admin", "message": "The organisation needs at least one active admin. Make someone else an admin first."}
NOT_YOURSELF = {"code": "self", "message": "You cannot deactivate your own account. Ask another admin."}
ACCOUNT_LINK = "/settings?tab=account"


class RoleIn(BaseModel):
    role: Literal["admin", "manager", "expert", "new_hire", "executive"]


def _member(c, admin: User, user_id: str) -> dict:
    m = c.store.org_member(admin.org_id, user_id)
    if m is None:
        raise HTTPException(404, "Member not found")
    return m


def _open_work(c, org_id: str, user_id: str) -> dict:
    """What waits on this person: expert requests routed to them and still open, and active onboardings they mentor. Ids,
    projects and dates only: what a question says stays between the hire and the people it was routed to."""
    requests = [{"id": str(r["id"]), "project_id": str(r["project_id"]), "created_at": r.get("created_at")}
                for r in c.store.expert_requests_for(user_id)]
    mentoring = []
    for p in c.lifecycle.programs(org_id):
        if p.mentor_id != user_id:
            continue
        ob = c.store.get_onboarding(p.onboarding_id)
        if ob and ob.status == "active":
            hire = c.store.get_user(ob.new_hire_id)
            mentoring.append({"onboarding_id": ob.id, "project_id": ob.project_id,
                              "hire": {"id": ob.new_hire_id, "name": hire.name if hire else None}})
    return {"expert_requests": requests, "mentoring": mentoring}


@router.get("")
def list_members(user: User = Depends(require("admin")), c=Depends(container)):
    return c.store.org_members(user.org_id)


@router.patch("/{user_id}")
def change_role(user_id: str, body: RoleIn, user: User = Depends(require("admin")), c=Depends(container)):
    before = _member(c, user, user_id)
    if before["role"] == body.role:
        return before
    try:
        c.store.set_member_role(user.org_id, before["id"], body.role)
    except LastAdmin:
        raise HTTPException(409, detail=LAST_ADMIN) from None
    c.store.audit(user.org_id, user.id, "member.role_changed", before["id"], {"from": before["role"], "to": body.role})
    c.store.notify(before["id"], "account", f"{user.name} changed your role from {ROLE_WORDS[before['role']]} to {ROLE_WORDS[body.role]}.",
                   ACCOUNT_LINK)
    return _member(c, user, before["id"])


@router.post("/{user_id}/deactivate")
def deactivate(user_id: str, user: User = Depends(require("admin")), c=Depends(container)):
    before = _member(c, user, user_id)
    if before["id"] == user.id:
        raise HTTPException(409, detail=NOT_YOURSELF)
    work = _open_work(c, user.org_id, before["id"])
    if before["status"] == "deactivated":
        return {**before, "needs_reassignment": work}
    try:
        c.store.set_member_status(user.org_id, before["id"], "deactivated")
    except LastAdmin:
        raise HTTPException(409, detail=LAST_ADMIN) from None
    c.store.audit(user.org_id, user.id, "member.deactivated", before["id"],
                  {"open_expert_requests": len(work["expert_requests"]), "mentoring": len(work["mentoring"])})
    c.store.notify(before["id"], "account", f"{user.name} deactivated your account. Your work in Welcome Wave is kept.", ACCOUNT_LINK)
    return {**_member(c, user, before["id"]), "needs_reassignment": work}


@router.post("/{user_id}/reactivate")
def reactivate(user_id: str, user: User = Depends(require("admin")), c=Depends(container)):
    before = _member(c, user, user_id)
    if before["status"] == "active":
        return before
    c.store.set_member_status(user.org_id, before["id"], "active")
    c.store.audit(user.org_id, user.id, "member.reactivated", before["id"])
    c.store.notify(before["id"], "account", f"{user.name} reactivated your account. You can sign in again.", ACCOUNT_LINK)
    return _member(c, user, before["id"])
