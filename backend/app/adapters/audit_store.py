"""Reading and purging the audit log, for the in-memory store and for Postgres.

The writer is `store.audit(...)` (ports.Store). These are functions that take the store rather than store methods, so the
audit API (app/api/audit.py) can be added without touching the adapters other work is changing.

Memory: MemoryStore.audit appends rows with no id or time. `prepare_memory` wraps the store's writer so every row gets both
when written; rows written before (the demo seed) get the moment it ran, which is the app's start.

Postgres: reads run as the signed-in organisation, under row-level security (and filter on org_id as well). The purge
crosses organisations, so it calls `purge_audit_log(before)`, a SECURITY DEFINER function (012_audit_retention.sql) that
also refuses to delete anything younger than 30 days, whatever it is asked.
"""
from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterator

MIN_RETENTION_DAYS = 30  # the floor the database function enforces too: recent history cannot be purged from the app


@dataclass(frozen=True)
class AuditFilter:
    org_id: str
    action: str | None = None  # exact, or a prefix ending in "*" ("approval.*")
    actor: str | None = None  # a user id, or an email address
    target: str | None = None
    since: datetime | None = None  # inclusive
    until: datetime | None = None  # exclusive


def _now() -> datetime:
    return datetime.now(timezone.utc)


def is_postgres(store) -> bool:
    from sqlalchemy.engine import Engine

    return isinstance(getattr(store, "engine", None), Engine)


def _is_uuid(value: str) -> bool:
    try:
        uuid.UUID(value)
        return True
    except (ValueError, TypeError):
        return False


# ---------------------------------------------------------------- memory

_lock = threading.RLock()


def _inner(store):
    """The MemoryStore itself: demo mode wraps it in app.live.LiveStore."""
    return store.__dict__.get("_store", store)


def _stamp(inner) -> None:
    rows = inner.audit_rows
    i = len(rows)
    while i > 0 and "id" not in rows[i - 1]:
        i -= 1
    seq = inner.__dict__.get("_audit_seq", 0)
    now = _now()
    for row in rows[i:]:
        seq += 1
        row["id"] = seq
        row.setdefault("created_at", now)
    inner.__dict__["_audit_seq"] = seq


def prepare_memory(store) -> None:
    """Give every audit row an id and a time, now and from now on. Idempotent."""
    inner = _inner(store)
    with _lock:
        if not inner.__dict__.get("_audit_clock"):
            write = inner.audit  # the store's own method

            def audit(org_id, actor_id, action, target, detail=None):
                with _lock:
                    write(org_id, actor_id, action, target, detail)
                    _stamp(inner)

            inner.__dict__["audit"] = audit
            inner.__dict__["_audit_clock"] = True
        _stamp(inner)


def _memory_matches(store, f: AuditFilter, row: dict, people: dict) -> bool:
    if row.get("org_id") != f.org_id:
        return False
    if f.action:
        if f.action.endswith("*"):
            if not row["action"].startswith(f.action[:-1]):
                return False
        elif row["action"] != f.action:
            return False
    if f.target is not None and row["target"] != f.target:
        return False
    if f.since and row["created_at"] < f.since:
        return False
    if f.until and row["created_at"] >= f.until:
        return False
    if f.actor:
        if "@" in f.actor:
            who = _memory_person(store, row.get("actor_id"), people)
            return bool(who) and (who.get("email") or "").lower() == f.actor.lower()
        return str(row.get("actor_id")) == f.actor
    return True


def _memory_person(store, actor_id, people: dict) -> dict | None:
    if not actor_id:
        return None
    if actor_id not in people:
        u = store.get_user(str(actor_id))
        people[actor_id] = {"name": u.name, "email": u.email} if u else None
    return people[actor_id]


def _memory_rows(store, f: AuditFilter) -> list[dict]:
    prepare_memory(store)
    people: dict = {}
    with _lock:
        rows = [dict(r) for r in _inner(store).audit_rows]
    out = []
    for r in rows:
        if _memory_matches(store, f, r, people):
            who = _memory_person(store, r.get("actor_id"), people) or {}
            out.append(_shape(r["id"], r["created_at"], r.get("actor_id"), who.get("name"), who.get("email"), r["action"], r["target"], r.get("detail")))
    out.sort(key=lambda r: (r["created_at"], r["id"]), reverse=True)
    return out


def _memory_purge(store, before: datetime) -> dict[str, int]:
    prepare_memory(store)
    inner = _inner(store)
    removed: dict[str, int] = {}
    with _lock:
        kept = []
        for r in inner.audit_rows:
            if r["created_at"] < before:
                removed[str(r.get("org_id"))] = removed.get(str(r.get("org_id")), 0) + 1
            else:
                kept.append(r)
        inner.audit_rows[:] = kept
    return removed


