"""Integration tests against a real PostgreSQL 16 + pgvector, set up the way Cloud SQL is:

* the migrator runs as a NON-superuser owner (Cloud SQL's users are not superusers),
* the application connects as `ww_app`, which cannot bypass row-level security.

Run with a superuser URL to a scratch server (each test session creates and drops its own database):

    docker run -d --name ww-pg -e POSTGRES_USER=ww -e POSTGRES_PASSWORD=ww -p 55432:5432 pgvector/pgvector:pg16
    TEST_PG_ADMIN_URL=postgresql://ww:ww@localhost:55432/postgres pytest tests/test_postgres.py
"""
from __future__ import annotations

import os
import uuid
from datetime import date, timedelta
from pathlib import Path

import psycopg
import pytest
from sqlalchemy import text

from app.adapters.memory import InlineBus, LocalBlobStorage, LocalRepoSource
from app.adapters.postgres import PgIndex, PgStore, current_org, make_engine
from app.agents.deps import Deps
from app.agents.llm import FakeLLM, read_data
from app.agents.supervisor import build_supervisor
from app.config import Settings
from app.container import ApprovalBusy, Container, EventBusy
from app.services.embeddings import HashEmbedder
from app.services.redaction import RegexRedactor

ADMIN = os.environ.get("TEST_PG_ADMIN_URL")
OWNER_URL: dict[str, str] = {}  # the migrator's own connection to this run's database, for tests that act as the owner
pytestmark = pytest.mark.skipif(not ADMIN, reason="set TEST_PG_ADMIN_URL to run the Postgres integration tests")
SAMPLE = Path(__file__).resolve().parents[1] / "sample_repo"


@pytest.fixture(scope="module")
def db():
    from app import migrate

    name = f"ww_it_{uuid.uuid4().hex[:8]}"
    owner, owner_pw, app_pw = f"own_{name}", "owner-pw", "app-pw"
    host = ADMIN.rsplit("/", 1)[0].split("@", 1)[1]
    with psycopg.connect(ADMIN, autocommit=True) as c:
        # ww_app and ww_definer are cluster-wide roles: start from a clean server so this run's owner creates them
        for (old,) in c.execute("SELECT datname FROM pg_database WHERE datname LIKE 'ww\\_it\\_%' OR datname = 'ww_test'").fetchall():
            c.execute(f'DROP DATABASE "{old}" WITH (FORCE)')
        for (role,) in c.execute("SELECT rolname FROM pg_roles WHERE rolname IN ('ww_app', 'ww_definer') OR rolname LIKE 'own\\_ww\\_it\\_%' "
                                 "OR rolname = 'ww_owner' ORDER BY rolname LIKE 'own%' OR rolname = 'ww_owner'").fetchall():
            c.execute(f'DROP OWNED BY "{role}" CASCADE')
            c.execute(f'DROP ROLE "{role}"')
        c.execute(f"CREATE ROLE {owner} LOGIN CREATEROLE NOSUPERUSER PASSWORD '{owner_pw}'")
        c.execute(f"CREATE DATABASE {name} OWNER {owner}")
    with psycopg.connect(ADMIN.rsplit("/", 1)[0] + f"/{name}", autocommit=True) as c:
        c.execute("CREATE EXTENSION vector; CREATE EXTENSION pgcrypto")  # Cloud SQL: the owner may create these itself
    applied = migrate.run(f"postgresql://{owner}:{owner_pw}@{host}/{name}", app_pw)
    assert applied == sorted(p.stem for p in migrate.MIGRATIONS.glob("*.sql"))  # every migration, in order (001_init ... 011_scheduling)
    assert "011_scheduling" in applied
    # every later deploy runs the migrator again WITH the password: it must not need superuser rights
    assert migrate.run(f"postgresql://{owner}:{owner_pw}@{host}/{name}", app_pw) == []
    OWNER_URL["url"] = f"postgresql://{owner}:{owner_pw}@{host}/{name}"
    yield f"postgresql+psycopg://ww_app:{app_pw}@{host}/{name}"
    with psycopg.connect(ADMIN, autocommit=True) as c:
        c.execute(f"DROP DATABASE {name} WITH (FORCE)")
        c.execute(f"DROP ROLE {owner}")


@pytest.fixture(scope="module")
def engine(db):
    return make_engine(db)


@pytest.fixture
def store(engine):
    current_org.set(None)
    yield PgStore(engine)
    current_org.set(None)


def _org(store: PgStore, label: str):
    uid = f"uid-{label}-{uuid.uuid4().hex[:6]}"
    user = store.create_organization(uid, f"{label}-{uid}@example.com", f"{label} admin", f"{label} Inc", f"{label} project")
    current_org.set(user.org_id)
    project = store.projects_for(user)[0]
    return user, project


def test_app_role_cannot_bypass_rls(store):
    assert store.role_can_bypass_rls() is False


