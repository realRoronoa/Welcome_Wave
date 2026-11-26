"""Reading and purging the audit log, for the in-memory store and for Postgres.

The writer is `store.audit(...)` (ports.Store). These are functions that take the store rather than store methods, so the
audit API (app/api/audit.py) can be added without touching the adapters other work is changing.

Memory: MemoryStore.audit appends rows with no id or time. `prepare_memory` wraps the store's writer so every row gets both
when written; rows written before (the demo seed) get the moment it ran, which is the app's start.

Postgres: reads run as the signed-in organisation, under row-level security (and filter on org_id as well). The purge
crosses organisations, so it calls `purge_audit_log(before)`, a SECURITY DEFINER function (012_audit_retention.sql) that
also refuses to delete anything younger than 30 days, whatever it is asked.

Order. A page is sorted by one column (SORTS) in one direction (ORDERS); newest first is the default everywhere. Equal values
always break the same way, so paging never repeats or skips an entry:
    time            created_at, then id, both in the asked direction (the index audit_log_org_recent serves either way)
    action, target  the value (case-folded for target), then newest first
    actor           the person's name, case-folded, entries with no person ("System") last in both directions; then newest first
Both adapters sort by code point (Postgres: COLLATE "C"), so the memory store and the database agree on every page.
"""
from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterator

MIN_RETENTION_DAYS = 30  # the floor the database function enforces too: recent history cannot be purged from the app
SORTS = ("time", "action", "actor", "target")
ORDERS = ("desc", "asc")
SYSTEM = "system"  # the actor filter for entries no person made (the purge, database functions)


@dataclass(frozen=True)
class AuditFilter:
    org_id: str
    action: str | None = None  # exact, or a prefix ending in "*" ("approval.*")
    actor: str | None = None  # a user id, an email address, or "system" (no person)
    target: str | None = None
    since: datetime | None = None  # inclusive
    until: datetime | None = None  # exclusive
    exclude_action: str | None = None  # leave these out: exact, or a prefix ending in "*" ("audit.viewed")
    max_id: int | None = None  # a snapshot: only entries with this id or lower (written before a first page was read)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def is_postgres(store) -> bool:
    from sqlalchemy.engine import Engine

    return isinstance(getattr(store, "engine", None), Engine)


def _is_uuid(value: str) -> bool:
    try:
        uuid.UUID(value)
        return True
    except (ValueError, TypeError, AttributeError):
        return False


def _check_order(sort: str, order: str) -> None:
    if sort not in SORTS:
        raise ValueError(f"sort must be one of {SORTS}")
    if order not in ORDERS:
        raise ValueError(f"order must be one of {ORDERS}")


def _action_matches(pattern: str, action: str) -> bool:
    return action.startswith(pattern[:-1]) if pattern.endswith("*") else action == pattern


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
    if f.action and not _action_matches(f.action, row["action"]):
        return False
    if f.exclude_action and _action_matches(f.exclude_action, row["action"]):
        return False
    if f.target is not None and row["target"] != f.target:
        return False
    if f.max_id is not None and row["id"] > f.max_id:
        return False
    if f.since and row["created_at"] < f.since:
        return False
    if f.until and row["created_at"] >= f.until:
        return False
    if f.actor:
        if f.actor == SYSTEM:
            return not row.get("actor_id")
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


def _memory_sorted(rows: list[dict], sort: str, order: str) -> list[dict]:
    """The rule in the module docstring, as stable sorts: the tiebreak first, then the column."""
    desc = order == "desc"
    if sort == "time":
        return sorted(rows, key=lambda r: (r["created_at"], r["id"]), reverse=desc)
    rows = sorted(rows, key=lambda r: (r["created_at"], r["id"]), reverse=True)  # ties: newest first
    if sort == "action":
        return sorted(rows, key=lambda r: r["action"], reverse=desc)
    if sort == "target":
        return sorted(rows, key=lambda r: (r["target"] or "").lower(), reverse=desc)
    named = [r for r in rows if r["actor_name"] is not None]
    nameless = [r for r in rows if r["actor_name"] is None]
    return sorted(named, key=lambda r: r["actor_name"].lower(), reverse=desc) + nameless


def _memory_rows(store, f: AuditFilter, sort: str = "time", order: str = "desc") -> list[dict]:
    prepare_memory(store)
    people: dict = {}
    with _lock:
        rows = [dict(r) for r in _inner(store).audit_rows]
    out = []
    for r in rows:
        if _memory_matches(store, f, r, people):
            who = _memory_person(store, r.get("actor_id"), people) or {}
            out.append(_shape(r["id"], r["created_at"], r.get("actor_id"), who.get("name"), who.get("email"), r["action"], r["target"], r.get("detail")))
    return _memory_sorted(out, sort, order)


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


