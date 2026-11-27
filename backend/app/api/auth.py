"""Who is calling. Identity Platform (Firebase Auth) ID tokens in production. In demo mode, the X-Demo-User header: the
member id the demo sign-in (POST /api/demo/login) answered with; a request without one is not signed in.

A verified login maps to a `users` row by its Firebase uid. On a person's first sign-in there is no row yet: if they
were invited (by email) the API answers 403 `invitation_pending` with the invitations, and the person explicitly joins
one (POST /api/invitations/accept); otherwise it answers 403 `no_account` and the web app offers sign-up, which
creates a new organisation. A join can be undone (POST /api/me/leave), which frees the login for another invitation.
"""
from __future__ import annotations

from dataclasses import dataclass

from fastapi import Depends, Header, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from app.ports import Project, User


def container(request: Request):
    return request.app.state.container


@dataclass
class Identity:
    uid: str
    email: str
    email_verified: bool
    name: str


def _set_tenant(org_id: str | None) -> None:
    try:
        from app.adapters.postgres import current_org

        current_org.set(org_id)  # every query after this runs under row-level security for this org
    except ImportError:
        pass


def _verify(c, authorization: str | None) -> Identity:
    if c.settings.is_demo:
        raise HTTPException(400, "Sign-in is not used in demo mode")
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Missing bearer token")
    token = authorization.removeprefix("Bearer ")
    try:
        if c.settings.firebase_auth_emulator_host and c.settings.env != "prod":
            # the local emulator issues unsigned tokens that only the Admin SDK (pointed at the emulator) accepts
            from firebase_admin import auth as fb_auth

            claims = fb_auth.verify_id_token(token, clock_skew_seconds=10)
        else:
            # real Firebase: checked against Google's public keys; needs no Google Cloud credentials (app/firebase_tokens.py)
            from app.firebase_tokens import verify

            claims = verify(token, c.settings.firebase_project)
    except Exception as e:  # expired, wrong project, malformed, not signed by Google
        raise HTTPException(401, "Invalid or expired token") from e
    return Identity(uid=claims["uid"], email=(claims.get("email") or "").lower(), email_verified=bool(claims.get("email_verified")),
                    name=claims.get("name") or "")


async def identity(request: Request, authorization: str | None = Header(default=None)) -> Identity:
    """The verified login behind the request (no account needed yet)."""
    return await run_in_threadpool(_verify, container(request), authorization)


DEACTIVATED = {"code": "deactivated",
               "message": "This account has been deactivated. Ask an admin of your organisation to reactivate it."}


def refuse_deactivated(user: User) -> User:
    """A deactivated member (POST /api/org/members/{id}/deactivate) cannot sign in or call the API; their data stays."""
    if user.status == "deactivated":
        raise HTTPException(403, detail=DEACTIVATED)
    return user


def _resolve(c, authorization: str | None, x_demo_user: str | None, allow_deactivated: bool = False) -> User:
    """Blocking part of sign-in (token verification, database lookups). Runs in the threadpool. `allow_deactivated`: the one
    thing a deactivated member may still do is leave (POST /api/me/leave), which frees their login for another organisation."""
    check = (lambda u: u) if allow_deactivated else refuse_deactivated
    if c.settings.is_demo:
        if not x_demo_user:
            raise HTTPException(401, "Sign in to the demo first")
        user = c.store.get_user(x_demo_user)
        if not user:
            raise HTTPException(403, "Unknown demo user")
        return check(user)
    if c.settings.demo_showcase and authorization and authorization.startswith("Bearer wwdemo."):
        from app import showcase

        return check(showcase.resolve_user(c, authorization.removeprefix("Bearer ")))
    who = _verify(c, authorization)
    user = c.store.user_by_firebase(who.uid)
    if user:
        return check(user)
    if who.email and not who.email_verified and c.settings.require_verified_email:
        raise HTTPException(403, detail={"code": "email_not_verified", "message": "Verify your email address, then sign in again."})
    pending = c.store.pending_invitations(who.email) if who.email else []
    if pending:  # never join an organisation silently: the person sees who invited them and chooses
        raise HTTPException(403, detail={"code": "invitation_pending", "message": "You have been invited to join Welcome Wave.",
                                         "invitations": pending})
    raise HTTPException(403, detail={"code": "no_account", "message": "This login has no Welcome Wave account yet."})


async def current_user(
    request: Request,
    authorization: str | None = Header(default=None),
    x_demo_user: str | None = Header(default=None),
) -> User:
    """Async on purpose. FastAPI runs a sync dependency inside a *copy* of the request context, so a tenant set there
    would vanish before the endpoint runs and row-level security would hide every row. Here the blocking work runs in
    the threadpool and the tenant is set on the request task itself, which every later dependency and the endpoint
    (sync endpoints run in a copy of this context) inherit."""
    c = container(request)
    _set_tenant(None)
    leaving = request.method == "POST" and request.url.path in DEACTIVATED_ALLOWED
    user = await run_in_threadpool(_resolve, c, authorization, x_demo_user, leaving)
    _set_tenant(user.org_id)
    request.state.user = user
    if user.role in READ_ONLY_ROLES and request.method not in SAFE_METHODS and request.url.path not in READ_ONLY_ALLOWED:
        raise HTTPException(403, "Executives have read-only access")
    if c.settings.legal_enforce:  # off by default: 403 `legal_required` on workspace routes until the terms are accepted
        from app import legal

        await run_in_threadpool(legal.enforce, c, user, request.method, request.url.path)
    return user


# The executive role reads organisation-level reports and changes nothing: every write is refused here, for every route that
# signs the caller in, whatever the route itself allows. Leaving the organisation, marking one's own notifications read and
# accepting the terms for oneself (app/api/legal.py) are not changes to the organisation's data.
READ_ONLY_ROLES = frozenset({"executive"})
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
READ_ONLY_ALLOWED = frozenset({"/api/me/leave", "/api/notifications/read", "/api/legal/accept"})
# A deactivated member can call nothing else: leaving unbinds the login, so it can join another organisation.
DEACTIVATED_ALLOWED = frozenset({"/api/me/leave"})


def require(*roles: str):
    async def check(user: User = Depends(current_user)) -> User:
        if user.role not in roles and user.role != "admin":
            raise HTTPException(403, f"Requires role: {', '.join(roles)}")
        return user

    return check


def check_project(c, user: User, project_id: str) -> Project:
    """The project exists, belongs to the caller's organisation, and the caller is a member (admins see all)."""
    try:
        project = c.store.get_project(project_id)
    except (LookupError, ValueError, TypeError):  # unknown or malformed id. A database error is not caught: it is a 500, not a 404
        project = None
    if not project or project.org_id != user.org_id:
        raise HTTPException(404, "Project not found")
    if user.role != "admin" and not c.store.is_member(project_id, user.id):
        raise HTTPException(404, "Project not found")
    return project
