"""Release round, reliability: the self-hosted queue survives errors and gives up on events that kill the process, the
repository watcher survives one unreadable repository, indexing stops plainly at its limits and keeps the old index until
the new one is in place, a question's embedding fails fast instead of waiting behind indexing, a recording's claim is undone
when its event cannot be recorded, and a long recording keeps its finished segments across retries.

The Postgres half (session timeouts, the migrator's lock and checksums, the event-key sweep) runs with TEST_PG_ADMIN_URL,
like tests/test_postgres.py."""
from __future__ import annotations

import threading
import time
import uuid
from datetime import datetime, timedelta, timezone

import httpx
import psycopg
import pytest
from sqlalchemy import text

from app.adapters.memory import MemoryStore
from app.agents.specialists.librarian import agent as ingestion
from app.agents.specialists.note_taker import agent as meeting
from app.agents.supervisor.graph import run_event
from app.api.outbox import publish
from app.config import Settings
from app.container import build_container
from app.ports import Project, Repository, TooLarge
from app.selfhost import BUSY_RETRY_S, MAX_ATTEMPTS, UNREADABLE_ROUNDS, LocalQueue, RepoWatcher
from app.services import embeddings as E
from app.services import scheduling
from tests.test_postgres import ADMIN, OWNER_URL, db, engine, store  # noqa: F401 - Postgres fixtures

pg = pytest.mark.skipif(not ADMIN, reason="set TEST_PG_ADMIN_URL to run the Postgres integration tests")


# ---------------------------------------------------------------- the local queue
@pytest.fixture
def demo():
    return build_container(Settings(mode="demo", agent_retries=1, retry_sleep_s=0))


def _queued(container, workers=1):
    q = LocalQueue(workers=workers)
    container.deps.bus = q
    q.container = container
    return q


def _due(container, row_id):
    container.store.outbox[row_id]["next_attempt_at"] = datetime.now(timezone.utc) - timedelta(seconds=1)


def test_a_database_error_after_a_job_never_kills_its_worker(demo):
    """REL-03: outbox_sent raising (Postgres restarting as a job finishes) leaves the row due; the worker carries on."""
    q = _queued(demo)
    handled = []
    demo.handle = lambda ev: handled.append(ev["dedupe"]) or {"result": "ok"}
    real, failures = demo.store.outbox_sent, []

    def flaky(row_id):
        if not failures:
            failures.append(row_id)
            raise RuntimeError("server closed the connection unexpectedly")
        return real(row_id)

    demo.store.outbox_sent = flaky
    publish(demo, "reviews", {"type": "noop", "project_id": "proj-demo", "dedupe": "rel3:a"})
    publish(demo, "reviews", {"type": "noop", "project_id": "proj-demo", "dedupe": "rel3:b"})
    worker = threading.Thread(target=q._work, daemon=True)
    worker.start()
    deadline = time.monotonic() + 5
    while len(handled) < 2 and time.monotonic() < deadline:
        time.sleep(0.02)
    time.sleep(0.05)
    try:
        assert handled == ["rel3:a", "rel3:b"] and worker.is_alive()
        first = demo.store.outbox[failures[0]]
        assert first["sent_at"] is None  # stays due: handled again, its dedupe key answers "duplicate"
        assert not q.inflight
    finally:
        q.stopping.set()
        worker.join(2)


def test_an_exception_escaping_run_is_logged_and_the_worker_lives(demo):
    q = _queued(demo)
    q.run = lambda row_id, event: (_ for _ in ()).throw(RuntimeError("boom"))
    q.jobs.put(("row-x", {"type": "noop"}))
    q.inflight.add("row-x")
    worker = threading.Thread(target=q._work, daemon=True)
    worker.start()
    deadline = time.monotonic() + 3
    while "row-x" in q.inflight and time.monotonic() < deadline:
        time.sleep(0.02)
    try:
        assert worker.is_alive() and "row-x" not in q.inflight
    finally:
        q.stopping.set()
        worker.join(2)