def _memory_facets(store, org_id: str) -> dict:
    prepare_memory(store)
    with _lock:
        rows = [dict(r) for r in _inner(store).audit_rows if r.get("org_id") == org_id]
    actions: dict[str, int] = {}
    actors: dict[str | None, int] = {}
    for r in rows:
        actions[r["action"]] = actions.get(r["action"], 0) + 1
        key = str(r["actor_id"]) if r.get("actor_id") else None
        actors[key] = actors.get(key, 0) + 1
    people: dict = {}
    named = [(a, n, _memory_person(store, a, people) or {}) for a, n in actors.items() if a]
    times = [r["created_at"] for r in rows]
    return _facets_shape(sorted(actions.items()), [(a, who.get("name"), who.get("email"), n) for a, n, who in named],
                         actors.get(None, 0), min(times) if times else None, max(times) if times else None)


def _memory_targets(store, org_id: str, ids: set[str]) -> dict[str, tuple[str, str]]:
    """{id: (kind, name)} for the ids that name a person, an onboarding or a meeting of this organisation."""
    out: dict[str, tuple[str, str]] = {}

    def org_of_project(project_id):
        try:
            return store.get_project(project_id).org_id
        except Exception:  # noqa: BLE001 - an unknown project: not ours to describe
            return None

    for i in ids:
        user = store.get_user(i)
        if user is not None:
            if user.org_id == org_id:
                out[i] = ("person", user.name)
            continue
        ob = store.get_onboarding(i)
        if ob is not None:
            hire = store.get_user(ob.new_hire_id)
            if hire is not None and org_of_project(ob.project_id) == org_id:
                out[i] = ("onboarding", hire.name)
            continue
        meeting = store.get_meeting(i)
        if meeting is not None:
            if org_of_project(meeting.project_id) == org_id:
                out[i] = ("meeting", meeting.title)
            continue
        try:
            project = store.get_project(i)
        except Exception:  # noqa: BLE001 - not a project id
            project = None
        if project is not None and project.org_id == org_id:
            out[i] = ("project", project.name)
    return out


# ---------------------------------------------------------------- postgres

def _like(prefix: str) -> str:
    return prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def _pg_action(column: str, pattern: str, name: str, p: dict, negate: bool = False) -> str:
    if pattern.endswith("*"):
        p[name] = _like(pattern[:-1])
        return f"{column} {'NOT ' if negate else ''}LIKE :{name} ESCAPE '\\'"
    p[name] = pattern
    return f"{column} {'<>' if negate else '='} :{name}"


def _pg_where(f: AuditFilter) -> tuple[str, dict] | None:
    """The WHERE clause and its parameters, or None when the filter can match nothing (a malformed id)."""
    if not _is_uuid(f.org_id):
        return None
    clauses, p = ["a.org_id = CAST(:org AS uuid)"], {"org": f.org_id}
    if f.action:
        clauses.append(_pg_action("a.action", f.action, "action", p))
    if f.exclude_action:
        clauses.append(_pg_action("a.action", f.exclude_action, "exclude_action", p, negate=True))
    if f.actor:
        if f.actor == SYSTEM:
            clauses.append("a.actor_id IS NULL")
        elif "@" in f.actor:
            clauses.append("lower(u.email) = lower(:actor)")
            p["actor"] = f.actor
        elif _is_uuid(f.actor):
            clauses.append("a.actor_id = CAST(:actor AS uuid)")
            p["actor"] = f.actor
        else:
            return None
    if f.target is not None:
        clauses.append("a.target = :target")
        p["target"] = f.target
    if f.max_id is not None:
        clauses.append("a.id <= :max_id")
        p["max_id"] = f.max_id
    if f.since:
        clauses.append("a.created_at >= :since")
        p["since"] = f.since
    if f.until:
        clauses.append("a.created_at < :until")
        p["until"] = f.until
    return " AND ".join(clauses), p


PG_FROM = "FROM audit_log a LEFT JOIN users u ON u.id = a.actor_id"
PG_COLUMNS = "a.id, a.created_at, a.actor_id::text AS actor_id, u.name AS actor_name, u.email AS actor_email, a.action, a.target, a.detail"
_NEWEST = "a.created_at DESC, a.id DESC"


