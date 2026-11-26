"""Regression tests for the runtime defects found by the third review: redelivery, approvals, PR matching, ingestion."""
import json
import threading
import time
from datetime import date

import pytest
from langgraph.checkpoint.memory import MemorySaver

from app.agents import ingestion, meeting
from app.agents.schemas import Citation, KTLink, QAResult
from app.agents.supervisor import build_supervisor, run_event
from app.container import ApprovalNotReady, ApprovalStale, Container
from app.ports import Onboarding, RepoBusy, User

OB = {"type": "onboarding.started", "project_id": "p1", "onboarding_id": "ob1", "dedupe": "ob:ob1"}
PR = {"type": "pr.opened", "project_id": "p1", "full_name": "demo/mini-policy-engine", "pr_number": 7, "branch": "fix", "body": "Closes #12"}


def _plan(prompt, media):
    return {"tasks": [{"title": "Read the validation engine", "description": "d", "kind": "learn", "due_in_days": 3}],
            "first_pr": {"title": "Validate privileged init containers", "why": "starter", "issue_number": 12, "files": ["engine/validate.py"]}}


@pytest.fixture
def c(world):
    ingestion.ingest_repo(world, world.store.get_repo("r1"), "main")
    world.llm.responders["TaskPlan"] = _plan
    world.llm.responders["ReviewDraft"] = lambda p, m: {"recommendation": "looks_good", "summary": "ok"}
    saver = MemorySaver()
    return Container(world.settings, world, build_supervisor(world, saver), build_supervisor(world, None), saver)


def _fail(monkeypatch, obj, name, times, exc=None):
    real, left = getattr(obj, name), [times]

    def flaky(*a, **kw):
        if left[0]:
            left[0] -= 1
            raise exc or RuntimeError("db blip")
        return real(*a, **kw)

    monkeypatch.setattr(obj, name, flaky)


def _approve_all(c, onboarding_id="ob1"):
    a = next(a for a in c.store.approvals("p1") if a.status == "pending" and a.payload["plans"][0]["onboarding_id"] == onboarding_id)
    c.decide(a, {"approve": [t["id"] for p in a.payload["plans"] for t in p["tasks"]], "user_id": "u-mgr"})
    return next(t for p in a.payload["plans"] for t in p["tasks"] if t["kind"] == "code")


# ---------------- a crashed run is continued, not dropped (runtime#0 / fix#52) ----------------
def test_a_run_that_died_mid_node_is_continued_on_redelivery(c, world, monkeypatch):
    from app.agents import task

    _fail(monkeypatch, task, "plan_for", 1, KeyboardInterrupt())  # the pod is killed while the Task agent plans
    with pytest.raises(KeyboardInterrupt):
        c.handle(OB)
    assert c.graph.get_state({"configurable": {"thread_id": "ob:ob1"}}).next == ("planner",)
    assert world.store.event_status("ob:ob1") == "none"  # released for Pub/Sub's redelivery

    out = c.handle(OB)  # the redelivery picks the run up where it stopped
    assert "__interrupt__" in out
    assert len(world.store.approvals("p1")) == 1 and world.store.event_status("ob:ob1") == "done"


def test_a_node_that_raised_after_saving_its_approval_is_continued_and_the_approval_waits(c, world, monkeypatch):
    # record_run fails on success and on the failure path, so the error escapes guarded() after the approval was saved
    _fail(monkeypatch, world.store, "record_run", 2)
    with pytest.raises(RuntimeError):
        c.handle(OB)
    approval = world.store.approvals("p1")[0]
    with pytest.raises(ApprovalNotReady):  # the run has not paused on it yet: not "stale", and not rejected
        c.decide(approval, {"approve": [], "user_id": "u-mgr"})
    assert world.store.get_approval(approval.id).status == "pending"

    assert "__interrupt__" in c.handle(OB)
    assert [a.id for a in world.store.approvals("p1")] == [approval.id]  # the same approval, not a second plan
    c.decide(world.store.get_approval(approval.id), {"approve": [], "user_id": "u-mgr"})
    assert world.store.get_approval(approval.id).status == "rejected"


def test_a_failing_state_read_releases_the_event_instead_of_running_it_blind(c, world, monkeypatch):
    def down(*a, **kw):
        raise RuntimeError("checkpoint store unreachable")

    monkeypatch.setattr(c.graph, "get_state", down)
    with pytest.raises(RuntimeError):
        c.handle(OB)
    assert world.store.event_status("ob:ob1") == "none" and world.store.approvals("p1") == []


