"""Regression tests for the third review round's store changes. Each runs on MemoryStore and, with TEST_PG_ADMIN_URL set,
on PostgreSQL as `ww_app` (migrated by a non-superuser owner, as on Cloud SQL)."""
from __future__ import annotations

import os
import shutil
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

import psycopg
import pytest

from app.adapters.memory import LocalRepoSource, MemoryStore
from app.adapters.postgres import PgIndex, PgStore, current_org, make_engine
from app.agents.core.schemas import Chunk, KTLink, ProposedTask, ReviewResult
from app.ports import RepoBusy
from app.services.embeddings import HashEmbedder

ADMIN = os.environ.get("TEST_PG_ADMIN_URL")
needs_pg = pytest.mark.skipif(not ADMIN, reason="set TEST_PG_ADMIN_URL to run the Postgres tests")
SAMPLE = Path(__file__).resolve().parents[1] / "sample_repo"


def _server(url: str) -> str:
    return url.rsplit("/", 1)[0]


def _create_db(tag: str):
    """A database owned by a non-superuser. ww_app / ww_definer are cluster-wide: if they exist, the owner gets the admin
    rights it would have had by creating them itself."""
    name, owner = f"ww_r3_{tag}_{uuid.uuid4().hex[:6]}", f"own_ww_r3_{tag}_{uuid.uuid4().hex[:6]}"
    with psycopg.connect(ADMIN, autocommit=True) as c:
        c.execute(f"CREATE ROLE {owner} LOGIN CREATEROLE NOSUPERUSER PASSWORD 'owner-pw'")
        for role in ("ww_app", "ww_definer"):
            if c.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)).fetchone():
                c.execute(f"GRANT {role} TO {owner} WITH ADMIN OPTION, INHERIT FALSE, SET FALSE")
        c.execute(f"CREATE DATABASE {name} OWNER {owner}")
    with psycopg.connect(f"{_server(ADMIN)}/{name}", autocommit=True) as c:
        c.execute("CREATE EXTENSION vector; CREATE EXTENSION pgcrypto")
    host = ADMIN.rsplit("/", 1)[0].split("@", 1)[1]
    return name, owner, f"postgresql://{owner}:owner-pw@{host}/{name}", f"postgresql+psycopg://ww_app:app-pw@{host}/{name}"


def _drop_db(name: str, owner: str) -> None:
    with psycopg.connect(ADMIN, autocommit=True) as c:
        c.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        c.execute(f'DROP OWNED BY "{owner}" CASCADE')
        c.execute(f'DROP ROLE "{owner}"')


@pytest.fixture(scope="module")
def pg():
    if not ADMIN:
        pytest.skip("set TEST_PG_ADMIN_URL to run the Postgres tests")
    from app import migrate

    name, owner, owner_url, app_url = _create_db("store")
    assert migrate.run(owner_url, "app-pw")[-1] == max(p.stem for p in migrate.MIGRATIONS.glob("*.sql"))  # all of them, in order
    engine = make_engine(app_url)
    yield {"engine": engine, "name": name, "su": f"{_server(ADMIN)}/{name}", "app": app_url}
    engine.dispose()
    _drop_db(name, owner)


@pytest.fixture(params=["memory", pytest.param("pg", marks=needs_pg)])
def store(request):
    current_org.set(None)
    yield MemoryStore() if request.param == "memory" else PgStore(request.getfixturevalue("pg")["engine"])
    current_org.set(None)


def _org(store, label: str):
    tag = uuid.uuid4().hex[:6]
    admin = store.create_organization(f"uid-{label}-{tag}", f"{label}-{tag}@example.com", f"{label} admin", f"{label} {tag} Inc", "Main")
    current_org.set(admin.org_id)
    return admin, store.projects_for(admin)[0]


