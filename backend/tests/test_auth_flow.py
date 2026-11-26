"""Production sign-in over HTTP: verified tokens, first sign-in of an invited person, self-serve sign-up.
Only Firebase's token verification is stubbed; everything after it is the real code path."""
from datetime import date

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import create_app

TOKENS = {
    "tok-new": {"uid": "fb-new", "email": "founder@acme.dev", "email_verified": True, "name": "Fatima"},
    "tok-unverified": {"uid": "fb-unv", "email": "someone@acme.dev", "email_verified": False},
    "tok-invited": {"uid": "fb-inv", "email": "hire@acme.dev", "email_verified": True, "name": "Hari"},
}


@pytest.fixture
def client(monkeypatch):
    get_settings.cache_clear()
    with TestClient(create_app()) as c:
        c.app.state.container.settings.mode = "gcp"  # take the production auth path; data stays in memory

        def verify(token, clock_skew_seconds=0):
            if token not in TOKENS:
                raise ValueError("bad token")
            return TOKENS[token]

        import firebase_admin.auth

        monkeypatch.setattr(firebase_admin.auth, "verify_id_token", verify)

        _fake = verify

        monkeypatch.setattr("app.firebase_tokens.verify", lambda tok, *_a, **_k: _fake(tok))  # real Firebase path
        yield c
        c.app.state.container.settings.mode = "demo"


def bearer(tok):
    return {"Authorization": f"Bearer {tok}"}


def test_requests_without_a_valid_token_are_refused(client):
    assert client.get("/api/me").status_code == 401
    assert client.get("/api/me", headers=bearer("forged")).status_code == 401
    assert client.get("/api/me", headers={"X-Demo-User": "u-priya"}).status_code == 401  # the demo header means nothing here


def test_signup_creates_an_organisation_and_admin(client):
    r = client.get("/api/me", headers=bearer("tok-new"))
    assert r.status_code == 403 and r.json()["detail"]["code"] == "no_account"
    r = client.post("/api/signup", json={"name": "Fatima", "org_name": "Acme", "project_name": "Payments"}, headers=bearer("tok-new"))
    assert r.status_code == 201
    me = r.json()
    assert me["role"] == "admin" and me["org_name"] == "Acme" and [p["name"] for p in me["projects"]] == ["Payments"]
    assert client.post("/api/signup", json={"name": "F", "org_name": "Again", "project_name": "x"}, headers=bearer("tok-new")).status_code == 409

    # the admin invites a new hire; on first sign-in the hire SEES the invitation and chooses to join (never silently)
    pid = me["projects"][0]["id"]
    assert client.post(f"/api/projects/{pid}/invitations", json={"email": "hire@acme.dev", "role": "new_hire"}, headers=bearer("tok-new")).status_code == 201
    r = client.get("/api/me", headers=bearer("tok-invited"))
    detail = r.json()["detail"]
    assert r.status_code == 403 and detail["code"] == "invitation_pending"
    assert detail["invitations"] == [{"org_id": me["org_id"], "org_name": "Acme", "role": "new_hire", "invited_by_name": "Fatima",
                                      "invited_by_email": "founder@acme.dev", "org_created_at": date.today().isoformat(),
                                      "projects": ["Payments"]}]
    hire = client.post("/api/invitations/accept", json={"org_id": me["org_id"]}, headers=bearer("tok-invited")).json()
    assert hire["role"] == "new_hire" and hire["org_id"] == me["org_id"] and [p["id"] for p in hire["projects"]] == [pid]
    assert client.get("/api/me", headers=bearer("tok-invited")).status_code == 200
    # and cannot see the demo organisation's project
    assert client.get("/api/projects/proj-demo", headers=bearer("tok-invited")).status_code == 404


def test_unverified_email_cannot_sign_up_or_accept(client):
    r = client.get("/api/me", headers=bearer("tok-unverified"))
    assert r.status_code == 403 and r.json()["detail"]["code"] == "email_not_verified"
    r = client.post("/api/signup", json={"name": "x", "org_name": "y", "project_name": "z"}, headers=bearer("tok-unverified"))
    assert r.status_code == 403


def test_expired_invitation_never_sets_the_role(client):
    from datetime import datetime, timedelta, timezone

    store = client.app.state.container.store
    stale = store.create_invitation("org-demo", "proj-demo", "late@acme.dev", "admin", None, None, "u-priya")
    stale["expires_at"] = datetime.now(timezone.utc) - timedelta(days=1)  # an old admin invite that lapsed
    store.create_invitation("org-demo", "proj-demo", "late@acme.dev", "expert", None, None, "u-priya")
    TOKENS["tok-late"] = {"uid": "fb-late", "email": "late@acme.dev", "email_verified": True}
    pending = client.get("/api/me", headers=bearer("tok-late")).json()["detail"]["invitations"]
    assert [p["role"] for p in pending] == ["expert"]
    me = client.post("/api/invitations/accept", json={"org_id": "org-demo"}, headers=bearer("tok-late")).json()
    assert me["role"] == "expert"


def test_invitations_from_two_organisations_are_a_choice(client):
    store = client.app.state.container.store
    from app.ports import Project

    store.add_project(Project(id="proj-evil", org_id="org-evil", name="Phish", description=""))
    store.orgs["org-evil"] = "Evil Corp"
    store.create_invitation("org-evil", "proj-evil", "both@acme.dev", "manager", None, None, None)
    store.create_invitation("org-demo", "proj-demo", "both@acme.dev", "new_hire", None, None, "u-priya")
    TOKENS["tok-both"] = {"uid": "fb-both", "email": "both@acme.dev", "email_verified": True}
    pending = client.get("/api/me", headers=bearer("tok-both")).json()["detail"]["invitations"]
    assert {p["org_name"] for p in pending} == {"Evil Corp", "Mini Policy Engine"}
    me = client.post("/api/invitations/accept", json={"org_id": "org-demo"}, headers=bearer("tok-both")).json()
    assert me["org_id"] == "org-demo" and me["role"] == "new_hire"
    assert client.post("/api/invitations/accept", json={"org_id": "org-evil"}, headers=bearer("tok-both")).status_code == 409