def test_a_redelivery_of_a_parked_run_is_still_skipped(c, world):
    c.handle(OB)
    world.store.reset_event("ob:ob1")
    assert c.handle(OB) == {"skipped": "waiting for approval"}
    assert len(world.store.approvals("p1")) == 1


# ---------------- approvals: a retried decision, a superseded review ----------------
def test_a_retried_decision_reapplies_the_first_one(c, world, monkeypatch):
    c.handle(OB)
    approval = world.store.approvals("p1")[0]
    ids = [t["id"] for p in approval.payload["plans"] for t in p["tasks"]]
    _fail(monkeypatch, world.store, "get_onboarding", 1)  # the first resume fails halfway through applying the decision
    with pytest.raises(RuntimeError):
        c.decide(approval, {"approve": ids, "user_id": "u-mgr"})
    assert world.store.get_approval(approval.id).status == "pending"
    assert world.store.get_approval(approval.id).decision["approve"] == ids  # kept with the claim: the API audits this one

    out = c.decide(world.store.get_approval(approval.id), {"approve": ids[:1], "user_id": "u-other"})
    assert out["result"] == {"approved": 2, "rejected": 0}  # what the graph really applied, and what it reports
    assert {world.store.get_task(t).status for t in ids} == {"approved"}
    assert world.store.get_approval(approval.id).decided_by == "u-mgr"


def test_a_review_of_an_older_commit_is_superseded_by_the_newer_one(c, world):
    c.handle(OB)
    first_pr = _approve_all(c)
    c.handle({**PR, "author": "vijay", "head_sha": "s1", "dedupe": "pr:7:s1"})
    old = next(a for a in world.store.approvals("p1") if a.kind == "review")
    c.handle({**PR, "type": "pr.updated", "author": "vijay", "head_sha": "s2", "dedupe": "pr:7:s2"})

    assert world.store.get_approval(old.id).status == "rejected"
    assert c.graph.get_state({"configurable": {"thread_id": "pr:7:s1"}}).values == {}  # its parked run is gone
    with pytest.raises(ApprovalStale):
        c.decide(old, {"decision": "approved", "user_id": "u-mgr"})
    assert world.store.get_task(first_pr["id"]).status == "submitted"  # not "done" on code nobody reviewed

    new = next(a for a in world.store.approvals("p1") if a.kind == "review" and a.status == "pending")
    c.decide(new, {"decision": "approved", "user_id": "u-mgr"})
    assert world.store.get_task(first_pr["id"]).status == "done"


def test_the_older_commits_review_finishing_last_does_not_replace_the_newer_one(c, world):
    """Review r3 runtime#2: supersession followed the order reviews finished in, not the order of the commits."""
    c.handle(OB)
    first_pr = _approve_all(c)
    c.handle({**PR, "type": "pr.updated", "author": "vijay", "head_sha": "s2", "dedupe": "pr:7:s2", "pr_updated_at": "2026-10-16T10:05:00Z"})
    notes = len(world.store.notifications_for("u-mgr"))
    out = c.handle({**PR, "author": "vijay", "head_sha": "s1", "dedupe": "pr:7:s1", "pr_updated_at": "2026-10-16T10:00:00Z"})

    assert out["result"]["superseded"] is True and "__interrupt__" not in out  # ended, not parked on a dead approval
    assert c.graph.get_state({"configurable": {"thread_id": "pr:7:s1"}}).values == {}
    assert len(world.store.notifications_for("u-mgr")) == notes  # the manager is not asked to decide a stale review
    reviews = {world.store.get_review(a.payload["review_id"]).head_sha: a for a in world.store.approvals("p1", None) if a.kind == "review"}
    assert {sha: a.status for sha, a in reviews.items()} == {"s2": "pending", "s1": "rejected"}
    with pytest.raises(ApprovalStale):
        c.decide(reviews["s1"], {"decision": "approved", "user_id": "u-mgr"})
    assert world.store.get_task(first_pr["id"]).status == "submitted"
    c.decide(reviews["s2"], {"decision": "approved", "user_id": "u-mgr"})
    assert world.store.get_task(first_pr["id"]).status == "done"


# ---------------- PR matching (runtime#2 / fix#28) ----------------
def test_two_hires_sharing_an_issue_each_get_their_own_pr(c, world):
    world.store.add_user(User(id="u-ana", org_id="o1", email="ana@x.dev", name="Ana", role="new_hire", github_login="ana"), "p1")
    world.store.add_onboarding(Onboarding(id="ob2", project_id="p1", new_hire_id="u-ana", manager_id="u-mgr", start_date=date.today()))
    c.handle(OB)
    vijay = _approve_all(c, "ob1")
    c.handle({**OB, "onboarding_id": "ob2", "dedupe": "ob:ob2"})
    ana = _approve_all(c, "ob2")

    c.handle({**PR, "author": "ana", "head_sha": "a1", "dedupe": "pr:7:a1"})
    assert world.store.get_task(ana["id"]).status == "submitted" and world.store.get_task(vijay["id"]).status == "approved"


