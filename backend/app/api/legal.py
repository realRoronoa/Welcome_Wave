"""Legal acceptances: what each person has accepted, and the organisation's data-processing agreement (app/legal.py).

GET  /api/legal/status            any member: {needs, accepted: [{document, version, at}], versions, required, enforced}
POST /api/legal/accept            any member: {documents: [...], versions?: {...}} records the current version of each
                                  ('terms', 'privacy', 'recording') for the caller; answers the new status and `recorded`
GET  /api/org/legal               admins: {dpa: {version, accepted, accepted_at, accepted_by, previous}, versions, operator}
POST /api/org/legal/dpa           admins: {version?} accepts the data-processing agreement for the organisation
GET  /api/org/legal/dpa/record    admins: the acceptance as a text file to keep (version, time, who)

`needs` lists the personal documents whose CURRENT version the caller has not accepted, so when a document's version
changes (app/legal.py VERSIONS and frontend/src/legal/documents.ts, together) everyone is asked again, once. Accepting is
idempotent: a version already accepted is not recorded or audited twice. Executives are read-only everywhere else and may
still accept (app/api/auth.py READ_ONLY_ALLOWED): it changes nothing of the organisation's data.

Sign-up and joining by invitation record terms and privacy in the same request (app/api/routes.py); these routes are for
everyone who joined before the documents existed, and for every later version.

The organisation's acceptance of the data-processing agreement is one row per version: the first admin to accept is the one
recorded, and a second click (by them or another admin) answers the same record. Every acceptance is audited:
`legal.accepted` (the person) and `legal.dpa_accepted` (the organisation).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from app import legal
from app.api.auth import container, current_user, require
from app.ports import User

router = APIRouter()

NO_DPA = {"code": "dpa_not_accepted", "message": "This organisation has not accepted that version of the Data Processing Agreement."}


class AcceptIn(BaseModel):
    documents: list[str] = Field(min_length=1, max_length=8)
    # optional: the versions the person was shown. One that is no longer current answers 409 `version_changed`.
    versions: dict[str, str] | None = Field(default=None, max_length=8)


class DpaIn(BaseModel):
    version: str | None = Field(default=None, max_length=10)  # optional: the version the admin was shown


@router.get("/legal/status")
def legal_status(user: User = Depends(current_user), c=Depends(container)):
    """What the caller still needs to accept (the current terms, privacy policy and recording notice), and what they have."""
    return legal.status(c, user)


@router.post("/legal/accept")
def legal_accept(body: AcceptIn, request: Request, user: User = Depends(current_user), c=Depends(container)):
    """Record the caller's acceptance of the current version of each document. `recorded` lists the ones that were new."""
    documents = legal.check_documents(body.documents, legal.PERSONAL)
    legal.check_versions(body.versions, documents)
    recorded = legal.record(c, user, documents, "prompt", request)
    return {**legal.status(c, user), "recorded": recorded}


@router.get("/org/legal")
def org_legal(user: User = Depends(require("admin")), c=Depends(container)):
    """The organisation's data-processing agreement: accepted or not, by whom and when, and earlier versions."""
    return legal.org_status(c, user.org_id)


@router.post("/org/legal/dpa")
def accept_dpa(request: Request, body: DpaIn | None = None, user: User = Depends(require("admin")), c=Depends(container)):
    """Accept the current Data Processing Agreement for the organisation. Admins only (`require` lets nobody else through,
    and the check below holds even if that ever changed): the person who accepts binds the organisation."""
    if user.role != "admin":
        raise HTTPException(403, "Requires role: admin")
    legal.check_versions({"dpa": body.version} if body and body.version else None, ["dpa"])
    recorded = legal.record(c, user, ["dpa"], "organisation", request)
    return {**legal.org_status(c, user.org_id), "recorded": bool(recorded)}


@router.get("/org/legal/dpa/record")
def dpa_record(version: str | None = None, user: User = Depends(require("admin")), c=Depends(container)):
    """A text file recording the organisation's acceptance (newest version, or `?version=`): the version, the time and who.
    404 `dpa_not_accepted` when there is none."""
    found = legal.dpa_record(c, user.org_id, version)
    if found is None:
        raise HTTPException(404, detail=NO_DPA)
    name, text = found
    return Response(text, media_type="text/plain; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="{name}"'})
