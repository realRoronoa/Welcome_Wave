"""Live updates. A change to anything the web app shows reaches the signed-in browsers of its organisation within a moment,
so pages stay current without reloading: a new notification, an approval decided by another manager, a task moved by the
Review agent, a call that went live, a repository that finished indexing.

What travels is the fact of a change, never data: {"t": topic, "p": project id or null}. The browser then reloads that
topic through the normal API, which applies every permission check as usual.

Sources:
  * Postgres (self-hosted and Google Cloud): triggers announce row changes on the channel `ww_live` (db/migrations/009_live.sql),
    whichever process made them (the API, a worker, a webhook). `PgListener` relays them to this API's browsers.
  * the in-memory store (demo): `LiveStore` announces after each store call that changes something.

GET /api/live streams them (Server-Sent Events) to one signed-in browser.

Agent runs are the one exception that carries data: each step of a run (app/agents/core/runtime.py Step, already shaped for its
reader) is published on topic `agent:<run_id>` with the run's user as recipient, and only that user's streams that asked for
the topic (`GET /api/live?topics=agent:<run_id>`) receive it, as `event: step`. Steps are published in this process (the
API runs the agent while the browser waits), so they never pass through Postgres.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import threading
from dataclasses import dataclass, field

log = logging.getLogger("live")
QUEUE_MAX = 200  # a browser this far behind gets one "reload everything" instead


AGENT_TOPIC = re.compile(r"agent:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


@dataclass(eq=False)
class Subscriber:
    org_id: str
    user_id: str
    queue: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(QUEUE_MAX))
    topics: frozenset[str] | None = None  # only these topics (and "*"); None: every change topic, no agent steps


class LiveHub:
    """Fans changes out to the browsers connected to this process. `publish` may be called from any thread."""

    def __init__(self):
        self.loop: asyncio.AbstractEventLoop | None = None
        self.subscribers: set[Subscriber] = set()

    def bind(self, loop: asyncio.AbstractEventLoop) -> None:
        self.loop = loop

    def subscribe(self, org_id: str, user_id: str, topics=None) -> Subscriber:
        s = Subscriber(str(org_id), str(user_id), topics=frozenset(topics) if topics else None)
        self.subscribers.add(s)
        return s

    def unsubscribe(self, s: Subscriber) -> None:
        self.subscribers.discard(s)

    def publish(self, change: dict) -> None:
        if self.loop is None or self.loop.is_closed():
            return
        try:
            self.loop.call_soon_threadsafe(self._dispatch, change)
        except RuntimeError:  # the loop is shutting down
            pass

    def _dispatch(self, change: dict) -> None:
        org, topic, user = change.get("o"), change.get("t"), change.get("u")
        if isinstance(topic, str) and topic.startswith("agent:"):  # a run's step: its own user, on a stream that asked for it
            out = {"t": topic, "p": change.get("p"), "step": change.get("step")}
            for s in list(self.subscribers):
                if user and user == s.user_id and (not org or org == s.org_id) and s.topics and topic in s.topics:
                    _offer(s.queue, out)
            return
        out = {"t": topic, "p": change.get("p")}
        for s in list(self.subscribers):
            if org and org != s.org_id:
                continue
            if topic == "notifications" and user and user != s.user_id:
                continue
            if s.topics is not None and topic != "*" and topic not in s.topics:
                continue
            _offer(s.queue, out)


def _offer(q: asyncio.Queue, item: dict) -> None:
    if q.full():  # far behind: replace the backlog with one "reload everything"
        while not q.empty():
            q.get_nowait()
        q.put_nowait({"t": "*", "p": None})
        return
    q.put_nowait(item)


class PgListener:
    """LISTEN ww_live on its own connection, in a thread; relays each announcement to the hub. Reconnects after a failure,
    and then tells every browser to reload everything (what changed while it was away is unknown)."""

    def __init__(self, url: str, hub: LiveHub):
        self.url = url.replace("postgresql+psycopg://", "postgresql://")
        self.hub = hub
        self.stopping = threading.Event()
        self.thread = threading.Thread(target=self._run, name="ww-live", daemon=True)

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self.stopping.set()

    def _run(self) -> None:
        import psycopg

        wait, first = 1.0, True
        while not self.stopping.is_set():
            try:
                with psycopg.connect(self.url, autocommit=True, connect_timeout=10) as conn:
                    conn.execute("LISTEN ww_live")
                    if not first:
                        self.hub.publish({"t": "*"})
                    first, wait = False, 1.0
                    while not self.stopping.is_set():
                        for n in conn.notifies(timeout=5.0):
                            try:
                                self.hub.publish(json.loads(n.payload))
                            except ValueError:
                                log.warning("bad live payload dropped")
            except Exception as e:  # noqa: BLE001 - database restarted or unreachable: retry with backoff
                if self.stopping.is_set():
                    return
                log.warning("live listener reconnecting: %s", e)
                self.stopping.wait(wait)
                wait = min(30.0, wait * 2)


# ---- demo mode: the in-memory store announces its own changes ----
_WRITES = re.compile(r"^(create|update|decide|claim|release|notify|close|cancel|end|save|set|add|mark|accept|delete|remove|"
                     r"supersede|answer|record|retire|leave|bind|unbind|upsert|link|replace|reset|start|finish|book|schedule|"
                     r"expire|discard)")
_TOPICS = [
    ("notif", "notifications"), ("approval", "approvals"), ("review", "tasks"), ("task", "tasks"), ("onboarding", "tasks"),
    ("plan", "tasks"), ("proposal", "tasks"), ("kt_link", "knowledge"), ("link", "knowledge"), ("meeting", "meetings"), ("note", "meetings"),
    ("recording", "meetings"), ("repo", "sources"), ("document", "sources"), ("codeowners", "sources"), ("expert", "inbox"),
    ("request", "inbox"), ("answer", "inbox"), ("invitation", "people"), ("member", "people"), ("organization", "people"),
    ("project", "projects"), ("run", "runs"),
]


def topic_of(method: str) -> str | None:
    if not _WRITES.match(method):
        return None
    return next((t for key, t in _TOPICS if key in method), None)


class LiveStore:
    """Wraps the in-memory store: every call that changes something announces its topic (to the one demo organisation)."""

    def __init__(self, store, hub: LiveHub):
        object.__setattr__(self, "_store", store)
        object.__setattr__(self, "_hub", hub)

    def __getattr__(self, name):
        attr = getattr(self._store, name)
        topic = topic_of(name) if callable(attr) else None
        if topic is None:
            return attr
        hub = self._hub

        def call(*args, **kwargs):
            out = attr(*args, **kwargs)
            hub.publish({"t": topic, "u": str(args[0]) if topic == "notifications" and name == "notify" and args else None})
            return out

        return call

    def __setattr__(self, name, value):
        setattr(self._store, name, value)
