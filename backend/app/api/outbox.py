"""Reliable publishing. Every event the API emits is written to the outbox table first, then sent; a send that fails
(Pub/Sub down, a timeout) leaves the row due, and Cloud Scheduler's call to POST /internal/outbox/flush sends it later.
A row can be sent twice (a timeout that actually delivered, two flushers): consumers dedupe on the event's key.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import JSONResponse

from app.api.auth import container

router = APIRouter(prefix="/internal")
log = logging.getLogger("outbox")


def publish(c, topic: str, event: dict) -> None:
    """Record the event, then try to send it now. Raises only if the event could not be recorded."""
    _send(c, c.store.outbox_add(topic, event), topic, event)


def flush(c, limit: int) -> tuple[int, bool]:
    """Send up to `limit` due rows; returns how many were sent and whether a send failed. Stops at the first failure:
    the broker is likely down, and the rest stay due."""
    sent = 0
    for row in c.store.outbox_due(limit):
        if not _send(c, row["id"], row["topic"], row["event"]):
            return sent, True
        sent += 1
    return sent, False


def _send(c, row_id: str, topic: str, event: dict) -> bool:
    """One attempt. Never raises: until outbox_sent lands the row stays due, so a failure only delays the event."""
    if getattr(c.deps.bus, "owns_outbox", False):  # self-hosted: the local queue marks the row sent once its handler succeeds
        c.deps.bus.submit(row_id, topic, event)
        return True
    try:
        c.deps.bus.publish(topic, event)
        c.store.outbox_sent(row_id)
        return True
    except Exception as e:  # noqa: BLE001
        log.warning("event not sent; the outbox will retry", extra={"extra_fields": {"outbox_id": row_id, "type": event.get("type")}})
        try:
            c.store.outbox_failed(row_id, f"{type(e).__name__}: {e}"[:500])
        except Exception:  # noqa: BLE001 - the row is due either way
            log.warning("could not record the failed send", exc_info=True)
        return False


def _scheduler_only(authorization: str | None, c) -> None:
    """Cloud Scheduler's calls carry a Google-signed OIDC token for its own service account. Off without that setup."""
    s = c.settings
    if s.is_demo or not s.outbox_flush_audience:
        raise HTTPException(404, "Not Found")
    from google.auth.transport.requests import Request
    from google.oauth2 import id_token

    try:
        claims = id_token.verify_oauth2_token((authorization or "").removeprefix("Bearer "), Request(), audience=s.outbox_flush_audience)
    except Exception as e:  # missing, expired, wrong audience, not signed by Google
        raise HTTPException(401, "Invalid token") from e
    if not claims.get("email_verified") or claims.get("email") != s.outbox_flush_invoker:
        raise HTTPException(403, "Forbidden")


@router.post("/outbox/flush")
def flush_outbox(authorization: str | None = Header(default=None), c=Depends(container)):
    """Called by Cloud Scheduler with a Google-signed OIDC token for its own service account. Off without that setup."""
    _scheduler_only(authorization, c)
    sent, failed = flush(c, 100)
    if failed:  # fails the Scheduler run, so the job's status shows that events are waiting (the next run retries them)
        return JSONResponse({"sent": sent, "detail": "An event could not be published; it stays in the outbox."}, status_code=503)
    return {"sent": sent}


@router.post("/recordings/poll")
def poll_recordings(authorization: str | None = Header(default=None), c=Depends(container)):
    """Google Cloud's recording poller (self-hosted runs it in-process, app/selfhost.py): ended Google Calendar sessions get
    their Drive recording imported, and sessions whose recording never came are marked ended (docs/specs/scheduling.md).
    Protected exactly like the outbox flush."""
    _scheduler_only(authorization, c)
    from app.services.scheduling import poll_recordings as poll

    return poll(c)
