"""The audit log: who did what, when, in this organisation. Admins only.

    GET  /api/audit          one page. Order: sort = time | action | actor | target, order = desc | asc (default: time, desc,
                             newest first; equal values break the same way every time, see app/adapters/audit_store.py).
                             Filters: action (exact, or a prefix ending in *), exclude_action (the same, left out), actor
                             (user id, email, or "system" for entries no person made), target, since / until (ISO date or
                             date-time; a date-only `until` includes that day, in UTC: the web app sends the exact instants of
                             the viewer's own days). Paging: limit (1-200) and offset; `total` counts every match.
                             Snapshot: the response's `as_of` is the newest entry's id when the page was read; sent back
                             with the next pages (and the CSV), they leave out what was written since, so paging never
                             repeats or skips a row although every read writes an entry.
    GET  /api/audit.csv      the same filters, order and as_of, every matching entry (at most AUDIT_EXPORT_MAX_ROWS) as a CSV download
    GET  /api/audit/facets   what the log holds, for the filter menus: each action and each person with their entry counts
    POST /internal/audit/purge   deletes entries older than AUDIT_RETENTION_DAYS in every organisation. Called by Cloud
                             Scheduler with a Google-signed OIDC token, exactly like POST /internal/outbox/flush (same
                             audience and invoker settings); off without that setup. Self-hosted mode runs it once a day.

Reading the log is itself recorded (audit.viewed, the facets too), as is every export (audit.exported) and purge
(audit.purged). Nothing here edits an entry. The rows come from `store.audit(...)` calls across the API and the database
functions.
"""
from __future__ import annotations

import csv
import io
import json
import logging
import threading
from datetime import date, datetime, time, timedelta, timezone
from functools import lru_cache
from typing import Literal

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


Sort = Literal["time", "action", "actor", "target"]
Order = Literal["desc", "asc"]


def _filter(user: User, action, actor, target, since, until, exclude_action=None, as_of: int | None = None) -> AuditFilter:
    f = AuditFilter(org_id=user.org_id, action=action or None, actor=actor or None, target=target if target else None,
                    since=_when(since, end=False), until=_when(until, end=True), exclude_action=exclude_action or None,
                    max_id=as_of)
    if f.since and f.until and f.since >= f.until:
        raise HTTPException(422, "`since` must be before `until`.")
    return f


def _filters_used(f: AuditFilter, sort: str = "time", order: str = "desc") -> dict:
    """The filters a read used, for its own audit entry (the order only when it is not the default)."""
    used = {k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in
            {"action": f.action, "exclude_action": f.exclude_action, "actor": f.actor, "target": f.target, "since": f.since,
             "until": f.until}.items() if v}
    if (sort, order) != ("time", "desc"):
        used.update(sort=sort, order=order)
    return used


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _json(row: dict) -> dict:
    return {**row, "created_at": _iso(row["created_at"])}


def _retention_days() -> int:
    return max(audit_store.MIN_RETENTION_DAYS, audit_settings().audit_retention_days)


def _describe_lifecycle(c, org_id: str, rows: list[dict]) -> None:
    """Name the targets that live in the lifecycle store (access items, handovers, documentation feedback), for rows the
    core lookup left unnamed. Only this organisation's records are named; a failed lookup leaves the raw target."""
    lc = getattr(c, "lifecycle", None)
    if lc is None:
        return
    people: dict[str, str | None] = {}

    def person(user_id: str | None) -> str | None:
        if user_id and user_id not in people:
            u = c.store.get_user(user_id)
            people[user_id] = u.name if u and u.org_id == org_id else None
        return people.get(user_id) if user_id else None

    def project_org(project_id: str) -> str | None:
        try:
            return c.store.get_project(project_id).org_id
        except Exception:  # noqa: BLE001 - unknown or malformed: not ours
            return None

    for r in rows:
        if r.get("target_kind") or not r.get("target"):
            continue
        head, target = r["action"].split(".", 1)[0], r["target"]
        try:
            if head == "access":
                item = lc.get_access_item(target)
                ob = c.store.get_onboarding(item.onboarding_id) if item else None
                if item and ob and project_org(ob.project_id) == org_id:
                    hire = person(ob.new_hire_id)
                    r.update(target_kind="access", target_link=f"/program/{ob.id}?tab=access",
                             target_name=f"{item.system} access" + (f" for {hire}" if hire else ""))
            elif head == "handover":
                h = lc.get_handover(target)
                if h and h.org_id == org_id:
                    r.update(target_kind="handover", target_link="/handovers", target_name=person(h.user_id) or "A former member")
            elif head == "feedback":
                fb = lc.get_feedback(target)
                if fb and project_org(fb.project_id) == org_id:
                    r.update(target_kind="feedback", target_link=None, target_name=fb.target_kind)
        except Exception:  # noqa: BLE001 - names are a convenience
            continue


BOARD_TASK_ACTIONS = ("board.task.", "task.moved")


