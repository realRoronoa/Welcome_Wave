"""The audit log: who did what, when, in this organisation. Admins only.

    GET  /api/audit          one page, newest first; filters: action (exact, or a prefix ending in *), actor (user id or
                             email), target, since / until (ISO date or date-time; a date-only `until` includes that day)
    GET  /api/audit.csv      the same filters, every matching entry (at most AUDIT_EXPORT_MAX_ROWS) as a CSV download
    POST /internal/audit/purge   deletes entries older than AUDIT_RETENTION_DAYS in every organisation. Called by Cloud
                             Scheduler with a Google-signed OIDC token, exactly like POST /internal/outbox/flush (same
                             audience and invoker settings); off without that setup. Self-hosted mode runs it once a day.

Reading the log is itself recorded (audit.viewed), as is every export (audit.exported) and purge (audit.purged).
Nothing here edits an entry. The rows come from `store.audit(...)` calls across the API and the database functions.
"""
from __future__ import annotations

import csv
import io
import json
import logging
import threading
from datetime import date, datetime, time, timedelta, timezone
from functools import lru_cache

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import Response
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.adapters import audit_store
from app.adapters.audit_store import AuditFilter
from app.api.auth import container, require
from app.config import dotenv_unless_demo
from app.ports import User

router = APIRouter()
log = logging.getLogger("audit")


class AuditSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @classmethod
    def settings_customise_sources(cls, settings_cls, init_settings, env_settings, dotenv_settings, file_secret_settings):
        return init_settings, env_settings, dotenv_unless_demo(dotenv_settings), file_secret_settings  # demo reads no .env (config.py)

    audit_retention_days: int = 365  # never below 30: the purge keeps 30 days whatever this says
    audit_export_max_rows: int = 100_000
    audit_purge_every_s: int = 86_400  # self-hosted mode: how often the purge runs (0 = never)


@lru_cache
def audit_settings() -> AuditSettings:
    return AuditSettings()


def _when(value: str | None, end: bool) -> datetime | None:
    """An ISO date or date-time as an aware UTC datetime. A date alone means the start of that day, or for `until` the start
    of the next one, so the day is included."""
    if not value:
        return None
    try:
        if len(value) == 10:
            d = date.fromisoformat(value)
            return datetime.combine(d + timedelta(days=1) if end else d, time.min, tzinfo=timezone.utc)
        t = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(422, f"Not a date or date-time: {value!r}. Use YYYY-MM-DD or an ISO 8601 date-time.") from None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def _filter(user: User, action, actor, target, since, until) -> AuditFilter:
    f = AuditFilter(org_id=user.org_id, action=action or None, actor=actor or None, target=target if target else None,
                    since=_when(since, end=False), until=_when(until, end=True))
    if f.since and f.until and f.since >= f.until:
        raise HTTPException(422, "`since` must be before `until`.")
    return f


def _filters_used(f: AuditFilter) -> dict:
    return {k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in
            {"action": f.action, "actor": f.actor, "target": f.target, "since": f.since, "until": f.until}.items() if v}


def _json(row: dict) -> dict:
    return {**row, "created_at": row["created_at"].isoformat() if row["created_at"] else None}


@router.get("/api/audit")
def list_audit(
    action: str | None = Query(None, max_length=120),
    actor: str | None = Query(None, max_length=320),
    target: str | None = Query(None, max_length=500),
    since: str | None = Query(None, max_length=40),
    until: str | None = Query(None, max_length=40),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0, le=1_000_000),
    user: User = Depends(require("admin")),
    c=Depends(container),
):
    f = _filter(user, action, actor, target, since, until)
    rows, total = audit_store.page(c.store, f, limit, offset)
    c.store.audit(user.org_id, user.id, "audit.viewed", "audit_log", {**_filters_used(f), "offset": offset, "shown": len(rows)})
    nxt = offset + len(rows)
    return {"items": [_json(r) for r in rows], "total": total, "limit": limit, "offset": offset,
            "next_offset": nxt if nxt < total else None, "retention_days": max(audit_store.MIN_RETENTION_DAYS, audit_settings().audit_retention_days)}


