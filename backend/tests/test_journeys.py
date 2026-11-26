"""Regression tests for the end-to-end journey defects (questions, onboarding, review, KT calls), in demo mode: the seeded
project, the offline model, in-memory stores. The store-level parts also run on Postgres in test_journeys_store.py."""
import hashlib
import hmac
import json

import pytest
from fastapi.testclient import TestClient

from app.agents import qa
from app.agents.llm import OfflineLLM, embed_data
from app.agents.review import NO_DIFF, review_pr
from app.agents.schemas import Chunk, Hit, QADraft, ReviewDraft
from app.agents.supervisor import run_event
from app.config import get_settings
from app.main import create_app
from app.ports import TaskRow

PID = "proj-demo"
HIRE, MANAGER, RAVI, MEERA = ({"X-Demo-User": u} for u in ("u-vijay", "u-priya", "u-ravi", "u-meera"))
UNANSWERABLE = "How is the mutate module benchmarked?"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("AGENT_RETRIES", "1")
    monkeypatch.setenv("RETRY_SLEEP_S", "0")
    monkeypatch.setenv("CHAT_RATE_PER_MINUTE", "1000")  # the limiter is per process: these tests ask many questions as one person
    get_settings.cache_clear()
    with TestClient(create_app()) as c:
        yield c
    get_settings.cache_clear()


def _store(c):
    return c.app.state.container.store


def _notes(c, user_id, kind=None):
    return [n for n in _store(c).notifications if n["user_id"] == user_id and (kind is None or n["kind"] == kind)]


def _ask(c, question, who=HIRE):
    r = c.post("/api/chat", json={"project_id": PID, "question": question}, headers=who)
    assert r.status_code == 200, r.text
    return r.json()


def _approve_plan(c):
    plan = next(a for a in c.get(f"/api/approvals?project_id={PID}", headers=MANAGER).json() if a["kind"] == "task_plan")
    tasks = plan["payload"]["plans"][0]["tasks"]
    assert c.post(f"/api/approvals/{plan['id']}/decide", json={"approve": [t["id"] for t in tasks]}, headers=MANAGER).status_code == 200
    return plan, c.get(f"/api/tasks?project_id={PID}", headers=HIRE).json()


# ---------------- questions ----------------
def test_the_asker_sees_the_experts_answer_on_their_own_question(client):
    asked = _ask(client, UNANSWERABLE)
    assert asked["status"] == "not_enough_information" and asked["routed_to"] == "u-ravi"
    [req] = [r for r in client.get("/api/experts/requests", headers=RAVI).json() if r["question"] == UNANSWERABLE]
    [ping] = [n for n in _notes(client, "u-ravi", "expert_request") if UNANSWERABLE in n["text"]]  # the seed left one more in his inbox
    assert ping["text"] == f"Vijay (New hire) asked: {UNANSWERABLE}"  # who asked, not always "a new engineer"
    turn = client.get(f"/api/chat/history?project_id={PID}", headers=HIRE).json()[-1]
    assert turn["expert"] == {"status": "open", "name": "Ravi (Senior)", "answer": None, "answered_at": None}

    assert client.post(f"/api/experts/requests/{req['id']}/answer", json={"answer": "With pytest-benchmark in CI."}, headers=RAVI).status_code == 200
    turn = client.get(f"/api/chat/history?project_id={PID}", headers=HIRE).json()[-1]
    assert turn["expert"]["status"] == "answered" and turn["expert"]["answer"] == "With pytest-benchmark in CI."
    assert turn["expert"]["name"] == "Ravi (Senior)" and turn["expert"]["answered_at"]
    [told] = _notes(client, "u-vijay", "expert_answer")
    assert told["link"] == f"/chat?message={asked['message_id']}&project={PID}"  # opens the very turn
    assert [n for n in _notes(client, "u-ravi", "expert_request") if UNANSWERABLE in n["text"]][0]["read_at"] is not None  # settled
    again = _ask(client, UNANSWERABLE)  # the next person who asks gets the verified answer, labelled with who verified it
    assert any(c["label"].startswith("Verified by Ravi (Senior)") for c in again["citations"])