def test_an_event_that_ended_the_process_too_often_is_given_up_and_its_repository_marked_failed(demo):
    """REL-04: an out-of-memory index never reaches the handler's except; the pump gives the row up on its next claim."""
    q = _queued(demo)
    repo = demo.store.repos_for("proj-demo")[0]
    demo.store.update_repo(repo.id, status="indexing")
    publish(demo, "ingestion", {"type": "repo.connected", "project_id": "proj-demo", "repository_id": repo.id, "ref": "main",
                                "dedupe": "rel4:connect"})
    row_id, _ = q.jobs.get_nowait()
    q.inflight.clear()  # the process that held it is gone
    demo.store.outbox[row_id]["attempts"] = MAX_ATTEMPTS  # claimed that many times, never finished
    _due(demo, row_id)
    before = len(demo.store.notifications)
    q.pump_once()
    row = demo.store.outbox[row_id]
    assert row["sent_at"] is not None and row["last_error"].startswith("gave up: the process ended")
    assert q.jobs.empty()  # not handed out again
    assert demo.store.get_repo(repo.id).status == "failed"
    told = demo.store.notifications[before:]
    assert told and all(n["kind"] == "incident" for n in told) and repo.full_name in told[0]["text"]


def test_a_row_under_the_limit_is_handed_out_as_usual(demo):
    q = _queued(demo)
    publish(demo, "reviews", {"type": "noop", "project_id": "proj-demo", "dedupe": "rel4:ok"})
    row_id, _ = q.jobs.get_nowait()
    q.inflight.clear()
    demo.store.outbox[row_id]["attempts"] = MAX_ATTEMPTS - 1
    _due(demo, row_id)
    q.pump_once()
    assert q.jobs.get_nowait()[0] == row_id and demo.store.outbox[row_id]["sent_at"] is None


def test_a_busy_event_waits_in_memory_without_counting_attempts(demo, monkeypatch):
    """REL-17: RepoBusy/EventBusy is retried BUSY_RETRY_S later from memory: no exponential backoff, no attempt used."""
    from app.ports import RepoBusy

    q = _queued(demo)
    calls = []

    def handle(ev):
        calls.append(ev["dedupe"])
        if len(calls) == 1:
            raise RepoBusy("r")
        return {"result": "ok"}

    demo.handle = handle
    publish(demo, "ingestion", {"type": "noop", "project_id": "proj-demo", "dedupe": "rel17"})
    row_id, event = q.jobs.get_nowait()
    q.inflight.discard(row_id)
    demo.store.outbox[row_id]["attempts"] = 1
    q.run(row_id, event)
    assert row_id in q.waiting and demo.store.outbox[row_id]["sent_at"] is None
    _due(demo, row_id)
    q.pump_once()  # still waiting: the pump neither claims it nor hands it out
    assert q.jobs.empty() and demo.store.outbox[row_id]["attempts"] == 1
    clock = time.monotonic() + BUSY_RETRY_S + 1
    monkeypatch.setattr("app.selfhost.time.monotonic", lambda: clock)
    q.pump_once()
    got, ev = q.jobs.get_nowait()
    assert got == row_id and row_id not in q.waiting
    q.run(got, ev)
    assert calls == ["rel17", "rel17"] and demo.store.outbox[row_id]["sent_at"] is not None


# ---------------------------------------------------------------- the repository watcher
class Gone(httpx.HTTPStatusError):
    def __init__(self, status=404):
        req = httpx.Request("GET", "https://api.github.com/repos/o/gone/pulls")
        super().__init__("not found", request=req, response=httpx.Response(status, request=req))


class TwoRepos:
    def __init__(self):
        self.polled: list[str] = []
        self.limited = False

    def branch_head(self, full, branch):
        return "s1"

    def compare(self, full, base, head):
        return []

    def pulls(self, full, state):
        from app.adapters.github_url import GitHubLimited

        self.polled.append(full)
        if self.limited:
            raise GitHubLimited(None, False)
        if full == "o/gone":
            raise Gone()
        return []


