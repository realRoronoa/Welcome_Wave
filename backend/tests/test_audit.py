"""The audit-log API (app/api/audit.py, app/adapters/audit_store.py): admin-only reads with filters and paging, a CSV
export, retention with a 30-day floor, and reads and exports that are themselves recorded."""
from __future__ import annotations

import csv
import io
import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.adapters import audit_store
from app.adapters.audit_store import AuditFilter
from app.api import audit
from app.config import get_settings
from app.main import create_app
from app.seed import ORG

ADMIN = {"X-Demo-User": "u-admin"}
MANAGER = {"X-Demo-User": "u-priya"}


@pytest.fixture
def client():
    get_settings.cache_clear()
    audit.audit_settings.cache_clear()
    with TestClient(create_app()) as c:
        yield c
    audit.audit_settings.cache_clear()


def _store(client):
    return client.app.state.container.store


def _rows(client):
    return _store(client).__dict__.get("_store", _store(client)).audit_rows


def _age(client, action: str, days: float) -> None:
    for r in _rows(client):
        if r["action"] == action:
            r["created_at"] = datetime.now(timezone.utc) - timedelta(days=days)


def test_only_admins_read_the_audit_log(client):
    assert client.get("/api/audit", headers=MANAGER).status_code == 403
    assert client.get("/api/audit.csv", headers=MANAGER).status_code == 403
    assert client.get("/api/audit").status_code == 401
    r = client.get("/api/audit", headers=ADMIN)
    assert r.status_code == 200 and r.headers["content-security-policy"] == "default-src 'none'; frame-ancestors 'none'"
    assert r.json()["retention_days"] >= 30


def test_entries_page_newest_first_and_filter(client):
    store = _store(client)
    for i in range(5):
        store.audit(ORG, "u-priya", "task.moved", f"t{i}", {"to": "done"})
    store.audit(ORG, "u-ravi", "expert.answered", "req-1")
    store.audit("org-other", "u-x", "task.moved", "secret")  # another organisation: never shown

    page = client.get("/api/audit?action=task.moved&limit=2", headers=ADMIN).json()
    assert page["total"] == 5 and [i["target"] for i in page["items"]] == ["t4", "t3"] and page["next_offset"] == 2
    last = client.get("/api/audit?action=task.moved&limit=2&offset=4", headers=ADMIN).json()
    assert [i["target"] for i in last["items"]] == ["t0"] and last["next_offset"] is None
    item = page["items"][0]
    assert item["actor_id"] == "u-priya" and item["actor_email"] == "priya@demo.dev" and item["detail"] == {"to": "done"}
    assert datetime.fromisoformat(item["created_at"]).tzinfo is not None

    # the seeded log has Ravi's earlier entries too: count the one this test wrote
    assert client.get("/api/audit?actor=u-ravi&action=expert.answered", headers=ADMIN).json()["total"] == 1
    assert client.get("/api/audit?actor=RAVI@demo.dev&action=expert.answered", headers=ADMIN).json()["total"] == 1
    assert client.get("/api/audit?target=t2", headers=ADMIN).json()["total"] == 1
    assert client.get("/api/audit?action=task.*", headers=ADMIN).json()["total"] == 5
    assert client.get("/api/audit?target=secret", headers=ADMIN).json()["total"] == 0


def test_date_range(client):
    store = _store(client)
    store.audit(ORG, "u-admin", "old.thing", "a")
    store.audit(ORG, "u-admin", "new.thing", "b")
    _age(client, "old.thing", 10)
    today = datetime.now(timezone.utc).date()
    since = (today - timedelta(days=2)).isoformat()
    actions = {i["action"] for i in client.get(f"/api/audit?since={since}&limit=200", headers=ADMIN).json()["items"]}
    assert "new.thing" in actions and "old.thing" not in actions
    until = (today - timedelta(days=5)).isoformat()
    older = client.get(f"/api/audit?until={until}&limit=200", headers=ADMIN).json()["items"]  # the seeded history is older too
    assert "old.thing" in {i["action"] for i in older} and "new.thing" not in {i["action"] for i in older}
    assert [i["action"] for i in client.get(f"/api/audit?until={until}&action=old.thing", headers=ADMIN).json()["items"]] == ["old.thing"]
    assert client.get(f"/api/audit?until={today.isoformat()}&action=new.thing", headers=ADMIN).json()["total"] == 1  # a date includes its day
    assert client.get("/api/audit?since=yesterday", headers=ADMIN).status_code == 422
    assert client.get(f"/api/audit?since={today}&until={today - timedelta(days=1)}", headers=ADMIN).status_code == 422


