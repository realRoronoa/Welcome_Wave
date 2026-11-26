"""Request rate limits: an in-memory token bucket per caller and route group, in front of every route.

Who is the caller. A request is charged to the signed-in person once the API has verified who they are; before that (and
for routes nobody signs in to) it is charged to the client's address. Verification happens later, in the route's
dependencies, so the limiter learns it from the response: after a request whose credential (the Authorization or
X-Demo-User header) was verified, that credential's fingerprint maps to the person's id. A credential the API never
accepted (a made-up token) never gets a bucket of its own, so it cannot be used to escape the per-address limit.

Groups (first match wins). Each has its own limit per minute, set by an environment variable:

| Group       | Routes                                                      | Charged to       | Default | Variable                         |
|-------------|-------------------------------------------------------------|------------------|---------|----------------------------------|
| auth        | POST sign-in, sign-up and joining an organisation           | client address   | 10      | RATE_LIMIT_AUTH_PER_MINUTE        |
| chat        | POST /api/chat, /api/ask/..., a task's presubmit-review     | person / address | 20      | RATE_LIMIT_CHAT_PER_MINUTE        |
| preboarding | /api/preboarding/* (a link token instead of a sign-in)      | client address   | 30      | RATE_LIMIT_PREBOARDING_PER_MINUTE |
| webhooks    | /webhooks/* (GitHub, calendar providers)                    | client address   | 60      | RATE_LIMIT_WEBHOOKS_PER_MINUTE    |
| default     | everything else                                             | person / address | 120     | RATE_LIMIT_DEFAULT_PER_MINUTE     |

A bucket holds a minute's worth of requests and refills continuously, so a burst up to the limit is allowed. Over the limit
the API answers 429 with Retry-After (whole seconds). /health and /health/ready are never limited, nor are CORS preflights.

RATE_LIMIT_ENABLED=false turns it off (the tests do). RATE_LIMIT_TRUSTED_HOPS: how many proxies in front of the API append
to X-Forwarded-For; the client address is the entry that many places from the right. 0 (the default) uses the address the
server resolved (uvicorn --proxy-headers).

Limits are per process: with several API instances (Cloud Run scales out), each enforces its own. The chat route keeps its
own per-person limit too (CHAT_RATE_PER_MINUTE, app/api/routes.py).
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import re
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Callable

from pydantic_settings import BaseSettings, SettingsConfigDict

from app.config import dotenv_unless_demo
from app.logsafe import redact

log = logging.getLogger("ratelimit")


class RateLimitSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="RATE_LIMIT_", env_file=".env", extra="ignore")

    @classmethod
    def settings_customise_sources(cls, settings_cls, init_settings, env_settings, dotenv_settings, file_secret_settings):
        return init_settings, env_settings, dotenv_unless_demo(dotenv_settings), file_secret_settings  # demo reads no .env (config.py)

    enabled: bool = True
    default_per_minute: int = 120
    chat_per_minute: int = 20
    auth_per_minute: int = 10
    preboarding_per_minute: int = 30
    webhooks_per_minute: int = 60
    trusted_hops: int = 0
    max_keys: int = 100_000  # buckets kept; the least recently used go first (a full bucket loses nothing by going)


@dataclass(frozen=True)
class Group:
    name: str
    methods: frozenset[str] | None  # None = every method
    path: re.Pattern
    by_person: bool  # False: always the client address


GROUPS = (
    Group("auth", frozenset({"POST"}), re.compile(r"^/api/(demo/login|signup|invitations/accept|demo/invitations/accept)/?$"), False),
    Group("chat", frozenset({"POST"}), re.compile(r"^/api/(chat|ask(/[^/]+)?|tasks/[^/]+/presubmit-review)/?$"), True),  # each calls the model
    Group("preboarding", None, re.compile(r"^/api/preboarding(/|$)"), False),
    Group("webhooks", None, re.compile(r"^/webhooks/"), False),
)
DEFAULT = Group("default", None, re.compile(""), True)
EXEMPT = frozenset({"/health", "/health/ready"})


def group_for(method: str, path: str) -> Group:
    for g in GROUPS:
        if (g.methods is None or method in g.methods) and g.path.match(path):
            return g
    return DEFAULT


class TokenBuckets:
    """One bucket per key: `per_minute` tokens at most, refilled at per_minute/60 a second. Thread-safe."""

    def __init__(self, max_keys: int = 100_000, clock: Callable[[], float] = time.monotonic):
        self.max_keys = max_keys
        self.clock = clock
        self._buckets: OrderedDict[str, tuple[float, float]] = OrderedDict()  # key -> (tokens, at)
        self._lock = threading.Lock()

    def take(self, key: str, per_minute: int) -> float:
        """Spend one token. Returns 0 if allowed, else the seconds until a token is back."""
        if per_minute <= 0:
            return 60.0
        rate = per_minute / 60.0
        with self._lock:
            now = self.clock()
            tokens, at = self._buckets.pop(key, (float(per_minute), now))
            tokens = min(float(per_minute), tokens + (now - at) * rate)
            if tokens >= 1:
                self._buckets[key] = (tokens - 1, now)
                wait = 0.0
            else:
                self._buckets[key] = (tokens, now)
                wait = (1 - tokens) / rate
            while len(self._buckets) > self.max_keys:
                self._buckets.popitem(last=False)
            return wait


def _header(scope, name: bytes) -> str | None:
    for k, v in scope.get("headers") or ():
        if k == name:
            return v.decode("latin-1")
    return None


def client_address(scope, trusted_hops: int) -> str:
    if trusted_hops > 0:
        forwarded = [p.strip() for p in (_header(scope, b"x-forwarded-for") or "").split(",") if p.strip()]
        if len(forwarded) >= trusted_hops:
            return forwarded[-trusted_hops]
    client = scope.get("client")
    return client[0] if client else "unknown"


def _fingerprint(scope) -> str | None:
    cred = _header(scope, b"authorization") or _header(scope, b"x-demo-user")
    return hashlib.sha256(cred.encode("utf-8", "replace")).hexdigest()[:32] if cred else None


class RateLimitMiddleware:
    """Pure ASGI, so streamed responses (GET /api/live) pass through untouched. Register it inside CORSMiddleware, so a 429
    still carries the CORS headers a browser needs to read it."""

    def __init__(self, app, settings: RateLimitSettings | None = None, clock: Callable[[], float] = time.monotonic):
        self.app = app
        self.settings = settings or RateLimitSettings()
        self.buckets = TokenBuckets(self.settings.max_keys, clock)
        self._people: OrderedDict[str, str] = OrderedDict()  # credential fingerprint -> verified user id
        self._people_lock = threading.Lock()

    def limit_for(self, group: Group) -> int:
        return getattr(self.settings, f"{group.name}_per_minute")

    def _person(self, fingerprint: str | None) -> str | None:
        if not fingerprint:
            return None
        with self._people_lock:
            uid = self._people.get(fingerprint)
            if uid is not None:
                self._people.move_to_end(fingerprint)
            return uid

    def _learn(self, fingerprint: str, user_id: str) -> None:
        with self._people_lock:
            self._people[fingerprint] = user_id
            self._people.move_to_end(fingerprint)
            while len(self._people) > self.settings.max_keys:
                self._people.popitem(last=False)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not self.settings.enabled or scope["path"] in EXEMPT or scope["method"] == "OPTIONS":
            return await self.app(scope, receive, send)
        group = group_for(scope["method"], scope["path"])
        fingerprint = _fingerprint(scope)
        person = self._person(fingerprint) if group.by_person else None
        key = f"{group.name}:u:{person}" if person else f"{group.name}:ip:{client_address(scope, self.settings.trusted_hops)}"
        wait = self.buckets.take(key, self.limit_for(group))
        if wait > 0:
            seconds = max(1, math.ceil(wait))
            log.warning("rate limited", extra={"extra_fields": {"group": group.name, "path": redact(scope["path"]), "by": "person" if person else "address"}})
            body = json.dumps({"detail": f"Too many requests. Try again in {seconds} s.", "retry_after": seconds}).encode()
            await send({"type": "http.response.start", "status": 429, "headers": [
                (b"content-type", b"application/json"), (b"content-length", str(len(body)).encode()),
                (b"retry-after", str(seconds).encode())]})
            await send({"type": "http.response.body", "body": body})
            return

        state = scope.setdefault("state", {})  # shared with the route's request.state, where sign-in puts the user

        async def send_and_learn(message):
            if message["type"] == "http.response.start" and fingerprint:
                user = state.get("user")
                if user is not None and getattr(user, "id", None):
                    self._learn(fingerprint, str(user.id))
            await send(message)

        await self.app(scope, receive, send_and_learn)