def _watcher(mem):
    c = type("C", (), {})()
    c.store, c.settings = mem, Settings(mode="demo")
    c.deps = type("D", (), {"bus": None})()
    return RepoWatcher(c, TwoRepos(), 60)


@pytest.fixture
def two():
    mem = MemoryStore()
    mem.add_project(Project(id="p1", org_id="o1", name="P", description=""))
    from app.ports import User

    mem.add_user(User(id="u-m", org_id="o1", email="m@x.dev", name="M", role="manager"), "p1")
    mem.add_repo(Repository(id="r0", project_id="p1", full_name="o/gone", status="ready", last_indexed_sha="s1"))
    mem.add_repo(Repository(id="r1", project_id="p1", full_name="o/live", status="ready", last_indexed_sha="s1"))
    return mem


def test_one_unreadable_repository_does_not_stop_the_others(two):
    """REL-06: a deleted or private repository is skipped; every other repository is still polled."""
    w = _watcher(two)
    w.poll()
    assert w.github.polled.count("o/live") >= 1 and w.unreadable == {"r0": 1}
    for _ in range(UNREADABLE_ROUNDS - 1):
        w.poll()
    assert two.get_repo("r0").status == "failed" and two.get_repo("r1").status == "ready"
    told = [n for n in two.notifications if n["kind"] == "incident"]
    assert len(told) == 1 and "o/gone" in told[0]["text"] and "404" in told[0]["text"]
    w.poll()
    assert len([n for n in two.notifications if n["kind"] == "incident"]) == 1  # told once


def test_githubs_limit_still_stops_the_round(two):
    from app.adapters.github_url import GitHubLimited

    w = _watcher(two)
    w.github.limited = True
    with pytest.raises(GitHubLimited):
        w.poll()


# ---------------------------------------------------------------- indexing limits and the re-index
def _settings(world, **limits):
    world.settings = world.settings.model_copy(update=limits)
    return world


def test_a_repository_over_a_limit_stops_before_anything_changes(world):
    """REL-05: the limit is checked before any write or embedding; the previous index stays and the message names the
    limit and its setting."""
    ingestion.ingest_repo(world, world.store.get_repo("r1"), "main")
    before = {c.id for c in world.index.code_chunks("p1", content=False)}
    world.store.update_repo("r1", last_indexed_sha="older")
    _settings(world, index_max_files=2)
    embedded = []
    real = world.index.upsert
    world.index.upsert = lambda chunks: embedded.append(len(chunks)) or real(chunks)
    with pytest.raises(TooLarge) as e:
        ingestion.ingest_repo(world, world.store.get_repo("r1"), "main")
    assert "INDEX_MAX_FILES" in str(e.value) and "previous index stays" in str(e.value)
    assert embedded == [] and {c.id for c in world.index.code_chunks("p1", content=False)} == before
    assert world.store.get_repo("r1").status == "failed"


@pytest.mark.parametrize("limits, setting", [({"index_max_mb": 0.00001}, "INDEX_MAX_MB"), ({"index_max_chunks": 3}, "INDEX_MAX_CHUNKS")])
def test_every_limit_says_which_one(world, limits, setting):
    _settings(world, **limits)
    with pytest.raises(TooLarge) as e:
        ingestion.ingest_repo(world, world.store.get_repo("r1"), "main")
    assert setting in str(e.value)


def test_the_supervisor_tells_the_managers_the_limit_and_does_not_retry(world, graph, monkeypatch):
    world.settings = world.settings.model_copy(update={"index_max_files": 1, "agent_retries": 3})
    reads = []
    real = ingestion._plan
    monkeypatch.setattr(ingestion, "_plan", lambda *a: reads.append(1) or real(*a))
    from langgraph.checkpoint.memory import MemorySaver

    from app.agents.supervisor.graph import build_supervisor

    out = run_event(build_supervisor(world, MemorySaver()), {"type": "repo.connected", "project_id": "p1", "repository_id": "r1",
                                                             "ref": "main"}, "repo:limit")
    assert out["result"]["escalated"] and "INDEX_MAX_FILES" in out["result"]["error"]
    assert reads == [1]  # a limit is final: not read again agent_retries times
    [n] = [n for n in world.store.notifications if n["kind"] == "incident"]
    assert "INDEX_MAX_FILES" in n["text"]


