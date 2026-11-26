"""The milestone board API (app/lifecycle/board.py). Thin: validation and who may call what, then the board module.

Who: the hire reads their own board and moves their own cards between columns; the project's managers and the organisation's
admins read every hire's board and change everything on it. Nobody else: someone unrelated to the onboarding gets 404 (as
if it did not exist), a related person whose relation may not call the route (a mentor, or the hire on a manager's route)
403. docs/PERMISSIONS.md is the matrix, tests/test_board.py and tests/test_permissions_matrix.py hold the routes to it.
Every write answers with the whole board, so the web app replaces what it showed (and undoes an optimistic move on error)."""
from __future__ import annotations

from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.api.auth import check_project, container, current_user
from app.api.lifecycle import HIRE, MANAGER, _hire
from app.lifecycle import board as B
from app.lifecycle import hires
from app.lifecycle.context import Ctx, HireCtx
from app.ports import User

router = APIRouter()


def _board(c, user: User, onboarding_id: str, manager_only: bool = False) -> tuple[HireCtx, bool]:
    """The onboarding and whether the caller manages it. The hire may read and move; managers and admins may do anything."""
    hc = _hire(c, user, onboarding_id, *((MANAGER,) if manager_only else (HIRE, MANAGER)))
    manage = user.role in {"manager", "admin"} and hc.ob.new_hire_id != user.id
    if manager_only and not manage:  # _hire already refuses the hire; kept so the rule holds here whatever _hire becomes
        raise HTTPException(403, "Only the hire's manager changes the board")
    return hc, manage


def _run(fn, *args, **kwargs) -> dict:
    try:
        return fn(*args, **kwargs)
    except B.BoardError as e:
        raise HTTPException(e.status, {"code": e.code, "message": e.message} if e.code else e.message) from None


@router.get("/board/hires")
def board_hires(project_id: str, user: User = Depends(current_user), c=Depends(container)):
    """The boards the caller may open in this project: every active hire's for managers and admins, their own for a hire."""
    check_project(c, user, project_id)
    if user.role not in {"manager", "admin", "new_hire"}:
        raise HTTPException(403, "The board is for new hires and their managers")
    ctx = Ctx.of(c, user)
    rows = [hc for hc in hires.of_projects(ctx, [project_id]) if user.role != "new_hire" or hc.ob.new_hire_id == user.id]
    return {"hires": sorted((B.summary(hc) for hc in rows), key=lambda r: (r["name"] or "").lower()),
            "can_manage": user.role in {"manager", "admin"}}


@router.get("/onboardings/{onboarding_id}/board")
def get_board(onboarding_id: str, user: User = Depends(current_user), c=Depends(container)):
    hc, manage = _board(c, user, onboarding_id)
    return B.view(hires.refresh(hc), manage)


class MoveIn(BaseModel):
    column: Literal["todo", "in_progress", "done"] | None = None
    milestone: str | None = Field(None, max_length=40)  # a milestone key: managers only
    before_id: str | None = Field(None, max_length=64)  # the card it was dropped before; none: the end of its column
    changes_requested: bool = False  # managers: back to In progress, marked "Changes requested"
    sign_off: bool = False  # managers: a submitted task is done (it stays in Done)


@router.post("/onboardings/{onboarding_id}/board/tasks/{task_id}/move")
def move_task(onboarding_id: str, task_id: str, body: MoveIn, user: User = Depends(current_user), c=Depends(container)):
    hc, manage = _board(c, user, onboarding_id)
    return _run(B.move, hc, user, task_id, manage, body.column, body.milestone, body.before_id, body.changes_requested, body.sign_off)


class TaskIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str = Field("", max_length=2000)
    kind: Literal["learn", "code", "meet"] = "learn"
    milestone: str | None = Field(None, max_length=40)  # none: the milestone the hire is in now
    due_date: date | None = None  # none: the milestone's day


class TaskPatch(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=200)
    description: str | None = Field(None, max_length=2000)
    due_date: date | None = None


@router.post("/onboardings/{onboarding_id}/board/tasks", status_code=201)
def add_task(onboarding_id: str, body: TaskIn, user: User = Depends(current_user), c=Depends(container)):
    hc, _ = _board(c, user, onboarding_id, manager_only=True)
    return _run(B.add_task, hc, user, body.title.strip(), body.description.strip(), body.kind, body.milestone, body.due_date)


@router.patch("/onboardings/{onboarding_id}/board/tasks/{task_id}")
def edit_task(onboarding_id: str, task_id: str, body: TaskPatch, user: User = Depends(current_user), c=Depends(container)):
    hc, _ = _board(c, user, onboarding_id, manager_only=True)
    fields = body.model_dump(exclude_none=True)
    if "title" in fields:
        fields["title"] = fields["title"].strip()
    return _run(B.edit_task, hc, user, task_id, fields)


@router.delete("/onboardings/{onboarding_id}/board/tasks/{task_id}")
def remove_task(onboarding_id: str, task_id: str, user: User = Depends(current_user), c=Depends(container)):
    hc, _ = _board(c, user, onboarding_id, manager_only=True)
    return _run(B.remove_task, hc, user, task_id)


class MilestoneIn(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    due_day: int = Field(ge=1, le=B.MAX_DAY)


class MilestoneEdit(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=120)
    due_day: int | None = Field(None, ge=1, le=B.MAX_DAY)


class OrderIn(BaseModel):
    keys: list[str] = Field(min_length=1, max_length=20)


@router.post("/onboardings/{onboarding_id}/board/milestones", status_code=201)
def add_milestone(onboarding_id: str, body: MilestoneIn, user: User = Depends(current_user), c=Depends(container)):
    hc, _ = _board(c, user, onboarding_id, manager_only=True)
    return _run(B.add_phase, hc, user, body.title.strip(), body.due_day)


@router.put("/onboardings/{onboarding_id}/board/milestones/order")
def reorder_milestones(onboarding_id: str, body: OrderIn, user: User = Depends(current_user), c=Depends(container)):
    hc, _ = _board(c, user, onboarding_id, manager_only=True)
    return _run(B.reorder_phases, hc, user, body.keys)


@router.patch("/onboardings/{onboarding_id}/board/milestones/{milestone_key}")
def edit_milestone(onboarding_id: str, milestone_key: str, body: MilestoneEdit, user: User = Depends(current_user),
                   c=Depends(container)):
    hc, _ = _board(c, user, onboarding_id, manager_only=True)
    return _run(B.edit_phase, hc, user, milestone_key, body.title.strip() if body.title else None, body.due_day)


@router.delete("/onboardings/{onboarding_id}/board/milestones/{milestone_key}")
def remove_milestone(onboarding_id: str, milestone_key: str, move_to: str | None = Query(None, max_length=40),
                     user: User = Depends(current_user), c=Depends(container)):
    hc, _ = _board(c, user, onboarding_id, manager_only=True)
    return _run(B.remove_phase, hc, user, milestone_key, move_to)
