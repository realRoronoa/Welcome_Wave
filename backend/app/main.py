"""FastAPI entry point (Cloud Run service `ww-api`, or the whole server in self-hosted mode)."""
from __future__ import annotations

import asyncio
import json
import os
import logging
import time
import uuid
from contextlib import asynccontextmanager
from contextvars import ContextVar

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api import agents, audit, code_tour, knowledge_risk, kt_planner, lifecycle, members, outbox, routes, stream, webhooks
from app.config import get_settings
from app.container import EventBusy, build_container
from app.logsafe import AccessLogFilter, redact
from app.ratelimit import RateLimitMiddleware

request_id: ContextVar[str | None] = ContextVar("request_id", default=None)


class JsonFormatter(logging.Formatter):
    """One JSON object per line: Cloud Logging parses severity and fields, and groups lines by trace."""

    def format(self, record):
        out = {"severity": record.levelname, "message": record.getMessage(), "logger": record.name}
        rid = request_id.get()
        if rid:
            out["request_id"] = rid
        out.update(getattr(record, "extra_fields", {}))
        if record.exc_info:
            out["exception"] = self.formatException(record.exc_info)
        return json.dumps(out, default=str)


def setup_logging():
    h = logging.StreamHandler()
    h.setFormatter(JsonFormatter())
    logging.basicConfig(level=logging.INFO, handlers=[h], force=True)
    for noisy in ("httpx", "google", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").addFilter(AccessLogFilter())  # request lines name pre-boarding and signed links: redacted


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
    app.add_middleware(RateLimitMiddleware)  # added first, so it runs inside CORS: a 429 keeps the CORS headers (app/ratelimit.py)
    origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()] or (["*"] if settings.is_demo else [])
    if origins:
        app.add_middleware(CORSMiddleware, allow_origins=origins, allow_methods=["GET", "POST", "PATCH", "DELETE"],
                           allow_headers=["Authorization", "Content-Type", "X-Demo-User", "X-Request-Id"])

    @app.middleware("http")
    async def request_log(request: Request, call_next):
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex
        token = request_id.set(rid)
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
    app.include_router(stream.router, prefix="/api")
    app.include_router(lifecycle.router, prefix="/api")  # the onboarding lifecycle (app/lifecycle)
    for insight in (knowledge_risk, kt_planner, code_tour):  # the three insight views, one module each
        app.include_router(insight.router, prefix="/api")
    app.include_router(webhooks.router)
    app.include_router(outbox.router)
    app.include_router(audit.router)  # /api/audit, /api/audit.csv, /internal/audit/purge
    app.include_router(members.router, prefix="/api")  # /api/org/members: roles, deactivation (admins)
    app.include_router(agents.router)  # /api/agents (the multi-agent model), /internal/lifecycle/tick
    if settings.is_demo:  # stand-ins for the sign-in and the GitHub App the demo runs without; never registered in production
        from app.api import demo

        app.include_router(demo.router, prefix="/api")
    return app


app = create_app()