def test_reading_and_exporting_are_recorded(client):
    client.get("/api/audit?action=task.moved", headers=ADMIN)
    viewed = client.get("/api/audit?action=audit.viewed", headers=ADMIN).json()["items"]
    assert viewed and viewed[0]["actor_id"] == "u-admin" and viewed[0]["detail"]["action"] == "task.moved"

    _store(client).audit(ORG, "u-priya", "document.uploaded", "d1", {"title": "=HYPERLINK(\"http://evil\")"})
    r = client.get("/api/audit.csv?action=document.uploaded", headers=ADMIN)
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert r.headers["content-disposition"].startswith('attachment; filename="audit-')
    rows = list(csv.DictReader(io.StringIO(r.text)))
    assert len(rows) == 1 and rows[0]["actor_email"] == "priya@demo.dev" and rows[0]["target"] == "d1"
    assert '"title": "=HYPERLINK' in rows[0]["detail"]  # a JSON cell starts with "{", so it is never a formula

    exported = client.get("/api/audit?action=audit.exported", headers=ADMIN).json()["items"]
    assert exported[0]["detail"] == {"action": "document.uploaded", "rows": 1, "capped": False}


def test_csv_cells_that_look_like_formulas_are_quoted():
    assert audit._cell("=SUM(A1)") == "'=SUM(A1)" and audit._cell("@x") == "'@x" and audit._cell("-1") == "'-1"
    assert audit._cell("priya@demo.dev") == "priya@demo.dev" and audit._cell(None) == ""


def test_purge_keeps_the_retention_period_and_never_less_than_30_days(client, monkeypatch):
    store = _store(client)
    for action, days in (("ancient.thing", 400), ("month.old", 45), ("recent.thing", 5)):
        store.audit(ORG, "u-admin", action, "x")
        _age(client, action, days)
    store.audit("org-other", None, "ancient.thing", "y")
    _age(client, "ancient.thing", 400)

    monkeypatch.setenv("AUDIT_RETENTION_DAYS", "365")
    audit.audit_settings.cache_clear()
    removed = audit.purge_expired(client.app.state.container)
    assert removed == {ORG: 1, "org-other": 1}  # the seeded history is under a year old
    left = {r["action"] for r in _rows(client)}
    assert "ancient.thing" not in left and {"month.old", "recent.thing"} <= left

    monkeypatch.setenv("AUDIT_RETENTION_DAYS", "1")  # asks for one day: the 30-day floor wins
    audit.audit_settings.cache_clear()
    gone = audit.purge_expired(client.app.state.container)[ORG]
    assert gone >= 1  # 'month.old' and whatever the seeded history holds from before the 30-day floor
    left = {r["action"] for r in _rows(client)}
    assert "month.old" not in left and "recent.thing" in left
    purged = client.get("/api/audit?action=audit.purged", headers=ADMIN).json()["items"]
    assert purged[0]["actor_id"] is None and purged[0]["detail"]["removed"] == gone and purged[0]["detail"]["retention_days"] == 30


def test_the_purge_endpoint_is_off_in_the_demo(client):
    assert client.post("/internal/audit/purge").status_code == 404


def test_api_docs_keep_working_in_the_demo(client):
    r = client.get("/api/docs")
    assert r.status_code == 200 and "content-security-policy" not in r.headers


# ---------------------------------------------------------------- order, page size, dates (mentor review, 2026-11-18)

T0 = datetime(2026, 3, 10, 12, 0, tzinfo=timezone.utc)
SORT_ORG = "11111111-1111-1111-1111-111111111111"
PEOPLE = {  # names that tie once case-folded, and one that sorts first only by code point
    "aaaaaaaa-0000-0000-0000-000000000001": "Zed",
    "aaaaaaaa-0000-0000-0000-000000000002": "anna",
    "aaaaaaaa-0000-0000-0000-000000000003": "Bob",
    "aaaaaaaa-0000-0000-0000-000000000004": "bob",
}
GHOST = "aaaaaaaa-0000-0000-0000-00000000dead"  # an actor id with no user left: sorts with "System", last


def _dataset() -> list[tuple]:
    """(actor_id, action, target, created_at): ties on time, on action, on case-folded target and name, and no-person rows."""
    p = list(PEOPLE)
    return [
        (p[0], "member.added", "Alpha", T0),
        (p[1], "member.added", "alpha", T0),  # same instant as the row before: the id breaks the tie
        (None, "audit.purged", "audit_log", T0 + timedelta(minutes=1)),
        (p[2], "access.updated", "beta", T0 + timedelta(minutes=2)),
        (p[3], "access.updated", "Beta", T0 + timedelta(minutes=2)),
        (GHOST, "task.moved", "gamma", T0 + timedelta(minutes=3)),
        (p[1], "audit.viewed", "audit_log", T0 + timedelta(minutes=4)),
        (p[0], "zeta.thing", "delta", T0 + timedelta(days=1)),
        (None, "access.requested", "Delta", T0 + timedelta(days=1, minutes=1)),
        (p[2], "member.added", "epsilon", T0 + timedelta(days=2)),
        (p[3], "access.requested", "zeta", T0 + timedelta(days=2)),
        (p[1], "task.moved", "Alpha", T0 + timedelta(days=3)),
    ]