def _join(store, admin, project, label: str, role: str = "new_hire", github_login: str | None = None):
    email = f"{label}-{uuid.uuid4().hex[:6]}@example.com"
    store.create_invitation(admin.org_id, project.id, email, role, label, github_login, admin.id)
    current_org.set(None)
    user = store.accept_invitations(f"uid-{email}", email, label, admin.org_id)
    current_org.set(admin.org_id)
    return user


def _onboarding(store):
    admin, project = _org(store, "ob")
    hire = _join(store, admin, project, "hire")
    return admin, project, hire, store.create_onboarding(project.id, hire.id, admin.id, date.today(), "Engineer")


def _result(summary="ok"):
    return ReviewResult(recommendation="looks_good", summary=summary, findings=[], questions=["Why?"])


# ---------- data#0: definer functions never read the caller's temporary tables ----------
@needs_pg
def test_app_role_cannot_shadow_a_table_inside_a_definer_function(pg):
    admin, project = _org(PgStore(pg["engine"]), "victim")
    plain = pg["app"].replace("postgresql+psycopg://", "postgresql://")
    with psycopg.connect(plain) as c:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute("CREATE TEMP TABLE invitations (LIKE public.invitations)")  # the migrator revoked TEMP from PUBLIC
    with psycopg.connect(pg["su"], autocommit=True) as c:  # even if TEMP were granted again, pg_temp is searched last
        c.execute(f'GRANT TEMPORARY ON DATABASE "{pg["name"]}" TO ww_app')
    try:
        with psycopg.connect(plain, autocommit=True) as c:
            c.execute("CREATE TEMP TABLE invitations (LIKE public.invitations INCLUDING DEFAULTS)")
            c.execute("GRANT ALL ON invitations TO ww_definer")
            c.execute("INSERT INTO invitations (org_id, email, role) VALUES (%s, 'mallory@evil.example', 'admin')", (admin.org_id,))
            got = c.execute("SELECT accept_invitations('uid-mallory', 'mallory@evil.example', 'M', %s)", (admin.org_id,)).fetchone()[0]
            assert got is None  # the definer function read the real table, where there is no such invitation
            assert c.execute("SELECT count(*) FROM pending_invitations('mallory@evil.example')").fetchone()[0] == 0
    finally:
        with psycopg.connect(pg["su"], autocommit=True) as c:
            c.execute(f'REVOKE TEMPORARY ON DATABASE "{pg["name"]}" FROM ww_app')


@needs_pg
def test_migrator_refuses_a_definer_function_without_pg_temp_last(pg):
    from app import migrate

    with psycopg.connect(pg["su"]) as c:  # rolled back on exit
        c.execute("CREATE FUNCTION sneaky() RETURNS int LANGUAGE sql SECURITY DEFINER SET search_path = public AS 'SELECT 1'")
        with pytest.raises(SystemExit, match="sneaky"):
            migrate.verify(c)
        c.rollback()