def test_without_a_github_username_only_a_task_reference_links_a_pr(c, world):
    world.store.users["u-hire"] = world.store.users["u-hire"].model_copy(update={"github_login": None})
    c.handle(OB)
    first_pr = _approve_all(c)
    out = c.handle({**PR, "author": "mallory", "head_sha": "m1", "dedupe": "pr:7:m1"})  # "Closes #12" from anyone
    assert out["result"]["matched_task"] is None and world.store.get_task(first_pr["id"]).status == "approved"

    c.handle({**PR, "author": "someone", "branch": f"task-{first_pr['id']}", "head_sha": "m2", "dedupe": "pr:7:m2"})
    assert world.store.get_task(first_pr["id"]).status == "submitted"


# ---------------- task plans: orphans of a failed attempt (data#4 call site) ----------------
def test_a_failed_attempt_withdraws_its_own_proposals_so_the_retry_proposes_them_again(c, world, monkeypatch):
    world.settings.agent_retries = 2
    _fail(monkeypatch, world.store, "create_approval", 1)
    saver = MemorySaver()
    Container(world.settings, world, build_supervisor(world, saver), None, saver).handle(OB)
    approval = world.store.approvals("p1")[0]
    assert sum(len(p["tasks"]) for p in approval.payload["plans"]) == 2  # not shrunk by the first attempt's leftovers
    assert [t.status for t in world.store.tasks(onboarding_id="ob1")].count("rejected") == 2


# ---------------- ingestion (runtime#4 / fix#25 / runtime#5) ----------------
def _chunks(world, path):
    return [ch for ch in world.index.chunks.values() if ch.ref.get("path") == path]


def test_concurrent_pushes_to_one_repository_do_not_duplicate_chunks(world, monkeypatch):
    repo = world.store.get_repo("r1")
    ingestion.ingest_repo(world, repo, "main")
    single = len(_chunks(world, "engine/validate.py"))
    heads = iter(["sha-a", "sha-b"])
    monkeypatch.setattr(world.repos, "head_sha", lambda *a: next(heads))
    real_file, fetching, b_started = world.repos.file, threading.Event(), threading.Event()

    def slow_file(*a):
        if not fetching.is_set():  # push A is mid-fetch when push B arrives
            fetching.set()
            b_started.wait(2)
            time.sleep(0.2)
        return real_file(*a)

    monkeypatch.setattr(world.repos, "file", slow_file)
    a = threading.Thread(target=ingestion.ingest_push, args=(world, repo, ["engine/validate.py"], [], "main"))
    a.start()
    fetching.wait(2)
    b_started.set()
    ingestion.ingest_push(world, repo, ["engine/validate.py"], [], "main")
    a.join()
    chunks = _chunks(world, "engine/validate.py")
    assert len(chunks) == single and {ch.ref["commit"] for ch in chunks} == {"sha-b"}
    assert world.store.get_repo("r1").last_indexed_sha == "sha-b"


def test_a_push_reads_one_resolved_commit_and_labels_chunks_with_it(world):
    repo = world.store.get_repo("r1")
    ingestion.ingest_push(world, repo, ["engine/validate.py"], [], "main")
    sha = world.repos.head_sha(repo.full_name, "main", None)
    assert {ch.ref["commit"] for ch in _chunks(world, "engine/validate.py")} == {sha}
    assert world.store.get_repo("r1").last_indexed_sha == sha


def test_a_second_connect_at_the_same_commit_is_not_reindexed(world):
    repo = world.store.get_repo("r1")
    assert ingestion.ingest_repo(world, repo, "main")["chunks"] > 0
    assert "skipped" in ingestion.ingest_repo(world, repo, "main")


TRUNK_PUSH = {"type": "repo.pushed", "project_id": "p1", "repository_id": "r1", "after": "x", "branch": "trunk",
              "default_branch": "trunk", "changed_paths": ["engine/validate.py"], "removed_paths": []}


def _github_default(monkeypatch, world, branch):
    monkeypatch.setattr(world.repos, "repo_info", lambda name, inst=None: {"full_name": name, "default_branch": branch, "private": True})