def test_asking_the_same_question_again_does_not_hand_it_over_twice(client):
    first = _ask(client, UNANSWERABLE)
    second = _ask(client, "  how is the MUTATE module benchmarked  ")
    assert second["routed_to"] == first["routed_to"] == "u-ravi" and "asked this already" in second["answer"]
    assert len([r for r in client.get("/api/experts/requests", headers=RAVI).json() if r["question"] == UNANSWERABLE]) == 1  # the seeded one is another
    assert len([n for n in _notes(client, "u-ravi", "expert_request") if UNANSWERABLE in n["text"]]) == 1
    turns = client.get(f"/api/chat/history?project_id={PID}", headers=HIRE).json()[-2:]
    assert [t["expert"]["status"] for t in turns] == ["open", "open"]  # both turns show who has it


def test_a_question_is_never_routed_back_to_the_person_who_asked(client):
    asked = _ask(client, "Why is the mutate module benchmark slow?", who=RAVI)  # Ravi owns engine/
    assert asked["status"] == "not_enough_information" and asked["routed_to"] not in {None, "u-ravi"}
    assert "Ravi" not in asked["answer"].split("passed it to")[-1]
    [ping] = _notes(client, asked["routed_to"], "expert_request")
    assert ping["text"].startswith("Ravi (Senior) asked:")
    by_manager = _ask(client, "Who approves production deployments on weekends?", who=MANAGER)
    assert by_manager["routed_to"] != "u-priya"


def test_a_specific_code_owner_beats_the_catch_all(client):
    c = client.app.state.container
    hit = lambda path, sim: Hit(chunk=Chunk(id=path, project_id=PID, source_type="code", ref={"path": path, "start_line": 1, "end_line": 2},  # noqa: E731
                                            content="x"), score=sim, similarity=sim)
    expert, path = qa._pick_expert(c.deps, PID, [hit("README.md", 0.154), hit("webhooks/server.py", 0.138)], asker_id="u-vijay")
    assert (expert.id, path) == ("u-meera", "webhooks/server.py")  # README.md matches only '*' (@priya-mgr)
    expert, path = qa._pick_expert(c.deps, PID, [hit("README.md", 0.2)], asker_id="u-vijay")
    assert (expert.id, path) == ("u-priya", "README.md")  # the catch-all still answers when nothing more specific matched


def test_offline_answers_quote_the_cited_moment_and_only_relevant_sources():
    llm = OfflineLLM()

    def draft(question, blocks):
        return llm.structured(embed_data({"question": question, "context": blocks}), QADraft)

    meeting = {"n": 1, "label": "KT #1 at 01:38", "text": "Meeting: KT #1 (01:38-03:50)\n[01:38] Ravi: We never raise inside validation. "
               "We return a list of violations.\n[02:22] Vijay: Where do the default labels come from?"}
    code = {"n": 2, "label": "webhooks/server.py:L25-L30 (do_POST)", "text": "File: webhooks/server.py (lines 25-30)\nSymbol: do_POST\ndef do_POST(self): ..."}
    d = draft("Why does validation return a list instead of raising?", [meeting, code])
    assert d.sufficient and d.cited == [1]  # the unrelated second block is not cited
    assert "[01:38] Ravi: We never raise" in d.answer  # the first utterance is quoted, not skipped as a header
    off = draft("What is our on-call rotation and pager escalation policy?",
                [{"n": 1, "label": "engine/policy.py:L22-L26 (load_policy)", "text": "File: engine/policy.py\nSymbol: load_policy\ndef load_policy(text): return Policy(...)"}])
    assert not off.sufficient and off.cited == []


def test_a_vote_can_be_taken_back(client):
    mid = _ask(client, "Which function reports missing labels?")["message_id"]
    assert client.post(f"/api/chat/{mid}/feedback", json={"value": 1}, headers=HIRE).status_code == 204
    assert client.post(f"/api/chat/{mid}/feedback", json={"value": 0}, headers=HIRE).status_code == 204
    turn = next(t for t in client.get(f"/api/chat/history?project_id={PID}", headers=HIRE).json() if t["message_id"] == mid)
    assert turn["feedback"] is None


# ---------------- onboarding ----------------
def test_deciding_a_plan_retires_its_approval_notification(client):
    plan = lambda: [n for n in _notes(client, "u-priya", "approval") if "Task agent" in n["text"]]  # noqa: E731
    [before] = plan()
    assert before["read_at"] is None
    _approve_plan(client)
    assert plan()[0]["read_at"] is not None


