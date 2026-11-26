"""Pub/Sub worker for GKE Autopilot. One image, three deployments:

    WORKER_QUEUE=ingestion  -> repo + document indexing
    WORKER_QUEUE=meetings   -> KT recording analysis (long; Gemini video)
    WORKER_QUEUE=reviews    -> PR status, reviews, onboarding plans

Delivery is at-least-once. Each event's dedupe key is claimed with a lease that a heartbeat keeps alive while the
handler runs, and marked done only on success, so:
  * a duplicate of a finished event is acked and skipped;
  * a duplicate of an event another pod is working on is nacked and retried later;
  * an event whose pod died mid-run is continued from its last checkpoint once the lease lapses - nothing is lost;
  * an event for a repository another event is indexing is handed back for later, so it does not sit on a slot.
Failures are nacked so Pub/Sub retries with backoff; after 5 attempts the message lands in the dead-letter topic.
KEDA scales each deployment on its subscription backlog, down to zero when idle.
"""
from __future__ import annotations

import json
import logging
import os
import signal
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from app.secrets import load_from_secret_manager

log = logging.getLogger("worker")
MAX_DELIVERY_ATTEMPTS = 5  # infra/terraform/events.tf dead_letter_policy (ingestion and reviews)
REPO_BUSY_RETRY_S = 300  # an event for a repository that is being indexed comes back after this long


class _Health(BaseHTTPRequestHandler):
    """GET /healthz for the kubelet: 200 while the subscriber stream is alive."""

    alive = threading.Event()

    def do_GET(self):  # noqa: N802 - http.server API
        ok = self.path == "/healthz" and self.alive.is_set()
        self.send_response(200 if ok else 503)
        self.end_headers()
        self.wfile.write(b"ok" if ok else b"down")

    def log_message(self, *_):
        pass


def _handle_waiting_out_busy(container, event, lease_s, fields, wait_for_repo=False):
    """Another handler holds this event's lease. Nacking would spend one of the few delivery attempts before the
    dead-letter topic - and if that handler died, its lease only lapses after `lease_s`. So hold the message (the
    client keeps extending its ack deadline) and try again until the event is done or the lease is ours.

    A busy repository is waited out the same way only when `wait_for_repo` (the last delivery attempt); otherwise
    RepoBusy is raised and the message goes back to Pub/Sub."""
    import time

    from app.container import EventBusy
    from app.ports import RepoBusy

    deadline = time.monotonic() + lease_s * 2 + 30
    while True:
        try:
            return container.handle(event)
        except EventBusy:
            if time.monotonic() > deadline:
                raise
            log.info("event busy elsewhere; waiting for its lease", extra={"extra_fields": fields})
            time.sleep(min(15.0, max(1.0, lease_s / 10)))
        except RepoBusy:
            if not wait_for_repo:
                raise
            log.info("repository is being indexed; waiting (last delivery attempt)", extra={"extra_fields": fields})
            time.sleep(15.0)


def _handle_message(container, settings, message) -> None:
    """One Pub/Sub delivery: ack it once handled (or never handleable), nack it to be retried, or hand it back for later
    while its repository is being indexed."""
    from app.adapters.postgres import current_org
    from app.container import EventBusy
    from app.ports import RepoBusy

    try:
        event = json.loads(message.data)
    except ValueError:
        log.error("unparseable message dropped", extra={"extra_fields": {"message_id": message.message_id}})
        message.ack()
        return
    fields = {"type": event.get("type"), "dedupe": event.get("dedupe"), "attempt": message.delivery_attempt}
    current_org.set(None)
    try:
        org = container.store.org_of_project(event.get("project_id") or "") if event.get("project_id") else None
    except Exception:  # database unreachable: let Pub/Sub retry; malformed ids fall through to "unknown"
        log.exception("could not resolve tenant", extra={"extra_fields": fields})
        message.nack()
        return
    if not org:
        log.warning("event for unknown project dropped", extra={"extra_fields": fields})
        message.ack()
        return
    current_org.set(org)
    try:
        last = (message.delivery_attempt or 0) >= MAX_DELIVERY_ATTEMPTS
        out = _handle_waiting_out_busy(container, event, settings.event_lease_s, fields, wait_for_repo=last)
    except EventBusy:
        log.info("event still busy elsewhere; retry later", extra={"extra_fields": fields})
        message.nack()
    except RepoBusy:
        # Another event is indexing this repository, maybe for many minutes. Give the slot back for other tenants'
        # events and have Pub/Sub deliver this one again later (a nack would come back within seconds).
        log.info("repository is being indexed; retry later", extra={"extra_fields": fields})
        message.modify_ack_deadline(REPO_BUSY_RETRY_S)
        message.drop()
    except Exception:
        log.exception("event failed", extra={"extra_fields": fields})
        message.nack()
    else:
        log.info("event handled", extra={"extra_fields": {**fields, "result": str(out.get("result") or out.get("skipped"))[:300]}})
        message.ack()
    finally:
        current_org.set(None)


def main():
    load_from_secret_manager()  # before settings are read

    from app.config import get_settings
    from app.container import build_container
    from app.main import setup_logging

    setup_logging()
    settings = get_settings()
    queue = os.environ["WORKER_QUEUE"]
    container = build_container(settings, serves_http=False)

    from google.cloud import pubsub_v1

    subscriber = pubsub_v1.SubscriberClient()
    sub = subscriber.subscription_path(settings.gcp_project, f"{settings.pubsub_prefix}-{queue}-sub")

    # A meeting is slow: one per pod, and its lease is extended for up to 2 hours while Gemini watches the video.
    heavy = queue == "meetings"
    flow = pubsub_v1.types.FlowControl(max_messages=1 if heavy else 4, max_lease_duration=7200 if heavy else 1800)
    future = subscriber.subscribe(sub, callback=lambda m: _handle_message(container, settings, m), flow_control=flow, await_callbacks_on_shutdown=True)
    _Health.alive.set()
    server = HTTPServer(("0.0.0.0", int(os.environ.get("HEALTH_PORT", "8081"))), _Health)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    log.info("listening", extra={"extra_fields": {"subscription": sub}})

    def watch():
        try:
            future.result()  # returns or raises only if the streaming pull dies
        except Exception:
            log.exception("subscriber stopped")
        _Health.alive.clear()
        stop.set()

    threading.Thread(target=watch, daemon=True).start()
    stop.wait()
    log.info("shutting down: finishing in-flight messages")
    future.cancel()  # stop pulling; with await_callbacks_on_shutdown, result() waits for running handlers
    try:
        future.result(timeout=float(os.environ.get("SHUTDOWN_GRACE_S", "570")))
    except Exception:  # noqa: BLE001 - cancelled futures raise; we only care that handlers had time to finish
        pass
    server.shutdown()
    pool = container.extras.get("pool")
    if pool:
        pool.close()


if __name__ == "__main__":
    main()
