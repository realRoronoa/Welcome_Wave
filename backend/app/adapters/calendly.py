"""Calendly: a person's own scheduling link, opened prefilled from Welcome Wave, and the signed webhook that tells us a
session was booked or cancelled (docs/specs/scheduling.md).

Booking with Calendly happens on Calendly's page, so Welcome Wave learns of it from the organisation's webhook
subscription (invitee.created, invitee.canceled). The link we open carries utm_source=welcome-wave, the project in
utm_campaign and the KT planner session in utm_content; Calendly hands those back in the webhook's `tracking`, which is
how a booking finds its project and session. Bookings without them (someone booked the person's Calendly directly) are
not KT sessions and are ignored.
"""
from __future__ import annotations

import hashlib
import hmac
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from urllib.parse import urlencode, urlsplit, urlunsplit

SOURCE = "welcome-wave"  # utm_source on every link we open
TOLERANCE_S = 180  # a signature older (or newer) than this is refused: a captured delivery cannot be replayed later
_HOSTS = ("calendly.com",)
_KEY = re.compile(r"^[0-9a-f]{12}$")


def verify_signature(signing_key: str, header: str | None, body: bytes, now: float | None = None, tolerance_s: int = TOLERANCE_S) -> bool:
    """Calendly-Webhook-Signature: "t=<unix seconds>,v1=<hex HMAC-SHA256 of '<t>.<raw body>' with the signing key>"."""
    if not signing_key or not header:
        return False
    parts = dict(p.strip().split("=", 1) for p in header.split(",") if "=" in p)
    t, v1 = parts.get("t", ""), parts.get("v1", "")
    if not t.isdigit() or not v1:
        return False
    if abs((time.time() if now is None else now) - int(t)) > tolerance_s:
        return False
    expected = hmac.new(signing_key.encode(), t.encode() + b"." + body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, v1)


def sign(signing_key: str, body: bytes, t: int | None = None) -> str:
    """The header Calendly would send for this body (tests, and checking a subscription by hand)."""
    t = int(time.time()) if t is None else t
    return f"t={t},v1={hmac.new(signing_key.encode(), f'{t}.'.encode() + body, hashlib.sha256).hexdigest()}"


def clean_link(url: str) -> str | None:
    """A person's Calendly link, normalised (https, no query or fragment), or None if it is not one."""
    url = (url or "").strip()
    if not url:
        return None
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if not any(host == h or host.endswith("." + h) for h in _HOSTS) or len(parts.path.strip("/")) < 2 or len(url) > 300:
        return None
    return urlunsplit(("https", host, parts.path.rstrip("/"), "", ""))


def booking_link(url: str, name: str, email: str, project_id: str, kt_key: str | None = None) -> str:
    """The person's link, prefilled with who books and tagged so the webhook finds the project and the KT session."""
    params = {"name": name, "email": email, "utm_source": SOURCE, "utm_campaign": project_id}
    if kt_key:
        params["utm_content"] = kt_key
    return f"{url}?{urlencode(params)}"


@dataclass
class Booking:
    """One webhook delivery, reduced to what a meeting needs."""
    kind: str  # "created" | "canceled"
    event_uri: str
    name: str
    starts_at: datetime | None
    ends_at: datetime | None
    join_url: str | None
    project_id: str
    kt_key: str | None
    invitee: dict  # {"email", "name"}
    hosts: list[dict] = field(default_factory=list)  # [{"email", "name"}]
    guests: list[str] = field(default_factory=list)
    rescheduled: bool = False


def parse_webhook(payload: dict) -> Booking | None:
    """invitee.created / invitee.canceled from a Welcome Wave link; None for anything else (other events, other bookings)."""
    kind = {"invitee.created": "created", "invitee.canceled": "canceled"}.get(payload.get("event"))
    p = payload.get("payload")
    if not kind or not isinstance(p, dict):
        return None
    tracking = p.get("tracking") or {}
    project_id = (tracking.get("utm_campaign") or "").strip()
    if tracking.get("utm_source") != SOURCE or not project_id or len(project_id) > 64:
        return None
    ev = p.get("scheduled_event") if isinstance(p.get("scheduled_event"), dict) else {}
    uri = ev.get("uri") or (p.get("event") if isinstance(p.get("event"), str) else "")
    if not uri:
        return None
    loc = ev.get("location") or {}
    join = loc.get("join_url") or (loc.get("location") if str(loc.get("location") or "").startswith("https://") else None)
    key = (tracking.get("utm_content") or "").strip().lower()
    return Booking(
        kind=kind, event_uri=uri, name=(ev.get("name") or "KT session")[:200],
        starts_at=_when(ev.get("start_time")), ends_at=_when(ev.get("end_time")), join_url=join,
        project_id=project_id, kt_key=key if _KEY.match(key) else None,
        invitee={"email": (p.get("email") or "").lower(), "name": p.get("name") or ""},
        hosts=[{"email": (m.get("user_email") or "").lower(), "name": m.get("user_name") or ""} for m in ev.get("event_memberships") or []
               if m.get("user_email")],
        guests=[(g.get("email") or "").lower() for g in ev.get("event_guests") or [] if g.get("email")],
        rescheduled=bool(p.get("rescheduled")),
    )


def _when(value) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")) if value else None
    except ValueError:
        return None
