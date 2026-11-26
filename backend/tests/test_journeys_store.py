"""The store reads and writes the journey fixes added, on MemoryStore and (with TEST_PG_ADMIN_URL) on PostgreSQL as ww_app:
both adapters must answer the same."""
from __future__ import annotations

from datetime import date

from app.adapters.memory import MemoryIndex
from app.adapters.postgres import PgIndex, PgStore
from app.agents.core.schemas import Chunk, ProposedTask, QAResult
from app.services.embeddings import HashEmbedder
from tests import test_round3_store as r3
from tests.test_round3_store import _join, _onboarding, _org, _result

pg, store = r3.pg, r3.store  # the same fixtures: one Postgres database per module, every test on both adapters

NEI = QAResult(status="not_enough_information", answer="I don't know", confidence=0.0)


def test_notifications_close_by_target_and_list_who_was_told(store):
    admin, project = _org(store, "note")
    meera = _join(store, admin, project, "meera", "expert")
    store.notify(admin.id, "approval", "plan", f"/approvals/a1?project={project.id}")
    store.notify(admin.id, "approval", "another plan", f"/approvals/a12?project={project.id}")  # starts the same: not the target
    store.notify(meera.id, "kt_request", "booked", f"/meetings?call=m1&project={project.id}")
    store.notify(meera.id, "expert_request", "asked", f"/experts?message=x9&project={project.id}")
    assert store.close_notifications("/approvals/a1") == 1
    unread = {n["text"] for n in store.notifications_for(admin.id) if not n["read_at"]}
    assert unread == {"another plan"}
    assert store.notification_recipients("/meetings?call=m1", "kt_request") == [meera.id]
    assert store.notification_recipients("/meetings?call=m1", "meeting") == []
    assert store.close_notifications("/experts?message=x9") == 1 and store.close_notifications("/experts?message=x9") == 0


def test_chat_history_carries_the_expert_answer_and_votes_can_be_taken_back(store):
    admin, project = _org(store, "chat")
    hire = _join(store, admin, project, "hire")
    expert = _join(store, admin, project, "Ravi", "expert")
    first = store.save_chat(project.id, hire.id, "How is the mutate module benchmarked?", NEI)
    rid = store.create_expert_request(project.id, "How is the mutate module benchmarked?", expert.id, hire.id, first)
    again = store.save_chat(project.id, hire.id, "how is the MUTATE module  benchmarked", NEI)  # asked again, differently
    [a, b] = store.chat_history(project.id, hire.id)
    assert a["expert"]["status"] == b["expert"]["status"] == "open" and a["expert"]["name"] == "Ravi"
    store.answer_expert_request(rid, "With pytest-benchmark.")
    [a, b] = store.chat_history(project.id, hire.id)
    assert (a["message_id"], b["message_id"]) == (first, again)
    for turn in (a, b):
        assert turn["expert"]["status"] == "answered" and turn["expert"]["answer"] == "With pytest-benchmark." and turn["expert"]["answered_at"]
    assert store.chat_feedback(first, hire.id, 1) and store.chat_history(project.id, hire.id)[0]["feedback"] == 1
    assert store.chat_feedback(first, hire.id, 0) and store.chat_history(project.id, hire.id)[0]["feedback"] is None


def test_review_approvals_close_on_merge_and_the_newest_review_is_found(store):
    admin, project, hire, ob = _onboarding(store)
    [task] = store.create_tasks(ob.id, [ProposedTask(title="First PR", description="", kind="code", due_in_days=3)], date.today())
    r1 = store.save_review(task.id, 7, "https://x/7", "a1", _result("first"))
    r2 = store.save_review(task.id, 7, "https://x/7", "b2", _result("second"))
    a1 = store.create_approval(project.id, "review", "t-a1", {"review_id": r1.id, "task_id": task.id, "pr_updated_at": "2026-10-01T00:00:00Z"})
    a2 = store.create_approval(project.id, "review", "t-b2", {"review_id": r2.id, "task_id": task.id, "pr_updated_at": "2026-10-02T00:00:00Z"})
    assert [a.id for a in store.review_approvals(task.id)] == [a1.id, a2.id]
    assert store.latest_reviews([task.id, "00000000-0000-0000-0000-000000000000" if isinstance(store, PgStore) else "nope"]) == {task.id: r2.id}
    closed = store.close_review_approvals(task.id)
    assert {a.id for a in closed} == {a1.id, a2.id} and all(a.status == "rejected" and not a.decided_by for a in closed)
    assert store.close_review_approvals(task.id) == []


def test_meetings_remember_who_booked_them_and_end_only_when_live(store):
    admin, project = _org(store, "call")
    m = store.create_meeting(project.id, "KT #2", admin.id)
    assert m.created_by == admin.id and store.get_meeting(m.id).created_by == admin.id
    assert not store.end_meeting(m.id)  # a call that never started does not "end"
    assert store.claim_meeting_recording(m.id) and store.get_meeting(m.id).status == "live"
    assert store.end_meeting(m.id) and store.get_meeting(m.id).status == "ended"
    assert not store.end_meeting(m.id) and not store.cancel_meeting(m.id)


def test_a_document_is_read_back_and_deleted_with_its_chunks(store, request):
    admin, project = _org(store, "doc")
    index = MemoryIndex(HashEmbedder()) if not isinstance(store, PgStore) else PgIndex(request.getfixturevalue("pg")["engine"], HashEmbedder())
    did = store.create_document(project.id, "Runbook", "gs://local/docs/x/runbook.md", "text/markdown", admin.id)
    doc = store.get_document(did)
    assert (doc["title"], doc["uri"], doc["mime"], doc["status"]) == ("Runbook", "gs://local/docs/x/runbook.md", "text/markdown", "pending")
    index.upsert([Chunk(id=f"doc-{did}-1", project_id=project.id, source_type="doc", ref={"document_id": did, "title": "Runbook", "page": 1},
                        content="Document: Runbook (page 1)\nRoll back with quokka rollback.")])
    assert index.delete_document(project.id, did) == [f"doc-{did}-1"]
    assert index.delete_document(project.id, did) == []
    store.delete_document(did)
    assert store.get_document(did) is None
