"""Regression tests for the third review round, API side: re-asking for a plan, the outbox, the GitHub callback,
leaving an organisation."""
import hashlib
import hmac
import json
from datetime import datetime, timezone

import httpx
import pytest
from fastapi.testclient import TestClient

import app.adapters.github as gh
import app.agents.task as task_mod
from app.adapters.github import GitHubApp, events_from_webhook
from app.api.outbox import flush
from app.config import Settings, get_settings
from app.main import create_app
from app.ports import Project, User

PID = "proj-demo"
MANAGER = {"X-Demo-User": "u-priya"}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("AGENT_RETRIES", "1")
    monkeypatch.setenv("RETRY_SLEEP_S", "0")
    get_settings.cache_clear()
    with TestClient(create_app()) as c:
        yield c
    get_settings.cache_clear()


def _hire(c, uid="u-new"):
    store = c.app.state.container.store
    store.add_user(User(id=uid, org_id="org-demo", email=f"{uid}@demo.dev", name="Nia", role="new_hire"), PID)
    return uid


def _start(c, hire):
    return c.post(f"/api/projects/{PID}/onboardings", json={"new_hire_id": hire, "start_date": "2026-11-02"}, headers=MANAGER)


# ---------------- "ask for the plan again" (auth-api#0) ----------------
def test_asking_again_after_a_failed_plan_really_runs_the_task_agent(client, monkeypatch):
    calls, real = [], task_mod.plan_for

    def flaky(deps, ob, items=None):
        calls.append(ob.id)
        if len(calls) == 1:
            raise RuntimeError("Vertex AI is down")
        return real(deps, ob, items)

    monkeypatch.setattr(task_mod, "plan_for", flaky)
    store = client.app.state.container.store
    hire = _hire(client)
    ob = _start(client, hire).json()
    assert calls == [ob["id"]] and store.tasks(onboarding_id=ob["id"]) == []  # the run escalated and its key is done

    again = _start(client, hire)
    assert again.status_code == 202 and again.json()["id"] == ob["id"]
    assert len(calls) == 2 and store.tasks(onboarding_id=ob["id"])  # the same key ran again: a plan exists now
    r = _start(client, hire)
    assert r.status_code == 409 and "waiting for your approval" in r.json()["detail"]


def test_asking_again_while_the_first_run_is_going_is_refused(client, monkeypatch):
    monkeypatch.setattr(task_mod, "plan_for", lambda *a, **k: [])  # proposes nothing: no tasks, key done
    store = client.app.state.container.store
    ob = _start(client, _hire(client)).json()
    store.reset_event(f"ob:{ob['id']}")
    store.claim_event(f"ob:{ob['id']}", 300)  # a worker is planning right now
    r = _start(client, "u-new")
    assert r.status_code == 409 and r.json() == {"detail": "plan_in_progress"}


def test_a_plan_the_manager_rejected_can_be_asked_for_again(client):
    store = client.app.state.container.store
    ob = _start(client, _hire(client)).json()
    approval = next(a for a in store.approvals(PID) if a.kind == "task_plan" and any(p["onboarding_id"] == ob["id"] for p in a.payload["plans"]))
    assert client.post(f"/api/approvals/{approval.id}/decide", json={"approve": []}, headers=MANAGER).status_code == 200
    assert {t.status for t in store.tasks(onboarding_id=ob["id"])} == {"rejected"}
    assert _start(client, "u-new").status_code == 202
    assert any(t.status == "proposed" for t in store.tasks(onboarding_id=ob["id"]))


def test_deciding_before_the_run_paused_is_still_preparing(client, monkeypatch):
    from app.container import ApprovalNotReady

    c = client.app.state.container
    approval = next(a for a in c.store.approvals(PID) if a.kind == "task_plan")

    def not_ready(a, decision):
        raise ApprovalNotReady(a.id)

    monkeypatch.setattr(c, "decide", not_ready)
    r = client.post(f"/api/approvals/{approval.id}/decide", json={"approve": [], "decision": "approved"}, headers=MANAGER)
    assert r.status_code == 409 and r.json() == {"detail": "still_preparing"}


def test_a_superseded_approval_says_a_newer_one_is_waiting(client):
    store = client.app.state.container.store
    approval = next(a for a in store.approvals(PID) if a.kind == "task_plan")
    store.decide_approval(approval.id, "rejected", None)  # what supersede_review_approvals leaves behind
    r = client.post(f"/api/approvals/{approval.id}/decide", json={"approve": [], "decision": "approved"}, headers=MANAGER)
    assert r.status_code == 409 and "a newer one is in your approvals" in r.json()["detail"]