def _pg_order(sort: str, order: str) -> str:
    """The ORDER BY for one column and direction: the rule in the module docstring. Only fixed SQL reaches the query."""
    _check_order(sort, order)
    d = "DESC" if order == "desc" else "ASC"
    return {
        "time": f"a.created_at {d}, a.id {d}",
        "action": f'a.action COLLATE "C" {d}, {_NEWEST}',
        "target": f'lower(a.target) COLLATE "C" {d}, {_NEWEST}',
        "actor": f'lower(u.name) COLLATE "C" {d} NULLS LAST, {_NEWEST}',
    }[sort]


def _pg_rows(store, sql: str, p: dict, sort: str, order: str, limit: int, offset: int) -> list[dict]:
    rows = store.rows(f"SELECT {PG_COLUMNS} {PG_FROM} WHERE {sql} ORDER BY {_pg_order(sort, order)} LIMIT :limit OFFSET :offset",
                      **p, limit=limit, offset=offset)
    return [_shape(r["id"], r["created_at"], r["actor_id"], r["actor_name"], r["actor_email"], r["action"], r["target"], r["detail"])
            for r in rows]


def _pg_page(store, f: AuditFilter, limit: int, offset: int, sort: str, order: str) -> tuple[list[dict], int]:
    _check_order(sort, order)
    where = _pg_where(f)
    if where is None:
        return [], 0
    sql, p = where
    total = store.one(f"SELECT count(*) AS n {PG_FROM} WHERE {sql}", **p)["n"]
    return _pg_rows(store, sql, p, sort, order, limit, offset), total


def _pg_iterate(store, f: AuditFilter, cap: int, batch: int, sort: str, order: str) -> list[dict]:
    """Every matching entry up to `cap`, read in batches inside one read-only snapshot (PgStore.reading): entries written while
    the export runs (someone else's actions, this admin's own page views) never shift a later batch."""
    _check_order(sort, order)
    where = _pg_where(f)
    if where is None:
        return []
    sql, p = where
    out: list[dict] = []
    with store.reading():
        while len(out) < cap:
            rows = _pg_rows(store, sql, p, sort, order, min(batch, cap - len(out)), len(out))
            out.extend(rows)
            if len(rows) < batch:
                break
    return out


def _pg_purge(store, before: datetime) -> dict[str, int]:
    from app.adapters.postgres import current_org

    token = current_org.set(None)  # the definer function crosses organisations; no tenant is set for it
    try:
        rows = store.rows("SELECT out_org::text AS org_id, removed FROM purge_audit_log(:before)", before=before)
    finally:
        current_org.reset(token)
    return {r["org_id"]: int(r["removed"]) for r in rows}


def _pg_facets(store, org_id: str) -> dict:
    if not _is_uuid(org_id):
        return _facets_shape([], [], 0, None, None)
    with store.reading():
        actions = store.rows("SELECT a.action, count(*) AS n FROM audit_log a WHERE a.org_id = CAST(:org AS uuid) "
                             'GROUP BY a.action ORDER BY a.action COLLATE "C"', org=org_id)
        actors = store.rows(f"SELECT a.actor_id::text AS id, min(u.name) AS name, min(u.email) AS email, count(*) AS n {PG_FROM} "
                            "WHERE a.org_id = CAST(:org AS uuid) GROUP BY a.actor_id", org=org_id)
        span = store.one("SELECT min(created_at) AS first, max(created_at) AS last FROM audit_log a WHERE a.org_id = CAST(:org AS uuid)",
                         org=org_id)
    system = sum(int(r["n"]) for r in actors if r["id"] is None)
    return _facets_shape([(r["action"], int(r["n"])) for r in actions],
                         [(r["id"], r["name"], r["email"], int(r["n"])) for r in actors if r["id"] is not None],
                         system, span["first"] if span else None, span["last"] if span else None)


def _pg_targets(store, org_id: str, ids: set[str]) -> dict[str, tuple[str, str]]:
    keys = sorted(i for i in ids if _is_uuid(i))
    if not keys or not _is_uuid(org_id):
        return {}
    rows = store.rows(
        "SELECT t.id::text AS id, u.name AS person, hu.name AS hire, m.title AS meeting, p.name AS project "
        "FROM unnest(CAST(:ids AS uuid[])) AS t(id) "
        "LEFT JOIN users u ON u.id = t.id AND u.org_id = CAST(:org AS uuid) "
        "LEFT JOIN onboardings o ON o.id = t.id AND o.org_id = CAST(:org AS uuid) "
        "LEFT JOIN users hu ON hu.id = o.new_hire_id "
        "LEFT JOIN meetings m ON m.id = t.id AND m.org_id = CAST(:org AS uuid) "
        "LEFT JOIN projects p ON p.id = t.id AND p.org_id = CAST(:org AS uuid)", ids=keys, org=org_id)
    out: dict[str, tuple[str, str]] = {}
    for r in rows:
        if r["person"]:
            out[r["id"]] = ("person", r["person"])
        elif r["hire"]:
            out[r["id"]] = ("onboarding", r["hire"])
        elif r["meeting"]:
            out[r["id"]] = ("meeting", r["meeting"])
        elif r["project"]:
            out[r["id"]] = ("project", r["project"])
    return out