# ---------------------------------------------------------------- postgres

def _like(prefix: str) -> str:
    return prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def _pg_where(f: AuditFilter) -> tuple[str, dict] | None:
    """The WHERE clause and its parameters, or None when the filter can match nothing (a malformed id)."""
    if not _is_uuid(f.org_id):
        return None
    clauses, p = ["a.org_id = CAST(:org AS uuid)"], {"org": f.org_id}
    if f.action:
        if f.action.endswith("*"):
            clauses.append("a.action LIKE :action ESCAPE '\\'")
            p["action"] = _like(f.action[:-1])
        else:
            clauses.append("a.action = :action")
            p["action"] = f.action
    if f.actor:
        if "@" in f.actor:
            clauses.append("lower(u.email) = lower(:actor)")
        elif _is_uuid(f.actor):
            clauses.append("a.actor_id = CAST(:actor AS uuid)")
        else:
            return None
        p["actor"] = f.actor
    if f.target is not None:
        clauses.append("a.target = :target")
        p["target"] = f.target
    if f.since:
        clauses.append("a.created_at >= :since")
        p["since"] = f.since
    if f.until:
        clauses.append("a.created_at < :until")
        p["until"] = f.until
    return " AND ".join(clauses), p


PG_FROM = "FROM audit_log a LEFT JOIN users u ON u.id = a.actor_id"
PG_COLUMNS = "a.id, a.created_at, a.actor_id::text AS actor_id, u.name AS actor_name, u.email AS actor_email, a.action, a.target, a.detail"


def _pg_page(store, f: AuditFilter, limit: int, offset: int) -> tuple[list[dict], int]:
    where = _pg_where(f)
    if where is None:
        return [], 0
    sql, p = where
    total = store.one(f"SELECT count(*) AS n {PG_FROM} WHERE {sql}", **p)["n"]
    rows = store.rows(f"SELECT {PG_COLUMNS} {PG_FROM} WHERE {sql} ORDER BY a.created_at DESC, a.id DESC LIMIT :limit OFFSET :offset",
                      **p, limit=limit, offset=offset)
    return [_shape(r["id"], r["created_at"], r["actor_id"], r["actor_name"], r["actor_email"], r["action"], r["target"], r["detail"])
            for r in rows], total


def _pg_purge(store, before: datetime) -> dict[str, int]:
    from app.adapters.postgres import current_org

    token = current_org.set(None)  # the definer function crosses organisations; no tenant is set for it
    try:
        rows = store.rows("SELECT out_org::text AS org_id, removed FROM purge_audit_log(:before)", before=before)
    finally:
        current_org.reset(token)
    return {r["org_id"]: int(r["removed"]) for r in rows}


# ---------------------------------------------------------------- the API's entry points

def _shape(id_, created_at, actor_id, actor_name, actor_email, action, target, detail) -> dict:
    return {"id": id_, "created_at": created_at, "actor_id": str(actor_id) if actor_id else None, "actor_name": actor_name,
            "actor_email": actor_email, "action": action, "target": target, "detail": detail or {}}


def page(store, f: AuditFilter, limit: int, offset: int) -> tuple[list[dict], int]:
    """One page of matching entries, newest first, and how many match in all."""
    if is_postgres(store):
        return _pg_page(store, f, limit, offset)
    rows = _memory_rows(store, f)
    return rows[offset:offset + limit], len(rows)


def iterate(store, f: AuditFilter, cap: int, batch: int = 1000) -> Iterator[dict]:
    """Every matching entry, newest first, at most `cap`."""
    offset = 0
    while offset < cap:
        rows, _ = page(store, f, min(batch, cap - offset), offset)
        yield from rows
        if len(rows) < batch:
            return
        offset += len(rows)


def purge(store, before: datetime) -> dict[str, int]:
    """Delete entries older than `before` (never younger than MIN_RETENTION_DAYS) in every organisation; rows removed per
    organisation."""
    before = min(before, _now() - timedelta(days=MIN_RETENTION_DAYS))
    return _pg_purge(store, before) if is_postgres(store) else _memory_purge(store, before)


def write_as(store, org_id: str, actor_id: str | None, action: str, target: str, detail: dict) -> None:
    """store.audit for a given organisation outside a signed-in request (the purge): in Postgres the row must be written
    under that organisation's row-level security."""
    if not is_postgres(store):
        store.audit(org_id, actor_id, action, target, detail)
        return
    from app.adapters.postgres import current_org

    token = current_org.set(org_id)
    try:
        store.audit(org_id, actor_id, action, target, detail)
    finally:
        current_org.reset(token)