# ---------- data#1 / fix#4: migration 003 reconciles projects that already hold several repositories ----------
@needs_pg
def test_migrations_reconcile_existing_repositories_and_commit_file_by_file(tmp_path):
    from app import migrate

    name, owner, owner_url, _ = _create_db("mig")
    try:
        real = migrate.MIGRATIONS
        for f in ("001_init.sql", "002_production.sql"):
            shutil.copy(real / f, tmp_path / f)
        (tmp_path / "002_zz_broken.sql").write_text("SELECT no_such_function();")
        migrate.MIGRATIONS = tmp_path
        try:
            with pytest.raises(psycopg.Error):
                migrate.run(owner_url, "app-pw")  # the broken file fails; the files before it stay applied
        finally:
            migrate.MIGRATIONS = real
        su = f"{_server(ADMIN)}/{name}"
        with psycopg.connect(su, autocommit=True) as c:
            assert [r[0] for r in c.execute("SELECT version FROM schema_migrations ORDER BY 1")] == ["001_init", "002_production"]
            org = c.execute("INSERT INTO organizations (name) VALUES ('Acme') RETURNING id").fetchone()[0]
            user = c.execute("INSERT INTO users (org_id, email, name, role) VALUES (%s, 'a@x', 'A', 'admin') RETURNING id", (org,)).fetchone()[0]
            hire = c.execute("INSERT INTO users (org_id, email, name, role) VALUES (%s, 'h@x', 'H', 'new_hire') RETURNING id", (org,)).fetchone()[0]
            pid = c.execute("INSERT INTO projects (org_id, name) VALUES (%s, 'Payments') RETURNING id", (org,)).fetchone()[0]
            c.execute("INSERT INTO project_members (project_id, user_id, org_id) VALUES (%s, %s, %s)", (pid, user, org))
            for full_name, sha in (("acme/first", "s1"), ("acme/FIRST", None), ("acme/second", "s2")):
                c.execute("INSERT INTO repositories (org_id, project_id, full_name, last_indexed_sha) VALUES (%s, %s, %s, %s)", (org, pid, full_name, sha))
            for _ in range(2):  # a double click before onboardings were unique
                c.execute("INSERT INTO onboardings (org_id, project_id, new_hire_id, manager_id, start_date) VALUES (%s, %s, %s, %s, current_date)",
                          (org, pid, hire, user))
        assert migrate.run(owner_url, "app-pw") == sorted(p.stem for p in real.glob("*.sql"))[2:]  # 003_review_fixes onwards
        with psycopg.connect(su, autocommit=True) as c:
            rows = c.execute("""SELECT p.name, r.full_name, r.status::text, (SELECT count(*) FROM project_members m WHERE m.project_id = p.id)
                                FROM repositories r JOIN projects p ON p.id = r.project_id ORDER BY 1""").fetchall()
            assert rows == [("Payments", "acme/first", "failed", 1), ("Payments - acme/second", "acme/second", "failed", 1)]
            assert [r[0] for r in c.execute("SELECT status FROM onboardings ORDER BY created_at, id")] == ["active", "paused"]
            assert c.execute("""SELECT count(*) FROM pg_class WHERE relname IN ('repositories', 'projects', 'project_members', 'chunks', 'onboardings')
                                AND relforcerowsecurity""").fetchone()[0] == 5
    finally:
        _drop_db(name, owner)


# ---------- data#2: a re-invitation becomes the newest, so its role and GitHub login win ----------
def test_reinviting_someone_updates_the_role_they_join_with(store):
    admin, project = _org(store, "reinv")
    other = store.create_project(admin.org_id, "Other", "", admin.id)
    email = f"nia-{uuid.uuid4().hex[:6]}@example.com"
    store.create_invitation(admin.org_id, project.id, email, "new_hire", "Nia", None, admin.id)
    store.create_invitation(admin.org_id, other.id, email, "expert", "Nia", None, admin.id)
    store.create_invitation(admin.org_id, project.id, email, "manager", "Nia", "nia-gh", admin.id)  # the correction
    assert len(store.invitations(project.id)) == 1
    current_org.set(None)
    [pending] = store.pending_invitations(email)
    assert pending["role"] == "manager" and sorted(pending["projects"]) == sorted([project.name, other.name])
    assert pending["invited_by_email"] == admin.email and abs((date.fromisoformat(pending["org_created_at"]) - date.today()).days) <= 1
    user = store.accept_invitations(f"uid-{email}", email, "Nia", admin.org_id)
    assert user.role == "manager" and user.github_login == "nia-gh"


# ---------- data#3: a re-review of the same commit from a new PR points at the new PR ----------
def test_rereview_of_the_same_commit_stores_the_new_pr(store):
    _, _, _, ob = _onboarding(store)
    [task] = store.create_tasks(ob.id, [ProposedTask(title="t", description="d", kind="code", due_in_days=3)], date.today())
    first = store.save_review(task.id, 5, "https://x/pull/5", "sha1", _result("first"))
    store.decide_review(first.id, "approved", None)
    second = store.save_review(task.id, 6, "https://x/pull/6", "sha1", _result("second"))
    got = store.get_review(second.id)
    assert second.id == first.id and (got.pr_number, got.pr_url, got.result.summary) == (6, "https://x/pull/6", "second")
    assert got.manager_decision is None