def test_the_audit_records_the_decision_that_was_applied(client):
    store = client.app.state.container.store
    approval = next(a for a in store.approvals(PID) if a.kind == "task_plan")
    tasks = [t["id"] for p in approval.payload["plans"] for t in p["tasks"]]
    first = {"approve": [], "edits": {}, "decision": None, "user_id": "u-priya"}
    assert store.claim_approval(approval.id, first)  # a first attempt whose resume failed: its decision is kept
    store.release_approval(approval.id)
    r = client.post(f"/api/approvals/{approval.id}/decide", json={"approve": tasks}, headers=MANAGER)
    assert r.status_code == 200
    assert {store.get_task(t).status for t in tasks} == {"rejected"}  # the first decision was re-applied
    [row] = [a for a in store.audit_rows if a["target"] == approval.id]
    assert row["detail"] == {"approved": 0, "decision": None, "reapplied": True}


# ---------------- outbox (fix#53) ----------------
def _signed(secret, payload):
    body = json.dumps(payload).encode()
    return body, "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def test_a_webhook_event_survives_a_failed_publish_and_the_flush_sends_it(client, monkeypatch):
    c = client.app.state.container
    monkeypatch.setattr(c.settings, "mode", "gcp")  # publish through the bus, as production does
    sent = []

    def down(topic, event):
        raise TimeoutError("Pub/Sub")

    monkeypatch.setattr(c.deps.bus, "publish", down)
    push = {"ref": "refs/heads/main", "after": "abc123", "repository": {"full_name": "demo/mini-policy-engine", "default_branch": "main"},
            "commits": [{"modified": ["engine/validate.py"]}]}
    body, sig = _signed(c.settings.github_webhook_secret, push)
    r = client.post("/webhooks/github", content=body, headers={"X-GitHub-Event": "push", "X-Hub-Signature-256": sig})
    assert r.status_code == 202 and r.json() == {"events": 1}

    monkeypatch.setattr(c.deps.bus, "publish", lambda topic, event: sent.append((topic, event)))
    for row in c.store.outbox.values():  # as if the backoff had passed
        row["next_attempt_at"] = datetime.now(timezone.utc)
    assert flush(c, 10) == (1, False)
    (topic, event), = sent
    assert event["type"] == topic == "repo.pushed" and event["branch"] == event["default_branch"] == "main"
    assert flush(c, 10) == (0, False)  # sent once


def test_a_document_whose_event_cannot_be_recorded_is_not_left_pending(client, monkeypatch):
    c = client.app.state.container
    before = len(c.store.documents(PID))

    def broken(topic, event):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(c.store, "outbox_add", broken)
    r = client.post(f"/api/projects/{PID}/documents", files={"file": ("a.md", b"# A", "text/markdown")}, headers=MANAGER)
    assert r.status_code == 500 and len(c.store.documents(PID)) == before


def test_outbox_flush_accepts_only_the_scheduler(client, monkeypatch):
    from google.oauth2 import id_token

    c = client.app.state.container
    assert client.post("/internal/outbox/flush").status_code == 404  # demo mode: off
    monkeypatch.setattr(c.settings, "mode", "gcp")
    monkeypatch.setattr(c.settings, "outbox_flush_audience", "https://api.example.com")
    monkeypatch.setattr(c.settings, "outbox_flush_invoker", "scheduler@p.iam.gserviceaccount.com")
    seen = {}

    def verify(token, request, audience=None, clock_skew_in_seconds=0):
        seen["audience"] = audience
        if token == "forged":
            raise ValueError("bad signature")
        return {"email": token, "email_verified": True}

    monkeypatch.setattr(id_token, "verify_oauth2_token", verify)
    assert client.post("/internal/outbox/flush", headers={"Authorization": "Bearer forged"}).status_code == 401
    assert client.post("/internal/outbox/flush", headers={"Authorization": "Bearer someone@else.com"}).status_code == 403
    r = client.post("/internal/outbox/flush", headers={"Authorization": "Bearer scheduler@p.iam.gserviceaccount.com"})
    assert r.status_code == 200 and r.json() == {"sent": 0} and seen["audience"] == "https://api.example.com"