def test_a_push_after_the_default_branch_changed_rebuilds_from_the_new_branch(world, graph, monkeypatch):
    ingestion.ingest_repo(world, world.store.get_repo("r1"), "main")
    asked = []
    monkeypatch.setattr(world.repos, "head_sha", lambda name, ref, inst: asked.append(ref) or f"head-of-{ref}")
    _github_default(monkeypatch, world, "trunk")
    out = run_event(graph, TRUNK_PUSH, "push:x")
    assert "files" in out["result"] and asked == ["trunk"]
    repo = world.store.get_repo("r1")
    assert repo.default_branch == "trunk" and repo.last_indexed_sha == "head-of-trunk"


def test_a_failed_rebuild_for_a_new_default_branch_is_retried_as_a_rebuild(world, monkeypatch):
    """Review r3 runtime#4: the switch was saved first, so the retry took the push path and the repo stayed 'indexing'."""
    ingestion.ingest_repo(world, world.store.get_repo("r1"), "main")
    world.settings.agent_retries, world.settings.retry_sleep_s = 2, 0
    monkeypatch.setattr(world.repos, "head_sha", lambda name, ref, inst: f"head-of-{ref}")
    _github_default(monkeypatch, world, "trunk")
    _fail(monkeypatch, world.repos, "files", 1)
    out = run_event(build_supervisor(world, None), TRUNK_PUSH, "push:x")
    assert "files" in out["result"]  # the retry rebuilt the whole index, not just the push's paths
    repo = world.store.get_repo("r1")
    assert (repo.status, repo.default_branch, repo.last_indexed_sha) == ("ready", "trunk", "head-of-trunk")


def test_a_rebuild_that_keeps_failing_leaves_the_old_branch_and_offers_reindex(world, monkeypatch):
    ingestion.ingest_repo(world, world.store.get_repo("r1"), "main")
    world.settings.agent_retries, world.settings.retry_sleep_s = 1, 0
    monkeypatch.setattr(world.repos, "head_sha", lambda name, ref, inst: f"head-of-{ref}")
    _github_default(monkeypatch, world, "trunk")
    _fail(monkeypatch, world.repos, "files", 1)
    out = run_event(build_supervisor(world, None), TRUNK_PUSH, "push:x")
    assert out["result"]["escalated"] is True
    repo = world.store.get_repo("r1")
    assert (repo.status, repo.default_branch) == ("failed", "main")  # the next push still sees the switch and rebuilds


def test_a_delayed_push_from_before_a_switch_does_not_switch_back(world, monkeypatch):
    ingestion.ingest_repo(world, world.store.get_repo("r1"), "main")
    world.store.update_repo("r1", default_branch="trunk")
    _github_default(monkeypatch, world, "trunk")
    out = run_event(build_supervisor(world, None), {**TRUNK_PUSH, "branch": "main", "default_branch": "main"}, "push:old")
    assert "skipped" in out["result"] and world.store.get_repo("r1").default_branch == "trunk"


# ---------------- a full re-index keeps chunk ids (review r3 runtime#3) ----------------
def _cite(world, path):
    ch = next(c for c in world.index.chunks.values() if c.ref.get("path") == path)
    res = QAResult(status="answered", answer="a", citations=[Citation(chunk_id=ch.id, source_type="code", label=path, ref=ch.ref)],
                   kt_clips=[], confidence=0.9)
    return ch, world.store.save_chat("p1", "u-hire", "q", res)


def _stale(world, message_id):
    return world.store.chats[message_id]["stale"]


def test_answers_and_kt_links_survive_a_reindex_and_are_flagged_by_the_next_push(world, monkeypatch):
    heads = iter(["sha1", "sha2", "sha3"])
    monkeypatch.setattr(world.repos, "head_sha", lambda *a: next(heads))
    repo = world.store.get_repo("r1")
    ingestion.ingest_repo(world, repo, "main")
    before = set(world.index.chunks)
    ch, mid = _cite(world, "engine/validate.py")
    world.store.add_kt_links("p1", [KTLink(meeting_id="m1", file_path=ch.ref["path"], chunk_id=ch.id, start_s=1, end_s=2, confidence=0.9, method="path")])

    out = ingestion.ingest_repo(world, world.store.get_repo("r1"), "main")  # Re-index at a new commit, same files
    assert out["answers_marked_stale"] == 0 and not _stale(world, mid)
    assert set(world.index.chunks) == before  # same spans, same ids: the KT link still points at its chunk
    assert {ch.ref["commit"] for ch in world.index.chunks.values() if ch.source_type == "code"} == {"sha2"}

    out = ingestion.ingest_push(world, world.store.get_repo("r1"), ["engine/validate.py"], [], "main")
    assert out["answers_marked_stale"] == 1 and _stale(world, mid)
    assert world.store.kt_links("p1", path="engine/validate.py")[0].code_changed_since


