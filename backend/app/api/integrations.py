"""Settings -> Integrations: the organisation's Google Calendar connection, each person's Calendly link, and the Calendly
webhook's state (docs/specs/scheduling.md). Included by app/api/routes.py under /api."""
from __future__ import annotations

import hashlib
import hmac
import logging
import threading
import time
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from app.api.auth import _set_tenant, check_project, container, current_user, require
from app.ports import User
from app.services import integration_status, scheduling

router = APIRouter()
log = logging.getLogger("integrations")
SETTINGS_PAGE = "/app#/settings?tab=integrations"


@router.get("/integrations")
def integrations(user: User = Depends(current_user), c=Depends(container)):
    """Google Calendar (connected or not, by which account), the Calendly webhook, and the caller's own Calendly link."""
    return scheduling.status(c, user)


@router.get("/integrations/status")
def integrations_status(refresh: bool = False, user: User = Depends(require("admin")), c=Depends(container)):
    """Admins: is each integration set up, and does it work? {items: [{key, name, configured, ok, detail}], checked_at} for gemini,
    firebase, github, google_calendar, calendly, smtp and langsmith. `ok` is true or false where a real call was made and null
    where there is nothing to call. No key, token or password is ever in it. The live calls are cached for five minutes per
    organisation; `?refresh=true` makes them again."""
    connected = bool(c.store.calendar_connection(user.org_id, "google")) if c.settings.google_calendar_ready else None
    items, at = integration_status.cached(user.org_id, c.settings, refresh, connected)
    return {"items": items, "checked_at": datetime.fromtimestamp(at, tz=timezone.utc)}


# The browser that starts a Google connection gets a cookie bound to its state; the callback is completed only in a browser
# that has it. Without it, a manager could send their consent link to someone at another company, and that person's calendar
# and Drive grant would be stored as the manager's organisation's (OAuth login CSRF, RFC 6749 section 10.12).
STATE_COOKIE = "ww_google_state"
CALLBACK_PATH = "/api/integrations/google/callback"


def _state_mark(state: str) -> str:
    return hashlib.sha256(b"ww-google-browser:" + state.encode()).hexdigest()


class _UsedStates:
    """States already completed, until they expire: each consent link works once (in this process)."""

    def __init__(self):
        self._lock = threading.Lock()
        self._until: dict[str, float] = {}

    def first_use(self, mark: str, ttl_s: float) -> bool:
        now = time.time()
        with self._lock:
            self._until = {k: t for k, t in self._until.items() if t > now}
            if mark in self._until:
                return False
            self._until[mark] = now + ttl_s
            return True


_used_states = _UsedStates()


def _secure(request: Request, settings) -> bool:
    return request.url.scheme == "https" or settings.public_url.strip().lower().startswith("https://")


@router.get("/integrations/google/start")
def google_start(request: Request, response: Response, user: User = Depends(require("manager")), c=Depends(container)):
    """The Google consent page to send the browser to. The state names this organisation and person, signed, for 10 minutes;
    a cookie ties it to this browser (only the callback path receives it)."""
    s = c.settings
    if s.is_demo:
        raise HTTPException(404, "Not Found")  # the demo's calendar is built in
    if not s.google_calendar_ready:
        raise HTTPException(503, "Google Calendar is not set up on this server (GOOGLE_OAUTH_CLIENT_ID and GOOGLE_OAUTH_CLIENT_SECRET).")
    from app.adapters.google_calendar import auth_url

    state = scheduling.make_state(s, user.org_id, user.id)
    response.set_cookie(STATE_COOKIE, _state_mark(state), max_age=scheduling.STATE_TTL_S, path=CALLBACK_PATH, httponly=True,
                        samesite="lax", secure=_secure(request, s))
    return {"url": auth_url(s.google_oauth_client_id, scheduling.redirect_uri(s, str(request.base_url)), state)}