def _memory_sort_store():
    from app.adapters.memory import MemoryStore
    from app.ports import User

    store = MemoryStore()
    for uid, name in PEOPLE.items():
        store.users[uid] = User(id=uid, org_id=SORT_ORG, email=f"{name.lower()}-{uid[-1]}@example.com", name=name, role="admin")
    for i, (actor, action, target, at) in enumerate(_dataset(), start=1):
        store.audit_rows.append({"org_id": SORT_ORG, "actor_id": actor, "action": action, "target": target, "detail": {"i": i},
                                 "created_at": at, "id": i})
    store.audit_rows.append({"org_id": "org-other", "actor_id": None, "action": "member.added", "target": "Alpha", "detail": {},
                             "created_at": T0, "id": 99})  # another organisation: never in a page or a facet
    return store


def _key(row) -> tuple:
    return row["action"], row["target"], row["created_at"], row["detail"].get("i")


EXPECTED = {  # the dataset's row numbers (1-based) in each order: the rule in app/adapters/audit_store.py
    ("time", "desc"): [12, 11, 10, 9, 8, 7, 6, 5, 4, 3, 2, 1],
    ("time", "asc"): [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12],
    ("action", "asc"): [11, 9, 5, 4, 3, 7, 10, 2, 1, 12, 6, 8],
    ("action", "desc"): [8, 12, 6, 10, 2, 1, 7, 3, 5, 4, 11, 9],
    ("target", "asc"): [12, 2, 1, 7, 3, 5, 4, 9, 8, 10, 6, 11],
    ("target", "desc"): [11, 6, 10, 9, 8, 5, 4, 7, 3, 12, 2, 1],
    ("actor", "asc"): [12, 7, 2, 11, 10, 5, 4, 8, 1, 9, 6, 3],
    ("actor", "desc"): [8, 1, 11, 10, 5, 4, 12, 7, 2, 9, 6, 3],
}


@pytest.mark.parametrize("sort,order", list(EXPECTED))
def test_memory_order_follows_the_rule_with_a_stable_tiebreak(sort, order):
    store = _memory_sort_store()
    rows, total = audit_store.page(store, AuditFilter(org_id=SORT_ORG), 100, 0, sort, order)
    assert total == 12 and [r["detail"]["i"] for r in rows] == EXPECTED[(sort, order)]


