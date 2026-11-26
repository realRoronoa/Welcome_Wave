"""The demo, as a test: repo -> KT call -> notes + KT links -> plan (approve) -> chat -> PR -> review (decide)."""
from app.agents.llm import read_data
from app.agents.supervisor import resume, run_event


def test_golden_path_through_the_supervisor(world, graph):
    store = world.store

    # 1. Manager connects the repo
    out = run_event(graph, {"type": "repo.connected", "project_id": "p1", "repository_id": "r1", "ref": "main"}, "repo:1")
    assert out["result"]["chunks"] > 10

    # 2-3. A KT call is recorded; the Meeting agent watches it (speech + screen)
    world.llm.responders["SegmentAnalysis"] = lambda p, media: {
        "utterances": [
            {"start_s": 5, "end_s": 20, "speaker": "Ravi", "text": "Validation runs every rule of a policy. Mail me at ravi@corp.com."},
            {"start_s": 60, "end_s": 90, "speaker": "Ravi", "text": "check_labels is where missing labels get reported."},
        ],
        "screen_moments": [{"at_s": 58, "description": "editor", "visible_paths": ["engine/validate.py"], "visible_symbols": ["check_labels"]}],
        "decisions": ["Violations are returned, never raised"],
        "action_items": [{"text": "Vijay to read engine/policy.py before Friday", "owner_hint": "Vijay", "at_s": 100}],
        "terms": [{"term": "violation", "definition": "a rule a resource breaks"}],
    }
    world.llm.responders["MeetingSummary"] = lambda p, m: {"summary": "How validation works.", "decisions": ["Violations are returned, never raised"]}

    def plan(prompt, media):
        data = read_data(prompt)
        assert "webhooks" in data["coverage_gaps"]  # the KT call never showed the webhook module
        return {
            "tasks": [
                {"title": "Read engine/policy.py", "description": "From the KT action item", "kind": "learn", "due_in_days": 3, "related_paths": ["engine/policy.py"], "source": "meeting"},
                {"title": "Explore the webhook module", "description": "Not covered in any KT yet", "kind": "learn", "due_in_days": 5, "related_paths": ["webhooks", "made/up/path"], "source": "coverage_gap"},
            ],
            "first_pr": {"title": "Validate privileged init containers", "why": "Small, well-scoped engine change", "issue_number": 12, "files": ["engine/validate.py"]},
        }

    world.llm.responders["TaskPlan"] = plan
    out = run_event(graph, {"type": "meeting.recording_ready", "project_id": "p1", "meeting_id": "m1",
                            "recording_uri": "gs://bucket/recordings/m1.mp4", "duration_s": 300}, "meeting:m1")
    assert "__interrupt__" in out  # paused, waiting for the manager
    notes = store.meeting_notes("m1")
    assert "ravi@corp.com" not in notes.utterances[0].text  # redacted
    links = store.kt_links("p1", path="engine/validate.py")
    assert links and links[0].symbol == "check_labels" and links[0].start_s == 58

    assert [a.kind for a in store.approvals("p1")].count("kt_followup") == 1  # the transcript went to the manager
    approval = next(a for a in store.approvals("p1") if a.kind == "task_plan")
    tasks = approval.payload["plans"][0]["tasks"]
    assert [t["title"] for t in tasks][-1] == "First PR: Validate privileged init containers"
    explore = next(t for t in tasks if t["title"] == "Explore the webhook module")
    assert explore["related_paths"] == ["webhooks"]  # invented path removed by validation

    # 4. Manager approves everything except one task
    first_pr = next(t for t in tasks if t["kind"] == "code")
    keep = [t["id"] for t in tasks if t["title"] != "Read engine/policy.py"]
    out = resume(graph, "meeting:m1", {"approve": keep, "user_id": "u-mgr"})
    assert out["result"] == {"approved": 2, "rejected": 1}
    assert store.get_task(first_pr["id"]).status == "approved"
    assert store.get_approval(approval.id).status == "partially_approved"

    # 5. New hire asks a question; the answer cites code and links the KT clip
    world.llm.responders["QADraft"] = lambda p, m: {
        "answer": "In check_labels.", "confidence": 0.9, "sufficient": True,
        "cited": [next(c["n"] for c in read_data(p)["context"] if "check_labels" in c["label"])],
    }
    out = run_event(graph, {"type": "chat.question", "project_id": "p1", "user_id": "u-hire", "question": "Where are missing labels reported?"}, "chat:1")
    assert out["result"]["status"] == "answered" and out["result"]["kt_clips"][0]["start_s"] == 58

    # 6. New hire opens the first PR; Review agent recommends, manager decides
    world.llm.responders["ReviewDraft"] = lambda p, m: {
        "recommendation": "needs_work", "summary": "Raising breaks the engine's contract.",
        "findings": [{"file": "engine/validate.py", "line": 31, "severity": "major", "message": "Validation must return violations, not raise.",
                      "evidence": 'raise ValueError("missing securityContext")'}],
        "questions": ["Why raise here?", "What do callers expect?", "Which test covers this?"],
    }
    pr = {"type": "pr.opened", "project_id": "p1", "full_name": "demo/mini-policy-engine", "pr_number": 7, "head_sha": "abc",
          "pr_url": "https://github.com/demo/mini-policy-engine/pull/7", "branch": f"task-{first_pr['id']}", "body": "Closes #12"}
    out = run_event(graph, pr, "pr:7:abc")
    assert "__interrupt__" in out
    rv = store.get_review(out["review_id"])
    assert rv.result.recommendation == "needs_work"
    assert {f.source for f in rv.result.findings} == {"rule", "llm"}  # missing tests (rule) + raise (llm, with evidence)
    assert store.get_task(first_pr["id"]).status == "submitted"

    out = resume(graph, "pr:7:abc", {"decision": "changes_requested", "user_id": "u-mgr"})
    assert store.get_task(first_pr["id"]).status == "changes_requested"
    assert store.get_review(rv.id).manager_decision == "changes_requested"


def test_model_outage_in_chat_degrades_to_a_human(world, graph):
    # no QADraft responder -> the model call fails -> the new hire gets an honest answer and a person gets the question
    from app.agents import ingestion
    ingestion.ingest_repo(world, world.store.get_repo("r1"), "main")
    out = run_event(graph, {"type": "chat.question", "project_id": "p1", "user_id": "u-hire", "question": "How does check_labels work?"}, "chat:err")
    assert out["result"]["status"] == "not_enough_information" and out["result"]["routed_to"]
    assert any(n["kind"] == "expert_request" for n in world.store.notifications)


def test_failing_background_agent_is_escalated_to_a_human(world, graph):
    # no SegmentAnalysis responder -> the Meeting agent fails every retry -> Supervisor escalates instead of crashing
    from app.agents import ingestion
    ingestion.ingest_repo(world, world.store.get_repo("r1"), "main")
    out = run_event(graph, {"type": "meeting.recording_ready", "project_id": "p1", "meeting_id": "m1",
                            "recording_uri": "gs://b/r.mp4", "duration_s": 60}, "rec:err")
    assert out["result"]["escalated"] is True
    assert any(n["kind"] == "incident" and n["user_id"] == "u-mgr" for n in world.store.notifications)
    assert world.store.runs[-1]["status"] == "failed"


def test_unknown_event_goes_to_escalation(graph):
    out = run_event(graph, {"type": "mystery", "project_id": "p1"}, "x:1")
    assert out["result"]["escalated"] is True