def test_a_submitted_task_reaches_done_through_the_manager_with_a_notification_each_step(client):
    _, tasks = _approve_plan(client)
    learn = next(t for t in tasks if t["kind"] == "learn")
    assert learn["assignee"] == "Vijay (New hire)" and learn["review_id"] is None
    before = len(_notes(client, "u-priya", "task"))
    assert client.patch(f"/api/tasks/{learn['id']}", json={"status": "in_progress"}, headers=HIRE).status_code == 200
    assert client.patch(f"/api/tasks/{learn['id']}", json={"status": "submitted"}, headers=HIRE).status_code == 200
    started, submitted = _notes(client, "u-priya", "task")[before:]
    assert "started" in started["text"] and "submitted" in submitted["text"] and submitted["link"] == f"/tasks?project={PID}"
    assert client.patch(f"/api/tasks/{learn['id']}", json={"status": "done"}, headers=HIRE).status_code == 403  # not the hire's call
    r = client.patch(f"/api/tasks/{learn['id']}", json={"status": "done"}, headers=MANAGER)
    assert r.status_code == 200 and r.json()["status"] == "done"
    assert "marked" in _notes(client, "u-vijay", "task")[-1]["text"]
    dash = client.get(f"/api/dashboard?project_id={PID}", headers=MANAGER).json()
    assert "tasks_done" not in dash["people"][0] and dash["coverage"]  # KT coverage, not a person's output


def test_an_invitee_joins_the_demo_and_can_be_onboarded(client):
    r = client.post(f"/api/projects/{PID}/invitations", json={"email": "asha.qa@example.com", "role": "new_hire", "name": "Asha"}, headers=MANAGER)
    assert r.status_code == 201
    people = client.get("/api/demo/people", headers=MANAGER).json()
    [inv] = [i for i in people["invited"] if i["email"] == "asha.qa@example.com"]
    joined = client.post("/api/demo/invitations/accept", json={"project_id": PID, "invitation_id": inv["invitation_id"]}, headers=MANAGER)
    assert joined.status_code == 200, joined.text
    asha = joined.json()
    assert asha["role"] == "new_hire" and client.get("/api/me", headers={"X-Demo-User": asha["id"]}).json()["name"] == "Asha"
    assert not client.get("/api/demo/people", headers=MANAGER).json()["invited"]
    r = client.post(f"/api/projects/{PID}/onboardings", json={"new_hire_id": asha["id"], "start_date": "2026-11-02"}, headers=MANAGER)
    assert r.status_code == 202, r.text