def _describe_board(c, org_id: str, rows: list[dict]) -> None:
    """Board entries read as the board: a task's target is named for the task ("Task · <title>", from the entry or the task
    itself) and links to the hire's board; a board milestone's onboarding target links to the board, not the program."""
    postgres = audit_store.is_postgres(c.store)
    project_org: dict[str, str | None] = {}

    def org_of(project_id: str) -> str | None:
        if project_id not in project_org:
            try:
                project_org[project_id] = c.store.get_project(project_id).org_id
            except Exception:  # noqa: BLE001 - unknown: not ours
                project_org[project_id] = None
        return project_org[project_id]

    for r in rows:
        action, target, d = r["action"], str(r.get("target") or ""), r.get("detail") or {}
        if action.startswith("board.") and r.get("target_kind") == "onboarding":
            r["target_link"] = f"/board/{target.split(':', 1)[0]}"
            continue
        if r.get("target_kind") or not target or not action.startswith(BOARD_TASK_ACTIONS):
            continue
        try:
            title, ob_id = d.get("title"), d.get("onboarding_id")
            if not (title and ob_id) and (not postgres or audit_store._is_uuid(target)):
                t = c.store.get_task(target)
                title, ob_id = title or (t.title if t else None), ob_id or (t.onboarding_id if t else None)
            ob = c.store.get_onboarding(str(ob_id)) if ob_id and (not postgres or audit_store._is_uuid(str(ob_id))) else None
            if ob is None or org_of(ob.project_id) != org_id:
                continue
            hire = c.store.get_user(ob.new_hire_id)
            r.update(target_kind="task", target_name=str(title or "A task")[:160] + (f" · {hire.name}'s board" if hire else ""),
                     target_link=f"/board/{ob.id}")
        except Exception:  # noqa: BLE001 - names are a convenience
            continue


def _describe(c, org_id: str, rows: list[dict]) -> None:
    """Every name for a page's targets, from one database snapshot when the store is Postgres."""
    def run():
        audit_store.describe_targets(c.store, org_id, rows)
        _describe_lifecycle(c, org_id, rows)
        _describe_board(c, org_id, rows)

    if audit_store.is_postgres(c.store):
        with c.store.reading():
            run()
    else:
        run()


@router.get("/api/audit")
def list_audit(
    action: str | None = Query(None, max_length=120),
    actor: str | None = Query(None, max_length=320),
    target: str | None = Query(None, max_length=500),
    since: str | None = Query(None, max_length=40),
    until: str | None = Query(None, max_length=40),
    exclude_action: str | None = Query(None, max_length=120),
    sort: Sort = Query("time"),
    order: Order = Query("desc"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0, le=1_000_000),
    as_of: int | None = Query(None, ge=0, le=2**63 - 1),
    user: User = Depends(require("admin")),
    c=Depends(container),
):
    if as_of is None:
        as_of = audit_store.newest_id(c.store, user.org_id)  # read before this view's own entry is written
    f = _filter(user, action, actor, target, since, until, exclude_action, as_of)
    rows, total = audit_store.page(c.store, f, limit, offset, sort, order)
    _describe(c, user.org_id, rows)
    c.store.audit(user.org_id, user.id, "audit.viewed", "audit_log", {**_filters_used(f, sort, order), "offset": offset, "shown": len(rows)})
    nxt = offset + len(rows)
    return {"items": [_json(r) for r in rows], "total": total, "limit": limit, "offset": offset,
            "next_offset": nxt if nxt < total else None, "sort": sort, "order": order, "as_of": as_of,
            "page": offset // limit + 1, "pages": max(1, -(-total // limit)), "retention_days": _retention_days()}


@router.get("/api/audit/facets")
def audit_facets(user: User = Depends(require("admin")), c=Depends(container)):
    """Each action and each person in the log with how many entries they have, and the span of time it covers."""
    out = audit_store.facets(c.store, user.org_id)
    c.store.audit(user.org_id, user.id, "audit.viewed", "audit_log", {"facets": True})
    return {**out, "first_at": _iso(out["first_at"]), "last_at": _iso(out["last_at"]), "retention_days": _retention_days()}


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
    exclude_action: str | None = Query(None, max_length=120),
    sort: Sort = Query("time"),
    order: Order = Query("desc"),
    as_of: int | None = Query(None, ge=0, le=2**63 - 1),
    user: User = Depends(require("admin")),
    c=Depends(container),
):
    f = _filter(user, action, actor, target, since, until, exclude_action, as_of)
    cap = audit_settings().audit_export_max_rows
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(CSV_COLUMNS)
    n = 0
    for row in audit_store.iterate(c.store, f, cap, sort=sort, order=order):
        r = _json(row)
        r["detail"] = json.dumps(r["detail"], ensure_ascii=False, sort_keys=True, default=str)
        w.writerow([_cell(r[k]) for k in CSV_COLUMNS])
        n += 1
    c.store.audit(user.org_id, user.id, "audit.exported", "audit_log", {**_filters_used(f, sort, order), "rows": n, "capped": n >= cap})
    name = f"audit-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}.csv"
    return Response(out.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


def purge_expired(c) -> dict[str, int]:
    """Delete entries past the retention period in every organisation, and record the purge in each one that lost rows.
    For the self-hosted loop and the scheduled endpoint."""
    days = _retention_days()
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