def test_a_flush_that_could_not_publish_fails_the_scheduler_run(client, monkeypatch):
    """Review r3 infra#3: the flush answered 200 while every send failed, so the Scheduler job never showed a failure."""
    from google.oauth2 import id_token

    c = client.app.state.container
    monkeypatch.setattr(c.settings, "mode", "gcp")
    monkeypatch.setattr(c.settings, "outbox_flush_audience", "https://api.example.com")
    monkeypatch.setattr(c.settings, "outbox_flush_invoker", "scheduler@p.iam.gserviceaccount.com")
    monkeypatch.setattr(id_token, "verify_oauth2_token", lambda token, request, audience=None: {"email": token, "email_verified": True})
    c.store.outbox_add("ingestion", {"type": "repo.pushed", "dedupe": "push:due"})
    for row in c.store.outbox.values():
        row["next_attempt_at"] = datetime.now(timezone.utc)

    def down(topic, event):
        raise PermissionError("pubsub.topics.publish denied")

    monkeypatch.setattr(c.deps.bus, "publish", down)
    r = client.post("/internal/outbox/flush", headers={"Authorization": "Bearer scheduler@p.iam.gserviceaccount.com"})
    assert r.status_code == 503 and r.json()["sent"] == 0
    assert any(row["sent_at"] is None for row in c.store.outbox.values())  # still waiting for the next run


def test_production_requires_the_scheduled_flush():
    s = Settings(mode="gcp", gcp_project="p", gcs_bucket="b", github_webhook_secret="s")
    assert any("OUTBOX_FLUSH_AUDIENCE" in p for p in s.production_problems())
    assert not any("OUTBOX" in p for p in s.production_problems(serves_http=False))  # workers do not serve it


def test_push_events_say_which_branch():
    payload = {"ref": "refs/heads/trunk", "after": "f00", "repository": {"full_name": "a/b", "default_branch": "trunk"}, "commits": []}
    ev, = events_from_webhook("push", payload, "p", "r")
    assert ev["branch"] == "trunk" and ev["default_branch"] == "trunk"
    assert events_from_webhook("push", {**payload, "ref": "refs/heads/feature"}, "p", "r") == []


# ---------------- GitHub callback (auth-api#1, #2, #5) ----------------
@pytest.fixture
def github(client, monkeypatch):
    c = client.app.state.container
    monkeypatch.setattr(c.settings, "github_app_slug", "ww")
    exchanged = []

    def oauth(client_id, secret, code):
        exchanged.append(code)
        return {"id": 777, "login": "priya-gh", "token": "user-token"}

    monkeypatch.setattr(gh, "oauth_user", oauth)
    state = client.get(f"/api/github/install-url?project_id={PID}", headers=MANAGER).json()["url"].split("state=")[1]

    def callback(inst):
        return client.post("/api/github/callback", json={"code": "one-time", "installation_id": inst, "state": state}, headers=MANAGER)

    return c, callback, exchanged


def test_an_unconfirmed_installation_is_retryable_with_the_same_code(github, client):
    c, callback, exchanged = github
    r = callback("4242")
    assert r.status_code == 409 and r.json() == {"detail": "installation_unconfirmed"}
    assert exchanged == []  # the code was not spent, so the browser's retry can use it
    c.store.record_installation_sender("4242", 777, "priya-gh", "acme")  # the installation.created webhook lands
    r = callback("4242")
    assert r.status_code == 200 and r.json()["account"] == "acme" and exchanged == ["one-time"]
    assert c.store.installation_org("4242") == "org-demo"


def test_refusals_never_name_the_installer_or_the_owner(github, client):
    c, callback, exchanged = github
    c.store.record_installation_sender("5000", 999, "someone-else", "rival-co")
    r = callback("5000")
    assert r.status_code == 403 and r.json() == {"detail": "not_installer"}
    c.store.bind_installation("5001", "org-rival", "rival-co", "u-x")
    r = callback("5001")
    assert r.status_code == 403 and r.json() == {"detail": "not_installer"}


def test_an_installation_without_a_recorded_sender_binds_for_an_account_admin(github, client, monkeypatch):
    c, callback, _ = github
    accounts = {"6000": {"login": "acme", "id": 1, "type": "Organization"}}
    admins = []
    monkeypatch.setattr(c.deps.repos, "installation_account", accounts.get, raising=False)
    monkeypatch.setattr(c.deps.repos, "user_admin_of", lambda token, account: token == "user-token" and account["login"] in admins,
                        raising=False)
    assert callback("6000").json() == {"detail": "not_installer"}  # a member, not an admin
    admins.append("acme")
    c.store.installations.pop("6000", None)
    r = callback("6000")
    assert r.status_code == 200 and r.json()["account"] == "acme" and c.store.installation_org("6000") == "org-demo"


