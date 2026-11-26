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