def test_verify_wants_forced_rls_and_a_policy_on_every_org_table_and_a_reason_for_every_global_one(db):
    """migrate.verify refuses to finish when a table is org-scoped without FORCE row-level security, forced but with no policy
    (everything would be hidden, a mistake worth stopping), or has no org_id and is not in the explicit GLOBAL_TABLES list."""
    from app import migrate

    with psycopg.connect(OWNER_URL["url"], autocommit=True) as c:
        migrate.verify(c)  # the migrated schema passes
        assert migrate.protection_problems(c) == []
        public = {r[0] for r in c.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")}
        assert set(migrate.GLOBAL_TABLES) <= public  # the exemption list names real tables only: no stale entries
        cases = [("CREATE TABLE rogue_a (org_id uuid)", "rogue_a: org-scoped but without forced row-level security"),
                 ("CREATE TABLE rogue_b (org_id uuid); ALTER TABLE rogue_b ENABLE ROW LEVEL SECURITY; "
                  "ALTER TABLE rogue_b FORCE ROW LEVEL SECURITY", "rogue_b: forced row-level security but no policy"),
                 ("CREATE TABLE rogue_c (org_id uuid); ALTER TABLE rogue_c ENABLE ROW LEVEL SECURITY", "rogue_c: org-scoped but without forced"),
                 ("CREATE TABLE rogue_d (x int)", "rogue_d: no org_id and not listed in migrate.GLOBAL_TABLES")]
        for ddl, expected in cases:
            table = ddl.split()[2]
            try:
                c.execute(ddl)
                with pytest.raises(SystemExit) as refused:
                    migrate.verify(c)
                assert expected in str(refused.value)
            finally:
                c.execute(f"DROP TABLE IF EXISTS {table}")
        c.execute("CREATE TABLE rogue_e (org_id uuid); ALTER TABLE rogue_e ENABLE ROW LEVEL SECURITY; ALTER TABLE rogue_e FORCE ROW LEVEL SECURITY; "
                  "CREATE POLICY tenant_isolation ON rogue_e USING (org_id = nullif(current_setting('app.org_id', true), '')::uuid)")
        try:
            migrate.verify(c)  # org-scoped, forced and with a policy: fine
        finally:
            c.execute("DROP TABLE rogue_e")


def test_one_company_never_sees_another(store, engine):
    alice, pa = _org(store, "a")
    store.create_meeting(pa.id, "KT A", alice.id)
    bob, pb = _org(store, "b")  # tenant is now B
    assert store.projects_for(bob) == [pb]
    with pytest.raises(Exception):
        store.get_project(pa.id)  # A's project is invisible, not just filtered by the query
    assert store.list_meetings(pa.id) == []
    with engine.begin() as c:  # even a query with no WHERE clause sees only B
        c.execute(text("SELECT set_config('app.org_id', :o, true)"), {"o": bob.org_id})
        orgs = {str(r[0]) for r in c.execute(text("SELECT org_id FROM projects"))}
        assert orgs == {bob.org_id}
    current_org.set(None)
    assert store.rows("SELECT id FROM projects") == []  # no tenant set: nothing at all
    current_org.set(bob.org_id)
    with pytest.raises(Exception):  # and B cannot write a row into A
        store.run("INSERT INTO projects (org_id, name) VALUES (:o, 'sneaky')", o=alice.org_id)


def test_invitation_is_accepted_on_first_sign_in(store):
    admin, project = _org(store, "inv")
    store.create_invitation(admin.org_id, project.id, "New.Hire@Example.com", "new_hire", "Nia", "nia-gh", admin.id)
    assert [i["email"] for i in store.invitations(project.id)] == ["new.hire@example.com"]
    current_org.set(None)  # sign-in happens before the tenant is known
    [pending] = store.pending_invitations("new.hire@example.com")
    assert pending["org_id"] == admin.org_id and pending["role"] == "new_hire" and pending["projects"] == [project.name]
    user = store.accept_invitations("uid-nia", "new.hire@example.com", "Nia from Google", admin.org_id)
    assert user and user.org_id == admin.org_id and user.role == "new_hire" and user.name == "Nia" and user.github_login == "nia-gh"
    current_org.set(user.org_id)
    assert store.is_member(project.id, user.id) and store.invitations(project.id) == []
    current_org.set(None)
    assert store.user_by_firebase("uid-nia").id == user.id
    assert store.accept_invitations("uid-stranger", "stranger@example.com", "x", admin.org_id) is None
    assert store.pending_invitations("new.hire@example.com") == []


def test_signup_refuses_a_login_that_already_has_an_account(store):
    admin, _ = _org(store, "dup")
    uid = store.rows("SELECT firebase_uid FROM users WHERE id = :u", u=admin.id)[0]["firebase_uid"]
    current_org.set(None)
    with pytest.raises(Exception):
        store.create_organization(uid, "x@example.com", "x", "Second Inc", "p")


def test_installation_belongs_to_one_organisation(store):
    a, _ = _org(store, "gha")
    assert store.bind_installation("4242", a.org_id, "acme", a.id) is True
    assert store.bind_installation("4242", a.org_id, "acme", a.id) is True  # idempotent for the owner
    b, _ = _org(store, "ghb")
    assert store.bind_installation("4242", b.org_id, "evil", b.id) is False
    assert store.installation_org("4242") == a.org_id


def test_event_leases(store):
    key = f"evt-{uuid.uuid4().hex}"
    assert store.claim_event(key, 300) == "new"
    assert store.claim_event(key, 300) == "busy"  # another handler holds a live lease
    store.release_event(key)  # it failed: the retry may run
    assert store.claim_event(key, 300) == "new"
    assert store.claim_event(key, 0) == "new"  # the holder died and the lease lapsed: take it over
    store.finish_event(key)
    assert store.claim_event(key, 0) == "done"


def test_golden_path_on_postgres(engine, store):
    """Repo -> KT call -> plan (paused in Postgres, resumed) -> chat -> PR -> review (paused, decided)."""
    from langgraph.checkpoint.postgres import PostgresSaver
    from psycopg_pool import ConnectionPool

    admin, project = _org(store, "gp")
    pid = project.id
    store.create_invitation(admin.org_id, pid, "hire@gp.example.com", "new_hire", "Vijay", "vijay", admin.id)
    current_org.set(None)
    hire = store.accept_invitations("uid-gp-hire", "hire@gp.example.com", "Vijay", admin.org_id)
    current_org.set(admin.org_id)
    repo = store.create_repo(pid, "demo/mini-policy-engine", None, "main")
    meeting = store.create_meeting(pid, "KT #1", admin.id)
    ob = store.create_onboarding(pid, hire.id, admin.id, date.today(), "Engineer")

    settings = Settings(mode="demo", agent_retries=1, retry_sleep_s=0, event_lease_s=60)
    llm = FakeLLM()
    deps = Deps(settings=settings, llm=llm, index=PgIndex(engine, HashEmbedder(768)), store=store,
                repos=LocalRepoSource(str(SAMPLE), issues=[{"number": 12, "title": "Validate privileged init containers", "labels": []}],
                                      prs={7: [{"filename": "engine/validate.py", "status": "modified",
                                                "patch": "@@ -26,3 +26,4 @@\n     containers = []\n+    raise ValueError(\"x\")\n     return []\n"}]}),
                blobs=LocalBlobStorage("/tmp/ww-it"), redactor=RegexRedactor(), bus=InlineBus())
    pool = ConnectionPool(conninfo=str(engine.url.render_as_string(hide_password=False)).replace("postgresql+psycopg://", "postgresql://"),
                          min_size=1, max_size=2, kwargs={"autocommit": True, "prepare_threshold": 0}, open=True)
    saver = PostgresSaver(pool)
    c = Container(settings, deps, build_supervisor(deps, saver), build_supervisor(deps, None), saver)

    out = c.handle({"type": "repo.connected", "project_id": pid, "repository_id": repo.id, "ref": "main", "dedupe": f"connect:{repo.id}"})
    assert out["result"]["chunks"] > 10
    assert c.handle({"type": "repo.connected", "project_id": pid, "repository_id": repo.id, "dedupe": f"connect:{repo.id}"}) == {"skipped": "duplicate"}
    assert "check_labels" in {s.symbol for s in deps.index.symbols_in(pid, "engine/validate.py")}

    llm.responders["SegmentAnalysis"] = lambda p, m: {
        "utterances": [{"start_s": 50, "end_s": 80, "speaker": "Ravi", "text": "check_labels reports missing labels. ravi@corp.com"}],
        "screen_moments": [{"at_s": 55, "description": "editor", "visible_paths": ["validate.py"], "visible_symbols": ["check_labels"]}],
        "action_items": [{"text": "Read engine/policy.py", "at_s": 70}],
    }
    llm.responders["MeetingSummary"] = lambda p, m: {"summary": "Validation.", "decisions": []}
    llm.responders["TaskPlan"] = lambda p, m: {
        "tasks": [{"title": "Read engine/policy.py", "description": "x", "kind": "learn", "due_in_days": 3, "source": "meeting"}],
        "first_pr": {"title": "Validate privileged init containers", "why": "starter", "issue_number": 12, "files": ["engine/validate.py"]}}
    out = c.handle({"type": "meeting.recording_ready", "project_id": pid, "meeting_id": meeting.id, "recording_uri": "gs://b/r.mp4",
                    "duration_s": 120, "dedupe": f"rec:{meeting.id}"})
    assert "__interrupt__" in out
    assert "ravi@corp.com" not in store.meeting_notes(meeting.id).utterances[0].text
    link = store.kt_links(pid, path="engine/validate.py")[0]
    assert link.symbol == "check_labels" and link.method == "path" and link.meeting_title == "KT #1"

    # the transcript goes to the managers: a KT call review waits in approvals (the new approval kind round-trips)
    [kt] = [a for a in store.approvals(pid) if a.kind == "kt_followup"]
    assert kt.payload == {"meeting_id": meeting.id, "title": "KT #1"} and kt.thread_id == f"kt-review:{meeting.id}"
    from app.services import kt_review

    assert kt_review.open_review(store, pid, meeting.id, "KT #1") is None  # one review per call
    assert kt_review.decide(store, None, None, kt, admin, []) == {"booked": [], "skipped": []}
    assert store.get_approval(kt.id).status == "approved" and store.get_approval(kt.id).decided_by == admin.id

    approval = next(a for a in store.approvals(pid) if a.kind == "task_plan")
    tasks = approval.payload["plans"][0]["tasks"]
    first_pr = next(t for t in tasks if t["kind"] == "code")
    assert first_pr["github_issue_number"] == 12
    out = c.decide(approval, {"approve": [t["id"] for t in tasks], "edits": {first_pr["id"]: {"due_date": "2030-01-31"}}, "user_id": admin.id})
    assert out["result"] == {"approved": 2, "rejected": 0}
    assert store.get_task(first_pr["id"]).due_date == date(2030, 1, 31)
    with pytest.raises(ApprovalBusy):
        c.decide(store.get_approval(approval.id), {"approve": [], "user_id": admin.id})
    # the finished run's checkpoints are gone; the table does not grow forever
    with engine.begin() as conn:
        assert conn.execute(text("SELECT count(*) FROM checkpoints WHERE thread_id = :t"), {"t": approval.thread_id}).scalar() == 0

    llm.responders["QADraft"] = lambda p, m: {"answer": "In check_labels.", "confidence": 0.9, "sufficient": True,
                                              "cited": [next(x["n"] for x in read_data(p)["context"] if "check_labels" in x["label"])]}
    out = c.handle({"type": "chat.question", "project_id": pid, "user_id": hire.id, "question": "Where are missing labels reported?"})
    assert out["result"]["status"] == "answered" and out["result"]["kt_clips"][0]["start_s"] == 55
    assert store.chat_feedback(out["result"]["message_id"], hire.id, 1) is True
    assert store.chat_feedback(out["result"]["message_id"], admin.id, 1) is False  # only the asker rates an answer

    llm.responders["ReviewDraft"] = lambda p, m: {"recommendation": "looks_good", "summary": "ok", "findings": [], "questions": []}
    from app.agents.task import short_ref

    out = c.handle({"type": "pr.opened", "project_id": pid, "full_name": repo.full_name, "pr_number": 7, "head_sha": "abc",
                    "pr_url": "https://github.com/x/y/pull/7", "branch": f"task-{short_ref(first_pr['id'])}", "body": "", "dedupe": "pr:7:abc"})
    assert "__interrupt__" in out
    review = store.get_review(out["review_id"])
    assert review.result.recommendation == "needs_work"  # rule: no tests changed overrides the model's "looks good"
    decision = store.approvals(pid)[0]
    c.decide(decision, {"decision": "changes_requested", "user_id": admin.id})
    assert store.get_task(first_pr["id"]).status == "changes_requested"

    # a PR that only says "Closes #12" finds the same task through its issue number
    from app.agents.task import find_task_for_pr

    assert find_task_for_pr(store.tasks(project_id=pid), "feature/x", "Closes #12").id == first_pr["id"]
    assert ob.id in {t.onboarding_id for t in store.tasks(project_id=pid)}
    runs = store.list_runs(pid)
    assert runs and all(r["status"] == "succeeded" for r in runs)
    pool.close()


def test_busy_event_is_not_lost(engine, store):
    _org(store, "busy")
    settings = Settings(mode="demo", event_lease_s=300)
    deps = Deps(settings=settings, llm=FakeLLM(), index=PgIndex(engine, HashEmbedder(768)), store=store, repos=None,
                blobs=None, redactor=RegexRedactor(), bus=InlineBus())
    c = Container(settings, deps, build_supervisor(deps, None))
    key = f"busy-{uuid.uuid4().hex}"
    store.claim_event(key, 300)  # another pod is working on it
    with pytest.raises(EventBusy):
        c.handle({"type": "mystery", "project_id": "x", "dedupe": key})


def test_http_api_in_production_mode(engine, store, monkeypatch, tmp_path):
    """The whole API over HTTP, MODE=gcp auth, Postgres as ww_app. Only Firebase token checks and GitHub's OAuth code
    exchange are stubbed. This is the test that catches a tenant that never reaches the endpoints."""
    import firebase_admin.auth
    from fastapi.testclient import TestClient

    import app.adapters.github as gh
    from app.config import get_settings
    from app.main import create_app

    tag = uuid.uuid4().hex[:6]
    tokens = {f"t-{who}": {"uid": f"fb-{who}-{tag}", "email": f"{who}-{tag}@example.com", "email_verified": True, "name": who}
              for who in ("founder", "hire", "rival")}
    monkeypatch.setattr(firebase_admin.auth, "verify_id_token", lambda tok, clock_skew_seconds=0: tokens[tok])
    _fake = lambda tok, clock_skew_seconds=0: tokens[tok]
    monkeypatch.setattr("app.firebase_tokens.verify", lambda tok, *_a, **_k: _fake(tok))  # real Firebase path
    monkeypatch.setattr(gh, "oauth_user", lambda *a, **k: {"id": 777, "login": "founder-gh"})

    get_settings.cache_clear()
    with TestClient(create_app()) as client:
        settings = Settings(mode="gcp", agent_retries=1, retry_sleep_s=0, github_app_slug="ww", github_webhook_secret="s3cret")
        bus = InlineBus()
        deps = Deps(settings=settings, llm=FakeLLM(), index=PgIndex(engine, HashEmbedder(768)), store=store,
                    repos=LocalRepoSource(str(SAMPLE)), blobs=LocalBlobStorage(str(tmp_path)), redactor=RegexRedactor(), bus=bus)
        c = Container(settings, deps, build_supervisor(deps, None), build_supervisor(deps, None))
        bus.handler = c.handle
        client.app.state.container = c
        H = {k: {"Authorization": f"Bearer {k}"} for k in tokens}

        me = client.post("/api/signup", json={"name": "Fatima", "org_name": f"Acme {tag}", "project_name": "Payments"}, headers=H["t-founder"]).json()
        pid = me["projects"][0]["id"]
        assert client.get(f"/api/projects/{pid}", headers=H["t-founder"]).status_code == 200
        assert [p["id"] for p in client.get("/api/projects", headers=H["t-founder"]).json()] == [pid]
        p2 = client.post("/api/projects", json={"name": "Second"}, headers=H["t-founder"])
        assert p2.status_code == 201

        # invite -> the hire sees it and joins explicitly
        assert client.post(f"/api/projects/{pid}/invitations", json={"email": tokens["t-hire"]["email"], "role": "new_hire",
                                                                     "github_login": "hire-gh"}, headers=H["t-founder"]).status_code == 201
        r = client.get("/api/me", headers=H["t-hire"])
        assert r.status_code == 403 and r.json()["detail"]["code"] == "invitation_pending"
        hire = client.post("/api/invitations/accept", json={"org_id": me["org_id"]}, headers=H["t-hire"]).json()
        assert hire["role"] == "new_hire" and [p["id"] for p in hire["projects"]] == [pid]

        # GitHub: the webhook says who installed the app; only that person can bind it; then connect + index
        import hashlib
        import hmac
        import json as _json

        def hook(event, payload):
            body = _json.dumps(payload).encode()
            sig = "sha256=" + hmac.new(b"s3cret", body, hashlib.sha256).hexdigest()
            return client.post("/webhooks/github", content=body, headers={"X-GitHub-Event": event, "X-Hub-Signature-256": sig})

        inst = str(9000 + int(tag, 16) % 1000)
        assert hook("installation", {"action": "created", "installation": {"id": int(inst), "account": {"login": "acme"}},
                                     "sender": {"id": 777, "login": "founder-gh"}}).json() == {"installation": "recorded"}
        state = client.get(f"/api/github/install-url?project_id={pid}", headers=H["t-founder"]).json()["url"].split("state=")[1]
        cb = client.post("/api/github/callback", json={"code": "c", "installation_id": inst, "state": state}, headers=H["t-founder"])
        assert cb.status_code == 200 and cb.json()["repositories"]
        repo = client.post(f"/api/projects/{pid}/repositories", json={"full_name": "demo/mini-policy-engine", "installation_id": inst},
                           headers=H["t-founder"])
        assert repo.status_code == 202
        assert client.post(f"/api/projects/{pid}/repositories", json={"full_name": "demo/other", "installation_id": inst},
                           headers=H["t-founder"]).status_code == 409  # one repository per project
        files = {f["path"] for f in client.get(f"/api/code/files?project_id={pid}", headers=H["t-hire"]).json()}
        assert "engine/validate.py" in files
        assert client.get(f"/api/code/file?project_id={pid}&path=engine/validate.py", headers=H["t-hire"]).json()["lines"]

        # chat (model down -> honest answer routed to a person), coverage, uploads, notifications
        ans = client.post("/api/chat", json={"project_id": pid, "question": "Where is check_labels?"}, headers=H["t-hire"]).json()
        assert ans["status"] == "not_enough_information"
        assert client.post(f"/api/chat/{ans['message_id']}/feedback", json={"value": -1}, headers=H["t-hire"]).status_code == 204
        assert "engine" in {m["module"] for m in client.get(f"/api/coverage?project_id={pid}", headers=H["t-hire"]).json()["modules"]}
        up = client.post(f"/api/projects/{pid}/documents", files={"file": ("guide.md", b"# Guide\nRun tests before a PR.", "text/markdown")},
                         headers=H["t-founder"])
        assert up.status_code == 202
        docs = client.get(f"/api/projects/{pid}", headers=H["t-founder"]).json()["documents"]
        assert docs[0]["status"] == "ready"
        assert any("project=" in n["link"] for n in client.get("/api/notifications", headers=H["t-founder"]).json())
        assert client.get("/api/reviews/not-a-uuid", headers=H["t-founder"]).status_code == 404  # malformed id: 404, not 500

        # another company signs up and sees none of it
        rival = client.post("/api/signup", json={"name": "R", "org_name": f"Rival {tag}", "project_name": "X"}, headers=H["t-rival"]).json()
        assert client.get(f"/api/projects/{pid}", headers=H["t-rival"]).status_code == 404
        assert client.get(f"/api/code/files?project_id={pid}", headers=H["t-rival"]).status_code == 404
        assert client.post("/api/github/callback", json={"code": "c", "installation_id": inst, "state": "x.1.y"},
                           headers=H["t-rival"]).status_code == 400
        assert [p["name"] for p in rival["projects"]] == ["X"]
    get_settings.cache_clear()


def test_one_repository_per_project_even_under_concurrency(store):
    from concurrent.futures import ThreadPoolExecutor

    admin, project = _org(store, "repo1")
    org = admin.org_id
    first = store.create_repo(project.id, "acme/first", "1", "main")
    again = store.create_repo(project.id, "Acme/FIRST", "1", "develop")  # same repository, different case: a reconnect
    assert again.id == first.id and again.default_branch == "develop"
    assert store.create_repo(project.id, "acme/other", "1", "main") is None
    assert [r.full_name for r in store.repos_for(project.id)] == ["acme/first"]

    def connect(args):
        pid, name = args
        current_org.set(org)
        return store.create_repo(pid, name, "1", "main")

    for i in range(20):  # two managers connecting different repositories at the same moment: exactly one wins
        p = store.create_project(org, f"race {i}", "", admin.id)
        with ThreadPoolExecutor(2) as pool:
            results = list(pool.map(connect, [(p.id, "acme/alpha"), (p.id, "acme/beta")]))
        assert sum(r is not None for r in results) == 1
        assert len(store.repos_for(p.id)) == 1


def test_pg_code_chunks_matches_memory_shape(engine, store):
    """PgIndex.code_chunks: one SELECT of the non-stale code chunks, ordered like MemoryIndex (paths by code point,
    start_line as a number), content blanked on request; knowledge.code_files reads the same spans from both."""
    from app.adapters.memory import MemoryIndex
    from app.agents.schemas import Utterance
    from app.services import knowledge
    from app.services.chunking import chunk_code, chunk_document, chunk_transcript

    _, project = _org(store, "chunks")
    pid = project.id

    def funcs(*specs):  # (name, first line, body lines): a def every few lines, so start_line 3 sorts before 12 numerically
        out = []
        for name, first, body in specs:
            out += [""] * (first - 1 - len(out)) + [f"def {name}():"] + [f"    v{i} = {i}" for i in range(body)]
        return "\n".join(out)

    files = {
        "alpha/a_b.py": funcs(("under", 1, 2)),
        "alpha/a/b.py": funcs(("nested", 3, 2), ("later", 12, 2)),
        "Zeta.py": funcs(("upper", 1, 2)),
        "pkg/big.py": funcs(("huge", 1, 299)),  # split into three chunks, merged back into one span
    }
    chunks = [c for path, text in files.items() for c in chunk_code(pid, path, text, "sha1")]
    chunks += chunk_document(pid, "doc-1", "Guide", ["Run the tests before a PR."])
    chunks += chunk_transcript(pid, "mtg-x", "KT", [Utterance(start_s=0, end_s=5, speaker="Ravi", text="hello")])
    gone = next(c for c in chunks if c.symbol == "later")

    pg, mem = PgIndex(engine, HashEmbedder(768)), MemoryIndex(HashEmbedder(768))
    pg.upsert(chunks)
    mem.upsert(chunks)
    assert pg.run("UPDATE chunks SET stale = true WHERE id = :i", i=gone.id) == 1
    mem.chunks[gone.id] = gone.model_copy(update={"stale": True})

    lean = pg.code_chunks(pid, content=False)
    assert [(c.ref["path"], c.symbol, c.ref["start_line"]) for c in lean] == [
        ("Zeta.py", "upper", 1), ("alpha/a/b.py", "nested", 3), ("alpha/a_b.py", "under", 1),
        ("pkg/big.py", "huge", 1), ("pkg/big.py", "huge", 121), ("pkg/big.py", "huge", 241)]  # no stale, doc or meeting chunk
    assert all(c.content == "" and c.source_type == "code" and c.project_id == pid and not c.stale for c in lean)
    assert [c.model_dump() for c in lean] == [c.model_dump() for c in mem.code_chunks(pid, content=False)]
    full = pg.code_chunks(pid)
    assert [c.model_dump() for c in full] == [c.model_dump() for c in mem.code_chunks(pid)]
    assert full[0].content.startswith("File: Zeta.py (lines 1-3)\nSymbol: upper\ndef upper():")
    assert knowledge.code_files(pg, pid) == knowledge.code_files(mem, pid)
    assert knowledge.code_files(pg, pid)["pkg/big.py"] == [knowledge.Span("huge", 1, 300, path="pkg/big.py")]


def test_embedding_outage_on_postgres_degrades_risk_and_plan(engine, store):
    """A Gemini embedding outage on Postgres: PgIndex.search raises before its query. Knowledge risk and the KT planner
    still answer, without the searched signals, and say partial; two failures in a row open the circuit breaker, so
    requests stop waiting on the embedder for a while; after that the next request searches again, and a reload after
    that is answered from the memo."""
    from app.agents.schemas import QAResult
    from app.services import knowledge
    from app.services.chunking import chunk_code
    from app.services.knowledge_risk import build_knowledge_risk
    from app.services.kt_planner import build_plan

    admin, project = _org(store, "outage")
    pid = project.id
    store.create_invitation(admin.org_id, pid, "hire@outage.example.com", "new_hire", "Vijay", "vijay", admin.id)
    current_org.set(None)
    hire = store.accept_invitations("uid-outage-hire", "hire@outage.example.com", "Vijay", admin.org_id)
    current_org.set(admin.org_id)
    store.create_repo(pid, "demo/mini-policy-engine", None, "main")
    store.set_codeowners(pid, (SAMPLE / ".github/CODEOWNERS").read_text())
    store.create_onboarding(pid, hire.id, admin.id, date.today(), "Engineer")
    asked = store.save_chat(pid, hire.id, "How does the admission webhook handle a request?", QAResult(status="not_enough_information", answer=""))

    index = PgIndex(engine, HashEmbedder(768))
    index.upsert([c for path in ("engine/validate.py", "webhooks/server.py") for c in chunk_code(pid, path, (SAMPLE / path).read_text(), "sha1")])

    class Down:
        def embed(self, texts, task="document"):
            raise ConnectionError("Vertex AI embeddings unavailable")

    healthy, index.embedder = index.embedder, Down()
    risk = build_knowledge_risk(store, index, pid, admin.org_id)
    assert risk["partial"] is True and {f["path"] for f in risk["files"]} == {"engine/validate.py", "webhooks/server.py"}
    assert not [d for f in risk["files"] for d in f["demand"] if d["kind"] == "question"]
    plan = build_plan(store, index, pid, admin.org_id, admin, None)
    assert plan["partial"] is True and plan["indexed"] is True

    index.embedder = healthy
    # Two failed requests in a row opened the circuit breaker: for BREAKER_OPEN_S no request waits on the embedder again
    assert build_knowledge_risk(store, index, pid, admin.org_id)["partial"] is True
    knowledge._search_memos[index].open_until = 0.0  # ...and once that time has passed, the next request searches again
    risk = build_knowledge_risk(store, index, pid, admin.org_id)
    [question] = [d for f in risk["files"] for d in f["demand"] if d["kind"] == "question"]
    assert risk["partial"] is False and question["ref"] == {"message_id": str(asked)} and question["answered"] is False
    index.embedder = Down()  # down again, but the question's search is remembered for this code
    assert build_plan(store, index, pid, admin.org_id, admin, None)["partial"] is False
    knowledge.clear_search_memo()


# ---------------------------------------------------------------- parity: knowledge risk, KT planner, code tour
# One scenario, built through the same Store, AppStore and Index calls on each adapter, must give the same three pages.
# Stage 1 is the demo seed (app/seed.py). Stage 2 adds what production has and the demo does not: a cohort of new hires
# who start on one day with identical plans, unanswered questions from two of them that land on the same code, a push
# that adds two modules (one directory capitalised), an open expert request and a clip whose code changed since.
#
# What may differ, and how the comparison deals with it:
# - ids. MemoryStore makes 12 hex digits, Postgres makes UUIDs. _World.name records each id the scenario creates under
#   a name both worlds share; _norm replaces a string only when it IS one of those ids, and nothing else.
# - session keys. kt_planner.session_key hashes the presenter's id with the agenda. _keys checks that each key is
#   exactly that hash on its own adapter, then names it by the hash of the shared presenter name, so another presenter
#   or agenda still fails.
# - a booked call's internal room handle ('kt-' + a random UUID) and start time (now): each adapter makes them itself. They
#   are checked for shape and left out of the comparison.
# The pages carry no other timestamps: due dates are dates, computed from today on both.
PARITY_PEOPLE = (("priya", "Priya (Manager)", "manager", "priya-mgr"), ("vijay", "Vijay (New hire)", "new_hire", "vijay"),
                 ("ravi", "Ravi (Senior)", "expert", "ravi-dev"), ("meera", "Meera (Senior)", "expert", "meera-dev"))
COHORT = (("asha", "Asha (New hire)"), ("ben", "Ben (New hire)"))
NEW_MODULES = (("Tools/lint.py", "def lint(paths):\n    return [p for p in paths if p.endswith('.py')]\n"),
               ("audit/log.py", "def record(event):\n    return {'event': event}\n"))


class _World:
    """One adapter's copy of the scenario: its store and index, and the names of the ids it generated."""

    def __init__(self, store, index, tenant):
        self.store, self.index, self.tenant = store, index, tenant  # tenant(org): current_org.set on Postgres, a no-op in memory
        self.org = self.pid = ""
        self.people: dict = {}
        self.ob: dict = {}
        self.ids: dict[str, str] = {}
        self.messages = 0

    def name(self, actual, symbolic: str) -> str:
        self.ids[str(actual)] = symbolic
        return str(actual)


def _parity_world(store, index, tenant, tag: str) -> _World:
    """Stage 1, the demo seed, made only with calls both adapters implement (MemoryStore's add_* setup helpers have no
    Postgres twin). The admin create_organization always makes is the seed's admin (Alex, u-admin)."""
    from langgraph.checkpoint.memory import MemorySaver

    from app.agents.llm import OfflineLLM
    from app.agents.schemas import ActionItem, MeetingResult, Term, Utterance
    from app.agents.supervisor import run_event
    from app.seed import DEMO_ISSUES, MOMENTS, TRANSCRIPT, VIJAY_DAY
    from app.services.chunking import chunk_transcript
    from app.services.kt_linker import link_screen_moments

    w = _World(store, index, tenant)
    admin = store.create_organization(f"uid-par-admin-{tag}", f"admin-{tag}@par.example.com", "Alex (Admin)", f"Parity {tag}", "Mini Policy Engine")
    w.org = w.name(admin.org_id, "<org>")
    tenant(w.org)
    w.pid = w.name(store.projects_for(admin)[0].id, "<project>")
    w.people["admin"] = admin
    w.name(admin.id, "<admin>")
    for key, name, role, login in PARITY_PEOPLE:
        _join(w, tag, key, name, role, login)
    w.ob["vijay"] = store.create_onboarding(w.pid, w.people["vijay"].id, w.people["priya"].id, date.today() - timedelta(days=VIJAY_DAY),
                                            "Software Engineer")
    w.name(w.ob["vijay"].id, "<ob-vijay>")
    repo = store.create_repo(w.pid, "demo/mini-policy-engine", None, "main")
    w.name(repo.id, "<repo>")
    w.deps = Deps(settings=Settings(mode="demo", agent_retries=1, retry_sleep_s=0), llm=OfflineLLM(), index=index, store=store,
                  repos=LocalRepoSource(str(SAMPLE), issues=DEMO_ISSUES), blobs=LocalBlobStorage("/tmp/ww-parity"),
                  redactor=RegexRedactor(), bus=InlineBus())
    w.graph = build_supervisor(w.deps, MemorySaver())
    run_event(w.graph, {"type": "repo.connected", "project_id": w.pid, "repository_id": repo.id, "ref": "main"}, f"parity:repo:{tag}")

    m = store.create_meeting(w.pid, "KT #1: How validation works", w.people["priya"].id)
    store.update_meeting(m.id, status="processing", duration_s=270)
    w.name(m.id, "<mtg-1>")
    utterances = [Utterance(start_s=a, end_s=b, speaker=s, text=t) for a, b, s, t in TRANSCRIPT]
    links = link_screen_moments(w.pid, m.id, m.title, MOMENTS, utterances, index, meeting_end_s=270)
    store.save_meeting_result(MeetingResult(  # the seed's notes, word for word
        meeting_id=m.id, project_id=w.pid, title=m.title,
        summary="Ravi explained the validation flow: validate_resource loops over matching rules, check_labels and "
                "check_privileged return violation strings, and apply_defaults adds labels before validation runs.",
        decisions=["Validation returns a list of violations and never raises"],
        action_items=[ActionItem(text="Vijay to read engine/policy.py before Friday", owner_hint="Vijay", at_s=232),
                      ActionItem(text="Vijay to look at issue #12 as a first PR", owner_hint="Vijay", at_s=245)],
        terms=[Term(term="violation", definition="A message describing a rule the resource breaks")],
        utterances=utterances, screen_moments=MOMENTS, kt_links=links))
    store.add_kt_links(w.pid, links)
    index.upsert(chunk_transcript(w.pid, m.id, m.title, utterances))
    run_event(w.graph, {"type": "onboarding.started", "project_id": w.pid, "onboarding_id": w.ob["vijay"].id}, f"parity:plan:{tag}")
    _name_tasks(w)
    # what the demo seed leaves in Ravi's inbox (app/seed.py seed_conversations): a question with no chat turn and the assistant's draft
    w.name(store.create_expert_request(w.pid, "Who signs off a change to the staging deploy checklist?", w.people["ravi"].id,
                                       w.people["vijay"].id, None, draft="I found nothing about it. This draft is unverified."), "<req-0>")
    return w


def _join(w: _World, tag: str, key: str, name: str, role: str, login: str) -> None:
    email = f"{key}-{tag}@par.example.com"
    w.store.create_invitation(w.org, w.pid, email, role, name, login, w.people["admin"].id)
    w.tenant(None)  # sign-in happens before the tenant is known
    w.people[key] = w.store.accept_invitations(f"uid-par-{key}-{tag}", email, name, w.org)
    w.tenant(w.org)
    w.name(w.people[key].id, f"<{key}>")


def _name_tasks(w: _World) -> None:
    """Names the scenario's hires' tasks. The demo also seeds the onboarding lifecycle's other hires (app/seed.py
    seed_lifecycle); their tasks have no code paths and no first PR, so no page shows them, and they are left unnamed."""
    hire = {ob.id: key for key, ob in w.ob.items()}
    for t in w.store.tasks(project_id=w.pid):
        if t.onboarding_id in hire:
            w.name(t.id, f"<task {hire[t.onboarding_id]}: {t.title}>")


def _cohort(w: _World, tag: str) -> None:
    """Stage 2."""
    from app.agents.schemas import Citation, QAResult
    from app.agents.supervisor import run_event
    from app.services.chunking import chunk_code

    s = w.store
    w.tenant(w.org)
    w.index.upsert([c for path, text in NEW_MODULES for c in chunk_code(w.pid, path, text, "sha2")])
    for key, name in COHORT:  # same start date, so the Task agent proposes both of them the same titles and due dates
        _join(w, tag, key, name, "new_hire", key)
        w.ob[key] = s.create_onboarding(w.pid, w.people[key].id, w.people["priya"].id, date.today() - timedelta(days=1), "Software Engineer")
        w.name(w.ob[key].id, f"<ob-{key}>")
        run_event(w.graph, {"type": "onboarding.started", "project_id": w.pid, "onboarding_id": w.ob[key].id}, f"parity:plan-{key}:{tag}")
        for t in s.tasks(onboarding_id=w.ob[key].id):
            s.update_task(t.id, status="approved")
    _name_tasks(w)

    def ask(who: str, question: str, result: QAResult) -> str:
        w.messages += 1
        return w.name(s.save_chat(w.pid, w.people[who].id, question, result), f"<msg-{w.messages}>")

    unanswered = QAResult(status="not_enough_information", answer="")
    ask("vijay", "How does the admission webhook handle a request?", unanswered)
    ask("asha", "Where does the webhook server handle an admission request?", unanswered)
    ask("asha", "What does a policy rule look like?", QAResult(
        status="answered", answer="See engine/policy.py.", confidence=0.7,
        citations=[Citation(chunk_id=None, source_type="code", label="engine/policy.py", ref={"path": "engine/policy.py", "start_line": 1, "end_line": 6})]))
    routed = ask("vijay", "Who reviews pull requests to the webhook?", unanswered)
    w.name(s.create_expert_request(w.pid, "Who reviews pull requests to the webhook?", w.people["meera"].id, w.people["vijay"].id, routed), "<req-1>")
    s.mark_kt_code_changed(w.pid, ["engine/mutate.py"])


def _keys(payload, w: _World):
    """Name every KT plan session key in `payload` (a plan, or a booking's session) after checking it is
    session_key(presenter id, agenda) on this adapter. Returns the payload."""
    from app.services import knowledge as K
    from app.services.kt_planner import session_key

    for s in payload.get("sessions") or [payload.get("session") or payload]:
        if "key" not in s:
            continue
        spans = [K.Span(i["symbol"], i["start_line"], i["end_line"], path=i["path"]) for i in s["items"]]
        presenter = (s["presenter"] or {}).get("user_id")
        assert s["key"] == session_key(presenter, spans)
        w.ids[s["key"]] = "<key " + session_key(w.ids.get(presenter), spans) + ">"
    return payload


def _norm(v, w: _World):
    if isinstance(v, dict):
        return {k: _norm(x, w) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_norm(x, w) for x in v]
    return w.ids.get(v, v) if isinstance(v, str) else v


def _diff(a, b, path="") -> list[str]:
    if isinstance(a, dict) and isinstance(b, dict):
        return [f"{path}.{k}: on one side only" for k in sorted(set(a) ^ set(b), key=str)] + \
               [d for k in a if k in b for d in _diff(a[k], b[k], f"{path}.{k}")]
    if isinstance(a, list) and isinstance(b, list):
        return ([f"{path}: {len(a)} vs {len(b)} entries"] if len(a) != len(b) else []) + \
               [d for n, (x, y) in enumerate(zip(a, b)) for d in _diff(x, y, f"{path}[{n}]")]
    return [] if a == b else [f"{path}: postgres={a!r} memory={b!r}"]


def _same(pg: dict, mem: dict) -> None:
    problems = [f"{page}{d}" for page in pg for d in _diff(pg[page], mem[page])]
    assert not problems, "\n".join(problems[:25])


def _pages(w: _World) -> dict:
    """Each page the way its viewers see it, normalised."""
    from app.services.code_tour import build_tour
    from app.services.knowledge_risk import build_knowledge_risk
    from app.services.kt_planner import build_plan

    w.tenant(w.org)
    s, i, pid, org, p = w.store, w.index, w.pid, w.org, w.people
    pages = {"risk": build_knowledge_risk(s, i, pid, org),
             "plan": build_plan(s, i, pid, org, p["priya"]), "plan for ravi only": build_plan(s, i, pid, org, p["priya"], p["ravi"].id),
             "ravi's queue": build_plan(s, i, pid, org, p["ravi"]), "meera's queue": build_plan(s, i, pid, org, p["meera"]),
             "tour": build_tour(s, i, pid, org, p["priya"]), "tour in reading order": build_tour(s, i, pid, org, p["priya"], "reading"),
             "vijay's tour": build_tour(s, i, pid, org, p["vijay"])}
    for key, _ in COHORT if "asha" in p else ():
        pages[f"{key}'s tour"] = build_tour(s, i, pid, org, p[key])
    for name in pages:
        if "plan" in name or "queue" in name:
            _keys(pages[name], w)
    return _norm(pages, w)


def _demo_world() -> _World:
    """The real MODE=demo container, its fixed ids named the way _parity_world names its own."""
    from app.container import build_container

    c = build_container(Settings(mode="demo", gemini_api_key=""))
    w = _World(c.store, c.index, lambda org: None)
    w.org, w.pid = w.name("org-demo", "<org>"), w.name("proj-demo", "<project>")
    w.people = {key: c.store.get_user(w.name(f"u-{key}", f"<{key}>")) for key, *_ in PARITY_PEOPLE}
    w.people["admin"] = c.store.get_user(w.name("u-admin", "<admin>"))
    w.name("repo-demo", "<repo>")
    w.name("mtg-1", "<mtg-1>")
    w.ob = {"vijay": c.store.get_onboarding(w.name("ob-vijay", "<ob-vijay>"))}
    _name_tasks(w)
    return w


def _worlds(engine, store, tag: str) -> tuple[_World, _World]:
    from app.adapters.memory import MemoryIndex, MemoryStore
    from app.services import code_tour, knowledge

    knowledge.clear_search_memo()
    code_tour.clear_cache()
    pg = _parity_world(store, PgIndex(engine, HashEmbedder(768)), current_org.set, tag)
    mem = _parity_world(MemoryStore(), MemoryIndex(HashEmbedder(768)), lambda org: None, tag)
    return pg, mem


def test_three_pages_match_memory_on_postgres(engine, store):
    """Knowledge risk, the KT plan (team view, filter, each expert's queue) and the code tour (both orders, each new hire)
    are the same on Postgres as in memory, at the demo seed and again once a cohort joins. Stage 1 also equals the real
    demo container's pages."""
    from app.services import knowledge

    tag = uuid.uuid4().hex[:6]
    pg, mem = _worlds(engine, store, tag)
    seeded = _pages(pg)
    _same(seeded, _pages(mem))
    _same(seeded, _pages(_demo_world()))  # Postgres serves what MODE=demo serves
    assert [f["risk_score"] for f in seeded["risk"]["files"]] == [88, 75, 48, 0]
    assert seeded["risk"]["summary"]["highest_exposure"]["handover_minutes"] == 3.9
    assert [s["est_minutes"] for s in seeded["plan"]["sessions"]] == [4.0, 5.5]

    _cohort(pg, tag)
    _cohort(mem, tag)
    joined = _pages(pg)
    _same(joined, _pages(mem))

    # What made the two adapters disagree before, and the order both give now:
    risk = {f["path"]: f["demand"] for f in joined["risk"]["files"]}
    # tasks: due date, title by code point, then creation order (Postgres sorted titles in en_US, and ties in no fixed order)
    assert [d["ref"]["task_id"] for d in risk["engine/validate.py"] if d["kind"] == "first_pr"] == [
        f"<task {who}: First PR: Validate privileged init containers>" for who in ("vijay", "asha", "ben")]
    assert [d["ref"]["task_id"] for d in risk["webhooks/server.py"] if d["kind"] == "task"] == [
        f"<task {who}: Read and summarise the webhooks module>" for who in ("vijay", "asha", "ben")]
    # questions: newest first across hires (they followed whatever order the store returned the onboardings in)
    assert [d["ref"]["message_id"] for d in risk["webhooks/server.py"] if d["kind"] == "question"] == ["<msg-2>", "<msg-1>"]
    asked = {tuple((r["asked"], r["ref"] == {}) for r in i["reasons"] if r["kind"] == "question")
             for s in joined["plan"]["sessions"] for i in s["items"] if i["path"] == "webhooks/server.py"}
    assert asked - {()} == {((2, True),)}  # the planner counts both, naming neither (services/kt_planner.py _reason_dicts)
    # coverage gaps: index.all_paths by code point (Postgres sorted 'audit' before 'Tools')
    assert joined["plan"]["totals"]["gaps_before"] == ["Tools", "audit", "webhooks"]
    # first PRs on one stop: by start date, then the new hire's name (ties went to random ids)
    target = next(s for ch in joined["tour"]["chapters"] for s in ch["stops"] if s["first_prs"])
    assert [r["new_hire"] for r in target["first_prs"]] == ["Vijay", "Asha", "Ben"]
    assert [r["new_hire"] for ch in joined["asha's tour"]["chapters"] for s in ch["stops"] for r in s["first_prs"]] == ["Asha"]  # her own only
    assert joined["risk"]["partial"] is joined["plan"]["partial"] is joined["tour"]["partial"] is False
    knowledge.clear_search_memo()


def test_kt_plan_booking_on_postgres_matches_memory(engine, store, monkeypatch):
    """POST /api/kt-plan/schedule's store calls on Postgres, as ww_app under row-level security: create_meeting, audit
    and notify write rows that the tenant reads back and no other tenant sees, every step the same as in memory; and
    two simultaneous bookings of one session make one call (the advisory lock)."""
    import time
    from concurrent.futures import ThreadPoolExecutor

    from app.services import knowledge
    from app.services import kt_planner as P

    tag = uuid.uuid4().hex[:6]
    pg, mem = _worlds(engine, store, tag)
    for w in (pg, mem):
        _cohort(w, tag)

    def session_of(w: _World, who: str) -> dict:
        w.tenant(w.org)
        plan = P.build_plan(w.store, w.index, w.pid, w.org, w.people["priya"])
        return _keys(next(s for s in plan["sessions"] if (s["presenter"] or {}).get("user_id") == w.people[who].id), w)

    def book(w: _World, who: str, key: str) -> dict:
        w.tenant(w.org)
        return P.schedule(w.store, w.index, w.pid, w.org, w.people[who], key)  # unscheduled ('manual'): no calendar

    def audits(w: _World) -> list[dict]:
        if w is mem:
            rows = [r for r in mem.store.audit_rows if r["action"] == "kt_plan.scheduled"]
        else:
            rows = pg.store.rows("SELECT org_id, actor_id, action, target, detail FROM audit_log WHERE action = 'kt_plan.scheduled' ORDER BY id")
        return [{k: str(v) if isinstance(v, uuid.UUID) else v for k, v in r.items()} for r in rows]

    def effects(w: _World, booked: dict) -> dict:
        m = booked["meeting"]
        assert m.room.startswith("kt-") and len(m.room) == 39 and m.started_at is not None  # 'kt-' + a fresh UUID, now
        w.tenant(w.org)
        return _norm({
            "booked": {**_keys(booked, w), "meeting": m.model_dump(exclude={"room", "started_at"})},
            "meetings": [(x.id, x.title, x.status) for x in w.store.list_meetings(w.pid)],
            "notifications": [(n["kind"], n["text"], n["link"].replace(w.pid, "<project>").replace(m.id, "<meeting>"))
                              for n in w.store.notifications_for(w.people["meera"].id)],
            "audit": audits(w),
        }, w)

    # Priya books Meera's session: a call, an audit row, a notification for Meera
    seen = {}
    for w in (pg, mem):
        key = session_of(w, "meera")["key"]
        out = book(w, "priya", key)
        w.name(out["meeting"].id, "<booked meera>")
        assert out["notified_user_id"] == w.people["meera"].id
        seen[w] = effects(w, out)
    _same(seen[pg], seen[mem])
    assert seen[pg]["meetings"][0] == ["<booked meera>", "KT with Meera: webhooks/server.py", "scheduled"]
    [(kind, text, link)] = seen[pg]["notifications"]
    assert kind == "kt_request" and text.startswith("Priya (Manager) booked a KT session for you to present: 'KT with Meera: webhooks/server.py'")
    assert link == "/meetings?call=<meeting>&project=<project>"
    [row] = seen[pg]["audit"]
    assert (row["org_id"], row["actor_id"], row["target"], row["detail"]["presenter_id"]) == ("<org>", "<priya>", "<booked meera>", "<meera>")
    _same(_pages(pg), _pages(mem))  # the plan now marks the session booked, the same way on both

    for w in (pg, mem):
        key = session_of(w, "meera")["key"]
        with pytest.raises(P.AlreadyBooked):
            book(w, "priya", key)
        with pytest.raises(P.NotYourSession):
            book(w, "ravi", key)
        own = book(w, "ravi", session_of(w, "ravi")["key"])  # an expert books their own session: nobody to notify
        assert own["notified_user_id"] is None
        assert [a["actor_id"] for a in audits(w)] == [w.people["priya"].id, w.people["ravi"].id]

    # another tenant, or none, sees nothing of it and cannot book it
    meera_key = session_of(pg, "meera")["key"]
    rival, _ = _org(store, "rival")
    for tenant in (rival.org_id, None):
        current_org.set(tenant)
        assert store.list_meetings(pg.pid) == [] and store.notifications_for(pg.people["meera"].id) == [] and audits(pg) == []
        with pytest.raises(P.PlanChanged):  # row-level security hides the project's code: no plan, no session
            P.schedule(store, pg.index, pg.pid, rival.org_id, rival, meera_key)
    current_org.set(pg.org)
    assert len(store.list_meetings(pg.pid)) == 3

    # two people book the same session at the same moment: one call
    slow_create = store.create_meeting

    def create_meeting(*a, **kw):
        time.sleep(0.3)  # widen the window between the booked check and the insert
        return slow_create(*a, **kw)

    monkeypatch.setattr(store, "create_meeting", create_meeting)
    priya_key = session_of(pg, "priya")["key"]

    def race(_):
        current_org.set(pg.org)  # a worker thread starts with no tenant
        try:
            return book(pg, "priya", priya_key)["meeting"].id
        except P.AlreadyBooked:
            return None

    with ThreadPoolExecutor(2) as pool:
        made = [m for m in pool.map(race, range(2)) if m]
    assert len(made) == 1
    assert [m.id for m in store.list_meetings(pg.pid) if m.title.startswith("KT with Priya")] == made
    knowledge.clear_search_memo()


def test_simultaneous_bookings_never_exhaust_the_pool(db, store):
    """pool_size + max_overflow bookings at once, on the production pool (5 + 5, a 10 s checkout timeout). A booking used
    to hold a pooled connection for an advisory lock while it rebuilt the plan on others, so ten at once took the whole
    pool and failed with TimeoutError (a 500), and every other request waited. Now no booking holds a connection while
    it works: unknown keys are refused, one of the bookings of a real session makes the call, and a bystander's query is
    answered at once."""
    import threading
    import time
    from concurrent.futures import ThreadPoolExecutor

    from app.services import knowledge
    from app.services import kt_planner as P

    settings = Settings(mode="demo")
    eng = make_engine(db, settings.db_pool_size, settings.db_max_overflow)
    pstore, pindex = PgStore(eng), PgIndex(eng, HashEmbedder(768))
    knowledge.clear_search_memo()
    w = _parity_world(pstore, pindex, current_org.set, uuid.uuid4().hex[:6])
    priya = w.people["priya"]
    real = next(s for s in P.build_plan(pstore, pindex, w.pid, w.org, priya)["sessions"] if s["presenter"])["key"]
    n = settings.db_pool_size + settings.db_max_overflow
    keys = [uuid.uuid4().hex[:12] for _ in range(n // 2)] + [real] * (n - n // 2)
    barrier, bystander = threading.Barrier(n + 1), {}

    def book(key):
        current_org.set(w.org)
        barrier.wait()
        t0 = time.monotonic()
        try:
            P.schedule(pstore, pindex, w.pid, w.org, priya, key)
            out = "booked"
        except Exception as e:  # noqa: BLE001 - classified below
            out = type(e).__name__
        return out, time.monotonic() - t0

    def other_request():
        current_org.set(w.org)
        barrier.wait()
        time.sleep(0.05)
        t0 = time.monotonic()
        pstore.rows("SELECT 1 AS ok")
        bystander["s"] = time.monotonic() - t0

    try:
        side = threading.Thread(target=other_request)
        side.start()
        with ThreadPoolExecutor(n) as pool:
            results = list(pool.map(book, keys))
        side.join(30)
        outcomes = [r for r, _ in results]
        assert "TimeoutError" not in outcomes, results
        assert outcomes.count("PlanChanged") == n // 2 and outcomes.count("booked") == 1
        assert outcomes.count("AlreadyBooked") == n - n // 2 - 1
        assert max(t for _, t in results) < 5 and bystander["s"] < 1, (results, bystander)
        current_org.set(w.org)
        assert [m.kt_key for m in pstore.list_meetings(w.pid) if m.kt_key] == [real]
    finally:
        eng.dispose()
        knowledge.clear_search_memo()


def test_batched_reads_on_postgres(engine, store):
    """The Store reads the insight views batch (chat_histories, meeting_notes_many, open_expert_requests) answer what the
    one-by-one reads answer, under row-level security, and reading() runs them in one transaction on one connection."""
    from sqlalchemy import event

    from app.agents.schemas import MeetingResult, QAResult

    admin, project = _org(store, "batch")
    pid = project.id
    ids = []
    for key in ("a", "b"):
        store.create_invitation(admin.org_id, pid, f"{key}@batch.example.com", "new_hire", key.upper(), key, admin.id)
        current_org.set(None)
        ids.append(store.accept_invitations(f"uid-batch-{key}-{uuid.uuid4().hex[:4]}", f"{key}@batch.example.com", key.upper(), admin.org_id).id)
        current_org.set(admin.org_id)
    for n in range(4):
        store.save_chat(pid, ids[0], f"question {n}", QAResult(status="not_enough_information", answer=""))
    msg = store.save_chat(pid, ids[1], "only one", QAResult(status="answered", answer="x", confidence=0.7))
    m = store.create_meeting(pid, "KT #1: x", admin.id)
    store.save_meeting_result(MeetingResult(meeting_id=m.id, project_id=pid, title=m.title, summary="s", decisions=[], action_items=[],
                                            terms=[], utterances=[], screen_moments=[], kt_links=[]))
    store.create_expert_request(pid, "who?", ids[1], ids[1], msg)

    many = store.chat_histories(pid, ids, 3)
    # chat_history is the same rows, each with the expert request it was handed to (none here)
    assert {u: [{**r, "expert": None} for r in rows] for u, rows in many.items()} == {u: store.chat_history(pid, u, 3) for u in ids}
    assert [len(many[u]) for u in ids] == [3, 1]
    assert store.meeting_notes_many([m.id, str(uuid.uuid4()), "not-a-uuid"]) == {m.id: store.meeting_notes(m.id)}
    assert [r["question"] for r in store.open_expert_requests(pid)] == ["who?"]

    checkouts = []

    def counted(*_):
        checkouts.append(1)

    event.listen(engine.pool, "checkout", counted)
    try:
        with store.reading():
            store.chat_histories(pid, ids, 3)
            store.list_meetings(pid)
            store.meeting_notes_many([m.id])
            assert store.kt_links(pid) == []
    finally:
        event.remove(engine.pool, "checkout", counted)
    assert len(checkouts) == 1
    with pytest.raises(Exception):  # read-only: a write inside a page's read block is a bug, and it fails
        with store.reading():
            store.notify(admin.id, "x", "y")
    rival, _ = _org(store, "batch-rival")
    assert store.chat_histories(pid, ids, 3) == {u: [] for u in ids} and store.open_expert_requests(pid) == []  # row-level security


def test_three_pages_over_http_on_postgres(engine, store, monkeypatch):
    """The new routes over HTTP with production sign-in (Firebase checks stubbed) and Postgres as ww_app: the tenant
    reaches GET /api/knowledge-risk, /api/kt-plan and /api/tour and POST /api/kt-plan/schedule, their JSON equals the
    memory adapter's for the same scenario, and another company gets 404s."""
    import firebase_admin.auth
    from fastapi.encoders import jsonable_encoder
    from fastapi.testclient import TestClient

    from app.config import get_settings
    from app.main import create_app
    from app.services import knowledge
    from app.services.code_tour import build_tour
    from app.services.knowledge_risk import build_knowledge_risk
    from app.services.kt_planner import build_plan

    tag = uuid.uuid4().hex[:6]
    pg, mem = _worlds(engine, store, tag)
    for w in (pg, mem):
        _cohort(w, tag)
    store.create_organization(f"uid-par-rival-{tag}", f"rival-{tag}@par.example.com", "Rex", f"Rival {tag}", "Elsewhere")
    current_org.set(pg.org)
    tokens = {f"t-{key}": {"uid": f"uid-par-{key}-{tag}", "email": f"{key}-{tag}@par.example.com", "email_verified": True, "name": key}
              for key in ("admin", "priya", "vijay", "ravi", "meera", "asha", "rival")}
    monkeypatch.setattr(firebase_admin.auth, "verify_id_token", lambda tok, clock_skew_seconds=0: tokens[tok])
    _fake = lambda tok, clock_skew_seconds=0: tokens[tok]
    monkeypatch.setattr("app.firebase_tokens.verify", lambda tok, *_a, **_k: _fake(tok))  # real Firebase path
    H = {key[2:]: {"Authorization": f"Bearer {key}"} for key in tokens}
    pid = pg.pid

    def memory(page: dict) -> dict:
        return _norm(jsonable_encoder(page), mem)

    m = mem.people
    get_settings.cache_clear()
    with TestClient(create_app()) as client:
        client.app.state.container = Container(Settings(mode="gcp"), pg.deps, pg.graph)

        def page(path: str, who: str, w: _World = pg):
            r = client.get(path, headers=H[who])
            assert r.status_code == 200, r.text
            return _norm(_keys(r.json(), w) if "kt-plan" in path else r.json(), w)

        assert page(f"/api/knowledge-risk?project_id={pid}", "priya") == memory(build_knowledge_risk(mem.store, mem.index, mem.pid, mem.org))
        team = page(f"/api/kt-plan?project_id={pid}", "priya")
        assert team == memory(_keys(build_plan(mem.store, mem.index, mem.pid, mem.org, m["priya"]), mem))
        assert page(f"/api/kt-plan?project_id={pid}", "meera") == memory(_keys(build_plan(mem.store, mem.index, mem.pid, mem.org, m["meera"]), mem))
        for who in ("priya", "asha"):
            assert page(f"/api/tour?project_id={pid}", who) == memory(build_tour(mem.store, mem.index, mem.pid, mem.org, m[who]))

        # book Meera's session, naming the project in another spelling of its UUID: the service gets the canonical id
        [meera_session] = [s for s in page(f"/api/kt-plan?project_id={pid}", "meera")["sessions"]]
        raw_key = next(k for k, v in pg.ids.items() if v == meera_session["key"])
        r = client.post("/api/kt-plan/schedule", json={"project_id": pid.upper(), "key": raw_key}, headers=H["priya"])
        assert r.status_code == 200, r.text
        booked = r.json()
        assert booked["notified_user_id"] == pg.people["meera"].id and booked["meeting"]["project_id"] == pid
        [note] = [n for n in client.get("/api/notifications", headers=H["meera"]).json() if n["kind"] == "kt_request"]
        assert note["link"] == f"/meetings?call={booked['meeting']['id']}&project={pid}"
        assert [r["target"] for r in store.rows("SELECT target FROM audit_log WHERE action = 'kt_plan.scheduled'")] == [booked["meeting"]["id"]]
        again = client.post("/api/kt-plan/schedule", json={"project_id": pid, "key": raw_key}, headers=H["priya"])
        assert (again.status_code, again.json()["detail"]) == (409, "already_booked")
        assert client.post("/api/kt-plan/schedule", json={"project_id": pid, "key": raw_key}, headers=H["ravi"]).status_code == 403
        assert client.get(f"/api/kt-plan?project_id={pid}", headers=H["vijay"]).status_code == 403  # new hires have no plan view

        for path in (f"/api/knowledge-risk?project_id={pid}", f"/api/kt-plan?project_id={pid}", f"/api/tour?project_id={pid}"):
            assert client.get(path, headers=H["rival"]).status_code == 404
        assert client.post("/api/kt-plan/schedule", json={"project_id": pid, "key": raw_key}, headers=H["rival"]).status_code == 404
    get_settings.cache_clear()
    knowledge.clear_search_memo()


def test_live_changes_are_announced_by_postgres(db, store):
    """Triggers announce row changes on ww_live; the listener relays them to the browsers of that organisation only."""
    import asyncio

    from app.live import LiveHub, PgListener

    user, project = _org(store, "live")
    other, _ = _org(store, "dark")

    async def run():
        hub = LiveHub()
        hub.bind(asyncio.get_running_loop())
        sub = hub.subscribe(user.org_id, user.id)
        listener = PgListener(db, hub)
        listener.start()
        try:
            await asyncio.sleep(1.0)  # LISTEN is in place
            current_org.set(other.org_id)
            await asyncio.to_thread(store.notify, other.id, "info", "not yours", "/x")
            current_org.set(user.org_id)
            await asyncio.to_thread(store.notify, user.id, "info", "yours", "/x")
            assert await asyncio.wait_for(sub.queue.get(), 5) == {"t": "notifications", "p": None}
            await asyncio.to_thread(store.create_meeting, project.id, "KT #1", user.id)
            assert await asyncio.wait_for(sub.queue.get(), 5) == {"t": "meetings", "p": str(project.id)}
            await asyncio.to_thread(store.set_member_role, user.org_id, user.id, "admin")  # a member's row changed (013)
            assert await asyncio.wait_for(sub.queue.get(), 5) == {"t": "people", "p": None}
            assert sub.queue.empty()  # the other organisation's notification never arrived
        finally:
            listener.stop()

    asyncio.run(run())


def test_watcher_lists_repositories_connected_by_url_across_organisations(store):
    a, pa = _org(store, "wa")
    store.create_repo(pa.id, "acme/by-url", None, "main")
    b, pb = _org(store, "wb")
    store.create_repo(pb.id, "beta/app", "4242", "main")
    current_org.set(None)  # the watcher runs without a tenant: the definer function sees every organisation
    names = {r["full_name"]: r for r in store.watched_repositories()}
    assert "acme/by-url" in names and "beta/app" not in names
    assert str(names["acme/by-url"]["org_id"]) == str(a.org_id) and names["acme/by-url"]["status"] == "pending"


def test_outbox_claims_skip_events_in_hand(store):
    row = store.outbox_add("reviews", {"type": "noop", "dedupe": f"skip:{uuid.uuid4().hex}"})
    store.run("UPDATE outbox SET next_attempt_at = now() - interval '1 second' WHERE id = CAST(:i AS uuid)", i=row)
    assert row not in [r["id"] for r in store.outbox_due(50, skip=(row,))]
    assert row in [str(r["id"]) for r in store.outbox_due(50)]
    assert store.outbox_attempts(row) == 1


def test_scheduling_on_postgres(store):
    """011_scheduling as ww_app: a session's calendar fields round-trip, one meeting per calendar event, an import claims a
    session once, the poller's definer function lists ended sessions across organisations, and connections and Calendly
    links stay inside their organisation."""
    from datetime import datetime, timedelta, timezone

    from app.ports import Attendee

    a, pa = _org(store, "sa")
    now = datetime.now(timezone.utc)
    people = [Attendee(email=a.email, name=a.name, user_id=a.id), Attendee(email="guest@else.dev", name="Guest")]
    m = store.create_meeting(pa.id, "KT on Meet", a.id, provider="google", starts_at=now - timedelta(hours=2),
                             ends_at=now - timedelta(hours=1), attendees=people)
    assert (m.provider, m.started_at, m.attendees) == ("google", m.starts_at, people) and m.room.startswith("kt-")
    store.update_meeting(m.id, join_url="https://meet.google.com/abc-defg-hij", calendar_event_id="evt-1",
                         attendees=people[:1])
    got = store.get_meeting(m.id)
    assert (got.join_url, got.calendar_event_id, got.attendees) == ("https://meet.google.com/abc-defg-hij", "evt-1", people[:1])
    assert store.meeting_by_event("google", "evt-1").id == m.id and store.meeting_by_event("calendly", "evt-1") is None
    # one meeting per calendar event (a redelivered Calendly webhook)
    cal = store.create_meeting(pa.id, "Calendly", a.id, provider="calendly", calendar_event_id="https://api.calendly.com/scheduled_events/E1")
    assert cal and store.create_meeting(pa.id, "Again", a.id, provider="calendly", calendar_event_id=cal.calendar_event_id) is None
    old = store.create_meeting(pa.id, "Held in Zoom", a.id, starts_at=now - timedelta(days=3), ends_at=now - timedelta(days=3) + timedelta(hours=1))
    future = store.create_meeting(pa.id, "Next week", a.id, provider="google", starts_at=now + timedelta(days=7), ends_at=now + timedelta(days=7, hours=1))
    store.update_meeting(future.id, calendar_event_id="evt-2")

    b, pb = _org(store, "sb")
    other = store.create_meeting(pb.id, "B's KT", b.id, provider="google", starts_at=now - timedelta(hours=2), ends_at=now - timedelta(hours=1))
    store.update_meeting(other.id, calendar_event_id="evt-b")
    assert store.meeting_by_event("google", "evt-1") is None  # A's event is invisible from B
    current_org.set(None)  # the poller runs without a tenant: the definer function sees every organisation
    due = {str(r["id"]): r for r in store.meetings_awaiting_recording(600, 48 * 3600, 50)}
    assert m.id in due and other.id in due and old.id in due and future.id not in due and cal.id not in due
    assert str(due[m.id]["org_id"]) == str(a.org_id) and due[m.id]["calendar_event_id"] == "evt-1"
    assert [r for r in store.meetings_awaiting_recording(600, 48 * 3600, 1)]  # the limit holds

    current_org.set(a.org_id)
    assert store.claim_meeting_import(m.id, "gs://b/recordings/r.mp4", 754.0)
    assert not store.claim_meeting_import(m.id, "gs://b/recordings/s.mp4", 1.0)  # once
    assert store.get_meeting(m.id).status == "processing" and store.get_meeting(m.id).recording_uri == "gs://b/recordings/r.mp4"
    assert store.expire_meeting(old.id) and not store.expire_meeting(old.id) and store.get_meeting(old.id).status == "ended"
    assert store.claim_meeting_import(old.id, "gs://b/recordings/late.mp4", 60.0)  # an ended session can still get its recording
    assert store.discard_meeting(future.id) and store.get_meeting(future.id) is None
    assert not store.discard_meeting(m.id)  # a session with a recording is never discarded

    store.save_calendar_connection(a.org_id, "google", "kt@a.dev", "gAAAA-encrypted", "openid email", a.id)
    store.save_calendar_connection(a.org_id, "google", "kt2@a.dev", "gAAAA-again", "openid", a.id)  # a reconnect replaces it
    assert store.calendar_connection(a.org_id)["account_email"] == "kt2@a.dev"
    store.set_scheduling_link(a.org_id, a.id, "calendly", "https://calendly.com/a/kt")
    assert store.scheduling_link(a.id) == "https://calendly.com/a/kt" and store.scheduling_links(pa.id) == {a.id: "https://calendly.com/a/kt"}
    current_org.set(b.org_id)
    assert store.calendar_connection(a.org_id) is None and store.scheduling_link(a.id) is None and store.scheduling_links(pa.id) == {}
    assert not store.delete_calendar_connection(a.org_id)
    current_org.set(a.org_id)
    store.set_scheduling_link(a.org_id, a.id, "calendly", None)
    assert store.scheduling_link(a.id) is None
    assert store.delete_calendar_connection(a.org_id) and store.calendar_connection(a.org_id) is None


def test_members_roles_and_deactivation(store, monkeypatch):
    """Members administration on Postgres as ww_app, with MemoryStore's rules (tests/test_members.py): an organisation always
    keeps an active admin, a failed change is rolled back, and sign-in reads deactivation through auth_lookup (013) before
    the tenant is known."""
    from types import SimpleNamespace

    from fastapi import HTTPException

    from app.api import auth
    from app.ports import LastAdmin

    admin, project = _org(store, "mem")
    tag = uuid.uuid4().hex[:6]
    email, uid = f"esi-{tag}@example.com", f"uid-esi-{tag}"
    store.create_invitation(admin.org_id, project.id, email, "expert", "Esi", None, admin.id)
    current_org.set(None)
    expert = store.accept_invitations(uid, email, "Esi", admin.org_id)
    current_org.set(admin.org_id)

    rows = {r["id"]: r for r in store.org_members(admin.org_id)}
    assert set(rows) == {admin.id, expert.id}
    assert rows[expert.id] == {"id": expert.id, "name": "Esi", "email": email, "role": "expert", "status": "active",
                               "projects": [{"id": project.id, "name": project.name}]}
    assert store.org_member(admin.org_id, admin.id)["role"] == "admin" and store.org_member(admin.org_id, "not-a-uuid") is None

    for change in (lambda: store.set_member_role(admin.org_id, admin.id, "manager"),
                   lambda: store.set_member_status(admin.org_id, admin.id, "deactivated")):
        with pytest.raises(LastAdmin):
            change()
    assert (store.get_user(admin.id).role, store.get_user(admin.id).status) == ("admin", "active")  # rolled back

    assert store.set_member_status(admin.org_id, expert.id, "deactivated").status == "deactivated"
    assert store.org_member(admin.org_id, expert.id)["status"] == "deactivated"
    assert {u.id: u.status for u in store.members(project.id)}[expert.id] == "deactivated"
    current_org.set(None)
    assert store.user_by_firebase(uid).status == "deactivated"
    monkeypatch.setattr(auth, "_verify", lambda c, a: auth.Identity(uid=uid, email=email, email_verified=True, name="Esi"))
    with pytest.raises(HTTPException) as refused:
        auth._resolve(SimpleNamespace(settings=SimpleNamespace(is_demo=False), store=store), "Bearer t", None)
    assert refused.value.status_code == 403 and refused.value.detail["code"] == "deactivated"
    current_org.set(admin.org_id)
    assert store.set_member_status(admin.org_id, expert.id, "active").status == "active"

    store.set_member_role(admin.org_id, expert.id, "admin")  # a second active admin: the first may step down
    assert store.set_member_role(admin.org_id, admin.id, "manager").role == "manager"
    store.set_member_role(admin.org_id, admin.id, "admin")
    store.set_member_status(admin.org_id, expert.id, "deactivated")  # a deactivated admin is not the organisation's admin
    assert store.leave_organization(admin.id) is False
    other, _ = _org(store, "mem-other")
    current_org.set(admin.org_id)  # another organisation's member is not one of ours: row-level security hides them
    assert store.set_member_role(admin.org_id, other.id, "expert") is None and store.org_member(admin.org_id, other.id) is None


def test_agent_runs_keep_their_trace_id(store):
    """With LangSmith tracing on, each agent run records its trace (agent_runs.langsmith_run_id), and GET /api/runs lists it."""
    _, project = _org(store, "trace")
    store.record_run(project.id, "qa", "chat.question", "succeeded", {"keys": ["result"], "trace_id": "0b5f6c1e-trace"})
    store.record_run(project.id, "qa", "chat.question", "succeeded", {"keys": ["result"]})
    assert sorted(r["trace_id"] or "" for r in store.list_runs(project.id)) == ["", "0b5f6c1e-trace"]


def test_agent_runs_and_file_chunks_on_postgres(engine, store):
    """The agentic runtime's storage: a run stored under the client's run id (a taken id gets a new one, never an overwrite),
    read back by id in its own organisation only; and Index.file_chunks, one file's chunks in line order, like MemoryIndex."""
    from app.adapters.memory import MemoryIndex
    from app.services.chunking import chunk_code

    _, project = _org(store, "runs")
    rid = str(uuid.uuid4())
    detail = {"summary": "Routed a question: tier 1, answered from sources", "steps": [{"n": 1, "kind": "tool", "text": "Read a.py"}],
              "user_id": "u1", "visibility": "shared"}
    assert store.record_run(project.id, "guide", "ask.route", "succeeded", detail, run_id=rid) == rid
    again = store.record_run(project.id, "guide", "ask.route", "succeeded", {"summary": "x"}, run_id=rid)
    assert again != rid and store.get_run(rid)["detail"]["steps"][0]["text"] == "Read a.py"
    assert store.record_run(project.id, "guide", "ask.route", "succeeded", {"summary": "y"}, run_id="not-a-uuid") != "not-a-uuid"
    assert store.get_run("not-a-uuid") is None
    got = store.get_run(rid)
    assert got["agent"] == "guide" and str(got["project_id"]) == project.id and got["status"] == "succeeded"
    _org(store, "other-runs")  # another organisation does not see it
    assert store.get_run(rid) is None

    _, project = _org(store, "files")
    text = "\n".join(["import os", ""] + [f"def f{i}():\n    return {i}\n" for i in range(3)])
    chunks = chunk_code(project.id, "pkg/mod.py", text, "sha1") + chunk_code(project.id, "pkg/other.py", "x = 1\n", "sha1")
    pg, mem = PgIndex(engine, HashEmbedder(768)), MemoryIndex(HashEmbedder(768))
    pg.upsert(chunks)
    mem.upsert(chunks)
    assert [c.model_dump() for c in pg.file_chunks(project.id, "pkg/mod.py")] == [c.model_dump() for c in mem.file_chunks(project.id, "pkg/mod.py")]
    assert [c.ref["start_line"] for c in pg.file_chunks(project.id, "pkg/mod.py")] == sorted(c.ref["start_line"] for c in chunks if c.ref["path"] == "pkg/mod.py")
    assert pg.file_chunks(project.id, "nope.py") == []
