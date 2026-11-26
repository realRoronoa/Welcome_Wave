"""Self-hosted mode: settings, repositories by URL, the local queue, the repository watcher, signed file links, live updates."""
from __future__ import annotations

import asyncio
import json
import threading
import time
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from app.adapters.github_url import GitHubByUrl, GitHubLimited, RepoRouter, parse_repo
from app.adapters.memory import MemoryStore, ServedLocalStorage, signing_key
from app.api.outbox import publish
from app.config import Settings, get_settings
from app.container import build_container
from app.live import LiveHub, LiveStore, topic_of
from app.ports import AI_OFF, AIUnavailable, Project, Repository
from app.selfhost import MAX_ATTEMPTS, LocalQueue, RepoWatcher


# ---------------------------------------------------------------- settings
def test_self_hosted_needs_firebase_and_never_the_emulator_in_prod():
    bare = Settings(mode="self_hosted", firebase_project_id="", gcp_project="", firebase_api_key="")
    assert any("FIREBASE_PROJECT_ID" in p for p in bare.production_problems())
    assert any("FIREBASE_API_KEY" in p for p in bare.production_problems())
    ok = Settings(mode="self_hosted", firebase_project_id="acme-ww", firebase_api_key="AIza-x")
    assert ok.production_problems() == []  # no webhook secret, scheduler or Google Cloud settings needed on one machine
    emu = Settings(mode="self_hosted", firebase_project_id="demo-x", firebase_auth_emulator_host="127.0.0.1:9099")
    assert emu.production_problems() == []
    assert any("EMULATOR" in p for p in emu.model_copy(update={"env": "prod"}).production_problems())
    assert any("MODE must be" in p for p in Settings(mode="cloud").production_problems())


def test_ai_is_ready_only_with_a_model():
    # _env_file=None: a developer's backend/.env may hold a real GEMINI_API_KEY
    assert not Settings(mode="self_hosted", _env_file=None).ai_ready
    assert Settings(mode="self_hosted", _env_file=None, gemini_api_key="k").ai_ready
    assert Settings(mode="demo").ai_ready and Settings(mode="gcp").ai_ready


# ---------------------------------------------------------------- repositories by URL
@pytest.mark.parametrize("given, want", [
    ("https://github.com/psf/requests", "psf/requests"),
    ("github.com/psf/requests.git", "psf/requests"),
    ("https://github.com/psf/requests/tree/main/src", "psf/requests"),
    ("http://www.github.com/a-b/c.d", "a-b/c.d"),
    ("psf/requests", "psf/requests"),
    ("  psf/requests  ", "psf/requests"),
    ("https://gitlab.com/a/b", None),
    ("../etc", None),
    ("a/..", None),
    ("", None),
])
def test_parse_repo(given, want):
    assert parse_repo(given) == want


def _github(routes: dict, seen: list | None = None):
    """A GitHub stand-in: routes map 'METHOD path' to a function(request) -> httpx.Response."""
    def handle(request: httpx.Request):
        if seen is not None:
            seen.append(request)
        fn = routes.get(f"{request.method} {request.url.path}")
        return fn(request) if fn else httpx.Response(404, json={"message": "Not Found"})
    return GitHubByUrl("", client=httpx.Client(transport=httpx.MockTransport(handle)))


def test_repo_info_and_missing_repository():
    gh = _github({"GET /repos/psf/requests": lambda r: httpx.Response(200, json={"full_name": "psf/requests", "default_branch": "main", "private": False})})
    assert gh.repo_info("psf/requests") == {"full_name": "psf/requests", "default_branch": "main", "private": False}
    assert gh.repo_info("nobody/nothing") is None


def test_unchanged_answers_reuse_the_etag():
    seen: list[httpx.Request] = []

    def branch(request):
        if request.headers.get("if-none-match") == '"v1"':
            return httpx.Response(304)
        return httpx.Response(200, json={"commit": {"sha": "abc"}}, headers={"etag": '"v1"'})

    gh = _github({"GET /repos/o/r/branches/main": branch}, seen)
    assert gh.branch_head("o/r", "main") == "abc"
    assert gh.branch_head("o/r", "main") == "abc"  # answered 304: no API call counted, same answer
    assert [r.headers.get("if-none-match") for r in seen] == [None, '"v1"']