def test_a_fresh_installation_waits_for_its_webhook_instead_of_the_admin_check(github, client, monkeypatch):
    c, callback, exchanged = github
    just_now = datetime.now(timezone.utc).isoformat()
    monkeypatch.setattr(c.deps.repos, "installation_account", lambda i: {"login": "acme", "id": 1, "type": "Organization", "created_at": just_now},
                        raising=False)
    monkeypatch.setattr(c.deps.repos, "user_admin_of", lambda token, account: False, raising=False)
    r = callback("6100")  # a repository admin installed it seconds ago; the webhook naming them is on its way
    assert r.status_code == 409 and r.json() == {"detail": "installation_unconfirmed"} and exchanged == []
    c.store.record_installation_sender("6100", 777, "priya-gh", "acme")
    assert callback("6100").status_code == 200


def test_github_app_checks_who_administers_the_account():
    def handler(req: httpx.Request):
        routes = {
            "/app/installations/1": (200, {"account": {"login": "acme", "id": 10, "type": "Organization"}, "created_at": "2025-01-02T03:04:05Z"}),
            "/user/memberships/orgs/acme": (200, {"role": "admin", "state": "active"}),
            "/user/memberships/orgs/other": (404, {}),
            "/user": (200, {"id": 55, "login": "me"}),
        }
        status, body = routes.get(req.url.path, (404, {}))
        if req.url.path.startswith("/user"):
            assert req.headers["authorization"] == "Bearer user-token"
        return httpx.Response(status, json=body)

    app_ = GitHubApp("1", "key", client=httpx.Client(transport=httpx.MockTransport(handler)))
    app_._app_jwt = lambda: "jwt"
    acct = app_.installation_account("1")
    assert acct == {"login": "acme", "id": 10, "type": "Organization", "created_at": "2025-01-02T03:04:05Z"}
    assert app_.installation_account("2") is None
    assert app_.user_admin_of("user-token", acct)
    assert not app_.user_admin_of("user-token", {"login": "other", "id": 11, "type": "Organization"})
    assert app_.user_admin_of("user-token", {"login": "me", "id": 55, "type": "User"})
    assert not app_.user_admin_of("user-token", {"login": "x", "id": 56, "type": "User"})


# ---------------- a wrong join can be undone (fix#26) ----------------
@pytest.fixture
def prod(client, monkeypatch):
    import firebase_admin.auth

    tokens = {"tok-both": {"uid": "fb-both", "email": "both@acme.dev", "email_verified": True, "name": "Bo"}}
    monkeypatch.setattr(client.app.state.container.settings, "mode", "gcp")
    monkeypatch.setattr(firebase_admin.auth, "verify_id_token", lambda tok, clock_skew_seconds=0: tokens[tok])
    _fake = lambda tok, clock_skew_seconds=0: tokens[tok]
    monkeypatch.setattr("app.firebase_tokens.verify", lambda tok, *_a, **_k: _fake(tok))  # real Firebase path
    return {"Authorization": "Bearer tok-both"}


def test_invitations_show_the_inviters_email_and_a_wrong_join_can_be_left(client, prod):
    store = client.app.state.container.store
    store.add_project(Project(id="proj-evil", org_id="org-evil", name="Phish", description=""))
    store.orgs["org-evil"] = "Mini Policy Engine"  # a lookalike name
    store.create_invitation("org-evil", "proj-evil", "both@acme.dev", "manager", None, None, None)
    store.create_invitation("org-demo", PID, "both@acme.dev", "new_hire", None, None, "u-priya")
    pending = client.get("/api/me", headers=prod).json()["detail"]["invitations"]
    real = next(p for p in pending if p["org_id"] == "org-demo")
    assert real["invited_by_email"] == "priya@demo.dev" and "org_created_at" in real

    assert client.post("/api/invitations/accept", json={"org_id": "org-evil"}, headers=prod).status_code == 200  # the wrong one
    assert client.post("/api/invitations/accept", json={"org_id": "org-demo"}, headers=prod).status_code == 409
    assert client.post("/api/me/leave", headers=prod).status_code == 204
    assert client.get("/api/me", headers=prod).status_code == 403
    me = client.post("/api/invitations/accept", json={"org_id": "org-demo"}, headers=prod).json()
    assert me["org_id"] == "org-demo" and [p["id"] for p in me["projects"]] == [PID]


