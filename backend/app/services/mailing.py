"""The emails Welcome Wave sends, when SMTP is set up (docs/INTEGRATIONS.md). Nothing here runs inside a request that a person
waits on, and a failure to send never fails one:

  invitation        after a plan is approved for someone not in the organisation yet. The route publishes a `mail.invitation` event
                    (ids only) to the outbox; the worker that handles it sends the message and, if the mail server fails, the
                    outbox retries it (30 seconds, doubling).
  pre-boarding link when a manager asks for it ("email it"). The link's token is shown once and stored only as a hash, so it must
                    not be written to the outbox: the route hands it to a task that runs after the response, in memory.
  expert digest     the daily tick, once per question (app/lifecycle/routing.py digests): the count and a link, never the words.
  weekly summary    the tick that writes it (the Coach's `week`), for the newest closed week only: to the hire and the manager.

With no SMTP configured (`mailer.enabled` False) every function here returns without composing anything, so the demo and every
installation without a mail server send nothing, and /api/config says `email: false`. A message body or link is never logged."""
from __future__ import annotations

import logging
from datetime import datetime

log = logging.getLogger("mailing")
SENDER_NAME = "Welcome Wave"


def enabled(deps) -> bool:
    mailer = getattr(deps, "mailer", None)
    return bool(mailer is not None and mailer.enabled)


def _base(settings) -> str:
    return (settings.public_url or "").rstrip("/")


def _first(name: str | None) -> str:
    return (name or "").split(" (")[0].split(" ")[0] or "there"


def _day(value) -> str:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return value
    return f"{value:%d %b %Y}" if hasattr(value, "strftime") else str(value)


def _signature() -> str:
    return f"\n-- {SENDER_NAME}\nThis message was sent by your organisation's Welcome Wave. Do not reply to it.\n"


def _try(kind: str, fn) -> bool:
    """Run one send where a failure must not matter (the tick, a task after a response): log the kind and the error type only."""
    try:
        fn()
        return True
    except Exception as e:  # noqa: BLE001
        log.warning("mail not sent", extra={"extra_fields": {"kind": kind, "error": type(e).__name__}})
        return False


# ---------------------------------------------------------------- invitation (outbox event: ids only)
def invitation(c, event: dict) -> dict:
    """`mail.invitation` {project_id, invitation_id, onboarding_id}. Raises when the mail server fails, so the outbox retries."""
    if not enabled(c.deps):
        return {"skipped": "email is not set up"}
    store = c.store
    inv = next((i for i in store.invitations(event["project_id"]) if str(i["id"]) == str(event["invitation_id"])), None)
    if not inv or inv.get("accepted_at"):
        return {"skipped": "the invitation is gone or already accepted"}
    project = store.get_project(event["project_id"])
    ob = store.get_onboarding(event["onboarding_id"]) if event.get("onboarding_id") else None
    manager = store.get_user(ob.manager_id) if ob else None
    base = _base(c.settings)
    link = f"{base}/login" if base else ""
    when = f" starting on {ob.start_date:%d %B %Y}" if ob else ""
    body = (f"Hi {_first(inv.get('name'))},\n\n"
            f"{manager.name if manager else 'Your manager'} has set up your onboarding for {project.name}{when}.\n\n"
            + (f"Sign in with this email address ({inv['email']}) to get started:\n\n{link}\n\n" if link else
               f"Open Welcome Wave and sign in with this email address ({inv['email']}) to get started.\n\n")
            + (f"The invitation is open until {_day(inv['expires_at'])}.\n" if inv.get("expires_at") else "") + _signature())
    c.deps.mailer.send(inv["email"], f"You have been added to the onboarding for {project.name}", body)
    return {"sent": "invitation"}


# ---------------------------------------------------------------- the pre-boarding link (in memory, after the response)
def send_preboarding(deps, settings, to: str, name: str | None, project: str, url: str, expires_at) -> bool:
    """Email a manager-requested pre-boarding link. Swallows its failures (it runs after the response); returns whether it sent."""
    if not enabled(deps) or not to:
        return False
    body = (f"Hi {_first(name)},\n\n"
            f"Before your first day at {project} you can read and acknowledge a few documents, see which access is being arranged "
            f"for you and check your development environment. No sign-in is needed: this link is yours.\n\n{url}\n\n"
            f"It works until {_day(expires_at)}. Anyone with the link can open the page, so keep it to yourself.\n" + _signature())
    return _try("preboarding", lambda: deps.mailer.send(to, f"Your pre-boarding page for {project}", body))


# ---------------------------------------------------------------- the daily tick
def expert_digest(ctx, person, count: int, project_id: str) -> bool:
    """The count of questions waiting in the expert's digest, and where to open them. Never the questions."""
    deps = ctx.deps
    if not enabled(deps) or not person.email:
        return False
    link = f"{_base(ctx.settings)}/#/experts?project={project_id}" if _base(ctx.settings) else ""
    body = (f"Hi {_first(person.name)},\n\n{count} question{'s' if count != 1 else ''} from new hires "
            f"{'wait' if count != 1 else 'waits'} for you in today's digest.\n\n" + (f"Open your inbox: {link}\n" if link else
                                                                                       "Open Welcome Wave and go to your inbox.\n") + _signature())
    return _try("digest", lambda: deps.mailer.send(person.email, f"{count} question{'s' if count != 1 else ''} wait for you", body))


def weekly_summary(deps, settings, hc, summary) -> int:
    """The week's summary to the hire and to the manager (two messages; the lines are what both can read in the app).
    Returns how many were sent."""
    if not enabled(deps):
        return 0
    body_data = summary.body or {}
    lines = [l.get("text") for l in body_data.get("lines", []) if l.get("text")][:12]
    overview = body_data.get("overview") or ""
    base = _base(settings)
    link = f"{base}/#/program/{hc.ob.id}?project={hc.ob.project_id}" if base else ""
    hire, manager = deps.store.get_user(hc.ob.new_hire_id), deps.store.get_user(hc.ob.manager_id)
    week = f"the week of {_day(summary.week_start)}"
    sent = 0
    for person, you in ((hire, True), (manager, False)):
        if not person or not person.email or (not you and manager and hire and manager.id == hire.id):
            continue
        who = "your" if you else f"{_first(hire.name if hire else '')}'s"
        text = (f"Hi {_first(person.name)},\n\nHere is {who} summary for {week}.\n\n"
                + (overview + "\n\n" if overview else "") + "".join(f"- {line}\n" for line in lines)
                + (f"\nOpen the program: {link}\n" if link else "") + _signature())
        subject = f"Your week: {_day(summary.week_start)}" if you else f"{_first(hire.name if hire else '')}'s week: {_day(summary.week_start)}"
        sent += _try("weekly_summary", lambda p=person, s=subject, t=text: deps.mailer.send(p.email, s, t))
    return sent


# ---------------------------------------------------------------- the events
def handle(c, event: dict) -> dict:
    """Mail events (`mail.*`), run by the outbox's workers like any event. Mail is not an agent's work: no Supervisor, no run."""
    if event.get("type") == "mail.invitation":
        return invitation(c, event)
    return {"skipped": "unknown mail event"}
