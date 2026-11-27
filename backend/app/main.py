"""FastAPI entry point (Cloud Run service `ww-api`, or the whole server in self-hosted mode)."""
from __future__ import annotations

import asyncio
import json
import os
import logging
import re
import time
import uuid
from contextlib import asynccontextmanager
from contextvars import ContextVar

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api import agents, audit, client_errors, code_tour, knowledge_risk, kt_planner, lifecycle, members, outbox, routes, stream, uploads, voice, webhooks
from app.api import board
from app.api import legal
from app.config import get_settings
from app.container import EventBusy, build_container
from app.logsafe import AccessLogFilter, redact
from app.ratelimit import BodyLimitMiddleware, RateLimitMiddleware

request_id: ContextVar[str | None] = ContextVar("request_id", default=None)
trace_context: ContextVar[tuple[str, str | None] | None] = ContextVar("trace_context", default=None)  # (trace id, span id)

_CLOUD_TRACE = re.compile(r"^([0-9a-fA-F]{32})(?:/([0-9]{1,20}))?")  # X-Cloud-Trace-Context: TRACE_ID/SPAN_ID;o=1
_TRACEPARENT = re.compile(r"^[0-9a-f]{2}-([0-9a-f]{32})-([0-9a-f]{16})-[0-9a-f]{2}$")  # W3C: 00-TRACE_ID-SPAN_ID-01


def trace_of(headers) -> tuple[str, str | None] | None:
    """The request's trace (id, span id) from the headers Cloud Run passes on: X-Cloud-Trace-Context, else traceparent."""
    m = _CLOUD_TRACE.match((headers.get("x-cloud-trace-context") or "").strip())
    if m:
        span = int(m.group(2)) if m.group(2) else 0
        return m.group(1).lower(), (f"{span:016x}" if 0 < span < 2**64 else None)
    m = _TRACEPARENT.match((headers.get("traceparent") or "").strip().lower())
    return (m.group(1), m.group(2)) if m else None


class JsonFormatter(logging.Formatter):
    """One JSON object per line: Cloud Logging parses severity and fields, and groups a request's lines under its trace
    (logging.googleapis.com/trace, when the project is known), next to Cloud Run's own request log entry."""

    def __init__(self, project: str = ""):
        super().__init__()
        self.project = project

    def format(self, record):
        out = {"severity": record.levelname, "message": record.getMessage(), "logger": record.name}
        rid = request_id.get()
        if rid:
            out["request_id"] = rid
        trace = trace_context.get()
        if trace and self.project:
            out["logging.googleapis.com/trace"] = f"projects/{self.project}/traces/{trace[0]}"
            if trace[1]:
                out["logging.googleapis.com/spanId"] = trace[1]
        out.update(getattr(record, "extra_fields", {}))
        if record.exc_info:
            out["exception"] = self.formatException(record.exc_info)
        return json.dumps(out, default=str)