def test_large_data_files_are_not_indexed(world):
    big = "{" + ",".join(f'"k{i}": {i}' for i in range(20_000)) + "}"
    files = {"engine/a.py": "def a():\n    return 1\n", "fixtures/dump.json": big, "conf/small.yaml": "a: 1\n"}
    world.repos.files = lambda *a: files
    out = ingestion.ingest_repo(world, world.store.get_repo("r1"), "main")
    assert out["files"] == 3
    assert set(world.index.all_paths("p1")) == {"engine/a.py", "conf/small.yaml"}


class FailingIndex:
    """Wraps the memory index; the nth upsert raises (an embedding quota running out part-way)."""

    def __init__(self, inner, fail_at):
        self.inner, self.fail_at, self.upserts, self.deleted = inner, fail_at, 0, []

    def upsert(self, chunks):
        self.upserts += 1
        if self.upserts == self.fail_at:
            raise E.DailyQuotaExceeded("quota")
        return self.inner.upsert(chunks)

    def delete_paths(self, project_id, paths):
        self.deleted.append(sorted(paths))
        return self.inner.delete_paths(project_id, paths)

    def __getattr__(self, name):
        return getattr(self.inner, name)


def test_a_reindex_that_fails_part_way_keeps_the_old_index(world, monkeypatch):
    """REL-09: nothing is deleted up front. Unchanged files are written over in place; files gone are removed last."""
    monkeypatch.setattr(ingestion, "BATCH", 5)
    ingestion.ingest_repo(world, world.store.get_repo("r1"), "main")
    old = {c.id: c.ref["path"] for c in world.index.code_chunks("p1", content=False)}
    paths = sorted(set(old.values()))
    world.store.update_repo("r1", last_indexed_sha="older")
    files = world.repos.files("demo/mini-policy-engine", "main", None)
    files.pop(paths[-1])  # one file deleted upstream
    world.repos.files = lambda *a: files
    failing = FailingIndex(world.index, fail_at=2)
    world.index = failing
    with pytest.raises(E.DailyQuotaExceeded):
        ingestion.ingest_repo(world, world.store.get_repo("r1"), "main")
    now = {c.id for c in failing.inner.code_chunks("p1", content=False)}
    assert set(old) <= now  # every old chunk is still there, the deleted file's included (it goes last)
    assert failing.deleted == []
    assert world.store.get_repo("r1").status == "failed"
    world.index = failing.inner
    out = ingestion.ingest_repo(world, world.store.get_repo("r1"), "main")
    assert out["chunks"] > 0 and paths[-1] not in world.index.all_paths("p1")
    assert world.store.get_repo("r1").status == "ready"


def test_a_file_whose_chunks_moved_loses_only_its_old_chunks(world):
    ingestion.ingest_repo(world, world.store.get_repo("r1"), "main")
    files = world.repos.files("demo/mini-policy-engine", "main", None)
    target = "engine/validate.py"
    old_ids = {c.id for c in world.index.file_chunks("p1", target)}
    files[target] = "# a new first line moves every span\n" + files[target]
    world.repos.files = lambda *a: files
    world.store.update_repo("r1", last_indexed_sha="older")
    index = FailingIndex(world.index, fail_at=-1)
    world.index = index
    ingestion.ingest_repo(world, world.store.get_repo("r1"), "main")
    new_ids = {c.id for c in index.inner.file_chunks("p1", target)}
    assert new_ids and not (old_ids & new_ids)  # nothing stale left behind
    assert index.deleted == [[target], []]  # only the moved file, just before its batch; nothing gone at the end


