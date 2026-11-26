"""Inbound webhooks: GitHub (push, pull_request) and Calendly (a KT session booked or cancelled).

Both arrive before we know the tenant, so they resolve it through a SECURITY DEFINER lookup, set it, and only then
touch row-level-security-protected tables. Every event they publish carries a dedupe key; the handler is idempotent.
"""
from __future__ import annotations

import json
import logging
from urllib.parse import quote

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from app.adapters.github import events_from_webhook, verify_signature
from app.api.auth import _set_tenant, container
from app.api.outbox import flush, publish

router = APIRouter(prefix="/webhooks")
log = logging.getLogger("webhooks")


@router.post("/github", status_code=202)
async def github(request: Request, x_hub_signature_256: str | None = Header(default=None),
                 x_github_event: str = Header(default=""), c=Depends(container)):
    """GitHub App deliveries, signed with GITHUB_WEBHOOK_SECRET. Not served (404) while that secret is the public default or
    unset, or on a self-hosted server without a GitHub App: its repositories are connected by URL and polled instead."""
    if not c.settings.github_webhooks_ready:
        raise HTTPException(404, "Not Found")
    body = await request.body()
    if not verify_signature(c.settings.github_webhook_secret, body, x_hub_signature_256):
        raise HTTPException(401, "Bad signature")
    try:
        payload = json.loads(body)
    except ValueError:
        raise HTTPException(400, "Body is not JSON") from None
    if not isinstance(payload, dict):
        raise HTTPException(400, "Body is not a JSON object")
    out = await run_in_threadpool(_github_event, c, x_github_event, payload)
    if not c.settings.is_demo:
        try:  # piggyback: send a few rows an earlier failed send left behind, between the scheduler's runs
            await run_in_threadpool(flush, c, 3)
        except Exception:  # noqa: BLE001 - this delivery is recorded already; the scheduler catches up
            log.warning("outbox flush failed", exc_info=True)
    return out


def _github_event(c, kind: str, payload: dict) -> dict:
    """Blocking part of the GitHub webhook (database, Pub/Sub). Runs in the threadpool."""
    inst = payload.get("installation") or {}
    if kind == "installation":
        # Who installed the app, signed by GitHub. Binding an installation to an organisation requires being this person.
        if payload.get("action") == "created" and (payload.get("sender") or {}).get("id") and inst.get("id"):
            c.store.record_installation_sender(str(inst["id"]), payload["sender"]["id"], payload["sender"].get("login", ""),
                                               (inst.get("account") or {}).get("login", ""))
            return {"installation": "recorded"}
        if payload.get("action") == "deleted" and inst.get("id"):
            c.store.unbind_installation(str(inst["id"]))
            return {"installation": "removed"}
        return {"ignored": payload.get("action")}

    full_name = (payload.get("repository") or {}).get("full_name", "")
    sender_installation = str(inst.get("id") or "")
    published = 0
    for repo in c.store.repos_by_full_name(full_name) if full_name else []:
        # A repository connected by URL (no installation) is polled, never driven by webhooks: a delivery that names no
        # installation would otherwise match it ('' == '').
        if not c.settings.is_demo and (not repo.get("installation_id") or str(repo["installation_id"]) != sender_installation):
            log.warning("webhook installation mismatch", extra={"extra_fields": {"repo": full_name}})
            continue
        _set_tenant(str(repo["org_id"]))
        for ev in events_from_webhook(kind, payload, str(repo["project_id"]), str(repo["id"])):
            ev["dedupe"] = f"{ev['dedupe']}:{repo['id']}"  # one repo connected to two projects = two events
            if "pr_number" in ev:  # the link people click is built here, never taken from the payload
                ev["pr_url"] = pr_url(full_name, ev["pr_number"])
            if c.settings.is_demo:
                c.handle(ev)
            else:
                publish(c, ev["type"], ev)  # recorded before we answer 202: GitHub does not redeliver a failed delivery
            published += 1
    return {"events": published}


def pr_url(full_name: str, number: int) -> str:
    """The pull request's page on GitHub, from the repository's name and the number (an int, checked by events_from_webhook)."""
    return f"https://github.com/{quote(full_name, safe='/')}/pull/{int(number)}"


@router.post("/calendly", status_code=200)
async def calendly(request: Request, calendly_webhook_signature: str | None = Header(default=None), c=Depends(container)):
    """Calendly's webhook subscription (invitee.created, invitee.canceled), signed with CALENDLY_WEBHOOK_SIGNING_KEY. A
    booking made from a Welcome Wave link becomes a KT session in its project; its cancellation cancels it. Anything else
    is acknowledged and ignored (Calendly retries what is not answered with a 2xx)."""
    from app.adapters.calendly import parse_webhook, verify_signature
    from app.services.scheduling import handle_calendly

    if not c.settings.calendly_webhook_signing_key:
        raise HTTPException(404, "Not Found")
    body = await request.body()
    if not verify_signature(c.settings.calendly_webhook_signing_key, calendly_webhook_signature, body):
        raise HTTPException(401, "Bad Calendly signature")
    try:
        payload = json.loads(body)
    except ValueError:
        raise HTTPException(400, "Body is not JSON") from None
    booking = parse_webhook(payload) if isinstance(payload, dict) else None
    if booking is None:
        return {"ignored": payload.get("event") if isinstance(payload, dict) else None}
    try:
        return await run_in_threadpool(handle_calendly, c, booking)
    finally:
        _set_tenant(None)