def test_rate_limit_says_until_when_and_how_to_raise_it():
    gh = _github({"GET /repos/o/r": lambda r: httpx.Response(403, headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": "1893456000"})})
    with pytest.raises(GitHubLimited) as e:
        gh.repo_info("o/r")
    assert "GITHUB_TOKEN" in str(e.value) and "UTC" in str(e.value)


def test_compare_lists_paths_or_gives_up_on_big_changes():
    files = [{"filename": "a.py"}, {"filename": "b.py", "previous_filename": "old_b.py"}]
    gh = _github({"GET /repos/o/r/compare/s1...s2": lambda r: httpx.Response(200, json={"status": "ahead", "files": files}),
                  "GET /repos/o/r/compare/s1...s3": lambda r: httpx.Response(200, json={"status": "ahead", "files": [{"filename": f"{i}.py"} for i in range(300)]}),
                  "GET /repos/o/r/compare/s1...s4": lambda r: httpx.Response(200, json={"status": "diverged", "files": files})})
    assert gh.compare("o/r", "s1", "s2") == ["a.py", "b.py", "old_b.py"]
    assert gh.compare("o/r", "s1", "s3") is None  # more than GitHub lists
    assert gh.compare("o/r", "s1", "s4") is None  # a force-push: the base is not behind the head
    assert gh.compare("o/r", "s1", "nope") is None


def test_router_sends_installed_repositories_to_the_app():
    class App:
        def files(self, *a):
            return {"via": "app"}

        def installation_account(self, i):
            return {"login": "acme"}

    class Url:
        def files(self, *a):
            return {"via": "url"}

    r = RepoRouter(Url(), App())
    assert r.files("o/r", "main", "123") == {"via": "app"}
    assert r.files("o/r", "main", None) == {"via": "url"}
    assert r.installation_account("123") == {"login": "acme"}
    assert RepoRouter(Url()).installation_repositories("1") == []
    with pytest.raises(AttributeError):
        RepoRouter(Url()).installation_account("1")


# ---------------------------------------------------------------- the local queue
@pytest.fixture
def demo():
    return build_container(Settings(mode="demo", agent_retries=1, retry_sleep_s=0))


def _queued(container):
    q = LocalQueue(workers=1)
    container.deps.bus = q
    q.container = container
    return q


def test_an_event_is_marked_sent_only_after_its_handler_succeeds(demo):
    q = _queued(demo)
    calls = []
    demo.handle = lambda ev: calls.append(ev["type"]) or {"result": "ok"}
    publish(demo, "reviews", {"type": "noop", "project_id": "proj-demo", "dedupe": "t:1"})
    row_id, event = q.jobs.get_nowait()
    assert demo.store.outbox[row_id]["sent_at"] is None  # handed over, not yet sent
    q.run(row_id, event)
    assert calls == ["noop"] and demo.store.outbox[row_id]["sent_at"] is not None


def test_a_failed_event_stays_due_and_is_given_up_after_its_attempts(demo):
    q = _queued(demo)

    def boom(ev):
        raise RuntimeError("model timed out")
    demo.handle = boom
    publish(demo, "reviews", {"type": "noop", "project_id": "proj-demo", "dedupe": "t:2"})
    row_id, event = q.jobs.get_nowait()
    q.run(row_id, event)
    row = demo.store.outbox[row_id]
    assert row["sent_at"] is None and row["last_error"] == "RuntimeError"  # the kind of error only: its text can quote a row
    row["attempts"] = MAX_ATTEMPTS
    q.run(row_id, event)
    assert row["sent_at"] is not None and row["last_error"].startswith("gave up")


def test_no_ai_gives_up_at_once_and_says_why(demo):
    q = _queued(demo)

    def off(ev):
        raise AIUnavailable(AI_OFF)
    demo.handle = off
    publish(demo, "ingestion", {"type": "noop", "project_id": "proj-demo", "dedupe": "t:3"})
    row_id, event = q.jobs.get_nowait()
    q.run(row_id, event)
    assert demo.store.outbox[row_id]["sent_at"] is not None and "GEMINI_API_KEY" in demo.store.outbox[row_id]["last_error"]


def test_the_pump_skips_events_already_in_hand(demo):
    q = _queued(demo)
    publish(demo, "reviews", {"type": "noop", "project_id": "proj-demo", "dedupe": "t:4"})
    row_id, _ = q.jobs.get_nowait()
    demo.store.outbox[row_id]["next_attempt_at"] = datetime.now(timezone.utc) - timedelta(seconds=1)
    assert demo.store.outbox_due(10, skip=(row_id,)) == []
    assert [r["id"] for r in demo.store.outbox_due(10)] == [row_id]


def test_a_publish_with_the_same_key_is_one_job(demo):
    q = _queued(demo)
    publish(demo, "reviews", {"type": "noop", "project_id": "proj-demo", "dedupe": "t:5"})
    publish(demo, "reviews", {"type": "noop", "project_id": "proj-demo", "dedupe": "t:5"})
    assert q.jobs.qsize() == 1


# ---------------------------------------------------------------- the repository watcher
class FakeGitHub:
    def __init__(self):
        self.head = "s1"
        self.open: list[dict] = []
        self.closed: list[dict] = []
        self.diff: list[str] | None = ["engine/validate.py"]

    def branch_head(self, full, branch):
        return self.head

    def compare(self, full, base, head):
        return self.diff

    def pulls(self, full, state):
        return self.open if state == "open" else self.closed


def _pr(n, sha, **extra):
    return {"number": n, "html_url": f"https://github.com/o/r/pull/{n}", "head": {"sha": sha, "ref": f"task-{n}"}, "body": "",
            "user": {"login": "vijay"}, "updated_at": "2026-11-11T10:00:00Z", **extra}


@pytest.fixture
def watched():
    store = MemoryStore()
    store.add_project(Project(id="p1", org_id="o1", name="P", description=""))
    store.add_repo(Repository(id="r1", project_id="p1", full_name="o/r", status="ready", last_indexed_sha="s1"))
    store.add_repo(Repository(id="r2", project_id="p1", full_name="o/app", installation_id="99", status="ready", last_indexed_sha="x"))
    return store


def _watcher(store, gh):
    published = []

    class C:
        pass

    c = C()
    c.store = store
    c.settings = Settings(mode="demo")

    class Bus:
        def publish(self, topic, event):
            published.append((topic, event))

    c.deps = type("D", (), {"bus": Bus()})()
    return RepoWatcher(c, gh, 60), published


def test_watcher_sees_new_commits_and_pull_requests_once(watched):
    gh = FakeGitHub()
    w, out = _watcher(watched, gh)
    assert w.poll() == 0  # nothing new
    gh.head = "s2"
    gh.open = [_pr(4, "h1")]
    assert w.poll() == 2
    assert [(t, e["type"]) for t, e in out] == [("ingestion", "repo.pushed"), ("reviews", "pr.opened")]
    push = out[0][1]
    assert push["changed_paths"] == ["engine/validate.py"] and push["installation_id"] is None and push["dedupe"] == "push:s2:r1"
    assert out[1][1]["dedupe"] == "pr:4:h1:r1"
    assert w.poll() == 0  # the same head and pull request are not published again
    gh.open = [_pr(4, "h2")]
    assert w.poll() == 1 and out[-1][1]["dedupe"] == "pr:4:h2:r1"


def test_watcher_reindexes_when_github_cannot_list_the_changes(watched):
    gh = FakeGitHub()
    gh.head, gh.diff = "s9", None
    w, out = _watcher(watched, gh)
    w.poll()
    assert out[0][1]["type"] == "repo.connected" and out[0][1]["ref"] == "main"


def test_watcher_reports_recent_merges_only_and_skips_repositories_still_indexing(watched):
    gh = FakeGitHub()
    now = datetime.now(timezone.utc)
    gh.closed = [_pr(5, "m1", merged_at=(now - timedelta(hours=1)).isoformat()),
                 _pr(6, "m2", merged_at=(now - timedelta(days=30)).isoformat()),
                 _pr(7, "m3", merged_at=None)]
    w, out = _watcher(watched, gh)
    w.poll()
    assert [(e["type"], e["pr_number"]) for _, e in out] == [("pr.merged", 5)]
    watched.update_repo("r1", status="indexing")
    gh.head = "s5"
    w2, out2 = _watcher(watched, gh)
    w2.poll()
    assert all(e["type"] != "repo.pushed" for _, e in out2)


def test_only_repositories_connected_by_url_are_watched(watched):
    assert [r["id"] for r in watched.watched_repositories()] == ["r1"]


# ---------------------------------------------------------------- signed file links
def test_signed_links_name_one_file_and_expire(tmp_path):
    blobs = ServedLocalStorage(str(tmp_path), signing_key(str(tmp_path)))
    uri = blobs.put("gs://local/recordings/m1/a.webm", b"video")
    link = blobs.signed_url(uri, minutes=5)
    token = link.rsplit("/", 1)[1]
    assert blobs.verify(token) == uri
    body, sig = token.split(".")
    assert blobs.verify(body + "." + sig[:-2] + ("AA" if not sig.endswith("AA") else "BB")) is None  # altered signature
    assert blobs.verify("garbage") is None
    assert blobs.verify(blobs.signed_url(uri, minutes=-1).rsplit("/", 1)[1]) is None  # expired
    other = ServedLocalStorage(str(tmp_path), b"another key")
    assert other.verify(token) is None
    assert signing_key(str(tmp_path)) == signing_key(str(tmp_path))  # generated once, then kept


# ---------------------------------------------------------------- live updates
def test_topics_of_store_writes():
    assert topic_of("notify") == "notifications"
    assert topic_of("create_approval") == "approvals"
    assert topic_of("update_task") == "tasks"
    assert topic_of("end_meeting") == "meetings"
    assert topic_of("update_repo") == "sources"
    assert topic_of("get_meeting") is None and topic_of("tasks") is None


def test_hub_sends_a_change_to_its_organisation_and_a_notification_to_its_person():
    async def run():
        hub = LiveHub()
        hub.bind(asyncio.get_running_loop())
        a, b, other = hub.subscribe("o1", "u1"), hub.subscribe("o1", "u2"), hub.subscribe("o2", "u3")
        hub.publish({"o": "o1", "t": "tasks", "p": "p1"})
        hub.publish({"o": "o1", "t": "notifications", "u": "u2"})
        await asyncio.sleep(0.01)
        got = lambda s: [s.queue.get_nowait() for _ in range(s.queue.qsize())]  # noqa: E731
        assert got(a) == [{"t": "tasks", "p": "p1"}]
        assert got(b) == [{"t": "tasks", "p": "p1"}, {"t": "notifications", "p": None}]
        assert got(other) == []
        for _ in range(500):  # far behind: one "reload everything" instead of the backlog
            hub._dispatch({"o": "o1", "t": "tasks", "p": "p1"})
        assert got(a)[0] == {"t": "*", "p": None}
    asyncio.run(run())


def test_demo_store_announces_its_writes():
    seen = []

    class Hub:
        def publish(self, change):
            seen.append(change)

    store = LiveStore(MemoryStore(), Hub())
    store.add_project(Project(id="p1", org_id="o1", name="P", description=""))
    store.get_project("p1")
    store.notify("u1", "info", "hello", "/x")
    assert seen == [{"t": "projects", "u": None}, {"t": "notifications", "u": "u1"}]


def test_demo_store_announces_expired_and_discarded_meetings():
    """Postgres announces every meetings row change (009_live.sql); the demo store must too, or a scheduled session that
    lapsed or was discarded stays on other people's screens until they reload."""
    seen = []

    class Hub:
        def publish(self, change):
            seen.append(change)

    store = LiveStore(MemoryStore(), Hub())
    store.add_project(Project(id="p1", org_id="o1", name="P", description=""))
    expired, discarded = (store.create_meeting("p1", title, None, provider="manual") for title in ("Lapsed", "Never held"))
    seen.clear()
    assert store.expire_meeting(expired.id) and store.discard_meeting(discarded.id)
    assert seen == [{"t": "meetings", "u": None}, {"t": "meetings", "u": None}]
    assert topic_of("discard_orphan_proposals") == "tasks" and topic_of("set_member_status") == "people"


def test_live_stream_over_http_in_demo_mode():
    """A real server (the test client cannot stream an endless response): sign-in is required, a change arrives as an event."""
    import socket

    import uvicorn

    from app.main import create_app

    get_settings.cache_clear()
    app = create_app()
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning", lifespan="on"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    try:
        for _ in range(200):
            if server.started:
                break
            time.sleep(0.05)
        base = f"http://127.0.0.1:{port}/api"
        with httpx.Client(timeout=10) as http:
            assert http.get(f"{base}/live").status_code == 401
            with http.stream("GET", f"{base}/live", headers={"X-Demo-User": "u-priya"}) as r:
                assert r.headers["content-type"].startswith("text/event-stream")
                lines = r.iter_lines()
                assert next(lines) == "retry: 3000"
                while not next(lines).startswith("event: ready"):
                    pass

                def change():
                    time.sleep(0.2)
                    app.state.container.store.notify("u-priya", "info", "Live test", "/x")
                threading.Thread(target=change).start()
                event = None
                for line in lines:
                    if line.startswith("data:") and "notifications" in line:
                        event = json.loads(line[5:])
                        break
                assert event == {"t": "notifications", "p": None}
    finally:
        server.should_exit = True
        thread.join(timeout=10)