# ---------------------------------------------------------------- a question's embedding
def _embedder(responses, rpm=0):
    emb = object.__new__(E.VertexEmbedder)
    emb.model, emb.dim, emb.batch, emb.workers = "m", 3, 64, 1
    emb.pace = E._Pace(rpm, reserve=max(1, rpm // 10)) if rpm else None
    emb.query_deadline_s = 8.0

    def embed_content(**kw):
        r = responses.pop(0) if responses else "ok"
        if isinstance(r, Exception):
            raise r
        return type("R", (), {"embeddings": [type("V", (), {"values": [1.0, 0.0, 0.0]})() for _ in kw["contents"]]})()

    emb.client = type("C", (), {"models": type("M", (), {"embed_content": staticmethod(embed_content)})()})()
    return emb


class Quota(Exception):
    code = 429


def test_indexing_never_takes_the_questions_share_of_the_minute():
    pace = E._Pace(10, reserve=2)
    for _ in range(8):
        pace.wait()
    start = time.monotonic()
    pace.wait(query=True, deadline=start + 1)  # a question still finds a slot at once
    pace.wait(query=True, deadline=start + 1)
    assert time.monotonic() - start < 0.5
    with pytest.raises(E.SearchBusy):
        pace.wait(query=True, deadline=time.monotonic() + 1)  # all ten taken: not a minute's wait, SearchBusy


def test_a_question_waits_seconds_not_minutes_for_googles_limit(monkeypatch):
    """REL-10: one short retry inside the deadline, then SearchBusy (indexing would wait up to 90 s, twelve times)."""
    slept = []
    monkeypatch.setattr(E.time, "sleep", slept.append)
    emb = _embedder([Quota("429 RESOURCE_EXHAUSTED retry in 50s"), Quota("429 RESOURCE_EXHAUSTED retry in 50s")])
    with pytest.raises(E.SearchBusy) as e:
        emb.embed(["where is validation"], "query")
    assert slept == [E.QUERY_RETRY_WAIT_S] and "Ask again" in str(e.value)
    emb = _embedder([Quota("429 RESOURCE_EXHAUSTED retry in 50s")])
    assert emb.embed(["where is validation"], "query") == [[1.0, 0.0, 0.0]]


def test_a_documents_429_is_still_waited_out():
    emb = _embedder([])
    assert emb.embed(["a", "b"], "document") == [[1.0, 0.0, 0.0], [1.0, 0.0, 0.0]]


# ---------------------------------------------------------------- recordings
def test_a_claim_whose_event_cannot_be_recorded_is_undone(demo):
    """REL-13: the session goes back to where it was, its copy is removed, and the recording can be added again."""
    st = demo.store
    m = st.create_meeting("proj-demo", "KT: claims", "u-priya")
    uri = "gs://local/recordings/rel13.mp4"
    demo.deps.blobs.put(uri, b"video", "video/mp4")
    real = st.outbox_add

    def down(topic, event):
        raise RuntimeError("connection pool timed out")

    st.outbox_add = down
    with pytest.raises(RuntimeError):
        scheduling.start_notes(demo, m, uri, 60.0)
    after = st.get_meeting(m.id)
    assert after.status == m.status and after.recording_uri == m.recording_uri
    with pytest.raises(Exception):
        demo.deps.blobs.get(uri)
    st.outbox_add = real
    assert scheduling.start_notes(demo, st.get_meeting(m.id), uri, 60.0) is True
    again = st.get_meeting(m.id)  # the demo bus runs the notes at once: processing, then ready
    assert again.recording_uri == uri and again.status in {"processing", "ready"}


def test_a_new_recording_for_a_failed_session_removes_the_old_file(demo):
    """PRIV-3: re-adding a recording after failed notes leaves no file that no row names."""
    st = demo.store
    m = st.create_meeting("proj-demo", "KT: replaced", "u-priya")
    old, new = "gs://local/recordings/priv3-old.mp4", "gs://local/recordings/priv3-new.mp4"
    for u in (old, new):
        demo.deps.blobs.put(u, b"video", "video/mp4")
    st.update_meeting(m.id, status="failed", recording_uri=old)
    assert scheduling.start_notes(demo, st.get_meeting(m.id), new, 60.0) is True
    assert st.get_meeting(m.id).recording_uri == new and demo.deps.blobs.get(new) == b"video"
    with pytest.raises(Exception):
        demo.deps.blobs.get(old)


def test_a_failed_note_taker_marks_the_session_failed_and_a_retry_reuses_finished_segments(world, graph):
    """REL-15: the Supervisor's retry starts at the clip that failed, not at the first; the session is never left
    'processing' (it says 'failed', and the recording can be added again)."""
    ingestion.ingest_repo(world, world.store.get_repo("r1"), "main")
    seen: list[float] = []
    fail = {"at": 1200.0, "left": 1}

    def segment(prompt, media):
        start = media[0].start_s
        seen.append(start)
        if start == fail["at"] and fail["left"]:
            fail["left"] -= 1
            raise RuntimeError("the model timed out")
        return {"utterances": [{"start_s": 1, "end_s": 2, "speaker": "Ravi", "text": f"clip {start}"}]}

    world.llm.responders["SegmentAnalysis"] = segment
    world.llm.responders["MeetingSummary"] = lambda p, m: {"summary": "s", "decisions": []}
    world.llm.responders["TaskPlan"] = lambda p, m: {"tasks": [], "first_pr": None}
    ev = {"type": "meeting.recording_ready", "project_id": "p1", "meeting_id": "m1", "recording_uri": "gs://b/rel15.mp4",
          "duration_s": 1800}
    out = run_event(graph, ev, "meeting:rel15")  # agent_retries=1 in this world: the failure escalates
    assert out["result"]["escalated"] and world.store.get_meeting("m1").status == "failed"
    assert seen == [0, 600, 1200]
    seen.clear()
    run_event(graph, ev, "meeting:rel15b")
    assert seen == [1200]  # 0 and 600 were kept
    assert world.store.get_meeting("m1").status == "ready"
    assert ("m1", "gs://b/rel15.mp4") not in meeting._cache(world.store)  # forgotten once the notes are saved


# ---------------------------------------------------------------- Postgres
def test_session_options_follow_the_settings():
    from app.adapters.postgres import session_options

    opts = session_options(Settings(mode="demo"))
    assert opts["connect_timeout"] == 10
    assert opts["options"] == "-c statement_timeout=30000 -c lock_timeout=5000 -c idle_in_transaction_session_timeout=60000"
    off = session_options(Settings(mode="demo", db_statement_timeout_ms=0, db_lock_timeout_ms=0, db_idle_in_transaction_timeout_ms=0,
                                   db_connect_timeout_s=0))
    assert off == {}


@pg
def test_app_sessions_have_timeouts_and_errors_carry_no_values(engine):  # noqa: F811
    """REL-12, OPS-09: statement, lock and idle-in-transaction timeouts on every pooled connection; an error names neither
    the bound parameters nor PostgreSQL's DETAIL line (which quotes the row)."""
    with engine.begin() as c:
        assert c.execute(text("SHOW statement_timeout")).scalar() == "30s"
        assert c.execute(text("SHOW lock_timeout")).scalar() == "5s"
        assert c.execute(text("SHOW idle_in_transaction_session_timeout")).scalar() == "1min"
    assert engine.hide_parameters
    value = f"new.hire+{uuid.uuid4().hex[:6]}@example.com"
    with pytest.raises(Exception) as e:
        with engine.begin() as c:  # a unique key's DETAIL line quotes the value: "Key (dedupe_key)=(...) already exists."
            c.execute(text("INSERT INTO processed_events (dedupe_key) VALUES (:v)"), {"v": value})
            c.execute(text("INSERT INTO processed_events (dedupe_key) VALUES (:v)"), {"v": value})
    assert value not in str(e.value) and value not in repr(e.value)
    assert "duplicate key" in str(e.value) and value in e.value.orig.diag.message_detail  # the detail stays readable in code


@pg
def test_one_migrator_at_a_time_and_an_edited_migration_stops_the_run(db):  # noqa: F811
    from app import migrate

    url = OWNER_URL["url"]
    with psycopg.connect(url, autocommit=True) as c:
        rows = dict(c.execute("SELECT version, sha256 FROM schema_migrations").fetchall())
        assert all(rows.values()) and rows["001_init"] == migrate.checksum(migrate.MIGRATIONS / "001_init.sql")
        assert not c.execute("SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND objid = %s",
                             (migrate.RUN_LOCK & 0xFFFFFFFF,)).fetchone()[0]  # released after each run
        c.execute("UPDATE schema_migrations SET sha256 = 'edited' WHERE version = '001_init'")
        try:
            with psycopg.connect(url) as conn, pytest.raises(SystemExit) as stop:
                migrate.apply_migrations(conn)
            assert "001_init.sql" in str(stop.value)
        finally:
            c.execute("UPDATE schema_migrations SET sha256 = %s WHERE version = '001_init'",
                      (migrate.checksum(migrate.MIGRATIONS / "001_init.sql"),))
        with psycopg.connect(url, autocommit=True) as holder:  # another migrator holds the lock: this one waits for it
            holder.execute("SELECT pg_advisory_lock(%s)", (migrate.RUN_LOCK,))
            done = []
            t = threading.Thread(target=lambda: done.append(migrate.run(url, None)), daemon=True)
            t.start()
            time.sleep(0.5)
            assert not done
            holder.execute("SELECT pg_advisory_unlock(%s)", (migrate.RUN_LOCK,))
            t.join(30)
            assert done == [[]]


@pg
def test_old_event_keys_are_forgotten_but_pull_request_keys_stay(store):  # noqa: F811
    """REL-19: done keys older than EVENTS_KEPT_DAYS go (hourly, from the outbox read); pr:<n>:<sha> keys stay."""
    from app.adapters.postgres import PgStore

    tag = uuid.uuid4().hex[:6]
    keys = {f"old:{tag}": 120, f"recent:{tag}": 5, f"pr:1:{tag}": 400}
    with psycopg.connect(OWNER_URL["url"], autocommit=True) as c:
        for key, days in keys.items():
            c.execute("INSERT INTO processed_events (dedupe_key, status, claimed_at) VALUES (%s, 'done', now() - make_interval(days => %s))",
                      (key, days))
    PgStore._events_swept = None
    store.outbox_due(1)
    left = {r["dedupe_key"] for r in store.rows("SELECT dedupe_key FROM processed_events WHERE dedupe_key LIKE :t", t=f"%{tag}")}
    assert left == {f"recent:{tag}", f"pr:1:{tag}"}


def test_an_older_build_never_deploys_after_a_newer_one():
    """REL-14: the build's start is stamped, ww-api is labelled with it, and every step that changes something stops when
    a newer build is already live."""
    from pathlib import Path

    import yaml

    build = yaml.safe_load((Path(__file__).resolve().parents[2] / "cloudbuild.yaml").read_text())
    steps = {s["id"]: s for s in build["steps"]}
    assert "/workspace/ww_build_started" in steps["stamp"]["args"][-1]
    assert "stamp" in steps["migrate"]["waitFor"]
    assert "--update-labels ww_build=$$(cat /workspace/ww_build_started)" in steps["deploy-api"]["args"][-1]
    for sid in ("migrate", "deploy-api", "deploy-web", "deploy-workers"):
        script = steps[sid]["args"][-1]
        guard = script.index("metadata.labels.ww_build")
        change = min(i for i in (script.find("gcloud run jobs deploy"), script.find("gcloud run deploy"),
                                 script.find("kubectl apply")) if i >= 0)
        assert guard < change and '-gt "$$(cat /workspace/ww_build_started)" ]; then' in script
