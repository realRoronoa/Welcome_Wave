"""Legal acceptances on PostgreSQL (db/migrations/019_legal.sql), as ww_app under row-level security: an acceptance is
recorded once per person, document and version, and once per organisation and version for the data-processing agreement;
another organisation sees and writes none of it; the app role can add and read rows but never change or remove one; the
organisation's export holds them and its deletion removes them (through delete_org_legal, and only then); erasing a person
leaves their rows; and the API records them over HTTP with the tenant set. Skipped without TEST_PG_ADMIN_URL (run it through
pg_tests.sh)."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import psycopg
import pytest
from sqlalchemy.exc import DBAPIError, IntegrityError, ProgrammingError

from app import legal, migrate
from app.adapters.lifecycle_pg import PgLifecycleStore
from app.adapters.postgres import current_org
from app.lifecycle import orgdata
from tests.test_lifecycle_postgres import _container
from tests.test_postgres import ADMIN, OWNER_URL, _org, db, engine, store

FIXTURES = (db, engine, store)  # the Postgres fixtures, reused here by name

pytestmark = pytest.mark.skipif(not ADMIN, reason="set TEST_PG_ADMIN_URL to run the Postgres integration tests")
V = legal.VERSIONS


@pytest.fixture
def lc(engine):
    return PgLifecycleStore(engine)


def test_an_acceptance_is_recorded_once_and_read_back(store):
    admin, _ = _org(store, "la")
    row = store.record_legal_acceptance(admin.org_id, admin.id, "terms", V["terms"], "signup", "203.0.113.0/24", "Mozilla/5.0 (Test)")
    assert row["id"] and row["document"] == "terms" and row["version"] == V["terms"] and row["method"] == "signup"
    assert (row["org_id"], row["user_id"], row["ip_prefix"], row["user_agent"]) == (admin.org_id, admin.id, "203.0.113.0/24", "Mozilla/5.0 (Test)")
    assert isinstance(row["accepted_at"], datetime) and abs(row["accepted_at"] - datetime.now(timezone.utc)) < timedelta(minutes=1)
    assert store.record_legal_acceptance(admin.org_id, admin.id, "terms", V["terms"], "prompt") is None  # the first one stands
    assert store.record_legal_acceptance(admin.org_id, admin.id, "privacy", V["privacy"], "signup") is not None
    assert store.record_legal_acceptance(admin.org_id, admin.id, "terms", "2027-03-01", "prompt") is not None  # a new version is a new row
    rows = store.legal_acceptances(admin.org_id, user_id=admin.id)
    assert [(r["document"], r["version"]) for r in rows] == [("terms", "2027-03-01"), ("privacy", V["privacy"]), ("terms", V["terms"])]  # newest first
    assert [r["version"] for r in store.legal_acceptances(admin.org_id, document="terms")] == ["2027-03-01", V["terms"]]
    assert store.legal_acceptances(admin.org_id, user_id=str(uuid.uuid4())) == [] and store.legal_acceptances("nope") == []
    assert store.record_legal_acceptance("nope", admin.id, "terms", V["terms"], "signup") is None
    past = datetime(2026, 1, 2, 3, 4, tzinfo=timezone.utc)
    assert store.record_legal_acceptance(admin.org_id, admin.id, "recording", V["recording"], "prompt", at=past)["accepted_at"] == past


def test_the_dpa_is_one_row_per_organisation_and_version(store, lc):
    admin, _ = _org(store, "ld")
    second = lc.ensure_user(admin.org_id, f"second-{uuid.uuid4().hex[:6]}@example.com", "Second", None)
    assert store.record_legal_acceptance(admin.org_id, admin.id, "dpa", V["dpa"], "organisation") is not None
    assert store.record_legal_acceptance(admin.org_id, second.id, "dpa", V["dpa"], "organisation") is None  # another admin, same version
    [row] = store.legal_acceptances(admin.org_id, document="dpa")
    assert row["user_id"] == admin.id
    # the same person may accept the same version of a personal document only once, but two people each have their own
    assert store.record_legal_acceptance(admin.org_id, admin.id, "terms", V["terms"], "signup") is not None
    assert store.record_legal_acceptance(admin.org_id, second.id, "terms", V["terms"], "invitation") is not None
    assert len(store.legal_acceptances(admin.org_id, document="terms")) == 2


def test_the_table_takes_only_what_the_code_writes(store):
    admin, _ = _org(store, "lk")
    for bad in ({"document": "cookies"}, {"version": "latest"}, {"method": "magic"}, {"user_agent": "x" * 201}, {"ip_prefix": "9" * 65}):
        args = {"document": "terms", "version": V["terms"], "method": "signup", "ip_prefix": None, "user_agent": None, **bad}
        with pytest.raises(IntegrityError):
            store.record_legal_acceptance(admin.org_id, admin.id, **args)
    assert store.legal_acceptances(admin.org_id) == []


def test_another_organisation_sees_and_writes_none_of_it(store):
    a, _ = _org(store, "lx")
    assert store.record_legal_acceptance(a.org_id, a.id, "terms", V["terms"], "signup") is not None
    b, _ = _org(store, "ly")  # the tenant is now b's
    assert store.legal_acceptances(a.org_id) == [] and store.legal_acceptances(a.org_id, user_id=a.id) == []
    with pytest.raises(DBAPIError):  # row-level security: a row for another organisation cannot be written
        store.record_legal_acceptance(a.org_id, a.id, "privacy", V["privacy"], "signup")
    assert store.record_legal_acceptance(b.org_id, b.id, "terms", V["terms"], "signup") is not None
    assert [r["user_id"] for r in store.legal_acceptances(b.org_id)] == [b.id]
    current_org.set(None)  # no tenant: nothing is visible
    assert store.legal_acceptances(a.org_id) == [] and store.legal_acceptances(b.org_id) == []
    current_org.set(a.org_id)
    assert [r["document"] for r in store.legal_acceptances(a.org_id)] == ["terms"]


def test_the_app_role_can_never_change_or_remove_an_acceptance(store):
    admin, _ = _org(store, "lo")
    store.record_legal_acceptance(admin.org_id, admin.id, "terms", V["terms"], "signup")
    for statement in ("UPDATE legal_acceptances SET version = '2020-01-01'", "UPDATE legal_acceptances SET accepted_at = now()",
                      "DELETE FROM legal_acceptances", "TRUNCATE legal_acceptances"):
        with pytest.raises(ProgrammingError) as e:
            store.run(statement)
        assert "permission denied" in str(e.value)
    with pytest.raises(DBAPIError):  # and not through the definer function either, while the organisation exists
        store.one("SELECT delete_org_legal(CAST(:o AS uuid))", o=admin.org_id)
    assert [(r["document"], r["version"]) for r in store.legal_acceptances(admin.org_id)] == [("terms", V["terms"])]


def test_the_migrator_keeps_it_append_only_and_tenant_protected(db):
    assert "legal_acceptances" in migrate.APPEND_ONLY_TABLES and "delete_org_legal(uuid)" in migrate.DEFINER_FUNCTIONS
    with psycopg.connect(OWNER_URL["url"], autocommit=True) as c:
        migrate.verify(c)
        assert migrate.protection_problems(c) == []
        try:  # a grant that slipped in (a careless GRANT ALL) stops the next deploy
            c.execute("GRANT UPDATE ON legal_acceptances TO ww_app")
            with pytest.raises(SystemExit) as e:
                migrate.verify(c)
            assert "legal_acceptances: UPDATE" in str(e.value)
        finally:
            migrate.grant_app(c)  # what every run of the migrator does: grant the data privileges, then revoke these again
        migrate.verify(c)
        owner = c.execute("SELECT pg_get_userbyid(proowner) FROM pg_proc WHERE proname = 'delete_org_legal'").fetchone()[0]
        assert owner == "ww_definer"
        assert not c.execute("SELECT has_function_privilege('public', 'delete_org_legal(uuid)', 'EXECUTE')").fetchone()[0]


def test_erasing_a_person_leaves_their_acceptances(store, lc):
    admin, _ = _org(store, "le")
    hire = lc.ensure_user(admin.org_id, f"hire-{uuid.uuid4().hex[:6]}@example.com", "Nia", None)
    for d in legal.PERSONAL:
        store.record_legal_acceptance(admin.org_id, hire.id, d, V[d], "invitation", "198.51.100.0/24", "Mozilla/5.0")
    before = store.legal_acceptances(admin.org_id, user_id=hire.id)
    store.erase_person(admin.org_id, hire.id)
    assert store.get_user(hire.id).name == "Erased person"
    assert store.legal_acceptances(admin.org_id, user_id=hire.id) == before and len(before) == 3
    assert hire.email not in str(before) and "Nia" not in str(before)  # the rows never held a name or an address


def test_export_holds_the_acceptances_and_deleting_the_organisation_removes_only_its_own(engine, store, lc):
    a, _ = _org(store, "lz")
    for d in legal.PERSONAL:
        store.record_legal_acceptance(a.org_id, a.id, d, V[d], "signup", "203.0.113.0/24", "Mozilla/5.0")
    store.record_legal_acceptance(a.org_id, a.id, "dpa", V["dpa"], "organisation")
    b, _ = _org(store, "lw")
    store.record_legal_acceptance(b.org_id, b.id, "terms", V["terms"], "signup")
    current_org.set(a.org_id)
    c = _container(engine, store, lc)
    c.checkpointer = None

    data = orgdata.export(c, a.org_id)
    rows = data["legal_acceptances"]
    assert {r["document"] for r in rows} == {"terms", "privacy", "recording", "dpa"} and {r["org_id"] for r in rows} == {a.org_id}
    assert {r["user_id"] for r in rows} == {a.id} and all(r["ip_prefix"] in ("203.0.113.0/24", None) for r in rows)
    assert all(isinstance(r["accepted_at"], str) for r in rows)  # JSON-ready

    counts = orgdata.delete(c, a.org_id)
    assert counts["legal_acceptances"] == 4
    assert store.legal_acceptances(a.org_id) == []
    current_org.set(None)
    superuser = ADMIN.rsplit("/", 1)[0] + "/" + OWNER_URL["url"].rsplit("/", 1)[1]  # past row-level security: what is really there
    with psycopg.connect(superuser, autocommit=True) as su:
        left = dict(su.execute("SELECT org_id::text, count(*) FROM legal_acceptances WHERE org_id IN (%s, %s) GROUP BY 1",
                               (a.org_id, b.org_id)).fetchall())
    assert left == {b.org_id: 1}
    current_org.set(b.org_id)
    assert [r["document"] for r in store.legal_acceptances(b.org_id)] == ["terms"]  # the other organisation's stays
    with pytest.raises(DBAPIError):  # the function removes the CURRENT organisation's rows only, and only once it is gone
        store.one("SELECT delete_org_legal(CAST(:o AS uuid))", o=a.org_id)
    assert [r["document"] for r in store.legal_acceptances(b.org_id)] == ["terms"]


def test_the_api_records_acceptances_on_postgres_with_the_tenant_set(engine, store, lc, monkeypatch):
    """Sign-up, joining, the prompt and the DPA over HTTP as ww_app: the acceptance of a brand new organisation is written
    under that organisation's row-level security (a tenant that never reached the insert would fail it)."""
    import firebase_admin.auth
    from fastapi.testclient import TestClient

    from app.config import Settings, get_settings
    from app.main import create_app

    tag = uuid.uuid4().hex[:6]
    tokens = {f"t-{who}": {"uid": f"fb-{who}-{tag}", "email": f"{who}-{tag}@example.com", "email_verified": True, "name": who.title()}
              for who in ("founder", "hire", "rival")}
    verify = lambda tok, *_a, **_k: tokens[tok]
    monkeypatch.setattr(firebase_admin.auth, "verify_id_token", verify)
    monkeypatch.setattr("app.firebase_tokens.verify", verify)
    legal._satisfied.forget()
    get_settings.cache_clear()
    with TestClient(create_app(), client=("203.0.113.77", 50000)) as client:
        c = _container(engine, store, lc)
        c.settings = c.deps.settings = Settings(mode="gcp", agent_retries=1, retry_sleep_s=0, legal_enforce=True, _env_file=None)
        client.app.state.container = c
        H = {k: {"Authorization": f"Bearer {k}", "User-Agent": "Mozilla/5.0 (Postgres)"} for k in tokens}
        body = {"name": "Fatima", "org_name": f"Acme {tag}", "project_name": "Payments"}

        r = client.post("/api/signup", json=body, headers=H["t-founder"])
        assert r.status_code == 422 and r.json()["detail"]["code"] == "legal_required"
        me = client.post("/api/signup", json={**body, "accept": ["terms", "privacy"]}, headers=H["t-founder"]).json()
        pid = me["projects"][0]["id"]
        status = client.get("/api/legal/status", headers=H["t-founder"]).json()
        assert status["needs"] == ["recording"] and {a["document"] for a in status["accepted"]} == {"terms", "privacy"}
        assert status["enforced"] is True and client.get(f"/api/projects/{pid}", headers=H["t-founder"]).status_code == 200

        assert client.post(f"/api/projects/{pid}/invitations", json={"email": tokens["t-hire"]["email"], "role": "new_hire"},
                           headers=H["t-founder"]).status_code == 201
        assert client.post("/api/invitations/accept", json={"org_id": me["org_id"]}, headers=H["t-hire"]).status_code == 422
        hire = client.post("/api/invitations/accept", json={"org_id": me["org_id"], "accept": ["terms", "privacy", "recording"]},
                           headers=H["t-hire"]).json()
        assert client.get("/api/legal/status", headers=H["t-hire"]).json()["needs"] == []
        assert client.post("/api/org/legal/dpa", headers=H["t-hire"]).status_code == 403

        assert client.post("/api/legal/accept", json={"documents": ["recording"]}, headers=H["t-founder"]).json()["recorded"] == ["recording"]
        dpa = client.post("/api/org/legal/dpa", json={}, headers=H["t-founder"]).json()
        assert dpa["recorded"] is True and dpa["dpa"]["accepted_by"] == {"id": me["id"], "name": "Fatima", "email": tokens["t-founder"]["email"]}
        record = client.get("/api/org/legal/dpa/record", headers=H["t-founder"])
        assert record.status_code == 200 and f"Acme {tag}" in record.text and "203.0.113.0/24" in record.text and "203.0.113.77" not in record.text

        # another organisation: its own acceptances, none of the first one's
        rival = client.post("/api/signup", json={"name": "R", "org_name": f"Rival {tag}", "project_name": "X", "accept": ["terms", "privacy"]},
                            headers=H["t-rival"]).json()
        assert client.get("/api/org/legal", headers=H["t-rival"]).json()["dpa"]["accepted"] is False
        assert client.get("/api/org/legal/dpa/record", headers=H["t-rival"]).status_code == 404

        current_org.set(me["org_id"])
        rows = store.legal_acceptances(me["org_id"])
        assert {(r["user_id"] == me["id"], r["document"], r["method"]) for r in rows} == {
            (True, "terms", "signup"), (True, "privacy", "signup"), (True, "recording", "prompt"), (True, "dpa", "organisation"),
            (False, "terms", "invitation"), (False, "privacy", "invitation"), (False, "recording", "invitation")}
        assert {r["user_id"] for r in rows} == {me["id"], hire["id"]}
        assert {(r["ip_prefix"], r["user_agent"]) for r in rows} == {("203.0.113.0/24", "Mozilla/5.0 (Postgres)")}
        audits = store.rows("SELECT actor_id::text AS actor, action, detail FROM audit_log WHERE action LIKE 'legal.%' ORDER BY id")
        assert [(a["actor"] == me["id"], a["action"], a["detail"].get("method")) for a in audits] == [
            (True, "legal.accepted", "signup"), (False, "legal.accepted", "invitation"), (True, "legal.accepted", "prompt"),
            (True, "legal.dpa_accepted", "organisation")]
        current_org.set(rival["org_id"])
        assert {r["user_id"] for r in store.legal_acceptances(rival["org_id"])} == {rival["id"]}
    get_settings.cache_clear()
    legal._satisfied.forget()