def test_a_reindex_flags_answers_and_kt_links_on_files_that_are_gone(world, monkeypatch):
    repo = world.store.get_repo("r1")
    ingestion.ingest_repo(world, repo, "main")
    ch, mid = _cite(world, "engine/validate.py")
    world.store.add_kt_links("p1", [KTLink(meeting_id="m1", file_path=ch.ref["path"], start_s=1, end_s=2, confidence=0.9, method="path")])
    real = world.repos.files
    monkeypatch.setattr(world.repos, "files", lambda *a: {p: t for p, t in real(*a).items() if p != "engine/validate.py"})
    monkeypatch.setattr(world.repos, "head_sha", lambda *a: "sha-without-it")
    out = ingestion.ingest_repo(world, world.store.get_repo("r1"), "main")
    assert out["answers_marked_stale"] == 1 and _stale(world, mid)
    assert world.store.kt_links("p1", path="engine/validate.py")[0].code_changed_since


# ---------------- a busy repository hands the event back (review r3 data#1) ----------------
def test_an_event_for_a_repository_being_indexed_is_released_untouched_and_continued_later(c, world, monkeypatch):
    real, held = world.store.ingest_lock, [True]

    def lock(repo_id):
        if held[0]:
            raise RepoBusy(repo_id)
        return real(repo_id)

    monkeypatch.setattr(world.store, "ingest_lock", lock)
    push = {"type": "repo.pushed", "project_id": "p1", "repository_id": "r1", "after": "y", "branch": "main",
            "changed_paths": ["engine/validate.py"], "removed_paths": [], "dedupe": "push:y"}
    with pytest.raises(RepoBusy):
        c.handle(push)
    assert world.store.event_status("push:y") == "none"  # released for the redelivery
    assert not [r for r in world.store.runs if r["status"] == "failed"] and not world.store.notifications_for("u-mgr")  # no escalation
    held[0] = False
    assert c.handle(push)["result"]["reindexed_files"] == 1 and world.store.event_status("push:y") == "done"


class _Message:
    def __init__(self, event, attempt):
        self.data, self.message_id, self.delivery_attempt, self.calls = json.dumps(event).encode(), "m-1", attempt, []

    def __getattr__(self, name):  # ack, nack, modify_ack_deadline, drop
        return lambda *a: self.calls.append((name, *a))


@pytest.mark.parametrize("attempt", [1, None])
def test_the_worker_gives_the_slot_back_while_the_repository_is_busy(world, monkeypatch, attempt):
    from workers import worker

    container = Container(world.settings, world, build_supervisor(world, None))
    monkeypatch.setattr(world.store, "org_of_project", lambda pid: "o1")
    monkeypatch.setattr(container, "handle", lambda event: (_ for _ in ()).throw(RepoBusy("r1")))
    msg = _Message({"type": "repo.pushed", "project_id": "p1"}, attempt)
    worker._handle_message(container, world.settings, msg)
    assert msg.calls == [("modify_ack_deadline", worker.REPO_BUSY_RETRY_S), ("drop",)]  # neither held nor nacked


def test_the_last_delivery_attempt_waits_for_the_repository_instead_of_dead_lettering(world, monkeypatch):
    from workers import worker

    container = Container(world.settings, world, build_supervisor(world, None))
    monkeypatch.setattr(world.store, "org_of_project", lambda pid: "o1")
    tries = iter([RepoBusy("r1"), RepoBusy("r1"), {"result": "indexed"}])

    def handle(event):
        out = next(tries)
        if isinstance(out, Exception):
            raise out
        return out

    monkeypatch.setattr(container, "handle", handle)
    monkeypatch.setattr(time, "sleep", lambda s: None)
    msg = _Message({"type": "repo.pushed", "project_id": "p1"}, worker.MAX_DELIVERY_ATTEMPTS)
    worker._handle_message(container, world.settings, msg)
    assert msg.calls == [("ack",)]


# ---------------- meetings (runtime#6) ----------------
@pytest.mark.parametrize("duration", [0.0, 0.0004, 0.9])
def test_an_empty_recording_sends_no_clip(world, duration):
    world.llm.responders["MeetingSummary"] = lambda p, m: {"summary": "Nothing was recorded.", "decisions": []}
    meeting.process_recording(world, "p1", "m1", "gs://b/r.mp4", duration)
    assert [name for name, _ in world.llm.calls] == ["MeetingSummary"]