CSV_COLUMNS = ["id", "created_at", "actor_id", "actor_name", "actor_email", "action", "target", "detail"]


def _cell(value) -> str:
    """Spreadsheets run a cell that starts with = + - @ (or a tab or carriage return) as a formula: entries quote text that
    people typed (emails, titles), so such a cell is prefixed with an apostrophe."""
    text = "" if value is None else value if isinstance(value, str) else str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


@router.get("/api/audit.csv")
def export_audit(
    action: str | None = Query(None, max_length=120),
    actor: str | None = Query(None, max_length=320),
    target: str | None = Query(None, max_length=500),
    since: str | None = Query(None, max_length=40),
    until: str | None = Query(None, max_length=40),
    user: User = Depends(require("admin")),
    c=Depends(container),
):
    f = _filter(user, action, actor, target, since, until)
    cap = audit_settings().audit_export_max_rows
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(CSV_COLUMNS)
    n = 0
    for row in audit_store.iterate(c.store, f, cap):
        r = _json(row)
        r["detail"] = json.dumps(r["detail"], ensure_ascii=False, sort_keys=True, default=str)
        w.writerow([_cell(r[k]) for k in CSV_COLUMNS])
        n += 1
    c.store.audit(user.org_id, user.id, "audit.exported", "audit_log", {**_filters_used(f), "rows": n, "capped": n >= cap})
    name = f"audit-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}.csv"
    return Response(out.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


def purge_expired(c) -> dict[str, int]:
    """Delete entries past the retention period in every organisation, and record the purge in each one that lost rows.
    For the self-hosted loop and the scheduled endpoint."""
    days = max(audit_store.MIN_RETENTION_DAYS, audit_settings().audit_retention_days)
    before = datetime.now(timezone.utc) - timedelta(days=days)
    removed = audit_store.purge(c.store, before)
    for org_id, n in removed.items():
        if n and org_id and org_id != "None":
            audit_store.write_as(c.store, org_id, None, "audit.purged", "audit_log",
                                 {"removed": n, "before": before.isoformat(), "retention_days": days})
    log.info("audit log purged", extra={"extra_fields": {"organisations": len(removed), "removed": sum(removed.values()), "retention_days": days}})
    return removed


def _scheduler_only(c, authorization: str | None) -> None:
    """The caller is Cloud Scheduler's service account, proven by a Google-signed OIDC token (as POST /internal/outbox/flush)."""
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


@router.post("/internal/audit/purge")
def purge_endpoint(authorization: str | None = Header(default=None), c=Depends(container)):
    _scheduler_only(c, authorization)
    removed = purge_expired(c)
    return {"removed": sum(removed.values()), "organisations": len([n for n in removed.values() if n])}


class RetentionLoop:
    """Self-hosted mode: the purge once a day (AUDIT_PURGE_EVERY_S), first a minute after start."""

    def __init__(self, c, every_s: int):
        self.c, self.every_s = c, every_s
        self._stop = threading.Event()
        self.thread = threading.Thread(target=self._loop, name="ww-audit-retention", daemon=True)

    def start(self) -> "RetentionLoop":
        self.thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        wait = min(60, self.every_s)
        while not self._stop.wait(wait):
            try:
                purge_expired(self.c)
            except Exception:  # noqa: BLE001 - tried again next time
                log.warning("audit purge failed", exc_info=True)
            wait = self.every_s


def on_start(c) -> list:
    """Called once the container is built (app/main.py). Returns what must be stopped at shutdown."""
    if not audit_store.is_postgres(c.store):
        audit_store.prepare_memory(c.store)
    every = audit_settings().audit_purge_every_s
    if c.settings.is_self_hosted and every > 0:
        return [RetentionLoop(c, every).start()]
    return []