@pytest.mark.parametrize("size", [25, 30, 50, 5, 7])
@pytest.mark.parametrize("sort,order", [("time", "desc"), ("actor", "asc"), ("target", "desc")])
def test_pages_of_any_size_cover_every_entry_once(size, sort, order):
    store = _memory_sort_store()
    for i in range(80):  # enough rows for several pages of 25, 30 and 50
        store.audit_rows.append({"org_id": SORT_ORG, "actor_id": list(PEOPLE)[i % 4], "action": "task.moved", "target": f"t{i % 7}",
                                 "detail": {"i": 100 + i}, "created_at": T0 + timedelta(hours=i // 3), "id": 100 + i})
    f = AuditFilter(org_id=SORT_ORG)
    whole, total = audit_store.page(store, f, 200, 0, sort, order)
    seen = []
    for offset in range(0, total, size):
        rows, t = audit_store.page(store, f, size, offset, sort, order)
        assert t == total and len(rows) == min(size, total - offset)
        seen += rows
    assert total == 92 and [_key(r) for r in seen] == [_key(r) for r in whole]


def test_exclusions_system_actor_and_bounds_in_memory():
    store = _memory_sort_store()
    page = lambda **kw: audit_store.page(store, AuditFilter(org_id=SORT_ORG, **kw), 100, 0)  # noqa: E731
    assert page(exclude_action="audit.viewed")[1] == 11
    assert page(exclude_action="access.*")[1] == 8 and page(action="access.*", exclude_action="access.requested")[1] == 2
    assert {r["detail"]["i"] for r in page(actor="system")[0]} == {3, 9}
    # since is inclusive and until exclusive, to the microsecond: the two rows at T0 + 2 min are on both edges
    edge = T0 + timedelta(minutes=2)
    assert {r["detail"]["i"] for r in page(since=edge, until=edge + timedelta(minutes=1))[0]} == {4, 5}
    assert {r["detail"]["i"] for r in page(until=edge)[0]} == {1, 2, 3}


def test_the_api_sorts_pages_and_says_where_it_is(client):
    store = _store(client)
    for i in range(70):
        store.audit(ORG, ("u-priya", "u-ravi", None)[i % 3], "sorting.check", f"target-{i:02d}", {"i": i})
    page = client.get("/api/audit?action=sorting.check&limit=25", headers=ADMIN).json()
    assert page["total"] == 70 and page["sort"] == "time" and page["order"] == "desc" and page["page"] == 1 and page["pages"] == 3
    assert [r["detail"]["i"] for r in page["items"][:3]] == [69, 68, 67]  # newest first by default
    for size, pages in ((25, 3), (30, 3), (50, 2)):
        last = client.get(f"/api/audit?action=sorting.check&limit={size}&offset={(pages - 1) * size}", headers=ADMIN).json()
        assert last["pages"] == pages and last["page"] == pages and len(last["items"]) == 70 - (pages - 1) * size
        assert last["next_offset"] is None
    oldest = client.get("/api/audit?action=sorting.check&order=asc&limit=2", headers=ADMIN).json()
    assert [r["detail"]["i"] for r in oldest["items"]] == [0, 1]
    by_target = client.get("/api/audit?action=sorting.check&sort=target&order=desc&limit=2", headers=ADMIN).json()
    assert [r["target"] for r in by_target["items"]] == ["target-69", "target-68"]
    by_actor = client.get("/api/audit?action=sorting.check&sort=actor&order=asc&limit=70", headers=ADMIN).json()["items"]
    names = [r["actor_name"] for r in by_actor]
    assert names[:24] == ["Priya (Manager)"] * 24 and names[24:47] == ["Ravi (Senior)"] * 23 and names[47:] == [None] * 23  # System last
    by_actor_desc = client.get("/api/audit?action=sorting.check&sort=actor&order=desc&limit=70", headers=ADMIN).json()["items"]
    assert by_actor_desc[0]["actor_name"] == "Ravi (Senior)" and by_actor_desc[-1]["actor_name"] is None  # last in both directions
    assert client.get("/api/audit?action=sorting.check&actor=system", headers=ADMIN).json()["total"] == 23
    assert client.get("/api/audit?sort=detail", headers=ADMIN).status_code == 422
    assert client.get("/api/audit?order=up", headers=ADMIN).status_code == 422
    viewed = client.get("/api/audit?action=audit.viewed&limit=200", headers=ADMIN).json()["items"]
    assert any(v["detail"].get("sort") == "actor" and v["detail"].get("order") == "desc" for v in viewed)  # the order is recorded


def test_dates_are_exact_instants_so_a_viewers_own_day_works(client):
    """The web app sends the viewer's local midnights as instants; a To day ends at the next local midnight."""
    store = _store(client)
    store.audit(ORG, "u-admin", "tz.early", "x")
    store.audit(ORG, "u-admin", "tz.late", "x")
    rows = _rows(client)
    # 00:10 and 23:50 on 2026-03-11 in India (UTC+05:30) are 18:40 on the 10th and 18:20 on the 11th in UTC
    next(r for r in rows if r["action"] == "tz.early")["created_at"] = datetime(2026, 3, 10, 18, 40, tzinfo=timezone.utc)
    next(r for r in rows if r["action"] == "tz.late")["created_at"] = datetime(2026, 3, 11, 18, 20, tzinfo=timezone.utc)
    ist_day = "since=2026-03-10T18:30:00.000Z&until=2026-03-11T18:30:00.000Z"
    assert {i["action"] for i in client.get(f"/api/audit?action=tz.*&{ist_day}", headers=ADMIN).json()["items"]} == {"tz.early", "tz.late"}
    offset_day = "since=2026-03-11T00:00:00%2B05:30&until=2026-03-12T00:00:00%2B05:30"  # the same day, written with an offset
    assert client.get(f"/api/audit?action=tz.*&{offset_day}", headers=ADMIN).json()["total"] == 2
    # the same calendar day in UTC (a date-only value) holds only the later one
    assert [i["action"] for i in client.get("/api/audit?action=tz.*&since=2026-03-11&until=2026-03-11", headers=ADMIN).json()["items"]] == ["tz.late"]
    assert client.get("/api/audit?action=tz.*&since=2026-03-11T18:20:00Z&until=2026-03-11T18:20:00Z", headers=ADMIN).status_code == 422


def test_csv_keeps_the_filters_and_the_order(client):
    store = _store(client)
    for i, who in enumerate(("u-ravi", "u-priya", "u-ravi", None)):
        store.audit(ORG, who, "csv.order", f"c{i}", {"i": i})
    r = client.get("/api/audit.csv?action=csv.order&sort=actor&order=asc", headers=ADMIN)
    rows = list(csv.DictReader(io.StringIO(r.text)))
    assert [x["target"] for x in rows] == ["c1", "c2", "c0", "c3"]  # Priya, Ravi (newest first within), System last
    r = client.get("/api/audit.csv?action=csv.*&exclude_action=csv.order", headers=ADMIN)
    assert list(csv.DictReader(io.StringIO(r.text))) == []
    exported = client.get("/api/audit?action=audit.exported&limit=1", headers=ADMIN).json()["items"][0]["detail"]
    assert exported["exclude_action"] == "csv.order" and "sort" not in exported  # the default order is not repeated
    assert client.get("/api/audit.csv?sort=nope", headers=ADMIN).status_code == 422


def test_facets_list_what_the_log_holds_and_are_recorded(client):
    assert client.get("/api/audit/facets", headers=MANAGER).status_code == 403
    store = _store(client)
    store.audit(ORG, None, "facet.system", "x")
    store.audit("org-other", "u-x", "facet.elsewhere", "secret")
    f = client.get("/api/audit/facets", headers=ADMIN).json()
    actions = {a["action"]: a["count"] for a in f["actions"]}
    assert actions["facet.system"] == 1 and "facet.elsewhere" not in actions
    assert [a["action"] for a in f["actions"]] == sorted(actions)
    priya = next(p for p in f["actors"] if p["id"] == "u-priya")
    assert priya["name"] == "Priya (Manager)" and priya["email"] == "priya@demo.dev" and priya["count"] >= 1
    assert f["system"] >= 1 and f["first_at"] <= f["last_at"] and f["retention_days"] >= 30
    assert client.get("/api/audit?action=audit.viewed&limit=1", headers=ADMIN).json()["items"][0]["detail"] == {"facets": True}


def test_targets_are_named_only_for_this_organisation(client):
    store = _store(client)
    store.audit(ORG, "u-admin", "names.check", "u-ravi")
    store.audit(ORG, "u-admin", "names.check", "ob-vijay:meet_team")
    store.audit(ORG, "u-admin", "names.check", "mtg-1")
    store.audit(ORG, "u-admin", "names.check", ORG)
    store.audit(ORG, "u-admin", "names.check", "not-a-thing")
    items = {i["target"]: i for i in client.get("/api/audit?action=names.check", headers=ADMIN).json()["items"]}
    assert (items["u-ravi"]["target_kind"], items["u-ravi"]["target_name"], items["u-ravi"]["target_link"]) == ("person", "Ravi (Senior)", "/people")
    ob = items["ob-vijay:meet_team"]
    assert ob["target_kind"] == "onboarding" and ob["target_name"] == "Vijay (New hire)" and ob["target_link"] == "/program/ob-vijay?tab=milestones"
    assert items["mtg-1"]["target_kind"] == "meeting" and items["mtg-1"]["target_link"] == "/meetings/mtg-1"
    assert items[ORG]["target_kind"] == "organisation"
    assert items["not-a-thing"]["target_kind"] is None and items["not-a-thing"]["target_name"] is None
    # a person of another organisation is never named
    from app.ports import User
    inner = _store(client).__dict__.get("_store", _store(client))
    inner.users["u-stranger"] = User(id="u-stranger", org_id="org-other", email="s@x.dev", name="Stranger", role="admin")
    rows = audit_store.describe_targets(store, ORG, [{"target": "u-stranger"}])
    assert rows[0]["target_kind"] is None and rows[0]["target_name"] is None


def test_lifecycle_targets_are_named(client):
    c = client.app.state.container
    item = next(a for p in c.lifecycle.programs(ORG) for a in c.lifecycle.access_items(p.onboarding_id))
    handover = c.lifecycle.handovers(ORG)[0]
    store = _store(client)
    store.audit(ORG, "u-admin", "access.updated", item.id, {"status": "granted"})
    store.audit(ORG, "u-admin", "handover.updated", handover.id)
    items = {i["action"]: i for i in client.get("/api/audit?action=*&limit=2", headers=ADMIN).json()["items"]}
    access = items["access.updated"]
    assert access["target_kind"] == "access" and access["target_name"].startswith(f"{item.system} access")
    assert access["target_link"] == f"/program/{item.onboarding_id}?tab=access"
    assert items["handover.updated"]["target_kind"] == "handover" and items["handover.updated"]["target_link"] == "/handovers"


# ---------------------------------------------------------------- snapshots: paging while each read writes an entry
def test_paging_at_the_first_pages_snapshot_never_repeats_a_row_although_every_read_writes_one(client):
    """Each read of the log writes audit.viewed, the newest entry. With views shown, newest first, page 2 read without the
    snapshot would start with page 1's last row; read at page 1's as_of it never does (security review)."""
    first = client.get("/api/audit", params={"limit": 25, "offset": 0}, headers=ADMIN).json()
    assert first["as_of"] == max(r["id"] for r in first["items"])  # read before this view's own entry was written
    pages, offset = [first], 25
    while offset < first["total"]:
        pages.append(client.get("/api/audit", params={"limit": 25, "offset": offset, "as_of": first["as_of"]}, headers=ADMIN).json())
        offset += 25
    ids = [r["id"] for p in pages for r in p["items"]]
    assert len(ids) == len(set(ids)) == first["total"]  # every entry once
    assert all(p["total"] == first["total"] and p["as_of"] == first["as_of"] for p in pages)
    newer = client.get("/api/audit", params={"limit": 5}, headers=ADMIN).json()
    assert newer["as_of"] > first["as_of"] and newer["items"][0]["action"] == "audit.viewed"  # a fresh read sees the reads since


def test_the_csv_keeps_the_snapshot(client):
    as_of = client.get("/api/audit", params={"limit": 1}, headers=ADMIN).json()["as_of"]
    _store(client).audit(ORG, "u-priya", "task.moved", "after-the-snapshot", {})
    body = client.get("/api/audit.csv", params={"as_of": as_of}, headers=ADMIN).text
    assert "after-the-snapshot" not in body
    assert "after-the-snapshot" in client.get("/api/audit.csv", headers=ADMIN).text
    assert client.get("/api/audit", params={"as_of": -1}, headers=ADMIN).status_code == 422


def test_board_entries_name_the_task_and_link_to_the_board(client):
    board = client.get("/api/onboardings/ob-vijay/board", headers=MANAGER).json()
    card = next(t for t in board["tasks"] if t["status"] == "approved")
    client.post(f"/api/onboardings/ob-vijay/board/tasks/{card['id']}/move", json={"column": "in_progress"}, headers=MANAGER)
    _store(client).audit(ORG, "u-priya", "board.task.moved", card["id"], {"from": "in_progress", "to": "approved"})  # an older entry: no title
    rows = client.get("/api/audit", params={"action": "board.task.moved", "limit": 5}, headers=ADMIN).json()["items"]
    for r in rows[:2]:
        assert r["target_kind"] == "task" and r["target_link"] == "/board/ob-vijay"
        assert r["target_name"].startswith(card["title"]) and r["target_name"].endswith("'s board")
    ms = client.get("/api/audit", params={"action": "board.milestone.*", "limit": 5}, headers=ADMIN).json()["items"]
    assert ms and all(r["target_kind"] == "onboarding" and r["target_link"].startswith("/board/") for r in ms)


# ---------------------------------------------------------------- Postgres (TEST_PG_ADMIN_URL, as tests/test_postgres.py)

ADMIN_URL = os.environ.get("TEST_PG_ADMIN_URL")


@pytest.fixture(scope="module")
def pg():
    if not ADMIN_URL:
        pytest.skip("set TEST_PG_ADMIN_URL to run the Postgres integration tests")
    import psycopg

    from app import migrate
    from app.adapters.postgres import PgStore, make_engine

    name = f"ww_it_{uuid.uuid4().hex[:8]}"
    owner = f"own_{name}"
    host = ADMIN_URL.rsplit("/", 1)[0].split("@", 1)[1]
    with psycopg.connect(ADMIN_URL, autocommit=True) as c:
        for (old,) in c.execute("SELECT datname FROM pg_database WHERE datname LIKE 'ww\\_it\\_%'").fetchall():
            c.execute(f'DROP DATABASE "{old}" WITH (FORCE)')
        for (role,) in c.execute("SELECT rolname FROM pg_roles WHERE rolname IN ('ww_app', 'ww_definer') OR rolname LIKE 'own\\_ww\\_it\\_%' "
                                 "ORDER BY rolname LIKE 'own%'").fetchall():
            c.execute(f'DROP OWNED BY "{role}" CASCADE')
            c.execute(f'DROP ROLE "{role}"')
        c.execute(f"CREATE ROLE {owner} LOGIN CREATEROLE NOSUPERUSER PASSWORD 'owner-pw'")
        c.execute(f"CREATE DATABASE {name} OWNER {owner}")
    with psycopg.connect(ADMIN_URL.rsplit("/", 1)[0] + f"/{name}", autocommit=True) as c:
        c.execute("CREATE EXTENSION vector; CREATE EXTENSION pgcrypto")
    applied = migrate.run(f"postgresql://{owner}:owner-pw@{host}/{name}", "app-pw")
    assert "012_audit_retention" in applied
    engine = make_engine(f"postgresql+psycopg://ww_app:app-pw@{host}/{name}")
    owner_url = f"postgresql://{owner}:owner-pw@{host}/{name}"
    yield PgStore(engine), owner_url
    engine.dispose()
    with psycopg.connect(ADMIN_URL, autocommit=True) as c:
        c.execute(f"DROP DATABASE {name} WITH (FORCE)")
        c.execute(f"DROP OWNED BY {owner} CASCADE")
        c.execute(f"DROP ROLE {owner}")


def _pg_org(store, label):
    from app.adapters.postgres import current_org

    current_org.set(None)
    uid = f"uid-{label}-{uuid.uuid4().hex[:6]}"
    user = store.create_organization(uid, f"{label}@example.com", f"{label} admin", f"{label} Inc", f"{label} project")
    return user


def test_postgres_reads_one_organisation_and_purges_across_all_with_a_floor(pg):
    import psycopg

    from app.adapters.postgres import current_org

    store, owner_url = pg
    a, b = _pg_org(store, "acme"), _pg_org(store, "globex")
    for user, n in ((a, 3), (b, 2)):
        current_org.set(user.org_id)
        for i in range(n):
            store.audit(user.org_id, user.id, "task.moved", f"t{i}", {"i": i})
    current_org.set(a.org_id)
    f = AuditFilter(org_id=a.org_id, action="task.*")
    rows, total = audit_store.page(store, f, 2, 0)
    assert total == 3 and [r["target"] for r in rows] == ["t2", "t1"] and rows[0]["actor_email"] == "acme@example.com"
    assert audit_store.page(store, AuditFilter(org_id=a.org_id, actor="ACME@example.com", target="t0"), 10, 0)[1] == 1
    assert audit_store.page(store, AuditFilter(org_id=b.org_id), 10, 0) == ([], 0)  # RLS: acme cannot see globex's rows
    assert audit_store.page(store, AuditFilter(org_id=a.org_id, actor="not-a-uuid"), 10, 0) == ([], 0)
    assert len(list(audit_store.iterate(store, f, cap=10, batch=2))) == 3

    with psycopg.connect(owner_url, autocommit=True) as c:  # back-date some rows, as time would
        c.execute("UPDATE audit_log SET created_at = now() - interval '400 days' WHERE target = 't0'")
        c.execute("UPDATE audit_log SET created_at = now() - interval '10 days' WHERE target = 't1'")
    current_org.set(None)
    removed = audit_store.purge(store, datetime.now(timezone.utc))  # asks for everything: only rows past 30 days go
    assert removed == {a.org_id: 1, b.org_id: 1}
    for user in (a, b):
        audit_store.write_as(store, user.org_id, None, "audit.purged", "audit_log", {"removed": 1})
    current_org.set(a.org_id)
    left = {r["target"] for r in audit_store.page(store, AuditFilter(org_id=a.org_id), 50, 0)[0]}
    assert "t0" not in left and {"t1", "t2", "audit_log"} <= left
    current_org.set(None)


def _pg_sort_org(store, owner_url):
    """An organisation in Postgres holding the same people and entries as _memory_sort_store (same ids, same times, inserted
    in the same order, so the id tiebreak agrees)."""
    import psycopg

    from app.adapters.postgres import current_org

    admin = _pg_org(store, f"sort{uuid.uuid4().hex[:4]}")
    org = admin.org_id
    ids = {uid: str(uuid.uuid4()) for uid in PEOPLE}
    with psycopg.connect(owner_url, autocommit=True) as c:
        c.execute("SELECT set_config('app.org_id', %s, false)", (org,))
        for uid, name in PEOPLE.items():
            c.execute("INSERT INTO users (id, org_id, email, name, role) VALUES (%s, %s, %s, %s, 'expert')",
                      (ids[uid], org, f"{name.lower()}-{ids[uid][:6]}@example.com", name))
    current_org.set(org)
    for i, (actor, action, target, at) in enumerate(_dataset(), start=1):
        store.run("INSERT INTO audit_log (org_id, actor_id, action, target, detail, created_at) "
                  "VALUES (:o, :a, :ac, :t, CAST(:d AS jsonb), :at)",
                  o=org, a=ids.get(actor, actor), ac=action, t=target, d=f'{{"i": {i}}}', at=at)
    return admin, ids


def test_postgres_orders_pages_and_filters_exactly_as_memory(pg):
    from app.adapters.postgres import current_org

    store, owner_url = pg
    admin, ids = _pg_sort_org(store, owner_url)
    org = admin.org_id
    current_org.set(org)
    try:
        f = lambda **kw: AuditFilter(org_id=org, since=T0 - timedelta(days=1), until=T0 + timedelta(days=4), **kw)  # noqa: E731
        for (sort, order), expected in EXPECTED.items():
            rows, total = audit_store.page(store, f(), 100, 0, sort, order)
            assert total == 12 and [r["detail"]["i"] for r in rows] == expected, (sort, order)
            for size in (5, 7):  # paging never repeats or skips an entry
                seen = []
                for offset in range(0, total, size):
                    seen += audit_store.page(store, f(), size, offset, sort, order)[0]
                assert [r["detail"]["i"] for r in seen] == expected, (sort, order, size)
        assert audit_store.page(store, f(exclude_action="audit.viewed"), 100, 0)[1] == 11
        assert audit_store.page(store, f(exclude_action="access.*"), 100, 0)[1] == 8
        assert {r["detail"]["i"] for r in audit_store.page(store, f(actor="system"), 100, 0)[0]} == {3, 9}
        edge = T0 + timedelta(minutes=2)
        assert {r["detail"]["i"] for r in audit_store.page(store, AuditFilter(org_id=org, since=edge, until=edge + timedelta(minutes=1)), 100, 0)[0]} == {4, 5}
        assert {r["detail"]["i"] for r in audit_store.page(store, AuditFilter(org_id=org, since=T0 - timedelta(days=1), until=edge), 100, 0)[0]} == {1, 2, 3}
        exported = list(audit_store.iterate(store, f(), cap=100, batch=5, sort="actor", order="desc"))
        assert [r["detail"]["i"] for r in exported] == EXPECTED[("actor", "desc")]
        assert len(list(audit_store.iterate(store, f(), cap=4, batch=3))) == 4

        facets = audit_store.facets(store, org)
        counts = {a["action"]: a["count"] for a in facets["actions"]}
        assert counts["access.updated"] == 2 and counts["member.added"] == 3 and "organization.created" in counts
        assert [a["action"] for a in facets["actions"]] == sorted(counts)
        assert facets["system"] == 2 and {p["name"] for p in facets["actors"]} >= set(PEOPLE.values())
        assert facets["first_at"] is not None and facets["first_at"] <= facets["last_at"]
    finally:
        current_org.set(None)


def test_postgres_names_targets_of_its_own_organisation_only(pg):
    from datetime import date

    from app.adapters.postgres import current_org

    store, _ = pg
    a, b = _pg_org(store, "names"), _pg_org(store, "other")
    current_org.set(a.org_id)
    try:
        project = store.projects_for(a)[0]
        ob = store.create_onboarding(project.id, a.id, a.id, date(2026, 3, 1))
        meeting = store.create_meeting(project.id, "KT: the policy engine", a.id)
        rows = [{"target": t} for t in (a.id, f"{ob.id}:meet_team", meeting.id, project.id, a.org_id, b.id, "x", "not-a-uuid:1")]
        audit_store.describe_targets(store, a.org_id, rows)
        kinds = [(r["target_kind"], r["target_name"]) for r in rows]
        assert kinds[0] == ("person", a.name) and kinds[1] == ("onboarding", a.name) and kinds[2] == ("meeting", "KT: the policy engine")
        assert kinds[3] == ("project", project.name) and kinds[4] == ("organisation", None)
        assert kinds[5:] == [(None, None)] * 3  # another organisation's person, and ids that are not ids
        assert rows[1]["target_link"] == f"/program/{ob.id}?tab=milestones" and rows[2]["target_link"] == f"/meetings/{meeting.id}"
    finally:
        current_org.set(None)


def test_postgres_keeps_a_page_snapshot(pg):
    from app.adapters.postgres import current_org

    store, _ = pg
    a = _pg_org(store, "snap")
    current_org.set(a.org_id)
    try:
        for i in range(6):
            store.audit(a.org_id, a.id, "task.moved", f"s{i}", {"i": i})
        as_of = audit_store.newest_id(store, a.org_id)
        store.audit(a.org_id, a.id, "audit.viewed", "audit_log", {})  # written between two pages
        f = AuditFilter(org_id=a.org_id, max_id=as_of)
        first, total = audit_store.page(store, f, 3, 0)
        store.audit(a.org_id, a.id, "audit.viewed", "audit_log", {})
        second, total2 = audit_store.page(store, f, 3, 3)
        ids = [r["id"] for r in first + second]
        assert total == total2 and len(ids) == len(set(ids)) and all(i <= as_of for i in ids)
        assert all(r["action"] != "audit.viewed" for r in first + second if r["target"].startswith("s"))
        assert audit_store.newest_id(store, a.org_id) > as_of and audit_store.newest_id(store, "not-a-uuid") is None
        assert len(list(audit_store.iterate(store, f, cap=100))) == total
    finally:
        current_org.set(None)