# ---------------------------------------------------------------- the API's entry points

def _shape(id_, created_at, actor_id, actor_name, actor_email, action, target, detail) -> dict:
    return {"id": id_, "created_at": created_at, "actor_id": str(actor_id) if actor_id else None, "actor_name": actor_name,
            "actor_email": actor_email, "action": action, "target": target, "detail": detail or {}}


def _facets_shape(actions, actors, system: int, first, last) -> dict:
    people = sorted(({"id": a, "name": name, "email": email, "count": n} for a, name, email, n in actors),
                    key=lambda p: ((p["name"] or "￿").lower(), p["id"]))
    return {"actions": [{"action": a, "count": n} for a, n in actions], "actors": people, "system": system,
            "first_at": first, "last_at": last}


def page(store, f: AuditFilter, limit: int, offset: int, sort: str = "time", order: str = "desc") -> tuple[list[dict], int]:
    """One page of matching entries in the asked order (newest first unless told otherwise), and how many match in all."""
    _check_order(sort, order)
    if is_postgres(store):
        return _pg_page(store, f, limit, offset, sort, order)
    rows = _memory_rows(store, f, sort, order)
    return rows[offset:offset + limit], len(rows)


def newest_id(store, org_id: str) -> int | None:
    """The id of the organisation's newest entry: the snapshot a first page is read at. Ids only grow (a sequence in Postgres,
    a counter in memory), so `max_id` = this keeps later pages from shifting when entries are written in between (each read
    of the log writes one)."""
    if is_postgres(store):
        if not _is_uuid(org_id):
            return None
        r = store.one("SELECT max(id) AS n FROM audit_log a WHERE a.org_id = CAST(:org AS uuid)", org=org_id)
        return int(r["n"]) if r and r["n"] is not None else None
    prepare_memory(store)
    with _lock:
        ids = [r["id"] for r in _inner(store).audit_rows if r.get("org_id") == org_id and "id" in r]
    return max(ids) if ids else None


def iterate(store, f: AuditFilter, cap: int, batch: int = 1000, sort: str = "time", order: str = "desc") -> Iterator[dict]:
    """Every matching entry in the asked order, at most `cap`, from one consistent view of the log."""
    _check_order(sort, order)
    rows = _pg_iterate(store, f, cap, batch, sort, order) if is_postgres(store) else _memory_rows(store, f, sort, order)[:cap]
    yield from rows


def facets(store, org_id: str) -> dict:
    """What the organisation's log holds, for the filter menus: each action and each person with how many entries, how many
    entries no person made, and the first and last entry's time."""
    return _pg_facets(store, org_id) if is_postgres(store) else _memory_facets(store, org_id)


def target_link(kind: str | None, target: str) -> str | None:
    """Where a described target lives in the web app (a path, as API links are elsewhere), or None."""
    base, _, rest = target.partition(":")
    return {"person": "/people", "onboarding": f"/program/{base}" + ("?tab=milestones" if rest else ""),
            "meeting": f"/meetings/{base}"}.get(kind or "")


def describe_targets(store, org_id: str, rows: list[dict]) -> list[dict]:
    """Add `target_kind`, `target_name` and `target_link` to each row: "person", "onboarding" (named for the hire), "meeting"
    or "project" for targets that are this organisation's, "organisation" (no name) for the organisation itself, else None.
    A milestone's target ("<onboarding id>:<key>") names its onboarding. A lookup that fails leaves the raw target:
    describing never breaks the log."""
    ids = {str(r["target"]).split(":", 1)[0] for r in rows if r.get("target")}
    try:
        known = _pg_targets(store, org_id, ids) if is_postgres(store) else _memory_targets(store, org_id, ids)
    except Exception:  # noqa: BLE001 - names are a convenience; the entries are what matter
        known = {}
    known[str(org_id)] = ("organisation", None)
    for r in rows:
        target = str(r.get("target") or "")
        kind, name = known.get(target.split(":", 1)[0], (None, None))
        r["target_kind"], r["target_name"], r["target_link"] = kind, name, target_link(kind, target)
    return rows


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