def test_experts_can_manage_documents_they_can_open(client, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # LocalBlobStorage writes under .blobs in the working directory
    files = {"file": ("runbook.md", b"# Quokka release runbook\n\nRoll back with `quokka rollback --to previous`.", "text/markdown")}
    r = client.post(f"/api/projects/{PID}/documents", files=files, data={"title": "Quokka release runbook"}, headers=RAVI)
    assert r.status_code == 202, r.text
    doc_id = r.json()["document_id"]
    view = client.get(f"/api/documents/{doc_id}", headers=HIRE).json()
    assert view["document"]["title"] == "Quokka release runbook" and "quokka rollback" in view["pages"][0]["text"]
    assert "uri" not in view["document"]
    f = client.get(f"/api/documents/{doc_id}/file", headers=HIRE)
    assert f.status_code == 200 and f.content.startswith(b"# Quokka") and "attachment" in f.headers["content-disposition"]
    assert client.delete(f"/api/projects/{PID}/documents/{doc_id}", headers=HIRE).status_code == 403
    assert client.delete(f"/api/projects/{PID}/documents/{doc_id}", headers=RAVI).status_code == 204
    assert client.get(f"/api/documents/{doc_id}", headers=HIRE).status_code == 404
    hits = client.app.state.container.index.search(PID, "quokka rollback", k=8)
    assert not [h for h in hits if h.chunk.source_type == "doc"]


# ---------------- review ----------------
def test_a_demo_pull_request_gets_a_review_with_findings(client):
    _, tasks = _approve_plan(client)
    code = next(t for t in tasks if t["kind"] == "code")
    assert client.post("/api/demo/pull-request", json={"task_id": code["id"]}, headers=MANAGER).status_code == 403
    r = client.post("/api/demo/pull-request", json={"task_id": code["id"]}, headers=HIRE)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["events"] == 1 and out["task"]["status"] == "submitted" and out["review_id"]
    review = client.get(f"/api/reviews/{out['review_id']}", headers=HIRE).json()
    assert review["result"]["recommendation"] == "needs_work" and review["approval_state"] == "pending"
    sources = {f["source"] for f in review["result"]["findings"]}
    assert sources == {"rule", "llm"}  # "no tests" from the rules, the new `raise` from the (offline) model, each with evidence
    assert any("raise" in f["evidence"] for f in review["result"]["findings"])
    listed = next(t for t in client.get(f"/api/tasks?project_id={PID}", headers=HIRE).json() if t["id"] == code["id"])
    assert listed["review_id"] == out["review_id"]  # the task links to its review
    team = next(t for t in client.get(f"/api/tasks?project_id={PID}", headers=MANAGER).json() if t["id"] == code["id"])
    assert team["review_pending"] is True and team["assignee"] == "Vijay (New hire)"
    # a manager cannot mark it done behind the review's back
    assert client.patch(f"/api/tasks/{code['id']}", json={"status": "done"}, headers=MANAGER).status_code == 409
    [approval] = [a for a in client.get(f"/api/approvals?project_id={PID}", headers=MANAGER).json() if a["kind"] == "review"]
    assert client.post(f"/api/approvals/{approval['id']}/decide", json={"decision": "changes_requested"}, headers=MANAGER).status_code == 200
    told = _notes(client, "u-vijay", "review")[-1]
    assert told["link"] == f"/reviews/{out['review_id']}?project={PID}"  # to what to change, not the task list
    assert _notes(client, "u-priya", "approval")[-1]["read_at"] is not None


def test_a_review_replaced_by_a_newer_commit_says_so(client):
    _, tasks = _approve_plan(client)
    code = next(t for t in tasks if t["kind"] == "code")
    first = client.post("/api/demo/pull-request", json={"task_id": code["id"]}, headers=HIRE).json()["review_id"]
    second = client.post("/api/demo/pull-request", json={"task_id": code["id"]}, headers=HIRE).json()["review_id"]
    assert first != second
    old = client.get(f"/api/reviews/{first}", headers=HIRE).json()
    assert old["approval_state"] == "closed" and old["newer_review_id"] == second
    new = client.get(f"/api/reviews/{second}", headers=HIRE).json()
    assert new["approval_state"] == "pending" and new["newer_review_id"] is None


def test_a_review_of_an_empty_diff_says_it_could_not_judge(world):
    world.llm.responders["ReviewDraft"] = lambda p, m: ReviewDraft(recommendation="looks_good", summary="Looks fine.")
    task = TaskRow(id="t1", onboarding_id="ob1", title="Fix", description="", kind="code", status="submitted")
    res = review_pr(world, "p1", task, "demo/mini-policy-engine", None, 999, "abc")  # no files for PR 999
    assert res.recommendation == "not_enough_information" and res.summary == NO_DIFF and res.findings == []


def test_a_merged_pull_request_closes_its_pending_review(world, graph):
    from datetime import date

    from app.agents.schemas import ProposedTask

    store = world.store
    world.llm.responders["ReviewDraft"] = lambda p, m: ReviewDraft(recommendation="looks_good", summary="ok")
    [code] = store.create_tasks("ob1", [ProposedTask(title="Fix privileged init containers", description="", kind="code", due_in_days=5,
                                                     source="first_pr", issue_number=12)], date.today())
    store.update_task(code.id, status="approved")
    pr = {"project_id": "p1", "repository_id": "r1", "full_name": "demo/mini-policy-engine", "installation_id": None, "pr_number": 7,
          "pr_url": "https://github.com/demo/mini-policy-engine/pull/7", "head_sha": "a1", "branch": "fix", "body": "Closes #12",
          "author": "vijay"}
    run_event(graph, {**pr, "type": "pr.opened"}, "pr-open")
    [pending] = [a for a in store.approvals("p1") if a.kind == "review"]
    run_event(graph, {**pr, "type": "pr.merged"}, "pr-merged")
    assert store.get_task(code.id).status == "done"
    closed = store.get_approval(pending.id)
    assert closed.status == "rejected" and closed.decided_by is None  # closed by the system, so deciding it is refused as stale
    assert not graph.get_state({"configurable": {"thread_id": "pr-open"}}).next  # its parked run is gone: nothing can resume it


def test_a_malformed_pull_request_webhook_is_ignored_not_a_500(client):
    def send(body: bytes):
        sig = "sha256=" + hmac.new(b"dev-secret", body, hashlib.sha256).hexdigest()
        return client.post("/webhooks/github", content=body, headers={"X-GitHub-Event": "pull_request", "X-Hub-Signature-256": sig})

    partial = json.dumps({"action": "opened", "repository": {"full_name": "demo/mini-policy-engine"}, "pull_request": {"number": 5}}).encode()
    assert (send(partial).status_code, send(partial).json()) == (202, {"events": 0})
    no_user = json.dumps({"action": "opened", "repository": {"full_name": "demo/mini-policy-engine"},
                          "pull_request": {"number": 5, "html_url": "https://x/5", "head": {"sha": "s", "ref": "b"}, "user": None}}).encode()
    assert send(no_user).status_code == 202
    assert send(b"not json").status_code == 400


# ---------------- KT sessions ----------------
def _book(client, who=MANAGER, presenter="u-meera"):
    plan = client.get(f"/api/kt-plan?project_id={PID}", headers=who).json()
    s = next(s for s in plan["sessions"] if (s["presenter"] or {}).get("user_id") == presenter)
    r = client.post("/api/kt-plan/schedule", json={"project_id": PID, "key": s["key"]}, headers=who)
    assert r.status_code == 200, r.text
    return r.json()["meeting"]


def test_cancelling_a_booked_session_tells_the_presenter(client):
    m = _book(client)
    [booking] = _notes(client, "u-meera", "kt_request")
    assert booking["link"] == f"/meetings?call={m['id']}&project={PID}"
    assert client.post(f"/api/meetings/{m['id']}/cancel", headers=RAVI).status_code == 403  # not the booker, not a manager
    assert client.post(f"/api/meetings/{m['id']}/cancel", headers=MANAGER).status_code == 200
    [off] = _notes(client, "u-meera", "meeting")
    assert "cancelled" in off["text"] and m["title"] in off["text"]
    assert _notes(client, "u-meera", "kt_request")[0]["read_at"] is not None  # the booking no longer asks her to join


def test_an_expert_can_cancel_the_session_they_booked(client):
    m = _book(client, who=RAVI, presenter="u-ravi")
    assert m["created_by"] == "u-ravi"
    assert client.post(f"/api/meetings/{m['id']}/cancel", headers=RAVI).json()["meeting"]["status"] == "cancelled"


def test_a_session_on_the_demo_calendar_has_a_meet_link_and_its_cancel_reaches_the_calendar(client):
    """KT sessions are held on Google Meet (the demo's built-in calendar), not in the app: no join token, a Meet link."""
    m = client.post("/api/meetings", json={"project_id": PID, "title": "KT #2", "starts_at": "2030-01-07T10:00:00Z", "attendees": ["u-ravi"]},
                    headers=MANAGER).json()["meeting"]
    assert m["provider"] == "google" and m["join_url"].startswith("https://meet.google.com/") and m["status"] == "scheduled"
    assert [a["user_id"] for a in m["attendees"]] == ["u-priya", "u-ravi"]
    sched = client.app.state.container.extras["scheduler"]
    assert sched.events[m["calendar_event_id"]].attendees == ["priya@demo.dev", "ravi@demo.dev"]
    assert client.post(f"/api/meetings/{m['id']}/cancel", headers=MANAGER).json()["meeting"]["status"] == "cancelled"
    assert sched.cancelled == [m["calendar_event_id"]]
    assert client.post(f"/api/meetings/{m['id']}/cancel", headers=MANAGER).status_code == 409


def test_the_seeded_kt_call_has_a_date(client):
    mtg = next(m for m in client.get(f"/api/meetings?project_id={PID}", headers=HIRE).json() if m["id"] == "mtg-1")
    assert mtg["started_at"]