def test_the_last_admin_cannot_leave(client):
    store = client.app.state.container.store
    store.add_project(Project(id="proj-solo", org_id="org-solo", name="Solo", description=""))
    store.add_user(User(id="u-solo", org_id="org-solo", email="solo@x.dev", name="Solo", role="admin"), "proj-solo")
    store.add_user(User(id="u-dev", org_id="org-solo", email="dev@x.dev", name="Dev", role="manager"), "proj-solo")
    store.firebase.update({"fb-solo": "u-solo", "fb-dev": "u-dev"})
    r = client.post("/api/me/leave", headers={"X-Demo-User": "u-solo"})
    assert r.status_code == 409 and r.json() == {"detail": "last_admin"}
    store.firebase.pop("fb-dev")  # alone: an organisation made by mistake can be left (review r3 frontend#2)
    assert client.post("/api/me/leave", headers={"X-Demo-User": "u-solo"}).status_code == 204


def test_someone_who_left_can_be_invited_back(client, monkeypatch):
    """Review r3 api#1: the re-invite silently re-added the unbound row, and the person could never sign in again."""
    import firebase_admin.auth

    tokens = {"tok-f": {"uid": "fb-f", "email": "founder@acme.dev", "email_verified": True, "name": "F"},
              "tok-h": {"uid": "fb-h", "email": "hire@acme.dev", "email_verified": True, "name": "H"}}
    monkeypatch.setattr(client.app.state.container.settings, "mode", "gcp")
    monkeypatch.setattr(firebase_admin.auth, "verify_id_token", lambda tok, clock_skew_seconds=0: tokens[tok])
    _fake = lambda tok, clock_skew_seconds=0: tokens[tok]
    monkeypatch.setattr("app.firebase_tokens.verify", lambda tok, *_a, **_k: _fake(tok))  # real Firebase path
    f, h = {"Authorization": "Bearer tok-f"}, {"Authorization": "Bearer tok-h"}
    me = client.post("/api/signup", json={"name": "F", "org_name": "Acme", "project_name": "P"}, headers=f).json()
    pid, org = me["projects"][0]["id"], me["org_id"]
    client.post(f"/api/projects/{pid}/invitations", json={"email": "hire@acme.dev", "role": "admin"}, headers=f)
    assert client.post("/api/invitations/accept", json={"org_id": org}, headers=h).json()["role"] == "admin"
    assert client.post("/api/me/leave", headers=h).status_code == 204

    r = client.post(f"/api/projects/{pid}/invitations", json={"email": "hire@acme.dev", "role": "new_hire"}, headers=f)
    assert r.status_code == 201 and "invited" in r.json()  # an invitation, not a silent add
    r = client.get("/api/me", headers=h)
    assert r.status_code == 403 and r.json()["detail"]["code"] == "invitation_pending"
    back = client.post("/api/invitations/accept", json={"org_id": org}, headers=h).json()
    assert back["role"] == "new_hire" and [p["id"] for p in back["projects"]] == [pid]  # the role invited with, not admin


def test_github_app_resolves_a_branch_head():
    def handler(req: httpx.Request):
        assert req.headers["accept"] == "application/vnd.github.sha" and req.headers["authorization"] == "Bearer inst-token"
        if req.url.path == "/repos/acme/app/commits/main":
            return httpx.Response(200, text="0123abcd\n")
        return httpx.Response(422 if req.url.path.endswith("/gone") else 404, json={})

    app_ = GitHubApp("1", "key", client=httpx.Client(transport=httpx.MockTransport(handler)))
    app_._token = lambda installation_id: "inst-token"
    assert app_.head_sha("acme/app", "main", "9") == "0123abcd"
    assert app_.head_sha("acme/app", "gone", "9") is None and app_.head_sha("acme/other", "main", "9") is None


def test_pr_events_carry_when_github_sent_them():
    pr = {"number": 7, "html_url": "u", "head": {"sha": "s2", "ref": "fix"}, "user": {"login": "vijay"}, "updated_at": "2026-10-16T10:05:00Z"}
    ev, = events_from_webhook("pull_request", {"action": "synchronize", "pull_request": pr, "repository": {"full_name": "a/b"}}, "p", "r")
    assert ev["pr_updated_at"] == "2026-10-16T10:05:00Z"  # orders reviews by commit, not by which finished first