def setup_logging():
    h = logging.StreamHandler()
    h.setFormatter(JsonFormatter(os.environ.get("GCP_PROJECT") or os.environ.get("GOOGLE_CLOUD_PROJECT") or ""))
    logging.basicConfig(level=logging.INFO, handlers=[h], force=True)
    for noisy in ("httpx", "google", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    for name in ("uvicorn", "uvicorn.error"):  # uvicorn's own lines (start-up, errors) as JSON too, not plain text beside it
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.propagate = True
    # One line per request: the app's own "request" line (path redacted, person, latency, trace). uvicorn's access line
    # repeated it in plain text, with the client's address.
    access = logging.getLogger("uvicorn.access")
    access.handlers.clear()
    access.propagate = False
    if not any(isinstance(f, AccessLogFilter) for f in access.filters):
        access.addFilter(AccessLogFilter())  # should a handler be added back: request lines name pre-boarding and signed links


log = logging.getLogger("api")

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
    "Cache-Control": "no-store",
}
# The API serves JSON (and files the web app fetches): nothing in a response may load, run or frame anything. The web app's
# own policy is in frontend/nginx.conf.template. Demo mode's API docs page (/api/docs) loads Swagger UI, so it is left out.
API_CSP = "default-src 'none'; frame-ancestors 'none'"


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    settings = get_settings()
    if not settings.is_demo:
        import firebase_admin

        if settings.firebase_auth_emulator_host and settings.env != "prod":  # development: the local Firebase Auth emulator
            os.environ.setdefault("FIREBASE_AUTH_EMULATOR_HOST", settings.firebase_auth_emulator_host)
        if not firebase_admin._apps:
            firebase_admin.initialize_app(options={"projectId": settings.firebase_project})
    from app.live import LiveHub, PgListener

    # Blocking request work (sign-in, database, model calls) runs on anyio's thread pool, 40 threads by default: a few long
    # Guide answers or imports would leave every other request, sign-in included, waiting for a thread. The Guide also has
    # its own cap (CHAT_CONCURRENCY, app/api/routes.py guide_slot).
    import anyio.to_thread

    anyio.to_thread.current_default_thread_limiter().total_tokens = max(40, settings.api_threads)
    hub = LiveHub()
    hub.bind(asyncio.get_running_loop())
    app.state.live = hub
    container = build_container(settings, live=hub)
    app.state.container = container
    background = []
    if not settings.is_demo:  # Postgres announces every change, whichever process made it
        listener = PgListener(settings.database_url, hub)
        listener.start()
        background.append(listener)
    if settings.is_self_hosted:
        from app import selfhost

        background += selfhost.start(container)
    if settings.demo_showcase and not settings.is_demo:
        from app import showcase

        showcase.start_background_seed(container)
    background += audit.on_start(container)  # audit-log timestamps in memory; the retention purge in self-hosted mode
    log.info("started", extra={"extra_fields": {"mode": settings.mode, "env": settings.env, "ai": settings.ai_ready}})
    yield
    for b in background:
        b.stop()
    pool = container.extras.get("pool")
    if pool:
        pool.close()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="Welcome Wave API", version="1.0.0", lifespan=lifespan,
                  docs_url="/api/docs" if settings.is_demo else None, redoc_url=None, openapi_url="/api/openapi.json" if settings.is_demo else None)
    app.add_middleware(BodyLimitMiddleware, settings=settings)  # innermost: an oversized body is refused (413) before it is read
    app.add_middleware(RateLimitMiddleware)  # added before CORS, so it runs inside it: a 429 keeps the CORS headers (app/ratelimit.py)
    origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()] or (["*"] if settings.is_demo else [])
    if origins:
        app.add_middleware(CORSMiddleware, allow_origins=origins, allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
                           allow_headers=["Authorization", "Content-Type", "X-Demo-User", "X-Request-Id"])

    @app.middleware("http")
    async def request_log(request: Request, call_next):
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex
        token = request_id.set(rid)
        traced = trace_context.set(trace_of(request.headers))  # every log line of this request carries its trace
        start = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            log.exception("unhandled error", extra={"extra_fields": {"path": redact(request.url.path)}})
            response = JSONResponse({"detail": "Something went wrong. Quote this id if you report it.", "request_id": rid}, status_code=500)
        ms = round((time.perf_counter() - start) * 1000)
        response.headers["x-request-id"] = rid
        for k, v in SECURITY_HEADERS.items():
            response.headers.setdefault(k, v)
        if not request.url.path.startswith("/api/docs"):
            response.headers.setdefault("Content-Security-Policy", API_CSP)
        user = getattr(request.state, "user", None)
        path = redact(request.url.path)  # /welcome/<token> and /api/preboarding/<token> are credentials: never logged
        log.info("request", extra={"extra_fields": {
            "path": path, "method": request.method, "status": response.status_code, "latency_ms": ms,
            "user_id": user.id if user else None, "org_id": user.org_id if user else None,
            "httpRequest": {"requestMethod": request.method, "requestUrl": path, "status": response.status_code,
                            "latency": f"{ms / 1000:.3f}s"}}})
        request_id.reset(token)
        trace_context.reset(traced)
        return response

    @app.exception_handler(EventBusy)
    async def busy(_request: Request, _exc: EventBusy):
        return JSONResponse({"detail": "That event is already being processed."}, status_code=409)

    @app.get("/health")
    def health():
        """Liveness: the process is up. Cheap on purpose (uptime checks call it every minute)."""
        return {"ok": True}

    @app.get("/health/ready")
    def ready(request: Request):
        """Readiness: the database answers."""
        try:
            ok = request.app.state.container.store.ping()
        except Exception:
            log.exception("readiness check failed")
            ok = False
        return JSONResponse({"ok": ok}, status_code=200 if ok else 503)

    app.include_router(routes.router, prefix="/api")
    app.include_router(uploads.router, prefix="/api")  # KT recording uploads: straight to Cloud Storage on Google Cloud
    app.include_router(stream.router, prefix="/api")
    app.include_router(lifecycle.router, prefix="/api")  # the onboarding lifecycle (app/lifecycle)
    app.include_router(voice.router, prefix="/api")  # live Agora/Murf employee voice sessions
    app.include_router(board.router, prefix="/api")  # the milestone board (app/lifecycle/board.py)
    for insight in (knowledge_risk, kt_planner, code_tour):  # the three insight views, one module each
        app.include_router(insight.router, prefix="/api")
    app.include_router(webhooks.router)
    app.include_router(outbox.router)
    app.include_router(audit.router)  # /api/audit, /api/audit.csv, /internal/audit/purge
    app.include_router(members.router, prefix="/api")  # /api/org/members: roles, deactivation (admins)
    app.include_router(legal.router, prefix="/api")  # /api/legal, /api/org/legal: terms, privacy and DPA acceptances
    app.include_router(agents.router)  # /api/agents (the multi-agent model), /internal/lifecycle/tick
    app.include_router(client_errors.router, prefix="/api")  # the web app's crash reports: no sign-in, logged, never stored
    if settings.is_demo:  # stand-ins for the sign-in and the GitHub App the demo runs without; never registered in production
        from app.api import demo

        app.include_router(demo.router, prefix="/api")
    elif settings.demo_showcase:  # optional isolated showcase accounts underneath the production sign-in form
        from app import showcase

        app.include_router(showcase.router, prefix="/api")
    return app


app = create_app()