# ---------- data#4: a concurrent run's fresh proposals are not discarded as orphans ----------
def test_fresh_proposals_survive_an_orphan_sweep(store):
    _, _, _, ob = _onboarding(store)
    [task] = store.create_tasks(ob.id, [ProposedTask(title="t", description="d", kind="learn", due_in_days=3)], date.today())
    assert store.discard_orphan_proposals(ob.id, grace_s=600) == 0
    assert store.get_task(task.id).status == "proposed"
    assert store.discard_orphan_proposals(ob.id) == 1


# ---------- auth-api#4: one active onboarding per person and project, even under concurrency ----------
def test_starting_an_onboarding_twice_returns_the_first(store):
    admin, project, hire, ob = _onboarding(store)

    def start(_):
        current_org.set(admin.org_id)
        return store.create_onboarding(project.id, hire.id, admin.id, date.today(), "Engineer").id

    with ThreadPoolExecutor(4) as pool:
        assert set(pool.map(start, range(8))) == {ob.id}
    assert [o.id for o in store.onboardings(project.id)] == [ob.id]


# ---------- fix#36: a link to a chunk being deleted right now falls back to file + symbol ----------
@needs_pg
def test_kt_link_waits_for_an_in_flight_chunk_delete(pg):
    from sqlalchemy import text

    store, index = PgStore(pg["engine"]), PgIndex(pg["engine"], HashEmbedder(768))
    admin, project = _org(store, "kt")
    meeting = store.create_meeting(project.id, "KT", admin.id)
    cid = f"chunk-{uuid.uuid4().hex}"
    index.upsert([Chunk(id=cid, project_id=project.id, source_type="code", symbol="f", ref={"path": "a.py"}, content="def f(): pass")])
    link = KTLink(meeting_id=meeting.id, file_path="a.py", symbol="f", chunk_id=cid, start_s=1, end_s=2, confidence=0.9, method="symbol")
    with pg["engine"].connect() as c:
        c.execute(text("SELECT set_config('app.org_id', :o, true)"), {"o": admin.org_id})
        c.execute(text("DELETE FROM chunks WHERE id = :c"), {"c": cid})  # a re-index, not yet committed
        errors = []

        def add():
            current_org.set(admin.org_id)
            try:
                store.add_kt_links(project.id, [link])
            except Exception as e:  # noqa: BLE001
                errors.append(e)

        t = threading.Thread(target=add)
        t.start()
        time.sleep(0.5)
        c.commit()
    t.join(10)
    assert not errors
    [saved] = store.kt_links(project.id, path="a.py")
    assert saved.chunk_id is None and saved.symbol == "f"


# ---------- contract additions ----------
def test_ingest_lock_serialises_one_repository(store):
    """The in-process demo waits for the lock. Postgres refuses at once: a worker waiting on it would hold one of its few
    Pub/Sub slots for as long as the other ingestion runs (review r3 data#1)."""
    order, busy = [], []

    def ingest(name):
        try:
            with store.ingest_lock("repo-1"):
                order.append(f"{name}+")
                time.sleep(0.2)
                order.append(f"{name}-")
        except RepoBusy:
            busy.append(name)

    a = threading.Thread(target=ingest, args=("a",))
    a.start()
    time.sleep(0.05)
    with store.ingest_lock("repo-2"):  # another repository is not held up
        assert order == ["a+"]
    ingest("b")
    a.join()
    if isinstance(store, MemoryStore):
        assert order == ["a+", "a-", "b+", "b-"] and busy == []
    else:
        assert order == ["a+", "a-"] and busy == ["b"]
        ingest("c")  # free again once the first one is done
        assert order[-2:] == ["c+", "c-"]


