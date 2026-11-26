"""Recording consent and removal requests (app/services/recordings.py has the rules; docs/SECURITY.md 5 and 8).

    POST /api/meetings/{id}/recording/consent                          {"confirm": true}
        Whoever may add the session's recording confirms that everyone in it was told it is recorded and agreed. The upload
        and Drive import routes take the same confirmation with the request (`consent: true`); this route is for a Google
        Calendar session whose recording the poller brings in by itself, which it does only after someone confirmed.
        409 `not_held_yet` for a session that has not started: nobody can know yet who took part. This is the confirmation
        AFTER the call; it replaces the promise the booker made at booking (a Google Meet session carries one, see
        POST /api/meetings `recording_promise`), and the poller accepts either.
    POST /api/meetings/{id}/recording/removal-requests                 {"reason"?}
        Anyone on the project asks for the session's recording to be removed. The project's managers are told. Nothing is
        removed: a manager does that with DELETE /api/meetings/{id}/recording, which closes the request.
    GET  /api/recording-removal-requests?project_id=&meeting_id=&status=
        Managers and admins: the project's requests. Everyone else: their own.
    POST /api/meetings/{id}/recording/removal-requests/{request_id}/decline   {"note"?}
        A manager declines one, with a note for whoever asked.

The routes are served by app/api/uploads.py's router. In the demo these work as everywhere else (the demo's sessions are
sample data, and so are their consent records).
"""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.adapters import recording_store
from app.api.auth import check_project, container, current_user, require
from app.ports import User
from app.services import recordings

router = APIRouter()


def _meeting(c, user: User, meeting_id: str):
    m = c.store.get_meeting(meeting_id)
    if not m:
        raise HTTPException(404, "Not found")
    check_project(c, user, m.project_id)
    return m


class ConsentIn(BaseModel):
    confirm: bool = False


@router.post("/meetings/{meeting_id}/recording/consent")
def confirm_consent(meeting_id: str, body: ConsentIn, user: User = Depends(current_user), c=Depends(container)):
    """Confirm the session's recording consent (see the module docstring). Confirming again changes nothing: the first
    confirmation is the one kept."""
    from app.api.uploads import may_add_recording

    m = _meeting(c, user, meeting_id)
    may_add_recording(user, m)
    if m.status == "cancelled":
        raise HTTPException(409, "This session was cancelled")
    if (m.recording_consent is None or m.recording_consent.kind == "promised_at_booking") and m.status == "scheduled" and recordings.not_held_yet(m):
        raise HTTPException(409, detail={"code": recordings.NOT_HELD_YET, "message": recordings.NOT_HELD_MESSAGE})
    if not body.confirm:
        raise HTTPException(422, detail={"code": recordings.CONSENT_REQUIRED, "message": recordings.CONSENT_MESSAGE})
    return {"meeting": recordings.confirm_consent(c, user, m, "confirm"), "statement": recordings.CONSENT_STATEMENT}


class RemovalIn(BaseModel):
    reason: str = Field("", max_length=recordings.REASON_MAX)


@router.post("/meetings/{meeting_id}/recording/removal-requests", status_code=201)
def request_removal(meeting_id: str, body: RemovalIn, user: User = Depends(current_user), c=Depends(container)):
    """Ask for this session's recording to be removed. `created` is false when the caller's earlier request is still open."""
    m = _meeting(c, user, meeting_id)
    try:
        row, created = recordings.request_removal(c, user, m, body.reason)
    except recordings.NothingRecorded:
        raise HTTPException(409, detail={"code": "nothing_recorded", "message": "This session has no recording to remove."}) from None
    return {"request": row, "created": created}


@router.get("/recording-removal-requests")
def removal_requests(project_id: str, meeting_id: str | None = None, status: Literal["open", "removed", "declined"] | None = None,
                     user: User = Depends(current_user), c=Depends(container)):
    """The project's removal requests, oldest first: all of them for its managers and admins, the caller's own for anyone else."""
    check_project(c, user, project_id)
    rows = recording_store.removal_requests(c.store, project_id=project_id, meeting_id=meeting_id, status=status)
    if user.role not in {"manager", "admin"}:
        rows = [r for r in rows if r.get("requested_by") == user.id]
    return {"requests": rows}


class DeclineIn(BaseModel):
    note: str = Field("", max_length=recordings.REASON_MAX)


@router.post("/meetings/{meeting_id}/recording/removal-requests/{request_id}/decline")
def decline_removal(meeting_id: str, request_id: str, body: DeclineIn, user: User = Depends(require("manager")), c=Depends(container)):
    """Decline one open request; whoever asked is told, with the note. Removing the recording instead closes every open
    request of the session (DELETE /api/meetings/{id}/recording)."""
    m = _meeting(c, user, meeting_id)
    row = recording_store.get_removal_request(c.store, request_id)
    if not row or row["meeting_id"] != m.id:
        raise HTTPException(404, "Not found")
    done = recordings.decline_removal(c, user, m, request_id, body.note)
    if done is None:
        raise HTTPException(409, "This request has already been decided")
    return {"request": done}
