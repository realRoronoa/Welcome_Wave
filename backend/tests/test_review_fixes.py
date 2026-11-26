"""Regression tests for defects found by the production review."""
from datetime import date

import pytest
from langgraph.checkpoint.memory import MemorySaver

from app.agents.specialists.planner.task import find_task_for_pr, short_ref
from app.agents.supervisor.graph import build_supervisor
from app.container import ApprovalStale, Container
from app.ports import TaskRow


def _task(tid, issue=None):
    return TaskRow(id=tid, onboarding_id="ob", title="t", description="d", kind="code", status="approved", github_issue_number=issue)


@pytest.mark.parametrize("branch", ["task-{ref}", "task-{ref}-fix-docs", "task/{ref}-add-tests", "feature/task_{ref}", "task-{full}"])
def test_branch_names_with_suffixes_find_the_task(branch):
    tid = "1a2b3c4d-5e6f-4a1b-8c9d-0e1f2a3b4c5d"
    other = "9f8e7d6c-5e6f-4a1b-8c9d-0e1f2a3b4c5d"
    tasks = [_task(tid), _task(other)]
    assert find_task_for_pr(tasks, branch.format(ref=short_ref(tid), full=tid), "").id == tid


def test_ambiguous_or_short_refs_match_nothing():
    tasks = [_task("aaaaaaaa-0000-4000-8000-000000000001"), _task("aaaaaaaa-0000-4000-8000-000000000002")]
    assert find_task_for_pr(tasks, "task-aaaaaaaa", "") is None  # two tasks share the prefix: refuse to guess
    assert find_task_for_pr(tasks, "task-aaaa", "") is None


def _plan_responder(prompt, media):
    return {"tasks": [], "first_pr": {"title": "Validate privileged init containers", "why": "starter", "issue_number": 12,
                                      "files": ["engine/validate.py"]}}


def _ready(world):
    from app.agents.specialists.librarian import agent as ingestion

    ingestion.ingest_repo(world, world.store.get_repo("r1"), "main")
    world.llm.responders["TaskPlan"] = _plan_responder


def test_only_the_new_hires_own_pr_moves_their_task(world):
    _ready(world)
    saver = MemorySaver()
    c = Container(world.settings, world, build_supervisor(world, saver), build_supervisor(world, None), saver)
    c.handle({"type": "onboarding.started", "project_id": "p1", "onboarding_id": "ob1", "dedupe": "ob:ob1"})
    approval = world.store.approvals("p1")[0]
    first_pr = next(t for p in approval.payload["plans"] for t in p["tasks"] if t["kind"] == "code")
    c.decide(approval, {"approve": [first_pr["id"]], "user_id": "u-mgr"})

    pr = {"type": "pr.opened", "project_id": "p1", "full_name": "demo/mini-policy-engine", "pr_number": 9, "head_sha": "x",
          "branch": "fix", "body": "Closes #12"}
    out = c.handle({**pr, "author": "mallory", "dedupe": "pr:9:x"})  # a stranger's PR that "closes" the issue
    assert out["result"]["matched_task"] is None and world.store.get_task(first_pr["id"]).status == "approved"
    world.llm.responders["ReviewDraft"] = lambda p, m: {"recommendation": "looks_good", "summary": "ok"}
    out = c.handle({**pr, "author": "Vijay", "head_sha": "y", "dedupe": "pr:9:y"})  # the hire's own (login is case-insensitive)
    assert world.store.get_task(first_pr["id"]).status == "submitted"


def test_redelivered_event_does_not_restart_a_run_waiting_for_a_manager(world):
    _ready(world)
    saver = MemorySaver()
    c = Container(world.settings, world, build_supervisor(world, saver), build_supervisor(world, None), saver)
    c.handle({"type": "onboarding.started", "project_id": "p1", "onboarding_id": "ob1", "dedupe": "ob:ob1"})
    world.store.events.pop("ob:ob1")  # as if the worker died after the run paused but before marking the event done
    out = c.handle({"type": "onboarding.started", "project_id": "p1", "onboarding_id": "ob1", "dedupe": "ob:ob1"})
    assert out == {"skipped": "waiting for approval"}
    assert len(world.store.approvals("p1")) == 1  # the manager is not asked twice


def test_a_decision_for_a_superseded_approval_is_refused(world):
    _ready(world)
    saver = MemorySaver()
    c = Container(world.settings, world, build_supervisor(world, saver), build_supervisor(world, None), saver)
    c.handle({"type": "onboarding.started", "project_id": "p1", "onboarding_id": "ob1", "dedupe": "ob:ob1"})
    real = world.store.approvals("p1")[0]
    orphan = world.store.create_approval("p1", "task_plan", real.thread_id, {"plans": []})  # e.g. left by a failed retry
    with pytest.raises(ApprovalStale):
        c.decide(orphan, {"approve": [], "user_id": "u-mgr"})
    assert world.store.get_approval(orphan.id).status == "rejected"
    assert world.store.get_approval(real.id).status == "pending"  # the real one is untouched and still decidable


def test_orphaned_proposals_are_discarded_before_a_new_plan(world):
    _ready(world)
    from app.agents.core.schemas import ProposedTask

    stray = world.store.create_tasks("ob1", [ProposedTask(title="Stray", description="d", kind="learn", due_in_days=2)], date.today())
    assert world.store.discard_orphan_proposals("ob1") == 1
    assert world.store.get_task(stray[0].id).status == "rejected"


def test_question_is_routed_only_to_project_members_who_can_answer(world):
    from app.agents.specialists.guide import qa
    from app.agents.specialists.librarian import agent as ingestion
    from app.ports import User

    ingestion.ingest_repo(world, world.store.get_repo("r1"), "main")
    world.store.set_codeowners("p1", "* @outsider @vijay")
    world.store.add_user(User(id="u-out", org_id="o1", email="o@x.dev", name="Outsider", role="expert", github_login="outsider"))
    world.llm.responders["QADraft"] = lambda p, m: {"answer": "", "cited": [], "confidence": 0.0, "sufficient": False}
    result, _ = qa.answer(world, "p1", "u-hire", "How does check_labels work?")
    assert result.routed_to == "u-mgr"  # not the non-member owner, not the new hire who is also an "owner"