def test_event_status_and_reset(store):
    key = f"evt-{uuid.uuid4().hex}"
    assert store.event_status(key) == "none" and store.reset_event(key) is True
    store.claim_event(key, 300)
    assert store.event_status(key) == "busy" and store.reset_event(key) is False
    store.finish_event(key)
    assert store.event_status(key) == "done"
    assert store.reset_event(key) is True and store.claim_event(key, 300) == "new"  # the same key really runs again
    stale = f"evt-{uuid.uuid4().hex}"
    store.claim_event(stale, 0)  # its worker died: the lease has lapsed
    time.sleep(0.01)
    assert store.event_status(stale) == "none" and store.reset_event(stale) is True


def test_the_first_claimed_decision_is_kept(store):
    _, project = _org(store, "dec")
    a = store.create_approval(project.id, "task_plan", "thread-1", {"plans": []})
    assert store.claim_approval(a.id, {"approve": ["x"]}) is True
    store.release_approval(a.id)
    assert store.claim_approval(a.id, {"approve": []}) is True
    assert store.get_approval(a.id).decision == {"approve": ["x"]}
    assert store.claim_approval(a.id) is False  # still claimed


def test_a_newer_review_supersedes_older_pending_ones(store):
    _, project = _org(store, "sup")
    old = store.create_approval(project.id, "review", "t-1", {"task_id": "task-a", "review_id": "r1"})
    other = store.create_approval(project.id, "review", "t-2", {"task_id": "task-b", "review_id": "r2"})
    new = store.create_approval(project.id, "review", "t-3", {"task_id": "task-a", "review_id": "r3"})
    assert [a.id for a in store.supersede_review_approvals("task-a")] == [old.id]
    assert store.get_approval(old.id).status == "rejected" and store.get_approval(old.id).decided_by is None
    assert store.get_approval(new.id).status == "pending" and store.get_approval(other.id).status == "pending"


def test_the_review_of_the_newest_pr_state_wins_whichever_finished_last(store):
    """Review r3 runtime#2: the newer commit's review saved first, the older one's second."""
    _, project = _org(store, "order")
    newer = store.create_approval(project.id, "review", "t-s2", {"task_id": "task-o", "pr_updated_at": "2026-10-16T10:05:00Z"})
    older = store.create_approval(project.id, "review", "t-s1", {"task_id": "task-o", "pr_updated_at": "2026-10-16T10:00:00Z"})
    assert [a.id for a in store.supersede_review_approvals("task-o")] == [older.id]
    assert store.get_approval(newer.id).status == "pending"
    store.decide_approval(newer.id, "approved", None)
    late = store.create_approval(project.id, "review", "t-s1", {"task_id": "task-o", "pr_updated_at": "2026-10-16T10:00:00Z"})
    assert [a.id for a in store.supersede_review_approvals("task-o")] == [late.id]  # decided already: still the newest