@router.get("/integrations/google/callback")
async def google_callback(request: Request, code: str = "", state: str = "", error: str = "", c=Depends(container)):
    """Google sends the browser back here (no sign-in header: the signed state says who asked). The code is exchanged for
    a refresh token, which is stored encrypted; the browser returns to Settings -> Integrations with the outcome."""
    s = c.settings
    if s.is_demo or not s.google_calendar_ready:
        raise HTTPException(404, "Not Found")
    who = scheduling.read_state(s, state)
    mark = request.cookies.get(STATE_COOKIE, "")
    if not who or not hmac.compare_digest(mark, _state_mark(state)) or not _used_states.first_use(mark, scheduling.STATE_TTL_S):
        # forged, expired, opened in a browser that did not start it, or already used: start again from Settings
        return _done(RedirectResponse(f"{SETTINGS_PAGE}&google=expired", status_code=303), request, s)
    if error or not code:
        return _done(RedirectResponse(f"{SETTINGS_PAGE}&google=denied", status_code=303), request, s)
    _set_tenant(who["org_id"])
    from app.adapters.google_calendar import GoogleError, exchange_code

    def connect():
        user = c.store.get_user(who["user_id"])
        if not user or user.org_id != who["org_id"] or user.role not in {"manager", "admin"}:
            return "denied"
        got = exchange_code(s.google_oauth_client_id, s.google_oauth_client_secret, scheduling.redirect_uri(s, str(request.base_url)),
                            code, transport=c.extras.get("google_transport"))
        c.store.save_calendar_connection(user.org_id, "google", got["email"], scheduling.encrypt_token(s, got["refresh_token"]),
                                         got["scopes"], user.id)
        c.store.audit(user.org_id, user.id, "integration.google.connected", user.org_id, {"account": got["email"]})
        return "connected"

    try:
        outcome = await run_in_threadpool(connect)
    except GoogleError as e:
        log.warning("google connection failed", extra={"extra_fields": {"org_id": who["org_id"], "error": str(e)[:300]}})
        outcome = "failed"
    finally:
        _set_tenant(None)
    return _done(RedirectResponse(f"{SETTINGS_PAGE}&google={outcome}", status_code=303), request, s)


def _done(response: Response, request: Request, settings) -> Response:
    """The callback's answer, with the state cookie cleared."""
    response.delete_cookie(STATE_COOKIE, path=CALLBACK_PATH, httponly=True, samesite="lax", secure=_secure(request, settings))
    return response


@router.delete("/integrations/google", status_code=204)
def google_disconnect(user: User = Depends(require("manager")), c=Depends(container)):
    """Forget the organisation's Google account (and ask Google to drop the grant). Booked sessions keep their Meet links;
    their recordings are uploaded from now on."""
    conn = c.store.calendar_connection(user.org_id, "google")
    if not conn:
        return
    token = scheduling.decrypt_token(c.settings, conn["refresh_token_enc"])
    if token:
        from app.adapters.google_calendar import revoke

        revoke(token, transport=c.extras.get("google_transport"))
    c.store.delete_calendar_connection(user.org_id, "google")
    c.store.audit(user.org_id, user.id, "integration.google.disconnected", user.org_id, {"account": conn.get("account_email")})


class LinkIn(BaseModel):
    url: str = Field(min_length=1, max_length=300)


@router.put("/integrations/calendly/link")
def save_calendly_link(body: LinkIn, user: User = Depends(current_user), c=Depends(container)):
    """The caller's own Calendly link (https://calendly.com/<you>/<event>). Booking a KT session with them opens it, prefilled."""
    from app.adapters.calendly import clean_link

    url = clean_link(body.url)
    if not url:
        raise HTTPException(422, "Paste your Calendly link, like https://calendly.com/your-name/kt-session")
    c.store.set_scheduling_link(user.org_id, user.id, "calendly", url)
    return {"link": url}


@router.delete("/integrations/calendly/link", status_code=204)
def delete_calendly_link(user: User = Depends(current_user), c=Depends(container)):
    c.store.set_scheduling_link(user.org_id, user.id, "calendly", None)


@router.get("/scheduling/links")
def scheduling_links(project_id: str, user: User = Depends(current_user), c=Depends(container)):
    """The project members' Calendly links, and what to prefill when the caller books with one: the link opens with the
    caller's name and email, and the tags (utm_*) that let the webhook file the booking under this project and session."""
    from app.adapters.calendly import SOURCE

    project = check_project(c, user, project_id)
    return {"links": c.store.scheduling_links(project.id, "calendly"),
            "prefill": {"name": user.name, "email": user.email, "utm_source": SOURCE, "utm_campaign": project.id},
            "webhook": bool(c.settings.calendly_webhook_signing_key)}