def test_outbox(store):
    tag = uuid.uuid4().hex
    oid = store.outbox_add("ingestion", {"type": "repo.pushed", "dedupe": f"push:{tag}"})
    assert store.outbox_add("ingestion", {"type": "repo.pushed", "dedupe": f"push:{tag}"}) == oid
    other = store.outbox_add("ingestion", {"type": "document.uploaded"})
    assert oid not in {r["id"] for r in store.outbox_due()}  # the request's own publish gets a head start
    if isinstance(store, MemoryStore):
        for r in store.outbox.values():
            r["next_attempt_at"] = r["next_attempt_at"].replace(year=2000)
    else:
        store.run("UPDATE outbox SET next_attempt_at = now() - interval '1 second' WHERE id IN (CAST(:a AS uuid), CAST(:b AS uuid))", a=oid, b=other)
    store.outbox_sent(other)
    due = store.outbox_due()
    assert [(r["id"], r["topic"], r["event"]["dedupe"]) for r in due] == [(oid, "ingestion", f"push:{tag}")]
    assert store.outbox_due() == []  # claimed: a concurrent flusher does not send it again
    store.outbox_failed(oid, "pubsub down")
    # a key that was sent already is a deliberate re-send (a reset event): it is due again, so a failed re-send is retried
    store.outbox_sent(oid)
    assert store.outbox_add("ingestion", {"type": "repo.pushed", "dedupe": f"push:{tag}", "again": True}) == oid
    if isinstance(store, MemoryStore):
        store.outbox[oid]["next_attempt_at"] = store.outbox[oid]["next_attempt_at"].replace(year=2000)
    else:
        store.run("UPDATE outbox SET next_attempt_at = now() - interval '1 second' WHERE id = CAST(:a AS uuid)", a=oid)
    assert [r["event"].get("again") for r in store.outbox_due() if r["id"] == oid] == [True]


def test_leaving_an_organisation(store):
    admin, project = _org(store, "leave")
    hire = _join(store, admin, project, "hire")
    assert store.leave_organization(admin.id) is False  # the last admin cannot leave people behind without one
    second = _join(store, admin, project, "second", role="admin")
    assert store.leave_organization(second.id) is True
    assert not store.is_member(project.id, second.id)
    current_org.set(None)
    assert store.user_by_firebase(f"uid-{second.email}") is None
    other_admin, other_project = _org(store, "next")  # the person can now join another organisation
    store.create_invitation(other_admin.org_id, other_project.id, second.email, "manager", None, None, other_admin.id)
    current_org.set(None)
    assert store.accept_invitations(f"uid-{second.email}", second.email, "S", other_admin.org_id).org_id == other_admin.org_id
    current_org.set(admin.org_id)
    assert store.leave_organization(admin.id) is False
    assert store.leave_organization(hire.id) is True


def test_the_only_member_can_leave_an_organisation_made_by_mistake(store):
    """Review r3 frontend#2: nobody could be made admin, so a sole admin was stuck in an organisation created by mistake."""
    admin, _ = _org(store, "solo")
    assert store.leave_organization(admin.id) is True
    current_org.set(None)
    assert store.user_by_firebase(f"uid-{admin.email.split('@')[0]}") is None  # free to accept the invitation it meant to


def test_someone_who_left_comes_back_through_a_new_invitation_with_its_role(store):
    """Review r3 api#1: re-inviting someone who left silently added their unbound row; they could never sign in again."""
    admin, project = _org(store, "back")
    ex = _join(store, admin, project, "ex", role="admin", github_login="ex-gh")
    assert store.leave_organization(ex.id) is True
    assert store.user_by_email(admin.org_id, ex.email) is None  # not a member: the invite route creates an invitation
    assert store.user_by_email(admin.org_id, admin.email).id == admin.id
    store.create_invitation(admin.org_id, project.id, ex.email, "new_hire", None, None, admin.id)
    current_org.set(None)
    back = store.accept_invitations(f"uid-{ex.email}", ex.email, "Ex", admin.org_id)
    assert back.id == ex.id and back.role == "new_hire" and back.github_login == "ex-gh"  # not admin again
    current_org.set(admin.org_id)
    assert store.is_member(project.id, ex.id) and store.user_by_email(admin.org_id, ex.email).id == ex.id


def test_delete_document(store):
    _, project = _org(store, "doc")
    did = store.create_document(project.id, "Guide", "gs://b/g.md", "text/markdown", None)
    store.delete_document(did)
    assert store.documents(project.id) == []


def test_local_repo_head_sha_is_stable():
    src = LocalRepoSource(str(SAMPLE))
    sha = src.head_sha("demo/x", "main", None)
    assert len(sha) == 40 and sha == src.head_sha("demo/x", "main", None)
